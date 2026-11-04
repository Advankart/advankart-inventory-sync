"""Thin WooCommerce REST API (wc/v3) client used by the inventory app."""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

PRODUCT_FIELDS = ("id,parent_id,type,name,sku,images,regular_price,sale_price,price,manage_stock,stock_quantity,"
                  "stock_status,status,categories,permalink,date_on_sale_from,date_on_sale_to,date_modified,"
                  "menu_order,variations")
VARIATION_FIELDS = ("id,parent_id,sku,image,regular_price,sale_price,price,manage_stock,stock_quantity,stock_status,"
                    "status,attributes,permalink,date_on_sale_from,date_on_sale_to,date_modified,menu_order")


class WooError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Woo:
    def __init__(self, settings):
        self.site = (settings.get("site_url") or "").strip().rstrip("/")
        self.ck = (settings.get("consumer_key") or "").strip()
        self.cs = (settings.get("consumer_secret") or "").strip()
        self.auth_mode = settings.get("auth_mode") or "basic"
        self.verify = settings.get("verify_ssl", "1") != "0"
        self.include_drafts = settings.get("include_drafts", "1") == "1"
        if not self.site or not self.ck or not self.cs:
            raise WooError("WooCommerce site URL and API keys are not set. Open Settings first.")
        if not self.site.startswith("http"):
            self.site = "https://" + self.site
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "AdvankartInventory/1.0", "Accept": "application/json"})

    # ---------------- low level ----------------
    def _url(self, path):
        return f"{self.site}/wp-json/wc/v3/{path.lstrip('/')}"

    def _req(self, method, path, params=None, json=None, retries=3):
        params = dict(params or {})
        auth = None
        if self.auth_mode == "query":
            params.update({"consumer_key": self.ck, "consumer_secret": self.cs})
        else:
            auth = (self.ck, self.cs)
        last = None
        for attempt in range(retries):
            try:
                r = self.s.request(method, self._url(path), params=params, json=json, auth=auth,
                                   timeout=90, verify=self.verify)
            except requests.RequestException as e:
                last = WooError(f"Network error talking to {self.site}: {e}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401 and self.auth_mode == "basic":
                # Some hosts strip the Authorization header — fall back to query-string auth automatically.
                self.auth_mode = "query"
                return self._req(method, path, params=params, json=json, retries=retries)
            if r.status_code in (429, 500, 502, 503, 504):
                last = WooError(f"Server busy ({r.status_code}). Retrying…", r.status_code)
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                try:
                    msg = r.json().get("message") or r.text[:300]
                except ValueError:
                    msg = r.text[:300]
                    if "cloudflare" in r.text.lower() or "challenge" in r.text.lower():
                        msg = ("Blocked by Cloudflare/WAF. Add a WAF skip rule for /wp-json/wc/ "
                               "from your store IP, then try again.")
                raise WooError(f"WooCommerce error {r.status_code}: {msg}", r.status_code)
            try:
                return r.json(), r.headers
            except ValueError:
                raise WooError("WooCommerce returned a non-JSON response. Check the site URL and that the REST API "
                               "is not blocked by a security plugin.")
        raise last or WooError("Request failed")

    def test(self):
        data, headers = self._req("GET", "products", {"per_page": 1, "_fields": "id"})
        return {"ok": True, "total_products": int(headers.get("X-WP-Total", 0) or 0), "auth_mode": self.auth_mode}

    # ---------------- reading ----------------
    def _paged(self, path, params, progress=None, label=""):
        out, page = [], 1
        while True:
            p = dict(params, per_page=100, page=page)
            data, headers = self._req("GET", path, p)
            out.extend(data)
            total_pages = int(headers.get("X-WP-TotalPages", 1) or 1)
            if progress:
                progress(f"{label} page {page}/{total_pages}")
            if page >= total_pages or not data:
                break
            page += 1
        return out

    def list_products(self, modified_after=None, include=None, progress=None):
        params = {"_fields": PRODUCT_FIELDS, "orderby": "id", "order": "asc"}
        params["status"] = "any" if self.include_drafts else "publish"
        if modified_after:
            params["modified_after"] = modified_after
        if include:
            params["include"] = ",".join(str(i) for i in include)
        return self._paged("products", params, progress, "Products")

    def list_variations(self, parent_id, include=None):
        params = {"_fields": VARIATION_FIELDS, "orderby": "menu_order", "order": "asc"}
        if include:
            params["include"] = ",".join(str(i) for i in include)
        return self._paged(f"products/{parent_id}/variations", params)

    def variations_for(self, parents, progress=None, workers=6):
        """Fetch variations for many variable parents in parallel. parents: list of normalised parent dicts."""
        result, done = [], 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.list_variations, p["id"]): p for p in parents}
            for f in as_completed(futs):
                parent = futs[f]
                for v in f.result():
                    result.append(normalise_variation(v, parent))
                done += 1
                if progress:
                    progress(f"Variations {done}/{len(parents)}")
        return result

    # ---------------- writing ----------------
    def batch_products(self, updates):
        """updates: list of {'id':..., field: value}. Returns list of responses."""
        out = []
        for i in range(0, len(updates), 100):
            data, _ = self._req("POST", "products/batch", json={"update": updates[i:i + 100]})
            out.extend(data.get("update", []))
        return out

    def batch_variations(self, parent_id, updates):
        out = []
        for i in range(0, len(updates), 100):
            data, _ = self._req("POST", f"products/{parent_id}/variations/batch", json={"update": updates[i:i + 100]})
            out.extend(data.get("update", []))
        return out


# ---------------- normalisation ----------------
def _ms(v):
    if v is True:
        return "1"
    if v == "parent":
        return "parent"
    return "0"


def _qty(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def normalise_product(p):
    imgs = p.get("images") or []
    return {
        "id": p["id"],
        "parent_id": 0,
        "type": p.get("type") or "simple",
        "name": p.get("name") or "",
        "variation_label": "",
        "sku": (p.get("sku") or "").strip(),
        "image": imgs[0].get("src", "") if imgs else "",
        "regular_price": p.get("regular_price") or "",
        "sale_price": p.get("sale_price") or "",
        "price": p.get("price") or "",
        "manage_stock": _ms(p.get("manage_stock")),
        "stock_quantity": _qty(p.get("stock_quantity")),
        "stock_status": p.get("stock_status") or "",
        "status": p.get("status") or "",
        "categories": ", ".join(c.get("name", "") for c in (p.get("categories") or [])),
        "permalink": p.get("permalink") or "",
        "date_on_sale_from": p.get("date_on_sale_from") or "",
        "date_on_sale_to": p.get("date_on_sale_to") or "",
        "date_modified": p.get("date_modified") or "",
        "menu_order": p.get("menu_order") or 0,
    }


def normalise_variation(v, parent):
    attrs = v.get("attributes") or []
    label = " / ".join(f"{a.get('option')}" for a in attrs if a.get("option")) or f"#{v['id']}"
    img = (v.get("image") or {}).get("src") or parent.get("image", "")
    return {
        "id": v["id"],
        "parent_id": parent["id"],
        "type": "variation",
        "name": parent.get("name", ""),
        "variation_label": label,
        "sku": (v.get("sku") or "").strip(),
        "image": img,
        "regular_price": v.get("regular_price") or "",
        "sale_price": v.get("sale_price") or "",
        "price": v.get("price") or "",
        "manage_stock": _ms(v.get("manage_stock")),
        "stock_quantity": _qty(v.get("stock_quantity")),
        "stock_status": v.get("stock_status") or "",
        "status": v.get("status") or "",
        "categories": parent.get("categories", ""),
        "permalink": v.get("permalink") or parent.get("permalink", ""),
        "date_on_sale_from": v.get("date_on_sale_from") or "",
        "date_on_sale_to": v.get("date_on_sale_to") or "",
        "date_modified": v.get("date_modified") or "",
        "menu_order": v.get("menu_order") or 0,
    }

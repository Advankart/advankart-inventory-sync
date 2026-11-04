"""Read BillBook (or any) CSV/XLSX exports, auto-map columns and build a validated change preview."""
import csv
import io
import re

FIELD_SYNONYMS = {
    "sku": ["sku", "sku code", "item code", "itemcode", "product code", "productcode", "code", "article no",
            "article code", "item sku", "barcode", "bar code", "part no", "model no"],
    "name": ["item name", "product name", "name", "item", "product", "description", "item description"],
    "stock": ["stock", "current stock", "closing stock", "stock qty", "stock quantity", "qty", "quantity",
              "available qty", "available quantity", "balance qty", "balance", "in stock", "inventory",
              "opening stock", "closing qty"],
    "selling_price": ["selling price", "sales price", "sale price", "sp", "selling rate", "sales rate", "rate",
                      "price", "selling price (incl tax)", "selling price incl tax", "sale rate", "unit price"],
    "mrp": ["mrp", "m.r.p", "m.r.p.", "regular price", "max retail price", "maximum retail price", "list price"],
}
FIELD_LABELS = {"sku": "SKU / Item code", "name": "Item name (optional)", "stock": "Stock quantity",
                "selling_price": "Selling price", "mrp": "MRP → Regular price (optional)"}


def _norm_header(h):
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", str(h or ""))).strip().lower().rstrip(":")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_table(filename, raw: bytes):
    """Returns (headers, rows) where rows is a list of lists of strings. Detects the header row."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        grid = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
    elif name.endswith(".xls"):
        raise ValueError("Old .xls files are not supported. In BillBook export as CSV or .xlsx, or open the file in "
                         "Excel and Save As .xlsx / CSV.")
    else:
        text = None
        for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                if enc == "utf-16" and "\x00" in text:
                    continue
                break
            except UnicodeDecodeError:
                continue
        sample = text[:5000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        grid = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), dialect)]

    grid = [r for r in grid if any(c for c in r)]
    if not grid:
        raise ValueError("The file is empty.")
    # Header row = first of the first 20 rows that contains a recognisable SKU column
    header_idx = 0
    sku_words = set(FIELD_SYNONYMS["sku"])
    for i, row in enumerate(grid[:20]):
        if any(_norm_header(c) in sku_words for c in row):
            header_idx = i
            break
    headers = [h or f"Column {j + 1}" for j, h in enumerate(grid[header_idx])]
    width = len(headers)
    rows = []
    for r in grid[header_idx + 1:]:
        r = (r + [""] * width)[:width]
        rows.append(r)
    return headers, rows


def guess_mapping(headers):
    normed = [_norm_header(h) for h in headers]
    mapping, used = {}, set()
    for field in ("sku", "mrp", "selling_price", "stock", "name"):
        syn = FIELD_SYNONYMS[field]
        best = None
        for rank, s in enumerate(syn):  # exact match in order of preference
            for j, h in enumerate(normed):
                if j not in used and h == s:
                    best = j
                    break
            if best is not None:
                break
        if best is None:  # contains match
            for s in syn:
                if len(s) < 4:
                    continue
                for j, h in enumerate(normed):
                    if j not in used and s in h:
                        best = j
                        break
                if best is not None:
                    break
        if best is not None:
            mapping[field] = headers[best]
            used.add(best)
    return mapping


NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_number(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("INR", "").strip()
    if s == "" or s == "-":
        return None
    m = NUM_RE.search(s)
    if not m:
        return "invalid"
    return float(m.group(0))


def sku_key(s):
    s = str(s or "").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s.upper()


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _same(a, b):
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) < 0.005


def fmt_price(x):
    if x is None:
        return ""
    return ("%.2f" % x).rstrip("0").rstrip(".")


def build_preview(headers, rows, mapping, items, settings):
    """Compare file rows with cached website items. Returns preview dict."""
    idx = {h: i for i, h in enumerate(headers)}
    col = {f: idx.get(mapping.get(f)) for f in FIELD_LABELS if mapping.get(f) in idx}
    if "sku" not in col:
        raise ValueError("Please map the SKU / Item code column.")
    if not any(f in col for f in ("stock", "selling_price", "mrp")):
        raise ValueError("Map at least one of Stock, Selling price or MRP.")

    price_mode = settings.get("price_mode", "auto")
    big_pct = float(settings.get("big_price_change_pct") or 30)
    big_stock = float(settings.get("big_stock_change") or 100)

    by_sku = {}
    for it in items:
        if it.get("sku"):
            by_sku.setdefault(sku_key(it["sku"]), []).append(it)
    parents = {it["id"]: it for it in items if it["parent_id"] == 0}

    # group file rows by sku
    file_rows, seen = [], {}
    blank_sku = 0
    for n, r in enumerate(rows, start=2):
        raw_sku = r[col["sku"]] if col["sku"] < len(r) else ""
        if not str(raw_sku).strip():
            blank_sku += 1
            continue
        entry = {"line": n, "sku": str(raw_sku).strip(), "key": sku_key(raw_sku),
                 "name": r[col["name"]] if "name" in col else "",
                 "stock_raw": r[col["stock"]] if "stock" in col else None,
                 "sp_raw": r[col["selling_price"]] if "selling_price" in col else None,
                 "mrp_raw": r[col["mrp"]] if "mrp" in col else None}
        file_rows.append(entry)
        seen.setdefault(entry["key"], []).append(entry)

    out_rows, unmatched = [], []
    handled = set()
    rid = 0
    for e in file_rows:
        key = e["key"]
        if key in handled:
            continue
        handled.add(key)
        dupes = seen[key]
        errors, warnings = [], []
        if len(dupes) > 1:
            vals = {(d["stock_raw"], d["sp_raw"], d["mrp_raw"]) for d in dupes}
            lines = ", ".join(str(d["line"]) for d in dupes)
            if len(vals) > 1:
                errors.append(f"SKU appears {len(dupes)} times in the file with different values (lines {lines}).")
            else:
                warnings.append(f"SKU repeated on lines {lines} with the same values — used once.")

        matches = by_sku.get(key, [])
        if not matches:
            unmatched.append({"line": e["line"], "sku": e["sku"], "name": e["name"],
                              "stock": e["stock_raw"], "selling_price": e["sp_raw"]})
            continue
        rid += 1
        if len(matches) > 1:
            errors.append(f"This SKU is used by {len(matches)} products/variations on the website — fix duplicates "
                          f"in WooCommerce first.")
        it = matches[0]

        stock = parse_number(e["stock_raw"]) if e["stock_raw"] is not None else None
        sp = parse_number(e["sp_raw"]) if e["sp_raw"] is not None else None
        mrp = parse_number(e["mrp_raw"]) if e["mrp_raw"] is not None else None
        for label, v in (("Stock", stock), ("Selling price", sp), ("MRP", mrp)):
            if v == "invalid":
                errors.append(f"{label} value is not a number.")
        stock = None if stock == "invalid" else stock
        sp = None if sp == "invalid" else sp
        mrp = None if mrp == "invalid" else mrp

        before = {"regular_price": it["regular_price"], "sale_price": it["sale_price"],
                  "stock_quantity": it["stock_quantity"], "manage_stock": it["manage_stock"]}
        after = {}

        # ---------- stock ----------
        if stock is not None:
            if stock != int(stock):
                warnings.append(f"Stock {stock} is not a whole number — rounded down to {int(stock)}.")
            new_q = int(stock)
            if new_q < 0:
                errors.append(f"Negative stock ({new_q}) in BillBook — not applied. Fix the stock in BillBook.")
            elif it["type"] == "variable" and it["manage_stock"] != "1":
                warnings.append("Variable product — stock is tracked on its variations; parent stock not changed.")
            else:
                if it["manage_stock"] != "1" or it["stock_quantity"] != new_q:
                    after["stock_quantity"] = new_q
                    if it["manage_stock"] != "1":
                        after["manage_stock"] = "1"
                        warnings.append("Stock tracking is off for this item on the website — it will be turned ON "
                                        "(Manage stock) so the quantity is used." if it["manage_stock"] == "0" else
                                        "Variation stock currently follows the parent — this variation will get its "
                                        "own stock.")
                    old_q = it["stock_quantity"] or 0
                    if abs(new_q - old_q) >= big_stock:
                        warnings.append(f"Large stock change: {old_q} → {new_q}.")
                    if new_q == 0:
                        warnings.append("Stock becomes 0 — item will show Out of stock.")

        # ---------- price ----------
        if (sp is not None or mrp is not None) and it["type"] in ("variable", "grouped"):
            warnings.append("Price ignored — variable/grouped parents have no price of their own; update each "
                            "variation SKU instead.")
            sp = mrp = None
        for label, v in (("Selling price", sp), ("MRP", mrp)):
            if v is not None and v <= 0:
                errors.append(f"{label} is {fmt_price(v)} — must be greater than 0.")
        if not errors and (sp is not None or mrp is not None):
            cur_reg = _num(it["regular_price"])
            cur_sale = _num(it["sale_price"])
            new_reg, new_sale = cur_reg, cur_sale
            protect = settings.get("protect_sale", "1") == "1"
            P = fmt_price

            def skip_price(msg):
                nonlocal new_reg, new_sale
                new_reg, new_sale = cur_reg, cur_sale
                warnings.append("Price not changed: " + msg)

            if mrp is not None:
                # MRP column -> regular price; selling price -> sale price when lower than MRP
                new_reg = mrp
                if sp is not None and sp < mrp - 0.004:
                    new_sale = sp
                elif sp is not None and sp > mrp + 0.004:
                    skip_price(f"selling price ₹{P(sp)} is higher than MRP ₹{P(mrp)} in BillBook.")
                elif cur_sale is not None:
                    if cur_sale < mrp - 0.004 and protect:
                        pass  # website-only sale kept
                    elif cur_sale >= mrp - 0.004:
                        if protect:
                            skip_price(f"website sale price ₹{P(cur_sale)} would not be lower than the new "
                                       f"regular price ₹{P(mrp)}. Fix the sale price in WooCommerce first.")
                        else:
                            new_sale = None
                            warnings.append("Sale price removed (it was not lower than the new MRP).")
                    elif sp is not None:
                        new_sale = None  # BillBook price equals MRP -> no discount
            elif sp is not None:
                mode = price_mode if price_mode in ("regular", "effective", "sale") else "regular"
                if _same(sp, cur_reg) or (mode != "sale" and _same(sp, cur_sale)):
                    pass  # BillBook price already matches the website — nothing to change
                elif mode == "regular":
                    new_reg = sp
                    if cur_sale is not None and cur_sale >= sp - 0.004:
                        if protect:
                            skip_price(f"website sale price ₹{P(cur_sale)} would not be lower than the new "
                                       f"regular price ₹{P(sp)}. Fix the sale price in WooCommerce first.")
                        else:
                            new_sale = None
                            warnings.append("Sale price removed (it was not lower than the new regular price).")
                elif mode == "effective":
                    if cur_sale is None:
                        new_reg = sp
                    elif cur_reg is not None and sp < cur_reg - 0.004:
                        new_sale = sp
                    elif protect:
                        skip_price(f"item is on sale (₹{P(cur_sale)}) and ₹{P(sp)} is not below its regular "
                                   f"price ₹{P(cur_reg)}.")
                    else:
                        new_reg, new_sale = sp, None
                        warnings.append("Regular price updated and sale price removed.")
                else:  # sale
                    if cur_reg is not None and sp < cur_reg - 0.004:
                        new_sale = sp
                    else:
                        skip_price(f"₹{P(sp)} is not below the regular price ₹{P(cur_reg)}, so it cannot be "
                                   f"a sale price.")
            if not _same(new_reg, cur_reg):
                after["regular_price"] = fmt_price(new_reg)
            if not _same(new_sale, cur_sale):
                after["sale_price"] = fmt_price(new_sale)
            old_eff = cur_sale if cur_sale is not None else cur_reg
            new_eff = new_sale if new_sale is not None else new_reg
            if old_eff and new_eff and abs(new_eff - old_eff) / old_eff * 100 >= big_pct:
                warnings.append(f"Large price change: ₹{fmt_price(old_eff)} → ₹{fmt_price(new_eff)} "
                                f"({(new_eff - old_eff) / old_eff * 100:+.0f}%).")
            if "sale_price" in after and (it.get("date_on_sale_from") or it.get("date_on_sale_to")):
                warnings.append("This item has scheduled sale dates — the sale price only shows within those dates.")

        if errors:
            status = "error"
        elif after:
            status = "update"
        else:
            status = "nochange"
        parent = parents.get(it["parent_id"]) if it["parent_id"] else None
        out_rows.append({
            "rid": rid, "line": e["line"], "sku": it["sku"], "file_name": e["name"],
            "item_id": it["id"], "parent_id": it["parent_id"], "type": it["type"],
            "name": it["name"], "variation_label": it["variation_label"], "image": it["image"],
            "before": before, "after": after, "status": status,
            "errors": errors, "warnings": warnings, "parent_name": parent["name"] if parent else "",
        })

    file_keys = set(seen)
    not_in_file = [{"sku": it["sku"], "name": it["name"], "variation_label": it["variation_label"],
                    "stock_quantity": it["stock_quantity"]}
                   for it in items if it.get("sku") and sku_key(it["sku"]) not in file_keys
                   and it["type"] not in ("variable", "grouped")]
    no_sku = [{"id": it["id"], "name": it["name"], "variation_label": it["variation_label"]}
              for it in items if not it.get("sku") and it["type"] not in ("variable", "grouped")]

    summary = {
        "file_rows": len(rows), "blank_sku_rows": blank_sku, "matched": len(out_rows),
        "to_update": sum(1 for r in out_rows if r["status"] == "update"),
        "no_change": sum(1 for r in out_rows if r["status"] == "nochange"),
        "errors": sum(1 for r in out_rows if r["status"] == "error"),
        "with_warnings": sum(1 for r in out_rows if r["status"] == "update" and r["warnings"]),
        "unmatched": len(unmatched),
        "stock_changes": sum(1 for r in out_rows if r["status"] == "update" and "stock_quantity" in r["after"]),
        "price_changes": sum(1 for r in out_rows if r["status"] == "update"
                             and ("regular_price" in r["after"] or "sale_price" in r["after"])),
        "website_not_in_file": len(not_in_file), "website_without_sku": len(no_sku),
    }
    return {"summary": summary, "rows": out_rows, "unmatched": unmatched,
            "not_in_file": not_in_file[:2000], "no_sku": no_sku[:2000]}

"""Advankart Inventory Sync — local WooCommerce stock & price manager.

Run:  python app.py      then open http://localhost:8765
"""
import csv
import io
import os
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, Response

import db
import importer
import updater
from version import VERSION
from woo import Woo, WooError, normalise_product, normalise_variation

APP_NAME = "Advankart Inventory Sync"
PORT = int(os.environ.get("INVENTORY_PORT", "8765"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

JOBS = {}
UPLOADS = {}
write_lock = threading.Lock()   # only one sync / update job at a time


# ====================================================================== jobs
def start_job(kind, fn, *args):
    if not write_lock.acquire(blocking=False):
        running = next((j for j in JOBS.values() if j["state"] == "running"), None)
        raise WooError(f"Another task is running ({running['kind'] if running else 'busy'}). Please wait.")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "state": "running", "progress": "Starting…", "result": None, "error": None,
           "started": db.now()}
    JOBS[jid] = job

    def run():
        try:
            job["result"] = fn(job, *args)
            job["state"] = "done"
        except WooError as e:
            job["error"], job["state"] = str(e), "error"
        except Exception as e:  # noqa
            job["error"], job["state"] = f"{type(e).__name__}: {e}", "error"
        finally:
            write_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return jid


def progress(job):
    return lambda msg: job.__setitem__("progress", msg)


# ====================================================================== sync
def do_sync(job):
    settings = db.get_settings()
    w = Woo(settings)
    t0 = time.time()
    raw = w.list_products(progress=progress(job))
    parents = [normalise_product(p) for p in raw]
    variable = [p for p in parents if p["type"] == "variable"]
    job["progress"] = f"Loaded {len(parents)} products. Loading variations for {len(variable)} variable products…"
    variations = w.variations_for(variable, progress=progress(job))
    items = parents + variations
    db.replace_all_items(items)
    db.save_settings({"last_sync": db.now(), "last_full_sync": db.now()})
    return {"products": len(parents), "variations": len(variations), "seconds": round(time.time() - t0, 1)}


def fetch_live(w, targets):
    """targets: list of cached item dicts. Returns {id: normalised live item}."""
    cache_parents = {}
    prod_ids = [t["id"] for t in targets if not t["parent_id"]]
    var_by_parent = {}
    for t in targets:
        if t["parent_id"]:
            var_by_parent.setdefault(t["parent_id"], []).append(t["id"])
    live = {}
    for i in range(0, len(prod_ids), 100):
        for p in w.list_products(include=prod_ids[i:i + 100]):
            n = normalise_product(p)
            live[n["id"]] = n
    if var_by_parent:
        cached = db.get_items(var_by_parent.keys())
        for pid, vids in var_by_parent.items():
            parent = cached.get(pid) or {"id": pid, "name": "", "image": "", "categories": "", "permalink": ""}
            cache_parents[pid] = parent
            for j in range(0, len(vids), 100):
                for v in w.list_variations(pid, include=vids[j:j + 100]):
                    n = normalise_variation(v, parent)
                    live[n["id"]] = n
    return live


def _payload(after, live_item):
    p = {}
    if "regular_price" in after:
        p["regular_price"] = after["regular_price"]
    if "sale_price" in after:
        p["sale_price"] = after["sale_price"]
    if "manage_stock" in after:
        p["manage_stock"] = after["manage_stock"] == "1"
    if "stock_quantity" in after and after["stock_quantity"] is not None:
        p["stock_quantity"] = int(after["stock_quantity"])
        if live_item and live_item["manage_stock"] != "1":
            p["manage_stock"] = True
    return p


def _check(after, live_item):
    """Return list of mismatch strings comparing expected after-values with live values."""
    bad = []
    if live_item is None:
        return ["Item not found on website after update"]
    for k, v in after.items():
        actual = live_item.get(k)
        if k == "stock_quantity":
            ok = actual == (int(v) if v is not None else None)
        elif k == "manage_stock":
            ok = (actual == "1") == (v == "1")
        else:
            ok = importer._same(actual, v)
        if not ok:
            bad.append(f"{k}: expected {v!r}, website shows {actual!r}")
    return bad


def do_apply(job, changes, kind, source):
    """changes: list of {item_id, parent_id, sku, name, after}. Applies, then verifies against live data."""
    settings = db.get_settings()
    w = Woo(settings)
    cached = db.get_items([c["item_id"] for c in changes])
    targets = [cached[c["item_id"]] for c in changes if c["item_id"] in cached]
    job["progress"] = f"Reading current website values for {len(targets)} items…"
    live_before = fetch_live(w, targets)

    run_id = db.create_run(kind, source, len(changes))
    prod_updates, var_updates, meta = [], {}, {}
    pre_errors = {}
    for c in changes:
        lb = live_before.get(c["item_id"])
        if lb is None:
            pre_errors[c["item_id"]] = "Item no longer exists on the website"
            continue
        payload = _payload(c["after"], lb)
        if not payload:
            continue
        payload["id"] = c["item_id"]
        meta[c["item_id"]] = (c, lb)
        if c["parent_id"]:
            var_updates.setdefault(c["parent_id"], []).append(payload)
        else:
            prod_updates.append(payload)

    write_errors = {}
    total_batches = (1 if prod_updates else 0) + len(var_updates)
    done = 0
    if prod_updates:
        job["progress"] = f"Updating {len(prod_updates)} products…"
        try:
            for r in w.batch_products(prod_updates):
                if r.get("error"):
                    write_errors[r.get("id")] = r["error"].get("message", "error")
        except WooError as e:
            for p in prod_updates:
                write_errors[p["id"]] = str(e)
        done += 1
    for pid, ups in var_updates.items():
        job["progress"] = f"Updating variations ({done}/{total_batches})…"
        try:
            for r in w.batch_variations(pid, ups):
                if r.get("error"):
                    write_errors[r.get("id")] = r["error"].get("message", "error")
        except WooError as e:
            for p in ups:
                write_errors[p["id"]] = str(e)
        done += 1

    job["progress"] = "Verifying updates on the website…"
    time.sleep(0.5)
    live_after = fetch_live(w, [cached[i] for i in meta])
    db.upsert_items(list(live_after.values()))

    applied = verified = failed = 0
    results = []
    for c in changes:
        iid = c["item_id"]
        if iid in pre_errors:
            failed += 1
            db.add_run_item(run_id, iid, c["parent_id"], c["sku"], c["name"], {}, c["after"], None, "error", pre_errors[iid])
            results.append({"item_id": iid, "sku": c["sku"], "result": "error", "message": pre_errors[iid]})
            continue
        if iid not in meta:
            continue
        _, lb = meta[iid]
        before = {k: lb.get(k) for k in ("regular_price", "sale_price", "stock_quantity", "manage_stock")}
        la = live_after.get(iid)
        actual = {k: la.get(k) for k in before} if la else None
        if iid in write_errors:
            res, msg = "error", write_errors[iid]
            failed += 1
        else:
            applied += 1
            bad = _check(c["after"], la)
            if bad:
                res, msg = "mismatch", "; ".join(bad)
                failed += 1
            else:
                res, msg = "verified", ""
                verified += 1
        drift = []
        cb = cached.get(iid) or {}
        for k in ("regular_price", "sale_price", "stock_quantity"):
            if k in c["after"] and not importer._same(cb.get(k), lb.get(k)) and str(cb.get(k)) != str(lb.get(k)):
                drift.append(f"{k} had changed on the website since the preview ({cb.get(k)} → {lb.get(k)})")
        if drift and not msg:
            msg = "Note: " + "; ".join(drift)
        db.add_run_item(run_id, iid, c["parent_id"], c["sku"], c["name"], before, c["after"], actual, res, msg)
        results.append({"item_id": iid, "sku": c["sku"], "result": res, "message": msg})

    status = "verified" if failed == 0 else ("partial" if verified else "failed")
    db.finish_run(run_id, applied, verified, failed, status)
    return {"run_id": run_id, "total": len(changes), "applied": applied, "verified": verified, "failed": failed,
            "status": status, "results": results}


# ====================================================================== auto sync
def auto_sync_loop():
    while True:
        time.sleep(30)
        try:
            s = db.get_settings()
            mins = int(s.get("auto_sync_minutes") or 0)
            if mins <= 0 or not s.get("consumer_key"):
                continue
            last = s.get("last_sync")
            if last:
                age = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds()
                if age < mins * 60:
                    continue
            start_job("auto-sync", do_sync)
        except Exception:
            pass


# ====================================================================== routes
def err(msg, code=400):
    return jsonify({"ok": False, "error": str(msg)}), code


@app.errorhandler(WooError)
def _woo_err(e):
    return err(e, 400)


@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/status")
def status():
    s = db.get_settings()
    running = [j for j in JOBS.values() if j["state"] == "running"]
    age = None
    if s["last_sync"]:
        try:
            age = (datetime.now() - datetime.strptime(s["last_sync"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            pass
    return jsonify({"last_sync_age": age, "ok": True, "app": APP_NAME, "version": VERSION, "configured": bool(s["consumer_key"] and s["site_url"]),
                    "site_url": s["site_url"], "last_sync": s["last_sync"], "items": db.item_count(),
                    "running": running[0] if running else None})


@app.get("/api/settings")
def get_settings():
    s = db.get_settings()
    s["consumer_secret"] = ("•" * 8 + s["consumer_secret"][-4:]) if s["consumer_secret"] else ""
    return jsonify(s)


@app.post("/api/settings")
def save_settings():
    data = request.get_json(force=True) or {}
    if data.get("consumer_secret", "").startswith("•"):
        data.pop("consumer_secret")
    db.save_settings(data)
    return jsonify({"ok": True})


@app.post("/api/test-connection")
def test_connection():
    s = db.get_settings()
    res = Woo(s).test()
    if res["auth_mode"] != s.get("auth_mode"):
        db.save_settings({"auth_mode": res["auth_mode"]})
    return jsonify(res)


@app.post("/api/sync")
def sync():
    return jsonify({"ok": True, "job": start_job("sync", do_sync)})


@app.get("/api/job/<jid>")
def job(jid):
    j = JOBS.get(jid)
    return jsonify(j) if j else err("Unknown job", 404)


@app.get("/api/update/check")
def update_check():
    try:
        return jsonify({"ok": True, **updater.check(force=request.args.get("force") == "1")})
    except Exception as e:  # noqa
        return err(f"Could not check for updates: {e}")


def do_update(job):
    res = updater.apply(progress=progress(job))
    if res.get("updated"):
        job["progress"] = "Restarting…"
        updater.restart_soon()
    return res


@app.post("/api/update/apply")
def update_apply():
    return jsonify({"ok": True, "job": start_job("update", do_update)})


@app.get("/api/items")
def items():
    s = db.get_settings()
    return jsonify({"items": db.all_items(), "last_sync": s["last_sync"], "low_stock": int(s.get("low_stock") or 3)})


@app.post("/api/item/<int:item_id>")
def inline_update(item_id):
    """Quick edit from the inventory table: body {regular_price?, sale_price?, stock_quantity?}"""
    data = request.get_json(force=True) or {}
    it = db.get_items([item_id]).get(item_id)
    if not it:
        return err("Item not in local cache — sync first.")
    after = {}
    for k in ("regular_price", "sale_price"):
        if k in data:
            v = str(data[k]).strip()
            if v:
                n = importer.parse_number(v)
                if n in (None, "invalid") or n <= 0:
                    return err(f"{k.replace('_', ' ')} must be a positive number")
                v = importer.fmt_price(n)
            after[k] = v
    if "stock_quantity" in data:
        n = importer.parse_number(data["stock_quantity"])
        if n in (None, "invalid") or n < 0:
            return err("Stock must be 0 or more")
        after["stock_quantity"] = int(n)
        if it["manage_stock"] != "1":
            after["manage_stock"] = "1"
    reg = importer._num(after.get("regular_price", it["regular_price"]))
    sale = importer._num(after.get("sale_price", it["sale_price"]))
    if sale is not None and reg is not None and sale >= reg:
        return err("Sale price must be lower than the regular price")
    change = {"item_id": item_id, "parent_id": it["parent_id"], "sku": it["sku"],
              "name": it["name"] + (f" — {it['variation_label']}" if it["variation_label"] else ""), "after": after}
    return jsonify({"ok": True, "job": start_job("quick edit", do_apply, [change], "inline", "Inventory table")})


# ---------------- import ----------------
@app.post("/api/import/upload")
def import_upload():
    f = request.files.get("file")
    if not f:
        return err("No file received")
    try:
        headers, rows = importer.read_table(f.filename, f.read())
    except ValueError as e:
        return err(e)
    except Exception as e:  # noqa
        return err(f"Could not read this file: {e}")
    uid = uuid.uuid4().hex[:10]
    UPLOADS[uid] = {"filename": f.filename, "headers": headers, "rows": rows, "preview": None}
    return jsonify({"ok": True, "upload_id": uid, "filename": f.filename, "headers": headers,
                    "sample": rows[:6], "row_count": len(rows), "mapping": importer.guess_mapping(headers),
                    "fields": importer.FIELD_LABELS})


@app.post("/api/import/preview")
def import_preview():
    data = request.get_json(force=True) or {}
    up = UPLOADS.get(data.get("upload_id"))
    if not up:
        return err("Upload expired — please choose the file again.")
    items = db.all_items()
    if not items:
        return err("No website products in the local cache. Click 'Sync from website' first.")
    try:
        pv = importer.build_preview(up["headers"], up["rows"], data.get("mapping") or {}, items, db.get_settings())
    except ValueError as e:
        return err(e)
    up["preview"] = pv
    up["mapping"] = data.get("mapping")
    pv["last_sync"] = db.get_settings()["last_sync"]
    return jsonify({"ok": True, **pv})


@app.post("/api/import/apply")
def import_apply():
    data = request.get_json(force=True) or {}
    up = UPLOADS.get(data.get("upload_id"))
    if not up or not up.get("preview"):
        return err("Preview expired — please run the preview again.")
    wanted = set(data.get("rids") or [])
    changes = [{"item_id": r["item_id"], "parent_id": r["parent_id"], "sku": r["sku"],
                "name": r["name"] + (f" — {r['variation_label']}" if r["variation_label"] else ""), "after": r["after"]}
               for r in up["preview"]["rows"] if r["rid"] in wanted and r["status"] == "update"]
    if not changes:
        return err("Nothing selected to update.")
    return jsonify({"ok": True, "job": start_job("import", do_apply, changes, "import", up["filename"])})


# ---------------- history ----------------
@app.get("/api/runs")
def runs():
    return jsonify(db.list_runs())


@app.get("/api/runs/<int:run_id>")
def run_detail(run_id):
    r = db.get_run(run_id)
    return jsonify(r) if r else err("Not found", 404)


@app.post("/api/runs/<int:run_id>/rollback")
def rollback(run_id):
    r = db.get_run(run_id)
    if not r:
        return err("Not found", 404)
    changes = []
    for it in r["items"]:
        if it["result"] not in ("verified", "mismatch") or not it["before"]:
            continue
        after = {}
        for k in it["after"]:
            if k in it["before"]:
                v = it["before"][k]
                if k == "manage_stock":
                    v = "1" if v == "1" else "0"
                after[k] = v if v is not None else ("" if k != "stock_quantity" else None)
        if "stock_quantity" in after and after["stock_quantity"] is None:
            after.pop("stock_quantity")
        if after:
            changes.append({"item_id": it["item_id"], "parent_id": it["parent_id"], "sku": it["sku"],
                            "name": it["name"], "after": after})
    if not changes:
        return err("Nothing to roll back in this run.")
    return jsonify({"ok": True, "job": start_job("rollback", do_apply, changes, "rollback", f"Undo of run #{run_id}")})


@app.get("/api/runs/<int:run_id>/report.csv")
def run_report(run_id):
    r = db.get_run(run_id)
    if not r:
        return err("Not found", 404)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["SKU", "Item", "Result", "Old regular", "Old sale", "Old stock", "New regular", "New sale",
                 "New stock", "Website regular", "Website sale", "Website stock", "Message"])
    for it in r["items"]:
        b, a, x = it["before"] or {}, it["after"] or {}, it["actual"] or {}
        wr.writerow([it["sku"], it["name"], it["result"], b.get("regular_price"), b.get("sale_price"),
                     b.get("stock_quantity"), a.get("regular_price", "—"), a.get("sale_price", "—"),
                     a.get("stock_quantity", "—"), x.get("regular_price"), x.get("sale_price"),
                     x.get("stock_quantity"), it["message"]])
    return _download(buf.getvalue().encode("utf-8-sig"), f"update-run-{run_id}.csv", "text/csv")


# ---------------- export ----------------
def _export_rows(layout):
    items = db.all_items()
    parents = {i["id"]: i for i in items if not i["parent_id"]}
    ordered = []
    for p in sorted(parents.values(), key=lambda x: x["name"].lower()):
        ordered.append(p)
        ordered.extend(sorted([i for i in items if i["parent_id"] == p["id"]], key=lambda x: (x["menu_order"], x["id"])))
    if layout == "billbook":
        head = ["SKU", "Item Name", "Stock", "Selling Price", "MRP"]
        rows = []
        for i in ordered:
            if i["type"] in ("variable", "grouped"):
                continue
            nm = i["name"] + (f" - {i['variation_label']}" if i["variation_label"] else "")
            rows.append([i["sku"], nm, i["stock_quantity"] if i["manage_stock"] == "1" else "",
                         i["sale_price"] or i["regular_price"], i["regular_price"]])
        return head, rows
    head = ["Product", "Variation", "SKU", "Type", "Regular Price", "Sale Price", "Selling Price", "Stock Qty",
            "Stock Status", "Manage Stock", "Status", "Categories", "Product ID", "Parent ID", "Link"]
    rows = []
    for i in ordered:
        rows.append([i["name"], i["variation_label"], i["sku"], i["type"], i["regular_price"], i["sale_price"],
                     i["price"], i["stock_quantity"] if i["stock_quantity"] is not None else "", i["stock_status"],
                     {"1": "Yes", "0": "No", "parent": "Parent"}.get(i["manage_stock"], ""), i["status"],
                     i["categories"], i["id"] if not i["parent_id"] else i["parent_id"],
                     i["parent_id"] or "", i["permalink"]])
    return head, rows


def _download(content, filename, mime):
    return Response(content, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export")
def export():
    fmt = request.args.get("format", "csv")
    layout = request.args.get("layout", "full")
    head, rows = _export_rows(layout)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    base = f"website-stock-{'billbook-' if layout == 'billbook' else ''}{stamp}"
    if fmt == "xlsx":
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Website stock"
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F4E3D")
        numeric = {j for j, h in enumerate(head) if "Price" in h or h in ("MRP", "Stock", "Stock Qty")}
        for r in rows:
            out = []
            for j, v in enumerate(r):
                if j in numeric and v not in (None, ""):
                    try:
                        v = float(v)
                        v = int(v) if v.is_integer() else v
                    except (TypeError, ValueError):
                        pass
                out.append(v)
            ws.append(out)
        for col in ws.columns:
            width = max(len(str(c.value or "")) for c in col[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
        ws.freeze_panes = "A2"
        bio = io.BytesIO()
        wb.save(bio)
        return _download(bio.getvalue(), base + ".xlsx",
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(rows)
    return _download(buf.getvalue().encode("utf-8-sig"), base + ".csv", "text/csv")


@app.post("/api/import/report")
def preview_report():
    data = request.get_json(force=True) or {}
    up = UPLOADS.get(data.get("upload_id"))
    if not up or not up.get("preview"):
        return err("Preview expired")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Line", "SKU", "Item", "Status", "Old regular", "New regular", "Old sale", "New sale", "Old stock",
                "New stock", "Errors", "Warnings"])
    for r in up["preview"]["rows"]:
        b, a = r["before"], r["after"]
        w.writerow([r["line"], r["sku"], r["name"] + (" — " + r["variation_label"] if r["variation_label"] else ""),
                    r["status"], b["regular_price"], a.get("regular_price", ""), b["sale_price"],
                    a.get("sale_price", ""), b["stock_quantity"], a.get("stock_quantity", ""),
                    " | ".join(r["errors"]), " | ".join(r["warnings"])])
    for u in up["preview"]["unmatched"]:
        w.writerow([u["line"], u["sku"], u["name"], "not on website", "", "", "", "", "", u["stock"], "", ""])
    return _download(buf.getvalue().encode("utf-8-sig"), "import-preview.csv", "text/csv")


def main():
    db.con()
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    url = f"http://localhost:{PORT}"
    import socket
    if "--wait-port" in sys.argv:          # restarting after an update: wait for the old process to exit
        for _ in range(40):
            with socket.socket() as sk:
                if sk.connect_ex(("127.0.0.1", PORT)) != 0:
                    break
            time.sleep(0.5)
    with socket.socket() as sk:
        if sk.connect_ex(("127.0.0.1", PORT)) == 0:   # already running → just open the browser
            print("Inventory Sync is already running — opening browser.")
            webbrowser.open(url)
            return
    print(f"\n  {APP_NAME} v{VERSION}\n  Open {url} in your browser (keep this window open).\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=PORT, threads=8)
    except ImportError:
        app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()

"""SQLite storage for the local inventory cache, settings and import history."""
import json
import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "inventory.db")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,          -- WooCommerce product or variation id
    parent_id INTEGER DEFAULT 0,     -- 0 for top-level products
    type TEXT,                       -- simple | variable | variation | grouped | external
    name TEXT,
    variation_label TEXT,
    sku TEXT,
    image TEXT,
    regular_price TEXT,
    sale_price TEXT,
    price TEXT,
    manage_stock TEXT,               -- '1', '0' or 'parent'
    stock_quantity INTEGER,
    stock_status TEXT,
    status TEXT,
    categories TEXT,
    permalink TEXT,
    date_on_sale_from TEXT,
    date_on_sale_to TEXT,
    date_modified TEXT,
    menu_order INTEGER DEFAULT 0,
    synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_sku ON items(sku);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(parent_id);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    kind TEXT,                 -- import | rollback | inline
    source_file TEXT,
    total INTEGER,
    applied INTEGER,
    verified INTEGER,
    failed INTEGER,
    status TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    item_id INTEGER,
    parent_id INTEGER,
    sku TEXT,
    name TEXT,
    before_json TEXT,
    after_json TEXT,
    actual_json TEXT,
    result TEXT,               -- verified | mismatch | error | skipped
    message TEXT
);
"""

DEFAULT_SETTINGS = {
    "site_url": "",
    "consumer_key": "",
    "consumer_secret": "",
    "auth_mode": "basic",            # basic | query
    "verify_ssl": "1",
    "price_mode": "auto",            # auto | regular | sale
    "low_stock": "3",
    "big_price_change_pct": "30",
    "big_stock_change": "100",
    "auto_sync_minutes": "0",
    "include_drafts": "1",
    "last_full_sync": "",
    "last_sync": "",
}


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    return con


_con = None


def con():
    global _con
    if _con is None:
        _con = connect()
        _con.executescript(SCHEMA)
        _con.commit()
    return _con


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- settings ----------------
def get_settings():
    with _lock:
        rows = con().execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


def save_settings(values: dict):
    with _lock:
        c = con()
        for k, v in values.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, "" if v is None else str(v)))
        c.commit()


# ---------------- items ----------------
ITEM_COLS = ["id", "parent_id", "type", "name", "variation_label", "sku", "image", "regular_price", "sale_price",
             "price", "manage_stock", "stock_quantity", "stock_status", "status", "categories", "permalink",
             "date_on_sale_from", "date_on_sale_to", "date_modified", "menu_order", "synced_at"]


def upsert_items(items):
    if not items:
        return
    ts = now()
    with _lock:
        c = con()
        for it in items:
            it = dict(it)
            it["synced_at"] = ts
            vals = [it.get(col) for col in ITEM_COLS]
            c.execute(f"INSERT OR REPLACE INTO items({','.join(ITEM_COLS)}) VALUES ({','.join('?' * len(ITEM_COLS))})", vals)
        c.commit()


def replace_all_items(items):
    with _lock:
        c = con()
        c.execute("DELETE FROM items")
        c.commit()
    upsert_items(items)


def delete_items(ids):
    if not ids:
        return
    with _lock:
        c = con()
        c.executemany("DELETE FROM items WHERE id = ?", [(i,) for i in ids])
        c.commit()


def delete_children_of(parent_id):
    with _lock:
        c = con()
        c.execute("DELETE FROM items WHERE parent_id = ?", (parent_id,))
        c.commit()


def all_items():
    with _lock:
        rows = con().execute("SELECT * FROM items ORDER BY parent_id, menu_order, name").fetchall()
    return [dict(r) for r in rows]


def get_items(ids):
    if not ids:
        return {}
    out = {}
    ids = list(ids)
    with _lock:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            rows = con().execute(f"SELECT * FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            out.update({r["id"]: dict(r) for r in rows})
    return out


def item_count():
    with _lock:
        return con().execute("SELECT COUNT(*) FROM items").fetchone()[0]


# ---------------- runs / history ----------------
def create_run(kind, source_file, total, note=""):
    with _lock:
        c = con()
        cur = c.execute("INSERT INTO runs(created_at, kind, source_file, total, applied, verified, failed, status, note) "
                        "VALUES (?,?,?,?,0,0,0,'running',?)", (now(), kind, source_file, total, note))
        c.commit()
        return cur.lastrowid


def finish_run(run_id, applied, verified, failed, status):
    with _lock:
        c = con()
        c.execute("UPDATE runs SET applied=?, verified=?, failed=?, status=? WHERE id=?",
                  (applied, verified, failed, status, run_id))
        c.commit()


def add_run_item(run_id, item_id, parent_id, sku, name, before, after, actual, result, message=""):
    with _lock:
        c = con()
        c.execute("INSERT INTO run_items(run_id, item_id, parent_id, sku, name, before_json, after_json, actual_json, result, message) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (run_id, item_id, parent_id, sku, name, json.dumps(before), json.dumps(after),
                   json.dumps(actual) if actual is not None else None, result, message))
        c.commit()


def list_runs(limit=100):
    with _lock:
        rows = con().execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id):
    with _lock:
        r = con().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        items = con().execute("SELECT * FROM run_items WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
    if not r:
        return None
    out = dict(r)
    out["items"] = []
    for it in items:
        d = dict(it)
        for k in ("before_json", "after_json", "actual_json"):
            d[k.replace("_json", "")] = json.loads(d.pop(k)) if d.get(k) else None
        out["items"].append(d)
    return out

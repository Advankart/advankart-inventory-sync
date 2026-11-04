# Advankart Inventory Sync

A local app for your store PC. It loads every WooCommerce product and variation with its price and stock,
updates them from a BillBook CSV/Excel export after you review and check each change, then confirms every
update against the live website.

## 1. Install (one time)
**Download:** grab the latest `Advankart-Inventory-Sync-vX.Y.Z.zip` from the
[Releases page](https://github.com/Advankart/advankart-inventory-sync/releases/latest).

1. Install **Python 3.10 or newer** from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** during setup.
2. Unzip this folder somewhere permanent, e.g. `C:\Advankart\InventorySync`.
3. Double-click **install.bat**. It installs everything, creates an **Inventory Sync** shortcut on your desktop, and opens the app.
4. Optional: double-click **enable-autostart.bat** so the app starts with Windows.

Day to day: double-click **Inventory Sync** on the desktop. The app opens at **http://localhost:8765**.
Leave the small black window open (it is minimised) while you use the app.

## 2. Connect WooCommerce
1. In WordPress go to **WooCommerce → Settings → Advanced → REST API → Add key**.
   Use an Administrator user and set permissions to **Read/Write**. You can reuse the keys from your orders app.
2. In the app, open **Settings**, paste the URL (`https://saairaaorganics.com`), the Consumer key and the Consumer secret.
   Click **Test connection**, then **Save settings**.
3. Click **Sync from website** in the top bar.

## 3. Daily workflow: BillBook → website
1. Update stock and prices in BillBook, then export the item list as **CSV or Excel (.xlsx)**.
2. Go to **Import from BillBook** and drop the file in.
3. **Map columns.** SKU, Stock, Selling price and MRP are detected automatically. If a guess is wrong, change it.
4. **Review & validate.** The app syncs the latest website data first, then shows:
   - **To update**: old → new for regular price, sale price and stock
   - **With warnings**: big price or stock jumps, stock tracking being switched on, sale price removed, scheduled sale dates
   - **Errors (skipped)**: duplicate SKUs, negative or non-numeric stock, zero price, the same SKU in the file twice with different values
   - **Not on website**: SKUs in the file that no product has
   - **Website items not in file / without SKU**: to help you keep both systems matched
   Untick any row you don't want to send. You can download the whole preview as a CSV.
5. Click **Update N items on website** and confirm. When the update finishes, the app re-reads every item from
   WooCommerce and marks it **verified** or **mismatch**.
6. If something went wrong, go to **History** and use **Undo this run** to put the old values back. The undo is also verified.

### How "Selling price" is applied
- **Default:** BillBook's selling price updates the WooCommerce **regular price**. If it already matches, nothing changes.
  A sale price you set on the website is **kept**. For example, a product at ₹300 regular with a ₹240 website sale and ₹300 in BillBook: only the stock is updated, and the ₹240 sale stays.
- If a new regular price would not be above the website sale price, the price is **not changed** and the row is flagged so you can fix the sale price in WooCommerce. Stock still updates.
- If you also map an **MRP** column, MRP sets the regular price and a lower selling price sets the sale price.
- On the Map columns step you can untick **Prices** or **Stock** to update only one of them.
- Under **Settings → Import rules** you can switch to "the price customers pay" or "always sale price", or turn off sale-price protection.

Variable (parent) products have no price of their own, so update each variation's SKU.

## 4. Other features
- **Inventory tab:** image, product, and variations listed under their parent, with SKU, regular and sale price, stock and status.
  Search by name or SKU. Filter by in stock, low stock, out of stock, missing SKU, on sale or category.
  Click any price or stock value to edit it on the website straight away. Each edit is verified too.
- **Export:** full stock report or a BillBook-format sheet (SKU, Item Name, Stock, Selling Price, MRP) as Excel or CSV.
- **Auto-sync:** set "Auto-sync every N minutes" in Settings to keep the local copy fresh.

## Troubleshooting
- **401 / cannot list resources:** wrong keys, or the key is Read-only. The app switches to query-string auth by itself if your host strips headers.
- **Blocked by Cloudflare/WAF:** add a Cloudflare custom-rule *Skip* for URI path starting with `/wp-json/wc/` from your store's IP.
  Wordfence can also block it. Allowlist your store IP there.
- **Port in use:** set the environment variable `INVENTORY_PORT` to another port (e.g. 8766).
- The local data lives in the `data\inventory.db` file. Delete it to start fresh. Your keys and history are stored there too, so keep this PC secure.

## Updates
The app checks GitHub for a new release when it opens and every 6 hours. When one is out, a green bar shows
**"Update now"**. The update keeps your settings, keys, synced data and history (the `data` folder), backs up the
current version to `data\backups\`, and then restarts the app. You can also check by hand under **Settings → About & updates**.

## Publishing a new version (developer)
1. Make your changes and test them (`python app.py`).
2. Change `VERSION` in `version.py` (e.g. `1.0.1`).
3. Add a `## v1.0.1 — date` section at the top of `CHANGELOG.md`. It becomes the release notes shown in the app.
4. Commit, then tag and push:
   ```
   git commit -am "v1.0.1: what changed"
   git tag v1.0.1
   git push origin main --tags
   ```
5. GitHub Actions builds the zip and publishes the release. Every installed PC offers the update within 6 hours, or straight away with **Check for updates**.

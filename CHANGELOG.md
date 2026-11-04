# Changelog

## v1.0.1 — 2026-11-04
- **Website sale prices are now protected.** BillBook's selling price updates the WooCommerce regular price. If it already matches the website, nothing changes, and a sale price you set on the website (e.g. ₹300 → ₹240) is never removed by an import.
- New "What to update" checkboxes on the Map columns step let you send only stock or only prices.
- Back navigation in the import steps: use the Back buttons or click a step to go back (e.g. Review → Map columns).
- Optional editing of product name and SKU in the Inventory table. Turn it on under Settings → Editing. Every edit is verified and can be undone.
- New **View** button that opens the product on your website, next to **Edit** (WooCommerce admin).
- New "Protect website sale prices" setting and clearer price options.

## v1.0.0 — 2026-11-04
First release.
- Inventory table: products with variations listed under their parent, plus images, SKU, regular and sale price, stock and status. Includes search, filters, low and out-of-stock counts, and price/stock editing in the table that is checked against the website.
- BillBook import (CSV or Excel): finds the columns itself, checks every row, shows old → new values, applies only the rows you select, then confirms each change on the website.
- History, with downloadable reports and a one-click undo for any update.
- Export of the website's stock as a full report or in BillBook format (Excel or CSV).
- Optional auto-sync.
- One-click updates from GitHub Releases.

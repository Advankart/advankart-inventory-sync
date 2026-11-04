/* Advankart Inventory Sync — front end */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = v => (v === "" || v === null || v === undefined) ? "" : Number(v).toLocaleString("en-IN", {maximumFractionDigits: 2});
const ICON = {
  chev: '<svg viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 9v4M12 17h.01M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
  bad: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
};
const NOIMG = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" fill="#eef2ef"/><path d="M12 27l6-7 4 5 3-3 4 5z" fill="#c6d3cb"/></svg>');

async function api(path, opts = {}) {
  const o = {...opts};
  if (o.body && !(o.body instanceof FormData)) { o.body = JSON.stringify(o.body); o.headers = {"Content-Type": "application/json"}; }
  const r = await fetch(path, o);
  let data; try { data = await r.json(); } catch { throw new Error(`Server error ${r.status}`); }
  if (!r.ok || data.ok === false) throw new Error(data.error || `Error ${r.status}`);
  return data;
}
function toast(msg, kind = "") {
  const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").appendChild(t); setTimeout(() => t.remove(), kind === "bad" ? 7000 : 4000);
}
function modal(html, actions) {
  return new Promise(res => {
    $("#modalBody").innerHTML = html; const a = $("#modalActions"); a.innerHTML = "";
    actions.forEach(([label, cls, val]) => { const b = document.createElement("button"); b.className = "btn " + cls; b.textContent = label;
      b.onclick = () => { $("#modal").classList.add("hidden"); res(val); }; a.appendChild(b); });
    $("#modal").classList.remove("hidden");
  });
}

/* ---------------- jobs ---------------- */
async function runJob(jobId, label) {
  const bar = $("#jobbar"); bar.classList.remove("hidden"); $("#jobText").textContent = label + "…";
  try {
    while (true) {
      await new Promise(r => setTimeout(r, 700));
      const j = await api("/api/job/" + jobId);
      $("#jobText").textContent = `${label}: ${j.progress || "working"}`;
      if (j.state === "done") return j.result;
      if (j.state === "error") throw new Error(j.error);
    }
  } finally { bar.classList.add("hidden"); }
}
async function syncNow(silent) {
  $("#syncBtn").disabled = true;
  try {
    const {job} = await api("/api/sync", {method: "POST"});
    const r = await runJob(job, "Syncing from website");
    if (!silent) toast(`Synced ${r.products} products and ${r.variations} variations in ${r.seconds}s`, "ok");
    await loadItems(); await refreshStatus();
    return true;
  } catch (e) { toast(e.message, "bad"); return false; }
  finally { $("#syncBtn").disabled = false; }
}
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("#siteLabel").textContent = s.site_url ? s.site_url.replace(/^https?:\/\//, "") : "Not connected — open Settings";
    const dot = $("#syncDot");
    if (s.last_sync) {
      const ageMin = (s.last_sync_age ?? 0) / 60;
      $("#syncText").textContent = "Synced " + timeAgo(ageMin);
      dot.className = "dot " + (ageMin < 60 ? "ok" : "old");
    } else { $("#syncText").textContent = "Not synced yet"; dot.className = "dot"; }
    return s;
  } catch { return {}; }
}
function timeAgo(m) { if (m < 1) return "just now"; if (m < 60) return Math.round(m) + " min ago"; if (m < 1440) return Math.round(m / 60) + " h ago"; return Math.round(m / 1440) + " days ago"; }

/* ---------------- tabs ---------------- */
$$("#tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));
function showTab(t) {
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + t));
  if (t === "history") loadHistory();
  if (t === "settings") loadSettings();
  try { localStorage.setItem("inv_tab", t); } catch {}
}

/* ================= INVENTORY ================= */
let ITEMS = [], PARENTS = [], CHILDREN = {}, LOW = 3, OPEN = new Set(), PAGE = 1;
const PER_PAGE = 50;
async function loadItems() {
  const d = await api("/api/items");
  ITEMS = d.items; LOW = d.low_stock;
  CHILDREN = {}; PARENTS = [];
  for (const it of ITEMS) { if (it.parent_id) (CHILDREN[it.parent_id] ||= []).push(it); else PARENTS.push(it); }
  Object.values(CHILDREN).forEach(a => a.sort((x, y) => (x.menu_order - y.menu_order) || (x.id - y.id)));
  PARENTS.sort((a, b) => a.name.localeCompare(b.name));
  const cats = new Set(); PARENTS.forEach(p => (p.categories || "").split(", ").filter(Boolean).forEach(c => cats.add(c)));
  const cur = $("#fCat").value;
  $("#fCat").innerHTML = '<option value="">All categories</option>' + [...cats].sort().map(c => `<option ${c === cur ? "selected" : ""}>${esc(c)}</option>`).join("");
  renderStats(); renderInventory();
}
function sellable() { return ITEMS.filter(i => i.type !== "variable" && i.type !== "grouped"); }
function isLow(i) { return i.manage_stock === "1" && i.stock_quantity !== null && i.stock_quantity > 0 && i.stock_quantity <= LOW; }
function renderStats() {
  const s = sellable();
  const st = [
    ["Products", PARENTS.length, "", ""], ["Variations", ITEMS.length - PARENTS.length, "", ""],
    ["In stock", s.filter(i => i.stock_status === "instock").length, "ok", "instock"],
    ["Low stock (≤" + LOW + ")", s.filter(isLow).length, "warn", "low"],
    ["Out of stock", s.filter(i => i.stock_status === "outofstock").length, "bad", "outofstock"],
    ["Missing SKU", s.filter(i => !i.sku).length, "info", "nosku"],
  ];
  $("#stats").innerHTML = st.map(([k, v, c, f]) => `<div class="stat ${c} ${f ? "click" : ""}" data-f="${f}"><div class="k">${k}</div><div class="v">${v.toLocaleString("en-IN")}</div></div>`).join("");
  $$("#stats .stat.click").forEach(el => el.onclick = () => {
    const f = el.dataset.f;
    if (f === "nosku") { $("#fOther").value = "nosku"; $("#fStock").value = ""; } else { $("#fStock").value = f; $("#fOther").value = ""; }
    PAGE = 1; renderInventory();
  });
}
function matchItem(i, f) {
  const {q, stock, other} = f;
  if (q && !(i.name.toLowerCase().includes(q) || (i.sku || "").toLowerCase().includes(q) || (i.variation_label || "").toLowerCase().includes(q))) return false;
  if (stock === "low" && !isLow(i)) return false;
  if (stock === "untracked" && i.manage_stock === "1") return false;
  if (["instock", "outofstock", "onbackorder"].includes(stock) && i.stock_status !== stock) return false;
  if (other === "nosku" && i.sku) return false;
  if (other === "onsale" && !i.sale_price) return false;
  return true;
}
function filteredParents() {
  const f = {q: $("#q").value.trim().toLowerCase(), stock: $("#fStock").value, other: $("#fOther").value, cat: $("#fCat").value};
  const out = [];
  for (const p of PARENTS) {
    if (f.cat && !(p.categories || "").split(", ").includes(f.cat)) continue;
    if (f.other === "variable" && p.type !== "variable") continue;
    if (f.other === "simple" && p.type !== "simple") continue;
    if (f.other === "draft" && p.status === "publish") continue;
    const kids = CHILDREN[p.id] || [];
    const fx = {...f, other: ["variable", "simple", "draft"].includes(f.other) ? "" : f.other};
    const kidHits = kids.filter(k => matchItem(k, fx));
    const selfHit = (p.type === "variable" && kids.length) ? (fx.q && matchItem(p, {q: fx.q})) && !fx.stock && !fx.other : matchItem(p, fx);
    if (selfHit || kidHits.length) out.push({p, kids: (selfHit && !(fx.stock || fx.other)) ? kids : kidHits, filtered: !selfHit || !!(fx.stock || fx.other)});
  }
  return {list: out, active: !!(f.q || f.stock || f.other || f.cat)};
}
function stockCell(i) {
  if (i.type === "variable" && i.manage_stock !== "1") return `<span class="sub">per variation</span>`;
  if (i.type === "grouped" || i.type === "external") return "";
  const q = i.stock_quantity;
  const cls = i.manage_stock !== "1" ? "" : (q <= 0 ? "zeroq" : (q <= LOW ? "lowq" : ""));
  const shown = i.manage_stock === "1" ? (q ?? 0) : (i.manage_stock === "parent" ? "parent" : "—");
  return `<span class="edit ${cls}" data-id="${i.id}" data-f="stock_quantity" data-v="${i.manage_stock === "1" ? (q ?? 0) : ""}" title="Click to edit">${shown}</span>`;
}
function priceCell(i, f) {
  if (i.type === "variable" || i.type === "grouped") {
    if (f !== "regular_price") return "";
    const kids = CHILDREN[i.id] || []; const ps = kids.map(k => Number(k.price)).filter(n => n > 0);
    if (!ps.length) return "";
    const lo = Math.min(...ps), hi = Math.max(...ps);
    return `<span class="sub">${lo === hi ? "₹" + money(lo) : "₹" + money(lo) + " – " + money(hi)}</span>`;
  }
  const v = i[f];
  return `<span class="edit" data-id="${i.id}" data-f="${f}" data-v="${esc(v)}" title="Click to edit">${v === "" ? '<span class="sub">—</span>' : money(v)}</span>`;
}
function statusCell(i) {
  const map = {instock: ["In stock", "b-ok"], outofstock: ["Out of stock", "b-bad"], onbackorder: ["Backorder", "b-warn"]};
  let h = "";
  if (i.type !== "variable" || !(CHILDREN[i.id] || []).length) { const [t, c] = map[i.stock_status] || [i.stock_status, "b-mut"]; h += `<span class="badge ${c}">${esc(t)}</span> `; }
  if (isLow(i)) h += `<span class="badge b-warn">Low</span> `;
  if (i.sale_price) h += `<span class="badge b-gold">Sale</span> `;
  if (i.status && i.status !== "publish") h += `<span class="badge b-mut">${esc(i.status)}</span>`;
  return h;
}
function rowHtml(i, isVar, hasKids, open) {
  const name = isVar ? `${esc(i.variation_label)}` : (EDIT_NAME ? `<span class="edit txt" data-id="${i.id}" data-f="name" data-v="${esc(i.name)}" title="Click to rename">${esc(i.name)}</span>` : esc(i.name));
  const sub = isVar ? "" : `<div class="sub">${esc(i.type)}${hasKids ? ` · ${(CHILDREN[i.id] || []).length} variations` : ""}${i.categories ? " · " + esc(i.categories) : ""}</div>`;
  return `<tr class="${isVar ? "var" : "par"}" data-row="${i.id}">
    <td>${hasKids ? `<button class="tog ${open ? "open" : ""}" data-tog="${i.id}">${ICON.chev}</button>` : ""}</td>
    <td><img class="thumb ${isVar ? "sm" : ""}" loading="lazy" src="${esc(i.image) || NOIMG}" onerror="this.src='${NOIMG}'"></td>
    <td><div class="pname">${name}</div>${sub}</td>
    <td class="mono">${EDIT_SKU ? `<span class="edit txt" data-id="${i.id}" data-f="sku" data-v="${esc(i.sku)}" title="Click to edit SKU">${i.sku ? esc(i.sku) : '<span class="badge b-info">Add SKU</span>'}</span>` : (i.sku ? esc(i.sku) : (i.type === "variable" ? "" : '<span class="badge b-info">No SKU</span>'))}</td>
    <td class="r">${priceCell(i, "regular_price")}</td><td class="r">${priceCell(i, "sale_price")}</td>
    <td class="r">${stockCell(i)}</td><td>${statusCell(i)}</td>
    <td><div class="acts"><a class="btn ghost" title="Open this product on your website" target="_blank" href="${esc(i.permalink)}">View</a><a class="btn ghost" title="Open in WooCommerce admin" target="_blank" href="${adminLink(i)}">Edit ${ICON.ext}</a></div></td></tr>`;
}
let SITE = "";
function adminLink(i) { return `${SITE}/wp-admin/post.php?post=${i.parent_id || i.id}&action=edit`; }
function renderInventory() {
  const {list, active} = filteredParents();
  const pages = Math.max(1, Math.ceil(list.length / PER_PAGE)); PAGE = Math.min(PAGE, pages);
  const slice = list.slice((PAGE - 1) * PER_PAGE, PAGE * PER_PAGE);
  let h = "";
  for (const {p, kids, filtered} of slice) {
    const hasKids = (CHILDREN[p.id] || []).length > 0;
    const open = OPEN.has(p.id) || (active && filtered && kids.length > 0);
    h += rowHtml(p, false, hasKids, open);
    if (open) for (const k of kids) h += rowHtml(k, true, false, false);
  }
  $("#invBody").innerHTML = h;
  const empty = $("#invEmpty");
  if (!ITEMS.length) { empty.innerHTML = 'No products yet. Add your API keys in <b>Settings</b>, then click <b>Sync from website</b>.'; empty.classList.remove("hidden"); }
  else if (!list.length) { empty.textContent = "No products match these filters."; empty.classList.remove("hidden"); }
  else empty.classList.add("hidden");
  let pg = `<span class="muted">${list.length} products</span>`;
  if (pages > 1) for (let n = 1; n <= pages; n++) if (n === 1 || n === pages || Math.abs(n - PAGE) <= 2) pg += `<button class="${n === PAGE ? "cur" : ""}" data-pg="${n}">${n}</button>`; else if (Math.abs(n - PAGE) === 3) pg += "…";
  $("#pager").innerHTML = pg;
}
$("#pager").onclick = e => { const b = e.target.closest("[data-pg]"); if (b) { PAGE = +b.dataset.pg; renderInventory(); window.scrollTo({top: 0}); } };
["#q", "#fStock", "#fOther", "#fCat"].forEach(s => $(s).addEventListener("input", () => { PAGE = 1; renderInventory(); }));
$("#expandAll").onclick = () => { PARENTS.forEach(p => (CHILDREN[p.id] || []).length && OPEN.add(p.id)); renderInventory(); };
$("#collapseAll").onclick = () => { OPEN.clear(); renderInventory(); };
$("#invBody").addEventListener("click", e => {
  const t = e.target.closest("[data-tog]");
  if (t) { const id = +t.dataset.tog; OPEN.has(id) ? OPEN.delete(id) : OPEN.add(id); renderInventory(); return; }
  const ed = e.target.closest(".edit");
  if (ed && !ed.querySelector("input")) startEdit(ed);
});
function startEdit(el) {
  const id = +el.dataset.id, f = el.dataset.f, old = el.dataset.v;
  const orig = el.innerHTML;
  const isTxt = f === "name" || f === "sku";
  el.innerHTML = `<input value="${esc(old)}" ${isTxt ? "" : 'inputmode="decimal"'}>`;
  const inp = el.querySelector("input"); inp.focus(); inp.select();
  let done = false;
  const cancel = () => { if (!done) { done = true; el.innerHTML = orig; } };
  inp.onkeydown = async ev => {
    if (ev.key === "Escape") cancel();
    if (ev.key === "Enter") {
      const val = inp.value.trim();
      if (val === String(old)) return cancel();
      if (f === "sku" && !confirm(`Change SKU from "${old || "(none)"}" to "${val}"?\n\nRemember to use the same SKU / Item code in BillBook, otherwise imports will not match this item.`)) return cancel();
      done = true; el.innerHTML = '<span class="spinner" style="display:inline-block"></span>';
      try {
        const {job} = await api("/api/item/" + id, {method: "POST", body: {[f]: val}});
        const r = await runJob(job, "Updating website");
        const res = r.results[0] || {};
        if (res.result === "verified") toast("Updated & verified on website ✓", "ok");
        else toast("Update problem: " + (res.message || res.result), "bad");
      } catch (e) { toast(e.message, "bad"); }
      await loadItems();
    }
  };
  inp.onblur = () => setTimeout(cancel, 150);
}

/* ================= IMPORT ================= */
let UP = null, PV = null, PVTAB = "update", SEL = new Set();
let STEP = 1, APPLIED = false;
function canGo(k) {
  if (k === STEP) return false;
  if (APPLIED) return k === 1;               // after an update only "start a new import" makes sense
  if (k === 1) return true;
  if (k === 2) return !!UP;
  if (k === 3) return !!PV && STEP > 3;
  return false;
}
function setStep(n) {
  STEP = n;
  $$(".step").forEach(s => { const k = +s.dataset.s; s.classList.toggle("active", k === n); s.classList.toggle("done", k < n); s.classList.toggle("can", canGo(k)); });
  ["#imp1", "#imp2", "#imp3", "#imp4"].forEach((s, i) => $(s).classList.toggle("hidden", i + 1 !== n));
}
$$(".step").forEach(s => s.onclick = () => { const k = +s.dataset.s; if (!canGo(k)) return; if (k === 1) resetImport(); else setStep(k); });
document.addEventListener("click", e => { const b = e.target.closest("[data-back]"); if (!b) return; const k = +b.dataset.back; if (k === 1) resetImport(); else setStep(k); });
function resetImport() { UP = PV = null; APPLIED = false; $("#fileIn").value = ""; setStep(1); }
const drop = $("#drop");
["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", e => e.dataTransfer.files[0] && uploadFile(e.dataTransfer.files[0]));
$("#fileIn").onchange = e => e.target.files[0] && uploadFile(e.target.files[0]);
async function uploadFile(file) {
  const fd = new FormData(); fd.append("file", file);
  try { UP = await api("/api/import/upload", {method: "POST", body: fd}); } catch (e) { return toast(e.message, "bad"); }
  $("#fileInfo").textContent = `${UP.filename} · ${UP.row_count} rows · ${UP.headers.length} columns`;
  const opts = h => '<option value="">— not in file —</option>' + UP.headers.map(x => `<option ${x === h ? "selected" : ""}>${esc(x)}</option>`).join("");
  $("#mapGrid").innerHTML = Object.entries(UP.fields).map(([k, label]) =>
    `<label>${esc(label)}<select data-map="${k}" class="${UP.mapping[k] ? "set" : ""}">${opts(UP.mapping[k])}</select></label>`).join("");
  $$("#mapGrid select").forEach(s => s.onchange = () => { s.classList.toggle("set", !!s.value); priceNote(); });
  $("#sampleTable").innerHTML = `<thead><tr>${UP.headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
    UP.sample.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("") + "</tbody>";
  PV = null; priceNote(); setStep(2);
}
["#doStock", "#doPrice"].forEach(s => $(s).addEventListener("change", priceNote));
let PRICE_MODE = "regular", PROTECT = true, EDIT_NAME = false, EDIT_SKU = false;
function priceNote() {
  const m = getMapping(); let t;
  const doP = $("#doPrice").checked, doS = $("#doStock").checked;
  const prot = PROTECT ? " Sale prices you set on the website are <b>kept</b>." : "";
  if (!doP) t = "<b>Prices will not be touched</b> — only stock is compared.";
  else if (m.mrp && m.selling_price) t = "<b>MRP</b> → WooCommerce <b>regular price</b>; <b>Selling price</b> → <b>sale price</b> when lower than MRP." + prot;
  else if (m.mrp) t = "<b>MRP</b> → WooCommerce <b>regular price</b>." + prot;
  else if (m.selling_price) t = {regular: "<b>Selling price</b> → WooCommerce <b>regular price</b>. If it already matches the website, nothing changes." + prot,
    effective: "<b>Selling price</b> = the price customers pay: the sale price is updated if the item is on sale, otherwise the regular price." + prot,
    sale: "<b>Selling price</b> → WooCommerce <b>sale price</b>."}[PRICE_MODE] + ' <span class="muted">(change in Settings)</span>';
  else t = "No price column mapped — prices will not change.";
  if (!doS) t += " <b>Stock will not be touched.</b>"; else if (!m.stock) t += " No stock column mapped — stock will not change.";
  $("#priceNote").innerHTML = t;
}
function getMapping() { const m = {}; $$("#mapGrid select").forEach(s => { if (s.value) m[s.dataset.map] = s.value; }); return m; }
$("#previewBtn").onclick = async () => {
  const m = getMapping();
  if (!m.sku) return toast("Please choose the SKU / Item code column", "bad");
  const doS = $("#doStock").checked, doP = $("#doPrice").checked;
  if (!doS && !doP) return toast("Tick Stock and/or Prices to update", "bad");
  if ($("#refreshFirst").checked) { if (!(await syncNow(true))) return; }
  try { PV = await api("/api/import/preview", {method: "POST", body: {upload_id: UP.upload_id, mapping: m, apply_stock: doS, apply_price: doP}}); }
  catch (e) { return toast(e.message, "bad"); }
  SEL = new Set(PV.rows.filter(r => r.status === "update").map(r => r.rid));
  PVTAB = PV.summary.to_update ? "update" : (PV.summary.errors ? "error" : "unmatched");
  renderPreview(); setStep(3);
};
function chg(before, after, k, isMoney = true) {
  const fmt = v => (v === "" || v === null || v === undefined) ? "—" : (isMoney ? "₹" + money(v) : v);
  const b = (k === "stock_quantity" && before.manage_stock !== "1") ? "not tracked" : fmt(before[k]);
  if (!(k in after)) return `<span class="muted">${b}</span>`;
  return `<span class="chg"><span class="old">${b}</span><span class="arrow">→</span><span class="new">${fmt(after[k])}</span></span>`;
}
function renderPreview() {
  const s = PV.summary;
  $("#pvInfo").textContent = `${UP.filename} compared with website data synced ${PV.last_sync || "—"}`;
  $("#pvStats").innerHTML = [["To update", s.to_update, "ok"], ["Stock changes", s.stock_changes, ""], ["Price changes", s.price_changes, ""],
    ["Need attention", s.with_warnings, "warn"], ["Errors (skipped)", s.errors, "bad"], ["No change", s.no_change, ""], ["SKU not on website", s.unmatched, "info"]]
    .map(([k, v, c]) => `<div class="stat ${c}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  const tabs = [["update", `To update (${s.to_update})`], ["warn", `With warnings (${s.with_warnings})`], ["error", `Errors (${s.errors})`],
    ["nochange", `No change (${s.no_change})`], ["unmatched", `Not on website (${s.unmatched})`], ["notinfile", `Website items not in file (${s.website_not_in_file})`],
    ["nosku", `Website items without SKU (${s.website_without_sku})`]];
  $("#pvTabs").innerHTML = tabs.map(([k, l]) => `<button data-t="${k}" class="${k === PVTAB ? "active" : ""}">${l}</button>`).join("");
  $$("#pvTabs button").forEach(b => b.onclick = () => { PVTAB = b.dataset.t; renderPreview(); });
  const T = $("#pvTable");
  if (PVTAB === "unmatched") {
    T.innerHTML = `<thead><tr><th>Line</th><th>SKU in file</th><th>Name in file</th><th class="r">Stock</th><th class="r">Selling price</th></tr></thead><tbody>` +
      (PV.unmatched.map(u => `<tr><td>${u.line}</td><td class="mono">${esc(u.sku)}</td><td>${esc(u.name)}</td><td class="r">${esc(u.stock ?? "")}</td><td class="r">${esc(u.selling_price ?? "")}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">All SKUs in the file were found on the website.</td></tr>`) + "</tbody>";
  } else if (PVTAB === "notinfile" || PVTAB === "nosku") {
    const list = PVTAB === "notinfile" ? PV.not_in_file : PV.no_sku;
    T.innerHTML = `<thead><tr><th>Product</th><th>Variation</th><th>SKU</th><th class="r">Website stock</th></tr></thead><tbody>` +
      (list.map(u => `<tr><td>${esc(u.name)}</td><td>${esc(u.variation_label)}</td><td class="mono">${esc(u.sku || "—")}</td><td class="r">${u.stock_quantity ?? "—"}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">None 🎉</td></tr>`) + "</tbody>";
  } else {
    const rows = PV.rows.filter(r => PVTAB === "warn" ? (r.status === "update" && r.warnings.length) : r.status === PVTAB);
    const selectable = PVTAB === "update" || PVTAB === "warn";
    const allSel = selectable && rows.length && rows.every(r => SEL.has(r.rid));
    T.innerHTML = `<thead><tr>${selectable ? `<th style="width:30px"><input type="checkbox" id="selAll" ${allSel ? "checked" : ""}></th>` : ""}<th style="width:50px"></th><th>Item</th><th>SKU</th>
      <th class="r">Regular price</th><th class="r">Sale price</th><th class="r">Stock</th><th>Checks</th></tr></thead><tbody>` +
      (rows.map(r => `<tr>${selectable ? `<td><input type="checkbox" data-sel="${r.rid}" ${SEL.has(r.rid) ? "checked" : ""}></td>` : ""}
        <td><img class="thumb sm" loading="lazy" src="${esc(r.image) || NOIMG}" onerror="this.src='${NOIMG}'"></td>
        <td><div class="pname">${esc(r.name)}</div>${r.variation_label ? `<div class="sub">↳ ${esc(r.variation_label)}</div>` : ""}<div class="sub">File line ${r.line}${r.file_name ? " · " + esc(r.file_name) : ""}</div></td>
        <td class="mono">${esc(r.sku)}</td><td class="r">${chg(r.before, r.after, "regular_price")}</td><td class="r">${chg(r.before, r.after, "sale_price")}</td>
        <td class="r">${chg(r.before, r.after, "stock_quantity", false)}</td>
        <td><div class="msgs">${r.errors.map(e => `<div class="e">✖ ${esc(e)}</div>`).join("")}${r.warnings.map(w => `<div class="w">⚠ ${esc(w)}</div>`).join("")}${!r.errors.length && !r.warnings.length ? '<span class="badge b-ok">OK</span>' : ""}</div></td></tr>`).join("")
        || `<tr><td colspan="8" class="empty">Nothing here.</td></tr>`) + "</tbody>";
    if (selectable) {
      $$("[data-sel]", T).forEach(c => c.onchange = () => { c.checked ? SEL.add(+c.dataset.sel) : SEL.delete(+c.dataset.sel); updSel(); });
      const sa = $("#selAll"); if (sa) sa.onchange = () => { rows.forEach(r => sa.checked ? SEL.add(r.rid) : SEL.delete(r.rid)); renderPreview(); };
    }
  }
  updSel();
}
function updSel() {
  $("#selInfo").innerHTML = `<b>${SEL.size}</b> of ${PV.summary.to_update} updates selected. Errors and "no change" rows are never sent.`;
  $("#applyBtn").disabled = SEL.size === 0; $("#applyBtn").textContent = `Update ${SEL.size} item${SEL.size === 1 ? "" : "s"} on website`;
}
$("#pvCsv").onclick = () => downloadPost("/api/import/report", {upload_id: UP.upload_id}, "import-preview.csv");
async function downloadPost(url, body, name) {
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const b = await r.blob(); const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name; a.click();
}
$("#applyBtn").onclick = async () => {
  const chosen = PV.rows.filter(r => SEL.has(r.rid));
  const st = chosen.filter(r => "stock_quantity" in r.after).length, pr = chosen.filter(r => "regular_price" in r.after || "sale_price" in r.after).length;
  const w = chosen.filter(r => r.warnings.length).length;
  const ok = await modal(`<h3>Update the live website?</h3><p>This will change <b>${chosen.length}</b> items on <b>${esc(SITE)}</b>:</p>
    <ul><li>${st} stock quantity changes</li><li>${pr} price changes</li>${w ? `<li class="lowq">${w} items have warnings — please make sure you reviewed them</li>` : ""}</ul>
    <p class="muted">Every change is checked against the website afterwards, and you can undo the whole update from History.</p>`,
    [["Cancel", "ghost", false], ["Yes, update website", "primary", true]]);
  if (!ok) return;
  APPLIED = true; setStep(4); $("#resultBox").innerHTML = '<div class="result-hero"><div class="spinner" style="width:40px;height:40px"></div><div><h3>Updating website…</h3><div class="muted">Please keep this window open.</div></div></div>';
  try {
    const {job} = await api("/api/import/apply", {method: "POST", body: {upload_id: UP.upload_id, rids: [...SEL]}});
    const r = await runJob(job, "Updating website");
    showResult(r); loadItems(); refreshStatus();
  } catch (e) { $("#resultBox").innerHTML = `<div class="result-hero"><div class="ic bad">${ICON.bad}</div><div><h3>Update failed</h3><div>${esc(e.message)}</div></div></div><button class="btn" data-reset2>Back</button>`;
    $("[data-reset2]").onclick = () => { APPLIED = false; setStep(3); }; }
};
function showResult(r) {
  const good = r.failed === 0;
  const bad = r.results.filter(x => x.result !== "verified");
  $("#resultBox").innerHTML = `<div class="result-hero"><div class="ic ${good ? "ok" : (r.verified ? "warn" : "bad")}">${good ? ICON.ok : ICON.warn}</div>
    <div><h3>${good ? "All updates applied and verified" : "Update finished with problems"}</h3>
    <div class="muted">${r.verified} of ${r.total} items verified on the website${r.failed ? ` · ${r.failed} need attention` : ""} · Run #${r.run_id}</div></div></div>
    <div class="stats small"><div class="stat ok"><div class="k">Verified</div><div class="v">${r.verified}</div></div>
    <div class="stat bad"><div class="k">Failed / mismatch</div><div class="v">${r.failed}</div></div><div class="stat"><div class="k">Sent</div><div class="v">${r.applied}</div></div></div>
    ${bad.length ? `<h4>Items that need attention</h4><div class="table-wrap"><table class="grid"><thead><tr><th>SKU</th><th>Result</th><th>Details</th></tr></thead><tbody>${bad.map(b => `<tr><td class="mono">${esc(b.sku)}</td><td><span class="badge ${b.result === "mismatch" ? "b-warn" : "b-bad"}">${b.result}</span></td><td>${esc(b.message)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <div class="row-end"><a class="btn" href="/api/runs/${r.run_id}/report.csv">Download report</a><button class="btn" id="rView">View in History</button><button class="btn danger" id="rUndo">Undo this update</button><button class="btn primary" data-reset3>Import another file</button></div>`;
  $("[data-reset3]").onclick = resetImport;
  $("#rView").onclick = () => { showTab("history"); openRun(r.run_id); };
  $("#rUndo").onclick = () => rollback(r.run_id);
}

/* ================= HISTORY ================= */
async function loadHistory() {
  const runs = await api("/api/runs");
  const badge = s => ({verified: "b-ok", partial: "b-warn", failed: "b-bad", running: "b-info"}[s] || "b-mut");
  $("#histTable").innerHTML = `<thead><tr><th>#</th><th>When</th><th>Type</th><th>Source</th><th class="r">Items</th><th class="r">Verified</th><th class="r">Failed</th><th>Status</th><th></th></tr></thead><tbody>` +
    (runs.map(r => `<tr><td>${r.id}</td><td>${esc(r.created_at)}</td><td>${esc(r.kind)}</td><td>${esc(r.source_file)}</td><td class="r">${r.total}</td><td class="r">${r.verified}</td><td class="r">${r.failed}</td>
      <td><span class="badge ${badge(r.status)}">${esc(r.status)}</span></td><td><button class="btn ghost" data-run="${r.id}">Details</button></td></tr>`).join("")
      || '<tr><td colspan="9" class="empty">No updates yet.</td></tr>') + "</tbody>";
  $$("[data-run]").forEach(b => b.onclick = () => openRun(+b.dataset.run));
}
$("#histRefresh").onclick = loadHistory;
async function openRun(id) {
  const r = await api("/api/runs/" + id);
  const D = $("#histDetail"); D.classList.remove("hidden");
  const v = (o, k) => o && k in o ? (o[k] === "" || o[k] === null ? "—" : o[k]) : "";
  D.innerHTML = `<div class="card-head"><div><h3>Run #${r.id} — ${esc(r.kind)}</h3><div class="muted">${esc(r.created_at)} · ${esc(r.source_file)} · ${r.verified}/${r.total} verified</div></div>
    <div class="btns"><a class="btn" href="/api/runs/${r.id}/report.csv">Download report</a>${r.kind !== "rollback" ? `<button class="btn danger" id="undoBtn">Undo this run</button>` : ""}</div></div>
    <div class="table-wrap"><table class="grid"><thead><tr><th>SKU</th><th>Item</th><th class="r">Regular (before → after → website)</th><th class="r">Sale</th><th class="r">Stock</th><th>Result</th></tr></thead><tbody>
    ${r.items.map(it => { const a = it.after || {}; const cell = k => k in a ? `${v(it.before, k)} → <b>${a[k] === "" ? "—" : a[k]}</b> <span class="sub">(site: ${v(it.actual, k)})</span>` : '<span class="muted">—</span>';
      const txt = ["name", "sku"].filter(k => k in a).map(k => `<div class="sub">${k === "name" ? "Name" : "SKU"}: ${esc(v(it.before, k))} → <b>${esc(a[k] || "—")}</b></div>`).join("");
      return `<tr><td class="mono">${esc(it.sku)}</td><td>${esc(it.name)}${txt}${it.message ? `<div class="sub">${esc(it.message)}</div>` : ""}</td><td class="r">${cell("regular_price")}</td><td class="r">${cell("sale_price")}</td><td class="r">${cell("stock_quantity")}</td>
      <td><span class="badge ${it.result === "verified" ? "b-ok" : it.result === "mismatch" ? "b-warn" : "b-bad"}">${esc(it.result)}</span></td></tr>`; }).join("")}</tbody></table></div>`;
  const u = $("#undoBtn"); if (u) u.onclick = () => rollback(id);
  D.scrollIntoView({behavior: "smooth"});
}
async function rollback(id) {
  const ok = await modal(`<h3>Undo run #${id}?</h3><p>The website values for every item in this run will be put back to what they were <b>before</b> the update. The undo is also verified and logged.</p>`,
    [["Cancel", "ghost", false], ["Undo update", "danger", true]]);
  if (!ok) return;
  try {
    const {job} = await api(`/api/runs/${id}/rollback`, {method: "POST"});
    const r = await runJob(job, "Undoing update");
    toast(`Undo complete: ${r.verified}/${r.total} verified`, r.failed ? "bad" : "ok");
    showTab("history"); openRun(r.run_id); loadItems();
  } catch (e) { toast(e.message, "bad"); }
}

/* ================= EXPORT ================= */
$$("[data-exp]").forEach(b => b.onclick = async () => {
  const [layout, fmt] = b.dataset.exp.split(":");
  if ($("#expSync").checked) { if (!(await syncNow(true))) return; }
  location.href = `/api/export?layout=${layout}&format=${fmt}`;
});

/* ================= SETTINGS ================= */
const SKEYS = ["site_url", "consumer_key", "consumer_secret", "auth_mode", "price_mode", "low_stock", "big_price_change_pct", "big_stock_change", "auto_sync_minutes"];
async function loadSettings() {
  const s = await api("/api/settings");
  SKEYS.forEach(k => { const el = $("#s_" + k); if (el) el.value = s[k] ?? ""; });
  $("#s_verify_ssl").checked = s.verify_ssl !== "0"; $("#s_include_drafts").checked = s.include_drafts !== "0";
  if (!["regular", "effective", "sale"].includes(s.price_mode)) $("#s_price_mode").value = "regular";
  ["protect_sale", "allow_edit_name", "allow_edit_sku"].forEach(k => $("#s_" + k).checked = s[k] === "1");
  PROTECT = s.protect_sale !== "0"; EDIT_NAME = s.allow_edit_name === "1"; EDIT_SKU = s.allow_edit_sku === "1";
  PRICE_MODE = $("#s_price_mode").value; SITE = (s.site_url || "").replace(/\/$/, "");
  return s;
}
function collectSettings() {
  const o = {}; SKEYS.forEach(k => o[k] = $("#s_" + k).value.trim());
  o.verify_ssl = $("#s_verify_ssl").checked ? "1" : "0"; o.include_drafts = $("#s_include_drafts").checked ? "1" : "0";
  ["protect_sale", "allow_edit_name", "allow_edit_sku"].forEach(k => o[k] = $("#s_" + k).checked ? "1" : "0"); return o;
}
$("#saveBtn").onclick = async () => { try { await api("/api/settings", {method: "POST", body: collectSettings()}); await loadSettings(); refreshStatus(); toast("Settings saved", "ok"); loadItems(); } catch (e) { toast(e.message, "bad"); } };
$("#testBtn").onclick = async () => {
  try { await api("/api/settings", {method: "POST", body: collectSettings()}); const r = await api("/api/test-connection", {method: "POST"});
    toast(`Connected ✓ — ${r.total_products} products found`, "ok"); loadSettings(); refreshStatus(); }
  catch (e) { toast(e.message, "bad"); }
};
$("#syncBtn").onclick = () => syncNow(false);

/* ================= UPDATES ================= */
let UPD = null;
async function checkUpdate(force) {
  try {
    UPD = await api("/api/update/check" + (force ? "?force=1" : ""));
    $("#aboutText").innerHTML = `Installed version <b>${esc(UPD.current)}</b>` + (UPD.available ? ` · <b style="color:var(--brand2)">version ${esc(UPD.latest)} is available</b>` : " · you are up to date") + ` · <span class="mono">${esc(UPD.repo)}</span>`;
    $("#relLink").href = `https://github.com/${UPD.repo}/releases`;
    let hidden = ""; try { hidden = localStorage.getItem("inv_upd_hide") || ""; } catch {}
    if (UPD.available && (force || hidden !== UPD.latest)) { $("#updText").textContent = `Inventory Sync ${UPD.latest} is available (you have ${UPD.current}).`; $("#updbar").classList.remove("hidden"); }
    else $("#updbar").classList.add("hidden");
    if (force && !UPD.available) toast(UPD.message || `You have the latest version (${UPD.current})`, "ok");
  } catch (e) { if (force) toast(e.message, "bad"); $("#aboutText").textContent = "Could not reach GitHub to check for updates."; }
}
$("#checkUpd").onclick = () => checkUpdate(true);
$("#updHide").onclick = () => { $("#updbar").classList.add("hidden"); try { localStorage.setItem("inv_upd_hide", UPD.latest); } catch {} };
$("#updNotes").onclick = () => modal(`<h3>${esc(UPD.name || "Version " + UPD.latest)}</h3><div class="notes">${esc(UPD.notes || "No notes.")}</div>`, [["Close", "ghost", 0]]);
$("#updNow").onclick = async () => {
  const ok = await modal(`<h3>Update to version ${esc(UPD.latest)}?</h3><p>Your settings, API keys, synced data and history are kept. A backup of the current version is saved first. The app restarts by itself, which takes about 10 seconds.</p><div class="notes">${esc(UPD.notes || "")}</div>`,
    [["Later", "ghost", false], ["Update now", "primary", true]]);
  if (!ok) return;
  try {
    const {job} = await api("/api/update/apply", {method: "POST"});
    let r;
    try { r = await runJob(job, "Updating app"); }
    catch (e) { if (!/Unknown job|fetch|Server error/i.test(e.message)) throw e; r = {updated: true, to: UPD.latest}; }  // app already restarted
    if (!r.updated) return toast(r.message, "ok");
    $("#jobbar").classList.remove("hidden"); $("#jobText").textContent = `Updated to ${r.to}. Restarting…`;
    for (let i = 0; i < 60; i++) {
      await new Promise(res => setTimeout(res, 1500));
      try { const s = await (await fetch("/api/status")).json(); if (s.version === r.to) { location.reload(); return; } } catch {}
    }
    $("#jobText").textContent = "Update installed. If the app did not come back, close it and open Inventory Sync again.";
  } catch (e) { toast(e.message, "bad"); }
};

/* ---------------- boot ---------------- */
(async () => {
  await loadSettings();
  const st = await refreshStatus();
  await loadItems();
  let t = "inventory"; try { t = localStorage.getItem("inv_tab") || t; } catch {}
  if (!st.configured) t = "settings";
  showTab(t);
  checkUpdate(false); setInterval(() => checkUpdate(false), 6 * 3600 * 1000);
  setInterval(refreshStatus, 60000);
  setInterval(async () => { const s = await refreshStatus(); if (s.last_sync && s.last_sync !== window._ls) { if (window._ls) loadItems(); window._ls = s.last_sync; } }, 30000);
})();

const FS_PERIODS = [8, 20];
const FS_PT = {Q1: ["1Q", "qb-1Q"], Q2: ["2Q", "qb-2Q"], Q3: ["3Q", "qb-3Q"], FY: ["通期", "qb-fy"]};
const FS_STD = {JPGAAP: "日本基準", IFRS: "IFRS", USGAAP: "米国基準"};
const FS_SOURCE = {edinet: "有報", tdnet: "XBRL", tdnet_pdf: "PDF"};
const fsEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

let stockFsStatement = "BS";
let stockFsPeriods = FS_PERIODS[0];
let stockFsCache = new Map();
let stockFsView = "basic";
let stockFsOpen = new Map();
let stockFsShown = null;

function resetStockFs(){
  stockFsCache = new Map();
  stockFsOpen = new Map();
  stockFsShown = null;
  $("#st-fs-tbl").innerHTML = "";
  $("#st-fs-meta").textContent = "";
}

function fsAmount(v, unit){
  if(v == null) return "";
  const s = unit === "per_share" ? fmt(v, 2) : fmt(v / 1e6, 0);
  if(/^-0(\.0+)?$/.test(s)) return s.slice(1);
  return v < 0 ? `<span class="loss">${s}</span>` : s;
}

function fsSourceLabel(src){
  return FS_SOURCE[src] || src || "";
}

function fsCell(cell, period, unit, edge){
  const cls = edge ? "num fs-edge" : "num";
  if(!cell) return `<td class="${cls}"></td>`;
  const txt = fsAmount(cell.value, unit);
  if(!period || cell.source === period.source) return `<td class="${cls}">${txt}</td>`;
  const from = fsSourceLabel(cell.source), main = fsSourceLabel(period.source);
  const tip = `${main} に無く、${from} から補った値`;
  return `<td class="${cls} fill" title="${fsEsc(tip)}">${txt}<span class="qb fs-fill">${fsEsc(from)}</span></td>`;
}

function fsEdges(ps){
  return ps.map((p, i) => i > 0 && p.consolidated !== ps[i - 1].consolidated);
}

function fsStdName(s){
  return FS_STD[s] || s || "";
}

function fsHeadCell(p, mixed, edge, stds){
  const [pt, cls] = FS_PT[p.period_type] || [p.period_type || "", ""];
  const single = mixed && p.consolidated === false;
  const tip = [p.period_end, pt, single ? "個別" : "", `出どころ ${p.source_label || fsSourceLabel(p.source)}`,
    p.submitted_at ? `提出 ${p.submitted_at}` : "", p.doc_id || "", fsStdName(p.accounting_standard)]
    .filter(Boolean).join(" / ");
  const badge = single ? `<span class="qb fs-basis">個別</span>` : "";
  return `<th${edge ? ' class="fs-edge"' : ""} title="${fsEsc(tip)}">${fsEsc(p.label)}<span class="g"><span class="qb ${cls}">${fsEsc(pt)}</span>${badge}</span>`
    + `<span class="g fs-src">${fsEsc(p.source_label || fsSourceLabel(p.source))}</span>`
    + (stds ? `<span class="g fs-src fs-std">${fsEsc(fsStdName(p.accounting_standard))}</span>` : "") + `</th>`;
}

function fsMeta(d){
  const std = (d.accounting_standards || []).map(fsStdName).join("・");
  const parts = [d.basis, std, "単位: 百万円（1株当たりは円）"];
  if(d.cumulative) parts.push("期首からの累計");
  return parts.filter(Boolean).join(" / ");
}

function fsEarlierHtml(d, ctx = {}){
  return (d.earlier || []).map((t, i) => {
    const ps = t.periods || [];
    const span = ps.length ? `${ps[0].label}〜${ps[ps.length - 1].label}` : "";
    const std = (t.accounting_standards || []).map(fsStdName).join("・");
    const meta = [std ? `${std}の期` : "前の会計基準の期", span, t.basis].filter(Boolean).join(" / ");
    return `<p class="sub fs-earlier">${fsEsc(meta)}</p>`
      + `<div class="tbl-wrap"><table class="sheet fin fs">${fsTableHtml({...t, statement: t.statement || d.statement, cumulative: d.cumulative}, {...ctx, id: `${ctx.id || ""}e${i}`})}</table></div>`;
  }).join("");
}

const FS_MAX_DEPTH = 6;

const FS_BASIC = {
  BS: new Set(["CashAndDeposits", "CashAndCashEquivalentsIFRS", "CurrentAssets", "CurrentAssetsIFRS", "NoncurrentAssets",
    "NonCurrentAssetsIFRS", "Assets", "AssetsIFRS", "TotalAssets", "CurrentLiabilities", "TotalCurrentLiabilitiesIFRS",
    "NoncurrentLiabilities", "NonCurrentLabilitiesIFRS", "NonCurrentLiabilitiesIFRS", "Liabilities", "LiabilitiesIFRS",
    "TotalLiabilities", "ShareholdersEquity", "EquityAttributableToOwnersOfParentIFRS", "NetAssets", "EquityIFRS"]),
  PL: new Set(["GrossProfit", "GrossProfitIFRS", "OperatingIncome", "OperatingIncomeLoss", "OperatingIncomeIFRS",
    "OperatingProfitLossIFRS", "OrdinaryIncome", "IncomeBeforeIncomeTaxes", "ProfitLossBeforeTaxIFRS", "ProfitLoss",
    "ProfitLossIFRS", "NetIncome", "ProfitLossAttributableToOwnersOfParent", "ProfitAttributableToOwnersOfParent",
    "ProfitLossAttributableToOwnersOfParentIFRS", "ProfitAttributableToOwnersOfParentIFRS", "NetIncomePerShare",
    "BasicEarningsPerShare", "BasicEarningsPerShareIFRS", "BasicEarningsLossPerShareIFRS"]),
  CF: new Set(["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesIFRS",
    "NetCashProvidedByUsedInInvestmentActivities", "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInInvestingActivitiesIFRS", "NetCashProvidedByUsedInFinancingActivities",
    "NetCashProvidedByUsedInFinancingActivitiesIFRS", "NetIncreaseDecreaseInCashAndCashEquivalents",
    "NetIncreaseDecreaseInCashAndCashEquivalentsIFRS", "CashAndCashEquivalents", "CashAndCashEquivalentsIFRS"]),
};
const FS_REVENUE = new Set(["NetSales", "NetSalesIFRS", "SalesIFRS", "Revenue", "Revenue2", "RevenueIFRS", "Revenue2IFRS",
  "OperatingRevenue1", "OperatingRevenue2", "OperatingRevenues", "OperatingRevenuesIFRS", "GrossOperatingRevenue",
  "GrossOperatingRevenues", "OrdinaryIncomeBNK", "OrdinaryRevenuesBK", "OrdinaryRevenuesIN", "OperatingRevenueRWY",
  "OperatingRevenueELE", "OperatingRevenueSEC", "OperatingRevenuesSE", "OperatingRevenuesSpecific",
  "OperatingRevenuesREIT", "NetSalesOfCompletedConstructionContracts", "NetSalesOfCompletedConstructionContractsCNS",
  "NetSalesAndOperatingRevenues", "NetSalesUS", "OperatingRevenuesUS", "RevenuesUS", "TotalRevenuesUS"]);
const FS_CF_START = new Set(["IncomeBeforeIncomeTaxes", "ProfitLossBeforeTaxIFRS", "ProfitLoss", "ProfitLossIFRS"]);
const FS_LEAD = {PL: FS_REVENUE, CF: FS_CF_START};
const FS_BASIC_RE = {
  CF: [/^Depreciation\w*OpeCF(IFRS)?$/, /^PurchaseOfPropertyPlantAndEquipment\w*InvCF(IFRS)?$/,
    /^(Cash)?DividendsPaid(FinCF)?(IFRS)?$/, /^(PurchaseOf|PaymentsForPurchaseOf)Treasury(Stock|Shares)FinCF(IFRS)?$/],
};
const FS_DILUTED = /^Diluted(NetIncomePerShare|EarningsPerShare|EarningsLossPerShare)(IFRS)?$/;
const FS_TOTAL_RE = /^(NetCashProvidedByUsedIn\w+Activities|OtherComprehensiveIncome)(IFRS)?$/;

function fsIsBasic(key, st){
  if(!key || /^(ext|pdf):/.test(key)) return false;
  if(FS_LEAD[st] && FS_LEAD[st].has(key)) return true;
  return (FS_BASIC[st] || new Set()).has(key) || (FS_BASIC_RE[st] || []).some(re => re.test(key));
}

function fsIsTotal(n){
  return !!n.row && (FS_TOTAL_RE.test(n.row.key) || /合計$/.test(n.row.label || ""));
}

function fsGatherKind(n, st, root){
  if(!n.row || n.children.length) return "";
  const k = n.row.key, l = n.row.label || "";
  if(st === "PL" && FS_BASIC.PL.has(k) && !/PerShare/.test(k)) return "major";
  if(/^(ext:)?Subtotal/.test(k) || l === "小計") return "minor";
  return !(root && st === "BS") && /合計$/.test(l) ? "minor" : "";
}

function fsGather(items, st, root){
  const out = [];
  let minor = 0, major = 0;
  const lead = FS_LEAD[st] || new Set();
  const isRev = m => !!m.row && lead.has(m.row.key);
  for(const n of items){
    const kind = fsGatherKind(n, st, root);
    if(kind){
      const take = out.splice(kind === "major" ? major : minor);
      out.push(...take.filter(isRev));
      n.children = take.filter(m => !isRev(m));
      n.after = n.children.length > 0;
      out.push(n);
      minor = out.length;
      if(kind === "major") major = out.length;
      continue;
    }
    const prev = out[out.length - 1];
    if(n.row && FS_DILUTED.test(n.row.key) && prev && prev.row && /PerShare/.test(prev.row.key) && !FS_DILUTED.test(prev.row.key)){
      prev.children.push(n);
      continue;
    }
    out.push(n);
  }
  return out;
}

function fsTree(rows, st){
  const root = {name: "", path: "", items: [], stack: []};
  const secs = new Map([["", root]]);
  for(const r of rows){
    let sec = root;
    for(const h of r.heads || []){
      const path = `${sec.path}/${h.key}:${h.name}`;
      let s = secs.get(path);
      if(!s){
        s = {name: h.name, path, items: [], stack: []};
        secs.set(path, s);
        sec.items.push({sec: s});
      }
      sec = s;
    }
    const node = {row: r, children: []};
    const ind = r.indent || 0;
    const d = Math.min(ind, sec.stack.length);
    if(d) sec.stack[d - 1].children.push(node);
    else sec.items.push(node);
    sec.stack.length = d;
    sec.stack.push(node);
  }
  const build = (sec, root) => {
    const items = sec.items.map(it => it.sec ? build(it.sec, false) : it);
    if(root) return fsGather(items, st, true);
    let at = -1;
    for(let i = items.length - 1; i >= 0; i--) if(fsIsTotal(items[i])){ at = i; break; }
    if(at < 0) return {name: sec.name, path: sec.path, children: fsGather(items, st, false)};
    const total = items[at];
    const rest = fsGather(items.filter((_, i) => i !== at), st, false);
    total.children = [...total.children, ...rest];
    total.path = sec.path;
    total.sec = sec.name;
    return total;
  };
  const top = build(root, true);
  const mark = (n, path) => {
    n.key = n.path || `${path}/${n.row ? n.row.key : n.name}`;
    n.basic = !!n.row && fsIsBasic(n.row.key, st);
    n.children.forEach(c => mark(c, n.key));
    n.hasBasic = n.children.some(c => c.basic || c.hasBasic);
  };
  top.forEach(n => mark(n, ""));
  return top;
}

function fsIsOpen(n, ctx){
  const k = `${ctx.id || ""}|${n.key}`;
  if(ctx.open && ctx.open.has(k)) return ctx.open.get(k);
  return ctx.all ? true : n.hasBasic;
}

function fsNameCell(n, depth, ctx, cls){
  const tog = n.children.length > 0;
  const open = tog && fsIsOpen(n, ctx);
  const tw = `<span class="fs-tw">${tog ? (open ? "▾" : "▸") : ""}</span>`;
  const act = tog ? ` data-key="${fsEsc(`${ctx.id || ""}|${n.key}`)}" data-open="${open ? 1 : 0}" onclick="toggleStockFsRow(this.dataset.key, this.dataset.open === '1')"` : "";
  if(!n.row) return {open, html: `<td class="l${tog ? " fs-tog" : ""}"${act} title="${fsEsc(n.name)}">${tw}${fsEsc(n.name)}</td>`};
  const r = n.row;
  const mark = r.unmatched_pdf
    ? `<span class="qb fs-pdf" title="XBRL の科目に対応が付かなかった短信 PDF の行">PDFのみ</span>` : "";
  const unit = r.unit === "per_share" ? `<span class="sub">（円）</span>` : "";
  return {open, html: `<td class="${cls}${tog ? " fs-tog" : ""}"${act} title="${fsEsc(r.key)}">${tw}${fsEsc(r.label)}${unit}${mark}</td>`};
}

function fsRowHtml(n, d, ps, edges, ctx, par){
  const cell = fsNameCell(n, d, ctx, d ? `l d${d}` : "l");
  if(!n.row) return {open: cell.open, html: `<tr class="fs-sec fs-sec${Math.min(d, FS_MAX_DEPTH - 1)}">${cell.html}<td colspan="${ps.length}"></td></tr>`};
  const cls = [n.row.unmatched_pdf ? "pdf" : "", par ? "fs-par" : "", n.basic ? "fs-basic" : ""].filter(Boolean).join(" ");
  return {open: cell.open, html: `<tr${cls ? ` class="${cls}"` : ""}>${cell.html}`
    + ps.map((p, i) => fsCell((n.row.cells || [])[i], p, n.row.unit, edges[i])).join("") + `</tr>`};
}

function fsNodeHtml(n, depth, ps, edges, ctx){
  const d = Math.min(depth, FS_MAX_DEPTH);
  const kids = () => n.children.map(c => fsNodeHtml(c, depth + 1, ps, edges, ctx)).join("");
  if(!n.children.length || !fsIsOpen(n, ctx) || !n.row || (!n.sec && !n.after)) {
    const own = fsRowHtml(n, d, ps, edges, ctx, n.children.length > 0);
    return own.html + (own.open ? kids() : "");
  }
  if(n.sec){
    const head = fsRowHtml({...n, row: null, name: n.sec}, d, ps, edges, ctx, false);
    return head.html + kids() + fsRowHtml({...n, children: []}, Math.min(depth + 1, FS_MAX_DEPTH), ps, edges, ctx, true).html;
  }
  return kids() + fsRowHtml(n, d, ps, edges, ctx, true).html;
}

function fsTableHtml(d, ctx = {}){
  if(!d || !(d.periods || []).length || !(d.rows || []).length){
    const tip = d && d.empty && d.empty.message ? ` title="${fsEsc(d.empty.message)}"` : "";
    return `<tr><td class="l sub"${tip}>データなし</td></tr>`;
  }
  const ps = d.periods;
  const mixed = d.consolidated == null;
  const edges = fsEdges(ps);
  const stds = new Set(ps.map(p => p.accounting_standard).filter(Boolean)).size > 1;
  let h = `<thead><tr><th class="l">${d.cumulative ? "科目（累計）" : "科目"}</th>`
    + `${ps.map((p, i) => fsHeadCell(p, mixed, edges[i], stds)).join("")}</tr></thead><tbody>`;
  for(const n of fsTree(d.rows, d.statement)) h += fsNodeHtml(n, 0, ps, edges, ctx);
  return h + `</tbody>`;
}

function fsCtx(d){
  return {id: `${d.statement || ""}:`, open: stockFsOpen, all: stockFsView === "all"};
}

function paintStockFs(d){
  stockFsShown = d;
  const ctx = fsCtx(d);
  $("#st-fs-tbl").innerHTML = fsTableHtml(d, ctx);
  $("#st-fs-earlier").innerHTML = fsEarlierHtml(d, ctx);
}

function toggleStockFsRow(key, open){
  stockFsOpen.set(key, !open);
  if(stockFsShown) paintStockFs(stockFsShown);
}

function syncStockFsButtons(){
  document.querySelectorAll("#st-fs-tabs .ftab").forEach(b => b.classList.toggle("on", b.dataset.fs === stockFsStatement));
  document.querySelectorAll("#st-fs-periods .ftab").forEach(b => b.classList.toggle("on", Number(b.dataset.periods) === stockFsPeriods));
  document.querySelectorAll("#st-fs-view .ftab").forEach(b => b.classList.toggle("on", b.dataset.view === stockFsView));
}

function loadStockFs(code, statement, periods){
  const key = `${code}|${statement}|${periods}`;
  if(stockFsCache.has(key)) return stockFsCache.get(key);
  const req = api(`/api/stock/${code}/financials?statement=${statement}&periods=${periods}&consolidated=auto`,
    {signal: stockAc.signal});
  stockFsCache.set(key, req);
  req.catch(() => { if(stockFsCache.get(key) === req) stockFsCache.delete(key); });
  return req;
}

async function renderStockFs(){
  syncStockFsButtons();
  const code = stockCode, st = stockFsStatement, n = stockFsPeriods;
  const current = () => code === stockCode && st === stockFsStatement && n === stockFsPeriods && stockSub === "financials";
  let d;
  try {
    d = await loadStockFs(code, st, n);
  } catch(e){
    if(e.name === "AbortError" || !current()) return;
    $("#st-fs-meta").textContent = "";
    $("#st-fs-legend").hidden = true;
    $("#st-fs-tbl").innerHTML = `<tr><td class="l sub">読み込めませんでした。</td></tr>`;
    $("#st-fs-earlier").innerHTML = "";
    stockFsShown = null;
    return;
  }
  if(!current()) return;
  const has = (d.periods || []).length > 0;
  $("#st-fs-meta").textContent = has ? fsMeta(d) : "";
  $("#st-fs-legend").hidden = !has;
  paintStockFs(d);
}

document.querySelectorAll("#st-fs-tabs .ftab").forEach(b => b.onclick = () => {
  if(b.dataset.fs === stockFsStatement) return;
  stockFsStatement = b.dataset.fs;
  renderStockFs();
});
document.querySelectorAll("#st-fs-periods .ftab").forEach(b => b.onclick = () => {
  const n = Number(b.dataset.periods);
  if(n === stockFsPeriods) return;
  stockFsPeriods = n;
  renderStockFs();
});
document.querySelectorAll("#st-fs-view .ftab").forEach(b => b.onclick = () => {
  if(b.dataset.view === stockFsView) return;
  stockFsView = b.dataset.view;
  stockFsOpen = new Map();
  syncStockFsButtons();
  if(stockFsShown) paintStockFs(stockFsShown);
});

if(typeof window !== "undefined") window.toggleStockFsRow = toggleStockFsRow;

if(typeof module !== "undefined" && module.exports){
  module.exports = {fsAmount, fsCell, fsTableHtml, fsEarlierHtml, fsMeta, fsTree, FS_PERIODS};
}

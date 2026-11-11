const FS_PERIODS = [8, 20];
const FS_PT = {Q1: ["1Q", "qb-1Q"], Q2: ["2Q", "qb-2Q"], Q3: ["3Q", "qb-3Q"], FY: ["通期", "qb-fy"]};
const FS_STD = {JPGAAP: "日本基準", IFRS: "IFRS", USGAAP: "米国基準"};
const FS_SOURCE = {edinet: "有報", tdnet: "XBRL", tdnet_pdf: "PDF"};
const fsEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

let stockFsStatement = "BS";
let stockFsPeriods = FS_PERIODS[0];
let stockFsCache = new Map();

function resetStockFs(){
  stockFsCache = new Map();
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

function fsCell(cell, period, unit){
  if(!cell) return `<td class="num"></td>`;
  const txt = fsAmount(cell.value, unit);
  if(!period || cell.source === period.source) return `<td class="num">${txt}</td>`;
  const from = fsSourceLabel(cell.source), main = fsSourceLabel(period.source);
  const tip = `${main} に無く、${from} から補った値`;
  return `<td class="num fill" title="${fsEsc(tip)}">${txt}<span class="qb fs-fill">${fsEsc(from)}</span></td>`;
}

function fsHeadCell(p){
  const [pt, cls] = FS_PT[p.period_type] || [p.period_type || "", ""];
  const tip = [p.period_end, pt, `出どころ ${p.source_label || fsSourceLabel(p.source)}`,
    p.submitted_at ? `提出 ${p.submitted_at}` : "", p.doc_id || "", FS_STD[p.accounting_standard] || p.accounting_standard || ""]
    .filter(Boolean).join(" / ");
  return `<th title="${fsEsc(tip)}">${fsEsc(p.label)}<span class="g"><span class="qb ${cls}">${fsEsc(pt)}</span></span>`
    + `<span class="g fs-src">${fsEsc(p.source_label || fsSourceLabel(p.source))}</span></th>`;
}

function fsMeta(d){
  const std = (d.accounting_standards || []).map(s => FS_STD[s] || s).join("・");
  const parts = [d.basis, std, "単位: 百万円（1株当たりは円）"];
  if(d.cumulative) parts.push("期首からの累計");
  return parts.filter(Boolean).join(" / ");
}

function fsSecRow(name, level, span){
  return `<tr class="fs-sec fs-sec${level}"><td class="l">${fsEsc(name)}</td><td colspan="${span}"></td></tr>`;
}

function fsTableHtml(d){
  if(!d || !(d.periods || []).length || !(d.rows || []).length){
    const tip = d && d.empty && d.empty.message ? ` title="${fsEsc(d.empty.message)}"` : "";
    return `<tr><td class="l sub"${tip}>データなし</td></tr>`;
  }
  const ps = d.periods;
  const sectioned = d.rows.some(r => r.group || r.section);
  let h = `<thead><tr><th class="l">${d.cumulative ? "科目（累計）" : "科目"}</th>${ps.map(fsHeadCell).join("")}</tr></thead><tbody>`;
  let at = null;
  for(const r of d.rows){
    const g = r.group || null, s = r.section || null;
    if(sectioned){
      if(g && (!at || at.g !== g)) h += fsSecRow(g, 0, ps.length);
      if(s && (!at || at.g !== g || at.s !== s)) h += fsSecRow(s, g ? 1 : 0, ps.length);
      at = {g, s};
    }
    const depth = (g ? 1 : 0) + (s ? 1 : 0);
    const mark = r.unmatched_pdf
      ? `<span class="qb fs-pdf" title="XBRL の科目に対応が付かなかった短信 PDF の行">PDFのみ</span>` : "";
    const unit = r.unit === "per_share" ? `<span class="sub">（円）</span>` : "";
    const cls = depth ? `l d${depth}` : "l";
    h += `<tr${r.unmatched_pdf ? ' class="pdf"' : ""}><td class="${cls}" title="${fsEsc(r.key)}">${fsEsc(r.label)}${unit}${mark}</td>`
      + ps.map((p, i) => fsCell((r.cells || [])[i], p, r.unit)).join("") + `</tr>`;
  }
  return h + `</tbody>`;
}

function syncStockFsButtons(){
  document.querySelectorAll("#st-fs-tabs .ftab").forEach(b => b.classList.toggle("on", b.dataset.fs === stockFsStatement));
  document.querySelectorAll("#st-fs-periods .ftab").forEach(b => b.classList.toggle("on", Number(b.dataset.periods) === stockFsPeriods));
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
    return;
  }
  if(!current()) return;
  const has = (d.periods || []).length > 0;
  $("#st-fs-meta").textContent = has ? fsMeta(d) : "";
  $("#st-fs-legend").hidden = !has;
  $("#st-fs-tbl").innerHTML = fsTableHtml(d);
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

if(typeof module !== "undefined" && module.exports){
  module.exports = {fsAmount, fsCell, fsTableHtml, fsMeta, FS_PERIODS};
}

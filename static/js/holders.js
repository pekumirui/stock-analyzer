// 保有者タブと銘柄 › 株主（KAB-267。docs/DESIGN_holders.md / DESIGN_stock.md）
const hdEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const HD_KIND = {new: "新規5%超え", buy: "買い増し", sell: "売り", below: "5%割れ"};
const HD_COLORS = ["#0077b6", "#d32f2f", "#1b8a5a", "#e6a100", "#8e4ec6", "#0e7490"];
const HD_CHART_N = 6;

function hdToday(){
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function hdYmd(iso){ return iso ? iso.slice(2).replace(/-/g, "/") : "–"; }
function hdOdd(ob, file){
  if(!ob) return false;
  if(ob > hdToday()) return true;
  if(!file) return false;
  const a = Date.parse(ob), b = Date.parse(file);
  return Number.isFinite(a) && Number.isFinite(b) && (a - b) > 30 * 864e5;
}
function hdDateCell(ob, file, pin){
  const odd = hdOdd(ob, file);
  const title = file ? `提出日 ${file}` : "";
  const extra = `${odd ? " odd-date" : ""}${pin ? " " + pin : ""}`;
  return `<td class="num${extra}" title="${hdEsc(title)}">${hdEsc(hdYmd(ob))}</td>`;
}
function hdPct(v){ return v == null ? "–" : `${fmt(v * 100, 2)}%`; }
function hdPt(cur, prev){
  if(cur == null || prev == null) return "–";
  const d = (cur - prev) * 100;
  const cls = d > 0 ? "pos" : d < 0 ? "neg" : "";
  const sign = d > 0 ? "+" : "";
  return `<span class="${cls}">${sign}${fmt(d, 2)}pt</span>`;
}
function hdYen(v){
  if(v == null) return "–";
  const a = Math.abs(v);
  if(a >= 1e8) return `${fmt(v / 1e8, 1)}億円`;
  if(a >= 1e4) return `${fmt(v / 1e4, 0)}万円`;
  return `${fmt(v, 0)}円`;
}
function hdCost(v){ return v == null ? "–" : `${fmt(v, 0)}円`; }
function hdShares(v){ return v == null ? "–" : `${fmt(v, 0)}株`; }
function hdKind(k){ return HD_KIND[k] || k || ""; }
function hdKindCell(k, pin){
  const cls = {new: "qb-init", buy: "qb-up", sell: "qb-dn", below: "qb-dn"}[k] || "";
  return `<td class="l${pin ? " " + pin : ""}"><span class="qb ${cls}">${hdEsc(hdKind(k))}</span></td>`;
}
function hdHolderCell(edinet, name, pin){
  const raw = String(name || edinet || "");
  const pinCls = pin ? ` ${pin}` : "";
  return `<td class="l name${pinCls}" title="${hdEsc(raw)}"><span class="clicky" onclick="event.stopPropagation();gotoHolder('${hdEsc(edinet)}')">${hdEsc(shortName(raw))}</span>${under(`<span class="sub">${hdEsc(edinet || "")}</span>`)}</td>`;
}

function hdIssuerCell(code, name, pin){
  const raw = String(name || "");
  const pinCls = pin ? ` ${pin}` : "";
  if(code){
    return `<td class="l name${pinCls}" title="${hdEsc(raw)}"><span class="clicky" onclick="event.stopPropagation();gotoChart('${hdEsc(code)}')">${hdEsc(shortName(raw))}</span>${under(`<span class="sub">${hdEsc(code.slice(0, 4))}</span>`)}</td>`;
  }
  return `<td class="l name${pinCls}">${hdEsc(raw || "–")}</td>`;
}
function hdPurposeCell(text){
  const s = String(text || "–");
  return `<td class="l" title="${hdEsc(s)}">${hdEsc(s)}</td>`;
}

function drawHdChart(host, series, yFmt){
  if(!host) return;
  const lines = (series || []).filter(s => (s.points || []).some(p => p.y != null && p.date));
  if(!lines.length){ host.innerHTML = ""; return; }
  const dates = [...new Set(lines.flatMap(s => s.points.map(p => p.date).filter(Boolean)))].sort();
  const ys = lines.flatMap(s => s.points.map(p => p.y).filter(v => v != null));
  const yMax = Math.max(0.01, ...ys) * 1.1;
  const W = 640, H = 180, L = 48, R = 12, T = 12, B = 28;
  const plotW = W - L - R, plotH = H - T - B;
  const xOf = i => L + (dates.length === 1 ? plotW / 2 : plotW * i / (dates.length - 1));
  const yOf = v => T + plotH * (1 - v / yMax);
  const ticks = 4;
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img">`;
  for(let i = 0; i <= ticks; i++){
    const v = yMax * i / ticks, y = yOf(v);
    svg += `<line x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e8ecf0"/>`;
    svg += `<text x="${L - 6}" y="${y.toFixed(1)}" font-size="10" text-anchor="end" dominant-baseline="middle" fill="#66707a">${hdEsc(yFmt(v))}</text>`;
  }
  const labelAt = [0, Math.floor((dates.length - 1) / 2), dates.length - 1];
  for(const i of [...new Set(labelAt)]){
    if(i < 0 || i >= dates.length) continue;
    svg += `<text x="${xOf(i).toFixed(1)}" y="${H - 8}" font-size="10" text-anchor="middle" fill="#66707a">${hdEsc(hdYmd(dates[i]))}</text>`;
  }
  for(const s of lines){
    const pts = s.points.filter(p => p.y != null && dates.includes(p.date))
      .map(p => `${xOf(dates.indexOf(p.date)).toFixed(1)},${yOf(p.y).toFixed(1)}`);
    if(pts.length){
      svg += `<path d="M${pts.join("L")}" fill="none" stroke="${s.color}" stroke-width="1.8" stroke-linejoin="round"/>`;
      for(const p of s.points){
        if(p.y == null || !dates.includes(p.date)) continue;
        svg += `<circle cx="${xOf(dates.indexOf(p.date)).toFixed(1)}" cy="${yOf(p.y).toFixed(1)}" r="2.4" fill="${s.color}"/>`;
      }
    }
  }
  svg += `</svg>`;
  const legend = lines.map(s => `<span><i style="background:${s.color}"></i>${hdEsc(s.name)}</span>`).join("");
  host.innerHTML = `<div class="hd-legend">${legend}</div><div class="hd-line">${svg}</div>`;
}

function hdFeedRows(items, withHolder){
  let h = "<thead><tr><th class='pin0'>日付</th><th class='l pin1'>種類</th>";
  if(withHolder) h += "<th class='l name pin2'>保有者</th>";
  h += `<th class='l name${withHolder ? "" : " pin2"}'>銘柄</th><th>保有割合</th></tr></thead><tbody>`;
  for(const r of items){
    const holder = withHolder ? hdHolderCell(r.edinet_code, r.holder_name, "pin2") : "";
    h += `<tr>${hdDateCell(r.obligation_date, r.file_date, "pin0")}
      ${hdKindCell(r.kind, "pin1")}${holder}
      ${hdIssuerCell(r.code, r.issuer_name, withHolder ? "" : "pin2")}
      <td class="num">${hdEsc(hdPct(r.holding_ratio))}${under(hdPt(r.holding_ratio, r.holding_ratio_prev))}</td></tr>`;
  }
  return h + "</tbody>";
}

let hdHomeLoaded = false;
let hdEdinet = "";
let hdData = null;
let hdPick = 0;
let hdAc = null;
let hdLastSuggest = [];

function hdShowHome(){
  $("#hd-top").style.display = "";
  $("#hd-detail").style.display = "none";
}
function hdShowDetail(){
  $("#hd-top").style.display = "none";
  $("#hd-detail").style.display = "";
}

function loadHoldersHome(){
  hdEdinet = "";
  hdShowHome();
  writeRoute("holders");
  if(hdHomeLoaded && $("#hd-feed").innerHTML) return;
  paintHoldersFeed();
}

async function paintHoldersFeed(){
  const msg = $("#hd-feed-msg");
  msg.hidden = true;
  msg.textContent = "";
  $("#hd-feed").innerHTML = "";
  let d;
  try { d = await api("/api/holders/activity"); }
  catch(e){
    msg.textContent = "読み込めませんでした。";
    msg.hidden = false;
    return;
  }
  hdHomeLoaded = true;
  const items = d.items || [];
  if(!items.length){
    msg.textContent = "最近の大量保有の動きはありません。";
    msg.hidden = false;
    return;
  }
  $("#hd-feed").innerHTML = hdFeedRows(items, true);
  pinCols($("#hd-feed"), 3);
}

function hdSuggest(rs){
  const box = $("#hd-suggest");
  box.innerHTML = "";
  for(const s of rs){
    const d = document.createElement("div");
    d.textContent = `${s.name}  ${s.edinet_code}`;
    d.onclick = () => gotoHolder(s.edinet_code);
    box.appendChild(d);
  }
  box.style.display = rs.length ? "block" : "none";
  return rs;
}

$("#hd-sym").addEventListener("input", async e => {
  const q = e.target.value.trim();
  if(!q){ hdSuggest([]); hdLastSuggest = []; return; }
  const d = await api(`/api/holders/search?q=${encodeURIComponent(q)}`).catch(() => ({results: []}));
  if(e.target.value.trim() !== q) return;
  hdLastSuggest = hdSuggest(d.results || []);
});
$("#hd-sym").addEventListener("keydown", e => {
  if(e.key === "Enter" && hdLastSuggest.length) gotoHolder(hdLastSuggest[0].edinet_code);
  if(e.key === "Escape") hdSuggest([]);
});
document.addEventListener("click", e => {
  if(!e.target.closest("#hd-search")) $("#hd-suggest").style.display = "none";
});

function gotoHolder(edinet){
  hdSuggest([]);
  hdLastSuggest = [];
  openHolder(edinet);
}

async function openHolder(edinet){
  hdEdinet = edinet;
  showTab("holders");
  writeRoute("holders", edinet);
  hdShowDetail();
  if(hdAc) hdAc.abort();
  hdAc = new AbortController();
  hdData = null;
  hdPick = 0;
  $("#hd-title").textContent = "";
  $("#hd-holdings").innerHTML = "";
  $("#hd-chart").innerHTML = "";
  $("#hd-series").innerHTML = "";
  $("#hd-activity").innerHTML = "";
  const msg = $("#hd-msg");
  msg.hidden = true;
  msg.textContent = "";
  let d;
  try { d = await api(`/api/holders/${encodeURIComponent(edinet)}`, {signal: hdAc.signal}); }
  catch(e){
    if(e.name === "AbortError") return;
    msg.textContent = String(e.message) === "404" ? "保有者が見つかりません。" : "読み込めませんでした。";
    msg.hidden = false;
    return;
  }
  if(hdEdinet !== edinet) return;
  hdData = d;
  const first = (d.holdings || []).findIndex(x => (x.holding_ratio || 0) >= 0.05);
  hdPick = first >= 0 ? first : 0;
  paintHolder();
}

function paintHolder(){
  const d = hdData;
  if(!d) return;
  $("#hd-title").textContent = `${d.name || ""}  ${d.edinet_code || ""}`;
  const rows = d.holdings || [];
  let h = "<table class='sheet'><thead><tr>"
    + "<th class='l name pin0'>銘柄</th>"
    + "<th>保有割合</th><th title='前回からの差'>差</th><th>株数</th>"
    + "<th title='時価（目安）'>時価</th>"
    + "<th title='平均取得単価（目安）'>取得単価</th>"
    + "<th title='評価損益（目安）'>評価損益</th>"
    + "<th title='報告義務発生日'>義務発生日</th>"
    + "<th class='l'>保有目的</th></tr></thead><tbody>";
  let below = false;
  rows.forEach((r, i) => {
    const isBelow = (r.holding_ratio || 0) < 0.05;
    if(isBelow && !below){
      h += `<tr class="below-h"><td class="l sub" colspan="9">5%未満</td></tr>`;
      below = true;
    }
    const un = r.unrealized;
    const ucls = un > 0 ? "pos" : un < 0 ? "neg" : "";
    h += `<tr class="${isBelow ? "below" : ""}${i === hdPick ? " on" : ""}" data-i="${i}">
      ${hdIssuerCell(r.code, r.name, "pin0")}
      <td class="num">${hdEsc(hdPct(r.holding_ratio))}</td>
      <td class="num">${hdPt(r.holding_ratio, r.holding_ratio_prev)}</td>
      <td class="num">${hdShares(r.shares_held)}</td>
      <td class="num">${hdYen(r.market_value)}</td>
      <td class="num">${hdCost(r.avg_cost)}</td>
      <td class="num ${ucls}">${hdYen(un)}</td>
      ${hdDateCell(r.obligation_date, r.file_date)}
      ${hdPurposeCell(r.purpose)}</tr>`;
  });
  h += "</tbody></table>";
  $("#hd-holdings").innerHTML = rows.length ? h : "<p class='sub'>大量保有報告はありません。</p>";
  pinCols($("#hd-holdings table.sheet"), 1);
  const chartRows = rows.slice(0, HD_CHART_N);
  drawHdChart($("#hd-chart"), chartRows.map((r, i) => ({
    name: shortName(r.name || r.code || ""),
    color: HD_COLORS[i % HD_COLORS.length],
    points: (r.history || []).map(p => ({date: p.date, y: p.ratio})),
  })), v => `${fmt(v * 100, 1)}%`);
  paintHolderSeries();
  const act = d.activity || [];
  $("#hd-activity").innerHTML = act.length ? hdFeedRows(act, false) : "";
  if(act.length) pinCols($("#hd-activity"), 2);
}

function paintHolderSeries(){
  const host = $("#hd-series");
  const r = (hdData && hdData.holdings || [])[hdPick];
  if(!r){ host.innerHTML = ""; return; }
  const hist = r.history || [];
  const title = r.name || r.code || "";
  let tbl = `<h3>${hdEsc(title)} の推移</h3>`;
  tbl += `<div class="hd-pair"><div id="hd-series-ratio"></div><div id="hd-series-shares"></div></div>`;
  tbl += "<div class='tbl-wrap'><table class='sheet'><thead><tr><th>日付</th><th>株数</th><th>保有割合</th></tr></thead><tbody>";
  for(const p of hist){
    tbl += `<tr>${hdDateCell(p.date, p.file_date)}<td class="num">${hdShares(p.shares)}</td><td class="num">${hdEsc(hdPct(p.ratio))}</td></tr>`;
  }
  tbl += "</tbody></table></div>";
  host.innerHTML = hist.length ? tbl : `<h3>${hdEsc(title)} の推移</h3><p class="sub">推移はありません。</p>`;
  if(!hist.length) return;
  const name = shortName(title);
  drawHdChart($("#hd-series-ratio"), [{name: "保有割合", color: HD_COLORS[0], points: hist.map(p => ({date: p.date, y: p.ratio}))}], v => `${fmt(v * 100, 1)}%`);
  drawHdChart($("#hd-series-shares"), [{name: "株数", color: HD_COLORS[1], points: hist.map(p => ({date: p.date, y: p.shares}))}], v => fmt(v, 0));
}

$("#hd-holdings").addEventListener("click", e => {
  const tr = e.target.closest("tr[data-i]");
  if(!tr) return;
  hdPick = +tr.dataset.i;
  $("#hd-holdings tbody tr.on")?.classList.remove("on");
  tr.classList.add("on");
  paintHolderSeries();
});
$("#hd-back").onclick = () => { hdHomeLoaded = false; loadHoldersHome(); };

window.addEventListener("popstate", () => {
  if($("#tab-holders").style.display === "none") return;
  const r = parseRoute(location.hash);
  if(r.tab !== "holders") return;
  if(r.code) openHolder(r.code);
  else loadHoldersHome();
});

function gotoHolderFromStock(edinet){
  if(!edinet) return;
  gotoHolder(edinet);
}

let stockHolders = null;
let stockHoldersReq = null;

function resetStockHolders(){
  stockHolders = null;
  stockHoldersReq = null;
}

function loadStockHolders(){
  if(stockHolders) return Promise.resolve(stockHolders);
  if(stockHoldersReq) return stockHoldersReq;
  const code = stockCode;
  stockHoldersReq = api(`/api/stock/${code}/holders`, {signal: stockAc.signal}).then(d => {
    if(code === stockCode) stockHolders = d;
    return d;
  }).finally(() => { if(code === stockCode) stockHoldersReq = null; });
  return stockHoldersReq;
}

async function renderStockHoldersTab(){
  const code = stockCode;
  let d;
  try { d = await loadStockHolders(); }
  catch(e){
    if(e.name === "AbortError" || code !== stockCode) return;
    $("#st-holders-large").innerHTML = "";
    $("#st-holders-chart").innerHTML = "";
    $("#st-holders-treasury").innerHTML = "";
    stockMsg("読み込めませんでした。");
    return;
  }
  if(code !== stockCode || stockSub !== "holders") return;
  const rows = d.large_holders || [];
  if(!rows.length){
    $("#st-holders-large").innerHTML = "<p class='sub'>大量保有報告はありません。</p>";
    $("#st-holders-chart").innerHTML = "";
  } else {
    let h = "<table class='sheet'><thead><tr><th class='l name pin0'>提出者</th><th>保有割合</th><th title='前回からの差'>差</th><th title='報告義務発生日'>義務発生日</th><th class='l'>保有目的</th></tr></thead><tbody>";
    let below = false;
    for(const r of rows){
      const isBelow = (r.holding_ratio || 0) < 0.05;
      if(isBelow && !below){
        h += `<tr class="below-h"><td class="l sub" colspan="5">5%未満</td></tr>`;
        below = true;
      }
      const raw = r.filer_name || r.filer_edinet_code || "";
      const name = r.filer_edinet_code
        ? `<span class="clicky" onclick="gotoHolderFromStock('${hdEsc(r.filer_edinet_code)}')">${hdEsc(shortName(raw))}</span>${under(`<span class="sub">${hdEsc(r.filer_edinet_code)}</span>`)}`
        : hdEsc(shortName(raw) || "–");
      h += `<tr class="${isBelow ? "below" : ""}"><td class="l name pin0" title="${hdEsc(raw)}">${name}</td>
        <td class="num">${hdEsc(hdPct(r.holding_ratio))}</td>
        <td class="num">${hdPt(r.holding_ratio, r.holding_ratio_prev)}</td>
        ${hdDateCell(r.obligation_date, r.file_date)}
        ${hdPurposeCell(r.purpose)}</tr>`;
    }
    $("#st-holders-large").innerHTML = h + "</tbody></table>";
    pinCols($("#st-holders-large table.sheet"), 1);
    drawHdChart($("#st-holders-chart"), rows.slice(0, HD_CHART_N).map((r, i) => ({
      name: r.filer_name || r.filer_edinet_code || "",
      color: HD_COLORS[i % HD_COLORS.length],
      points: (r.history || []).map(p => ({date: p.date, y: p.ratio})),
    })), v => `${fmt(v * 100, 1)}%`);
  }
  const t = d.treasury;
  const box = $("#st-holders-treasury");
  if(!t){
    box.innerHTML = "<p class='sub'>自己株券買付状況報告書はありません。</p>";
    return;
  }
  const ratio = t.treasury_ratio;
  let html = `<h3>自己株</h3><p>発行済 ${hdShares(t.issued_shares)} / 自己株 ${hdShares(t.treasury_shares)}`;
  if(ratio != null) html += `（${fmt(ratio * 100, 2)}%）`;
  html += ` <span class="sub">${hdEsc(t.report_month || "")}</span></p>`;
  for(const a of t.active || []){
    const pct = a.progress_shares == null ? 0 : Math.max(0, Math.min(1, a.progress_shares));
    html += `<div class="hd-bar-row"><div class="hd-bar" aria-hidden="true"><i style="width:${(pct * 100).toFixed(1)}%"></i></div>`;
    html += `<span>${a.progress_shares == null ? "–" : `${fmt(a.progress_shares * 100, 1)}%`}</span>`;
    html += `<span class="sub">決議 ${hdEsc(a.resolution_date || "–")} 〜 ${hdEsc(a.acquire_to || "")}　累計 ${hdShares(a.cumulative_shares)} / 枠 ${hdShares(a.limit_shares)}</span></div>`;
  }
  box.innerHTML = html;
}

window.gotoHolder = gotoHolder;
window.gotoHolderFromStock = gotoHolderFromStock;
window.loadHoldersHome = loadHoldersHome;
window.openHolder = openHolder;
window.renderStockHoldersTab = renderStockHoldersTab;
window.resetStockHolders = resetStockHolders;

if(window.ResizeObserver){
  const feedWrap = $("#hd-feed") && $("#hd-feed").closest(".tbl-wrap");
  if(feedWrap) new ResizeObserver(() => pinCols($("#hd-feed"), 3)).observe(feedWrap);
  const holdWrap = $("#hd-holdings");
  if(holdWrap) new ResizeObserver(() => pinCols($("#hd-holdings table.sheet"), 1)).observe(holdWrap);
  const actWrap = $("#hd-activity") && $("#hd-activity").closest(".tbl-wrap");
  if(actWrap) new ResizeObserver(() => pinCols($("#hd-activity"), 2)).observe(actWrap);
  const stWrap = $("#st-holders-large");
  if(stWrap) new ResizeObserver(() => pinCols($("#st-holders-large table.sheet"), 1)).observe(stWrap);
}

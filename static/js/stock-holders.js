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

window.gotoHolderFromStock = gotoHolderFromStock;
window.renderStockHoldersTab = renderStockHoldersTab;
window.resetStockHolders = resetStockHolders;

if(window.ResizeObserver){
  const stWrap = $("#st-holders-large");
  if(stWrap) new ResizeObserver(() => pinCols($("#st-holders-large table.sheet"), 1)).observe(stWrap);
}

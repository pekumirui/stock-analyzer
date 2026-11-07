// 銘柄タブの株価（KAB-181 / KAB-300。docs/DESIGN_stock.md「株価」）。チャートは chart-core.js
let stockPriceCode = "";   // 描いた銘柄。違えば描き直す
let stockPriceAc = null;
let stockChart = null;

// ほかのタブの銘柄名から開く（決算速報・スクリーナー・ランキング・サプライ）
function gotoChart(code){
  const from = typeof currentScreenId === "function" ? currentScreenId() : "";
  stockSub = "price";
  stockNewsId = "";
  showTab("stock");
  showStock(code, "price", "", from);
}

// 株価タブを開いたとき。同じ銘柄を描いてあれば描き直さない
function showStockPrice(){
  if(stockPriceCode !== stockCode) loadChart();
}

// 足・下段・業績の段・高安値・PERバンド・対数は localStorage に残す
function loadChartView(){
  const v = {tf: "D", sub: "v", fund: "", hl: true, band: false, log: false};
  try { Object.assign(v, JSON.parse(localStorage.getItem("chart.view") || "{}")); } catch(e) {}
  return v;
}
function saveChartView(){
  try {
    localStorage.setItem("chart.view", JSON.stringify({tf: chartTf(), sub: $("#chart-sub").value,
      fund: $("#chart-fund").value, hl: $("#chart-hl").checked, band: $("#chart-band").checked,
      log: $("#chart-log").checked}));
  } catch(e) {}
}
function chartViewOpts(){
  return {tf: chartTf(), mas: chartParseMas($("#mas").value), sub: $("#chart-sub").value,
          fund: $("#chart-fund").value, hl: $("#chart-hl").checked, band: $("#chart-band").checked,
          log: $("#chart-log").checked,
          rel: $("#rel").checked};
}
const chartTf = () => ($("#tf .ftab.on") || {dataset: {tf: "D"}}).dataset.tf;
function markTf(tf){
  document.querySelectorAll("#tf .ftab").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
}

function ensureStockChart(){
  if(stockChart) return stockChart;
  stockChart = createStockChart($("#chart"), {height: 440});
  stockChart.set(chartViewOpts());
  return stockChart;
}

// 対TOPIX を入れていれば TOPIX も読む（一度だけ）
async function applyRel(signal){
  if(!$("#rel").checked){ stockChart.set({rel: false}); return; }
  try { stockChart.set({rel: true, topix: await chartTopix(signal)}); }
  catch(e){ if(e.name !== "AbortError") stockChart.set({rel: false}); }
}

async function loadChart(){
  const code = stockCode;
  if(!code) return;
  stockPriceCode = code;
  if(stockPriceAc) stockPriceAc.abort();
  stockPriceAc = new AbortController();
  const signal = stockPriceAc.signal;
  const msg = $("#chart-msg");
  let d, perf;
  try {
    [d, perf] = await Promise.all([chartDaily(code, signal), api(`/api/earnings-performance/${code}`, {signal})]);
  } catch(e){
    if(e.name === "AbortError") return;
    stockPriceCode = "";
    msg.textContent = "読み込めませんでした。";
    msg.hidden = false;
    return;
  }
  if(code !== stockCode) return;
  msg.hidden = d.t.length > 0;
  msg.textContent = d.t.length ? "" : "株価データなし";
  const ch = ensureStockChart();
  ch.setData(d);
  ch.showLatest();
  applyRel(signal);
  let h = "<tr><th class='l'>発表日</th><th>+1d</th><th>α</th><th>+5d</th><th>α</th><th>+20d</th><th>α</th><th>+60d</th><th>α</th><th>現在まで</th><th>α</th></tr>";
  for(const p of perf.slice(0, 12)){
    h += `<tr><td class="l">${p.disclosed_date}</td><td>${pct(p.return_1d)}</td><td>${pct(p.alpha_1d)}</td>
      <td>${pct(p.return_5d)}</td><td>${pct(p.alpha_5d)}</td><td>${pct(p.return_20d)}</td><td>${pct(p.alpha_20d)}</td>
      <td>${pct(p.return_60d)}</td><td>${pct(p.alpha_60d)}</td><td>${pct(p.return_to_date)}</td><td>${pct(p.alpha_to_date)}</td></tr>`;
  }
  $("#perf-tbl").innerHTML = h;
}

// 操作は読み直さない。移動平均は Enter か入力から外れたとき（change）
(function setupPriceControls(){
  const v = loadChartView();
  markTf(CHART_TF_NAMES[v.tf] ? v.tf : "D");
  if(v.sub === "tv") $("#chart-sub").value = "tv";
  if(CHART_FUND_NAMES[v.fund]) $("#chart-fund").value = v.fund;
  $("#chart-hl").checked = v.hl !== false;
  $("#chart-band").checked = !!v.band;
  $("#chart-log").checked = !!v.log;
  const redraw = () => { saveChartView(); if(stockChart) stockChart.set(chartViewOpts()); };
  ["#mas", "#chart-sub", "#chart-fund", "#chart-hl", "#chart-band", "#chart-log"].forEach(s => $(s).addEventListener("change", redraw));
  $("#rel").addEventListener("change", () => { if(stockChart) applyRel(stockPriceAc && stockPriceAc.signal); });
  $("#tf").addEventListener("click", e => {
    const b = e.target.closest("[data-tf]");
    if(!b || b.classList.contains("on")) return;
    markTf(b.dataset.tf);
    saveChartView();
    if(stockChart) stockChart.set({tf: b.dataset.tf});
  });
})();

window.gotoChart = gotoChart;
window.loadChart = loadChart;

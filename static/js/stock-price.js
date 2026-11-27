let stockPriceCode = "";
let stockPriceAc = null;
let stockChart = null;

function gotoChart(code){
  const from = typeof currentScreenId === "function" ? currentScreenId() : "";
  stockSub = "price";
  stockNewsId = "";
  showTab("stock");
  showStock(code, "price", "", from);
}

function showStockPrice(){
  if(stockPriceCode !== stockCode) loadChart();
}

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
  const span = [["+1d", "1d"], ["+5d", "5d"], ["+20d", "20d"], ["+60d", "60d"], ["現在まで", "to_date"]];
  let h = "<thead><tr><th class='l'>発表日</th>" + span.map(([t]) => `<th title='下段は対TOPIX超過リターン（α）'>${t}${under(`<span class="sub">(α)</span>`)}</th>`).join("") + "</tr></thead><tbody>";
  for(const p of perf.slice(0, 12)){
    h += `<tr><td class="l">${p.disclosed_date}</td>${span.map(([, k]) => `<td class="num">${pct(p[`return_${k}`])}${under(pctParen(p[`alpha_${k}`]))}</td>`).join("")}</tr>`;
  }
  $("#perf-tbl").innerHTML = h + "</tbody>";
}

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

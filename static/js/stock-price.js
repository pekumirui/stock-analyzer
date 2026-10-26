// 銘柄タブの株価（KAB-181。docs/DESIGN_stock.md「株価」）。もとのチャートタブの中身
let stockPriceCode = "";   // 描いた銘柄。違えば描き直す
let stockPriceAc = null;

// ほかのタブの銘柄名から開く（決算速報・スクリーナー・PTS・サプライ）
function gotoChart(code){
  stockSub = "price";
  stockNewsId = "";
  showTab("stock");
  showStock(code, "price", "");
}

// 株価タブを開いたとき。同じ銘柄を描いてあれば描き直さない
function showStockPrice(){
  if(stockPriceCode !== stockCode) loadChart();
}

async function loadChart(){
  const code = stockCode;
  if(!code) return;
  stockPriceCode = code;
  if(stockPriceAc) stockPriceAc.abort();
  stockPriceAc = new AbortController();
  const signal = stockPriceAc.signal;
  const tf = $("#tf").value, mas = $("#mas").value, rel = $("#rel").checked;
  const msg = $("#chart-msg");
  let d, perf;
  try {
    [d, perf] = await Promise.all([
      api(`/api/chart/${code}?tf=${tf}&ma=${encodeURIComponent(mas)}&rel=${rel}`, {signal}),
      api(`/api/earnings-performance/${code}`, {signal}),
    ]);
  } catch(e){
    if(e.name === "AbortError") return;
    stockPriceCode = "";
    msg.textContent = "読み込めませんでした。";
    msg.hidden = false;
    return;
  }
  if(code !== stockCode) return;
  msg.hidden = d.candles.length > 0;
  msg.textContent = d.candles.length ? "" : "株価データなし";
  chartObj = renderPriceChart($("#chart"), d, {height: 420, prev: chartObj});
  if(rel && d.relative){
    $("#relwrap").style.display = "";
    if(relObj){ try { relObj.remove(); } catch(e) {} }
    $("#relchart").innerHTML = "";
    const th = chartTheme();
    relObj = LightweightCharts.createChart($("#relchart"), {height: 160, autoSize: true, layout: {background: {color: "transparent"}, textColor: th.text}, grid: {vertLines: {color: th.grid}, horzLines: {color: th.grid}}, timeScale: {borderColor: th.border}, rightPriceScale: {borderColor: th.border}});
    relObj.addSeries(LightweightCharts.LineSeries, {color: th.warn, lineWidth: 2}).setData(d.relative);
    relObj.timeScale().fitContent();
  } else $("#relwrap").style.display = "none";
  let h = "<tr><th class='l'>発表日</th><th>+1d</th><th>α</th><th>+5d</th><th>α</th><th>+20d</th><th>α</th><th>+60d</th><th>α</th><th>現在まで</th><th>α</th></tr>";
  for(const p of perf.slice(0, 12)){
    h += `<tr><td class="l">${p.disclosed_date}</td><td>${pct(p.return_1d)}</td><td>${pct(p.alpha_1d)}</td>
      <td>${pct(p.return_5d)}</td><td>${pct(p.alpha_5d)}</td><td>${pct(p.return_20d)}</td><td>${pct(p.alpha_20d)}</td>
      <td>${pct(p.return_60d)}</td><td>${pct(p.alpha_60d)}</td><td>${pct(p.return_to_date)}</td><td>${pct(p.alpha_to_date)}</td></tr>`;
  }
  $("#perf-tbl").innerHTML = h;
}

// 足と対TOPIX は変えたらすぐ、移動平均は Enter か入力から外れたとき（change）に描き直す
["#tf", "#rel", "#mas"].forEach(s => $(s).addEventListener("change", loadChart));

window.gotoChart = gotoChart;
window.loadChart = loadChart;

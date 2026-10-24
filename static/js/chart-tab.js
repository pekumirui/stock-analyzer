$("#sym").addEventListener("input", async e => {
  const q = e.target.value.trim();
  if(q.length<1){ $("#suggest").style.display="none"; return; }
  const rs = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`).catch(()=>[]);
  $("#suggest").innerHTML = rs.map(s=>`<div onclick="pick('${s.code}','${s.name}')">${s.code.slice(0,4)} ${s.name}</div>`).join("");
  $("#suggest").style.display = rs.length?"block":"none";
});
function pick(code,name){ curCode=code; $("#sym").value=name; $("#suggest").style.display="none"; loadChart(); }
function gotoChart(code){ curCode=code; selectTab("chart"); loadChart(); }
async function loadChart(){
  if(!curCode){ return; }
  if(typeof writeRoute === "function") writeRoute("chart", curCode);
  const tf=$("#tf").value, mas=$("#mas").value, rel=$("#rel").checked;
  const d = await api(`/api/chart/${curCode}?tf=${tf}&ma=${mas}&rel=${rel}`);
  const tfLabel = {D:"日足", W:"週足", M:"月足"}[tf] || tf;
  $("#chart-title").innerHTML = `<b>${d.name}</b> <small>${d.code.slice(0,4)} / ${tfLabel}</small>`;
  chartObj = renderPriceChart($("#chart"), d, {height:420, prev:chartObj});
  if(rel && d.relative){
    $("#relwrap").style.display="";
    if(relObj){ try { relObj.remove(); } catch(e) {} }
    $("#relchart").innerHTML="";
    const th = chartTheme();
    relObj = LightweightCharts.createChart($("#relchart"),{height:160,autoSize:true,layout:{background:{color:"transparent"},textColor:th.text},grid:{vertLines:{color:th.grid},horzLines:{color:th.grid}},timeScale:{borderColor:th.border},rightPriceScale:{borderColor:th.border}});
    relObj.addSeries(LightweightCharts.LineSeries, {color:th.warn,lineWidth:2}).setData(d.relative);
    relObj.timeScale().fitContent();
  } else $("#relwrap").style.display="none";
  const perf = await api(`/api/earnings-performance/${curCode}`);
  let h = "<tr><th class='l'>発表日</th><th>+1d</th><th>α</th><th>+5d</th><th>α</th><th>+20d</th><th>α</th><th>+60d</th><th>α</th><th>現在まで</th><th>α</th></tr>";
  for(const p of perf.slice(0,12)){
    h += `<tr><td class="l">${p.disclosed_date}</td><td>${pct(p.return_1d)}</td><td>${pct(p.alpha_1d)}</td>
      <td>${pct(p.return_5d)}</td><td>${pct(p.alpha_5d)}</td><td>${pct(p.return_20d)}</td><td>${pct(p.alpha_20d)}</td>
      <td>${pct(p.return_60d)}</td><td>${pct(p.alpha_60d)}</td><td>${pct(p.return_to_date)}</td><td>${pct(p.alpha_to_date)}</td></tr>`;
  }
  $("#perf-tbl").innerHTML = h;
}

window.pick = pick;
window.gotoChart = gotoChart;
window.loadChart = loadChart;

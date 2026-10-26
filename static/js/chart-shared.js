let chartObj=null, relObj=null, eChartObj=null;

function chartTheme(){
  const s = getComputedStyle(document.documentElement);
  const v = n => s.getPropertyValue(n).trim();
  return {text:v("--sub"), grid:v("--grid"), border:v("--border"),
          up:v("--up"), down:v("--down"), accent:v("--accent"), warn:v("--warn")};
}

const EVENT_LAYERS = [
  {id:"earnings", label:"決算発表", color:"warn", text:"決", key:"earnings_dates"},
];
function loadEvtOn(){
  const base = {};
  EVENT_LAYERS.forEach(l => { base[l.id] = false; });
  try {
    const saved = JSON.parse(localStorage.getItem("chartEventOverlays") || "{}");
    for(const l of EVENT_LAYERS){
      if(typeof saved[l.id] === "boolean") base[l.id] = saved[l.id];
    }
  } catch(e) {}
  return base;
}
let evtOn = loadEvtOn();

function snapEventTime(t, dates, candles){
  if(dates.has(t)) return t;
  // 週足・月足は足の日付が期間末。発表日を含む足（初めて time >= 発表日）に載せる
  for(const c of candles){
    if(c.time >= t) return c.time;
  }
  return null;
}
function applyChartMarkers(state){
  if(!state || !state.cs) return;
  const {cs, d, dates, opt} = state;
  const th = chartTheme();
  const hi = th.accent;
  const byTime = new Map();
  const put = (raw, color, text) => {
    const t = snapEventTime(raw, dates, d.candles);
    if(!t) return;
    const prev = byTime.get(t);
    if(!prev || color === hi)
      byTime.set(t, {time:t, position:"belowBar", color, shape:"arrowUp", text});
  };
  for(const layer of EVENT_LAYERS){
    if(!evtOn[layer.id]) continue;
    for(const t of (d[layer.key] || [])) put(t, th[layer.color] || th.warn, layer.text);
  }
  if(opt.mark) put(opt.mark, hi, "決");
  const markers = [...byTime.values()];
  if(state.markers) state.markers.setMarkers(markers);
  else state.markers = LightweightCharts.createSeriesMarkers(cs, markers);
}
function refreshEventMarkers(){
  if(chartObj && chartObj._sa) applyChartMarkers(chartObj._sa);
  if(eChartObj && eChartObj._sa) applyChartMarkers(eChartObj._sa);
}
function fillEventToggles(){
  const html = `<span class="evt-cap">イベント</span>` + EVENT_LAYERS.map(l =>
    `<label class="evt-label"><input type="checkbox" data-evt="${l.id}"${evtOn[l.id]?" checked":""}>
       <span class="swatch" style="background:var(--${l.color})"></span>${l.label}</label>`
  ).join("");
  document.querySelectorAll(".evt-toggles").forEach(el => {
    el.innerHTML = html;
    el.addEventListener("change", e => {
      const inp = e.target.closest("input[data-evt]");
      if(!inp) return;
      evtOn[inp.dataset.evt] = inp.checked;
      try { localStorage.setItem("chartEventOverlays", JSON.stringify(evtOn)); } catch(err) {}
      document.querySelectorAll(`input[data-evt="${inp.dataset.evt}"]`).forEach(x => { x.checked = inp.checked; });
      refreshEventMarkers();
    });
  });
}

function renderPriceChart(el, d, opt={}){
  if(opt.prev){ try { opt.prev.remove(); } catch(e) {} }
  el.innerHTML = "";
  const th = chartTheme();
  const c = LightweightCharts.createChart(el, {
    height:opt.height||420, autoSize:true,
    layout:{background:{color:"transparent"},textColor:th.text},
    grid:{vertLines:{color:th.grid},horzLines:{color:th.grid}}, timeScale:{borderColor:th.border},
    rightPriceScale:{borderColor:th.border}});
  const cs = c.addSeries(LightweightCharts.CandlestickSeries, {upColor:th.up,downColor:th.down,borderVisible:false,
    wickUpColor:th.up,wickDownColor:th.down});
  cs.setData(d.candles);
  const dates = new Set(d.candles.map(x=>x.time));
  c._sa = {cs, d, dates, opt};
  applyChartMarkers(c._sa);
  const colors=[th.accent, th.warn, "#8e4ec6", th.down];
  Object.entries(d.ma).forEach(([w,pts],i)=>{
    c.addSeries(LightweightCharts.LineSeries, {color:colors[i%4],lineWidth:1,priceLineVisible:false,lastValueVisible:false,
                     title:`MA${w}`}).setData(pts);
  });
  c.timeScale().fitContent();
  // autoSize は生成後のレイアウトで幅が確定するため、確定後にもう一度フィットさせる
  requestAnimationFrame(() => { try { c.timeScale().fitContent(); } catch(e) {} });
  return c;
}

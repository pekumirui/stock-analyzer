// 株価チャートの部品（KAB-300。docs/DESIGN_stock.md「株価」）。銘柄タブの株価と決算速報の #e-chart が使う。
// 日足は銘柄ごとに一度だけ読み、週足・月足・移動平均・対TOPIX はここで作る（足を変えても通信しない）

// ---- 足と指標（DOM に触らない。tests/market/test_chart_core_js.py が node で呼ぶ） ----

const CHART_TF_DAYS = {D: 1, W: 5, M: 20};  // 移動平均の日数 → 本数（週=÷5、月=÷20）
const CHART_TF_STEP = {W: 5, M: 4.3};       // 1 つ細かい足の何本ぶんか
const CHART_TF_FINER = {W: "D", M: "W"};
const CHART_TF_COARSER = {D: "W", W: "M"};
const CHART_THIN = 2;   // 1 本がこれより細くなったら粗い足へ (px)
const CHART_WIDE = 4;   // 細かい足でもこれ以上の幅なら細かい足へ (px)
const CHART_RANGE_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60, "ALL": 0};

const chartTs = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 1000;

// ISO 週の木曜の日付。同じ週なら同じ値
function chartWeekKey(iso){
  const d = new Date(chartTs(iso) * 1000);
  d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7 + 3);
  return d.toISOString().slice(0, 10);
}

// 日足 {t,o,h,l,c,v,tv} → 足。週足は ISO 週、月足は暦月。足の日付は期間の最終営業日
function chartBars(daily, tf){
  if(tf === "D") return daily;
  const keyOf = tf === "W" ? chartWeekKey : (t => t.slice(0, 7));
  const out = {t: [], o: [], h: [], l: [], c: [], v: [], tv: []};
  let key = null, n = -1;
  for(let i = 0; i < daily.t.length; i++){
    const k = keyOf(daily.t[i]);
    if(k !== key){
      key = k; n++;
      for(const f of ["t", "o", "h", "l", "c", "v", "tv"]) out[f].push(daily[f][i]);
      continue;
    }
    out.t[n] = daily.t[i];
    if(daily.h[i] > out.h[n]) out.h[n] = daily.h[i];
    if(daily.l[i] < out.l[n]) out.l[n] = daily.l[i];
    out.c[n] = daily.c[i];
    out.v[n] += daily.v[i];
    out.tv[n] += daily.tv[i];
  }
  return out;
}

const chartMaLen = (days, tf) => Math.max(1, Math.floor(days / CHART_TF_DAYS[tf]));

function chartParseMas(text){
  return [...new Set(String(text || "").split(",").map(x => parseInt(x.trim(), 10)).filter(x => x > 0))];
}

// 単純移動平均。窓が埋まるまでは null
function chartSma(values, n){
  const out = new Array(values.length).fill(null);
  let s = 0;
  for(let i = 0; i < values.length; i++){
    s += values[i];
    if(i >= n) s -= values[i - n];
    if(i >= n - 1) out[i] = Math.round(s / n * 10) / 10;
  }
  return out;
}

// 直前 n 本の平均の k 倍以上か
function chartSpikes(values, n = 20, k = 2){
  const out = new Array(values.length).fill(false);
  let s = 0;
  for(let i = 0; i < values.length; i++){
    if(i >= n && s > 0 && values[i] >= k * s / n) out[i] = true;
    s += values[i];
    if(i >= n) s -= values[i - n];
  }
  return out;
}

// 対TOPIX 相対（TOPIX がある最初の日=100）。日足の日付ごとの値。TOPIX の無い日は直前の値を使う
function chartRelative(daily, topix){
  const out = new Map();
  let j = 0, last = null, base = null;
  for(let i = 0; i < daily.t.length; i++){
    while(j < topix.t.length && topix.t[j] <= daily.t[i]){ last = topix.c[j]; j++; }
    if(last == null) continue;
    if(base == null) base = {c: daily.c[i], tc: last};
    out.set(daily.t[i], Math.round((daily.c[i] / base.c) / (last / base.tc) * 10000) / 100);
  }
  return out;
}

// ズームで変える足。いまの 1 本の幅 px から決める。変えないなら tf のまま
function chartZoomTf(tf, spacing){
  if(spacing < CHART_THIN && CHART_TF_COARSER[tf]) return CHART_TF_COARSER[tf];
  if(CHART_TF_FINER[tf] && spacing / CHART_TF_STEP[tf] >= CHART_WIDE) return CHART_TF_FINER[tf];
  return tf;
}

// t（昇順）のうち from 以上 to 以下の本数
function chartCountIn(t, from, to){
  const lo = lowerBound(t, from), hi = lowerBound(t, to + "~");
  return Math.max(0, hi - lo);
}
function lowerBound(a, x){
  let lo = 0, hi = a.length;
  while(lo < hi){ const m = (lo + hi) >> 1; if(a[m] < x) lo = m + 1; else hi = m; }
  return lo;
}

// 期間 [from, to] を幅 width px に出すとき、1 本が CHART_WIDE 以上になる一番細かい足
function chartPickTf(barsByTf, from, to, width){
  for(const tf of ["D", "W"]){
    const n = chartCountIn(barsByTf(tf).t, from, to);
    if(n === 0 || width / n >= CHART_WIDE) return tf;
  }
  return "M";
}

// 業績の階段 fund {t, eps, op_q, op_ttm} を日足の日付にそろえる（t 以降、次の点まで同じ値）。
// per は 調整後の終値 ÷ 予想EPS、mcop は 時価総額 ÷ 直近 12 か月の営業利益。0 以下・無い日は null
function chartFundDaily(daily){
  const n = daily.t.length, f = daily.fund || {t: []};
  const out = {eps: [], opq: [], opttm: [], per: [], mcop: []};
  let j = -1;
  for(let i = 0; i < n; i++){
    while(j + 1 < f.t.length && f.t[j + 1] <= daily.t[i]) j++;
    const eps = j >= 0 ? f.eps[j] : null, opq = j >= 0 ? f.op_q[j] : null, ttm = j >= 0 ? f.op_ttm[j] : null;
    const mc = daily.mc ? daily.mc[i] : null;
    out.eps.push(eps); out.opq.push(opq); out.opttm.push(ttm);
    out.per.push(eps > 0 ? Math.round(daily.c[i] / eps * 10) / 10 : null);
    out.mcop.push(mc != null && ttm > 0 ? Math.round(mc * 1e6 / ttm * 10) / 10 : null);
  }
  return out;
}

// 昇順の配列の q 点（線形補間）
function chartQuantile(sorted, q){
  if(!sorted.length) return null;
  const x = (sorted.length - 1) * q, lo = Math.floor(x), hi = Math.ceil(x);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (x - lo);
}

// PERバンドの倍率。予想PER の 10%・50%・90% 点。10 倍未満は小数 1 桁、ほかは整数。重なれば 1 本
function chartBandMultiples(per){
  const v = per.filter(x => x != null && x > 0).sort((a, b) => a - b);
  if(!v.length) return [];
  const r = x => x < 10 ? Math.round(x * 10) / 10 : Math.round(x);
  return [...new Set([0.1, 0.5, 0.9].map(q => r(chartQuantile(v, q))))];
}

// YYYY-MM-DD から months か月前（月末は丸める）
function chartMonthsBefore(iso, months){
  const y = +iso.slice(0, 4), m = +iso.slice(5, 7) - 1 - months, d = +iso.slice(8, 10);
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(d, last))).toISOString().slice(0, 10);
}

// 値の書き方。株価は小数 1 桁まで、株数と円は 万・億
const chartPrice = v => v == null ? "–" : Number(v).toLocaleString("ja-JP", {maximumFractionDigits: 1});
function chartQty(v, unit = ""){
  if(v == null) return "–";
  const a = Math.abs(v);
  if(a >= 1e12) return (v / 1e12).toLocaleString("ja-JP", {maximumFractionDigits: 2}) + "兆" + unit;
  if(a >= 1e8) return (v / 1e8).toLocaleString("ja-JP", {maximumFractionDigits: 1}) + "億" + unit;
  if(a >= 1e4) return (v / 1e4).toLocaleString("ja-JP", {maximumFractionDigits: 1}) + "万" + unit;
  return Math.round(v).toLocaleString("ja-JP") + unit;
}

// ---- 読み込み（銘柄ごとに一度。10 分たったら次に開いたとき ETag で確かめる） ----

const CHART_TTL_MS = 10 * 60 * 1000;
const CHART_CACHE_MAX = 30;
const chartCache = new Map();

function chartGet(url){
  const hit = chartCache.get(url);
  if(hit && Date.now() - hit.at < CHART_TTL_MS){
    chartCache.delete(url); chartCache.set(url, hit);  // 使った順に並べ、古いものから捨てる
    return hit.p;
  }
  const p = fetch(url).then(r => { if(!r.ok) throw new Error(r.status); return r.json(); });
  chartCache.set(url, {at: Date.now(), p});
  p.catch(() => { if(chartCache.get(url)?.p === p) chartCache.delete(url); });
  while(chartCache.size > CHART_CACHE_MAX) chartCache.delete(chartCache.keys().next().value);
  return p;
}

// 読み込みは銘柄を変えても止めず（先の分はキャッシュに残す）、待つ側だけ signal で抜ける
function chartAwait(p, signal){
  if(!signal) return p;
  const abort = () => new DOMException("aborted", "AbortError");
  if(signal.aborted) return Promise.reject(abort());
  return new Promise((res, rej) => {
    const on = () => rej(abort());
    signal.addEventListener("abort", on, {once: true});
    p.then(v => { signal.removeEventListener("abort", on); res(v); },
           e => { signal.removeEventListener("abort", on); rej(e); });
  });
}
const chartDaily = (code, signal) => chartAwait(chartGet(`/api/chart/${code}`), signal);
const chartTopix = signal => chartAwait(chartGet("/api/topix"), signal);

// ---- 見た目とイベントの重ね合わせ ----

function chartTheme(){
  const s = getComputedStyle(document.documentElement);
  const v = n => s.getPropertyValue(n).trim();
  return {text: v("--sub"), grid: v("--grid"), border: v("--border"),
          up: v("--up"), down: v("--down"), accent: v("--accent"), warn: v("--warn")};
}
const CHART_VOL_COLOR = "#aab4bf";
const CHART_MA_COLORS = ["accent", "warn", "#8e4ec6", "down"];
const CHART_BAND_COLOR = "#7d8792";
const CHART_EXTRA_PANE_PX = 120;  // 業績の段・対TOPIX を 1 段出すごとに高くする
const CHART_FUND_NAMES = {op: "営業利益", per: "予想PER", mcop: "時価総額÷営業利益"};

const EVENT_LAYERS = [
  {id: "earnings", label: "決算発表", color: "warn", text: "決", key: "earnings_dates"},
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
let evtOn = typeof localStorage === "undefined" ? {} : loadEvtOn();
const chartViews = new Set();

function refreshEventMarkers(){
  chartViews.forEach(v => v.refreshMarkers());
}
function fillEventToggles(){
  const html = `<span class="evt-cap">イベント</span>` + EVENT_LAYERS.map(l =>
    `<label class="evt-label"><input type="checkbox" data-evt="${l.id}"${evtOn[l.id] ? " checked" : ""}>
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

// ---- チャート ----
// el に作る。opt: {height（業績の段・対TOPIX を出すとそのぶん高くする）, onTf(tf), onRange(key|null)}。onTf は足が変わったとき、onRange は手で動かしたとき null
function createStockChart(el, opt = {}){
  const LW = LightweightCharts;
  const th = chartTheme();
  el.innerHTML = "";
  el.style.position = "relative";
  const chart = LW.createChart(el, {
    height: opt.height || 420, autoSize: true,
    layout: {background: {color: "transparent"}, textColor: th.text, panes: {separatorColor: th.border}},
    localization: {locale: "ja-JP", dateFormat: "yyyy/MM/dd"},
    grid: {vertLines: {color: th.grid}, horzLines: {color: th.grid}},
    crosshair: {mode: LW.CrosshairMode.Normal},
    timeScale: {borderColor: th.border, minBarSpacing: 0.5},
    rightPriceScale: {borderColor: th.border},
  });
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  el.appendChild(legend);

  const candle = chart.addSeries(LW.CandlestickSeries, {
    upColor: th.up, downColor: th.down, borderVisible: false, wickUpColor: th.up, wickDownColor: th.down,
    priceFormat: {type: "custom", formatter: chartPrice, minMove: 0.1}}, 0);
  const sub = chart.addSeries(LW.HistogramSeries, {
    priceLineVisible: false, lastValueVisible: false,
    priceFormat: {type: "custom", formatter: v => chartQty(v), minMove: 1}}, 1);
  sub.priceScale().applyOptions({scaleMargins: {top: 0.1, bottom: 0}});
  let maSeries = [], bandSeries = [], extra = [], markers = null;
  const baseHeight = opt.height || 420;
  el.style.height = baseHeight + "px";

  const st = {daily: null, byTf: {}, rel: null, tf: "D", mode: "A", mas: [50, 150, 200], sub: "v",
              log: false, relOn: false, topix: null, mark: null, bars: null, idx: new Map(),
              fund: "", band: false, fd: null, dIdx: new Map(), quiet: 0, userMoved: false};
  const barsOf = tf => st.byTf[tf] || (st.byTf[tf] = chartBars(st.daily, tf));
  const width = () => chart.timeScale().width() || el.clientWidth || 600;

  function layoutPanes(){
    const panes = chart.panes();
    if(panes[0]) panes[0].setStretchFactor(4);
    if(panes[1]) panes[1].setStretchFactor(1);
    for(let k = 2; k < panes.length; k++) panes[k].setStretchFactor(1.15);
  }

  // 自分で動かしたぶんは手で動かしたことにしない（範囲の通知は描画のフレームで届くので 2 フレーム待つ）
  function quietly(fn){
    st.quiet++;
    try { fn(); } finally { requestAnimationFrame(() => requestAnimationFrame(() => { st.quiet--; })); }
  }
  const setRange = (from, to) => quietly(() => chart.timeScale().setVisibleRange({from: chartTs(from), to: chartTs(to)}));
  function visible(){
    const r = chart.timeScale().getVisibleRange();
    if(!r) return null;
    const iso = x => new Date(x * 1000).toISOString().slice(0, 10);
    return {from: iso(r.from), to: iso(r.to)};
  }

  function drawMarkers(){
    if(!st.bars) return;
    const t = st.bars.t;
    const snap = iso => { const i = lowerBound(t, iso); return i < t.length ? t[i] : null; };  // 発表日を含む足
    const byTime = new Map();
    const put = (raw, color, text) => {
      const at = snap(raw);
      if(!at) return;
      const prev = byTime.get(at);
      if(!prev || color === th.accent)
        byTime.set(at, {time: chartTs(at), position: "belowBar", color, shape: "arrowUp", text});
    };
    for(const layer of EVENT_LAYERS){
      if(!evtOn[layer.id]) continue;
      for(const d of (st.daily[layer.key] || [])) put(d, th[layer.color] || th.warn, layer.text);
    }
    if(st.mark) put(st.mark, th.accent, "決");
    const list = [...byTime.values()].sort((a, b) => a.time - b.time);
    if(markers) markers.setMarkers(list);
    else markers = LW.createSeriesMarkers(candle, list);
  }

  function drawMas(){
    maSeries.forEach(s => chart.removeSeries(s));
    maSeries = st.mas.map((w, i) => {
      const c = CHART_MA_COLORS[i % CHART_MA_COLORS.length];
      const s = chart.addSeries(LW.LineSeries, {color: th[c] || c, lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false, title: `MA${w}`}, 0);
      const ma = chartSma(st.bars.c, chartMaLen(w, st.tf));
      const pts = [];
      for(let k = 0; k < ma.length; k++) if(ma[k] != null) pts.push({time: chartTs(st.bars.t[k]), value: ma[k]});
      s.setData(pts);
      return s;
    });
  }

  function drawSub(){
    const b = st.bars, vals = st.sub === "tv" ? b.tv : b.v;
    const spike = chartSpikes(vals);
    sub.setData(b.t.map((t, i) => ({time: chartTs(t), value: vals[i], color: spike[i] ? th.accent : CHART_VOL_COLOR})));
    sub.applyOptions({priceFormat: {type: "custom", formatter: v => chartQty(v), minMove: 1}});
  }

  // 足の日付ごとの値（業績・対TOPIX）。無い日は線を切る（whitespace）
  function barPoints(get){
    return st.bars.t.map(t => {
      const v = get(t);
      return v == null ? {time: chartTs(t)} : {time: chartTs(t), value: v};
    });
  }
  const fundAt = (key, t) => { const i = st.dIdx.get(t); return i == null ? null : st.fd[key][i]; };

  // ローソク足の段に PERバンド（予想EPS × n 倍）。縦軸の伸び縮みには使わない
  function drawBand(){
    bandSeries.forEach(x => chart.removeSeries(x));
    bandSeries = [];
    if(!st.band || !st.fd) return;
    bandSeries = chartBandMultiples(st.fd.per).map(m => {
      const x = chart.addSeries(LW.LineSeries, {color: CHART_BAND_COLOR, lineWidth: 1, lineStyle: LW.LineStyle.Dashed,
        lineType: LW.LineType.WithSteps, priceLineVisible: false, crosshairMarkerVisible: false,
        title: `PER ${m}倍`, autoscaleInfoProvider: () => null,
        priceFormat: {type: "custom", formatter: chartPrice, minMove: 0.1}}, 0);
      x.setData(barPoints(t => { const e = fundAt("eps", t); return e > 0 ? Math.round(e * m * 10) / 10 : null; }));
      return x;
    });
  }

  // 3 段目からの段（業績の段、対TOPIX）を作り直す。出す段の数だけチャートを高くする
  function drawExtra(){
    extra.forEach(x => chart.removeSeries(x));
    extra = [];
    while(chart.panes().length > 2) chart.removePane(chart.panes().length - 1);
    let pane = 2;
    const line = (o, data) => {
      const x = chart.addSeries(LW.LineSeries, {lineWidth: 2, priceLineVisible: false, ...o}, pane);
      x.setData(data);
      extra.push(x);
      return x;
    };
    const times = {type: "custom", formatter: v => chartPrice(v) + "倍", minMove: 0.1};
    if(st.fund && st.fd){
      if(st.fund === "op"){
        const yen = {type: "custom", formatter: v => chartQty(v, "円"), minMove: 1};
        line({color: th.accent, title: "四半期", lineType: LW.LineType.WithSteps, priceFormat: yen},
             barPoints(t => fundAt("opq", t)));
        line({color: th.warn, title: "12か月", lineType: LW.LineType.WithSteps, priceFormat: yen},
             barPoints(t => fundAt("opttm", t)));
      } else {
        const x = line({color: th.accent, title: CHART_FUND_NAMES[st.fund], priceFormat: times},
                       barPoints(t => fundAt(st.fund, t)));
        const v = st.fd[st.fund].filter(y => y != null && y > 0).sort((a, b) => a - b);
        if(st.fund === "per" && v.length)
          x.createPriceLine({price: Math.round(chartQuantile(v, 0.5) * 10) / 10, color: th.text, lineWidth: 1,
                             lineStyle: LW.LineStyle.Dotted, axisLabelVisible: true, title: "中央値"});
      }
      pane++;
    }
    if(st.relOn && st.topix){
      if(!st.rel) st.rel = chartRelative(st.daily, st.topix);
      line({color: th.warn, title: "対TOPIX", priceFormat: {type: "price", precision: 1, minMove: 0.1}},
           barPoints(t => st.rel.get(t)));
      pane++;
    }
    el.style.height = baseHeight + (pane - 2) * CHART_EXTRA_PANE_PX + "px";
    layoutPanes();
  }

  function drawLegend(i){
    const b = st.bars;
    if(!b || !b.t.length){ legend.innerHTML = ""; return; }
    if(i == null || i < 0 || i >= b.t.length) i = b.t.length - 1;
    const prev = i > 0 ? b.c[i - 1] : null;
    let chg = "";
    if(prev){
      const d = b.c[i] - prev, cls = d > 0 ? "up" : d < 0 ? "down" : "";
      chg = ` <span class="${cls}">${d > 0 ? "+" : ""}${chartPrice(d)} (${d > 0 ? "+" : ""}${(d / prev * 100).toFixed(2)}%)</span>`;
    }
    const tfName = {D: "日足", W: "週足", M: "月足"}[st.tf];
    legend.innerHTML = `<span class="sub">${b.t[i].replaceAll("-", "/")} ${tfName}</span> `
      + `始 ${chartPrice(b.o[i])} 高 ${chartPrice(b.h[i])} 安 ${chartPrice(b.l[i])} 終 <b>${chartPrice(b.c[i])}</b>${chg}`
      + `<br>出来高 ${chartQty(b.v[i], "株")} 売買代金 ${chartQty(b.tv[i], "円")}`
      + legendFund(b.t[i]);
  }
  function legendFund(t){
    if(!st.fund || !st.fd) return "";
    if(st.fund === "op")
      return `<br>営業利益 四半期 ${chartQty(fundAt("opq", t), "円")} 12か月 ${chartQty(fundAt("opttm", t), "円")}`;
    const v = fundAt(st.fund, t);
    return `<br>${CHART_FUND_NAMES[st.fund]} ${v == null ? "–" : chartPrice(v) + "倍"}`;
  }

  // 足を tf にして全部描き直す。range があればその期間を出す
  function applyTf(tf, range){ quietly(() => applyTfNow(tf, range)); }
  function applyTfNow(tf, range){
    st.tf = tf;
    st.bars = barsOf(tf);
    st.idx = new Map(st.bars.t.map((t, i) => [chartTs(t), i]));
    const b = st.bars;
    candle.setData(b.t.map((t, i) => ({time: chartTs(t), open: b.o[i], high: b.h[i], low: b.l[i], close: b.c[i]})));
    drawMas(); drawBand(); drawSub(); drawExtra(); drawMarkers(); drawLegend();
    if(range) setRange(range.from, range.to);
    if(opt.onTf) opt.onTf(tf);
  }

  // ズームで足を変える（足が自動のときだけ）
  let zoomPending = false;
  chart.timeScale().subscribeVisibleLogicalRangeChange(lr => {
    if(!st.bars || !lr) return;
    if(!st.quiet && !zoomPending){
      st.userMoved = true;
      if(opt.onRange) opt.onRange(null);
    }
    if(st.mode !== "A" || zoomPending) return;
    zoomPending = true;
    requestAnimationFrame(() => {
      zoomPending = false;
      const r = chart.timeScale().getVisibleLogicalRange();
      if(!r || r.to - r.from <= 0) return;
      const next = chartZoomTf(st.tf, width() / (r.to - r.from));
      if(next !== st.tf){ const v = visible(); applyTf(next, v); }
    });
  });
  chart.subscribeCrosshairMove(p => drawLegend(p && p.time != null ? st.idx.get(p.time) : null));

  // 期間 [from, to] を出す。足が自動なら足も選ぶ
  function showSpan(from, to){
    const tf = st.mode === "A" ? chartPickTf(barsOf, from, to, width()) : st.mode;
    if(tf !== st.tf) applyTf(tf);
    setRange(from, to);
    st.userMoved = false;
    // 幅は作った直後のレイアウトで決まるので、決まったらもう一度合わせる
    requestAnimationFrame(() => { if(!st.userMoved) setRange(from, to); });
  }

  const view = {
    chart,
    // 銘柄を差し替える。opt2: {mark}
    setData(daily, opt2 = {}){
      st.daily = daily; st.byTf = {}; st.rel = null; st.mark = opt2.mark || null;
      st.fd = chartFundDaily(daily);
      st.dIdx = new Map(daily.t.map((t, i) => [t, i]));
      applyTf(st.mode === "A" ? st.tf : st.mode);
    },
    // 表示の設定を変える。見えている期間は保つ
    set(o){
      const keep = st.bars ? visible() : null;
      if(o.mode !== undefined) st.mode = o.mode;
      if(o.mas !== undefined) st.mas = o.mas;
      if(o.sub !== undefined) st.sub = o.sub;
      if(o.log !== undefined) candle.priceScale().applyOptions({mode: o.log ? LW.PriceScaleMode.Logarithmic : LW.PriceScaleMode.Normal});
      if(o.rel !== undefined) st.relOn = o.rel;
      if(o.fund !== undefined) st.fund = o.fund;
      if(o.band !== undefined) st.band = o.band;
      if(o.topix !== undefined){ st.topix = o.topix; st.rel = null; }
      if(!st.daily) return;
      const tf = st.mode === "A" ? st.tf : st.mode;
      applyTf(tf, keep);
    },
    // 期間ボタン（CHART_RANGE_MONTHS のキー）
    showRange(key){
      const t = st.daily && st.daily.t;
      if(!t || !t.length) return;
      const last = t[t.length - 1], m = CHART_RANGE_MONTHS[key];
      showSpan(m ? chartMonthsBefore(last, m) : t[0], last);
    },
    // iso の前後 months か月
    showAround(iso, months){
      const t = st.daily.t, last = t[t.length - 1];
      const to = chartMonthsBefore(iso, -months);
      showSpan(chartMonthsBefore(iso, months), to < last ? to : last);
    },
    refreshMarkers: drawMarkers,
    tf: () => st.tf,
    destroy(){
      chartViews.delete(view);
      try { chart.remove(); } catch(e) {}
      el.innerHTML = "";
    },
  };
  layoutPanes();
  chartViews.add(view);
  return view;
}

if(typeof module !== "undefined" && module.exports){
  module.exports = {chartFundDaily, chartQuantile, chartBandMultiples, chartBars, chartWeekKey, chartSma, chartMaLen, chartParseMas, chartSpikes, chartRelative,
                    chartZoomTf, chartPickTf, chartCountIn, chartMonthsBefore, chartPrice, chartQty};
}

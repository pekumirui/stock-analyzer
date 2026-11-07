// 株価チャートの部品（KAB-300。docs/DESIGN_stock.md「株価」）。銘柄タブの株価と決算速報の #e-chart が使う。
// 日足は銘柄ごとに一度だけ読み、週足・月足・移動平均・対TOPIX はここで作る（足を変えても通信しない）

// ---- 足と指標（DOM に触らない。tests/market/test_chart_core_js.py が node で呼ぶ） ----

const CHART_TF_DAYS = {D: 1, W: 5, M: 20, Y: 250};  // 移動平均の日数 → 本数（週=÷5、月=÷20、年=÷250）
const CHART_TF_NAMES = {D: "日足", W: "週足", M: "月足", Y: "年足"};
const CHART_BAR_PX = 6;  // 週足・月足・年足は 1 本がこの幅になるだけ最新を出す

const chartTs = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 1000;

// ISO 週の木曜の日付。同じ週なら同じ値
function chartWeekKey(iso){
  const d = new Date(chartTs(iso) * 1000);
  d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7 + 3);
  return d.toISOString().slice(0, 10);
}

// 日足 {t,o,h,l,c,v,tv} → 足。週足は ISO 週、月足は暦月、年足は暦年。足の日付は期間の最終営業日
function chartBars(daily, tf){
  if(tf === "D") return daily;
  const keyOf = tf === "W" ? chartWeekKey : tf === "M" ? (t => t.slice(0, 7)) : (t => t.slice(0, 4));
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

// 移動平均の本数。2 本に満たなければ 0（線を引かない）
const chartMaLen = (days, tf) => { const n = Math.floor(days / CHART_TF_DAYS[tf]); return n >= 2 ? n : 0; };

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

function lowerBound(a, x){
  let lo = 0, hi = a.length;
  while(lo < hi){ const m = (lo + hi) >> 1; if(a[m] < x) lo = m + 1; else hi = m; }
  return lo;
}

// 最新の n 本の足を出す期間 [from, to]（日足の日付）。from は先頭の足の期間の初日
function chartLatestSpan(daily, bars, n){
  const t = bars.t, i = Math.max(0, t.length - n);
  return [chartSpanStart(daily, bars, i), t[t.length - 1]];
}
// iso を真ん中にした n 本の期間。右端を越えるなら最新に寄せる
function chartSpanAround(daily, bars, iso, n){
  const t = bars.t, last = t.length - 1;
  const z = Math.min(last, Math.max(0, lowerBound(t, iso)) + Math.floor(n / 2)), a = Math.max(0, z - n + 1);
  return [chartSpanStart(daily, bars, a), t[z]];
}
function chartSpanStart(daily, bars, i){
  return i > 0 ? daily.t[Math.min(daily.t.length - 1, lowerBound(daily.t, bars.t[i - 1] + "~"))] : daily.t[0];
}

// iso から years 年。2/29 を平年へ移すときはその月の最終日
function chartShiftYears(iso, years){
  const y = +iso.slice(0, 4) + years, m = +iso.slice(5, 7), day = +iso.slice(8, 10);
  const dim = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return y + "-" + String(m).padStart(2, "0") + "-" + String(Math.min(day, dim)).padStart(2, "0");
}

// 初期の期間。日足は直近 1 年（最後の足から 1 年前の同じ日付以降。足りなければ全期間）。ほかの足は最新 n 本
function chartInitialSpan(daily, bars, tf, n){
  if(tf !== "D") return chartLatestSpan(daily, bars, n);
  const t = bars.t, end = t[t.length - 1];
  const i = Math.min(lowerBound(t, chartShiftYears(end, -1)), t.length - 1);
  return [chartSpanStart(daily, bars, i), end];
}

// 初期の本数。日足は直近 1 年の本数（決算速報が発表日を真ん中に出すときもこれ）
function chartInitialCount(daily, bars, tf, n){
  if(tf !== "D") return n;
  const t = bars.t, from = chartInitialSpan(daily, bars, tf, n)[0];
  return t.length - lowerBound(t, from);
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

// 高安値。足 a..b（両端を含む）のうち、前後それぞれ k 本の中で一番高い山・一番安い谷の位置。
// 同じ値なら先の足。見えている最高値・最安値は必ず入れる
function chartSwings(h, l, a, b, k){
  const highs = [], lows = [];
  if(b < a) return {highs, lows};
  let top = a, bottom = a;
  for(let i = a; i <= b; i++){
    if(h[i] > h[top]) top = i;
    if(l[i] < l[bottom]) bottom = i;
    let isH = true, isL = true;
    for(let j = Math.max(a, i - k); j <= Math.min(b, i + k) && (isH || isL); j++){
      if(j === i) continue;
      if(h[j] > h[i] || (h[j] === h[i] && j < i)) isH = false;
      if(l[j] < l[i] || (l[j] === l[i] && j < i)) isL = false;
    }
    if(isH) highs.push(i);
    if(isL) lows.push(i);
  }
  if(!highs.includes(top)) highs.push(top);
  if(!lows.includes(bottom)) lows.push(bottom);
  return {highs: highs.sort((x, y) => x - y), lows: lows.sort((x, y) => x - y)};
}

// 高安値のラベル。期間が 1 年を超えると年も付ける
function chartHlText(iso, v, long){
  const d = `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;
  return `${long ? iso.slice(2, 4) + "/" : ""}${d} ${chartPrice(v)}`;
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

const CHART_AXIS_UNITS = {share: [1e4, "万株"], yen: [1e6, "百万円"]};
function chartAxisUnit(max, kind){
  const [div, label] = CHART_AXIS_UNITS[kind];
  return Math.abs(max || 0) >= div ? {div, label} : {div: 1, label: ""};
}

const chartFade = (hex, a) => {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || "");
  if(!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
};

// 日付・始高安終・前日比。プロットの外（.chart-ohlc）に出す。足種は出さない（KAB-314）
function chartOhlcHtml(bars, i){
  if(!bars || !bars.t.length) return "";
  if(i == null || i < 0 || i >= bars.t.length) i = bars.t.length - 1;
  const prev = i > 0 ? bars.c[i - 1] : null;
  let chg = "";
  if(prev){
    const d = bars.c[i] - prev, cls = d > 0 ? "up" : d < 0 ? "down" : "";
    chg = `<span class="${cls}">${d > 0 ? "+" : ""}${chartPrice(d)} (${d > 0 ? "+" : ""}${(d / prev * 100).toFixed(2)}%)</span>`;
  }
  return `<span class="sub">${bars.t[i].replaceAll("-", "/")}</span>`
    + `<span>始 ${chartPrice(bars.o[i])}</span><span>高 ${chartPrice(bars.h[i])}</span>`
    + `<span>安 ${chartPrice(bars.l[i])}</span><span>終 <b>${chartPrice(bars.c[i])}</b></span>${chg}`;
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
  return {text: v("--sub"), ink: v("--text"), grid: v("--grid"), border: v("--border"),
          up: v("--up"), down: v("--down"), accent: v("--accent"), warn: v("--warn")};
}
const CHART_VOL_COLOR = "#aab4bf";
const CHART_MA_COLORS = ["accent", "warn", "#8e4ec6", "down"];
const CHART_BAND_COLOR = "#7d8792";
const CHART_REL_FADE = 0.3;      // 対TOPIX を重ねている間のローソク足・移動平均の濃さ
const CHART_NAV_H = 44;          // 期間のバーの高さ (px)
const CHART_NAV_MIN_DAYS = 10;   // 期間のバーで縮められる最短（営業日）
const CHART_HL_PX = 80;          // 高安値のラベルをおよそこの幅に 1 つ
const CHART_EXTRA_PANE_PX = 120;  // 業績の段を出すと高くする
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
// el に作る。opt: {height（業績の段・対TOPIX を出すとそのぶん高くする）}
function createStockChart(el, opt = {}){
  const LW = LightweightCharts;
  const th = chartTheme();
  el.innerHTML = "";
  el.style.position = "relative";
  let ohlc = el.previousElementSibling;
  if(!ohlc || ohlc.className !== "chart-ohlc"){
    ohlc = document.createElement("div");
    ohlc.className = "chart-ohlc";
    el.insertAdjacentElement("beforebegin", ohlc);
  }
  const chart = LW.createChart(el, {
    height: opt.height || 420, autoSize: true,
    layout: {background: {color: "transparent"}, textColor: th.text, panes: {separatorColor: th.border, enableResize: false}},
    localization: {locale: "ja-JP", dateFormat: "yyyy/MM/dd"},
    grid: {vertLines: {color: th.grid}, horzLines: {color: th.grid}},
    crosshair: {mode: LW.CrosshairMode.Normal},
    // 動かすのは下の期間のバーだけ。チャートに触れても動かさない（スマホは画面のスクロールになる）
    handleScroll: false, handleScale: false,
    timeScale: {borderColor: th.border, minBarSpacing: 0.05, rightOffset: 0},
    rightPriceScale: {borderColor: th.border},
  });

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

  const st = {daily: null, byTf: {}, rel: null, tf: "D", mas: [50, 150, 200], sub: "v",
              log: false, relOn: false, topix: null, mark: null, bars: null, idx: new Map(),
              fund: "", band: false, hl: true, fd: null, dIdx: new Map(), win: null, units: {}, subDiv: 0};
  const barsOf = tf => st.byTf[tf] || (st.byTf[tf] = chartBars(st.daily, tf));
  const relFade = c => st.relOn && st.topix ? chartFade(c, CHART_REL_FADE) : c;
  const width = () => chart.timeScale().width() || el.clientWidth || 600;

  function layoutPanes(){
    const panes = chart.panes();
    if(panes[0]) panes[0].setStretchFactor(4);
    if(panes[1]) panes[1].setStretchFactor(1);
    for(let k = 2; k < panes.length; k++) panes[k].setStretchFactor(1.15);
  }

  const setRange = (from, to) => chart.timeScale().setVisibleRange({from: chartTs(from), to: chartTs(to)});

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
    const list = [...byTime.values(), ...hlMarkers()].sort((a, b) => a.time - b.time);
    if(markers) markers.setMarkers(list);
    else markers = LW.createSeriesMarkers(candle, list);
  }

  // 見えている期間の山と谷
  function hlMarkers(){
    if(!st.hl || !st.win) return [];
    const t = st.bars.t, a = lowerBound(t, st.win.from), z = lowerBound(t, st.win.to + "~") - 1;
    if(z < a) return [];
    const k = Math.max(2, Math.round((z - a + 1) * CHART_HL_PX / width() / 2));
    const long = chartTs(st.win.to) - chartTs(st.win.from) > 366 * 86400;
    const {highs, lows} = chartSwings(st.bars.h, st.bars.l, a, z, k);
    const W = width(), px = W / (z - a + 1);
    // ラベルは足の真上・真下の中央に出る。端ではみ出すなら日付を外し、それでもはみ出せば出さない
    const fits = (i, text) => { const x = (i - a + 0.5) * px, half = text.length * 3.5; return x >= half && x <= W - half; };
    const out = [];
    const mk = (i, high) => {
      const v = high ? st.bars.h[i] : st.bars.l[i];
      let text = chartHlText(t[i], v, long);
      if(!fits(i, text)) text = chartPrice(v);
      if(!fits(i, text)) return;
      out.push({time: chartTs(t[i]), position: high ? "aboveBar" : "belowBar", shape: "circle", size: 0, color: th.text, text});
    };
    highs.forEach(i => mk(i, true));
    lows.forEach(i => mk(i, false));
    return out;
  }

  function drawMas(){
    maSeries.forEach(s => chart.removeSeries(s));
    st.maVals = [];
    maSeries = st.mas.map((w, i) => {
      const c = CHART_MA_COLORS[i % CHART_MA_COLORS.length];
      const s = chart.addSeries(LW.LineSeries, {color: relFade(th[c] || c), lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null}, 0);
      const len = chartMaLen(w, st.tf), ma = len ? chartSma(st.bars.c, len) : [];
      if(len) st.maVals.push({w, color: th[c] || c, vals: ma});
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
    subUnit();
  }
  const axisFormat = u => ({type: "custom", formatter: v => chartPrice(v / u.div), minMove: 1});
  function subUnit(){
    const b = st.bars, vals = st.sub === "tv" ? b.tv : b.v;
    let a = 0, z = b.t.length - 1, max = 0;
    if(st.win){ a = lowerBound(b.t, st.win.from); z = lowerBound(b.t, st.win.to + "~") - 1; }
    for(let i = a; i <= z; i++) if(vals[i] > max) max = vals[i];
    const u = chartAxisUnit(max, st.sub === "tv" ? "yen" : "share");
    if(st.units.sub === u.label && st.subDiv === u.div) return;
    st.units.sub = u.label; st.subDiv = u.div;
    sub.applyOptions({priceFormat: axisFormat(u)});
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
    st.bandMs = [];
    if(!st.band || !st.fd) return;
    st.bandMs = chartBandMultiples(st.fd.per);
    bandSeries = st.bandMs.map(m => {
      const x = chart.addSeries(LW.LineSeries, {color: CHART_BAND_COLOR, lineWidth: 1, lineStyle: LW.LineStyle.Dashed,
        lineType: LW.LineType.WithSteps, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null,
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
    st.panes = ["price", "sub"];
    st.units.fund = "";
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
        let max = 0;
        for(const k of ["opq", "opttm"]) for(const v of st.fd[k]) if(v != null && Math.abs(v) > max) max = Math.abs(v);
        const u = chartAxisUnit(max, "yen"), yen = axisFormat(u);
        st.units.fund = u.label;
        line({color: th.accent, lineType: LW.LineType.WithSteps, priceFormat: yen},
             barPoints(t => fundAt("opq", t)));
        line({color: th.warn, lineType: LW.LineType.WithSteps, priceFormat: yen},
             barPoints(t => fundAt("opttm", t)));
      } else {
        const x = line({color: th.accent, priceFormat: times},
                       barPoints(t => fundAt(st.fund, t)));
        const v = st.fd[st.fund].filter(y => y != null && y > 0).sort((a, b) => a - b);
        if(st.fund === "per" && v.length)
          x.createPriceLine({price: Math.round(chartQuantile(v, 0.5) * 10) / 10, color: th.text, lineWidth: 1,
                             lineStyle: LW.LineStyle.Dotted, axisLabelVisible: true, title: "中央値"});
      }
      st.panes.push("fund");
      pane++;
    }
    const relShown = st.relOn && st.topix;
    if(relShown){
      if(!st.rel) st.rel = chartRelative(st.daily, st.topix);
      const x = chart.addSeries(LW.LineSeries, {color: th.ink, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        priceScaleId: "rel", priceFormat: {type: "price", precision: 1, minMove: 0.1}}, 0);
      x.priceScale().applyOptions({scaleMargins: {top: 0.1, bottom: 0.1}});
      x.setData(barPoints(t => st.rel.get(t)));
      extra.push(x);
    }
    const up = relShown ? chartFade(th.up, CHART_REL_FADE) : th.up, down = relShown ? chartFade(th.down, CHART_REL_FADE) : th.down;
    candle.applyOptions({upColor: up, downColor: down, wickUpColor: up, wickDownColor: down});
    el.style.height = baseHeight + (pane - 2) * CHART_EXTRA_PANE_PX + "px";
    layoutPanes();
  }

  // 日付・始高安終はプロットの上（.chart-ohlc）。MA・出来高などは段ごとに左上へ重ねる
  const legends = [], unitTags = [];
  function paneDiv(list, k, cls){
    if(!list[k]){
      const d = document.createElement("div");
      d.className = cls;
      el.appendChild(d);
      list[k] = d;
    }
    return list[k];
  }
  const paneLegend = k => paneDiv(legends, k, "chart-legend");
  const sw = (color, text) => `<span style="color:${color}">${text}</span>`;
  function drawLegend(i){
    const b = st.bars;
    if(!b || !b.t.length){
      ohlc.innerHTML = "";
      legends.forEach(d => { d.innerHTML = ""; });
      return;
    }
    if(i == null || i < 0 || i >= b.t.length) i = b.t.length - 1;
    const t = b.t[i];
    ohlc.innerHTML = chartOhlcHtml(b, i);
    let price = (st.maVals || []).map(m => sw(m.color, `MA${m.w} ${chartPrice(m.vals[i])}`)).join(" ");
    if(st.relOn && st.rel) price = sw(th.ink, `対TOPIX ${st.rel.get(t) ?? "–"}`) + (price ? " " + price : "");
    if(st.band && st.bandMs && st.bandMs.length){
      const e = fundAt("eps", t);
      const band = sw(CHART_BAND_COLOR, "PER " + st.bandMs.map(m => `${m}倍 ${e > 0 ? chartPrice(Math.round(e * m * 10) / 10) : "–"}`).join(" / "));
      price = price ? price + "<br>" + band : band;
    }
    const texts = {
      price,
      sub: st.sub === "tv" ? `売買代金 ${chartQty(b.tv[i], "円")}` : `出来高 ${chartQty(b.v[i], "株")}`,
      fund: legendFund(t),
    };
    const panes = chart.panes(), kinds = st.panes || ["price", "sub"];
    const axisW = chart.priceScale("right", 0).width();
    let top = 0;
    kinds.forEach((kind, k) => {
      const d = paneLegend(k);
      d.style.top = top + 4 + "px";
      d.innerHTML = texts[kind] || "";
      d.hidden = !d.innerHTML;
      const u = paneDiv(unitTags, k, "chart-unit");
      u.style.top = top + 2 + "px";
      u.style.right = axisW + 4 + "px";
      u.textContent = st.units[kind] ? `(${st.units[kind]})` : "";
      u.hidden = !u.textContent;
      top += (panes[k] ? panes[k].getHeight() : 0) + 1;
    });
    for(let k = kinds.length; k < legends.length; k++) legends[k].hidden = true;
    for(let k = kinds.length; k < unitTags.length; k++) unitTags[k].hidden = true;
  }
  function legendFund(t){
    if(!st.fund || !st.fd) return "";
    if(st.fund === "op")
      return `営業利益 ${sw(th.accent, "四半期 " + chartQty(fundAt("opq", t), "円"))} ${sw(th.warn, "12か月 " + chartQty(fundAt("opttm", t), "円"))}`;
    const v = fundAt(st.fund, t);
    return sw(th.accent, `${CHART_FUND_NAMES[st.fund]} ${v == null ? "–" : chartPrice(v) + "倍"}`);
  }

  // 足を tf にして全部描き直す。range があればその期間を出す
  function applyTf(tf){
    st.tf = tf;
    st.bars = barsOf(tf);
    st.idx = new Map(st.bars.t.map((t, i) => [chartTs(t), i]));
    const b = st.bars;
    candle.setData(b.t.map((t, i) => ({time: chartTs(t), open: b.o[i], high: b.h[i], low: b.l[i], close: b.c[i]})));
    drawMas(); drawBand(); drawSub(); drawExtra(); drawMarkers(); drawLegend();
    if(st.win) setRange(st.win.from, st.win.to);
  }

  chart.subscribeCrosshairMove(p => drawLegend(p && p.time != null ? st.idx.get(p.time) : null));

  // pan-y で横の指の動きをブラウザに取られないようにする。LWC は handleScroll: false だと横に滑らせても十字カーソルを動かさない
  el.style.touchAction = "pan-y";
  function touchCrosshair(e){
    if(e.pointerType !== "touch" || !st.bars) return;
    const x = e.clientX - el.getBoundingClientRect().left;
    if(x < 0 || x > chart.timeScale().width()) return;
    const time = chart.timeScale().coordinateToTime(x), i = time == null ? null : st.idx.get(time);
    if(i == null) return;
    chart.setCrosshairPosition(st.bars.c[i], time, candle);
    drawLegend(i);
  }
  el.addEventListener("pointerdown", touchCrosshair);
  el.addEventListener("pointermove", touchCrosshair);

  // 期間 [from, to] を幅いっぱいに出す
  function showSpan(from, to){
    st.win = {from, to};
    subUnit();
    setRange(from, to);
    drawMarkers();
    nav.draw();
  }
  const latestCount = () => Math.max(20, Math.floor(width() / CHART_BAR_PX));

  // ---- 期間のバー（チャートの下。全期間の終値と、いまの期間の窓） ----
  const nav = (() => {
    const box = document.createElement("div");
    box.className = "chart-nav";
    const cv = document.createElement("canvas");
    box.appendChild(cv);
    el.insertAdjacentElement("afterend", box);
    const ctx = cv.getContext("2d");
    let drag = null, pending = null;
    const n = () => st.daily ? st.daily.t.length : 0;
    const w = () => box.clientWidth || el.clientWidth || 600;
    const X = i => n() > 1 ? i / (n() - 1) * (w() - 1) : 0;
    const I = x => Math.round(x / Math.max(1, w() - 1) * (n() - 1));
    const winIdx = () => {
      const t = st.daily.t;
      return [lowerBound(t, st.win.from), Math.max(lowerBound(t, st.win.from), lowerBound(t, st.win.to + "~") - 1)];
    };
    function draw(){
      if(!st.daily || !n()) return;
      const dpr = window.devicePixelRatio || 1, W = w(), H = CHART_NAV_H;
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + "px"; cv.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const c = st.daily.c, t = st.daily.t;
      let lo = Infinity, hi = -Infinity;
      for(const v of c){ if(v < lo) lo = v; if(v > hi) hi = v; }
      const top = 4, bot = H - 14, Y = v => hi > lo ? bot - (v - lo) / (hi - lo) * (bot - top) : (top + bot) / 2;
      // 終値の線（1px に 1 点）
      ctx.beginPath();
      const step = Math.max(1, Math.floor(n() / W));
      for(let i = 0; i < n(); i += step) ctx[i ? "lineTo" : "moveTo"](X(i), Y(c[i]));
      ctx.lineTo(X(n() - 1), Y(c[n() - 1]));
      ctx.strokeStyle = th.accent; ctx.lineWidth = 1; ctx.stroke();
      ctx.lineTo(X(n() - 1), bot); ctx.lineTo(0, bot); ctx.closePath();
      ctx.fillStyle = "rgba(0,119,182,.12)"; ctx.fill();
      // 年の目盛り
      ctx.fillStyle = th.text; ctx.strokeStyle = th.border; ctx.font = "10px sans-serif"; ctx.textBaseline = "bottom";
      let lastX = -99;
      for(let i = 1; i < n(); i++){
        if(t[i].slice(0, 4) === t[i - 1].slice(0, 4)) continue;
        const x = X(i);
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bot); ctx.stroke();
        if(x - lastX > 24){ ctx.fillText(t[i].slice(2, 4), x + 2, H - 1); lastX = x; }
      }
      if(!st.win) return;
      const [a, z] = winIdx(), xa = X(a), xz = X(z);
      ctx.fillStyle = "rgba(0,0,0,.08)";
      ctx.fillRect(0, 0, xa, H); ctx.fillRect(xz, 0, W - xz, H);
      ctx.strokeStyle = th.accent; ctx.lineWidth = 1.5; ctx.strokeRect(xa, 1, Math.max(1, xz - xa), H - 2);
      for(const x of [xa, xz]){
        ctx.fillStyle = "#fff"; ctx.fillRect(x - 4, H / 2 - 10, 8, 20);
        ctx.strokeStyle = th.accent; ctx.lineWidth = 1; ctx.strokeRect(x - 4, H / 2 - 10, 8, 20);
      }
    }
    function apply(a, z){
      const t = st.daily.t;
      pending = [t[a], t[z]];
      requestAnimationFrame(() => { if(pending){ const [f, to] = pending; pending = null; showSpan(f, to); } });
    }
    cv.addEventListener("pointerdown", e => {
      if(!st.daily || !st.win || !n()) return;
      const x = e.offsetX, [a, z] = winIdx(), xa = X(a), xz = X(z);
      const tol = e.pointerType === "touch" ? 16 : 8;
      let mode = Math.abs(x - xa) <= tol ? "a" : Math.abs(x - xz) <= tol ? "z" : x > xa && x < xz ? "move" : "jump";
      let sa = a, sz = z;
      if(mode === "jump"){  // 押したところへ窓を動かす（幅はそのまま）
        const span = z - a, mid = I(x);
        sa = Math.min(Math.max(0, mid - Math.round(span / 2)), n() - 1 - span); sz = sa + span;
        apply(sa, sz);
        mode = "move";
      }
      drag = {mode, x, a: sa, z: sz};
      cv.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    cv.addEventListener("pointermove", e => {
      if(!drag){
        if(!st.win || !n()) return;
        const [a, z] = winIdx(), x = e.offsetX;
        cv.style.cursor = Math.abs(x - X(a)) <= 8 || Math.abs(x - X(z)) <= 8 ? "ew-resize" : x > X(a) && x < X(z) ? "grab" : "pointer";
        return;
      }
      const d = I(e.offsetX) - I(drag.x), last = n() - 1, min = Math.min(CHART_NAV_MIN_DAYS, last);
      let a = drag.a, z = drag.z;
      if(drag.mode === "move"){
        const span = z - a;
        a = Math.min(Math.max(0, a + d), last - span); z = a + span;
      } else if(drag.mode === "a") a = Math.min(Math.max(0, a + d), z - min);
      else z = Math.max(Math.min(last, z + d), a + min);
      apply(a, z);
    });
    const end = () => { drag = null; };
    cv.addEventListener("pointerup", end);
    cv.addEventListener("pointercancel", end);
    return {draw, remove: () => box.remove()};
  })();

  // 幅が変わっても期間は保つ（autoSize で幅が変わると 1 本の幅が保たれ、期間がずれるため）
  const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => requestAnimationFrame(() => {
    if(st.win && st.bars){ setRange(st.win.from, st.win.to); drawMarkers(); drawLegend(); }
    nav.draw();
  }));
  if(ro) ro.observe(el);

  const view = {
    chart,
    // 銘柄を差し替える。opt2: {mark}
    setData(daily, opt2 = {}){
      st.daily = daily; st.byTf = {}; st.rel = null; st.mark = opt2.mark || null;
      st.fd = chartFundDaily(daily);
      st.dIdx = new Map(daily.t.map((t, i) => [t, i]));
      applyTf(st.tf);
    },
    // 表示の設定を変える。足を変えたらその足の最新を出し、ほかは見えている期間を保つ
    set(o){
      const newTf = o.tf !== undefined && o.tf !== st.tf;
      if(o.tf !== undefined) st.tf = o.tf;
      if(o.mas !== undefined) st.mas = o.mas;
      if(o.sub !== undefined) st.sub = o.sub;
      if(o.log !== undefined) candle.priceScale().applyOptions({mode: o.log ? LW.PriceScaleMode.Logarithmic : LW.PriceScaleMode.Normal});
      if(o.rel !== undefined) st.relOn = o.rel;
      if(o.fund !== undefined) st.fund = o.fund;
      if(o.band !== undefined) st.band = o.band;
      if(o.hl !== undefined) st.hl = o.hl;
      if(o.topix !== undefined){ st.topix = o.topix; st.rel = null; }
      if(!st.daily) return;
      applyTf(st.tf);
      if(newTf || !st.win) view.showLatest();
      else showSpan(st.win.from, st.win.to);
    },
    // 日足は直近 1 年。ほかの足は 1 本が CHART_BAR_PX になるだけ
    showLatest(){
      if(!st.daily || !st.daily.t.length) return;
      const [f, to] = chartInitialSpan(st.daily, st.bars, st.tf, latestCount());
      showSpan(f, to);
    },
    // iso を真ん中に、同じ本数を出す（決算速報で古い発表日を選んだとき）
    showAround(iso){
      const n = chartInitialCount(st.daily, st.bars, st.tf, latestCount());
      const [f, to] = chartSpanAround(st.daily, st.bars, iso, n);
      showSpan(f, to);
    },
    // 最新の足を出したとき iso が入るか
    latestHas(iso){
      return chartInitialSpan(st.daily, st.bars, st.tf, latestCount())[0] <= iso;
    },
    refreshMarkers: drawMarkers,
    tf: () => st.tf,
    destroy(){
      chartViews.delete(view);
      if(ro) ro.disconnect();
      ohlc.remove();
      nav.remove();
      try { chart.remove(); } catch(e) {}
      el.innerHTML = "";
    },
  };
  layoutPanes();
  chartViews.add(view);
  return view;
}

if(typeof module !== "undefined" && module.exports){
  module.exports = {chartSwings, chartHlText, chartFundDaily, chartQuantile, chartBandMultiples, chartBars, chartWeekKey, chartSma, chartMaLen, chartParseMas, chartSpikes, chartRelative,
                    chartLatestSpan, chartSpanAround, chartInitialSpan, chartInitialCount, chartPrice, chartQty, chartAxisUnit, chartFade, chartOhlcHtml};
}

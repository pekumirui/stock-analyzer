const FSC_PT = {Q1: "1Q", Q2: "2Q", Q3: "3Q", FY: "通期"};
const FSC_STEPS = [1, 2, 2.5, 5, 10];
const FSC_TICKS = [4, 5, 6];
const FSC_SERIES = {
  net_sales: {name: "売上高", color: "#5b7fc4"},
  gross_profit: {name: "売上総利益", color: "#e0a23b"},
  operating_income: {name: "営業利益", color: "#c0504d"},
  ordinary_income: {name: "経常利益", color: "#8db34a"},
  net_income: {name: "純利益", color: "#7b5ea7"},
  current_assets: {name: "流動資産", color: "#a9c4e8"},
  noncurrent_assets: {name: "固定資産", color: "#9fbf8a"},
  total_assets: {name: "資産合計", color: "#a9c4e8"},
  current_liabilities: {name: "流動負債", color: "#e3a3a8"},
  noncurrent_liabilities: {name: "固定負債", color: "#b9aedb"},
  total_liabilities: {name: "負債合計", color: "#e3a3a8"},
  net_assets: {name: "純資産", color: "#f2e17a"},
  operating_cf: {name: "営業CF", color: "#b85450"},
  investing_cf: {name: "投資CF", color: "#9bbb59"},
  financing_cf: {name: "財務CF", color: "#6f58a6"},
  cash_end: {name: "現金・現金同等物", color: "#5b7fc4"},
  free_cf: {name: "フリーCF", color: "#e53935"},
};
const FSC_PL_LINES = ["gross_profit", "operating_income", "ordinary_income", "net_income"];
const fscEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

const FSC_OPEN_KEY = "fs.chart.open";

function fscLoadOpen(){
  try {
    const v = localStorage.getItem(FSC_OPEN_KEY);
    if(v === "1" || v === "0") return v === "1";
  } catch(e) {}
  return !(typeof matchMedia === "function" && matchMedia("(max-width:640px)").matches);
}

let fscOpen = fscLoadOpen();
let fscPlQuarter = true;
let fscPlRate = false;
let fscBsRate = false;
let fscOff = new Set();
let fscLast = null;

const fscHas = vs => (vs || []).some(v => v != null);
const fscSum = (...vs) => vs.some(v => v == null) ? null : vs.reduce((a, b) => a + b, 0);
const fscRatio = (v, base) => v == null || !base ? null : v / base * 100;

function fscSeries(key, values, extra = {}){
  return {key, ...FSC_SERIES[key], values, unit: "money", axis: "left", ...extra};
}

function fscPeriods(d, keep){
  return (d.periods || []).map((p, i) => ({label: p.label, sub: FSC_PT[p.period_type] || p.period_type || "", i}))
    .filter(p => keep(p.i));
}

function fscPick(values, idx){
  return idx.map(i => (values || [])[i] ?? null);
}

function fscPlModel(d, quarter, rate){
  const src = (quarter ? d.chart.quarter : d.chart.series) || {};
  const periods = fscPeriods(d, () => true);
  const idx = periods.map(p => p.i);
  const sales = fscPick(src.net_sales, idx);
  const series = [fscSeries("net_sales", sales, {kind: "bar"})];
  for(const k of FSC_PL_LINES){
    const vs = fscPick(src[k], idx);
    if(!fscHas(vs)) continue;
    const shown = rate ? vs.map((v, i) => fscRatio(v, sales[i])) : vs;
    series.push(fscSeries(k, shown, {kind: "line", axis: "right", unit: rate ? "pct" : "money"}));
  }
  return {periods, series, stacks: [["net_sales"]]};
}

function fscBsModel(d, rate){
  const s = d.chart.series || {};
  const periods = fscPeriods(d, i => ["current_assets", "noncurrent_assets", "total_assets", "net_assets"].some(k => (s[k] || [])[i] != null));
  const idx = periods.map(p => p.i);
  const get = k => fscPick(s[k], idx);
  const assets = fscHas(get("current_assets")) || fscHas(get("noncurrent_assets"))
    ? ["current_assets", "noncurrent_assets"] : ["total_assets"];
  const debts = fscHas(get("current_liabilities")) || fscHas(get("noncurrent_liabilities"))
    ? ["current_liabilities", "noncurrent_liabilities"] : ["total_liabilities"];
  const total = get("total_assets").map((v, i) => v ?? fscSum(...assets.map(k => get(k)[i] ?? 0)));
  const series = [...assets, ...debts, "net_assets"].map(k => {
    const vs = get(k);
    return fscSeries(k, rate ? vs.map((v, i) => fscRatio(v, total[i])) : vs, {kind: "bar", unit: rate ? "pct" : "money"});
  });
  return {periods, series, stacks: [[...assets].reverse(), ["net_assets", ...[...debts].reverse()]]};
}

function fscCfModel(d){
  const s = d.chart.series || {};
  const flows = ["operating_cf", "investing_cf", "financing_cf"];
  const periods = fscPeriods(d, i => flows.some(k => (s[k] || [])[i] != null));
  const idx = periods.map(p => p.i);
  const get = k => fscPick(s[k], idx);
  const op = get("operating_cf"), inv = get("investing_cf");
  const series = [...flows, "cash_end"].map(k => fscSeries(k, get(k), {kind: "bar"}));
  series.push(fscSeries("free_cf", op.map((v, i) => fscSum(v, inv[i])), {kind: "line"}));
  return {periods, series, stacks: [...flows, "cash_end"].map(k => [k])};
}

function fsChartModel(d, opt = {}){
  if(!d || !d.chart || !(d.periods || []).length) return null;
  const st = d.statement;
  const m = st === "PL" ? fscPlModel(d, opt.quarter ?? true, !!opt.rate)
    : st === "BS" ? fscBsModel(d, !!opt.rate)
    : st === "CF" ? fscCfModel(d) : null;
  if(!m || !m.periods.length || !m.series.some(s => fscHas(s.values))) return null;
  return {statement: st, ...m};
}

function fscNiceCeil(x){
  if(!(x > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(x)));
  return FSC_STEPS.find(s => s * e >= x * (1 - 1e-9)) * e;
}

function fscExtent(values){
  let lo = 0, hi = 0;
  for(const v of values) if(v != null){ lo = Math.min(lo, v); hi = Math.max(hi, v); }
  return [lo, hi];
}

function fsChartAxes(extents, counts = FSC_TICKS){
  let best = null;
  for(const n of counts) for(let k = 0; k < n; k++){
    const steps = extents.map(([lo, hi]) => {
      if(lo < 0 && k === 0) return null;
      return fscNiceCeil(Math.max(hi / (n - k), k ? -lo / k : 0));
    });
    if(steps.includes(null)) continue;
    const waste = extents.reduce((a, [lo, hi], j) => a + n * steps[j] / Math.max(hi - lo, 1e-9), 0);
    if(!best || waste < best.waste - 1e-9) best = {waste, k, n, steps};
  }
  return best.steps.map(step => ({min: -best.k * step, max: (best.n - best.k) * step, step, n: best.n}));
}

function fscStackRange(m, on){
  const out = [];
  m.periods.forEach((_, i) => {
    for(const keys of m.stacks){
      let pos = 0, neg = 0;
      for(const k of keys){
        const s = m.series.find(x => x.key === k);
        const v = s && on(s) ? s.values[i] : null;
        if(v == null) continue;
        if(v >= 0) pos += v; else neg += v;
      }
      out.push(pos, neg);
    }
  });
  return out;
}

function fsChartFmt(v, unit){
  if(v == null) return "–";
  if(unit === "pct") return `${v.toFixed(1)}%`;
  return `${Math.round(v / 1e6).toLocaleString("ja-JP")} 百万円`;
}

const fscNum = (x, dec) => Number(x.toFixed(dec)).toLocaleString("ja-JP", {maximumFractionDigits: dec});

function fscTick(v, unit, step){
  if(unit === "pct") return `${fscNum(v, step < 1 ? 1 : 0)}%`;
  const m = v / 1e6, a = Math.abs(m), sa = step / 1e6;
  if(a >= 1e6 || sa >= 1e6) return `${fscNum(m / 1e6, sa >= 1e6 ? 0 : 1)}兆`;
  if(a >= 100 || sa >= 100) return `${fscNum(m / 100, sa >= 100 ? 0 : 1)}億`;
  return `${fscNum(m, 0)}百万`;
}

function fsChartSvg(m, width, off = new Set()){
  const on = s => !off.has(s.key);
  const W = Math.max(320, Math.round(width)), H = W < 560 ? 260 : 300;
  const hasRight = m.series.some(s => s.axis === "right");
  const L = 52, R = hasRight ? 52 : 12, T = 12, B = 34;
  const plotW = W - L - R, plotH = H - T - B, n = m.periods.length, slot = plotW / n;
  const leftUnit = (m.series.find(s => s.axis === "left") || {}).unit || "money";
  const rightUnit = (m.series.find(s => s.axis === "right") || {}).unit || "money";
  const left = [...fscStackRange(m, on), ...m.series.filter(s => s.axis === "left" && s.kind === "line" && on(s)).flatMap(s => s.values)];
  const right = m.series.filter(s => s.axis === "right" && on(s)).flatMap(s => s.values);
  const [la, ra] = fsChartAxes(hasRight ? [fscExtent(left), fscExtent(right)] : [fscExtent(left)]);
  const yOf = (ax, v) => T + plotH * (ax.max - v) / (ax.max - ax.min);
  const xOf = i => L + slot * (i + 0.5);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">`;
  for(let t = 0; t <= la.n; t++){
    const v = la.min + la.step * t, y = yOf(la, v).toFixed(1);
    svg += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="${v === 0 ? "#9aa4ae" : "#e8ecf0"}"/>`;
    svg += `<text x="${L - 4}" y="${y}" font-size="10" text-anchor="end" dominant-baseline="middle" fill="#66707a">${fscEsc(fscTick(v, leftUnit, la.step))}</text>`;
    if(ra){
      const rv = ra.min + ra.step * t;
      svg += `<text x="${W - R + 4}" y="${y}" font-size="10" dominant-baseline="middle" fill="#66707a">${fscEsc(fscTick(rv, rightUnit, ra.step))}</text>`;
    }
  }
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 40))));
  m.periods.forEach((p, i) => {
    if((n - 1 - i) % every) return;
    svg += `<text x="${xOf(i).toFixed(1)}" y="${H - 20}" font-size="10" text-anchor="middle" fill="#66707a">${fscEsc(p.label)}</text>`;
    svg += `<text x="${xOf(i).toFixed(1)}" y="${H - 7}" font-size="9" text-anchor="middle" fill="#8a949e">${fscEsc(p.sub)}</text>`;
  });
  const groups = m.stacks.length, gap = Math.min(10, slot * 0.2), bw = Math.max(2, (slot - gap) / groups);
  m.periods.forEach((_, i) => {
    m.stacks.forEach((keys, g) => {
      let pos = 0, neg = 0;
      const x = L + slot * i + gap / 2 + bw * g;
      for(const k of keys){
        const s = m.series.find(z => z.key === k);
        const v = s && on(s) ? s.values[i] : null;
        if(v == null || v === 0) continue;
        const from = v > 0 ? pos : neg, to = from + v;
        if(v > 0) pos = to; else neg = to;
        const y1 = yOf(la, Math.max(from, to)), y2 = yOf(la, Math.min(from, to));
        svg += `<rect x="${(x + 0.5).toFixed(1)}" y="${y1.toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${Math.max(0.5, y2 - y1).toFixed(1)}" fill="${s.color}"/>`;
      }
    });
  });
  for(const s of m.series.filter(z => z.kind === "line" && on(z))){
    const ax = s.axis === "right" ? ra : la;
    let d = "", pen = false;
    s.values.forEach((v, i) => {
      if(v == null){ pen = false; return; }
      d += `${pen ? "L" : "M"}${xOf(i).toFixed(1)},${yOf(ax, v).toFixed(1)}`;
      pen = true;
    });
    svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round"/>`;
    s.values.forEach((v, i) => {
      if(v != null) svg += `<circle cx="${xOf(i).toFixed(1)}" cy="${yOf(ax, v).toFixed(1)}" r="2.6" fill="${s.color}"/>`;
    });
  }
  svg += `<rect class="fsc-band" x="0" y="${T}" width="${slot.toFixed(1)}" height="${plotH}" fill="#5b7fc4" fill-opacity=".08" visibility="hidden"/>`;
  m.periods.forEach((_, i) => {
    svg += `<rect class="fsc-hit" data-i="${i}" x="${(L + slot * i).toFixed(1)}" y="${T}" width="${slot.toFixed(1)}" height="${plotH}" fill="transparent"/>`;
  });
  return svg + `</svg>`;
}

function fsChartTip(m, i, off = new Set()){
  const p = m.periods[i];
  const rows = m.series.filter(s => !off.has(s.key) && fscHas(s.values)).map(s =>
    `<span><i style="background:${s.color}"></i>${fscEsc(s.name)}</span><span class="num">${fscEsc(fsChartFmt(s.values[i], s.unit))}</span>`).join("");
  return `<b>${fscEsc(p.label)} ${fscEsc(p.sub)}</b><div class="fsc-tip-rows">${rows}</div>`;
}

function fscCtl(groups){
  return groups.map(g => `<div class="fin-tabs" role="group" aria-label="${fscEsc(g.label)}">`
    + g.items.map(([v, txt]) => `<button type="button" class="ftab${g.on === v ? " on" : ""}" data-fsc="${g.key}" data-v="${v}">${fscEsc(txt)}</button>`).join("")
    + `</div>`).join("");
}

function fscControls(st){
  if(st === "PL") return fscCtl([
    {key: "pq", label: "期間", on: fscPlQuarter ? "q" : "c", items: [["q", "4半期"], ["c", "累計"]]},
    {key: "pr", label: "表し方", on: fscPlRate ? "r" : "a", items: [["a", "金額"], ["r", "利益率"]]},
  ]);
  if(st === "BS") return fscCtl([{key: "br", label: "表し方", on: fscBsRate ? "r" : "a", items: [["a", "金額"], ["r", "構成比"]]}]);
  return "";
}

function fscNote(m){
  if(m.statement === "PL"){
    const span = fscPlQuarter ? "3か月ごと（累計の差）" : "期首からの累計";
    return fscPlRate ? `${span}。棒は売上高（左軸）、線は売上高に対する利益率（右軸）` : `${span}。棒は売上高（左軸）、線は利益（右軸）`;
  }
  if(m.statement === "BS") return fscBsRate ? "資産合計を 100% とした構成比。左が資産、右が負債・純資産" : "左が資産、右が負債・純資産";
  return "期首からの累計。フリーCF = 営業CF + 投資CF";
}

function drawFsChart(){
  const host = $("#st-fs-chart");
  if(!host || !fscLast) return;
  const d = fscLast;
  const m = fsChartModel(d, {quarter: fscPlQuarter, rate: d.statement === "PL" ? fscPlRate : fscBsRate});
  if(!m){ host.hidden = true; host.innerHTML = ""; return; }
  host.hidden = false;
  host.classList.toggle("closed", !fscOpen);
  const fold = `<button type="button" class="fsc-fold" data-fsc="open" aria-expanded="${fscOpen}">${fscOpen ? "推移図を隠す" : "推移図を出す"}</button>`;
  if(!fscOpen){ host.innerHTML = `<div class="row fsc-ctl">${fold}</div>`; return; }
  const legend = m.series.filter(s => fscHas(s.values)).map(s =>
    `<label><input type="checkbox" data-k="${s.key}"${fscOff.has(s.key) ? "" : " checked"}><i style="background:${s.color}"></i>${fscEsc(s.name)}</label>`).join("");
  host.innerHTML = `<div class="row fsc-ctl">${fold}${fscControls(m.statement)}<span class="sub">${fscEsc(fscNote(m))}</span></div>`
    + `<div class="fsc-plot"></div><div class="fsc-legend">${legend}</div>`;
  const plot = host.querySelector(".fsc-plot");
  plot.innerHTML = fsChartSvg(m, plot.clientWidth || 720, fscOff) + `<div class="fsc-tip" hidden></div>`;
  const tip = plot.querySelector(".fsc-tip"), band = plot.querySelector(".fsc-band");
  const show = el => {
    const i = Number(el.dataset.i);
    tip.innerHTML = fsChartTip(m, i, fscOff);
    tip.hidden = false;
    band.setAttribute("x", el.getAttribute("x"));
    band.setAttribute("visibility", "visible");
    const cx = Number(el.getAttribute("x")) + Number(el.getAttribute("width")) / 2;
    const scale = plot.clientWidth / (Number(plot.querySelector("svg").getAttribute("width")) || plot.clientWidth);
    const left = cx * scale < plot.clientWidth / 2 ? cx * scale + 12 : cx * scale - tip.offsetWidth - 12;
    tip.style.left = `${Math.max(0, left)}px`;
  };
  const ctl = chartTip(host, () => { tip.hidden = true; band.setAttribute("visibility", "hidden"); });
  plot.querySelectorAll(".fsc-hit").forEach(el => {
    el.addEventListener("pointerenter", e => { if(e.pointerType === "mouse" && ctl.hover(el.dataset.i)) show(el); });
    el.addEventListener("click", e => { if(ctl.press(el.dataset.i, e)) show(el); });
  });
  plot.addEventListener("pointerleave", e => { if(e.pointerType === "mouse") ctl.close(); });
}

function renderFsChart(d){
  fscLast = d;
  drawFsChart();
}

function clearFsChart(){
  fscLast = null;
  const host = $("#st-fs-chart");
  if(host){ host.hidden = true; host.innerHTML = ""; }
}

if(typeof document !== "undefined" && document.addEventListener){
  document.addEventListener("click", e => {
    const host = $("#st-fs-chart");
    if(!host || !host.contains(e.target)) return;
    const b = e.target.closest("[data-fsc]");
    if(!b) return;
    const v = b.dataset.v;
    if(b.dataset.fsc === "open"){
      fscOpen = !fscOpen;
      try { localStorage.setItem(FSC_OPEN_KEY, fscOpen ? "1" : "0"); } catch(err) {}
    }
    if(b.dataset.fsc === "pq") fscPlQuarter = v === "q";
    if(b.dataset.fsc === "pr") fscPlRate = v === "r";
    if(b.dataset.fsc === "br") fscBsRate = v === "r";
    drawFsChart();
  });
  document.addEventListener("change", e => {
    const k = e.target && e.target.dataset && e.target.dataset.k;
    const host = $("#st-fs-chart");
    if(!k || !host || !host.contains(e.target)) return;
    if(e.target.checked) fscOff.delete(k); else fscOff.add(k);
    drawFsChart();
  });
  let fscWidth = 0;
  window.addEventListener("resize", () => {
    const host = $("#st-fs-chart");
    if(!host || host.hidden || !fscOpen || !fscLast || host.clientWidth === fscWidth) return;
    fscWidth = host.clientWidth;
    requestAnimationFrame(drawFsChart);
  });
}

if(typeof module !== "undefined" && module.exports){
  module.exports = {fsChartModel, fsChartAxes, fsChartSvg, fsChartTip, fsChartFmt, fscLoadOpen};
}

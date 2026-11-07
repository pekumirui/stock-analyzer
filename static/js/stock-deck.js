// 決算説明資料の中身（KAB-350。docs/DESIGN_stock.md）。ニュースの行から開く。読み取った表・要約の復元ビュー。
// 原本の PDF は出さない。表（列 × 行、行の of）からグラフと表を組み直す。規則は analyzer/deck_reader/view.py と同じ
let stockDecks = null;      // null = まだ読んでいない
let stockDeckKey = "";

const DECK_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const DECK_OTHER = "#a3a29c";
const DECK_RATE_UNITS = ["%", "pts", "pt", "ポイント"];
// 為替の行。単位「円」だけでは見ない（一株当たり利益なども円）
const deckFxRow = r => String(r.metric || "").includes("為替");
const deckFcst = c => ["forecast", "plan"].includes(c.kind);
const DECK_SERIES_KINDS = ["actual", "forecast", "plan"];
const DECK_KIND = {actual: "実績", forecast: "予想", previous_forecast: "前回予想", plan: "計画", change: "増減"};
// 幅は描くときの画面の幅（320〜760px）。SVG を縮めずに描くので、スマホでも値の文字が小さくならない
let DECK_W = 720;
const DECK_H = 250, DECK_ML = 52, DECK_MR = 12, DECK_MT = 16, DECK_MB = 46;
const DECK_DIGITS = /(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/;
const deckEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function resetStockDecks(){
  stockDecks = null;
  stockDeckKey = "";
  $("#st-deck-body").innerHTML = "";
}

function deckNum(v){
  if(v == null || v === "" || v === "null") return null;
  const s = String(v).normalize("NFKC").trim().replace(/\s/g, "");
  const m = s.match(DECK_DIGITS);
  if(!m) return null;
  const n = Number(m[0].replace(/,/g, ""));
  return /^[△▲▼\-−]/.test(s) ? -n : n;
}

function deckDec(rows){
  let d = 0;
  for(const r of rows) for(const v of (r.values || [])){
    const m = String(v ?? "").normalize("NFKC").match(/\.(\d+)/);
    if(m) d = Math.max(d, m[1].length);
  }
  return d;
}

function deckFmt(v, dec){
  if(v == null) return "";
  return v.toLocaleString("ja-JP", {minimumFractionDigits: dec, maximumFractionDigits: dec}).replace("-", "−");
}

const deckKey = r => `${r.metric}|${r.segment == null || r.segment === "null" ? "null" : r.segment}`;
const deckOf = r => !r.of ? "" : (r.of.includes("|") ? r.of : `${r.of}|null`);
const deckLabel = r => r.segment != null && r.segment !== "null" && r.segment !== "" ? `${r.metric}（${r.segment}）` : String(r.metric || "");
function deckKidLabel(c, parent){
  if(c.segment != null && c.segment !== "null" && c.segment !== "" && c.segment !== parent.segment) return String(c.segment);
  return String(c.metric || "").split("/").pop();
}
const deckTextW = (s, size) => [...s].reduce((w, ch) => w + (",.:".includes(ch) ? 0.3 : 0.58), 0) * size;
function deckInk(color){
  const [r, g, b] = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? "#0b0b0b" : "#ffffff";
}

function deckTicks(lo, hi, n = 4){
  if(hi === lo) hi = lo + 1;
  let step = Math.pow(10, Math.floor(Math.log10((hi - lo) / n)));
  for(const m of [1, 2, 2.5, 5, 10]){ if((hi - lo) / (step * m) <= n){ step *= m; break; } }
  const ticks = [];
  let t = Math.floor(lo / step) * step;
  while(!ticks.length || ticks[ticks.length - 1] < hi){ ticks.push(Math.round(t * 1e10) / 1e10); t += step; }
  return ticks;
}

function deckScale(lo, hi, head){
  const span = (hi - lo) || Math.abs(hi) || 1;
  const ticks = deckTicks(lo - (lo < 0 ? span * head : 0), hi + (hi > 0 ? span * head : 0));
  const a = Math.min(...ticks), b = Math.max(...ticks);
  return {ticks, y: v => DECK_MT + (b - v) / (b - a) * (DECK_H - DECK_MT - DECK_MB)};
}

function deckFrame(ticks, y, dec){
  return ticks.map(t => `<line x1="${DECK_ML}" x2="${DECK_W - DECK_MR}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" class="grid${t === 0 ? " zero" : ""}"/>`
    + `<text x="${DECK_ML - 6}" y="${(y(t) + 4).toFixed(1)}" class="ax" text-anchor="end">${deckEsc(deckFmt(t, Number.isInteger(t) ? 0 : Math.min(dec, 2)))}</text>`).join("");
}

function deckXLabels(cols, band){
  let h = "";
  cols.forEach((c, i) => {
    const x = DECK_ML + band * (i + 0.5);
    const p = String(c.period || "");
    // 列の幅に入らなければ空白で 2 行に分け、それでも入らなければ詰める
    let lines = [p];
    if(deckTextW(p, 11) > band - 2 && p.includes(" ")){
      const k = p.indexOf(" ");
      lines = [p.slice(0, k), p.slice(k + 1)];
    }
    const room = Math.max(3, Math.floor((band - 2) / 6.5));
    lines = lines.map(t => t.length <= room ? t : t.slice(0, room - 1) + "…");
    if(c.kind !== "actual") lines.push(DECK_KIND[c.kind] || "");
    lines.forEach((t, j) => { h += `<text x="${x.toFixed(1)}" y="${DECK_H - DECK_MB + 16 + j * 13}" class="ax" text-anchor="middle">${deckEsc(t)}</text>`; });
  });
  return h;
}

function deckBar(x, w, y0, y1, r = 4){
  const h = Math.abs(y1 - y0);
  r = Math.min(r, h / 2, w / 2);
  const f = n => n.toFixed(1);
  if(y1 <= y0) return `M${f(x)},${f(y0)}V${f(y1 + r)}Q${f(x)},${f(y1)} ${f(x + r)},${f(y1)}H${f(x + w - r)}Q${f(x + w)},${f(y1)} ${f(x + w)},${f(y1 + r)}V${f(y0)}Z`;
  return `M${f(x)},${f(y0)}V${f(y1 - r)}Q${f(x)},${f(y1)} ${f(x + r)},${f(y1)}H${f(x + w - r)}Q${f(x + w)},${f(y1)} ${f(x + w)},${f(y1 - r)}V${f(y0)}Z`;
}

function deckPatterns(colors, id){
  return colors.map((c, i) => `<pattern id="${id}h${i}" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">`
    + `<rect width="6" height="6" fill="${c}" opacity="0.18"/><rect width="2.5" height="6" fill="${c}"/></pattern>`).join("");
}

function deckLegend(names, colors, shape){
  if(names.length < 2) return "";
  return `<div class="dk-legend">${names.map((n, i) => `<span><i class="dk-sw ${shape}" style="background:${colors[i]}"></i>${deckEsc(n)}</span>`).join("")}</div>`;
}

function deckBarLabel(cx, slot, yTip, s, up){
  if(deckTextW(s, 10) <= slot) return `<text x="${cx.toFixed(1)}" y="${(up ? yTip - 5 : yTip + 13).toFixed(1)}" class="val" text-anchor="middle">${deckEsc(s)}</text>`;
  const ty = up ? yTip - 4 : yTip + 4, tx = cx + 3.5;
  return `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" class="val" text-anchor="${up ? "start" : "end"}" transform="rotate(-90 ${tx.toFixed(1)} ${ty.toFixed(1)})">${deckEsc(s)}</text>`;
}

let deckSvgSeq = 0;
function deckFigure(title, unit, note, legend, svg){
  return `<figure class="dk-chart"><figcaption>${deckEsc(title)}<span class="dk-unit">（${deckEsc(unit)}）</span>`
    + `${note ? `<span class="dk-note">${note}</span>` : ""}</figcaption>${legend}${svg}</figure>`;
}

// series: [[名前, [値…]]]。total（合計の行）があり内訳がすべてプラスなら積み上げ。マイナスが混ざれば内訳と合計を並べた棒
function deckBarChart(cols, series, unit, title, dec, total, totalName){
  if(total && series.some(([, vs]) => vs.some(v => v != null && v < 0))){ series = series.concat([[totalName, total]]); total = null; }
  const id = `dk${++deckSvgSeq}`;
  const colors = series.map(([n], i) => n === "その他" ? DECK_OTHER : DECK_SERIES[i % DECK_SERIES.length]);
  const n = cols.length, band = (DECK_W - DECK_ML - DECK_MR) / n;
  const fill = (k, c) => ["forecast", "plan"].includes(c.kind) ? `url(#${id}h${k})` : colors[k];
  const fmt = v => deckFmt(v, dec);
  let marks = "", sc;
  if(total){
    const pos = cols.map((_, i) => series.reduce((s, [, vs]) => s + Math.max(vs[i] || 0, 0), 0));
    sc = deckScale(0, Math.max(0, ...pos, ...total.filter(v => v != null)), 0.1);
    const {y} = sc, bw = Math.min(n <= 5 ? 48 : 28, band * 0.5);
    cols.forEach((c, i) => {
      const x = DECK_ML + band * (i + 0.5) - bw / 2;
      let up = 0;
      const segs = [];
      series.forEach(([name, vs], k) => { const v = vs[i]; if(v){ segs.push([k, name, v, up, up + v]); up += v; } });
      const side = [];
      for(const [k, name, v, base, top] of segs){
        const y0 = y(base), y1 = y(top), gap = y0 - y1 > 3 ? 1 : 0;
        marks += `<path d="${deckBar(x, bw, y0 - gap, y1 + gap, top === up ? 4 : 0)}" fill="${fill(k, c)}"><title>${deckEsc(`${c.period} ${name} ${fmt(v)}`)}</title></path>`;
        const s = fmt(v), ym = (y0 + y1) / 2 + 3.2;
        if(y0 - y1 >= 13 && deckTextW(s, 9) <= bw - 4 && c.kind === "actual"){
          marks += `<text x="${(x + bw / 2).toFixed(1)}" y="${ym.toFixed(1)}" class="seg" fill="${deckInk(colors[k])}" text-anchor="middle">${deckEsc(s)}</text>`;
        } else if(bw >= 11 && y0 - y1 >= deckTextW(s, 9) + 4 && c.kind === "actual"){  // 棒が細ければ縦書きで中に
          const tx = x + bw / 2 + 3.2, ty = (y0 + y1) / 2;
          marks += `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" class="seg" fill="${deckInk(colors[k])}" text-anchor="middle" transform="rotate(-90 ${tx.toFixed(1)} ${ty.toFixed(1)})">${deckEsc(s)}</text>`;
        } else side.push([ym, s, x + bw + 2, (band - bw) / 2 - 2]);
      }
      const t = total[i] != null ? total[i] : (segs.length ? up : null);
      const topLabel = y(Math.max(t ?? up, up)) - 5;
      let last = null;
      for(const [ym, s, sx, room] of side){
        const ty = last == null ? ym : Math.max(Math.min(ym, last - 10), DECK_MT + 8);
        if(deckTextW(s, 9) <= room && (last == null || last - ty >= 10) && ty - topLabel >= 10){
          marks += `<text x="${sx.toFixed(1)}" y="${ty.toFixed(1)}" class="seg side" text-anchor="start">${deckEsc(s)}</text>`;
          last = ty;
        }
      }
      if(t != null) marks += deckBarLabel(x + bw / 2, band - 4, y(Math.max(t, up)), fmt(t), true);
    });
  } else {
    const vals = series.flatMap(([, vs]) => vs.filter(v => v != null));
    sc = deckScale(Math.min(0, ...vals), Math.max(0, ...vals), 0.14);
    const {y} = sc, kn = series.length, bw = Math.min(24, band * 0.7 / kn);
    cols.forEach((c, i) => {
      const x0 = DECK_ML + band * (i + 0.5) - (bw * kn + 2 * (kn - 1)) / 2;
      series.forEach(([name, vs], k) => {
        const v = vs[i];
        if(v == null) return;
        const x = x0 + k * (bw + 2);
        marks += `<path d="${deckBar(x, bw, y(0), y(v))}" fill="${fill(k, c)}"><title>${deckEsc(`${c.period} ${name} ${fmt(v)}`)}</title></path>`;
        marks += deckBarLabel(x + bw / 2, kn > 1 ? bw + 2 : band - 4, y(v), fmt(v), v >= 0);
      });
    });
  }
  const svg = `<svg viewBox="0 0 ${DECK_W} ${DECK_H}" role="img" aria-label="${deckEsc(title)}"><defs>${deckPatterns(colors, id)}</defs>`
    + `${deckFrame(sc.ticks, sc.y, dec)}${marks}${deckXLabels(cols, band)}</svg>`;
  const note = cols.some(c => ["forecast", "plan"].includes(c.kind)) ? "斜線は予想・計画" : "";
  return deckFigure(title, unit, note, deckLegend(series.map(([n]) => n), colors, "box"), svg);
}

function deckLinePaths(pts, color, fx){
  if(pts.length < 2) return "";
  if(!fx) return `<path d="M${pts.map(([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`).join("L")}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  // 右の列が予想・計画のときだけ破線（その列へ入る区間）。実績どうしは実線
  let out = "", d = "", dash = false, on = false;
  const flush = () => {
    if(!on) return;
    out += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="${dash ? "butt" : "round"}"${dash ? ' stroke-dasharray="6 4"' : ""}/>`;
    on = false;
  };
  for(let i = 1; i < pts.length; i++){
    const segDash = deckFcst(pts[i][3]);
    const bit = `${pts[i][0].toFixed(1)},${pts[i][1].toFixed(1)}`;
    if(on && dash === segDash) d += `L${bit}`;
    else {
      flush();
      dash = segDash;
      on = true;
      d = `M${pts[i - 1][0].toFixed(1)},${pts[i - 1][1].toFixed(1)}L${bit}`;
    }
  }
  flush();
  return out;
}

function deckLineChart(cols, series, unit, title, dec, fx){
  const colors = series.map((_, i) => DECK_SERIES[i % DECK_SERIES.length]);
  const n = cols.length, band = (DECK_W - DECK_ML - DECK_MR) / n;
  const vals = series.flatMap(([, vs]) => vs.filter(v => v != null));
  // 為替は 0 を起点にすると 150 と 160 が潰れる。率の折れ線は今までどおり 0 起点
  let ticks, y;
  if(fx){
    const span = Math.max(...vals) - Math.min(...vals) || Math.abs(Math.max(...vals)) || 1;
    ({ticks, y} = deckScale(Math.min(...vals) - span * 0.18, Math.max(...vals) + span * 0.22, 0));
  } else ({ticks, y} = deckScale(Math.min(0, ...vals), Math.max(...vals), 0.12));
  let marks = "", labels = "";
  series.forEach(([name, vs], k) => {
    const pts = vs.map((v, i) => v == null ? null : [DECK_ML + band * (i + 0.5), y(v), v, cols[i]]).filter(Boolean);
    marks += deckLinePaths(pts, colors[k], fx);
    for(const [px, py, v, c] of pts){
      const hollow = deckFcst(c);
      marks += `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="4.5" fill="${hollow ? "#fff" : colors[k]}" stroke="${hollow ? colors[k] : "#fff"}" stroke-width="2"><title>${deckEsc(`${c.period} ${name} ${deckFmt(v, dec)}${unit}`)}</title></circle>`;
    }
  });
  for(let i = 0; i < n; i++){
    const pts = series.map(([, vs]) => vs[i]).filter(v => v != null).map(v => [y(v), v]).sort((a, b) => a[0] - b[0]);
    const taken = [];
    pts.forEach(([py, v], j) => {
      for(const ty of (j === 0 ? [py - 9, py + 17] : [py + 17, py - 9])){
        if(DECK_MT <= ty - 10 && ty <= DECK_H - DECK_MB && taken.every(t => Math.abs(ty - t) >= 12) && pts.every(([q]) => Math.abs(ty - 3 - q) >= 8)){
          taken.push(ty);
          labels += `<text x="${(DECK_ML + band * (i + 0.5)).toFixed(1)}" y="${ty.toFixed(1)}" class="val" text-anchor="middle">${deckEsc(deckFmt(v, dec))}</text>`;
          break;
        }
      }
    });
  }
  const svg = `<svg viewBox="0 0 ${DECK_W} ${DECK_H}" role="img" aria-label="${deckEsc(title)}">${deckFrame(ticks, y, dec)}${marks}${labels}${deckXLabels(cols, band)}</svg>`;
  const note = cols.some(deckFcst) ? (fx ? "破線・白抜きは予想・計画" : "白抜きは予想・計画") : "";
  return deckFigure(title, unit, note, deckLegend(series.map(([nm]) => nm), colors, "line"), svg);
}

// 表 → グラフ（実績・予想・計画の列が 3 つ以上のときだけ）。内訳は積み上げ、内訳の無い金額は棒（3 本まで）、率と為替は折れ線
function deckCharts(table){
  const cols = table.cols || [], rows = table.rows || [];
  const idx = cols.map((c, i) => DECK_SERIES_KINDS.includes(c.kind) ? i : -1).filter(i => i >= 0);
  if(idx.length < 3) return [];
  const scols = idx.map(i => cols[i]);
  const data = rows.map(r => [r, idx.map(i => deckNum((r.values || [])[i]))]).filter(([, vs]) => vs.filter(v => v != null).length >= 2);
  const out = [], used = new Set();
  for(const [r, vals] of data){
    const key = deckKey(r), unit = String(r.unit || "");
    const kidRows = data.filter(([c]) => deckOf(c) === key);
    if(kidRows.length < 2 || DECK_RATE_UNITS.includes(unit)) continue;
    let kids = kidRows.map(([c, v]) => [deckKidLabel(c, r), v]);
    if(kids.length > 8){
      const latest = kv => Math.abs([...kv[1]].reverse().find(v => v != null) || 0);
      kids.sort((a, b) => latest(b) - latest(a));
      const rest = kids.slice(7);
      kids = kids.slice(0, 7).concat([["その他", scols.map((_, i) => rest.reduce((s, [, v]) => s + (v[i] || 0), 0))]]);
    }
    out.push(deckBarChart(scols, kids, unit, `${deckLabel(r)}の内訳`, deckDec([r, ...kidRows.map(([c]) => c)]), vals, deckLabel(r)));
    used.add(key);
    kidRows.forEach(([c]) => used.add(deckKey(c)));
    if(out.length >= 3) return out;
  }
  const groups = new Map();
  for(const [r, v] of data){
    if(used.has(deckKey(r)) || r.of) continue;
    const g = `${r.unit || ""}\t${String(r.metric || "").split("/")[0]}`;
    if(!groups.has(g)) groups.set(g, []);
    groups.get(g).push([r, v]);
  }
  const merged = new Map();
  for(const [g, items] of groups){
    const unit = g.split("\t")[0];
    const k = items.length === 1 && unit !== "円" ? `${unit}\t*` : g;
    if(!merged.has(k)) merged.set(k, []);
    merged.get(k).push(...items);
  }
  for(const [g, items] of merged){
    const unit = g.split("\t")[0];
    if(items.length > 4) continue;
    const series = items.map(([r, v]) => [deckLabel(r), v]), title = items.map(([r]) => deckLabel(r)).join("・");
    const dec = deckDec(items.map(([r]) => r));
    const fx = items.every(([r]) => deckFxRow(r));
    if(DECK_RATE_UNITS.includes(unit) || fx) out.push(deckLineChart(scols, series, unit, title, dec, fx));
    else if(items.length <= 3) out.push(deckBarChart(scols, series, unit, title, dec));
    if(out.length >= 3) break;
  }
  return out;
}

function deckTable(table){
  const cols = table.cols || [];
  const head = cols.map(c => `<th>${deckEsc(c.period)}<span class="dk-k">${deckEsc(DECK_KIND[c.kind] || c.kind || "")}</span></th>`).join("");
  const body = (table.rows || []).map(r => {
    const cells = cols.map((c, i) => {
      const raw = (r.values || [])[i];
      const v = deckNum(raw);
      let s = v == null ? "" : String(raw).normalize("NFKC").trim().replace("-", "−");
      if(v != null && c.kind === "change" && v > 0 && !s.startsWith("+")) s = "+" + s;
      return `<td class="n">${deckEsc(s)}</td>`;
    }).join("");
    return `<tr><th class="dk-r${r.of ? " d1" : ""}">${deckEsc(deckLabel(r))}</th><td class="u">${deckEsc(r.unit || "")}</td>${cells}</tr>`;
  }).join("");
  return `<div class="tbl-wrap"><table class="sheet dk-tbl"><thead><tr><th></th><th class="u">単位</th>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

const DECK_STATUS = {done: "読み取り済み", submitted: "読み取り中", failed: "読めなかった"};

function renderStockDecks(){
  if(!stockDeckKey){
    stockSub = "news";
    renderStockSub();
    return;
  }
  showStockDeck(stockDeckKey);
}

async function showStockDeck(key){
  const body = $("#st-deck-body");
  body.innerHTML = "";
  const code = stockCode;
  let d;
  try {
    d = await api(`/api/stock/${code}/decks/${key}`, {signal: stockAc.signal});
  } catch(e){
    if(e.name === "AbortError" || code !== stockCode) return;
    stockMsg(String(e.message) === "404" ? "説明資料が見つかりません。" : "読み込めませんでした。");
    return;
  }
  if(code !== stockCode || key !== stockDeckKey) return;
  body.innerHTML = deckDocHtml(d, body.clientWidth);
}

function deckDocHtml(d, width){
  const doc = d.document, ex = d.extract;
  let h = `<div class="dk-head"><b>${deckEsc(doc.title)}</b><span class="sub">${deckEsc(doc.pubdate.slice(0, 10))}</span>`;
  if(doc.url) h += `<a href="${deckEsc(doc.url)}" target="_blank" rel="noopener noreferrer">原本の PDF</a>`;
  h += "</div>";
  if(!ex) return h + `<p class="sub">${DECK_STATUS[doc.status] || "まだ読み取っていません"}。</p>`;
  DECK_W = Math.max(320, Math.min(760, width || 720));
  h += `<p class="sub dk-meta">読み取った数値と要約から組み直したもの（原本ではない）。数字 ${ex.facts} 件のうち文字データで確かめた ${ex.verified} 件、`
    + `内訳の合計が合わない ${ex.sum_mismatch} 件。</p>`;
  for(const p of d.pages){
    if(!p.title && !p.summary && !p.error) continue;
    let s = `<section class="dk-page"><h3><span class="sub">p${p.page}</span> ${deckEsc(p.title || "")}</h3>`;
    if(p.summary) s += `<p class="dk-sum">${deckEsc(p.summary)}</p>`;
    if(p.error) s += `<p class="sub">このページは読めませんでした。</p>`;
    for(const t of p.tables) s += deckCharts(t).join("") + deckTable(t);
    const notes = [];
    if(p.unverified) notes.push(`文字データで確かめられない数字 ${p.unverified} 件`);
    if(p.mismatch) notes.push(`内訳の合計が合わない行 ${p.mismatch} 件`);
    if(notes.length) s += `<p class="sub dk-warn">${notes.join("・")}</p>`;
    h += s + "</section>";
  }
  return h;
}

if(typeof document !== "undefined") $("#st-deck-back").addEventListener("click", e => {
  e.preventDefault();
  stockDeckKey = "";
  stockSub = "news";
  writeRoute("stock", stockRoutePath(), "stock-deck-detail");
  renderStockSub();
});
// node のテスト（tests/market/test_stock_decks.py）が組み立てを呼ぶ
if(typeof module !== "undefined") module.exports = {deckCharts, deckTable, deckNum, deckDocHtml};

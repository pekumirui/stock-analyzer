const $ = s => document.querySelector(s);
const api = (p, init) => fetch(p, init).then(r => { if(!r.ok) throw new Error(r.status); return r.json(); });
const fmt = (v,d=0) => v==null ? "–" : Number(v).toLocaleString("ja-JP",{maximumFractionDigits:d,minimumFractionDigits:d});
const pct = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v>0?'+':''}${fmt(v,1)}%</span>`;
const under = html => `<span class="g">${html || " "}</span>`;
const shortName = n => { const s = String(n||""); return s.length>5 ? s.slice(0,5) : s; };
function pinCols(table, n){
  if(!table) return;
  const ths = table.querySelectorAll("thead tr:first-child th");
  if(ths.length < n) return;
  const w = [...ths].slice(0, n).map(th => th.offsetWidth);
  if(w.some(x => x < 1)) return;
  let x = 0;
  for(let i=0; i<n; i++){ table.style.setProperty(`--pin${i}`, `${x}px`); x += w[i]; }
}
// 市場と業種。月次とランキングで同じ表記（市場は 東証P / 東証S / 東証G。色の点は CSS の .mkt-*）
function fmtMarket(market){
  const s = String(market || "");
  if(s.includes("プライム") || /prime/i.test(s)) return {cls:"mkt mkt-p", label:"東証P"};
  if(s.includes("スタンダード") || /standard/i.test(s)) return {cls:"mkt mkt-s", label:"東証S"};
  if(s.includes("グロース") || /growth/i.test(s)) return {cls:"mkt mkt-g", label:"東証G"};
  return {cls:"", label:s};
}
// 33 業種。末尾の「業」を外して先頭 5 文字
function fmtSector(sector){
  const s = String(sector || "").replace(/業$/, "");
  return Array.from(s).slice(0, 5).join("");
}

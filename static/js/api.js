const $ = s => document.querySelector(s);
const api = (p, init) => fetch(p, init).then(r => { if(!r.ok) throw new Error(r.status); return r.json(); });
const fmt = (v,d=0) => v==null ? "–" : Number(v).toLocaleString("ja-JP",{maximumFractionDigits:d,minimumFractionDigits:d});
const pct = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v>0?'+':''}${fmt(v,1)}%</span>`;
const under = html => `<span class="g">${html || " "}</span>`;
function pinCols(table, n){
  if(!table) return;
  const ths = table.querySelectorAll("thead tr:first-child th");
  if(ths.length < n) return;
  const w = [...ths].slice(0, n).map(th => th.offsetWidth);
  if(w.some(x => x < 1)) return;
  let x = 0;
  for(let i=0; i<n; i++){ table.style.setProperty(`--pin${i}`, `${x}px`); x += w[i]; }
}

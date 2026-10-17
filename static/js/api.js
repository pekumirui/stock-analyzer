const $ = s => document.querySelector(s);
const api = (p, init) => fetch(p, init).then(r => { if(!r.ok) throw new Error(r.status); return r.json(); });
const fmt = (v,d=0) => v==null ? "–" : Number(v).toLocaleString("ja-JP",{maximumFractionDigits:d,minimumFractionDigits:d});
const pct = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v>0?'+':''}${fmt(v,1)}%</span>`;

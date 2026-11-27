const $ = s => document.querySelector(s);
const api = (p, init) => fetch(p, init).then(r => { if(!r.ok) throw new Error(r.status); return r.json(); });
const fmtNf = {};
const fmt = (v,d=0) => v==null ? "–" : (fmtNf[d] ||= new Intl.NumberFormat("ja-JP",{maximumFractionDigits:d,minimumFractionDigits:d})).format(Number(v));
const pct = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v>0?'+':''}${fmt(v,1)}%</span>`;
const under = html => `<span class="g">${html || " "}</span>`;
const shortName = n => { const s = String(n||""); return s.length>5 ? s.slice(0,5) : s; };
function chartTip(host, hide){
  if(host._chartTip){ host._chartTip.hide = hide; host._chartTip.at = null; return host._chartTip; }
  const ctl = {
    at: null, hide,
    hover(key){ ctl.at = key; return true; },
    tap(key){ ctl.at = ctl.at === key ? null : key; if(ctl.at === null) ctl.hide(); return ctl.at !== null; },
    press(key, e){ return e && e.pointerType === "mouse" ? ctl.hover(key) : ctl.tap(key); },
    close(){ if(ctl.at === null) return; ctl.at = null; ctl.hide(); },
  };
  host._chartTip = ctl;
  document.addEventListener("pointerdown", e => { if(!host.contains(e.target)) ctl.close(); }, true);
  document.addEventListener("keydown", e => { if(e.key === "Escape") ctl.close(); });
  return ctl;
}
function pinCols(table, n){
  if(!table) return;
  const ths = table.querySelectorAll("thead tr:first-child th");
  if(ths.length < n) return;
  const w = [...ths].slice(0, n).map(th => th.offsetWidth);
  if(w.some(x => x < 1)) return;
  // 位置は表の親に持たせる。表を作り直しても残り、変わらなければ組み直しが 1 回で済む
  const host = table.parentElement.style;
  let x = 0;
  for(let i=0; i<n; i++){
    if(host.getPropertyValue(`--pin${i}`) !== `${x}px`) host.setProperty(`--pin${i}`, `${x}px`);
    x += w[i];
  }
}
function fmtMarket(market){
  const s = String(market || "").normalize("NFKC");
  if(s.includes("プライム") || /prime/i.test(s) || s === "東P") return {cls:"mkt mkt-p", label:"東証P"};
  if(s.includes("スタンダード") || /standard/i.test(s) || s === "東S") return {cls:"mkt mkt-s", label:"東証S"};
  if(s.includes("グロース") || /growth/i.test(s) || s === "東G") return {cls:"mkt mkt-g", label:"東証G"};
  return {cls:"", label:s};
}
function fmtSector(sector){
  const s = String(sector || "").replace(/業$/, "");
  return Array.from(s).slice(0, 5).join("");
}

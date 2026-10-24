const mil = v => v==null ? "–" : fmt(v/1e6, 0);
const GST = {turn_profit:["黒転","up"], turn_loss:["赤転","dn"], loss:["赤継",null]};
function gc(c){
  if(!c || c.state==="na") return "–";
  if(c.state==="up" || c.state==="down"){
    if(c.pct != null && c.pct >= 100) return `<span class="pos">${fmt(c.pct/100+1,1)}倍</span>`;
    return pct(c.pct);
  }
  const [txt,cls] = GST[c.state] || ["–", null];
  return cls ? `<span class="badge ${cls}">${txt}</span>` : `<span class="sub">${txt}</span>`;
}

let calY, calM, selDate=null;
(()=>{ const t=new Date(); calY=t.getFullYear(); calM=t.getMonth()+1; })();
const todayISO = () => {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth()+1).padStart(2,"0")}-${String(t.getDate()).padStart(2,"0")}`;
};
function calOpen(){
  const pop = $("#cal-pop"), btn = $("#cal-btn"), bd = $("#cal-backdrop");
  pop.hidden = false;
  bd.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  renderCal();
}
function calClose(){
  const pop = $("#cal-pop"), btn = $("#cal-btn"), bd = $("#cal-backdrop");
  if(pop.hidden) return;
  pop.hidden = true;
  bd.hidden = true;
  btn.setAttribute("aria-expanded", "false");
}
function calToggle(){ $("#cal-pop").hidden ? calOpen() : calClose(); }
function calMove(d){ calM+=d; if(calM>12){calM=1;calY++} if(calM<1){calM=12;calY--} renderCal(); }
const calCells = {}, calReq = {};
function fetchCal(mm){
  calReq[mm] ??= api(`/api/earnings/calendar?month=${mm}&today=${todayISO()}`)
    .then(c => calCells[mm] = c)
    .finally(() => { delete calReq[mm]; });
  return calReq[mm];
}
const calMonth = () => `${calY}-${String(calM).padStart(2,"0")}`;
function calPrefetch(){ fetchCal(calMonth()).catch(()=>{}); }
async function renderCal(){
  const mm = calMonth();
  $("#cal-title").textContent = `${calY}年${calM}月`;
  if(calCells[mm]) drawCal(mm, calCells[mm]);
  const cells = await fetchCal(mm).catch(()=>({}));
  if(mm === calMonth()) drawCal(mm, cells);
}
function drawCal(mm, cells){
  const today = todayISO();
  const first = new Date(calY, calM-1, 1), days = new Date(calY, calM, 0).getDate();
  let html = ["日","月","火","水","木","金","土"].map(d=>`<div class="h">${d}</div>`).join("");
  for(let i=0;i<first.getDay();i++) html += "<div></div>";
  for(let d=1;d<=days;d++){
    const ds = `${mm}-${String(d).padStart(2,"0")}`;
    const cell = cells[ds];
    const cls = ["d"];
    if(ds===selDate) cls.push("sel");
    if(ds===today) cls.push("today");
    let badge = "";
    if(cell && cell.n){
      const muted = cell.kind==="scheduled" ? " sched" : "";
      badge += `<div class="cnt${muted}">${cell.n}件</div>`;
    }
    if(cell && cell.pending) badge += `<div class="cnt pend">+${cell.pending}</div>`;
    html += `<div class="${cls.join(" ")}" onclick="selectDate('${ds}')"><div class="n">${d}</div>${badge}</div>`;
  }
  $("#cal").innerHTML = html;
}
$("#cal-btn").addEventListener("click", e => { e.stopPropagation(); calToggle(); });
$("#cal-pop").addEventListener("click", e => e.stopPropagation());
$("#cal-backdrop").addEventListener("click", calClose);
document.addEventListener("keydown", e => { if(e.key==="Escape") calClose(); });
document.addEventListener("click", e => {
  if($("#cal-pop").hidden) return;
  if(!$("#cal-wrap").contains(e.target) && e.target!==$("#cal-backdrop")) calClose();
});

const MET = [["net_sales","売上"],["operating_profit","営利"],["ordinary_profit","経利"],["profit","純利"]];
const revBadge = r => r.restated ? `<span class="qb qb-rev" title="既出期間の訂正">訂正</span>` : "";
const listQ = q => q ? qBadge({kind:"single", period:q}) : "";
const under = html => `<span class="g">${html || " "}</span>`;
const shortName = n => { const s = String(n||""); return s.length>5 ? s.slice(0,5) : s; };
const nameCell = r => `<td class="l name pin2" title="${r.name}">${shortName(r.name)}${under(`<span class="sub">${r.code.slice(0,4)}</span>`)}</td>`;
const valCell = (vals, g, k, i) => `<td class="num ${i?'':'grp'}">${amt((vals||{})[k])}${under(gc(g && g[k]))}</td>`;
const progTxt = v => v==null ? "–" : `<span class="${v<0?"loss":""}">${fmt(v,1)}%</span>`;
const progCell = (p, k, i) => {
  const cur = p && p.cur && p.cur[k], prev = p && p.prev && p.prev[k];
  return `<td class="num ${i?'':'grp'}">${progTxt(cur)}${under(prev==null ? "" : `<span class="prev" title="前年同期の進捗率">${progTxt(prev)}</span>`)}</td>`;
};
const fyShort = s => String(s||"").replace(/^FY/, "");
let earnBoard={kind:"scheduled", actuals:[], scheduled:[]}, selKey=null;
let detailAc = null;
function beginDetail(){
  if(detailAc) detailAc.abort();
  detailAc = new AbortController();
  return detailAc.signal;
}
function apiMaybe(p, signal){
  return api(p, {signal}).catch(e => { if(e.name === "AbortError") throw e; return null; });
}
let earnDay="", earnToday="";
let earnSector="", earnChipsOpen=false, earnView="cum";
const CHIP_TOP = 5;  // 畳んだときに見せる業種の数 (「すべて」は別)
const sectorOf = r => r.sector33 || "未分類";
const inSector = r => !earnSector || sectorOf(r) === earnSector;

function emptyCopy(ds, today, board){
  if(ds > today && board.ready === false) return "予定未取得";
  if(ds < today || board.kind === "actual") return "開示なし";
  return "予定なし";
}

function flatRows(){
  const actuals = (earnBoard.actuals||[]).filter(inSector).map(r => ({side:"a", id:r.disclosure_no, row:r}));
  const scheduled = (earnBoard.scheduled||[]).filter(inSector).map(r => ({side:"s", id:r.code, row:r}));
  return actuals.concat(scheduled);
}

async function selectDate(ds, want){
  selDate = ds;
  earnSector = "";
  earnChipsOpen = false;
  calClose();
  const today = todayISO();
  earnDay = ds;
  earnToday = today;
  let board;
  try {
    board = await api(`/api/earnings?date=${ds}&today=${today}`);
  } catch(e) {
    earnBoard = {kind:"scheduled", actuals:[], scheduled:[]};
    selKey = null;
    $("#earn-title").textContent = `${ds}の決算発表`;
    $("#earn-count").textContent = "";
    $("#earn-groups").innerHTML = "";
    $("#earn-list").innerHTML = `<div class="sub">取得できませんでした</div>`;
    clearDetail("取得できませんでした");
    return;
  }
  earnBoard = board;
  const future = ds > today;
  $("#earn-title").textContent = `${ds}の${future ? "決算発表予定" : "決算発表"}`;
  const nA = (board.actuals||[]).length, nS = (board.scheduled||[]).length;
  if(board.kind==="mixed") $("#earn-count").textContent = `${nA}件・未発表 ${nS}`;
  else if(nA) $("#earn-count").textContent = `${nA}件`;
  else if(nS) $("#earn-count").textContent = `${nS}件`;
  else $("#earn-count").textContent = "";
  selKey = null;
  renderGroups();
  renderList();
  const rows = flatRows();
  const hit = want && (rows.find(r => r.side==="a" && r.id===want.no)
                    || rows.find(r => r.row.code===want.code));
  if(hit){ selectFlat(hit); scrollToSel(); }
  else if(want) loadDetail({code:want.code, name:want.name, disclosure_no:want.no, fy_label:"", period:want.period});
  else if(rows.length) selectFlat(rows[0]);
  else clearDetail(emptyCopy(ds, today, board));
}

function renderGroups(){
  const codes = {};
  for(const r of (earnBoard.actuals||[]).concat(earnBoard.scheduled||[]))
    (codes[sectorOf(r)] ||= new Set()).add(r.code);
  const groups = Object.entries(codes).map(([g, set]) => [g, set.size])
    .sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0], "ja"));
  if(!groups.length){ $("#earn-groups").innerHTML = ""; return; }
  const chip = (g, label, n) =>
    `<button type="button" class="mchip${earnSector===g?" on":""}" data-sector="${escHtml(g)}">`
    + `${escHtml(label)}${n==null ? "" : `<span class="n">${n}</span>`}</button>`;
  const shown = earnChipsOpen ? groups
    : groups.filter(([g], i) => i < CHIP_TOP || g === earnSector);
  const rest = groups.length - shown.length;
  const more = earnChipsOpen && groups.length > CHIP_TOP
    ? `<button type="button" class="mchip more" data-more="0">閉じる</button>`
    : rest > 0 ? `<button type="button" class="mchip more" data-more="1">他 ${rest}業種</button>` : "";
  $("#earn-groups").innerHTML = chip("", "すべて", null)
    + shown.map(([g, n]) => chip(g, g.replace(/業$/, ""), n)).join("") + more;
}

function setSector(g){
  earnSector = g;
  renderGroups();
  renderList();
  const rows = flatRows();
  if(rows.length && !rows.some(r => (r.side==="a" ? "a:" : "s:")+r.id === selKey)) selectFlat(rows[0]);
}

function renderList(){
  const actuals = (earnBoard.actuals||[]).filter(inSector), scheduled = (earnBoard.scheduled||[]).filter(inSector);
  if(!actuals.length && !scheduled.length){
    $("#earn-list").innerHTML = `<div class="sub">${emptyCopy(earnDay, earnToday, earnBoard)}</div>`;
    return;
  }
  $("#earn-list").innerHTML = tableHtml(actuals, scheduled);
  pinCols();
  markSel();
}

function pinCols(){
  const t = $("#earn-tbl");
  const ths = t ? t.querySelectorAll("thead tr:first-child th") : [];
  if(ths.length < 3) return;
  const w = [...ths].slice(0, 3).map(th => th.offsetWidth);
  if(w.some(x => x < 1)) return;
  let x = 0;
  for(let i=0; i<3; i++){ t.style.setProperty(`--pin${i}`, `${x}px`); x += w[i]; }
}
new ResizeObserver(pinCols).observe($("#earn-list"));

function scrollToSel(){
  const el = document.querySelector("#earn-list tr.sel");
  if(el) el.scrollIntoView({block:"nearest"});
}

function tableHtml(actuals, scheduled){
  const sub = () => MET.map(([k,l],i) => `<th class="${i?'':'grp'}">${l}</th>`).join("");
  const dash = MET.map((_,i)=>`<td class="num ${i?'':'grp'}">–</td>`).join("");
  let h = `<table id="earn-tbl"><thead>`
        + `<tr><th class="pin0">時刻</th><th class="l fy pin1">決算期</th>`
        + `<th class="l name pin2">銘柄</th>${sub()}</tr></thead><tbody>`;
  for(const r of actuals){
    const tip = r.period==="1Q" ? ' title="前期4Q単独比"' : "";
    h += `<tr data-no="${r.disclosure_no}" onclick="selectRow('${r.disclosure_no}')">`
       + `<td class="pin0">${(r.disclosed_time||"").slice(0,5)}${under(revBadge(r))}</td>`
       + `<td class="l fy pin1">${fyShort(r.fy_label)}${under(listQ(r.period))}</td>`
       + nameCell(r)
       + (earnView==="q" ? MET.map(([k],i)=>valCell(r.single, r.qoq, k, i).replace("<td ", `<td${tip} `)).join("")
        : earnView==="cum" ? MET.map(([k],i)=>valCell(r.cumulative, r.yoy, k, i)).join("")
        : MET.map(([k],i)=>progCell(r.progress, k, i)).join(""))
       + `</tr>`;
  }
  for(const r of scheduled){
    const fq = (r.periods||[]).map(p => listQ(p.fq)).join("");
    const label = (r.periods||[]).map(p => fyShort(p.label)).join(" / ");
    h += `<tr data-code="${r.code}" onclick="selectSched('${r.code}')">`
       + `<td class="pin0">–</td>`
       + `<td class="l fy pin1">${label}${under(fq)}</td>`
       + nameCell(r)
       + dash
       + `</tr>`;
  }
  return h + `</tbody></table>`;
}

function markSel(){
  document.querySelectorAll("#earn-list [data-no]").forEach(el =>
    el.classList.toggle("sel", "a:"+el.dataset.no === selKey));
  document.querySelectorAll("#earn-list [data-code]").forEach(el =>
    el.classList.toggle("sel", "s:"+el.dataset.code === selKey));
}

function selectFlat(entry){
  if(entry.side==="a") selectRow(entry.id);
  else selectSched(entry.id);
}

function selectRow(no){
  selKey = "a:"+no; markSel();
  const row = (earnBoard.actuals||[]).find(r => r.disclosure_no === no);
  if(!row) return;
  loadDetail(row);
}

function selectSched(code){
  selKey = "s:"+code; markSel();
  const row = (earnBoard.scheduled||[]).find(r => r.code === code);
  if(!row) return;
  loadSched(row);
}

function clearDetail(msg){
  if(detailAc){ detailAc.abort(); detailAc = null; }
  $("#e-chart-title").textContent = msg;
  $("#e-chart").innerHTML = ""; $("#cum-tbl").innerHTML = ""; $("#prog-tbl").innerHTML = ""; $("#q-tbl").innerHTML = ""; $("#rev-tbl").innerHTML = "";
}

async function loadSched(row){
  const signal = beginDetail();
  $("#e-chart-title").innerHTML = `<span class="clicky" onclick="gotoChart('${row.code}')">${row.name}</span>
    <small class="sub">${row.code.slice(0,4)}</small>`;
  let ch;
  try { ch = await apiMaybe(`/api/chart/${row.code}?tf=D&ma=50,150,200`, signal); }
  catch(e){ if(e.name === "AbortError") return; throw e; }
  if(signal.aborted) return;
  if(ch) eChartObj = renderPriceChart($("#e-chart"), ch, {height:320, prev:eChartObj});
  else { $("#e-chart").innerHTML = `<div class="sub">株価データなし</div>`; }
  const pending = `<tr><td class="l sub">まだ開示されていません</td></tr>`;
  $("#cum-tbl").innerHTML = pending;
  $("#q-tbl").innerHTML = pending;
  $("#prog-tbl").innerHTML = pending;
  $("#rev-tbl").innerHTML = pending;
}

async function loadDetail(row){
  const signal = beginDetail();
  $("#e-chart-title").innerHTML = `<span class="clicky" onclick="gotoChart('${row.code}')">${row.name}</span>
    <small class="sub">${row.code.slice(0,4)} / ${fyShort(row.fy_label)} ${row.period||""}</small>`;
  let d, ch;
  try {
    [d, ch] = await Promise.all([
      apiMaybe(`/api/earnings/detail/${row.code}?disclosure_no=${encodeURIComponent(row.disclosure_no)}`, signal),
      apiMaybe(`/api/chart/${row.code}?tf=D&ma=50,150,200`, signal)]);
  } catch(e){ if(e.name === "AbortError") return; throw e; }
  if(signal.aborted) return;
  if(ch) eChartObj = renderPriceChart($("#e-chart"), ch, {height:320, prev:eChartObj, mark:d && d.disclosed_date});
  else { $("#e-chart").innerHTML = `<div class="sub">株価データなし</div>`; }
  renderFin($("#cum-tbl"), d && d.cumulative, d);
  renderFin($("#q-tbl"),   d && d.quarterly,  d);
  renderProg($("#prog-tbl"), d && d.progress, d);
  renderRev($("#rev-tbl"), d && d.revisions,  d);
}

const FIN_MET = [["net_sales","売上高"],["operating_profit","営業益"],
                 ["ordinary_profit","経常益"],["profit","最終益"]];
function disc(iso){ if(!iso) return "–"; const [y,m,d]=iso.split("-"); return `${y.slice(2)}/${m}/${d}`; }
function opm(v){
  const s = v && v.net_sales, o = v && v.operating_profit;
  if(s == null || s <= 0 || o == null) return null;
  return o / s * 100;
}
function amt(v){
  if(v == null) return "–";
  return `<span class="${v<0?"loss":""}">${mil(v)}</span>`;
}
function opAmt(r){
  const v = r.values && r.values.operating_profit;
  const m = opm(r.values);
  const rate = m==null ? "" : `<span class="opm${m<0?" loss":""}">(${fmt(m,1)}%)</span>`;
  return `${amt(v)}${rate}`;
}
function epsTxt(v){
  if(v == null) return "–";
  return `<span class="${v<0?"loss":""}">${fmt(v,1)}</span>`;
}
function finLabel(r){
  return r.range || r.label;
}
function finTitle(r){
  if(r.kind==="forecast_fy") return `${r.label||""} 通期予想`;
  if(r.kind==="forecast_h") return `${r.label||""} 中間予想`;
  if(r.kind==="actual_fy_prev") return `${r.label||""} 通期`;
  return r.label||"";
}
function finBadge(r){
  if(r.kind==="forecast_fy") return `<span class="qb qb-fy">通予</span>`;
  if(r.kind==="forecast_h") return `<span class="qb qb-h">中予</span>`;
  if(r.kind==="actual_fy_prev") return `<span class="qb qb-prev">通期</span>`;
  return qBadge(r);
}
function qBadge(r){
  let q = r.period || "";
  if(r.kind==="single" && q==="FY") q = "4Q";
  if(r.kind!=="single" && (q==="4Q" || q==="FY")) return "";
  if(q==="5Q") return `<span class="qb">5Q</span>`;
  return /^[1-4]Q$/.test(q) ? `<span class="qb qb-${q}">${q}</span>` : "";
}
function renderFin(el, rows, d){
  if(!rows || !rows.length){
    el.innerHTML = `<tr><td class="l sub">${d && d.notes && d.notes.single_unavailable ? "算出不能 (決算期変更など)" : "データなし"}</td></tr>`;
    return;
  }
  const dim = d && d.notes && d.notes.no_ordinary;
  const dc = k => k==="ordinary_profit" && dim ? " dim" : "";
  let h = `<thead><tr><th class="l">決算期</th>`
        + FIN_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
        + `<th>EPS</th><th class="disc">発表日</th></tr></thead><tbody>`;
  let hi = -1;
  rows.forEach((r,i) => { if(r.kind==="actual_current" || r.kind==="single") hi = i; });
  const extra = r => r.kind==="actual_fy_prev" || String(r.kind||"").startsWith("forecast");
  rows.forEach((r,i) => {
    const now = r.kind==="actual_current" || (r.kind==="single" && i===hi);
    const fc = String(r.kind||"").startsWith("forecast");
    const prev = r.kind==="actual_fy_prev";
    const sep = extra(r) && !rows.slice(0,i).some(extra);
    const cls = [now&&"now", fc&&"fc", prev&&"prevfy", sep&&"sep"].filter(Boolean).join(" ");
    const cell = (k, txt) => {
      const g = r.growth && r.growth[k];
      return `<td class="num${dc(k)}">${txt}<span class="g">${g ? gc(g) : " "}</span></td>`;
    };
    h += `<tr class="${cls}"><td class="l" title="${finTitle(r)}">${finLabel(r)}<span class="g">${finBadge(r) || " "}</span></td>`
       + FIN_MET.map(([k])=>cell(k, k==="operating_profit" ? opAmt(r) : amt(r.values[k]))).join("")
       + cell("eps", epsTxt(r.values.eps))
       + `<td class="num disc sub">${disc(r.disclosed_date)}</td></tr>`;
  });
  el.innerHTML = h + `</tbody>`;
}

function renderProg(el, rows, d){
  if(!rows || !rows.length){
    el.innerHTML = `<tr><td class="l sub">${d && /^(FY|4Q)$/.test(d.period||"") ? "本決算は進捗率なし" : "データなし"}</td></tr>`;
    return;
  }
  const dim = d && d.notes && d.notes.no_ordinary;
  const dc = k => k==="ordinary_profit" && dim ? " dim" : "";
  let h = `<thead><tr><th class="l">決算期</th>`
        + FIN_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
        + `<th class="disc">発表日</th></tr></thead><tbody>`;
  rows.forEach(r => {
    const now = r.kind==="actual_current";
    const cell = k => {
      const b = r.base && r.base[k];
      const tip = now ? "通期予想" : "通期実績";
      return `<td class="num${dc(k)}">${progTxt(r.progress && r.progress[k])}`
           + `<span class="g"><span class="base" title="${tip}">${b==null ? " " : mil(b)}</span></span></td>`;
    };
    h += `<tr class="${now?"now":""}"><td class="l" title="${now ? "累計 / 通期予想" : "累計 / 通期実績"}">${finLabel(r)}<span class="g">${finBadge(r) || " "}</span></td>`
       + FIN_MET.map(([k])=>cell(k)).join("")
       + `<td class="num disc sub">${disc(r.disclosed_date)}</td></tr>`;
  });
  el.innerHTML = h + `</tbody>`;
}

const REV_BADGE = {forecast_initial:["期初","qb-init","期初予想"], forecast:["予想","qb-init","取得できる最初の予想"],
                   forecast_revised:["修正","qb-mod","予想修正"],
                   actual_fy:["実績","qb-act","通期実績"]};
function revG(c){
  if(!c || c.state==="na") return "–";
  if(c.state==="flat") return `<span class="sub">→</span>`;
  const arrow = c.state==="up" ? `<span class="pos">↑</span>` : c.state==="down" ? `<span class="neg">↓</span>` : "";
  return arrow + gc(c);
}
function renderRev(el, rows, d){
  if(!rows || !rows.length){
    el.innerHTML = `<tr><td class="l sub">修正履歴なし</td></tr>`;
    return;
  }
  const dim = rows.every(r => r.values.ordinary_profit == null);
  const dc = k => k==="ordinary_profit" && dim ? " dim" : "";
  let h = `<thead><tr><th class="l">決算期</th><th class="disc">発表日</th>`
        + FIN_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
        + `<th>EPS</th></tr></thead><tbody>`;
  rows.forEach((r,i) => {
    const [txt, bc, tip] = REV_BADGE[r.kind] || ["", "", ""];
    const now = d && r.disclosed_date === d.disclosed_date;
    const sep = i > 0 && r.fiscal_year_end !== rows[i-1].fiscal_year_end;
    const cls = [now&&"now", r.kind==="actual_fy"&&"act", sep&&"sep"].filter(Boolean).join(" ");
    const cell = (k, txt) => {
      const g = r.growth && r.growth[k];
      return `<td class="num${dc(k)}">${txt}<span class="g">${g ? revG(g) : " "}</span></td>`;
    };
    h += `<tr class="${cls}"><td class="l" title="${r.label||""} ${tip}">${r.range}<span class="g"><span class="qb ${bc}">${txt}</span></span></td>`
       + `<td class="num disc sub">${disc(r.disclosed_date)}</td>`
       + FIN_MET.map(([k])=>cell(k, k==="operating_profit" ? opAmt(r) : amt(r.values[k]))).join("")
       + cell("eps", epsTxt(r.values.eps)) + `</tr>`;
  });
  el.innerHTML = h + `</tbody>`;
}

const FIN_WRAP = {cum:"#cum-wrap", q:"#q-wrap", prog:"#prog-wrap", rev:"#rev-wrap"};
document.querySelectorAll(".ftab[data-view]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ftab[data-view]").forEach(x => x.classList.toggle("on", x===b));
  earnView = b.dataset.view;
  const sc = $("#earn-list").scrollTop;
  renderList();
  $("#earn-list").scrollTop = sc;
});
document.querySelectorAll(".ftab[data-fin]").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ftab[data-fin]").forEach(x => x.classList.toggle("on", x===b));
  for(const [k, sel] of Object.entries(FIN_WRAP)) $(sel).hidden = k !== b.dataset.fin;
});

$("#earn-list").addEventListener("keydown", e => {
  if(e.key!=="ArrowDown" && e.key!=="ArrowUp") return;
  e.preventDefault();
  const rows = flatRows();
  const i = rows.findIndex(r => (r.side==="a" ? "a:" : "s:")+r.id === selKey);
  const nxt = rows[Math.max(0, Math.min(rows.length-1, i + (e.key==="ArrowDown"?1:-1)))];
  if(!nxt) return;
  selectFlat(nxt);
  const el = nxt.side==="a"
    ? document.querySelector(`#earn-list [data-no="${nxt.id}"]`)
    : document.querySelector(`#earn-list [data-code="${nxt.id}"]`);
  if(el) el.scrollIntoView({block:"nearest"});
});

$("#earn-groups").addEventListener("click", e => {
  const b = e.target.closest(".mchip");
  if(!b) return;
  if(b.dataset.more){ earnChipsOpen = b.dataset.more === "1"; renderGroups(); return; }
  setSector(b.dataset.sector);
});

let earnSearchAc = null;
function hideEarnSuggest(){ $("#earn-suggest").style.display = "none"; $("#earn-suggest").innerHTML = ""; }
async function refreshEarnSuggest(){
  if(earnSearchAc) earnSearchAc.abort();
  const q = $("#earn-sym").value.trim();
  if(!q){ hideEarnSuggest(); return []; }
  const ac = earnSearchAc = new AbortController();
  let hits;
  try { hits = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`, {signal: ac.signal}); }
  catch(e){
    if(e.name === "AbortError") return null;
    hits = [];
  }
  if(ac.signal.aborted) return null;
  const box = $("#earn-suggest");
  box.innerHTML = hits.map(s =>
    `<div data-code="${escHtml(s.code)}" data-name="${escHtml(s.name)}">${escHtml(s.code.slice(0,4))} ${escHtml(s.name)}</div>`).join("");
  box.style.display = hits.length ? "block" : "none";
  return hits;
}
async function pickEarnStock(code, name){
  $("#earn-sym").value = name;
  hideEarnSuggest();
  const d = await api(`/api/earnings/detail/${code}`).catch(()=>null);
  if(!d){
    selKey = null; markSel();
    loadSched({code, name});
    return;
  }
  const [y, m] = d.disclosed_date.split("-").map(Number);
  calY = y; calM = m;
  await selectDate(d.disclosed_date, {no:d.disclosure_no, code, name:d.name, period:d.period});
}
$("#earn-sym").addEventListener("input", refreshEarnSuggest);
$("#earn-sym").addEventListener("compositionend", refreshEarnSuggest);
$("#earn-sym").addEventListener("keydown", async e => {
  if(e.key === "Escape"){ hideEarnSuggest(); return; }
  if(e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
  const hits = await refreshEarnSuggest();
  if(hits && hits.length) pickEarnStock(hits[0].code, hits[0].name);
});
$("#earn-suggest").addEventListener("click", e => {
  const el = e.target.closest("[data-code]");
  if(el) pickEarnStock(el.dataset.code, el.dataset.name);
});
document.addEventListener("click", e => {
  if(!e.target.closest("#earn-suggest") && e.target !== $("#earn-sym")) hideEarnSuggest();
});

window.calMove = calMove;
window.selectDate = selectDate;
window.selectRow = selectRow;
window.selectSched = selectSched;

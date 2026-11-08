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

const MET = [["net_sales","売上"],["gross_profit","粗利"],["operating_profit","営利"],["ordinary_profit","経利"],["profit","純利"]];
function revBadge(r){
  const bits = [];
  const fr = r.forecast_revision;
  if(fr && (fr.direction==="up" || fr.direction==="down")){
    const up = fr.direction==="up";
    const sign = fr.pct>0 ? "+" : "";
    bits.push(`<span class="qb ${up?"qb-up":"qb-dn"}" title="通期営業利益予想 ${sign}${fr.pct}%">${up?"上方":"下方"}</span>`);
  }
  if(r.restated) bits.push(`<span class="qb qb-rev" title="既出期間の訂正">訂正</span>`);
  return bits.join("");
}
const listQ = q => q ? qBadge({kind:"single", period:q}) : "";
const latestQ = p => p==="FY" ? "4Q" : (p || "");
const latestPeriodLabel = p => { const q = latestQ(p); return q ? q+"実績" : ""; };
const latestBadge = p => {
  const label = latestPeriodLabel(p);
  return label ? `<span class="qb qb-act">${label}</span>` : "";
};
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
function chartMaybe(code, signal){
  return chartDaily(code, signal).catch(e => { if(e.name === "AbortError") throw e; return null; });
}

// #e-chart は銘柄タブの株価と同じ部品（chart-core.js）。日足の最新、発表日がそれより前なら発表日を真ん中に
let eChart = null;
function dropEChart(){
  if(eChart){ eChart.destroy(); eChart = null; }
  $("#e-chart").innerHTML = "";
}
function drawEChart(ch, mark){
  if(!ch){ dropEChart(); $("#e-chart").innerHTML = `<div class="sub">株価データなし</div>`; return; }
  if(!eChart) eChart = createStockChart($("#e-chart"), {height: 320});
  eChart.setData(ch, {mark});
  if(mark && !eChart.latestHas(mark)) eChart.showAround(mark);
  else eChart.showLatest();
}
let earnDay="", earnToday="";
let earnSector="", earnChipsOpen=false, earnView="cum";
const CHIP_TOP = 5;  // 畳んだときに見せる業種の数 (「すべて」は別)
const sectorOf = r => r.sector33 || "未分類";
const earnQs = new Set(["1Q","2Q","3Q","4Q"]);
const q4 = q => /^(FY|4Q|5Q)$/.test(q||"") ? "4Q" : q;  // 本決算と変則の 5Q は 4Q に数える
const rowQs = r => r.periods ? r.periods.map(p => q4(p.fq)) : [q4(r.period)];
const inQ = r => rowQs(r).some(q => earnQs.has(q));
const isShown = r => (!earnSector || sectorOf(r) === earnSector) && inQ(r);

function emptyCopy(ds, today, board){
  if(ds > today && board.ready === false) return "予定未取得";
  if(ds < today || board.kind === "actual") return "開示なし";
  return "予定なし";
}

function flatRows(){
  const actuals = (earnBoard.actuals||[]).filter(isShown).map(r => ({side:"a", id:r.disclosure_no, row:r}));
  const scheduled = (earnBoard.scheduled||[]).filter(isShown).map(r => ({side:"s", id:r.code, row:r}));
  return actuals.concat(scheduled);
}

async function selectDate(ds, want){
  selDate = ds;
  expKey = null;
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

$("#earn-qf").addEventListener("change", e => {
  const b = e.target;
  if(b.checked) earnQs.add(b.value); else earnQs.delete(b.value);
  refilter();
});

function setSector(g){
  earnSector = g;
  refilter();
}

function refilter(){
  renderGroups();
  renderList();
  const rows = flatRows();
  if(rows.length && !rows.some(r => (r.side==="a" ? "a:" : "s:")+r.id === selKey)){
    setExp(null);
    selectFlat(rows[0]);
  }
}

function renderList(){
  const actuals = (earnBoard.actuals||[]).filter(isShown), scheduled = (earnBoard.scheduled||[]).filter(isShown);
  if(!actuals.length && !scheduled.length){
    const any = (earnBoard.actuals||[]).length || (earnBoard.scheduled||[]).length;
    $("#earn-list").innerHTML = `<div class="sub">${any ? "該当なし" : emptyCopy(earnDay, earnToday, earnBoard)}</div>`;
    return;
  }
  $("#earn-fin-home").appendChild($("#earn-fin"));
  $("#earn-list").innerHTML = tableHtml(actuals, scheduled);
  pinEarn();
  markSel();
  placeFin();
}

function pinEarn(){
  pinCols($("#earn-tbl"), 3);
  const t = $("#earn-tbl");
  if(t) t.style.setProperty("--xw", `${$("#earn-list").clientWidth}px`);
}
new ResizeObserver(pinEarn).observe($("#earn-list"));

function scrollToSel(){
  const el = document.querySelector("#earn-list tr.sel");
  if(el) el.scrollIntoView({block:"nearest"});
}

function metricCells(src, period){
  const tip = period==="1Q" ? ' title="前期4Q単独比"' : "";
  if(earnView==="q") return MET.map(([k],i)=>valCell(src.single, src.qoq, k, i).replace("<td ", `<td${tip} `)).join("");
  if(earnView==="cum") return MET.map(([k],i)=>valCell(src.cumulative, src.yoy, k, i)).join("");
  return MET.map(([k],i)=>progCell(src.progress, k, i)).join("");
}
function tableHtml(actuals, scheduled){
  const sub = () => MET.map(([k,l],i) => `<th class="${i?'':'grp'}">${l}</th>`).join("");
  const dash = MET.map((_,i)=>`<td class="num ${i?'':'grp'}">–</td>`).join("");
  let h = `<table id="earn-tbl" class="sheet"><thead>`
        + `<tr><th class="pin0">時刻</th><th class="l fy pin1">決算期</th>`
        + `<th class="l name pin2">銘柄</th>${sub()}</tr></thead><tbody>`;
  for(const r of actuals){
    h += `<tr data-no="${r.disclosure_no}" onclick="pickRow('a:${r.disclosure_no}')">`
       + `<td class="pin0">${xBtn("a:"+r.disclosure_no)}${(r.disclosed_time||"").slice(0,5)}${under(revBadge(r))}</td>`
       + `<td class="l fy pin1">${fyShort(r.fy_label)}${under(listQ(r.period))}</td>`
       + nameCell(r)
       + metricCells(r, r.period)
       + `</tr>`;
  }
  for(const r of scheduled){
    const latest = r.latest;
    const fq = (r.periods||[]).map(p => listQ(p.fq)).join("");
    const label = (r.periods||[]).map(p => fyShort(p.label)).join(" / ");
    const fy = latest
      ? `${fyShort(latest.fy_label)}${under(latestBadge(latest.period))}`
      : `${label}${under(fq)}`;
    h += `<tr data-code="${r.code}" onclick="pickRow('s:${r.code}')">`
       + `<td class="pin0">${latest && latest.disclosure_no ? xBtn("s:"+r.code) : `<span class="xsp"></span>`}–</td>`
       + `<td class="l fy pin1">${fy}</td>`
       + nameCell(r)
       + (latest ? metricCells(latest, latest.period) : dash)
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
  setExp(null);
  clearNews();
  $("#e-chart-title").textContent = msg;
  dropEChart(); $("#cum-tbl").innerHTML = ""; $("#prog-tbl").innerHTML = ""; $("#q-tbl").innerHTML = ""; $("#rev-tbl").innerHTML = "";
}

let expKey = null;
const rowEl = key => key && document.querySelector(key.startsWith("a:")
  ? `#earn-tbl tr[data-no="${key.slice(2)}"]` : `#earn-tbl tr[data-code="${key.slice(2)}"]`);
const hasFin = key => !!rowEl(key)?.querySelector(".xbtn");
function xBtn(key){
  const on = key === expKey;
  return `<button type="button" class="xbtn" data-key="${key}" aria-expanded="${on}" aria-label="業績を${on ? "閉じる" : "開く"}"`
    + ` onclick="event.stopPropagation();toggleExp('${key}')">${on ? "▼" : "▶"}</button>`;
}
function selectKey(key){
  if(key.startsWith("a:")) selectRow(key.slice(2));
  else selectSched(key.slice(2));
}
function keepRow(key, fn){
  const before = rowEl(key)?.getBoundingClientRect().top;
  fn();
  const tr = rowEl(key);
  if(before == null || !tr) return;
  const list = $("#earn-list");
  if(!list.classList.contains("xopen")){
    const box = list.getBoundingClientRect().top;
    const head = list.querySelector("thead").offsetHeight;
    const want = Math.max(head, Math.min(before - box, list.clientHeight - tr.offsetHeight));
    list.scrollTop += tr.getBoundingClientRect().top - box - want;
  }
  const d = tr.getBoundingClientRect().top - before;
  if(d) window.scrollBy(0, d);
}
function pickRow(key){
  keepRow(key, () => {
    if(expKey && expKey !== key) setExp(null);
    selectKey(key);
  });
}
function toggleExp(key){
  keepRow(key, () => {
    const open = key !== expKey;
    if(key !== selKey) selectKey(key);
    setExp(open ? key : null);
  });
}
function setExp(key){
  expKey = key;
  placeFin();
}
function placeFin(){
  $("#earn-fin-home").appendChild($("#earn-fin"));
  document.querySelectorAll("#earn-tbl tr.xrow").forEach(x => x.remove());
  if(expKey && !hasFin(expKey)) expKey = null;
  document.querySelectorAll("#earn-tbl .xbtn").forEach(b => {
    const on = b.dataset.key === expKey;
    b.textContent = on ? "▼" : "▶";
    b.setAttribute("aria-expanded", String(on));
    b.setAttribute("aria-label", on ? "業績を閉じる" : "業績を開く");
  });
  $("#earn-list").classList.toggle("xopen", !!expKey);
  if(!expKey) return;
  const x = document.createElement("tr");
  x.className = "xrow";
  const td = document.createElement("td");
  td.colSpan = 3 + MET.length;
  td.appendChild($("#earn-fin"));
  x.appendChild(td);
  rowEl(expKey).after(x);
  pinEarn();
}

let newsCode = "", newsAc = null, newsFilings = [];
function newsMsg(text){
  $("#en-msg").textContent = text || "";
  $("#en-msg").hidden = !text;
}
function clearNews(){
  if(newsAc){ newsAc.abort(); newsAc = null; }
  newsCode = "";
  newsFilings = [];
  showNewsList();
  $("#en-title").textContent = "ニュース";
  $("#en-news").innerHTML = "";
  newsMsg("");
}
function showNewsList(){
  $("#en-detail").hidden = true;
  $("#en-list").hidden = false;
  $("#en-doc").innerHTML = "";
  $("#en-deck").innerHTML = "";
  $("#en-head").innerHTML = "";
  $("#en-src").hidden = true;
}
async function loadNews(code, name){
  if(code === newsCode) return;
  clearNews();
  newsCode = code;
  const ac = newsAc = new AbortController();
  $("#en-title").innerHTML = `ニュース <small class="sub">${stockEsc(shortName(name || ""))} ${code.slice(0,4)}</small>`;
  let fs, decks;
  try {
    [fs, decks] = await Promise.all([
      api(`/api/stock/${code}/filings`, {signal: ac.signal}).then(d => d.filings || []),
      api(`/api/stock/${code}/decks`, {signal: ac.signal}).then(d => d.decks || [])]);
  } catch(e){
    if(e.name === "AbortError" || ac.signal.aborted) return;
    newsMsg("読み込めませんでした。");
    return;
  }
  if(ac.signal.aborted) return;
  newsFilings = fs;
  const rows = stockNewsRows(fs, decks);
  if(!rows.length){ newsMsg("ニュースがありません。"); return; }
  $("#en-news").innerHTML = "<tr><th class='l'>日時</th><th class='l'>種類</th><th class='l'>題名</th></tr>" + rows.map(r => {
    const amend = r.amend ? ' <span class="kind">訂正</span>' : "";
    const open = r.open === "deck" ? ` data-deck="${stockEsc(r.deckKey)}"` : "";
    return `<tr${r.open ? ` class="go" data-id="${stockEsc(r.key)}"${open}` : ""}>
      <td class="l">${stockEsc(stockAt(r.at))}</td>
      <td class="l"><span class="kind">${stockEsc(r.kind)}</span>${amend}</td>
      <td class="l t">${stockEsc(r.title)}</td></tr>`;
  }).join("");
}
function showNewsDetail(){
  $("#en-list").hidden = true;
  $("#en-detail").hidden = false;
  $("#earn-news").scrollIntoView({block:"start"});
}
function openNewsFiling(key){
  const f = newsFilings.find(x => stockNewsKey(x) === key);
  showNewsDetail();
  $("#en-deck").innerHTML = "";
  if(!f || !f.documents.length){
    $("#en-head").textContent = "書類が見つかりません。";
    $("#en-doc").innerHTML = "";
    $("#en-src").hidden = true;
    return;
  }
  $("#en-head").innerHTML = `<span class="sub">${stockEsc(stockAt(f.submitted_at))}</span> <span class="kind">${stockKind(f)}</span>`
    + `${f.is_amendment ? ' <span class="kind">訂正</span>' : ""} <b>${stockEsc(stockTitle(f))}</b>`;
  mountFilingFrames($("#en-doc"), $("#en-src"), f);
}
async function openNewsDeck(key){
  const code = newsCode, ac = newsAc;
  showNewsDetail();
  $("#en-doc").innerHTML = "";
  $("#en-deck").innerHTML = "";
  let d;
  try { d = await api(`/api/stock/${code}/decks/${key}`, {signal: ac.signal}); }
  catch(e){
    if(e.name === "AbortError" || code !== newsCode) return;
    $("#en-head").textContent = String(e.message) === "404" ? "説明資料が見つかりません。" : "読み込めませんでした。";
    return;
  }
  if(code !== newsCode || $("#en-detail").hidden) return;
  $("#en-deck").innerHTML = deckDocHtml(d, $("#en-deck").clientWidth);
}
$("#en-news").addEventListener("click", e => {
  const tr = e.target.closest("tr.go");
  if(!tr) return;
  if(tr.dataset.deck != null) openNewsDeck(tr.dataset.deck);
  else openNewsFiling(tr.dataset.id);
});
$("#en-back").addEventListener("click", e => {
  e.preventDefault();
  showNewsList();
  $("#earn-news").scrollIntoView({block:"nearest"});
});
let enDocWidth = 0;
new ResizeObserver(() => {
  const w = $("#en-doc").clientWidth;
  if(w === 0 || w === enDocWidth) return;
  enDocWidth = w;
  document.querySelectorAll("#en-doc iframe").forEach(fitStockFrame);
}).observe($("#en-doc"));

async function loadSched(row){
  const latest = row.latest;
  if(latest && latest.disclosure_no){
    loadDetail({
      code: row.code, name: row.name,
      disclosure_no: latest.disclosure_no,
      fy_label: latest.fy_label,
      period: latestPeriodLabel(latest.period),
    });
    return;
  }
  const signal = beginDetail();
  $("#e-chart-title").innerHTML = `<span class="clicky" onclick="gotoChart('${row.code}')">${row.name}</span>
    <small class="sub">${row.code.slice(0,4)}</small>`;
  loadNews(row.code, row.name);
  let ch;
  try { ch = await chartMaybe(row.code, signal); }
  catch(e){ if(e.name === "AbortError") return; throw e; }
  if(signal.aborted) return;
  drawEChart(ch, null);
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
  loadNews(row.code, row.name);
  let d, ch;
  try {
    [d, ch] = await Promise.all([
      apiMaybe(`/api/earnings/detail/${row.code}?disclosure_no=${encodeURIComponent(row.disclosure_no)}`, signal),
      chartMaybe(row.code, signal)]);
  } catch(e){ if(e.name === "AbortError") return; throw e; }
  if(signal.aborted) return;
  drawEChart(ch, d && d.disclosed_date);
  renderFin($("#cum-tbl"), d && d.cumulative, d);
  renderFin($("#q-tbl"),   d && d.quarterly,  d);
  renderProg($("#prog-tbl"), d && d.progress, d);
  renderRev($("#rev-tbl"), d && d.revisions,  d);
}

const FIN_MET = [["net_sales","売上高"],["gross_profit","粗利益"],["operating_profit","営業益"],
                 ["ordinary_profit","経常益"],["profit","最終益"]];
// 進捗率と修正履歴は予想で割る・予想を並べる表。短信の予想に売上総利益は無いので列を置かない
const FC_MET = FIN_MET.filter(([k]) => k !== "gross_profit");
function disc(iso){ if(!iso) return "–"; const [y,m,d]=iso.split("-"); return `${y.slice(2)}/${m}/${d}`; }
function margin(v, k){
  const s = v && v.net_sales, o = v && v[k];
  if(s == null || s <= 0 || o == null) return null;
  return o / s * 100;
}
function amt(v){
  if(v == null) return "–";
  return `<span class="${v<0?"loss":""}">${mil(v)}</span>`;
}
// 営業益と粗利益は売上に対する率を薄い括弧で添える
function rateAmt(r, k){
  const v = r.values && r.values[k];
  const m = margin(r.values, k);
  const rate = m==null ? "" : `<span class="opm${m<0?" loss":""}">(${fmt(m,1)}%)</span>`;
  return `${amt(v)}${rate}`;
}
const finAmt = (r, k) => k==="operating_profit" || k==="gross_profit" ? rateAmt(r, k) : amt(r.values[k]);
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
       + FIN_MET.map(([k])=>cell(k, finAmt(r, k))).join("")
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
        + FC_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
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
       + FC_MET.map(([k])=>cell(k)).join("")
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
  el.classList.add("rev");
  let h = `<thead><tr><th class="l">決算期</th><th class="disc">発表日</th>`
        + FC_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
        + `<th>EPS</th></tr></thead><tbody>`;
  // 年度のまとまり: 決算期は年度の最初の行だけに書く。地の色は実績の行だけ（KAB-279）
  rows.forEach((r,i) => {
    const [txt, bc, tip] = REV_BADGE[r.kind] || ["", "", ""];
    const now = d && r.disclosed_date === d.disclosed_date;
    const head = i === 0 || r.fiscal_year_end !== rows[i-1].fiscal_year_end;
    const cls = [now&&"now", r.kind==="actual_fy"&&"act", head&&i>0&&"sep"].filter(Boolean).join(" ");
    const cell = (k, txt) => {
      const g = r.growth && r.growth[k];
      return `<td class="num${dc(k)}">${txt}<span class="g">${g ? revG(g) : " "}</span></td>`;
    };
    const badge = `<span class="qb ${bc}">${txt}</span>`;
    // バッジはどの行も決算期の下の段（数値の修正率と同じ段）。決算期は年度の最初の行だけ
    const label = `${head ? r.range : "&nbsp;"}<span class="g">${badge}</span>`;
    h += `<tr class="${cls}"><td class="l" title="${r.label||""} ${tip}">${label}</td>`
       + `<td class="num disc sub">${disc(r.disclosed_date)}</td>`
       + FC_MET.map(([k])=>cell(k, finAmt(r, k))).join("")
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
  if(expKey) setExp(hasFin(selKey) ? selKey : null);
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
  if(hasFin(selKey)) keepRow(selKey, () => setExp(selKey));
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
window.toggleExp = toggleExp;
window.pickRow = pickRow;

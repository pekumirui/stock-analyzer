// APIは円で返す。決算表の金額表示だけ百万円に換算する
const mil = v => v==null ? "–" : fmt(v/1e6, 0);
// 増減率セル: 前期が赤字/ゼロだと比率に意味がないので状態バッジで出す
const GST = {turn_profit:["黒転","up"], turn_loss:["赤転","dn"], loss:["赤継",null]};
function gc(c){
  if(!c || c.state==="na") return "–";
  if(c.state==="up" || c.state==="down"){
    // 2倍以上は株たんと同じく倍数表記 (420% → 5.2倍)
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
async function renderCal(){
  const mm = `${calY}-${String(calM).padStart(2,"0")}`;
  $("#cal-title").textContent = `${calY}年${calM}月`;
  const counts = await api(`/api/earnings/calendar?month=${mm}`).catch(()=>({}));
  const first = new Date(calY, calM-1, 1), days = new Date(calY, calM, 0).getDate();
  const today = todayISO();
  let html = ["日","月","火","水","木","金","土"].map(d=>`<div class="h">${d}</div>`).join("");
  for(let i=0;i<first.getDay();i++) html += "<div></div>";
  for(let d=1;d<=days;d++){
    const ds = `${mm}-${String(d).padStart(2,"0")}`;
    const c = counts[ds];
    const cls = ["d"];
    if(ds===selDate) cls.push("sel");
    if(ds===today) cls.push("today");
    html += `<div class="${cls.join(" ")}" onclick="selectDate('${ds}')"><div class="n">${d}</div>${c?`<div class="cnt">${c}件</div>`:""}</div>`;
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
// 既出期間の再提出で損益が変わったもの (値がそのままの再提出はAPI側で除外済み)
const revBadge = r => r.restated ? `<span class="badge rev" title="既出期間の訂正">訂正</span>` : "";
const shortName = n => { const s = String(n||""); return s.length>5 ? s.slice(0,5) : s; };
// 列見出しが「期」なので FY 接頭辞は幅の無駄
const fyShort = s => String(s||"").replace(/^FY/, "");
let earnRows=[], selNo=null, detailReq=0;

async function selectDate(ds){
  selDate = ds; renderCal();
  calClose();
  $("#earn-title").textContent = `${ds}の決算発表`;
  earnRows = await api(`/api/earnings?date=${ds}`).catch(()=>[]);
  $("#earn-count").textContent = earnRows.length ? `${earnRows.length}件` : "";
  selNo = null;
  renderList();
  if(earnRows.length) selectRow(earnRows[0].disclosure_no);
  else clearDetail("開示なし");
}

function renderList(){
  if(!earnRows.length){ $("#earn-list").innerHTML = `<div class="sub">開示なし</div>`; return; }
  $("#earn-list").innerHTML = tableHtml();
  markSel();
}

function tableHtml(){
  // 2段ヘッダにして列幅を詰める (1段だと「売上QoQ」等で8列が横に収まらない)
  const sub = () => MET.map(([k,l],i) => `<th class="${i?'':'grp'}">${l}</th>`).join("");
  let h = `<table id="earn-tbl"><thead>`
        + `<tr><th rowspan="2">時刻</th><th rowspan="2" class="l name">銘柄</th>`
        + `<th rowspan="2" class="l fy">期</th><th rowspan="2">Q</th>`
        + `<th colspan="4" class="grp">QoQ (3か月)</th><th colspan="4" class="grp">YoY (累計)</th></tr>`
        + `<tr>${sub()}${sub()}</tr></thead><tbody>`;
  for(const r of earnRows){
    // 1QのQoQは前事業年度の4Q単独が相手になる (季節性が断絶するため注記する)
    const tip = r.period==="1Q" ? ' title="前期4Q単独比"' : "";
    h += `<tr data-no="${r.disclosure_no}" onclick="selectRow('${r.disclosure_no}')">`
       + `<td>${(r.disclosed_time||"").slice(0,5)}</td>`
       + `<td class="l name" title="${r.name}">${shortName(r.name)} <small class="sub">${r.code.slice(0,4)}</small>${revBadge(r)}</td>`
       + `<td class="l fy">${fyShort(r.fy_label)}</td><td>${r.period||""}</td>`
       + MET.map(([k],i)=>`<td class="num ${i?'':'grp'}"${tip}>${gc((r.qoq||{})[k])}</td>`).join("")
       + MET.map(([k],i)=>`<td class="num ${i?'':'grp'}">${gc((r.yoy||{})[k])}</td>`).join("")
       + `</tr>`;
  }
  return h + `</tbody></table>`;
}

function markSel(){
  document.querySelectorAll("#earn-list [data-no]").forEach(el =>
    el.classList.toggle("sel", el.dataset.no === selNo));
}

function selectRow(no){
  selNo = no; markSel();
  const row = earnRows.find(r => r.disclosure_no === no);
  if(!row) return;
  loadDetail(row);
}

function clearDetail(msg){
  $("#e-chart-title").textContent = msg;
  $("#e-chart").innerHTML = ""; $("#cum-tbl").innerHTML = ""; $("#q-tbl").innerHTML = "";
  $("#q-chips").innerHTML = "";
}

async function loadDetail(row){
  const my = ++detailReq;                      // 連続選択時に古いレスポンスで上書きしない
  $("#e-chart-title").innerHTML = `<span class="clicky" onclick="gotoChart('${row.code}')">${row.name}</span>
    <small class="sub">${row.code.slice(0,4)} / ${fyShort(row.fy_label)} ${row.period||""}</small>`;
  const [d, ch] = await Promise.all([
    api(`/api/earnings/detail/${row.code}?disclosure_no=${encodeURIComponent(row.disclosure_no)}`).catch(()=>null),
    api(`/api/chart/${row.code}?tf=D&ma=50,150,200`).catch(()=>null)]);
  if(my !== detailReq) return;
  if(ch) eChartObj = renderPriceChart($("#e-chart"), ch, {height:320, prev:eChartObj, mark:d && d.disclosed_date});
  else { $("#e-chart").innerHTML = `<div class="sub">株価データなし</div>`; }
  renderQchips(d && d.period);
  renderFin($("#cum-tbl"), d && d.cumulative, d);
  renderFin($("#q-tbl"),   d && d.quarterly,  d);
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
function epsTxt(v){
  if(v == null) return "–";
  return `<span class="${v<0?"loss":""}">${fmt(v,1)}</span>`;
}
function finLabel(r){
  const base = r.range || r.label;
  if(r.kind==="forecast_fy") return `${base} 通期予想`;
  if(r.kind==="forecast_h") return `${base} 中間予想`;
  if(r.kind==="actual_fy_prev") return `${base} 通期`;
  return base;
}
function qBadge(r){
  // 4Q も FY と同じ通期累計。バッジは Q単独行だけ (累計の通期に 4Q を付けない)
  let q = r.period || "";
  if(r.kind==="single" && q==="FY") q = "4Q";
  if(r.kind!=="single" && (q==="4Q" || q==="FY")) return "";
  if(q==="5Q") return `<span class="qb">5Q</span>`;
  return /^[1-4]Q$/.test(q) ? `<span class="qb qb-${q}">${q}</span>` : "";
}
function renderQchips(period){
  const cur = (period==="FY" || period==="4Q") ? "" : (period || "");
  $("#q-chips").innerHTML = ["1Q","2Q","3Q","4Q"]
    .map(q => `<span class="qchip${q===cur?" on":""}">${q}</span>`).join("");
}
function renderFin(el, rows, d){
  if(!rows || !rows.length){
    el.innerHTML = `<tr><td class="l sub">${d && d.notes && d.notes.single_unavailable ? "算出不能 (決算期変更など)" : "データなし"}</td></tr>`;
    return;
  }
  const dim = d && d.notes && d.notes.no_ordinary;          // IFRS/米国基準は経常利益が無い
  const dc = k => k==="ordinary_profit" && dim ? " dim" : "";
  let h = `<thead><tr><th class="l">決算期</th>`
        + FIN_MET.map(([k,l])=>`<th class="${dc(k).trim()}">${l}</th>`).join("")
        + `<th>EPS</th><th>営利率</th><th class="disc">発表日</th></tr></thead><tbody>`;
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
    const m = opm(r.values);
    const mTxt = m==null ? "–" : `<span class="${m<0?"loss":""}">${fmt(m,1)}%</span>`;
    h += `<tr class="${cls}"><td class="l" title="${r.label||""}">${finLabel(r)}${qBadge(r)}</td>`
       + FIN_MET.map(([k])=>cell(k, amt(r.values[k]))).join("")
       + cell("eps", epsTxt(r.values.eps))
       + `<td class="num">${mTxt}<span class="g"> </span></td>`
       + `<td class="num disc sub">${disc(r.disclosed_date)}</td></tr>`;
  });
  el.innerHTML = h + `</tbody>`;
}

document.querySelectorAll(".ftab").forEach(b => b.onclick = () => {
  document.querySelectorAll(".ftab").forEach(x => x.classList.toggle("on", x===b));
  const q = b.dataset.fin === "q";
  $("#q-wrap").hidden = !q;
  $("#cum-wrap").hidden = q;
});

$("#earn-list").addEventListener("keydown", e => {
  if(e.key!=="ArrowDown" && e.key!=="ArrowUp") return;
  e.preventDefault();
  const i = earnRows.findIndex(r => r.disclosure_no === selNo);
  const nxt = earnRows[Math.max(0, Math.min(earnRows.length-1, i + (e.key==="ArrowDown"?1:-1)))];
  if(!nxt) return;
  selectRow(nxt.disclosure_no);
  const el = document.querySelector(`#earn-list [data-no="${nxt.disclosure_no}"]`);
  if(el) el.scrollIntoView({block:"nearest"});
});

window.calMove = calMove;
window.selectDate = selectDate;
window.selectRow = selectRow;

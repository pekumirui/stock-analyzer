let monthlyRows=[], monthlyGroup="", selMonthlyCode=null;
let monthlySearchApplied="";
let monthlySeries="", monthlyYearTables=[];
const MONTHLY_YEAR_COLORS = ["#0077b6","#2e8b57","#b8860b","#8e4ec6"];
let MONTHLY_ROW_ORDER = {};
let _monthlyRowOrderReady = null;
const MONTHLY_SEGMENT_TAIL_BASE = 10000;
let monthlyIrCodes = null;
// 同じ節に金額と前年比があれば 1 行にまとめ、金額の下に前年比を出す。色は前年比。
let monthlyTlOpen = new Set();
// セルのポップアップで同じ月を並べる最初の暦年（それより前は取込済みと未取込が混ざる）。
const MONTHLY_POPUP_MIN_YEAR = 2024;
let monthlyAllYears = [];
let monthlyExtraTables = new Map();
let monthlyTlRecs = new Map();
let monthlyTlRevealed = "";
let monthlyTlTreeSeeded = false;
let _monthlyIrCodesReady = null;

function monthlyPublic(){
  return !!(document.body && document.body.dataset.publicMonthly === "1");
}

function monthlyCode4(code){
  return String(code || "").slice(0, 4);
}

// 設定 JSON は no-store で取る。iOS Safari はメモリに残った古い中身を確かめずに使う（ベルーナの内訳の親子が消えた）。
function loadMonthlyRowOrderConfig(){
  if(_monthlyRowOrderReady) return _monthlyRowOrderReady;
  _monthlyRowOrderReady = fetch("/static/monthly_row_order.json", {cache: "no-store"})
    .then(r => r.ok ? r.json() : {})
    .then(j => { MONTHLY_ROW_ORDER = (j && typeof j === "object") ? j : {}; return MONTHLY_ROW_ORDER; })
    .catch(() => { MONTHLY_ROW_ORDER = {}; return MONTHLY_ROW_ORDER; });
  return _monthlyRowOrderReady;
}

function loadMonthlyIrCodes(){
  if(monthlyPublic()) return Promise.resolve(new Set());
  if(_monthlyIrCodesReady) return _monthlyIrCodesReady;
  _monthlyIrCodesReady = fetch("/static/ir_monthly_codes.json", {cache: "no-store"})
    .then(r => r.ok ? r.json() : {codes:[]})
    .then(j => {
      const codes = new Set();
      for(const c of (j && j.codes) || []){
        const s = String(c || "").trim().toUpperCase();
        if(s) codes.add(s.slice(0, 4));
      }
      monthlyIrCodes = codes;
      return monthlyIrCodes;
    })
    .catch(() => { monthlyIrCodes = new Set(); return monthlyIrCodes; });
  return _monthlyIrCodesReady;
}

function issuerSegmentTail(code){
  if(!code) return [];
  const cfg = MONTHLY_ROW_ORDER[String(code)];
  const tail = cfg && Array.isArray(cfg.segment_tail) ? cfg.segment_tail : [];
  const out = [];
  for(const item of tail){
    const name = String(item || "").trim();
    if(name && !out.includes(name)) out.push(name);
  }
  return out;
}

function monthlyDisplayYears(years){
  const ys = [...new Set((years || []).map(Number).filter(Number.isFinite))].sort((a,b)=>b-a);
  if(!ys.length) return [];
  const latest = ys[0];
  return ys.filter(y => y >= latest - 2);
}

function monthlyYearColor(year){
  const n = MONTHLY_YEAR_COLORS.length;
  const i = ((Number(year) % n) + n) % n;
  return MONTHLY_YEAR_COLORS[i];
}

function uniqueMonthlyIssuers(rows){
  const seen = new Set();
  const out = [];
  for(const r of rows || []){
    const code = r && r.code;
    if(!code || seen.has(code)) continue;
    seen.add(code);
    out.push(r);
  }
  return out;
}

function monthlySearchFold(s){
  return String(s || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[\u3041-\u3096]/g, ch => String.fromCharCode(ch.charCodeAt(0) + 0x60));
}

function matchMonthlyIssuerQuery(issuer, q){
  const raw = monthlySearchFold(q).trim();
  if(!raw) return false;
  const name = monthlySearchFold(issuer && issuer.name);
  const code = monthlySearchFold(issuer && issuer.code).toUpperCase();
  const industry = monthlySearchFold((issuer && issuer.industry) || "その他");
  const sector = monthlySearchFold(issuer && issuer.sector33);
  const qCode = raw.toUpperCase();
  return name.includes(raw)
    || code.slice(0, 4).startsWith(qCode)
    || code.startsWith(qCode)
    || industry.includes(raw)
    || sector.includes(raw);
}

function monthlyIssuerSuggestHits(q){
  const raw = String(q || "").trim();
  if(!raw) return [];
  const hits = [];
  for(const r of uniqueMonthlyIssuers(monthlyRows)){
    if(!matchMonthlyIssuerQuery(r, raw)) continue;
    hits.push(r);
    if(hits.length >= 20) break;
  }
  return hits;
}

const MONTHLY_METRIC_RANK = {
  sales:1, customers:2, spend:3, open:4, close:5, adj:6, stores:7, count:8, other:9,
};
const MONTHLY_BREAKDOWN_RANK = {
  "":0, ドラッグ:1, 調剤:2, 物販:3,
  プロパティ:1, ホテル:2, インカム太陽光:3, 専門通販:4,
  化粧品健康食品:5, グルメ:6, ワイン:7, ナース関連:8,
  呉服関連:1, アパレル雑貨:2, その他:3,
  管理テナント数:1, 取引店舗数:2, 増加:3, 減少:4,
};
const MONTHLY_COUNTRY_RANK = {"":0, 日本:1, 海外:2};
const MONTHLY_CHANNEL_RANK = {"直営+FC":0, 直営:1, FC:2, "":3};

function monthlyRowScopeRank(label){
  if(label.scope === "既存") return 2;
  if(label.scope === "全社") return 1;
  if(label.scope === "全店") return 0;
  if(label.metric_kind === "stores" || label.metric_kind === "open"
      || label.metric_kind === "close" || label.metric_kind === "adj"
      || label.metric_kind === "count") return 3;
  if(!label.segment) return 0;
  if(!label.scope && (label.metric_kind === "sales"
      || label.metric_kind === "customers" || label.metric_kind === "spend")) return 0;
  return 3;
}

function monthlyRowUnitRank(unit){
  const u = unit || "number";
  return (u === "yoy_index" || u === "yoy_pct") ? 1 : 0;
}

function isAllStoreSalesYoy(label, unit){
  const u = unit || "number";
  return (u === "yoy_index" || u === "yoy_pct")
    && label.metric === "売上高"
    && (label.scope === "全店" || label.scope === "全社" || !label.scope)
    && !label.segment && !label.breakdown && !label.country;
}

function monthlyRowOrder(tables, code){
  let resolved = code || null;
  if(!resolved){
    for(const t of tables || []){
      if(t && t.code){ resolved = String(t.code); break; }
    }
  }
  const tail = issuerSegmentTail(resolved);
  const tailRank = {};
  tail.forEach((name, i) => { tailRank[name] = i; });
  const years = (tables || []).filter(Boolean).slice().sort((a,b)=>(b.year||0)-(a.year||0));
  const seen = new Set();
  const items = [];
  const segIndex = {};
  let segN = 0;
  for(const d of years){
    for(const e of d.elements || []){
      const L = e.label;
      if(seen.has(L.display)) continue;
      seen.add(L.display);
      if(L.group && !(L.group in tailRank) && !(L.group in segIndex)) segIndex[L.group] = segN++;
      items.push({
        element: L.display, unit: e.unit || "number", label: L,
        segment: L.group,
        country: MONTHLY_COUNTRY_RANK[L.country] ?? 3,
        scope: monthlyRowScopeRank(L),
        channel: MONTHLY_CHANNEL_RANK[L.channel] ?? 3,
        slot: MONTHLY_METRIC_RANK[L.metric_kind],
        unitRank: monthlyRowUnitRank(e.unit),
        bd: MONTHLY_BREAKDOWN_RANK[L.breakdown] ?? 4,
        idx: items.length,
      });
    }
  }
  function segKey(segment){
    if(!segment) return 0;
    if(segment in tailRank) return MONTHLY_SEGMENT_TAIL_BASE + tailRank[segment];
    return segment in segIndex ? segIndex[segment] : MONTHLY_SEGMENT_TAIL_BASE - 1;
  }
  items.sort((a,b)=>{
    const ta = a.segment ? 1 : 0, tb = b.segment ? 1 : 0;
    if(ta!==tb) return ta - tb;
    if(a.country!==b.country) return a.country - b.country;
    if(a.segment || b.segment){
      const s = segKey(a.segment) - segKey(b.segment);
      if(s) return s;
    }
    if(a.scope!==b.scope) return a.scope - b.scope;
    if(a.channel!==b.channel) return a.channel - b.channel;
    if(a.slot!==b.slot) return a.slot - b.slot;
    if(a.unitRank!==b.unitRank) return a.unitRank - b.unitRank;
    if(a.bd!==b.bd) return a.bd - b.bd;
    return a.idx - b.idx;
  });
  return items.map(e => ({element:e.element, unit:e.unit, label:e.label}));
}

function escHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
}

function monthlySeriesChoices(tables){
  return monthlyRowOrder(tables).map(e => ({
    id: e.label.display,
    label: e.label.display,
    yoy: e.unit==="yoy_index" || e.unit==="yoy_pct",
  }));
}

function pickDefaultMonthlySeries(tables){
  const rows = monthlyRowOrder(tables);
  for(const e of rows){
    if(!e.label.group && isAllStoreSalesYoy(e.label, e.unit)){
      return e.label.display;
    }
  }
  for(const e of rows){
    if(isAllStoreSalesYoy(e.label, e.unit)) return e.label.display;
  }
  for(const e of rows){
    if(e.label.series_kind === "all_store") return e.label.display;
  }
  return rows.length ? rows[0].label.display : "";
}

function monthlySeriesElement(d, seriesId){
  return (d.elements || []).find(e => e.label.display === seriesId);
}

function fmtMonthlyYoyList(v){
  if(v==null || v==="") return "-";
  const n = Number(v);
  if(!Number.isFinite(n)) return "-";
  return n.toLocaleString("ja-JP",{maximumFractionDigits:4});
}

function monthlyIndustries(issuers){
  const set = new Set();
  for(const r of issuers) set.add(r.industry || "その他");
  return [...set].sort((a,b) => a.localeCompare(b, "ja"));
}

function defaultMonthlyGroup(issuers){
  if(issuers.some(r => r.is_new)) return "__new__";
  const ind = monthlyIndustries(issuers);
  return ind[0] || "__new__";
}

function filteredMonthlyIssuers(){
  const issuers = uniqueMonthlyIssuers(monthlyRows);
  const q = String(monthlySearchApplied || "").trim();
  if(q) return issuers.filter(r => matchMonthlyIssuerQuery(r, q));
  if(monthlyGroup==="__all__") return issuers;
  if(monthlyGroup==="__new__") return issuers.filter(r => r.is_new);
  if(monthlyGroup==="__ir__"){
    const want = monthlyIrCodes || new Set();
    return issuers.filter(r => want.has(monthlyCode4(r.code).toUpperCase()));
  }
  return issuers.filter(r => (r.industry || "その他") === monthlyGroup);
}

async function loadMonthlyList(opts){
  if(!(opts && opts.keepView)) showMonthlyTop();
  const rows = await api("/api/v1/tables").catch(()=>null);
  if(!Array.isArray(rows)){
    $("#monthly-groups").innerHTML = "";
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    hideMonthlySuggest();
    return;
  }
  monthlyRows = rows;
  if(!monthlyPublic()) await loadMonthlyIrCodes();
  const issuers = uniqueMonthlyIssuers(monthlyRows);
  if(!monthlyGroup) monthlyGroup = monthlyPublic() ? "__all__" : "__new__";
  if(!issuers.length){
    $("#monthly-groups").innerHTML = "";
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    hideMonthlySuggest();
    return;
  }
  const groups = monthlyIndustries(issuers);
  if(monthlyGroup!=="__new__" && monthlyGroup!=="__all__" && monthlyGroup!=="__ir__" && !groups.includes(monthlyGroup)){
    monthlyGroup = monthlyPublic() ? "__all__" : defaultMonthlyGroup(issuers);
  }else if(monthlyGroup==="__new__" && !issuers.some(r => r.is_new) && groups.length){
    monthlyGroup = groups[0];
  }
  renderMonthlyGroups(groups);
  renderMonthlyList();
}

function renderMonthlyGroups(groups){
  let h = "";
  if(monthlyPublic()){
    h += `<button type="button" class="mchip${monthlyGroup==="__all__"?" on":""}" data-group="__all__" onclick="setMonthlyGroup('__all__')">すべて</button>`;
  }
  h += `<button type="button" class="mchip${monthlyGroup==="__new__"?" on":""}" data-group="__new__" onclick="setMonthlyGroup('__new__')">新着</button>`;
  if(!monthlyPublic()){
    h += `<button type="button" class="mchip${monthlyGroup==="__ir__"?" on":""}" data-group="__ir__" onclick="setMonthlyGroup('__ir__')">IR（一時）</button>`;
  }
  for(const g of groups){
    const on = monthlyGroup===g ? " on" : "";
    h += `<button type="button" class="mchip${on}" data-group="${g.replace(/"/g,"&quot;")}" onclick="setMonthlyGroup(this.dataset.group)">${g}</button>`;
  }
  $("#monthly-groups").innerHTML = h;
}

async function setMonthlyGroup(g){
  monthlyGroup = g;
  monthlySearchApplied = "";
  const inp = $("#monthly-sym");
  if(inp) inp.value = "";
  hideMonthlySuggest();
  renderMonthlyGroups(monthlyIndustries(uniqueMonthlyIssuers(monthlyRows)));
  if(g === "__ir__"){
    await loadMonthlyIrCodes();
    if(monthlyGroup !== "__ir__") return;
  }
  renderMonthlyList();
}

function fmtMonthlyIssuerLabel(code, name){
  const short = Array.from(String(name || "")).slice(0, 5).join("");
  return `${monthlyCode4(code)} ${short}`;
}

function fmtMonthlyMarket(market){
  const s = String(market || "");
  if(s.includes("プライム") || /prime/i.test(s)) return {cls:"mkt mkt-p", label:"東証P"};
  if(s.includes("スタンダード") || /standard/i.test(s)) return {cls:"mkt mkt-s", label:"東証S"};
  if(s.includes("グロース") || /growth/i.test(s)) return {cls:"mkt mkt-g", label:"東証G"};
  return {cls:"", label:s};
}

function fmtMonthlySector(sector){
  const s = String(sector || "").replace(/業$/, "");
  return Array.from(s).slice(0, 5).join("");
}

function renderMonthlyList(){
  const issuers = filteredMonthlyIssuers();
  if(!issuers.length){
    const empty = String(monthlySearchApplied || "").trim()
      ? "該当する発行体はありません"
      : (monthlyGroup === "__ir__" ? "IR対象はありません" : "登録された月次データはありません");
    $("#monthly-list").innerHTML = `<div class="sub">${empty}</div>`;
    return;
  }
  const pub = monthlyPublic();
  let h = `<table id="monthly-tbl"><thead><tr>` +
    `<th class="l">会社名</th>` +
    (pub ? "" : `<th class="l">市場</th><th class="l">業種</th>`) +
    `<th class="l">月次業種</th><th>全店(前年比)</th><th>既存店(前年比)</th>` +
    `</tr></thead><tbody>`;
  for(const r of issuers){
    const neu = r.ingested_today ? `<span class="badge new">NEW</span>` : "";
    const mkt = fmtMonthlyMarket(r.market);
    const label = escHtml(fmtMonthlyIssuerLabel(r.code, r.name));
    const nameCell = pub
      ? `<td class="l name"><a href="/monthly/${monthlyCode4(r.code)}">${label}</a>${neu}</td>`
      : `<td class="l name" onclick="showMonthlyDetail('${r.code}')">${label}${neu}</td>`;
    h += `<tr data-code="${r.code}">` + nameCell +
      (pub ? "" : `<td class="l ${mkt.cls}">${escHtml(mkt.label)}</td>` +
        `<td class="l sector">${escHtml(fmtMonthlySector(r.sector33))}</td>`) +
      `<td class="l">${r.industry||"その他"}</td>` +
      `<td class="num">${fmtMonthlyYoyList(r.all_store_yoy)}</td>` +
      `<td class="num">${fmtMonthlyYoyList(r.existing_store_yoy)}</td>` +
      `</tr>`;
  }
  $("#monthly-list").innerHTML = h + `</tbody></table>`;
}

function showMonthlyTop(){
  $("#monthly-top").style.display = "";
  $("#monthly-detail").style.display = "none";
}

function hideMonthlySuggest(){
  const box = $("#monthly-suggest");
  if(!box) return;
  box.style.display = "none";
  box.innerHTML = "";
}

function fillMonthlySuggest(hits){
  const box = $("#monthly-suggest");
  if(!box) return;
  if(!hits.length){
    hideMonthlySuggest();
    return;
  }
  box.innerHTML = hits.map(s => {
    const code = String(s.code || "");
    const name = String(s.name || "");
    return `<div onclick="pickMonthlyIssuer('${escHtml(code)}','${escHtml(name)}')">${escHtml(code.slice(0,4))} ${escHtml(name)}</div>`;
  }).join("");
  box.style.display = "block";
}

function applyMonthlyIssuerSearch(q){
  const applied = String(q || "").trim();
  monthlySearchApplied = applied;
  const inp = $("#monthly-sym");
  if(inp && inp.value.trim() !== applied) inp.value = applied;
  hideMonthlySuggest();
  renderMonthlyList();
}

function pickMonthlyIssuer(code, name){
  const inp = $("#monthly-sym");
  if(inp) inp.value = name;
  applyMonthlyIssuerSearch(name);
}

function refreshMonthlyIssuerSuggest(inp){
  const q = (inp && inp.value || "").trim();
  if(!q){ hideMonthlySuggest(); return; }
  fillMonthlySuggest(monthlyIssuerSuggestHits(q));
}

function bindMonthlyIssuerSearch(){
  const inp = $("#monthly-sym");
  if(!inp) return;
  inp.addEventListener("input", () => refreshMonthlyIssuerSuggest(inp));
  inp.addEventListener("compositionend", () => refreshMonthlyIssuerSuggest(inp));
  inp.addEventListener("keydown", e => {
    if(e.key === "Escape"){
      hideMonthlySuggest();
      return;
    }
    if(e.key === "Enter"){
      if(e.isComposing || e.keyCode === 229) return;
      applyMonthlyIssuerSearch(inp.value);
    }
  });
}

function findMonthlyIssuer(code){
  const raw = String(code || "");
  const c4 = monthlyCode4(raw);
  return uniqueMonthlyIssuers(monthlyRows).find(r => r.code === raw || monthlyCode4(r.code) === c4) || null;
}

function showMonthlyDetail(code, opts){
  hideMonthlySuggest();
  const skipHist = !!(opts && opts.skipHist);
  const row = findMonthlyIssuer(code);
  if(!row){
    if(monthlyPublic()){
      if(!skipHist && location.pathname !== "/monthly") history.pushState({}, "", "/monthly");
    }else if(typeof writeRoute === "function") writeRoute("monthly", "");
    return;
  }
  selMonthlyCode = row.code;
  const nm = row.name || "";
  const title = nm ? `${nm} ${monthlyCode4(row.code)}` : monthlyCode4(row.code);
  const el = $("#monthly-title");
  if(el) el.textContent = title;
  monthlySeries = "";
  monthlyTlOpen = new Set();
  monthlyTlRevealed = "";
  monthlyTlTreeSeeded = false;
  $("#monthly-top").style.display = "none";
  $("#monthly-detail").style.display = "";
  if(monthlyPublic()){
    const path = `/monthly/${monthlyCode4(row.code)}`;
    if(!skipHist && location.pathname !== path) history.pushState({code: row.code}, "", path);
    document.title = `${nm || monthlyCode4(row.code)}（${monthlyCode4(row.code)}）の月次売上 | 株KPI`;
  }else if(typeof writeRoute === "function") writeRoute("monthly", row.code);
  loadMonthlyTables();
}

function backMonthly(ev){
  if(ev) ev.preventDefault();
  showMonthlyTop();
  if(monthlyPublic()){
    if(location.pathname !== "/monthly") history.pushState({}, "", "/monthly");
    document.title = "月次売上 | 株KPI";
  }else if(typeof writeRoute === "function") writeRoute("monthly", "");
}

function renderMonthlySeriesSelect(){
  const choices = monthlySeriesChoices(monthlyYearTables);
  const sel = $("#monthly-series");
  if(!choices.length){
    sel.innerHTML = "";
    return;
  }
  if(!choices.some(s => s.id === monthlySeries)) monthlySeries = pickDefaultMonthlySeries(monthlyYearTables);
  sel.innerHTML = choices.map(s =>
    `<option value="${escHtml(s.id)}">${escHtml(s.label)}</option>`).join("");
  sel.value = monthlySeries;
}

function setMonthlySeries(id){
  monthlySeries = id;
  const sel = $("#monthly-series");
  if(sel && sel.value !== id) sel.value = id;
  if(monthlyTimelineReveal(id)){
    renderMonthlyYearTables(monthlyYearTables);
    return;
  }
  drawMonthlyChart(monthlyYearTables, monthlySeries);
  markMonthlySeriesRows();
}

function monthlyIsYoyUnit(unit){
  return unit === "yoy_index" || unit === "yoy_pct";
}

function monthlyYoyBase(id){
  return String(id || "").replace(/\(前年比\)$/, "");
}

// 同じ節で「X」と「X(前年比)」が両方あれば、金額行の位置と名前に前年比の element（yoy）を持たせて 1 行にする。表示だけ。
function monthlyPairYoyRows(rows){
  const yoyByKey = new Map();
  const numKeys = new Set();
  let sec = "";
  for(const r of rows){
    if(r.kind === "section"){ sec = r.label; continue; }
    const key = sec + "\u0000" + monthlyYoyBase(r.element);
    if(!monthlyIsYoyUnit(r.unit)) numKeys.add(sec + "\u0000" + r.element);
    else if(monthlyYoyBase(r.element) !== r.element) yoyByKey.set(key, r.element);
  }
  const out = [];
  sec = "";
  for(const r of rows){
    if(r.kind === "section"){ sec = r.label; out.push(r); continue; }
    const key = sec + "\u0000" + monthlyYoyBase(r.element);
    if(monthlyIsYoyUnit(r.unit)){
      if(!(numKeys.has(key) && yoyByKey.get(key) === r.element)) out.push(r);
      continue;
    }
    const yoy = numKeys.has(key) ? yoyByKey.get(key) : "";
    out.push(yoy ? {...r, yoy} : r);
  }
  return out;
}

function monthlyTlShown(rec){
  return rec.element;
}

function monthlyTlAlt(rec){
  return rec.yoy || "";
}

// txt はセルの中身（HTML）、len は幅の根拠にする最長行の文字数。数字を出さないセルは塗らない。
function monthlyTlCell(byName, rec, m){
  const id = monthlyTlShown(rec);
  const src = byName[id];
  const unit = (src && src.unit) || rec.unit || "number";
  const v = src ? (src.values || {})[String(m)] : null;
  let txt = (v == null || v === "") ? "" : fmtMonthlyCell(v, unit, id);
  let len = txt.length;
  const heatId = rec.yoy || (monthlyIsYoyUnit(unit) ? id : "");
  const hs = heatId ? byName[heatId] : null;
  const hv = hs ? (hs.values || {})[String(m)] : null;
  if(rec.yoy && hv != null && hv !== ""){
    const sub = `(${fmtMonthlyCell(hv, "yoy_index", rec.yoy)})`;
    len = Math.max(len, sub.length);
    txt = `${txt}<span class="tl-sub">${sub}</span>`;
  }
  const st = txt ? monthlyYoyHeat(hv) : "";
  return {txt, len, st};
}

function monthlyTlRowAttrs(rec){
  const alt = monthlyTlAlt(rec);
  return ` data-series="${escHtml(monthlyTlShown(rec))}"${alt ? ` data-alt="${escHtml(alt)}"` : ""} onclick="setMonthlySeries(this.dataset.series)"`;
}

// 指標 ▸ 節の木。分析は「指標を 1 つ決め、その中を組織で降りる」（既存 売上高 → 焼肉 / ラーメン …、総額売上高 → 百貨店事業 → 各店）。
// 根が 1 つならその行が指標の行、根の子からが節。指標の行は既定で閉じ、先頭の指標（既存店売上高）だけ開く。
// API の tree を表示する年で 1 本にする。木は全発行体で返る（config の tree、無ければ API が作る自動の木 tree_auto）。
// 節の形は年で同じなので新しい年の tree を使い、各節の行は年の和集合。値の無い行は落とす。
// 各行の mkey は指標（tree.metric、無ければ葉）。横持ちは指標を一番上の行にし、その下に節の木を出す。
// tree の無い応答（古いキャッシュ）は全行を自動の根 1 つに入れる。
function monthlyTreeModel(tables){
  const ts = (tables || []).filter(Boolean).slice().sort((a, b) => Number(b.year) - Number(a.year));
  if(!ts.length) return null;
  const src = ts.find(d => Array.isArray(d.tree) && d.tree.length)
    || {tree: [{name: "全体", elements: ts.flatMap(d => (d.elements || []).map(e => e.element)), children: []}], tree_metrics: [], tree_auto: true};
  const hasValues = monthlyTimelineHasValues(ts);
  const byPath = new Map();
  const seen = new Set();
  const loose = [];
  for(const d of ts){
    for(const e of d.elements || []){
      const id = e.label.display;
      if(seen.has(id)) continue;
      seen.add(id);
      if(!Object.keys(e.values || {}).length) continue;
      const t = e.tree || (src.tree_auto && !d.tree ? {path: [src.tree[0].name], leaf: id, metric: null} : null);
      const leaf = t ? t.leaf : id;
      // config の木で指標に振り分けていない行は先頭の指標に入れる（パルコ テナント 既存 取扱高 は 総額売上高 の パルコ の 2 行目）
      const mkey = (t && t.metric) || (!src.tree_auto && (src.tree_metrics || [])[0]) || leaf;
      const rec = {element: id, unit: e.unit, label: e.label, leaf, mkey, saved: e.element, order: t ? t.order : undefined};
      if(!t){ loose.push(rec); continue; }
      const key = t.path.join("\u203a");
      if(!byPath.has(key)) byPath.set(key, []);
      byPath.get(key).push(rec);
    }
  }
  const all = [];
  const build = (nodes, path) => (nodes || []).map(n => {
    const p = [...path, n.name];
    // 節のキーは名前を › でつなぐ（data-key 属性に載るので NUL は使えない）
    const key = p.join("\u203a");
    const pool = byPath.get(key) || [];
    // 古い年にしか無い行は、その年の並びで直前にある行のすぐ後ろへ差し込む（末尾に回さない）。
    const order = [];
    for(const d of ts){
      const node = monthlyTreeFind(d.tree, p);
      let at = -1;
      for(const el of (node && node.elements) || []){
        const i = order.indexOf(el);
        if(i >= 0) at = i;
        else order.splice(++at, 0, el);
      }
    }
    // 自動の木は API の order（3 年を通した並び）で並べる。
    pool.sort((a, b) => (a.order != null && b.order != null) ? a.order - b.order : order.indexOf(a.saved) - order.indexOf(b.saved));
    const recs = n.heading ? [] : monthlyPairYoyRows(pool.filter(r => hasValues(r.element)));
    all.push(...recs);
    return {name: n.name, heading: !!n.heading, part: !!n.part, key: "tree:" + key, depth: path.length, recs, children: build(n.children, p)};
  });
  const roots = build(src.tree, []);
  const rest = monthlyPairYoyRows(loose.filter(r => hasValues(r.element)));
  all.push(...rest);
  // 指標の並びは tree_metrics（既存 → 全店 → 店舗 …）、載っていない指標は出てきた順で後ろへ
  const present = new Set(all.filter(r => !rest.includes(r)).map(r => r.mkey));
  const keys = (src.tree_metrics || []).filter(m => present.has(m));
  for(const r of all) if(present.has(r.mkey) && !keys.includes(r.mkey)) keys.push(r.mkey);
  // 指標どうしの親子（直営 全店 店舗数 → 国内 直営+FC 全店 店舗数 → 合計店舗数）。親も子も出ている指標だけ
  const parent = {};
  for(const [c, p] of Object.entries(src.tree_metric_parent || {})) if(keys.includes(c) && keys.includes(p)) parent[c] = p;
  return {roots, rest, all, keys, parent, auto: !!src.tree_auto};
}

function monthlyTreeFind(nodes, path){
  let cur = null, list = nodes || [];
  for(const name of path){
    cur = list.find(n => n.name === name);
    if(!cur) return null;
    list = cur.children || [];
  }
  return cur;
}

function monthlyTreeTop(tm){
  const single = tm.roots.length === 1 ? tm.roots[0] : null;
  return {single, top: single ? single.children : tm.roots};
}

function monthlyTreeHas(n, k){
  return n.recs.some(r => r.mkey === k) || n.children.some(c => monthlyTreeHas(c, k));
}

function monthlyTreeCount(nodes, k){
  return nodes.reduce((a, n) => a + n.recs.filter(r => r.mkey === k).length + monthlyTreeCount(n.children, k), 0);
}

// 指標 k の最初の行とその節（{n, r}）
function monthlyTreeFirst(nodes, k){
  for(const n of nodes){
    const r = n.recs.find(q => q.mkey === k);
    if(r) return {n, r};
    const hit = monthlyTreeFirst(n.children, k);
    if(hit) return hit;
  }
  return null;
}

// 節の行の下に並べる同じ指標の 2 行目から。葉が保存名・表示名のまま（節で剥がしていない）なら表示名、剥がしたら「節名 葉」。
function monthlyTreeOtherName(n, r){
  return (r.leaf === r.saved || r.leaf === r.element) ? r.element : `${n.name} ${r.leaf || r.element}`;
}

function monthlyTreeSeedOpen(tm){
  if(monthlyTlTreeSeeded) return;
  monthlyTlTreeSeeded = true;
  const first = tm.keys.find(k => !tm.parent[k]);
  if(first) monthlyTlOpen.add("m:" + first);
  // 会社の行が無い指標（トライアル: ブランド別だけ）は閉じると数字の無い行だけになるので開いておく
  const {single} = monthlyTreeTop(tm);
  for(const k of tm.keys){
    if(!tm.parent[k] && !(single && single.recs.some(r => r.mkey === k))) monthlyTlOpen.add("m:" + k);
  }
}

function monthlyTreeOpenMetric(tm, k, open){
  for(let m = k; m; m = tm.parent[m]) open("m:" + m);
}

// 選んだ系列が閉じた指標や節の中にあれば、指標の行からその節の親までを開く。
function monthlyTreeReveal(tm, id){
  const hit = r => r.element === id || r.yoy === id;
  let changed = false;
  const open = key => {
    if(monthlyTlOpen.has(key)) return;
    monthlyTlOpen.add(key);
    changed = true;
  };
  const {single, top} = monthlyTreeTop(tm);
  const own = single && single.recs.find(hit);
  if(own){
    // 会社の行は親の指標の行を開けば見える
    if(tm.parent[own.mkey]) monthlyTreeOpenMetric(tm, tm.parent[own.mkey], open);
    return changed;
  }
  const walk = (nodes, trail, topNode) => {
    for(const n of nodes){
      const r = n.recs.find(hit);
      if(r){
        // 節の外に 1 行しか無い指標は指標の行としてそのまま出ている
        if(!(single && single.recs.some(q => q.mkey === r.mkey)) && monthlyTreeCount(top, r.mkey) === 1 && !tm.parent[r.mkey]) return true;
        // 節の行は一番浅い指標の下。兄弟枝（直営 vs FC）にだけ行があるときはその枝
        const root = monthlyTreeDisplayMetric(tm, topNode || n, r.mkey);
        monthlyTreeOpenMetric(tm, root, open);
        trail.forEach(k => open("m:" + root + "\u203a" + k));
        if(r.mkey !== root){
          const base = "m:" + root + "\u203a" + n.key;
          open(base);
          const chain = [];
          for(let m = tm.parent[r.mkey]; m && m !== root; m = tm.parent[m]) chain.unshift(m);
          let key = base;
          for(const m of chain){
            if(!n.recs.some(q => q.mkey === m)) continue;
            key += "\u203a" + m;
            open(key);
          }
        }
        return true;
      }
      if(walk(n.children, [...trail, n.key], topNode || n)) return true;
    }
    return false;
  };
  if(!walk(top, []) && tm.rest.some(hit)) open("__more__");
  return changed;
}

// 指標の行（会社全体の数字）と、その下の子の指標と節の木。節の行にはその指標の数字を出す（無ければ空欄）。
// 会社の行が無い指標は数字の無い見出し。節の外にも 1 行しか無い指標（グループ 出店 など）は木にせずその行を出す。
// 見出し節（大丸・松坂屋）は数字の無いグレー帯。内数の節（うち商品売上高）は tl-part で薄く出す。
function monthlyTreeHtml(tm, cellsFor, cols){
  let body = "";
  // 木は 7 段まで下がる。スマホの名前列（12em）では 1 段 1.25em だと店名が 1 文字ずつ折り返す。
  const step = monthlyIsPhone() ? 0.6 : 1.25;
  const blank = cols.map(() => `<td class="num"></td>`).join("");
  const row = (rec, text, depth, tog, rowCls) => monthlyTlRemember(rec, text)
    + `<tr${rowCls ? ` class="${rowCls}"` : ""}${monthlyTlRowAttrs(rec)}>${monthlyTlNameCell({text, title: rec.saved, depth, tree: true, step, ...(tog || {})})}${cellsFor(rec)}</tr>`;
  const empty = (text, depth, tog, rowCls) =>
    `<tr${rowCls ? ` class="${rowCls}"` : ""}>${monthlyTlNameCell({text, title: text, depth, tree: true, step, ...(tog || {})})}${blank}</tr>`;
  const {single, top} = monthlyTreeTop(tm);
  // 節の行の下の、子の指標の行（焼肉 ▸ 直営 / FC）。節にその指標の行が無ければ孫の指標を繰り上げる。
  const chans = (n, k) => {
    const out = [];
    for(const c of monthlyTreeSubs(tm, k)){
      const rec = n.recs.find(r => r.mkey === c);
      if(rec) out.push({c, rec, kids: chans(n, c)});
      else out.push(...chans(n, c));
    }
    return out;
  };
  const chanRows = (n, list, pk, depth, base, cls) => {
    for(const e of list){
      const key = base + "\u203a" + e.c;
      const open = e.kids.length > 0 && monthlyTlOpen.has(key);
      body += row(e.rec, monthlyTreeShortName(e.c, pk), depth, e.kids.length ? {key, open} : null, cls);
      if(open) chanRows(n, e.kids, e.c, depth + 1, key, cls);
    }
  };
  const walk = (n, k, depth) => {
    const mine = n.recs.filter(r => r.mkey === k);
    const main = mine[0] || null;
    const kids = n.children.filter(c => monthlyTreeHasDeep(tm, c, k));
    const ch = n.heading ? [] : chans(n, k);
    const key = "m:" + k + "\u203a" + n.key;
    const has = kids.length > 0 || ch.length > 0;
    const open = has && monthlyTlOpen.has(key);
    const tog = has ? {key, open} : {};
    const cls = n.part ? "tl-part" : "";
    if(n.heading) body += `<tr class="tl-sec">${monthlyTlNameCell({text: n.name, title: n.name, depth, tree: true, step, ...tog})}<td class="tl-sec-fill" colspan="${cols.length}"></td></tr>`;
    else if(main) body += row(main, n.name, depth, tog, cls);
    else body += empty(n.name, depth, tog, cls);
    for(const r of mine.slice(1)) body += row(r, monthlyTreeOtherName(n, r), depth, null, cls);
    if(!open) return;
    chanRows(n, ch, k, depth + 1, key, cls);
    for(const c of kids) walk(c, k, depth + 1);
  };
  // 指標の行。子は先に会社の子の指標（直営 / FC、国内 / 海外）、次に節の木。
  // 節（ブランド）はその節の行がある一番浅い指標の下（物語の合計店舗数は国内 816 の下）。
  // 一番浅い指標が兄弟枝の一方（直営）だけで、もう一方（FC）にも行があるときは両方に出す。
  const homes = new Map();
  const home = n => {
    if(!homes.has(n)) homes.set(n, new Map());
    const per = homes.get(n);
    return k => {
      const root = monthlyTreeRootMetric(tm, k);
      if(!per.has(root)) per.set(root, monthlyTreeHome(tm, n, root));
      return per.get(root);
    };
  };
  const content = c => (single && single.recs.some(r => r.mkey === c))
    || top.some(n => monthlyTreePlaceUnder(tm, n, c, home(n)(c))) || monthlyTreeSubs(tm, c).some(content);
  const metric = (k, depth, pk) => {
    const mine = single ? single.recs.filter(r => r.mkey === k) : [];
    const kids = top.filter(n => monthlyTreePlaceUnder(tm, n, k, home(n)(k)));
    const ms = monthlyTreeSubs(tm, k).filter(content);
    if(!pk && !mine.length && !ms.length && !monthlyTreeSubs(tm, k).length && monthlyTreeCount(top, k) === 1){
      // config の木は「節名 葉」（直営 既存 売上高）。保存名の頭の事業名（キーパーLABO運営事業）を 1 段目に出すと、
      // 同じ事業が他の指標では 2 段目の節に出ていて深さが食い違う。自動の木は保存名のまま
      const {n, r} = monthlyTreeFirst(top, k);
      body += row(r, tm.auto ? r.element : monthlyTreeOtherName(n, r), depth);
      return;
    }
    const key = "m:" + k;
    const has = kids.length > 0 || ms.length > 0;
    const open = has && monthlyTlOpen.has(key);
    const tog = has ? {key, open} : {};
    // 会社の行があればその行の名前（指標名 売上高 のままだと全社の数字と読めない）。自動の木は 日本+海外 を外す（直営+FC 全店 店舗数）。
    // config の木は節で剥がした葉から (前年比) を外す（全社 売上高）。金額の無い前年比だけの行は外さない（神戸物産 全国 仕入れ）。
    // 会社の行が無ければ指標名。子の指標は親との差だけ（直営 / 海外）
    const leaf0 = mine.length ? (mine[0].leaf || mine[0].element) : "";
    const text = pk ? monthlyTreeShortName(k, pk)
      : !mine.length ? k : tm.auto ? monthlyTreeTopName(mine[0].element)
      : monthlyIsYoyUnit(mine[0].unit) ? leaf0 : monthlyYoyBase(leaf0);
    if(mine.length) body += row(mine[0], text, depth, tog);
    else body += empty(text, depth, tog);
    for(const r of mine.slice(1)) body += row(r, r.element, depth);
    if(!open) return;
    for(const c of ms) metric(c, depth + 1, k);
    for(const n of kids) walk(n, k, depth + 1);
  };
  for(const k of tm.keys) if(!tm.parent[k]) metric(k, 0, "");
  if(tm.rest.length){
    const open = monthlyTlOpen.has("__more__");
    body += `<tr class="tl-sec">${monthlyTlNameCell({text: "その他の項目", key: "__more__", open, tree: true, count: tm.rest.length, step})}<td class="tl-sec-fill" colspan="${cols.length}"></td></tr>`;
    if(open) for(const r of tm.rest) body += row(r, r.element, 1);
  }
  return body;
}

function monthlyTreeSubs(tm, k){
  return tm.keys.filter(c => tm.parent[c] === k);
}

// 節にその指標か子孫の指標の行があるか（直営の店舗数しか無いブランドも合計の下に出す）
function monthlyTreeHasDeep(tm, n, k){
  return monthlyTreeHas(n, k) || monthlyTreeSubs(tm, k).some(c => monthlyTreeHasDeep(tm, n, c));
}

// 節を出す指標。根の指標から幅優先で、節（子の節を含む）に行がある最初の指標
function monthlyTreeHome(tm, n, root){
  const queue = [root];
  while(queue.length){
    const m = queue.shift();
    if(monthlyTreeHas(n, m)) return m;
    queue.push(...monthlyTreeSubs(tm, m));
  }
  return null;
}

function monthlyTreeIsAncestor(tm, anc, k){
  for(let m = tm.parent[k]; m; m = tm.parent[m]) if(m === anc) return true;
  return false;
}

// 節 n を指標 k の下に出すか。home が k なら出す。home が k の祖先なら出さない
//（焼肉は 直営+FC の下に 1 回、直営の下には並べ直さない）。home が兄弟枝
//（直営 vs FC）なら、k の行があるときだけ出す（ハードオフの業態別 FC）。
function monthlyTreePlaceUnder(tm, n, k, homeK){
  if(homeK === k) return true;
  if(!n.recs.some(r => r.mkey === k)) return false;
  return !homeK || !monthlyTreeIsAncestor(tm, homeK, k);
}

// 選んだ系列を出す指標。親の指標の行がある節は親、兄弟枝にだけ行があるときはその枝
function monthlyTreeDisplayMetric(tm, n, k){
  const homeM = monthlyTreeHome(tm, n, monthlyTreeRootMetric(tm, k)) || k;
  if(homeM === k || monthlyTreeIsAncestor(tm, homeM, k)) return homeM;
  return k;
}

function monthlyTreeRootMetric(tm, k){
  let m = k;
  while(tm.parent[m]) m = tm.parent[m];
  return m;
}

// 子の指標の名前は親との差だけ（直営 全店 店舗数 / 直営+FC 全店 店舗数 → 直営）。差が無ければそのまま
function monthlyTreeShortName(k, pk){
  const p = new Set(String(pk || "").split(" "));
  const d = String(k).split(" ").filter(t => !p.has(t));
  return d.length ? d.join(" ") : k;
}

// 一番上の行の名前。全地域を表す 日本+海外 は外す（子の行が 日本 / 海外 を出す）。直営+FC は残す（直営の行と見分ける）
function monthlyTreeTopName(name){
  const t = String(name).split(" ").filter(x => x !== "日本+海外");
  return t.length ? t.join(" ") : name;
}

// 名前セル。子を持つ行と合計つきの節は ▸/▾ で開閉する（行のクリックは系列の選択なので親へ伝えない）。
function monthlyTlNameCell(o){
  const pad = o.depth ? ` style="padding-left:${(0.5 + o.depth * (o.step || 1.25)).toFixed(2)}em"` : "";
  const tw = o.key ? `<span class="tl-tw">${o.open ? "▾" : "▸"}</span>` : (o.tree ? `<span class="tl-tw"></span>` : "");
  const act = o.key ? ` data-key="${escHtml(o.key)}" onclick="event.stopPropagation();toggleMonthlyTimelineSection(this.dataset.key)"` : "";
  const span = (o.rowspan ? ` rowspan="${o.rowspan}"` : "") + (o.colspan ? ` colspan="${o.colspan}"` : "");
  const cnt = o.count ? `<span class="tl-cnt">${o.count}行</span>` : "";
  return `<td class="tl-name${o.cls || ""}${o.key ? " tl-tog" : ""}"${span}${pad}${act} title="${escHtml(o.title || "")}">${tw}${escHtml(o.text)}${cnt}</td>`;
}

function monthlyTimelineHasValues(tables){
  const ids = new Set();
  for(const d of tables || []){
    for(const e of (d && d.elements) || []){
      if(Object.keys(e.values || {}).length) ids.add(e.label.display);
    }
  }
  return id => ids.has(id);
}

function monthlyTimelineReveal(id){
  if(!id || id === monthlyTlRevealed) return false;
  monthlyTlRevealed = id;
  const tm = monthlyTreeModel(monthlyYearTables);
  if(!tm) return false;
  monthlyTreeSeedOpen(tm);
  return monthlyTreeReveal(tm, id);
}

function toggleMonthlyTimelineSection(key){
  if(monthlyTlOpen.has(key)) monthlyTlOpen.delete(key);
  else monthlyTlOpen.add(key);
  const wrap = $("#monthly-table-wrap");
  const left = wrap ? wrap.scrollLeft : 0;
  renderMonthlyYearTables(monthlyYearTables, {keepScroll: true});
  if(wrap) wrap.scrollLeft = left;
}

// 前年比は 100 を境に色を付ける（上げは --up、下げは --down の薄塗り。±20 で最も濃い）。
function monthlyYoyHeat(v){
  const n = Number(v);
  if(v == null || v === "" || !Number.isFinite(n) || n === 100) return "";
  const a = (Math.min(Math.abs(n - 100), 20) / 20 * 0.32 + 0.04).toFixed(3);
  return n > 100 ? `background:rgba(211,47,47,${a})` : `background:rgba(27,138,90,${a})`;
}

function renderMonthlyTimeline(tables, opts){
  const wrap = $("#monthly-table-wrap");
  const byYear = monthlyTablesByYear(tables);
  const years = [...byYear.keys()].sort((a, b) => a - b);
  const tm = monthlyTreeModel(tables);
  if(!tm){
    wrap.innerHTML = `<div class="sub">データなし</div>`;
    return;
  }
  monthlyTreeSeedOpen(tm);
  const all = tm.all;
  let first = null, last = null;
  for(const y of years){
    for(let m = 1; m <= 12; m++){
      const any = all.some(r => {
        const src = byYear.get(y)[monthlyTlShown(r)];
        const v = src && (src.values || {})[String(m)];
        return v != null && v !== "";
      });
      if(!any) continue;
      if(!first) first = {y, m};
      last = {y, m};
    }
  }
  if(!first){
    wrap.innerHTML = `<div class="sub">データなし</div>`;
    return;
  }
  const cols = [];
  for(const y of years){
    for(let m = 1; m <= 12; m++){
      if(y === first.y && m < first.m) continue;
      if(y === last.y && m > last.m) continue;
      cols.push({y, m});
    }
  }
  let maxNumLen = 0;
  // 見出しは 1 段。各列に暦年を小さく薄く載せ、横にスクロールしてもどの年かが分かるようにする。
  let head = `<th class="tl-corner"></th>`;
  for(const c of cols){
    const yb = c === cols[0] || c.m === 1 ? ` class="tl-yb"` : "";
    head += `<th${yb}><span class="tl-hy">${c.y}年</span>${c.m}月</th>`;
  }
  const cellsFor = rec => {
    let cells = "";
    for(const c of cols){
      const {txt, len, st} = monthlyTlCell(byYear.get(c.y), rec, c.m);
      if(len) maxNumLen = Math.max(maxNumLen, len);
      const cls = c === cols[0] || c.m === 1 ? "num tl-yb" : "num";
      cells += `<td class="${cls}" data-y="${c.y}" data-m="${c.m}"${st ? ` style="${st}"` : ""}>${txt}</td>`;
    }
    return cells;
  };
  monthlyTlRecs = new Map();
  const body = monthlyTreeHtml(tm, cellsFor, cols);
  wrap.style.setProperty("--monthly-m-w", `calc(${Math.max(maxNumLen, 3)}ch + 14px)`);
  wrap.innerHTML = `<table class="monthly-tl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  if(!(opts && opts.keepScroll)){
    wrap.scrollLeft = wrap.scrollWidth;
    requestAnimationFrame(() => { wrap.scrollLeft = wrap.scrollWidth; });
  }
}

function monthlyTablesByYear(tables){
  const byYear = new Map();
  for(const d of tables){
    if(!d) continue;
    const byName = {};
    for(const src of d.elements || []) byName[src.label.display] = src;
    byYear.set(Number(d.year), byName);
  }
  return byYear;
}

function monthlyTlRemember(rec, name){
  monthlyTlRecs.set(monthlyTlShown(rec), {rec, name});
  return "";
}

function closeMonthlyPopup(){
  const pop = $("#monthly-pop");
  if(pop) pop.remove();
}

// 押したセルの月を、MONTHLY_POPUP_MIN_YEAR 以降の各暦年で並べる。読み込んでいない年はここで取りに行く。
async function openMonthlyPopup(td){
  const tr = td.closest("tr[data-series]");
  const hit = tr && monthlyTlRecs.get(tr.dataset.series);
  if(!hit) return;
  const m = Number(td.dataset.m);
  const code = selMonthlyCode;
  const years = monthlyAllYears.filter(y => y >= MONTHLY_POPUP_MIN_YEAR).sort((a, b) => b - a);
  const loaded = new Map(monthlyYearTables.map(d => [Number(d.year), d]));
  const missing = years.filter(y => !loaded.has(y) && !monthlyExtraTables.has(y));
  closeMonthlyPopup();
  const pop = document.createElement("div");
  pop.id = "monthly-pop";
  pop.className = "monthly-pop";
  document.body.appendChild(pop);
  const draw = () => {
    const {rec, name} = hit;
    const paired = !!rec.yoy;
    let rows = "";
    for(const y of years){
      const d = loaded.get(y) || monthlyExtraTables.get(y);
      const byName = {};
      for(const e of (d && d.elements) || []) byName[e.label.display] = e;
      const val = id => {
        const e = id && byName[id];
        const v = e ? (e.values || {})[String(m)] : null;
        return (v == null || v === "") ? null : {v, unit: e.unit};
      };
      const a = val(rec.element), b = paired ? val(rec.yoy) : null;
      const yoyV = paired ? b : (a && monthlyIsYoyUnit(a.unit) ? a : null);
      const heat = yoyV ? monthlyYoyHeat(yoyV.v) : "";
      const fa = a ? fmtMonthlyCell(a.v, a.unit, rec.element) : (d ? "-" : "…");
      const cells = paired
        ? `<td class="num">${fa}</td><td class="num"${heat ? ` style="${heat}"` : ""}>${b ? fmtMonthlyCell(b.v, "yoy_index", rec.yoy) : (d ? "-" : "…")}</td>`
        : `<td class="num"${a && heat ? ` style="${heat}"` : ""}>${fa}</td>`;
      rows += `<tr><td class="yr"><span class="sw" style="background:${monthlyYearColor(y)}"></span>${y}</td>${cells}</tr>`;
    }
    const head = paired ? `<tr><th></th><th>金額</th><th>前年比</th></tr>` : "";
    pop.innerHTML = `<div class="mpop-head"><span class="mpop-title">${escHtml(name)}</span><span class="mpop-m">${m}月</span>`
      + `<button type="button" class="mpop-x" aria-label="閉じる" onclick="closeMonthlyPopup()">×</button></div>`
      + `<table>${head}${rows}</table>`;
    // PC もスマホも押したセルの近くに出す。iOS は表が横にはみ出すと layout viewport が見える幅より
    // 広くなるので、見えている範囲（visualViewport）に収める。
    const vv = window.visualViewport;
    const vx = vv ? vv.offsetLeft : 0, vy = vv ? vv.offsetTop : 0;
    const vw = vv ? vv.width : innerWidth, vh = vv ? vv.height : innerHeight;
    const r = td.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.min(Math.max(vx + 8, r.left + r.width / 2 - w / 2), vx + vw - w - 8);
    const below = r.bottom + 6 + h < vy + vh;
    pop.style.left = `${left + scrollX}px`;
    pop.style.top = `${(below ? r.bottom + 6 : r.top - h - 6) + scrollY}px`;
  };
  draw();
  if(!missing.length) return;
  await Promise.all(missing.map(y => api(`/api/v1/tables/${code}/${y}`)
    .then(d => { if(d) monthlyExtraTables.set(y, d); }).catch(() => {})));
  if(selMonthlyCode === code && $("#monthly-pop") === pop) draw();
}

function renderMonthlyYearTables(tables, opts){
  const wrap = $("#monthly-table-wrap");
  if(!wrap) return;
  renderMonthlySeriesSelect();
  monthlyTimelineReveal(monthlySeries);
  renderMonthlyTimeline(tables, opts);
  drawMonthlyChart(tables, monthlySeries);
  markMonthlySeriesRows();
}

function markMonthlySeriesRows(){
  document.querySelectorAll("#monthly-table-wrap tr[data-series]").forEach(tr =>
    tr.classList.toggle("on", tr.dataset.series === monthlySeries || tr.dataset.alt === monthlySeries));
}

async function loadMonthlyTables(){
  if(!selMonthlyCode) return;
  const wrap = $("#monthly-table-wrap");
  await loadMonthlyRowOrderConfig();
  const info = await api(`/api/v1/tables/${selMonthlyCode}`).catch(()=>null);
  monthlyAllYears = [...new Set(((info && info.years) || []).map(Number).filter(Number.isFinite))];
  monthlyExtraTables = new Map();
  closeMonthlyPopup();
  const years = monthlyDisplayYears((info && info.years) || []);
  if(!years.length){
    monthlyYearTables = [];
    wrap.style.removeProperty("--monthly-m-w");
    wrap.innerHTML = `<div class="sub">データなし</div>`;
    renderMonthlySeriesSelect();
    drawMonthlyChart([], monthlySeries);
    return;
  }
  const tables = await Promise.all(
    years.map(y => api(`/api/v1/tables/${selMonthlyCode}/${y}`).catch(()=>null))
  );
  monthlyYearTables = tables.filter(Boolean);
  renderMonthlyYearTables(monthlyYearTables);
}

function monthlyIsPhone(){
  return window.matchMedia("(max-width:640px)").matches;
}

function monthlyYTicks(ymin, ymax, yoy){
  const span = Math.max(ymax - ymin, yoy ? 20 : 1);
  const raw = span / 4;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  let step;
  if(n <= 1) step = mag;
  else if(n <= 2) step = 2 * mag;
  else if(n <= 5) step = 5 * mag;
  else step = 10 * mag;
  const ticks = [];
  const start = Math.ceil((ymin - step * 1e-9) / step) * step;
  for(let v = start; v <= ymax + step * 1e-6; v += step){
    const t = Math.round(v / step) * step;
    let r = Math.abs(t) >= 100 ? Math.round(t) : Math.round(t * 100) / 100;
    if(Math.abs(r) < 1e-9) r = 0;
    if(!ticks.length || Math.abs(ticks[ticks.length-1] - r) > step * 0.01) ticks.push(r);
  }
  if(yoy && ymin <= 100 && 100 <= ymax && !ticks.some(t => Math.abs(t-100) < 1e-6)){
    ticks.push(100);
    ticks.sort((a,b)=>a-b);
  }
  return ticks.length ? ticks : [ymin, ymax];
}

function fmtMonthlyTick(v){
  if(Math.abs(v) < 1e-9) v = 0;
  if(Math.abs(v - Math.round(v)) < 1e-6)
    return Math.round(v).toLocaleString("ja-JP");
  return v.toLocaleString("ja-JP", {maximumFractionDigits:1});
}

function fmtMonthlyBarV(v){
  if(Math.abs(v) < 1e-9) v = 0;
  if(Math.abs(v - Math.round(v)) < 1e-6) return String(Math.round(v));
  return String(Math.round(v * 10) / 10);
}

let monthlyChartCssW = 0;

function drawMonthlyChart(tables, seriesId){
  const host = $("#monthly-chart");
  const choices = monthlySeriesChoices(tables);
  const spec = choices.find(s => s.id === seriesId) || choices[0];
  if(!spec){
    host.innerHTML = `<div class="sub">データなし</div>`;
    return;
  }
  const keep = new Set(monthlyDisplayYears((tables || []).map(d => d && d.year)));
  const years = [];
  const byYear = {};
  for(const d of tables || []){
    if(!d || !keep.has(Number(d.year))) continue;
    const el = monthlySeriesElement(d, spec.id);
    if(!el) continue;
    const nums = [];
    let any = false;
    for(let m=1;m<=12;m++){
      const raw = el.values && el.values[String(m)];
      const n = raw==null || raw==="" ? null : Number(raw);
      const v = Number.isFinite(n) ? n : null;
      if(v!=null) any = true;
      nums.push(v);
    }
    if(!any) continue;
    years.push(d.year);
    byYear[d.year] = nums;
  }
  years.sort((a,b)=>b-a);
  if(!years.length){
    host.innerHTML = `<div class="sub">データなし</div>`;
    return;
  }
  const vals = [];
  for(const y of years) for(const v of byYear[y]) if(v!=null) vals.push(v);
  const ymin = 0;
  const ymax = Math.max(...vals, spec.yoy ? 100 : 1) * 1.08;
  const panels = years.map(year => ({
    year,
    color: monthlyYearColor(year),
    values: byYear[year],
  }));
  const chart = {spec, ymin, ymax, panels};

  const phone = monthlyIsPhone();
  const cssW = host.clientWidth || document.documentElement.clientWidth || 720;
  monthlyChartCssW = cssW;
  const W = Math.max(cssW, phone ? 280 : 480);
  const H = phone ? 148 : 140;
  const L = 52, R = phone ? 10 : 12, T = 20, B = phone ? 28 : 24;
  const fsAxis = phone ? 13 : 11;
  const plotW=W-L-R, plotH=H-T-B;
  const yx = v => T + (1 - (v-chart.ymin)/((chart.ymax-chart.ymin)||1)) * plotH;
  const slot = plotW/12;
  const barW = Math.max(3, slot*0.5);
  const fsBar = slot < 32 ? 9 : 11;
  const monthY = T + plotH + (phone ? 16 : 14);
  const monthLabel = slot < 28 ? (m => String(m)) : (m => `${m}月`);

  let html = "";
  for(const p of chart.panels){
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escHtml(chart.spec.label)}">`;
    for(const tick of monthlyYTicks(chart.ymin, chart.ymax, chart.spec.yoy)){
      const y = yx(tick);
      svg += `<line x1="${L}" x2="${W-R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e8ecf0"/>`;
      svg += `<text x="${L-6}" y="${y.toFixed(1)}" font-size="${fsAxis}" text-anchor="end" dominant-baseline="middle" fill="#66707a">${fmtMonthlyTick(tick)}</text>`;
    }
    if(chart.spec.yoy){
      const y100 = yx(100);
      svg += `<line x1="${L}" x2="${W-R}" y1="${y100}" y2="${y100}" stroke="#e6a100" stroke-dasharray="4 3"/>`;
    }
    p.values.forEach((v, mi) => {
      if(v==null) return;
      const x = L + slot*mi + (slot-barW)/2;
      const y = yx(Math.max(v, chart.ymin));
      const hgt = Math.max(1, yx(chart.ymin) - y);
      svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${hgt.toFixed(1)}" fill="${p.color}"/>`;
      const lx = x + barW/2;
      svg += `<text class="monthly-bar-v" x="${lx.toFixed(1)}" y="${(y-3).toFixed(1)}" font-size="${fsBar}" text-anchor="middle" fill="#333">${fmtMonthlyBarV(v)}</text>`;
    });
    for(let m=1;m<=12;m++){
      const x = L + slot*(m-1) + slot/2;
      svg += `<text x="${x.toFixed(1)}" y="${monthY}" font-size="${fsAxis}" text-anchor="middle" fill="#66707a">${monthLabel(m)}</text>`;
    }
    svg += `</svg>`;
    html += `<div class="monthly-year-chart"><div class="monthly-year-lab"><span class="monthly-year-swatch" style="background:${p.color}"></span>${p.year}年</div>${svg}</div>`;
  }
  host.innerHTML = html;
}

window.matchMedia("(max-width:640px)").addEventListener("change", () => {
  if(monthlyYearTables.length) drawMonthlyChart(monthlyYearTables, monthlySeries);
});
window.addEventListener("resize", () => {
  const host = $("#monthly-chart");
  if(!host || !$("#monthly-detail") || $("#monthly-detail").style.display === "none") return;
  if(Math.abs((host.clientWidth || 0) - monthlyChartCssW) < 8) return;
  if(monthlyYearTables.length) drawMonthlyChart(monthlyYearTables, monthlySeries);
});

const MONTHLY_DISPLAY_UNITS = [
  ["1人当たり受注高", "万円", 0],
  ["新規受注高", "百万円", 0],
  ["キャンセル額", "百万円", 0],
  ["家賃ベース入居率", "%", 2],
  ["入居者斡旋件数", "件", 0],
  ["営業人員数", "人", 0],
  ["空室在庫", "カ月", 2],
  ["空室数", "戸", 0],
  ["受注高", "百万円", 0],
];

function monthlyDisplayUnit(element, unit){
  if((unit || "number") !== "number") return null;
  const name = String(element || "");
  for(const [token, suffix, digits] of MONTHLY_DISPLAY_UNITS){
    if(name.includes(token)) return {suffix, digits};
  }
  return null;
}

function fmtMonthlyCell(v, unit, element){
  if(v==null || v==="") return "-";
  const n = Number(v);
  if(!Number.isFinite(n)) return "-";
  const spec = monthlyDisplayUnit(element, unit);
  if(!spec){
    return n.toLocaleString("ja-JP",{maximumFractionDigits:4});
  }
  const p = 10 ** spec.digits;
  const rounded = Math.round(n * p) / p;
  return rounded.toLocaleString("ja-JP", {maximumFractionDigits: spec.digits});
}

window.setMonthlyGroup = setMonthlyGroup;
window.showMonthlyDetail = showMonthlyDetail;
window.pickMonthlyIssuer = pickMonthlyIssuer;
window.backMonthly = backMonthly;
window.setMonthlySeries = setMonthlySeries;
window.toggleMonthlyTimelineSection = toggleMonthlyTimelineSection;
window.loadMonthlyList = loadMonthlyList;
window.findMonthlyIssuer = findMonthlyIssuer;
bindMonthlyIssuerSearch();
window.closeMonthlyPopup = closeMonthlyPopup;
if(typeof document.addEventListener === "function"){
  document.addEventListener("click", ev => {
    const td = ev.target.closest && ev.target.closest("#monthly-table-wrap table.monthly-tl td.num[data-m]");
    if(td){
      openMonthlyPopup(td);
      return;
    }
    if(!(ev.target.closest && ev.target.closest("#monthly-pop"))) closeMonthlyPopup();
  });
  document.addEventListener("keydown", ev => { if(ev.key === "Escape") closeMonthlyPopup(); });
}
const monthlyBack = $("#monthly-back");
if(monthlyBack) monthlyBack.addEventListener("click", backMonthly);

let monthlyRows=[], monthlyGroup="", selMonthlyCode=null;
let monthlySearchApplied="";
let monthlySeries="", monthlyYearTables=[];
const MONTHLY_YEAR_COLORS = ["#0077b6","#2e8b57","#b8860b","#8e4ec6"];
let MONTHLY_ROW_ORDER = {};
let _monthlyRowOrderReady = null;
const MONTHLY_SEGMENT_TAIL_BASE = 10000;
const MONTHLY_LAYOUT_MODE_KEY = "monthly.layoutMode.debug";
let monthlyLayoutMode = (() => {
  try{
    const v = sessionStorage.getItem(MONTHLY_LAYOUT_MODE_KEY);
    return v === "flat" ? "flat" : "gated";
  }catch(_){
    return "gated";
  }
})();
const MONTHLY_GATE_CODES_KEY = "monthly.gateCodes.debug";
let monthlyGateCodes = null;
let monthlyGateIndexFp = "";
let monthlyGateIndexPromise = null;
let monthlyIrCodes = null;
// 試作: 年を横に並べた 1 枚表（timeline）。years は従来の年表。個人 /app だけ。
const MONTHLY_VIEW_MODE_KEY = "monthly.viewMode.proto";
const MONTHLY_VIEW_MODES = ["timeline", "years"];
let monthlyViewMode = (() => {
  try{
    const v = localStorage.getItem(MONTHLY_VIEW_MODE_KEY);
    return MONTHLY_VIEW_MODES.includes(v) ? v : "timeline";
  }catch(_){
    return "timeline";
  }
})();
// 試作: 金額と前年比が両方ある項目は 1 行にまとめ、表示する方を切り替える。色はどちらでも前年比。
const MONTHLY_VALUE_MODE_KEY = "monthly.valueMode.proto";
let monthlyValueMode = (() => {
  try{
    const v = localStorage.getItem(MONTHLY_VALUE_MODE_KEY);
    return (v === "amount" || v === "both") ? v : "yoy";
  }catch(_){
    return "yoy";
  }
})();
let monthlyTlOpen = new Set();
// セルのポップアップで同じ月を並べる最初の暦年（それより前は取込済みと未取込が混ざる）。
const MONTHLY_POPUP_MIN_YEAR = 2024;
let monthlyAllYears = [];
let monthlyExtraTables = new Map();
let monthlyTlRecs = new Map();
let monthlyTlRevealed = "";
let _monthlyIrCodesReady = null;

function monthlyPublic(){
  return !!(document.body && document.body.dataset.publicMonthly === "1");
}

function monthlyCode4(code){
  return String(code || "").slice(0, 4);
}

function loadMonthlyRowOrderConfig(){
  if(_monthlyRowOrderReady) return _monthlyRowOrderReady;
  _monthlyRowOrderReady = fetch("/static/monthly_row_order.json")
    .then(r => r.ok ? r.json() : {})
    .then(j => { MONTHLY_ROW_ORDER = (j && typeof j === "object") ? j : {}; return MONTHLY_ROW_ORDER; })
    .catch(() => { MONTHLY_ROW_ORDER = {}; return MONTHLY_ROW_ORDER; });
  return _monthlyRowOrderReady;
}

function loadMonthlyIrCodes(){
  if(monthlyPublic()) return Promise.resolve(new Set());
  if(_monthlyIrCodesReady) return _monthlyIrCodesReady;
  _monthlyIrCodesReady = fetch("/static/ir_monthly_codes.json")
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
  sales:1, customers:2, spend:3, open:4, close:5, adj:6, stores:7, other:8,
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
      || label.metric_kind === "close" || label.metric_kind === "adj") return 3;
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
    && label.metric === "売上高" && (label.scope === "全店" || !label.scope)
    && !label.segment && !label.breakdown;
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

function monthlySegmentGate(tables){
  const keep = new Set(monthlyDisplayYears((tables || []).map(t => t && t.year)));
  const gated = new Set();
  for(const t of tables || []){
    if(!t || !keep.has(Number(t.year))) continue;
    const counts = {};
    for(const e of t.elements || []){
      const L = e.label;
      if(!L || !L.group) continue;
      if(!L.metric && !L.channel && !L.scope) continue;
      counts[L.group] = (counts[L.group] || 0) + 1;
    }
    for(const [seg, n] of Object.entries(counts)){
      if(n >= 2) gated.add(seg);
    }
  }
  return gated;
}

function monthlyStubChannelText(channel){
  return channel || "—";
}

function monthlyStubScopeText(scope){
  if(scope === "既存") return "既存店";
  return scope || "—";
}

function monthlyLayoutLeaf(label, unit, dropSegment){
  const leftover = (dropSegment && label.segment === dropSegment) ? "" : label.segment;
  const u = unit || "number";
  const isYoy = u === "yoy_index" || u === "yoy_pct";
  if(label.breakdown){
    let bd = label.breakdown;
    if(isYoy && !bd.endsWith("(前年比)")) bd += "(前年比)";
    return "└ " + bd;
  }
  let metric = label.metric || "";
  if(isYoy && metric && !metric.endsWith("(前年比)")) metric += "(前年比)";
  if(!isYoy && metric){
    const spec = monthlyDisplayUnit(label.display, u);
    if(spec && !metric.includes("（" + spec.suffix + "）")) metric += "（" + spec.suffix + "）";
  }
  return [label.country, leftover, metric].filter(Boolean).join(" ") || String(label.display || "");
}

function monthlyLayoutDataRow(item, gatedSegment, idx){
  const L = item.label;
  return {
    kind: "data",
    element: item.element,
    unit: item.unit,
    label: L,
    group: gatedSegment || "",
    channel: L.channel,
    scope: L.scope,
    breakdown: L.breakdown || "",
    breakdownDepth: L.breakdown_depth ?? (L.breakdown ? 1 : 0),
    leaf: monthlyLayoutLeaf(L, item.unit, gatedSegment || ""),
    channelRank: MONTHLY_CHANNEL_RANK[L.channel] ?? 3,
    scopeRank: monthlyRowScopeRank(L),
    slot: MONTHLY_METRIC_RANK[L.metric_kind],
    unitRank: monthlyRowUnitRank(item.unit),
    bdRank: MONTHLY_BREAKDOWN_RANK[L.breakdown] ?? 4,
    idx
  };
}

function monthlyReorderBlock(block){
  return block.slice().sort((a, b) => {
    if(a.channelRank !== b.channelRank) return a.channelRank - b.channelRank;
    if(a.scopeRank !== b.scopeRank) return a.scopeRank - b.scopeRank;
    if(a.slot !== b.slot) return a.slot - b.slot;
    if((a.unitRank || 0) !== (b.unitRank || 0)) return (a.unitRank || 0) - (b.unitRank || 0);
    if((a.bdRank || 0) !== (b.bdRank || 0)) return (a.bdRank || 0) - (b.bdRank || 0);
    return a.idx - b.idx;
  });
}

function monthlyTableLayout(rowOrder, tables, opts){
  const forceFlat = (opts && opts.forceFlat) || monthlyLayoutMode === "flat";
  const gated = forceFlat ? new Set() : monthlySegmentGate(tables);
  if(!gated.size){
    return {
      gated: false,
      rows: (rowOrder || []).map((e, idx) => monthlyLayoutDataRow(e, "", idx))
    };
  }
  const company = [];
  const brands = new Map();
  (rowOrder || []).forEach((e, idx) => {
    const seg = e.label.group;
    if(seg && gated.has(seg)){
      if(!brands.has(seg)) brands.set(seg, []);
      brands.get(seg).push(monthlyLayoutDataRow(e, seg, idx));
    }else{
      company.push(monthlyLayoutDataRow(e, "", idx));
    }
  });
  const rows = monthlyReorderBlock(company);
  for(const [label, items] of brands){
    rows.push({kind: "section", label});
    rows.push(...monthlyReorderBlock(items));
  }
  return {gated: true, rows};
}

function monthlyAssignStubSpans(rows){
  const n = rows.length;
  let i = 0;
  while(i < n){
    if(rows[i].kind !== "data"){
      i++;
      continue;
    }
    let j = i + 1;
    while(j < n && rows[j].kind === "data" && rows[j].channel === rows[i].channel) j++;
    const chanSpan = j - i;
    let k = i;
    while(k < j){
      let m = k + 1;
      while(m < j && rows[m].scope === rows[k].scope) m++;
      const scSpan = m - k;
      for(let t = k; t < m; t++){
        rows[t].channelSpan = (t === i) ? chanSpan : 0;
        rows[t].scopeSpan = (t === k) ? scSpan : 0;
      }
      k = m;
    }
    i = j;
  }
  return rows;
}

function monthlyVisibleLayoutRows(layout, byName){
  const out = [];
  let section = null;
  for(const rec of (layout && layout.rows) || []){
    if(rec.kind === "section"){
      section = rec;
      continue;
    }
    const src = byName[rec.element];
    const vals = (src && src.values) || {};
    if(!src || !Object.keys(vals).length) continue;
    if(section){
      out.push(section);
      section = null;
    }
    out.push(rec);
  }
  return monthlyAssignStubSpans(out);
}

function monthlySeriesChoices(tables){
  const rowOrder = monthlyRowOrder(tables);
  const layout = monthlyTableLayout(rowOrder, tables);
  const out = [];
  for(const e of layout.rows){
    if(e.kind === "section") continue;
    const id = e.label.display;
    out.push({
      id,
      label: layout.gated ? (e.leaf || id) : id,
      yoy: e.unit==="yoy_index" || e.unit==="yoy_pct",
      group: layout.gated ? (e.group || "全体") : "",
    });
  }
  return out;
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
  if(monthlyGroup==="__gate__"){
    const gated = monthlyGateCodes || new Set();
    return issuers.filter(r => gated.has(r.code));
  }
  if(monthlyGroup==="__ir__"){
    const want = monthlyIrCodes || new Set();
    return issuers.filter(r => want.has(monthlyCode4(r.code).toUpperCase()));
  }
  return issuers.filter(r => (r.industry || "その他") === monthlyGroup);
}

function monthlyIssuerListFingerprint(issuers){
  return (issuers || []).map(r => String(r.code || "")).filter(Boolean).sort().join(",");
}

function monthlyLatestYearFromList(row){
  let best = null;
  for(const y of (row && row.years) || []){
    const n = Number(y);
    if(!Number.isFinite(n)) continue;
    if(best == null || n > best) best = n;
  }
  return best;
}

function invalidateMonthlyGateIndex(){
  monthlyGateCodes = null;
  monthlyGateIndexFp = "";
  monthlyGateIndexPromise = null;
}

async function ensureMonthlyGateIndex(){
  const issuers = uniqueMonthlyIssuers(monthlyRows);
  const fp = monthlyIssuerListFingerprint(issuers);
  if(monthlyGateCodes && monthlyGateIndexFp === fp) return monthlyGateCodes;
  if(monthlyGateIndexPromise) return monthlyGateIndexPromise;
  monthlyGateIndexPromise = (async () => {
    try{
      const raw = sessionStorage.getItem(MONTHLY_GATE_CODES_KEY);
      const savedFp = sessionStorage.getItem(MONTHLY_GATE_CODES_KEY + ".fp");
      if(raw && savedFp === fp){
        const arr = JSON.parse(raw);
        if(Array.isArray(arr)){
          monthlyGateCodes = new Set(arr.map(String));
          monthlyGateIndexFp = fp;
          return monthlyGateCodes;
        }
      }
    }catch(_){}
    const gated = new Set();
    const jobs = [];
    for(const r of issuers){
      const year = monthlyLatestYearFromList(r);
      if(year == null) continue;
      jobs.push({code: r.code, year});
    }
    let cursor = 0;
    const workers = Array.from({length: Math.min(8, Math.max(jobs.length, 1))}, async () => {
      while(cursor < jobs.length){
        const job = jobs[cursor++];
        const table = await api(`/api/v1/tables/${job.code}/${job.year}`).catch(() => null);
        if(!table) continue;
        if(monthlySegmentGate([table]).size) gated.add(job.code);
      }
    });
    await Promise.all(workers);
    monthlyGateCodes = gated;
    monthlyGateIndexFp = fp;
    try{
      sessionStorage.setItem(MONTHLY_GATE_CODES_KEY, JSON.stringify([...gated].sort()));
      sessionStorage.setItem(MONTHLY_GATE_CODES_KEY + ".fp", fp);
    }catch(_){}
    return monthlyGateCodes;
  })();
  try{
    return await monthlyGateIndexPromise;
  }finally{
    monthlyGateIndexPromise = null;
  }
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
  const fp = monthlyIssuerListFingerprint(issuers);
  if(fp !== monthlyGateIndexFp) invalidateMonthlyGateIndex();
  if(!monthlyGroup) monthlyGroup = monthlyPublic() ? "__all__" : "__new__";
  if(!issuers.length){
    $("#monthly-groups").innerHTML = "";
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    hideMonthlySuggest();
    return;
  }
  const groups = monthlyIndustries(issuers);
  if(monthlyGroup!=="__new__" && monthlyGroup!=="__all__" && monthlyGroup!=="__gate__" && monthlyGroup!=="__ir__" && !groups.includes(monthlyGroup)){
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
    h += `<button type="button" class="mchip${monthlyGroup==="__gate__"?" on":""}" data-group="__gate__" onclick="setMonthlyGroup('__gate__')">ゲート（一時）</button>`;
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
  if(g === "__gate__"){
    const list = $("#monthly-list");
    if(list) list.innerHTML = `<div class="sub">ゲート判定中…</div>`;
    await ensureMonthlyGateIndex();
    if(monthlyGroup !== "__gate__") return;
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
      : (monthlyGroup === "__gate__" ? "ゲート対象はありません"
        : (monthlyGroup === "__ir__" ? "IR対象はありません" : "登録された月次データはありません"));
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
  const grouped = choices.some(s => s.group);
  if(!grouped){
    sel.innerHTML = choices.map(s =>
      `<option value="${escHtml(s.id)}">${escHtml(s.label)}</option>`).join("");
  }else{
    let h = "";
    let cur = null;
    for(const s of choices){
      if(s.group !== cur){
        if(cur !== null) h += `</optgroup>`;
        h += `<optgroup label="${escHtml(s.group)}">`;
        cur = s.group;
      }
      h += `<option value="${escHtml(s.id)}">${escHtml(s.label)}</option>`;
    }
    if(cur !== null) h += `</optgroup>`;
    sel.innerHTML = h;
  }
  sel.value = monthlySeries;
}

function setMonthlySeries(id){
  monthlySeries = id;
  const sel = $("#monthly-series");
  if(sel && sel.value !== id) sel.value = id;
  if(monthlyTimelineOn() && monthlyTimelineReveal(id)){
    renderMonthlyYearTables(monthlyYearTables);
    return;
  }
  drawMonthlyChart(monthlyYearTables, monthlySeries);
  markMonthlySeriesRows();
}

function syncMonthlyLayoutDebugUi(canGate){
  const host = $("#monthly-layout-debug");
  const sel = $("#monthly-layout-mode");
  if(!host || !sel) return;
  if(monthlyPublic()){
    host.hidden = true;
    return;
  }
  if(canGate){
    host.hidden = false;
    sel.value = monthlyLayoutMode === "flat" ? "flat" : "gated";
  }else{
    host.hidden = true;
  }
}

function setMonthlyLayoutMode(mode){
  monthlyLayoutMode = mode === "flat" ? "flat" : "gated";
  try{ sessionStorage.setItem(MONTHLY_LAYOUT_MODE_KEY, monthlyLayoutMode); }catch(_){}
  const sel = $("#monthly-layout-mode");
  if(sel && sel.value !== monthlyLayoutMode) sel.value = monthlyLayoutMode;
  if(!monthlyYearTables.length) return;
  renderMonthlyYearTables(monthlyYearTables);
}

function monthlyTimelineOn(){
  return !monthlyPublic() && monthlyViewMode !== "years";
}

function syncMonthlyViewDebugUi(){
  const host = $("#monthly-view-debug");
  const sel = $("#monthly-view-mode");
  if(!host || !sel) return;
  host.hidden = monthlyPublic();
  sel.value = monthlyViewMode;
}

function setMonthlyViewMode(mode){
  monthlyViewMode = MONTHLY_VIEW_MODES.includes(mode) ? mode : "timeline";
  try{ localStorage.setItem(MONTHLY_VIEW_MODE_KEY, monthlyViewMode); }catch(_){}
  syncMonthlyViewDebugUi();
  if(monthlyYearTables.length) renderMonthlyYearTables(monthlyYearTables);
}

const MONTHLY_TL_MAIN_KINDS = new Set(["sales", "customers", "spend", "stores"]);
// 主要行がこれより少ない発行体（セグメント別しかない J.フロント等）は、行順の先頭から足す。
const MONTHLY_TL_MIN_MAIN = 8;

function monthlyTimelineIsMain(L){
  return !L.group && !L.breakdown && !L.country && !L.channel
    && MONTHLY_TL_MAIN_KINDS.has(L.metric_kind);
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
  return rec.yoy && monthlyValueMode === "yoy" ? rec.yoy : rec.element;
}

function monthlyTlAlt(rec){
  if(!rec.yoy) return "";
  return monthlyTlShown(rec) === rec.yoy ? rec.element : rec.yoy;
}

// txt はセルの中身（HTML）、len は幅の根拠にする最長行の文字数。数字を出さないセルは塗らない。
function monthlyTlCell(byName, rec, m){
  const id = monthlyTlShown(rec);
  const src = byName[id];
  const unit = (src && src.unit) || (id === rec.yoy ? "yoy_index" : rec.unit) || "number";
  const v = src ? (src.values || {})[String(m)] : null;
  let txt = (v == null || v === "") ? "" : fmtMonthlyCell(v, unit, id);
  let len = txt.length;
  const heatId = rec.yoy || (monthlyIsYoyUnit(unit) ? id : "");
  const hs = heatId ? byName[heatId] : null;
  const hv = hs ? (hs.values || {})[String(m)] : null;
  if(rec.yoy && monthlyValueMode === "both" && hv != null && hv !== ""){
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

function syncMonthlyValueUi(show){
  const host = $("#monthly-value-tabs");
  if(!host) return;
  host.hidden = !show;
  host.querySelectorAll("button[data-v]").forEach(b => b.classList.toggle("on", b.dataset.v === monthlyValueMode));
}

function setMonthlyValueMode(mode){
  monthlyValueMode = (mode === "amount" || mode === "both") ? mode : "yoy";
  try{ localStorage.setItem(MONTHLY_VALUE_MODE_KEY, monthlyValueMode); }catch(_){}
  const base = monthlyYoyBase(monthlySeries);
  const want = monthlyValueMode === "yoy" ? base + "(前年比)" : base;
  const ids = new Set(monthlyYearTables.flatMap(d => (d.elements || []).map(e => e.label.display)));
  if(want !== monthlySeries && ids.has(base) && ids.has(base + "(前年比)")){
    monthlySeries = want;
    const sel = $("#monthly-series");
    if(sel) sel.value = want;
  }
  if(!monthlyYearTables.length) return;
  const wrap = $("#monthly-table-wrap");
  const left = wrap ? wrap.scrollLeft : 0;
  renderMonthlyYearTables(monthlyYearTables, {keepScroll: true});
  if(wrap) wrap.scrollLeft = left;
}

// layout の行を「常に出す主要行」と「畳める節」に分ける。値の無い行は落とす。
function monthlyTimelineSections(layout, tables){
  const hasValues = monthlyTimelineHasValues(tables);
  const newest = (tables || []).filter(Boolean).reduce((a, d) => (!a || Number(d.year) > Number(a.year)) ? d : a, null);
  const inNewest = monthlyTimelineHasValues(newest ? [newest] : []);
  const main = [];
  const more = {key: "__more__", label: "その他の項目", rows: []};
  const brands = [];
  let cur = null;
  for(const rec of monthlyPairYoyRows(layout.rows)){
    if(rec.kind === "section"){
      cur = {key: rec.label, label: rec.label, rows: []};
      brands.push(cur);
      continue;
    }
    if(!hasValues(rec.element) && !(rec.yoy && hasValues(rec.yoy))) continue;
    if(cur) cur.rows.push(rec);
    else if(monthlyTimelineIsMain(rec.label)) main.push(rec);
    else more.rows.push(rec);
  }
  // 足すのは最新の年に値がある行だけ（旧い年にしか無い行名を上げない）。
  for(const rec of more.rows.slice()){
    if(main.length >= MONTHLY_TL_MIN_MAIN) break;
    if(!inNewest(rec.element) && !(rec.yoy && inNewest(rec.yoy))) continue;
    main.push(rec);
    more.rows.splice(more.rows.indexOf(rec), 1);
  }
  // 主要行を親に持つ内訳は「その他」から主要行の側へ寄せる（開閉は親の ▸）。
  const pm = monthlyTlParents([...main, ...more.rows]);
  for(const rec of more.rows.slice()){
    let p = pm.get(rec);
    while(p && pm.get(p)) p = pm.get(p);
    if(p && main.includes(p)){
      main.push(rec);
      more.rows.splice(more.rows.indexOf(rec), 1);
    }
  }
  // 内訳の親になっている合計行（ベルーナの各領域の売上高）は節の見出しに数字ごと載せる。
  for(const sec of brands){
    const parents = new Set(monthlyTlParents(sec.rows).values());
    const total = sec.rows.find(r => !r.label.breakdown && parents.has(r))
      || (sec.rows.length === 1 && !sec.rows[0].label.breakdown ? sec.rows[0] : null);
    if(total){
      sec.total = total;
      sec.rows = sec.rows.filter(r => r !== total);
    }
  }
  return {main, sections: [more, ...brands].filter(s => s.rows.length || s.total)};
}

function monthlyBreakdownParents(){
  const cfg = MONTHLY_ROW_ORDER[String(selMonthlyCode || "")];
  const m = cfg && cfg.breakdown_parent;
  return (m && typeof m === "object") ? m : {};
}

// 内訳の親。1 段目は同じ group・指標・スコープ・チャネル・国で内訳の無い行。
// 2 段目以降は monthly_row_order.json の breakdown_parent（内訳名 → 親の内訳名）。範囲は rows の中だけ。
function monthlyTlParents(rows){
  const cfg = monthlyBreakdownParents();
  const sig = L => [L.group, L.metric, L.scope, L.channel, L.country].map(x => x || "").join("\u0000");
  const parent = new Map();
  for(const r of rows){
    const L = r.label;
    if(!L.breakdown) continue;
    const want = cfg[L.breakdown] || "";
    const cands = rows.filter(q => q !== r && sig(q.label) === sig(L) && (q.label.breakdown || "") === want);
    const p = cands.find(q => monthlyIsYoyUnit(q.unit) === monthlyIsYoyUnit(r.unit)) || cands[0];
    if(p) parent.set(r, p);
  }
  return parent;
}

// rows を木の先行順に並べ、閉じた親の子孫は落とす。親の無い行は rows の順のまま根になる。
function monthlyTlVisible(rows, parent, under){
  const kids = new Map();
  for(const r of rows){
    const p = parent.get(r);
    if(!p) continue;
    if(!kids.has(p)) kids.set(p, []);
    kids.get(p).push(r);
  }
  const out = [];
  const walk = (r, depth) => {
    const ks = kids.get(r) || [];
    const key = ks.length ? "node:" + r.element : "";
    const open = !!key && monthlyTlOpen.has(key);
    out.push({rec: r, depth, key, open, child: parent.has(r), under: !!under});
    if(open) for(const k of ks) walk(k, depth + 1);
  };
  // 合計を見出しに載せた節の中身は、見出しの子として 1 段下げる。
  for(const r of rows) if(!parent.has(r)) walk(r, under ? 1 : 0);
  return out;
}

function monthlyTlLabel(e, gated){
  const L = e.rec.label;
  if((e.child || e.under) && L.breakdown){
    const yoyOnly = !e.rec.yoy && monthlyIsYoyUnit(e.rec.unit);
    return L.breakdown + (yoyOnly ? "(前年比)" : "");
  }
  return monthlyTimelineRowName(e.rec, gated);
}

// 名前セル。子を持つ行と合計つきの節は ▸/▾ で開閉する（行のクリックは系列の選択なので親へ伝えない）。
function monthlyTlNameCell(o){
  const pad = o.depth ? ` style="padding-left:${(0.5 + o.depth * 1.25).toFixed(2)}em"` : "";
  const tw = o.key ? `<span class="tl-tw">${o.open ? "▾" : "▸"}</span>` : (o.tree ? `<span class="tl-tw"></span>` : "");
  const act = o.key ? ` data-key="${escHtml(o.key)}" onclick="event.stopPropagation();toggleMonthlyTimelineSection(this.dataset.key)"` : "";
  const span = (o.rowspan ? ` rowspan="${o.rowspan}"` : "") + (o.colspan ? ` colspan="${o.colspan}"` : "");
  const cnt = o.count ? `<span class="tl-cnt">${o.count}行</span>` : "";
  return `<td class="tl-name${o.cls || ""}${o.key ? " tl-tog" : ""}"${span}${pad}${act} title="${escHtml(o.title || "")}">${tw}${escHtml(o.text)}${cnt}</td>`;
}

function monthlyTimelineRowName(rec, gated){
  if(!gated) return rec.element;
  if(rec.breakdown) return rec.leaf;
  const sc = rec.scope ? monthlyStubScopeText(rec.scope) : "";
  return [rec.channel, sc, rec.leaf].filter(Boolean).join(" ");
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
  const layout = monthlyTableLayout(monthlyRowOrder(monthlyYearTables), monthlyYearTables);
  const {main, sections} = monthlyTimelineSections(layout, monthlyYearTables);
  const hit = r => r.element === id || r.yoy === id;
  let changed = false;
  const open = key => {
    if(monthlyTlOpen.has(key)) return;
    monthlyTlOpen.add(key);
    changed = true;
  };
  const openAncestors = rows => {
    const r = rows.find(hit);
    if(!r) return false;
    const parent = monthlyTlParents(rows);
    for(let p = parent.get(r); p; p = parent.get(p)) open("node:" + p.element);
    return true;
  };
  if(openAncestors(main)) return changed;
  for(const sec of sections){
    if(sec.total && hit(sec.total)) return changed;
    if(openAncestors(sec.rows)){
      open(sec.key);
      return changed;
    }
  }
  return changed;
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

function renderMonthlyTimeline(tables, layout, opts){
  const wrap = $("#monthly-table-wrap");
  const byYear = monthlyTablesByYear(tables);
  const years = [...byYear.keys()].sort((a, b) => a - b);
  const {main, sections} = monthlyTimelineSections(layout, tables);
  const all = [...main, ...sections.flatMap(s => s.rows)];
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
  const scopeHtml = (rows, under) => {
    const parent = monthlyTlParents(rows);
    const tree = parent.size > 0 || !!under;
    return monthlyTlVisible(rows, parent, under).map(e => monthlyTlRemember(e.rec, monthlyTlLabel(e, layout.gated)) +
      `<tr${monthlyTlRowAttrs(e.rec)}>${monthlyTlNameCell({text: monthlyTlLabel(e, layout.gated), title: e.rec.element, depth: e.depth, key: e.key, open: e.open, tree})}${cellsFor(e.rec)}</tr>`
    ).join("");
  };
  let body = scopeHtml(main);
  for(const sec of sections){
    const open = monthlyTlOpen.has(sec.key);
    const canOpen = sec.rows.length > 0;
    const name = monthlyTlNameCell({text: sec.label, title: sec.total ? sec.total.element : sec.label, key: canOpen ? sec.key : "", open, tree: true, count: canOpen ? sec.rows.length : 0});
    if(sec.total) body += monthlyTlRemember(sec.total, sec.label) + `<tr class="tl-sec tl-sec-total"${monthlyTlRowAttrs(sec.total)}>${name}${cellsFor(sec.total)}</tr>`;
    else body += `<tr class="tl-sec">${name}<td class="tl-sec-fill" colspan="${cols.length}"></td></tr>`;
    if(open) body += scopeHtml(sec.rows, !!sec.total);
  }
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
  const rowOrder = monthlyRowOrder(tables);
  const canGate = monthlySegmentGate(tables).size > 0;
  const layout = monthlyTableLayout(rowOrder, tables);
  syncMonthlyLayoutDebugUi(canGate);
  syncMonthlyViewDebugUi();
  syncMonthlyValueUi(false);
  renderMonthlySeriesSelect();
  if(monthlyTimelineOn()){
    monthlyTimelineReveal(monthlySeries);
    syncMonthlyValueUi(monthlyPairYoyRows(layout.rows).some(r => r.yoy));
    renderMonthlyTimeline(tables, layout, opts);
    drawMonthlyChart(tables, monthlySeries);
    markMonthlySeriesRows();
    return;
  }
  let h = "";
  let maxNumLen = 0;
  for(const d of tables){
    if(!d) continue;
    if(layout.gated){
      h += `<table class="fin monthly monthly-gated"><colgroup><col class="monthly-stub-ch"><col class="monthly-stub-sc"><col class="monthly-stub-leaf"><col class="monthly-m" span="12"></colgroup><thead><tr><th class="l year" colspan="3">${d.year}</th>`;
    }else{
      h += `<table class="fin monthly"><colgroup><col class="monthly-name"><col class="monthly-m" span="12"></colgroup><thead><tr><th class="l year">${d.year}</th>`;
    }
    for(let m=1;m<=12;m++) h += `<th>${m}月</th>`;
    h += `</tr></thead><tbody>`;
    const byName = {};
    for(const src of d.elements || []) byName[src.label.display] = src;
    for(const e of rowOrder){
      if(layout.gated) break;
      const src = byName[e.element];
      const vals = (src && src.values) || {};
      if(!Object.keys(vals).length) continue;
      const unit = (src && src.unit) || e.unit || "number";
      const ds = ` data-series="${escHtml(e.element)}" onclick="setMonthlySeries(this.dataset.series)"`;
      h += `<tr${ds}><td class="l">${escHtml(e.element)}</td>`;
      for(let m=1;m<=12;m++){
        const v = vals[String(m)];
        const txt = fmtMonthlyCell(v, unit, e.element);
        if(txt) maxNumLen = Math.max(maxNumLen, txt.length);
        h += `<td class="num">${txt}</td>`;
      }
      h += `</tr>`;
    }
    if(layout.gated){
      for(const e of monthlyVisibleLayoutRows(layout, byName)){
        if(e.kind === "section"){
          let months = "";
          for(let m=1;m<=12;m++) months += `<td class="num monthly-sec-month">${m}月</td>`;
          h += `<tr class="monthly-sec"><td class="monthly-sec-label" colspan="3">${escHtml(e.label)}</td>${months}</tr>`;
          continue;
        }
        const src = byName[e.element];
        const vals = (src && src.values) || {};
        if(!Object.keys(vals).length) continue;
        const unit = (src && src.unit) || e.unit || "number";
        const ds = ` data-series="${escHtml(e.element)}" onclick="setMonthlySeries(this.dataset.series)"`;
        let stubs = "";
        if(e.channelSpan) stubs += `<td class="monthly-stub monthly-stub-ch" rowspan="${e.channelSpan}">${escHtml(monthlyStubChannelText(e.channel))}</td>`;
        if(e.scopeSpan) stubs += `<td class="monthly-stub monthly-stub-sc" rowspan="${e.scopeSpan}">${escHtml(monthlyStubScopeText(e.scope))}</td>`;
        const bdClass = e.breakdown
          ? (Number(e.breakdownDepth) === 2 ? " monthly-bd monthly-bd-2" : " monthly-bd")
          : "";
        stubs += `<td class="l monthly-stub monthly-stub-leaf${bdClass}">${escHtml(e.leaf)}</td>`;
        h += `<tr${ds}>${stubs}`;
        for(let m=1;m<=12;m++){
          const v = vals[String(m)];
          const txt = fmtMonthlyCell(v, unit, e.element);
          if(txt) maxNumLen = Math.max(maxNumLen, txt.length);
          h += `<td class="num">${txt}</td>`;
        }
        h += `</tr>`;
      }
    }
    h += `</tbody></table>`;
  }
  wrap.style.setProperty("--monthly-m-w", `calc(${Math.max(maxNumLen, 3)}ch + 16px)`);
  wrap.innerHTML = h || `<div class="sub">データなし</div>`;
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
    syncMonthlyLayoutDebugUi(false);
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
window.setMonthlyViewMode = setMonthlyViewMode;
window.setMonthlyValueMode = setMonthlyValueMode;
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

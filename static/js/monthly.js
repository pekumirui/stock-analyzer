let monthlyRows=[], monthlyGroup="__new__", selMonthlyCode=null;
let monthlySeries="", monthlyYearTables=[];
const MONTHLY_YEAR_COLORS = ["#0077b6","#2e8b57","#b8860b","#8e4ec6"];

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

function monthlyElementKey(name){
  return String(name || "").normalize("NFKC").replace(/\s+/g, "");
}

function monthlySeriesKind(element, unit){
  const key = monthlyElementKey(element);
  const u = unit || "number";
  if(key.includes("店舗数") && u==="number"){
    if(["純増","新設","改装","業態転換"].some(m => key.includes(m))) return null;
    if(monthlyIsFlowMetric(parseMonthlyLayers(element).metric)) return null;
    return "stores";
  }
  if(u!=="yoy_index" && u!=="yoy_pct") return null;
  if(key.includes("既存")) return "existing";
  if(key.includes("全店") || key.includes("全社")) return "all_store";
  return null;
}

const MONTHLY_CHAN_BOTH = "\0CHANBOTH\0";
const MONTHLY_FLOW_PROTECT = [
  ["開店数","\0LAUNCHCNT\0"],["出店数","\0OPENCNT\0"],["退店数","\0LEAVECNT\0"],["閉店数","\0SHUTCNT\0"],["閉鎖数","\0CLOSECNT\0"],
  ["開店","\0LAUNCH\0"],["出店","\0OPEN\0"],["退店","\0LEAVE\0"],["閉店","\0SHUT\0"],["閉鎖","\0CLOSE\0"],
];
const MONTHLY_FLOW_RESTORE = MONTHLY_FLOW_PROTECT.map(([src, mark]) => [mark, src]);
const MONTHLY_UNGLUE = [
  ["既存直営店","直営 既存"],
  ["全店舗数","店舗数"],
  ["実店舗数","実店舗 店舗数"],
  ["店舗数","店舗数"],
  ["店数","店舗数"],
  ["来客数","来客数"],
  ["客単価","客単価"],
  ["売上高","売上高"],
  ["既存店舗","既存"],
  ["既存店","既存"],
  ["全店舗","全店"],
  ["全店","全店"],
  ["全社","全店"],
  ["フランチャイズ","FC"],
  ["FC店舗","FC"],
  ["FC店","FC"],
  ["ＦＣ店舗","FC"],
  ["ＦＣ店","FC"],
  ["直営店","直営"],
  ["直営","直営"],
  ["既存","既存"],
  ["月末","月末"],
  ["期末","期末"],
  ["合計","合計"],
  ["総計","総計"],
  ["小計","小計"],
  ["部門","部門"],
  ["うち","うち"],
];
const MONTHLY_DROP = new Set([
  "合計","総計","小計","計","月末","期末","うち","部門","店",
  "前期比","同期比","増減率","前年比","前年同月比","前年同月","全",
  "（","）","(",")","(前年比)","（前年比）",
]);
const MONTHLY_COUNTRY = {日本:"日本", 国内:"日本", 海外:"海外"};
const MONTHLY_CHANNEL = {
  "直営+FC":"直営+FC", "直営＋FC":"直営+FC", 直営:"直営",
  FC:"FC", ＦＣ:"FC", フランチャイズ:"FC",
};
const MONTHLY_METRIC_PREFIX = [
  ["店舗数","店舗数"],["来客数","客数"],["客単価","客単価"],["売上高","売上高"],
  ["客数","客数"],["来客","客数"],["単価","客単価"],["売上","売上高"],
];
const MONTHLY_METRIC_START = new Set(["店舗数","来客数","客単価","売上高","客数"]);
const MONTHLY_FILL_ALL = new Set(["売上高","客数","客単価"]);
const MONTHLY_STORE_FLOW = {
  出店:"出店", 出店数:"出店", 開店:"出店", 開店数:"出店", 新規:"出店",
  退店:"退店", 退店数:"退店", 閉店:"退店", 閉店数:"退店",
  閉鎖:"退店", 閉鎖数:"退店",
};
const MONTHLY_STORE_FLOW_METRICS = new Set(["出店","退店"]);
const MONTHLY_OPEN_SHARDS = new Set(["開"]);
const MONTHLY_CLOSE_SHARDS = new Set(["退","閉"]);
const MONTHLY_DUMMY_STORE = new Set(["店舗"]);
const MONTHLY_OPEN_QUALIFIERS = new Set(["新規"]);
const MONTHLY_STORE_ADJ = ["純増","新設","改装","業態転換"];
const MONTHLY_METRIC_RANK = {
  sales:1, customers:2, spend:3, open:4, close:5, adj:6, stores:7, other:8,
};
const MONTHLY_COUNTRY_RANK = {"":0, 日本:1, 海外:2};
const MONTHLY_CHANNEL_RANK = {"直営+FC":0, 直営:1, FC:2, "":3};

function monthlyUnglue(text){
  let s = String(text || "").split("直営+FC").join(" "+MONTHLY_CHAN_BOTH+" ").split("直営＋FC").join(" "+MONTHLY_CHAN_BOTH+" ");
  for(const [src, mark] of MONTHLY_FLOW_PROTECT) s = s.split(src).join(" "+mark+" ");
  for(const [src,dst] of MONTHLY_UNGLUE) s = s.split(src).join(" "+dst+" ");
  s = s.replace(/(?<![A-Za-z])FC(?![A-Za-z])/g, " FC ");
  s = s.split("ＦＣ").join(" FC ");
  for(const [mark, dst] of MONTHLY_FLOW_RESTORE) s = s.split(mark).join(dst);
  s = s.split(MONTHLY_CHAN_BOTH).join("直営+FC");
  return s.replace(/\s+/g, " ").trim();
}

function monthlyPeelMetric(tok){
  for(const [src, canon] of MONTHLY_METRIC_PREFIX){
    if(tok === src) return [canon, null];
    if(tok.length > src.length && tok.endsWith(src)) return [canon, tok.slice(0, tok.length - src.length)];
    if(MONTHLY_METRIC_START.has(src) && tok.length > src.length && tok.startsWith(src)) return [canon, tok.slice(src.length)];
  }
  return [null, tok];
}

function monthlyIsFlowMetric(metric){
  return String(metric || "").endsWith("出店") || String(metric || "").endsWith("退店");
}

function monthlyStitchStoreFlow(leftover, metric){
  if(metric === "" || metric === "店舗数"){
    if(leftover.some(tok => MONTHLY_OPEN_SHARDS.has(tok))){
      leftover = leftover.filter(tok => !MONTHLY_OPEN_SHARDS.has(tok));
      metric = "出店";
    }else if(leftover.some(tok => MONTHLY_CLOSE_SHARDS.has(tok))){
      leftover = leftover.filter(tok => !MONTHLY_CLOSE_SHARDS.has(tok));
      metric = "退店";
    }
  }
  if(monthlyIsFlowMetric(metric)){
    leftover = leftover.filter(tok => !MONTHLY_DUMMY_STORE.has(tok));
  }
  if(String(metric).endsWith("出店")){
    leftover = leftover.filter(tok => !MONTHLY_OPEN_QUALIFIERS.has(tok));
  }
  return [leftover, metric];
}

function monthlyStitchRealStore(leftover, metric){
  if(metric === "店舗数"){
    leftover = leftover.map(tok => tok === "実" ? "実店舗" : tok);
  }
  return [leftover, metric];
}

function monthlyStitchVsSales(leftover, metric){
  let had = false;
  const kept = [];
  for(const raw of leftover){
    let tok = raw;
    if(tok === "対"){ had = true; continue; }
    if(tok.length > 1 && tok.endsWith("対")){
      had = true;
      tok = tok.slice(0, -1);
      if(!tok || MONTHLY_DROP.has(tok)) continue;
    }
    kept.push(tok);
  }
  if(!metric && had) metric = "売上高";
  return [kept, metric];
}

function monthlyPeelAdjMetric(leftover, metric){
  if(metric) return [leftover, metric];
  const kept = [];
  let peeled = "";
  for(const tok of leftover){
    if(!peeled && MONTHLY_STORE_ADJ.some(mark => tok.includes(mark))){
      peeled = tok;
      continue;
    }
    kept.push(tok);
  }
  return [kept, peeled];
}

function monthlyIsStoreAdj(element, segment){
  const blob = String(segment || "") + String(element || "");
  return MONTHLY_STORE_ADJ.some(mark => blob.includes(mark));
}

function parseMonthlyLayers(element){
  let country="", channel="", scope="", metric="";
  let leftover = [];
  const unglued = monthlyUnglue(stripYoyFragments(element));
  for(const tok of unglued.split(" ").filter(Boolean)){
    if(MONTHLY_DROP.has(tok)) continue;
    if(tok in MONTHLY_STORE_FLOW){
      if(metric === "" || metric === "店舗数") metric = MONTHLY_STORE_FLOW[tok];
      continue;
    }
    const [peeled, rest] = monthlyPeelMetric(tok);
    if(peeled){
      const restFlow = rest && MONTHLY_STORE_FLOW[rest];
      if(restFlow){
        if(metric === "" || metric === "店舗数") metric = restFlow;
      }else if(!(peeled === "店舗数" && monthlyIsFlowMetric(metric))){
        metric = peeled;
        if(rest && !MONTHLY_DROP.has(rest)) leftover.push(rest);
      }else if(rest && !MONTHLY_DROP.has(rest)) leftover.push(rest);
      continue;
    }
    if(tok in MONTHLY_COUNTRY){ country = MONTHLY_COUNTRY[tok]; continue; }
    if(tok in MONTHLY_CHANNEL || tok === "ＦＣ"){ channel = MONTHLY_CHANNEL[tok] || "FC"; continue; }
    if(tok === "既存"){ scope = "既存"; continue; }
    if(tok === "全店"){ scope = "全店"; continue; }
    leftover.push(tok);
  }
  [leftover, metric] = monthlyStitchStoreFlow(leftover, metric);
  [leftover, metric] = monthlyStitchRealStore(leftover, metric);
  [leftover, metric] = monthlyStitchVsSales(leftover, metric);
  [leftover, metric] = monthlyPeelAdjMetric(leftover, metric);
  if(monthlyIsFlowMetric(metric)) scope = "";
  if(!scope && MONTHLY_FILL_ALL.has(metric)) scope = "全店";
  return {country, segment: leftover.join(" "), channel, scope, metric};
}

function normalizeElementTokens(element){
  const trimmed = String(element || "").trim().replace(/\s+/g, " ");
  if(!trimmed) return trimmed;
  const p = parseMonthlyLayers(trimmed);
  const built = [p.country, p.segment, p.channel, p.scope, p.metric].filter(Boolean).join(" ");
  return built || trimmed;
}

function monthlyRowSegment(element){
  const toks = parseMonthlyLayers(element).segment.split(" ").filter(Boolean);
  return toks.filter(tok => !MONTHLY_OPEN_QUALIFIERS.has(tok) && !MONTHLY_STORE_ADJ.some(mark => tok.includes(mark))).join(" ");
}

function monthlyRowMetric(element){
  const p = parseMonthlyLayers(element);
  if(p.metric.endsWith("出店")) return "open";
  if(p.metric.endsWith("退店")) return "close";
  if(monthlyIsStoreAdj(element, p.segment)) return "adj";
  if(p.metric === "店舗数") return "stores";
  if(p.metric === "客単価") return "spend";
  if(p.metric === "客数") return "customers";
  if(p.metric === "売上高") return "sales";
  const key = monthlyElementKey(element);
  if(MONTHLY_STORE_ADJ.some(mark => key.includes(mark))) return "adj";
  if(key.includes("店舗数")) return "stores";
  if(key.includes("客単価") || key.includes("単価")) return "spend";
  if(key.includes("客数") || key.includes("来客")) return "customers";
  if(key.includes("売上")) return "sales";
  return "other";
}

function monthlyRowSlot(element, unit){
  void unit;
  return MONTHLY_METRIC_RANK[monthlyRowMetric(element)];
}

function monthlyRowScopeRank(element){
  const p = parseMonthlyLayers(element);
  if(p.scope === "既存") return 1;
  if(p.scope === "全店") return 0;
  if(p.metric === "店舗数" || monthlyIsFlowMetric(p.metric)) return 3;
  const kind = monthlyRowMetric(element);
  if(kind === "stores" || kind === "open" || kind === "close" || kind === "adj") return 3;
  if(!p.segment) return 0;
  return 3;
}

function monthlyRowCountryRank(element){
  const c = parseMonthlyLayers(element).country;
  return c in MONTHLY_COUNTRY_RANK ? MONTHLY_COUNTRY_RANK[c] : 3;
}

function monthlyRowChannelRank(element){
  const c = parseMonthlyLayers(element).channel;
  return c in MONTHLY_CHANNEL_RANK ? MONTHLY_CHANNEL_RANK[c] : 3;
}

function isAllStoreSalesYoy(element, unit){
  const u = unit || "number";
  if(u!=="yoy_index" && u!=="yoy_pct") return false;
  const p = parseMonthlyLayers(element);
  return p.metric === "売上高" && p.scope === "全店" && !p.segment;
}

function monthlyRowOrder(tables){
  const years = (tables || []).filter(Boolean).slice().sort((a,b)=>(b.year||0)-(a.year||0));
  const seen = new Set();
  const items = [];
  const segIndex = {};
  let segN = 0;
  for(const d of years){
    for(const e of d.elements || []){
      const name = e.element;
      if(!name) continue;
      const unit = e.unit || "number";
      const display = fmtMonthlyElement(name, unit);
      if(seen.has(display)) continue;
      seen.add(display);
      const segment = monthlyRowSegment(display);
      if(!(segment in segIndex)) segIndex[segment] = segN++;
      items.push({
        element:display, unit, segment,
        country:monthlyRowCountryRank(display),
        scope:monthlyRowScopeRank(display),
        channel:monthlyRowChannelRank(display),
        slot:monthlyRowSlot(display, unit),
        idx:items.length
      });
    }
  }
  items.sort((a,b)=>{
    const ta = a.segment ? 1 : 0, tb = b.segment ? 1 : 0;
    if(ta!==tb) return ta - tb;
    if(a.country!==b.country) return a.country - b.country;
    if(a.segment){
      const s = segIndex[a.segment] - segIndex[b.segment];
      if(s) return s;
    }
    if(a.scope!==b.scope) return a.scope - b.scope;
    if(a.channel!==b.channel) return a.channel - b.channel;
    if(a.slot!==b.slot) return a.slot - b.slot;
    return a.idx - b.idx;
  });
  return items.map(e => ({element:e.element, unit:e.unit}));
}

function escHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;");
}

function monthlySeriesChoices(tables){
  return monthlyRowOrder(tables).map(e => {
    const id = fmtMonthlyElement(e.element, e.unit);
    return {
      id,
      label: id,
      yoy: e.unit==="yoy_index" || e.unit==="yoy_pct",
    };
  });
}

function pickDefaultMonthlySeries(tables){
  const rows = monthlyRowOrder(tables);
  for(const e of rows){
    if(!monthlyRowSegment(e.element) && isAllStoreSalesYoy(e.element, e.unit)){
      return fmtMonthlyElement(e.element, e.unit);
    }
  }
  for(const e of rows){
    if(isAllStoreSalesYoy(e.element, e.unit)) return fmtMonthlyElement(e.element, e.unit);
  }
  for(const e of rows){
    if(monthlySeriesKind(e.element, e.unit) === "all_store") return fmtMonthlyElement(e.element, e.unit);
  }
  return rows.length ? fmtMonthlyElement(rows[0].element, rows[0].unit) : "";
}

function monthlySeriesElement(d, seriesId){
  return (d.elements || []).find(e => fmtMonthlyElement(e.element, e.unit) === seriesId);
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
  if(monthlyGroup==="__new__") return issuers.filter(r => r.is_new);
  return issuers.filter(r => (r.industry || "その他") === monthlyGroup);
}

async function loadMonthlyList(){
  showMonthlyTop();
  const rows = await api("/api/v1/tables").catch(()=>null);
  if(!Array.isArray(rows)){
    $("#monthly-groups").innerHTML = "";
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    return;
  }
  monthlyRows = rows;
  const issuers = uniqueMonthlyIssuers(monthlyRows);
  if(!issuers.length){
    $("#monthly-groups").innerHTML = "";
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    return;
  }
  const groups = monthlyIndustries(issuers);
  if(monthlyGroup!=="__new__" && !groups.includes(monthlyGroup)) monthlyGroup = defaultMonthlyGroup(issuers);
  else if(monthlyGroup==="__new__" && !issuers.some(r => r.is_new) && groups.length) monthlyGroup = groups[0];
  renderMonthlyGroups(groups);
  renderMonthlyList();
}

function renderMonthlyGroups(groups){
  let h = `<button type="button" class="mchip${monthlyGroup==="__new__"?" on":""}" data-group="__new__" onclick="setMonthlyGroup('__new__')">新着</button>`;
  for(const g of groups){
    const on = monthlyGroup===g ? " on" : "";
    h += `<button type="button" class="mchip${on}" data-group="${g.replace(/"/g,"&quot;")}" onclick="setMonthlyGroup(this.dataset.group)">${g}</button>`;
  }
  $("#monthly-groups").innerHTML = h;
}

function setMonthlyGroup(g){
  monthlyGroup = g;
  renderMonthlyGroups(monthlyIndustries(uniqueMonthlyIssuers(monthlyRows)));
  renderMonthlyList();
}

function fmtMonthlyIssuerLabel(code, name){
  const short = Array.from(String(name || "")).slice(0, 5).join("");
  return `${code} ${short}`;
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
    $("#monthly-list").innerHTML = `<div class="sub">登録された月次データはありません</div>`;
    return;
  }
  let h = `<table id="monthly-tbl"><thead><tr>` +
    `<th class="l">会社名</th><th class="l">市場</th><th class="l">業種</th>` +
    `<th class="l">月次業種</th><th>全店(前年比)</th><th>既存店(前年比)</th>` +
    `</tr></thead><tbody>`;
  for(const r of issuers){
    const neu = r.ingested_today ? `<span class="badge new">NEW</span>` : "";
    const mkt = fmtMonthlyMarket(r.market);
    h += `<tr data-code="${r.code}">` +
      `<td class="l name" onclick="showMonthlyDetail('${r.code}')">${escHtml(fmtMonthlyIssuerLabel(r.code, r.name))}${neu}</td>` +
      `<td class="l ${mkt.cls}">${escHtml(mkt.label)}</td>` +
      `<td class="l sector">${escHtml(fmtMonthlySector(r.sector33))}</td>` +
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

function showMonthlyDetail(code){
  selMonthlyCode = code;
  const row = uniqueMonthlyIssuers(monthlyRows).find(r => r.code === code);
  if(!row){
    if(typeof writeRoute === "function") writeRoute("monthly", "");
    return;
  }
  const nm = row.name || "";
  $("#monthly-title").textContent = nm ? `${nm} ${row.code}` : row.code;
  monthlySeries = "";
  $("#monthly-top").style.display = "none";
  $("#monthly-detail").style.display = "";
  if(typeof writeRoute === "function") writeRoute("monthly", code);
  loadMonthlyTables();
}

function backMonthly(){
  showMonthlyTop();
  if(typeof writeRoute === "function") writeRoute("monthly", "");
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
  drawMonthlyChart(monthlyYearTables, monthlySeries);
  markMonthlySeriesRows();
}

function markMonthlySeriesRows(){
  document.querySelectorAll("#monthly-table-wrap tr[data-series]").forEach(tr =>
    tr.classList.toggle("on", tr.dataset.series === monthlySeries));
}

async function loadMonthlyTables(){
  if(!selMonthlyCode) return;
  const wrap = $("#monthly-table-wrap");
  const info = await api(`/api/v1/tables/${selMonthlyCode}`).catch(()=>null);
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
  const rowOrder = monthlyRowOrder(monthlyYearTables);
  renderMonthlySeriesSelect();
  let h = "";
  let maxNumLen = 0;
  for(const d of tables){
    if(!d) continue;
    h += `<table class="fin monthly"><colgroup><col class="monthly-name"><col class="monthly-m" span="12"></colgroup><thead><tr><th class="l year">${d.year}</th>`;
    for(let m=1;m<=12;m++) h += `<th>${m}月</th>`;
    h += `</tr></thead><tbody>`;
    const byName = {};
    for(const src of d.elements || []) byName[fmtMonthlyElement(src.element, src.unit)] = src;
    for(const e of rowOrder){
      const src = byName[e.element];
      const vals = (src && src.values) || {};
      if(!Object.keys(vals).length) continue;
      const unit = (src && src.unit) || e.unit || "number";
      const ds = ` data-series="${escHtml(e.element)}" onclick="setMonthlySeries(this.dataset.series)"`;
      h += `<tr${ds}><td class="l">${fmtMonthlyElement(e.element, unit)}</td>`;
      for(let m=1;m<=12;m++){
        const v = vals[String(m)];
        const txt = fmtMonthlyCell(v, unit);
        if(txt) maxNumLen = Math.max(maxNumLen, txt.length);
        h += `<td class="num">${txt}</td>`;
      }
      h += `</tr>`;
    }
    h += `</tbody></table>`;
  }
  wrap.style.setProperty("--monthly-m-w", `calc(${Math.max(maxNumLen, 3)}ch + 16px)`);
  wrap.innerHTML = h || `<div class="sub">データなし</div>`;
  drawMonthlyChart(monthlyYearTables, monthlySeries);
  markMonthlySeriesRows();
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
      let v = null;
      if(Number.isFinite(n)){
        v = el.unit==="yoy_pct" ? n+100 : n;
        any = true;
      }
      nums.push(v);
    }
    if(!any) continue;
    years.push(d.year);
    byYear[d.year] = nums;
  }
  years.sort((a,b)=>a-b);
  if(!years.length){
    host.innerHTML = `<div class="sub">データなし</div>`;
    return;
  }
  const vals = [];
  for(const y of years) for(const v of byYear[y]) if(v!=null) vals.push(v);
  let ymin = 0;
  let ymax = Math.max(...vals, spec.yoy ? 100 : 1);
  ymax = ymax * 1.08;
  // スマホは viewBox 720 を幅いっぱいに縮めると font-size 11 が読めない。1 CSS px ≒ 1 user unit にする
  const phone = monthlyIsPhone();
  const cssW = host.clientWidth || document.documentElement.clientWidth || 720;
  monthlyChartCssW = cssW;
  const W = phone ? Math.max(cssW, 280) : 720;
  const H = phone ? 280 : 232;
  const L = 52, R = phone ? 10 : 12, T = phone ? 18 : 14, B = phone ? 64 : 50;
  const fsAxis = phone ? 13 : 11;
  const plotW=W-L-R, plotH=H-T-B;
  const yx = v => T + (1 - (v-ymin)/((ymax-ymin)||1)) * plotH;
  const nY = years.length;
  const slot = plotW/12;
  const barW = Math.max(3, (slot*0.72)/nY);
  const monthY = T + plotH + (phone ? 18 : 14);
  const legendY = H - (phone ? 8 : 6);
  const legendGap = Math.min(phone ? 70 : 80, plotW / Math.max(nY, 1));
  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escHtml(spec.label)}">`;
  for(const tick of monthlyYTicks(ymin, ymax, spec.yoy)){
    const y = yx(tick);
    svg += `<line x1="${L}" x2="${W-R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#e8ecf0"/>`;
    svg += `<text x="${L-6}" y="${y.toFixed(1)}" font-size="${fsAxis}" text-anchor="end" dominant-baseline="middle" fill="#66707a">${fmtMonthlyTick(tick)}</text>`;
  }
  if(spec.yoy){
    const y100 = yx(100);
    svg += `<line x1="${L}" x2="${W-R}" y1="${y100}" y2="${y100}" stroke="#e6a100" stroke-dasharray="4 3"/>`;
  }
  years.forEach((year, yi) => {
    const color = monthlyYearColor(year);
    byYear[year].forEach((v, mi) => {
      if(v==null) return;
      const x = L + slot*mi + (slot-nY*barW)/2 + yi*barW;
      const y = yx(Math.max(v, ymin));
      const hgt = Math.max(1, yx(ymin) - y);
      svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${hgt.toFixed(1)}" fill="${color}"/>`;
    });
  });
  const monthLabel = slot < 28 ? (m => String(m)) : (m => `${m}月`);
  for(let m=1;m<=12;m++){
    const x = L + slot*(m-1) + slot/2;
    svg += `<text x="${x.toFixed(1)}" y="${monthY}" font-size="${fsAxis}" text-anchor="middle" fill="#66707a">${monthLabel(m)}</text>`;
  }
  years.forEach((year, yi) => {
    const color = monthlyYearColor(year);
    const x = L + yi*legendGap;
    svg += `<rect x="${x}" y="${legendY-10}" width="8" height="8" fill="${color}"/>`;
    svg += `<text x="${x+12}" y="${legendY}" font-size="${fsAxis}" fill="#66707a">${year}年</text>`;
  });
  svg += `</svg>`;
  host.innerHTML = svg;
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

function fmtMonthlyCell(v, unit){
  if(v==null || v==="") return "-";
  let n = Number(v);
  if(!Number.isFinite(n)) return "-";
  if(unit==="yoy_pct") n += 100;
  return n.toLocaleString("ja-JP",{maximumFractionDigits:4});
}

function stripYoyFragments(name){
  const frags = ["前年同月比","前年同月","(前年比)","（前年比）","前年比"];
  let s = String(name || "").trim();
  let prev;
  do {
    prev = s;
    for(const frag of frags) s = s.split(frag).join("");
    s = s.replace(/\s+/g, " ").trim();
  } while(s !== prev);
  return s;
}

function fmtMonthlyElement(name, unit){
  const trimmed = String(name || "").trim();
  const u = unit || "number";
  const isYoy = u === "yoy_index" || u === "yoy_pct";
  const base = normalizeElementTokens(isYoy ? stripYoyFragments(trimmed) : trimmed);
  if(!isYoy) return base;
  if(base.endsWith("(前年比)")) return base;
  return base + "(前年比)";
}

window.setMonthlyGroup = setMonthlyGroup;
window.showMonthlyDetail = showMonthlyDetail;
window.backMonthly = backMonthly;
window.setMonthlySeries = setMonthlySeries;

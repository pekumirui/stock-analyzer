const TABS = ["earnings", "chart", "screener", "pts", "monthly", "supply"];
const UI_ROUTE_KEY = "ui.route";
const CODE_RE = /^[0-9][0-9A-Z]{3}[0-9]$/;
const SUPPLY_ID_RE = /^[a-z][a-z0-9_]*$/;

// 2 段目: chart / monthly は銘柄コード、supply は素材 id
function routeArg(tab, raw){
  if(tab === "chart" || tab === "monthly") return CODE_RE.test(raw || "") ? raw : "";
  if(tab === "supply") return SUPPLY_ID_RE.test(raw || "") ? raw : "";
  return "";
}

function parseRoute(raw){
  const s = String(raw || "").replace(/^#/, "").trim();
  const [tab, rest] = s.split("/");
  if(!TABS.includes(tab)) return {tab: "earnings", code: ""};
  return {tab, code: routeArg(tab, rest)};
}

function currentRoute(){
  if(location.hash && location.hash.length > 1) return parseRoute(location.hash);
  try { return parseRoute(localStorage.getItem(UI_ROUTE_KEY) || ""); }
  catch(e){ return {tab: "earnings", code: ""}; }
}

function writeRoute(tab, code){
  if(!TABS.includes(tab)) tab = "earnings";
  const useCode = routeArg(tab, code);
  const path = useCode ? `${tab}/${useCode}` : tab;
  const hash = "#" + path;
  if(location.hash !== hash) history.replaceState(null, "", hash);
  try { localStorage.setItem(UI_ROUTE_KEY, path); } catch(e) {}
}

function showTab(tab){
  const b = document.querySelector(`nav button[data-tab="${tab}"]`);
  if(!b) return;
  document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  TABS.forEach(t => $("#tab-" + t).style.display = t === tab ? "" : "none");
}

function selectTab(tab){
  showTab(tab);
  writeRoute(tab, tab === "chart" ? (curCode || "") : "");
  if(tab === "screener" && !$("#sc-tbl").innerHTML) loadScreener();
  if(tab === "pts" && !$("#pts-tbl").innerHTML) loadPts();
  if(tab === "monthly") loadMonthlyList();
  if(tab === "supply"){
    writeRoute("supply", supplySelectedId());
    loadSupply();
  }
}

async function restoreRoute(){
  const {tab, code} = currentRoute();
  showTab(tab);
  if(tab === "screener"){
    loadScreener();
    writeRoute("screener");
    return;
  }
  if(tab === "pts"){
    loadPts();
    writeRoute("pts");
    return;
  }
  if(tab === "chart"){
    if(code){
      curCode = code;
      if($("#sym")) $("#sym").value = code;
      loadChart();
    } else writeRoute("chart");
    return;
  }
  if(tab === "supply"){
    writeRoute("supply", code);
    loadSupply(code);
    return;
  }
  if(tab === "monthly"){
    await loadMonthlyList();
    if(code) showMonthlyDetail(code);
    else writeRoute("monthly");
    return;
  }
  writeRoute("earnings");
}

document.querySelectorAll("nav button").forEach(b => b.onclick = () => selectTab(b.dataset.tab));
window.writeRoute = writeRoute;
window.restoreRoute = restoreRoute;

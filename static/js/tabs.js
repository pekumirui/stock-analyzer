const TABS = ["earnings", "chart", "screener", "monthly"];
const UI_ROUTE_KEY = "ui.route";
const CODE_RE = /^[0-9][0-9A-Z]{3}[0-9]$/;

function parseRoute(raw){
  const s = String(raw || "").replace(/^#/, "").trim();
  const [tab, rest] = s.split("/");
  const code = CODE_RE.test(rest || "") ? rest : "";
  if(!TABS.includes(tab)) return {tab: "earnings", code: ""};
  if(tab !== "chart" && tab !== "monthly") return {tab, code: ""};
  return {tab, code};
}

function currentRoute(){
  if(location.hash && location.hash.length > 1) return parseRoute(location.hash);
  try { return parseRoute(localStorage.getItem(UI_ROUTE_KEY) || ""); }
  catch(e){ return {tab: "earnings", code: ""}; }
}

function writeRoute(tab, code){
  if(!TABS.includes(tab)) tab = "earnings";
  const useCode = (tab === "chart" || tab === "monthly") && CODE_RE.test(code || "") ? code : "";
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
  if(tab === "monthly") loadMonthlyList();
}

async function restoreRoute(){
  const {tab, code} = currentRoute();
  showTab(tab);
  if(tab === "screener"){
    loadScreener();
    writeRoute("screener");
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

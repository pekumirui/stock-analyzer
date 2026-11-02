const TABS = ["earnings", "stock", "screener", "monthly", "supply", "ranking", "holders"];
const UI_ROUTE_KEY = "ui.route";
const CODE_RE = /^[0-9][0-9A-Z]{3}[0-9]$/;
const SUPPLY_ID_RE = /^[a-z][a-z0-9_]*$/;
const EDINET_RE = /^E[0-9]{5}$/;
// 銘柄タブは {code5}/{中のタブ}、ニュースの詳細は {code5}/news/{news_id}（docs/DESIGN_stock.md）
const STOCK_SUBS = ["summary", "price", "news", "holders"];
const NEWS_ID_RE = /^[a-z][0-9A-Za-z]{1,40}$/;
// ランキングの中の PTS。既定（決算後パフォーマンス）はルートに出さない
const RANKING_SUBS = ["pts"];

function stockRouteArg(raw){
  const [code, sub, id] = String(raw || "").split("/");
  if(!CODE_RE.test(code || "")) return "";
  if(!STOCK_SUBS.includes(sub)) return code;
  if(sub === "news" && NEWS_ID_RE.test(id || "")) return `${code}/news/${id}`;
  return `${code}/${sub}`;
}

function routeArg(tab, raw){
  if(tab === "stock") return stockRouteArg(raw);
  if(tab === "monthly") return CODE_RE.test(raw || "") ? raw : "";
  if(tab === "supply") return SUPPLY_ID_RE.test(raw || "") ? raw : "";
  if(tab === "ranking"){
    const sub = String(raw || "").split("/")[0];
    return RANKING_SUBS.includes(sub) ? sub : "";
  }
  if(tab === "holders") return EDINET_RE.test(raw || "") ? raw : "";
  return "";
}

function parseRoute(raw){
  const {tab, rest, from} = splitRouteString(raw);
  // チャートタブは銘柄の株価タブになった（KAB-181）。古いハッシュと ui.route を読み替える
  if(tab === "chart"){
    const code = CODE_RE.test(rest) ? rest : "";
    return {tab: "stock", code: code ? `${code}/price` : "", from};
  }
  // PTS タブはランキングの中になった（KAB-265）。古い #pts と ui.route を読み替える
  if(tab === "pts"){
    return {tab: "ranking", code: "pts", from};
  }
  if(!TABS.includes(tab)) return {tab: "earnings", code: "", from: ""};
  return {tab, code: routeArg(tab, rest), from};
}

function currentRoute(){
  if(location.hash && location.hash.length > 1) return parseRoute(location.hash);
  try { return parseRoute(localStorage.getItem(UI_ROUTE_KEY) || ""); }
  catch(e){ return {tab: "earnings", code: "", from: ""}; }
}

function currentScreenId(){
  const r = currentRoute();
  return screenIdFromTabCode(r.tab, r.code);
}

function writeRoute(tab, code, from, push){
  if(tab === "pts"){ tab = "ranking"; code = "pts"; }
  if(!TABS.includes(tab)) tab = "earnings";
  const useCode = routeArg(tab, code);
  const path = useCode ? `${tab}/${useCode}` : tab;
  const screen = screenIdFromTabCode(tab, useCode);
  const incoming = from === undefined
    ? ((parseRoute(location.hash).from || new URLSearchParams(location.search).get("from") || ""))
    : (from || "");
  const fromId = resolveFrom(screen, incoming);
  const hash = fromId ? `#${path}?from=${encodeURIComponent(fromId)}` : `#${path}`;
  const q = new URLSearchParams(location.search);
  q.delete("from");
  const search = q.toString();
  const next = location.pathname + (search ? "?" + search : "") + hash;
  const cur = location.pathname + location.search + location.hash;
  if(cur !== next){
    if(push) history.pushState(null, "", next);
    else history.replaceState(null, "", next);
  }
  try { localStorage.setItem(UI_ROUTE_KEY, path); } catch(e) {}
  if(typeof renderBreadcrumbs === "function") renderBreadcrumbs();
}

function showTab(tab){
  const b = document.querySelector(`nav button[data-tab="${tab}"]`);
  if(!b) return;
  document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  $("#cur-tab").textContent = b.textContent;
  TABS.forEach(t => $("#tab-" + t).style.display = t === tab ? "" : "none");
}

function selectTab(tab){
  showTab(tab);
  if(tab === "ranking"){
    showRankingSub();
    return;
  }
  writeRoute(tab, tab === "stock" ? stockRoutePath() : "");
  if(tab === "screener" && !$("#sc-tbl").innerHTML) loadScreener();
  if(tab === "monthly") loadMonthlyList();
  if(tab === "supply"){
    writeRoute("supply", supplySelectedId());
    loadSupply();
  }
  if(tab === "holders") loadHoldersHome();
}

async function restoreRoute(){
  const {tab, code} = currentRoute();
  showTab(tab);
  if(tab === "screener"){
    loadScreener();
    writeRoute("screener");
    return;
  }
  if(tab === "ranking"){
    showRankingSub(code === "pts" ? "pts" : "perf", {force: true});
    return;
  }
  if(tab === "stock"){
    if(code) openStockRoute(code);
    else writeRoute("stock");
    return;
  }
  if(tab === "supply"){
    writeRoute("supply", code);
    loadSupply(code);
    return;
  }
  if(tab === "monthly"){
    await loadMonthlyList();
    if(code) showMonthlyDetail(code, {keepFrom: true});
    else writeRoute("monthly");
    return;
  }
  if(tab === "holders"){
    if(code) openHolder(code);
    else { writeRoute("holders"); loadHoldersHome(); }
    return;
  }
  writeRoute("earnings");
}

// スマホのドロワー（三本線で左から出す）。PC では nav が常に出ているので開け閉めは見た目に効かない
const MOBILE_NAV_MQ = "(max-width: 720px)";
function mobileNav(){
  return window.matchMedia(MOBILE_NAV_MQ).matches;
}
function setMenu(open){
  const nav = $("#app-nav");
  const bd = $("#nav-backdrop");
  if(open){
    if(mobileNav()) nav.hidden = false;
    void nav.offsetWidth;
    nav.classList.add("open");
    bd.hidden = false;
  } else {
    nav.classList.remove("open");
    bd.hidden = true;
    if(mobileNav()){
      const hideNav = ev => {
        if(ev && ev.target !== nav) return;
        if(ev && ev.propertyName && ev.propertyName !== "transform") return;
        nav.removeEventListener("transitionend", hideNav);
        if(!nav.classList.contains("open") && mobileNav()) nav.hidden = true;
      };
      nav.addEventListener("transitionend", hideNav);
      setTimeout(hideNav, 280);
    } else {
      nav.hidden = false;
    }
  }
  $("#menu-btn").setAttribute("aria-expanded", String(open));
}
if(mobileNav()) $("#app-nav").hidden = true;
window.matchMedia(MOBILE_NAV_MQ).addEventListener("change", e => {
  if(!e.matches){
    $("#app-nav").hidden = false;
    $("#app-nav").classList.remove("open");
    $("#nav-backdrop").hidden = true;
    $("#menu-btn").setAttribute("aria-expanded", "false");
  } else if(!$("#app-nav").classList.contains("open")){
    $("#app-nav").hidden = true;
  }
});

document.querySelectorAll("nav button").forEach(b => b.onclick = () => { setMenu(false); selectTab(b.dataset.tab); });
$("#menu-btn").onclick = () => setMenu(!$("#app-nav").classList.contains("open"));
$("#nav-backdrop").onclick = () => setMenu(false);
document.addEventListener("keydown", e => { if(e.key === "Escape") setMenu(false); });
// ドロワーを左へスワイプで閉じる
{
  let x0 = null;
  const nav = $("#app-nav");
  nav.addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, {passive: true});
  nav.addEventListener("touchend", e => {
    if(x0 !== null && e.changedTouches[0].clientX - x0 < -50) setMenu(false);
    x0 = null;
  });
}
window.writeRoute = writeRoute;
window.restoreRoute = restoreRoute;
window.currentScreenId = currentScreenId;
window.parseRoute = parseRoute;

const crumbsNav = document.getElementById("crumbs");
if(crumbsNav){
  crumbsNav.addEventListener("click", ev => {
    const a = ev.target.closest("a[href^='#']");
    if(!a || document.body.getAttribute("data-public-monthly") === "1") return;
    ev.preventDefault();
    const href = a.getAttribute("href");
    if(location.hash !== href) history.replaceState(null, "", href);
    restoreRoute();
  });
}

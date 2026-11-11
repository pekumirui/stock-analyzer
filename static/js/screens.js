// 画面のルート定義とパンくず（KAB-276）。
// 正本は ROUTES。BREADCRUMBS は全 id 必須。tabs.js が照合し、tests/nonfunctional/test_breadcrumbs.py が列挙する。
const ROUTES = JSON.parse(String.raw`[
  {"id":"earnings","tab":"earnings","path":""},
  {"id":"stock","tab":"stock","path":""},
  {"id":"stock-summary","tab":"stock","path":":code"},
  {"id":"stock-summary","tab":"stock","path":":code/summary"},
  {"id":"stock-price","tab":"stock","path":":code/price"},
  {"id":"stock-financials","tab":"stock","path":":code/financials"},
  {"id":"stock-news","tab":"stock","path":":code/news"},
  {"id":"stock-news-detail","tab":"stock","path":":code/news/:newsId"},
  {"id":"stock-holders","tab":"stock","path":":code/holders"},
  {"id":"stock-deck-detail","tab":"stock","path":":code/decks/:deckKey"},
  {"id":"screener","tab":"screener","path":""},
  {"id":"monthly","tab":"monthly","path":""},
  {"id":"monthly-detail","tab":"monthly","path":":code"},
  {"id":"supply","tab":"supply","path":""},
  {"id":"supply","tab":"supply","path":":id?"},
  {"id":"ranking","tab":"ranking","path":""},
  {"id":"ranking-pts","tab":"ranking","path":"pts"},
  {"id":"holders","tab":"holders","path":""},
  {"id":"holders-detail","tab":"holders","path":":edinet"}
]`);

const BREADCRUMBS = JSON.parse(String.raw`{
  "earnings": {"label":"決算速報","defaultFrom":null,"from":[]},
  "stock": {"label":"銘柄","defaultFrom":null,"from":[]},
  "stock-summary": {"label":"概要","defaultFrom":"stock","from":["stock","stock-price","stock-financials","stock-news","stock-holders"]},
  "stock-price": {"label":"株価","defaultFrom":"stock","from":["stock","stock-summary","stock-financials","stock-news","stock-holders","earnings","screener","ranking","ranking-pts","supply","holders","holders-detail"]},
  "stock-financials": {"label":"業績詳細","defaultFrom":"stock","from":["stock","stock-summary","stock-price","stock-news","stock-holders"]},
  "stock-news": {"label":"ニュース","defaultFrom":"stock","from":["stock","stock-summary","stock-price","stock-financials","stock-holders","stock-news-detail","stock-deck-detail"]},
  "stock-news-detail": {"label":"ニュース詳細","defaultFrom":"stock-news","from":["stock-news"]},
  "stock-holders": {"label":"株主","defaultFrom":"stock","from":["stock","stock-summary","stock-price","stock-financials","stock-news"]},
  "stock-deck-detail": {"label":"説明資料の中身","defaultFrom":"stock-news","from":["stock-news"]},
  "screener": {"label":"スクリーナー","defaultFrom":null,"from":[]},
  "monthly": {"label":"月次","defaultFrom":null,"from":[]},
  "monthly-detail": {"label":"月次詳細","defaultFrom":"monthly","from":["monthly"]},
  "supply": {"label":"サプライ","defaultFrom":null,"from":[]},
  "ranking": {"label":"ランキング","defaultFrom":null,"from":["ranking-pts"]},
  "ranking-pts": {"label":"PTS","defaultFrom":"ranking","from":["ranking"]},
  "holders": {"label":"大量保有報告書","defaultFrom":null,"from":[]},
  "holders-detail": {"label":"保有者","defaultFrom":"holders","from":["holders","stock-holders"]}
}`);

function splitRouteString(raw){
  const s = String(raw || "").replace(/^#/, "").trim();
  const qi = s.indexOf("?");
  const path = qi < 0 ? s : s.slice(0, qi);
  const search = qi < 0 ? "" : s.slice(qi + 1);
  const from = new URLSearchParams(search).get("from") || "";
  const i = path.indexOf("/");
  return {
    path,
    from,
    tab: i < 0 ? path : path.slice(0, i),
    rest: i < 0 ? "" : path.slice(i + 1),
  };
}

function matchRoutePath(pattern, rest){
  let pat = String(pattern || "");
  const val = String(rest || "");
  if(pat.endsWith("?")){
    pat = pat.slice(0, -1);
    if(!val) return true;
  }
  const pp = pat ? pat.split("/") : [];
  const rr = val ? val.split("/") : [];
  if(pp.length !== rr.length) return false;
  for(let i = 0; i < pp.length; i++){
    if(pp[i].charAt(0) === ":"){
      if(!rr[i]) return false;
    }else if(pp[i] !== rr[i]) return false;
  }
  return true;
}

function screenIdFromTabCode(tab, code){
  const rest = String(code || "");
  const found = ROUTES.filter(r => r.tab === tab)
    .sort((a, b) => String(b.path).length - String(a.path).length);
  for(const r of found){
    if(matchRoutePath(r.path, rest)) return r.id;
  }
  return tab;
}

function resolveFrom(screenId, fromId){
  const cfg = BREADCRUMBS[screenId];
  if(!cfg || !fromId) return "";
  return (cfg.from || []).indexOf(fromId) >= 0 ? fromId : "";
}

function defaultTrail(screenId, seen){
  const top = {id: "top", label: "トップ"};
  const cfg = BREADCRUMBS[screenId];
  if(!cfg) return [top];
  seen = seen || {};
  if(seen[screenId]) return [top, {id: screenId, label: cfg.label}];
  seen[screenId] = true;
  const prefix = cfg.defaultFrom ? defaultTrail(cfg.defaultFrom, seen) : [top];
  return prefix.concat([{id: screenId, label: cfg.label}]);
}

function breadcrumbTrail(screenId, fromId){
  const top = {id: "top", label: "トップ"};
  const cfg = BREADCRUMBS[screenId];
  if(!cfg) return [top];
  const src = resolveFrom(screenId, fromId) || cfg.defaultFrom;
  if(!src) return [top, {id: screenId, label: cfg.label}];
  return defaultTrail(src).concat([{id: screenId, label: cfg.label}]);
}

function routeHref(id, ctx){
  const p = ctx || {};
  if(id === "top") return p.public ? "/monthly" : "#earnings";
  const rows = ROUTES.filter(r => r.id === id).sort((a, b) => String(b.path).length - String(a.path).length);
  const r = rows[0];
  if(!r) return p.public ? "/monthly" : "#earnings";
  if(p.public){
    if(id === "monthly" || id === "earnings") return "/monthly";
    if(id === "monthly-detail" && p.code) return "/monthly/" + String(p.code).slice(0, 4);
    return "/monthly";
  }
  let rest = String(r.path || "").replace(/\?$/, "");
  const fill = {code: p.code, newsId: p.newsId, deckKey: p.deckKey, edinet: p.edinet, id: p.supplyId};
  rest = rest.replace(/:(code|newsId|deckKey|edinet|id)/g, (_, k) => fill[k] || "");
  if(!rest) return "#" + r.tab;
  if(rest.indexOf(":") >= 0 || /(^|\/)$/.test(rest) || rest.indexOf("//") >= 0) return "#" + r.tab;
  return "#" + r.tab + "/" + rest;
}

function crumbEscape(s){
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function currentCrumbContext(){
  const pub = typeof document !== "undefined" && document.body && document.body.getAttribute("data-public-monthly") === "1";
  const searchFrom = (typeof location !== "undefined" && new URLSearchParams(location.search).get("from")) || "";
  if(pub){
    const m = String(location.pathname || "").match(/^\/monthly\/([0-9][0-9A-Z]{3})/i);
    return {
      public: true,
      screenId: m ? "monthly-detail" : "monthly",
      fromId: searchFrom,
      code: m ? m[1].toUpperCase() : "",
    };
  }
  const hash = typeof location !== "undefined" ? location.hash : "";
  const parsed = typeof parseRoute === "function"
    ? parseRoute(hash)
    : {tab: splitRouteString(hash).tab, code: splitRouteString(hash).rest, from: splitRouteString(hash).from};
  const parts = String(parsed.code || "").split("/");
  return {
    public: false,
    screenId: screenIdFromTabCode(parsed.tab, parsed.code),
    fromId: parsed.from || searchFrom,
    code: parts[0] || "",
    newsId: parsed.tab === "stock" && parts[1] === "news" ? (parts[2] || "") : "",
    deckKey: parsed.tab === "stock" && parts[1] === "decks" ? (parts[2] || "") : "",
    edinet: parsed.tab === "holders" ? (parts[0] || "") : "",
    supplyId: parsed.tab === "supply" ? (parts[0] || "") : "",
  };
}

function pinCrumbsEnd(host){
  if(!host || host.hidden) return;
  host.scrollLeft = host.scrollWidth;
}

function renderBreadcrumbs(){
  if(typeof document === "undefined") return;
  const host = document.getElementById("crumbs");
  if(!host) return;
  const ctx = currentCrumbContext();
  const trail = breadcrumbTrail(ctx.screenId, ctx.fromId);
  if(!trail.length){
    host.innerHTML = "";
    host.hidden = true;
    return;
  }
  const last = trail.length - 1;
  let html = "<ol>";
  for(let i = 0; i < trail.length; i++){
    const c = trail[i];
    if(i === last){
      html += `<li><span aria-current="page">${crumbEscape(c.label)}</span></li>`;
    }else{
      html += `<li><a href="${crumbEscape(routeHref(c.id, ctx))}">${crumbEscape(c.label)}</a></li>`;
    }
  }
  host.innerHTML = html + "</ol>";
  host.hidden = false;
  requestAnimationFrame(() => pinCrumbsEnd(host));
}

if(typeof window !== "undefined"){
  window.ROUTES = ROUTES;
  window.BREADCRUMBS = BREADCRUMBS;
  window.splitRouteString = splitRouteString;
  window.screenIdFromTabCode = screenIdFromTabCode;
  window.resolveFrom = resolveFrom;
  window.breadcrumbTrail = breadcrumbTrail;
  window.routeHref = routeHref;
  window.renderBreadcrumbs = renderBreadcrumbs;
  window.addEventListener("resize", () => pinCrumbsEnd(document.getElementById("crumbs")));
}
if(typeof module !== "undefined" && module.exports){
  module.exports = {
    ROUTES, BREADCRUMBS, splitRouteString, matchRoutePath, screenIdFromTabCode,
    resolveFrom, defaultTrail, breadcrumbTrail, routeHref,
  };
}

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

const SCREEN_LABELS = JSON.parse(String.raw`{
  "earnings":"決算速報",
  "stock":"銘柄",
  "stock-summary":"概要",
  "stock-price":"株価",
  "stock-financials":"業績詳細",
  "stock-news":"ニュース",
  "stock-news-detail":"ニュース詳細",
  "stock-holders":"株主",
  "stock-deck-detail":"説明資料の中身",
  "screener":"スクリーナー",
  "monthly":"月次",
  "monthly-detail":"月次詳細",
  "supply":"サプライ",
  "ranking":"ランキング",
  "ranking-pts":"PTS",
  "holders":"大量保有報告書",
  "holders-detail":"保有者"
}`);

function splitRouteString(raw){
  const s = String(raw || "").replace(/^#/, "").trim();
  const qi = s.indexOf("?");
  const path = qi < 0 ? s : s.slice(0, qi);
  const i = path.indexOf("/");
  return {
    path,
    tab: i < 0 ? path : path.slice(0, i),
    rest: i < 0 ? "" : path.slice(i + 1),
  };
}

if(typeof window !== "undefined") window.splitRouteString = splitRouteString;
if(typeof module !== "undefined" && module.exports) module.exports = {ROUTES, SCREEN_LABELS, splitRouteString};

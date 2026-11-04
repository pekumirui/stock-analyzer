// 銘柄タブ（KAB-161 / KAB-177。docs/DESIGN_stock.md）。共通ヘッダーと中のタブ 概要 / 株価 / ニュース / 株主 / 説明資料（stock-deck.js）。
// ニュースの詳細で決算短信の原文を PDF と同じ並びで読む
let stockCode = "";
let stockSub = "summary";
let stockNewsId = "";
let stockOverview = null;
let stockFilings = null;   // null = まだ読んでいない
let stockFilingsReq = null;
let stockDocKey = "";
let stockAc = null;

const STOCK_MAIN_DOC_TYPES = ["annual", "semiannual", "quarterly"];
// ニュースの id。XBRL の回は x{xbrl_filings.id}、PDF から変換した回（KAB-164）は p{TDnet の docID}
const stockNewsKey = f => f.source === "pdf" ? `p${f.disclosure_no}` : `x${f.id}`;
const stockEsc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function stockSuggest(rs){
  const box = $("#st-suggest");
  box.innerHTML = "";
  for(const s of rs){
    const d = document.createElement("div");
    d.textContent = `${s.code.slice(0, 4)} ${s.name}`;
    d.onclick = () => stockPick(s);
    box.appendChild(d);
  }
  box.style.display = rs.length ? "block" : "none";
  return rs;
}

let stockLastSuggest = [];
$("#st-sym").addEventListener("input", async e => {
  const q = e.target.value.trim();
  if(!q){ stockSuggest([]); stockLastSuggest = []; return; }
  const rs = await api(`/api/stocks/search?q=${encodeURIComponent(q)}`).catch(() => []);
  if(e.target.value.trim() !== q) return;
  stockLastSuggest = stockSuggest(rs);
});
$("#st-sym").addEventListener("keydown", e => {
  if(e.key === "Enter" && stockLastSuggest.length) stockPick(stockLastSuggest[0]);
  if(e.key === "Escape") stockSuggest([]);
});
document.addEventListener("click", e => {
  if(!e.target.closest("#st-search")) $("#st-suggest").style.display = "none";
});

function stockPick(s){
  stockSuggest([]);
  stockLastSuggest = [];
  $("#st-sym").value = s.name;
  loadStock(s.code);
}

function stockRoutePath(){
  if(!stockCode) return "";
  if(stockSub === "news" && stockNewsId) return `${stockCode}/news/${stockNewsId}`;
  if(stockSub === "decks" && stockDeckKey) return `${stockCode}/decks/${stockDeckKey}`;
  return `${stockCode}/${stockSub}`;
}

// ルート（{code5} / {code5}/{sub} / {code5}/news/{id} / {code5}/decks/{key}。tabs.js で形は確かめ済み）を開く
function openStockRoute(path){
  const [code, sub, id] = String(path).split("/");
  showStock(code, sub || "summary", sub === "news" ? (id || "") : "", undefined, sub === "decks" ? (id || "") : "");
}

// 銘柄を選び直す。中のタブはそのまま（ニュースの詳細にいたら一覧へ）
function loadStock(code){
  showStock(code, stockSub, "", "stock");
}

function stockMsg(text){
  const m = $("#st-msg");
  m.textContent = text || "";
  m.hidden = !text;
}

async function showStock(code, sub, newsId, from, deckKey = ""){
  const changed = code !== stockCode || !stockOverview;
  stockCode = code;
  stockSub = sub;
  stockNewsId = newsId;
  if(changed) resetStockDecks();
  stockDeckKey = deckKey;
  writeRoute("stock", stockRoutePath(), from);
  if(changed){
    if(stockAc) stockAc.abort();
    stockAc = new AbortController();
    stockOverview = null;
    stockFilings = null;
    stockFilingsReq = null;
    if(typeof resetStockHolders === "function") resetStockHolders();
    stockDocKey = "";
    $("#st-doc").innerHTML = "";
    $("#st-head").hidden = true;
    document.querySelectorAll(".st-sub").forEach(x => x.hidden = true);
    stockMsg("");
    let d;
    try {
      d = await api(`/api/stock/${code}/overview`, {signal: stockAc.signal});
    } catch(e){
      if(e.name === "AbortError") return;
      stockMsg(String(e.message) === "404" ? "銘柄が見つかりません。" : "読み込めませんでした。");
      return;
    }
    stockOverview = d;
    renderStockHead(d);
  }
  renderStockSub();
}

function renderStockHead(d){
  const t = $("#st-title");
  t.innerHTML = "";
  const b = document.createElement("b");
  b.textContent = d.name || d.code;
  const small = document.createElement("small");
  small.textContent = ` ${d.code.slice(0, 4)}` + [d.market, d.sector33].filter(Boolean).map(x => ` / ${x}`).join("");
  t.append(b, small);
  // ほかのタブから銘柄が変わることもあるので、入力も選んだ銘柄名にそろえる
  $("#st-sym").value = d.name || "";

  const q = d.quote;
  let h = "";
  if(q){
    const dec = Number.isInteger(q.close) ? 0 : 1;
    h += `<span class="px">${fmt(q.close, dec)}</span>`;
    if(q.change_pct != null){
      const cls = q.change_pct > 0 ? "pos" : q.change_pct < 0 ? "neg" : "";
      const sign = q.change > 0 ? "+" : "";
      h += `<span class="${cls}">${sign}${fmt(q.change, dec)} (${sign}${fmt(q.change_pct * 100, 2)}%)</span>`;
    }
    h += `<span class="sub">${q.date.slice(5).replace("-", "/")} 終値</span>`;
    if(q.market_cap != null) h += `<span>時価総額 ${fmt(q.market_cap / 100)}億円</span>`;
  } else {
    h += `<span class="sub">株価データなし</span>`;
  }
  const ne = d.next_earnings;
  if(ne) h += `<span>次回決算 ${ne.date ? ne.date.slice(5).replace("-", "/") : "未定"}</span>`;
  $("#st-quote").innerHTML = h;
  $("#st-head").hidden = false;
}

document.querySelectorAll("#st-tabs .ftab").forEach(b => b.onclick = () => {
  if(!stockCode) return;
  if(b.dataset.sub === stockSub && !(stockSub === "news" && stockNewsId) && !(stockSub === "decks" && stockDeckKey)) return;
  const from = typeof currentScreenId === "function" ? currentScreenId() : "";
  stockSub = b.dataset.sub;
  stockNewsId = "";
  stockDeckKey = "";
  writeRoute("stock", stockRoutePath(), from);
  renderStockSub();
});

function renderStockSub(){
  if(!stockOverview) return;
  document.querySelectorAll("#st-tabs .ftab").forEach(b => b.classList.toggle("on", b.dataset.sub === stockSub));
  document.querySelectorAll(".st-sub").forEach(x => x.hidden = x.id !== `st-sub-${stockSub}`);
  stockMsg("");
  if(stockSub === "summary") renderStockSummary();
  if(stockSub === "price") showStockPrice();
  if(stockSub === "news") renderStockNews();
  if(stockSub === "holders") renderStockHoldersTab();
  if(stockSub === "decks") renderStockDecks();
}

function renderStockSummary(){
  const m = stockOverview.metrics || {};
  const val = (v, f) => v == null ? "–" : f(v);
  const items = [
    ["PER（予想）", val(m.per, v => `${fmt(v, 1)}倍`)],
    ["PBR（実績）", val(m.pbr, v => `${fmt(v, 2)}倍`)],
    ["配当利回り（予想）", val(m.dividend_yield, v => `${fmt(v * 100, 2)}%`)],
    ["ROE（実績）", val(m.roe, v => `${fmt(v * 100, 1)}%`)],
    ["自己資本比率", val(m.equity_ratio, v => `${fmt(v * 100, 1)}%`)],
  ];
  $("#st-metrics").innerHTML = items.map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
  loadStockFin();
}

// 業績は決算速報の決算表と同じ表（earnings.js の renderFin / renderProg / renderRev）。最新の実績開示で描く
let stockFinCode = "";
async function loadStockFin(){
  const code = stockCode;
  if(stockFinCode === code) return;
  stockFinCode = code;
  const tbls = ["#st-cum-tbl", "#st-q-tbl", "#st-prog-tbl", "#st-rev-tbl"];
  tbls.forEach(s => { $(s).innerHTML = ""; });
  let d = null;
  try {
    d = await api(`/api/earnings/detail/${code}`, {signal: stockAc.signal});
  } catch(e){
    if(e.name === "AbortError") return;
    if(code !== stockCode) return;
    if(String(e.message) !== "404"){
      stockFinCode = "";
      tbls.forEach(s => { $(s).innerHTML = `<tr><td class="l sub">読み込めませんでした。</td></tr>`; });
      return;
    }
  }
  if(code !== stockCode) return;
  renderFin($("#st-cum-tbl"), d && d.cumulative, d);
  renderFin($("#st-q-tbl"), d && d.quarterly, d);
  renderProg($("#st-prog-tbl"), d && d.progress, d);
  renderRev($("#st-rev-tbl"), d && d.revisions, d);
}

document.querySelectorAll("#st-fin-tabs .ftab").forEach(b => b.onclick = () => {
  document.querySelectorAll("#st-fin-tabs .ftab").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll("[data-stfin-wrap]").forEach(w => { w.hidden = w.dataset.stfinWrap !== b.dataset.stfin; });
});

// ニュース（KAB-178 では短信の一覧 /filings の決算・修正だけ。ほかの種類は KAB-180）
function loadStockFilings(){
  if(stockFilings) return Promise.resolve(stockFilings);
  if(stockFilingsReq) return stockFilingsReq;
  const code = stockCode;
  stockFilingsReq = api(`/api/stock/${code}/filings`, {signal: stockAc.signal}).then(d => {
    if(code === stockCode) stockFilings = d.filings;
    return d.filings;
  }).finally(() => { if(code === stockCode) stockFilingsReq = null; });
  return stockFilingsReq;
}

const stockAt = s => (s || "").slice(2).replace(/-/g, "/");
const stockKind = f => STOCK_MAIN_DOC_TYPES.includes(f.doc_type) ? "決算" : "修正";
const stockTitle = f => `${f.title || ""}${f.source === "pdf" ? "（PDF から変換）" : ""}`;

async function renderStockNews(){
  const code = stockCode;
  let fs;
  try {
    fs = await loadStockFilings();
  } catch(e){
    if(e.name === "AbortError" || code !== stockCode) return;
    stockMsg("読み込めませんでした。");
    return;
  }
  if(code !== stockCode || stockSub !== "news") return;
  const list = $("#st-news-list"), detail = $("#st-news-detail");
  if(stockNewsId){
    list.hidden = true;
    detail.hidden = false;
    showStockNewsDetail(fs.find(f => stockNewsKey(f) === stockNewsId));
    return;
  }
  detail.hidden = true;
  list.hidden = false;
  if(!fs.length){
    $("#st-news").innerHTML = "";
    stockMsg("ニュースがありません。");
    return;
  }
  $("#st-news").innerHTML = "<tr><th class='l'>日時</th><th class='l'>種類</th><th class='l'>題名</th></tr>" + fs.map(f => {
    const go = f.documents.length > 0;
    const amend = f.is_amendment ? ' <span class="kind">訂正</span>' : "";
    return `<tr${go ? ` class="go" data-id="${stockEsc(stockNewsKey(f))}"` : ""}>
      <td class="l">${stockEsc(stockAt(f.submitted_at))}</td>
      <td class="l"><span class="kind">${stockKind(f)}</span>${amend}</td>
      <td class="l t">${stockEsc(stockTitle(f))}</td></tr>`;
  }).join("");
}

$("#st-news").addEventListener("click", e => {
  const tr = e.target.closest("tr.go");
  if(!tr) return;
  stockNewsId = tr.dataset.id;
  // 詳細は履歴に積む（ブラウザの戻るで一覧へ）
  writeRoute("stock", stockRoutePath(), "stock-news", true);
  renderStockNews();
});

$("#st-back").onclick = e => {
  e.preventDefault();
  stockNewsId = "";
  writeRoute("stock", stockRoutePath(), "stock-news-detail");
  renderStockNews();
};

window.addEventListener("popstate", () => {
  if($("#tab-stock").style.display === "none") return;
  const r = parseRoute(location.hash);
  if(r.tab === "stock" && r.code) openStockRoute(r.code);
});

function showStockNewsDetail(f){
  const head = $("#st-news-head");
  if(!f || !f.documents.length){
    head.textContent = "書類が見つかりません。";
    $("#st-doc").innerHTML = "";
    $("#st-src").hidden = true;
    stockDocKey = "";
    return;
  }
  head.innerHTML = `<span class="sub">${stockEsc(stockAt(f.submitted_at))}</span> <span class="kind">${stockKind(f)}</span>${f.is_amendment ? ' <span class="kind">訂正</span>' : ""} <b>${stockEsc(stockTitle(f))}</b>`;
  const key = stockNewsKey(f);
  if(key === stockDocKey) return;
  stockDocKey = key;
  showStockFiling(f);
}

// 原文の大きさ。fit = 画面の幅に合わせて縮める（指で拡大できる）/ mid = 0.75 倍（幅に合わせるより縮めない）/
// actual = 原寸。はみ出す分は #st-doc が横スクロール
const STOCK_ZOOM_KEY = "stock.zoom";
const STOCK_ZOOMS = ["fit", "mid", "actual"];
const STOCK_MID_SCALE = 0.75;
let stockZoom = "fit";
try {
  const z = localStorage.getItem(STOCK_ZOOM_KEY);
  if(STOCK_ZOOMS.includes(z)) stockZoom = z;
} catch(e) {}

function syncStockZoomButtons(){
  document.querySelectorAll("#st-zoom .ftab").forEach(b => b.classList.toggle("on", b.dataset.zoom === stockZoom));
}
syncStockZoomButtons();
document.querySelectorAll("#st-zoom .ftab").forEach(b => b.onclick = () => {
  stockZoom = b.dataset.zoom;
  try { localStorage.setItem(STOCK_ZOOM_KEY, stockZoom); } catch(e) {}
  syncStockZoomButtons();
  fitAllStockFrames();
});

function fitAllStockFrames(){
  document.querySelectorAll("#st-doc iframe").forEach(fitStockFrame);
}

// 中で script は動かない（sandbox）。同じオリジンなので親から中身を測り、iframe を中身の幅と高さまで広げる。
// iframe の中ではスクロールさせない（入れ子のスクロールは指で滑らせると引っかかる）。
// 縮めるときは iframe を transform で縮め、外枠（.st-page）を縮めた後の大きさにする
function fitStockFrame(frame){
  const page = frame.parentElement;
  try {
    const doc = frame.contentDocument;
    const de = doc.documentElement;
    de.style.overflow = "hidden";
    frame.style.width = "";
    frame.style.transform = "";
    page.style.width = "";
    const avail = page.clientWidth;
    const w = Math.max(de.scrollWidth, doc.body ? doc.body.scrollWidth : 0, avail);
    frame.style.width = w + "px";
    const h = Math.max(de.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
    frame.style.height = h + "px";
    const fit = w > avail ? avail / w : 1;
    const scale = stockZoom === "fit" ? fit : stockZoom === "mid" ? Math.max(fit, STOCK_MID_SCALE) : 1;
    if(scale < 1) frame.style.transform = `scale(${scale})`;
    if(w * scale > avail) page.style.width = Math.ceil(w * scale) + "px";
    page.style.height = Math.ceil(h * scale) + "px";
  } catch(e){
    frame.style.height = "80vh";
    page.style.height = "80vh";
  }
}

function showStockFiling(f){
  const host = $("#st-doc");
  host.innerHTML = "";
  const src = $("#st-src");
  src.textContent = f.attribution ? `出典: ${f.attribution}。PDF から変換した原文（画像は出ません）` : "";
  src.hidden = !f.attribution;
  for(const doc of f.documents){
    const frame = document.createElement("iframe");
    frame.className = "st-frame";
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("scrolling", "no");
    frame.title = doc.part === "summary" ? "サマリー" : doc.part === "pdf" ? "PDF から変換" : "添付資料";
    frame.onload = () => {
      fitStockFrame(frame);
      // 高さ確定で縦スクロールバーが出ると #st-doc が縮む。次フレームでもう一度（KAB-175）
      requestAnimationFrame(() => fitStockFrame(frame));
    };
    frame.src = doc.url;
    const page = document.createElement("div");
    page.className = "st-page";
    page.appendChild(frame);
    host.appendChild(page);
  }
}

// スマホはスクロールでアドレスバーが出入りして resize が続く。高さを測り直すのは幅が変わったときだけ
let stockFitWidth = window.innerWidth;
let stockFitPending = false;
window.addEventListener("resize", () => {
  if(window.innerWidth === stockFitWidth || stockFitPending) return;
  if($("#tab-stock").style.display === "none") return;
  stockFitPending = true;
  requestAnimationFrame(() => {
    stockFitPending = false;
    stockFitWidth = window.innerWidth;
    fitAllStockFrames();
  });
});

// 縦スクロールバーが出ると #st-doc の幅が約15px縮む。最初の onload ではまだバーが無く、
// iframe が古い幅のまま .st-page の overflow:hidden で右端が切れる（KAB-175）
const stDoc = $("#st-doc");
let stockDocWidth = stDoc.clientWidth;
let stockDocFitPending = false;
if(typeof ResizeObserver === "function"){
  new ResizeObserver(() => {
    if(stockDocFitPending) return;
    if($("#tab-stock").style.display === "none") return;
    stockDocFitPending = true;
    requestAnimationFrame(() => {
      stockDocFitPending = false;
      const w = stDoc.clientWidth;
      // 中のタブを切り替えて隠れた（幅 0）ときは測らない。また出たときに測る
      if(w === 0 || w === stockDocWidth) return;
      stockDocWidth = w;
      fitAllStockFrames();
    });
  }).observe(stDoc);
}

// 銘柄タブ（KAB-161。docs/DESIGN_stock.md）。銘柄を検索し、決算短信の原文を PDF と同じ並びで読む
let stockCode = "";
let stockFilings = [];
let stockAc = null;

const STOCK_MAIN_DOC_TYPES = ["annual", "semiannual", "quarterly"];
// XBRL の回は xbrl_filings.id、PDF から変換した回（KAB-164）は TDnet の docID
const stockFilingKey = f => f.source === "pdf" ? `pdf:${f.disclosure_no}` : `x:${f.id}`;

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

function stockMsg(text){
  const host = $("#st-doc");
  host.innerHTML = "";
  $("#st-src").hidden = true;
  const p = document.createElement("p");
  p.className = "sub";
  p.textContent = text;
  host.appendChild(p);
}

async function loadStock(code){
  stockCode = code;
  writeRoute("stock", code);
  if(stockAc) stockAc.abort();
  stockAc = new AbortController();
  let d;
  try {
    d = await api(`/api/stock/${code}/filings`, {signal: stockAc.signal});
  } catch(e){
    if(e.name === "AbortError") return;
    $("#st-title").textContent = "";
    $("#st-filing").innerHTML = "";
    $("#st-filing").hidden = true;
    stockMsg(String(e.message) === "404" ? "銘柄が見つかりません。" : "読み込めませんでした。");
    return;
  }
  const t = $("#st-title");
  t.innerHTML = "";
  const b = document.createElement("b");
  b.textContent = d.name || d.code;
  const small = document.createElement("small");
  small.textContent = ` ${d.code.slice(0, 4)}` + [d.market, d.sector33].filter(Boolean).map(x => ` / ${x}`).join("");
  t.append(b, small);
  if(!$("#st-sym").value) $("#st-sym").value = d.name || "";

  stockFilings = d.filings.filter(f => f.documents.length);
  const sel = $("#st-filing");
  sel.innerHTML = "";
  for(const f of stockFilings){
    const o = document.createElement("option");
    o.value = stockFilingKey(f);
    o.textContent = `${(f.submitted_at || "").slice(0, 10)} ${f.title || ""}${f.source === "pdf" ? "（PDF から変換）" : ""}`;
    sel.appendChild(o);
  }
  sel.hidden = !stockFilings.length;
  if(!stockFilings.length){
    stockMsg("表示できる決算短信がありません。");
    return;
  }
  const first = stockFilings.find(f => STOCK_MAIN_DOC_TYPES.includes(f.doc_type)) || stockFilings[0];
  sel.value = stockFilingKey(first);
  showStockFiling(first);
}

$("#st-filing").addEventListener("change", e => {
  const f = stockFilings.find(x => stockFilingKey(x) === e.target.value);
  if(f) showStockFiling(f);
});

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
      if(w === stockDocWidth) return;
      stockDocWidth = w;
      fitAllStockFrames();
    });
  }).observe(stDoc);
}

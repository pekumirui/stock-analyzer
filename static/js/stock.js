// 銘柄タブ（KAB-161。docs/DESIGN_stock.md）。銘柄を検索し、決算短信の原文を PDF と同じ並びで読む
let stockCode = "";
let stockFilings = [];
let stockAc = null;

const STOCK_MAIN_DOC_TYPES = ["annual", "semiannual", "quarterly"];

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
    o.value = f.id;
    o.textContent = `${(f.submitted_at || "").slice(0, 10)} ${f.title || ""}`;
    sel.appendChild(o);
  }
  sel.hidden = !stockFilings.length;
  if(!stockFilings.length){
    stockMsg("表示できる決算短信がありません。");
    return;
  }
  const first = stockFilings.find(f => STOCK_MAIN_DOC_TYPES.includes(f.doc_type)) || stockFilings[0];
  sel.value = first.id;
  showStockFiling(first);
}

$("#st-filing").addEventListener("change", e => {
  const f = stockFilings.find(x => String(x.id) === e.target.value);
  if(f) showStockFiling(f);
});

// 中で script は動かない（sandbox）。同じオリジンなので親から中身を測り、iframe を中身の幅と高さまで広げる。
// iframe の中ではスクロールさせない（入れ子のスクロールは指で滑らせると引っかかる）。横にはみ出す分は #st-doc が横スクロール
function fitStockFrame(frame){
  try {
    const doc = frame.contentDocument;
    const de = doc.documentElement;
    de.style.overflow = "hidden";
    frame.style.width = "";
    const w = Math.max(de.scrollWidth, doc.body ? doc.body.scrollWidth : 0);
    if(w > frame.clientWidth) frame.style.width = w + "px";
    frame.style.height = Math.max(de.scrollHeight, doc.body ? doc.body.scrollHeight : 0) + "px";
  } catch(e){
    frame.style.height = "80vh";
  }
}

function showStockFiling(f){
  const host = $("#st-doc");
  host.innerHTML = "";
  for(const doc of f.documents){
    const frame = document.createElement("iframe");
    frame.className = "st-frame";
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("scrolling", "no");
    frame.title = doc.part === "summary" ? "サマリー" : "添付資料";
    frame.onload = () => fitStockFrame(frame);
    frame.src = `/api/stock/filings/${f.id}/documents/${doc.seq}`;
    host.appendChild(frame);
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
    document.querySelectorAll("#st-doc iframe").forEach(fitStockFrame);
  });
});

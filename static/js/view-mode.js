// スマホ端末だけページ末尾に「PC版を表示」。viewport を width=1280 にして PC レイアウトにする。
const VIEW_MODE_KEY = "ui.pcView";
const VIEW_MODE_PC = "width=1280";
const VIEW_MODE_PHONE = "width=device-width, initial-scale=1.0";
const VIEW_MODE_MAX = 720;

function viewModeNativePhone(width){
  return Number(width) <= VIEW_MODE_MAX;
}

function viewModePcOn(store){
  try { return (store || localStorage).getItem(VIEW_MODE_KEY) === "1"; }
  catch(e){ return false; }
}

function viewModeLabel(pc){
  return pc ? "スマホ版に戻す" : "PC版を表示";
}

function viewModeViewport(pc){
  return pc ? VIEW_MODE_PC : VIEW_MODE_PHONE;
}

function applyViewMode(doc, screenWidth, store){
  if(!viewModeNativePhone(screenWidth) || !viewModePcOn(store)) return false;
  const meta = doc.querySelector('meta[name="viewport"]');
  if(!meta) return false;
  meta.setAttribute("content", VIEW_MODE_PC);
  return true;
}

function mountViewMode(doc, screenWidth, store, reload){
  const host = doc.getElementById("view-mode");
  const btn = doc.getElementById("view-mode-btn");
  if(!host || !btn) return false;
  if(!viewModeNativePhone(screenWidth)){
    host.hidden = true;
    return false;
  }
  host.hidden = false;
  const paint = () => { btn.textContent = viewModeLabel(viewModePcOn(store)); };
  paint();
  btn.onclick = ev => {
    if(ev && ev.preventDefault) ev.preventDefault();
    const next = !viewModePcOn(store);
    try { store.setItem(VIEW_MODE_KEY, next ? "1" : "0"); } catch(e){}
    const meta = doc.querySelector('meta[name="viewport"]');
    if(meta) meta.setAttribute("content", viewModeViewport(next));
    paint();
    if(typeof reload === "function") reload();
  };
  return true;
}

if(typeof window !== "undefined"){
  applyViewMode(document, screen.width, localStorage);
  const go = () => mountViewMode(document, screen.width, localStorage, () => location.reload());
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", go);
  else go();
}

if(typeof module !== "undefined" && module.exports){
  module.exports = {
    VIEW_MODE_KEY, VIEW_MODE_PC, VIEW_MODE_PHONE, VIEW_MODE_MAX,
    viewModeNativePhone, viewModePcOn, viewModeLabel, viewModeViewport,
    applyViewMode, mountViewMode,
  };
}

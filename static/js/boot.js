fillEventToggles();
selectDate(todayISO()).then(calPrefetch);
restoreRoute();

// iOS Safari はタブに戻るとページをメモリから復元し、デプロイ後も古い HTML / CSS / JS のまま動く。
// 復元（bfcache）なら読み直す。1 分以上裏にいた後は、ページの ETag を読み込み時と比べ、変わっていれば読み直す。
// ページの ETag は /static の ?v= を含む HTML から作るので、CSS / JS が変わっても変わる（app/main.py）。
(() => {
  const tag = r => r.ok ? (r.headers.get("etag") || r.headers.get("last-modified") || "") : null;
  const sign = () => fetch(location.pathname, {cache: "no-store", priority: "low"}).then(tag).catch(() => null);
  let base = null, hiddenAt = 0;
  sign().then(s => { base = s; });
  addEventListener("pageshow", e => { if(e.persisted) location.reload(); });
  document.addEventListener("visibilitychange", () => {
    if(document.hidden){ hiddenAt = Date.now(); return; }
    if(!base || Date.now() - hiddenAt < 60e3) return;
    sign().then(s => { if(s && s !== base) location.reload(); });
  });
})();

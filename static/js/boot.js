fillEventToggles();
selectDate(todayISO()).then(calPrefetch);
restoreRoute();

// iOS Safari はタブに戻るとページをメモリから復元し、デプロイ後も古い HTML / CSS / JS のまま動く。
// 復元（bfcache）なら読み直す。1 分以上裏にいた後は、ページを取り直して読み込み時と比べ、変わっていれば読み直す。
// 比べるのはページの ETag と、HTML の中の /static の ?v=（CSS / JS の中身のハッシュ）。ETag は途中のプロキシが外すことがある。
(() => {
  const sign = () => fetch(location.pathname, {cache: "no-store", priority: "low"}).then(async r => {
    if(!r.ok) return null;
    const urls = (await r.text()).match(/\/static\/[^"?#]+\?v=[0-9a-f]+/g) || [];
    return [r.headers.get("etag") || "", ...urls].join("|");
  }).catch(() => null);
  let base = null, hiddenAt = 0;
  sign().then(s => { base = s; });
  addEventListener("pageshow", e => { if(e.persisted) location.reload(); });
  document.addEventListener("visibilitychange", () => {
    if(document.hidden){ hiddenAt = Date.now(); return; }
    if(!base || Date.now() - hiddenAt < 60e3) return;
    sign().then(s => { if(s && s !== base) location.reload(); });
  });
})();

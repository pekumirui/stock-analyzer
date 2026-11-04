function publicMonthlyPathCode(){
  const m = String(location.pathname || "").match(/^\/monthly\/([0-9][0-9A-Z]{3}[0-9]?)$/i);
  return m ? m[1].toUpperCase() : "";
}

function publicMonthlyShowFromPath(){
  const code = publicMonthlyPathCode();
  if(code) showMonthlyDetail(code, {skipHist: true});
  else {
    showMonthlyTop();
    document.title = "月次売上 | 株KPI";
  }
}

function bootPublicMonthly(){
  const code = publicMonthlyPathCode();
  const ready = (typeof loadMonthlyList === "function")
    ? loadMonthlyList({keepView: true})
    : Promise.resolve();
  Promise.resolve(ready).then(() => {
    if(code) showMonthlyDetail(code, {skipHist: true});
    else showMonthlyTop();
  });
  window.addEventListener("popstate", publicMonthlyShowFromPath);
  document.addEventListener("click", ev => {
    if(ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    const a = ev.target && ev.target.closest && ev.target.closest("a[href]");
    if(!a) return;
    const url = new URL(a.getAttribute("href"), location.origin);
    if(url.origin !== location.origin) return;
    if(!/^\/monthly(?:\/[0-9][0-9A-Z]{3}[0-9]?)?$/i.test(url.pathname)) return;
    ev.preventDefault();
    if(url.pathname === "/monthly"){
      if(location.pathname !== "/monthly") history.pushState({}, "", "/monthly");
      showMonthlyTop();
      document.title = "月次売上 | 株KPI";
      return;
    }
    const m = url.pathname.match(/^\/monthly\/([0-9][0-9A-Z]{3}[0-9]?)$/i);
    if(m) showMonthlyDetail(m[1].toUpperCase());
  });
}

bootPublicMonthly();

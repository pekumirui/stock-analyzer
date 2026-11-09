"""ブラウザの戻るで前の画面へ（KAB-398）。

タブか対象が変わる遷移は履歴に積み、同じ画面の中のタブと遷移元だけの変化は積まない。
戻ると URL のハッシュから画面を開き直す。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_NODE = r"""
const fs = require("fs");
const vm = require("vm");
const root = process.argv[1];
const el = () => ({style: {}, dataset: {}, classList: {toggle(){}, contains(){ return false; }},
  addEventListener(){}, setAttribute(){}, getAttribute(){ return null; }, textContent: "", innerHTML: ""});
const els = new Map();
const $ = s => { if(!els.has(s)) els.set(s, el()); return els.get(s); };
const entries = [{url: "/app#ranking", state: null}];
let idx = 0;
const listeners = {};
const location = {pathname: "/app", search: "", hash: "#ranking"};
const setUrl = u => { location.hash = u.slice(u.indexOf("#")); };
const history = {
  pushState(state, _t, url){ entries.splice(idx + 1); entries.push({url, state}); idx++; setUrl(url); },
  replaceState(state, _t, url){ entries[idx] = {url: url || entries[idx].url, state}; if(url) setUrl(url); },
  get state(){ return entries[idx].state; },
  scrollRestoration: "auto",
};
const routes = [];
const ctx = {
  $, location, history, console, URLSearchParams, requestAnimationFrame: fn => fn(),
  localStorage: {getItem(){ return null; }, setItem(){}},
  document: {querySelector: $, querySelectorAll(){ return []; }, addEventListener(){},
    getElementById(){ return null; }, body: el()},
  window: null, scrollY: 120, scrollTo(){},
  showRankingSub(sub, opts){ routes.push(["ranking", sub, opts]); },
  openStockRoute(code){ routes.push(["stock", code]); ctx.writeRoute("stock", code); },
  stockRoutePath(){ return ""; },
};
ctx.window = ctx;
ctx.addEventListener = (t, fn) => { listeners[t] = fn; };
vm.createContext(ctx);
for(const f of ["static/js/screens.js", "static/js/tabs.js"]){
  vm.runInContext(fs.readFileSync(root + "/" + f, "utf8"), ctx, {filename: f});
}
(async () => {
  ctx.writeRoute("stock", "72030/price", "ranking");
  const afterOpen = entries.length;
  ctx.writeRoute("stock", "72030/summary", "stock-price");
  const afterSub = entries.length;
  ctx.writeRoute("ranking", "pts");
  ctx.writeRoute("ranking", "");
  const afterRanking = entries.length;
  idx = 0; setUrl(entries[0].url);
  await listeners.popstate({state: entries[0].state});
  process.stdout.write(JSON.stringify({
    afterOpen, afterSub, afterRanking, savedY: entries[0].state && entries[0].state.y,
    hash: location.hash, routes, manual: history.scrollRestoration,
  }));
})();
"""


def test_back_returns_to_previous_screen() -> None:
    out = subprocess.run(
        ["node", "-e", _NODE, str(ROOT)], capture_output=True, text=True, check=True,
    ).stdout
    r = json.loads(out)
    assert r["afterOpen"] == 2
    assert r["afterSub"] == 2
    assert r["afterRanking"] == 3
    assert r["savedY"] == 120
    assert r["hash"] == "#ranking"
    assert r["routes"][-1][:2] == ["ranking", "perf"]
    assert r["routes"][-1][2] == {"force": False}
    assert r["manual"] == "manual"

"""パンくず（KAB-276）。ルート定義から全画面を列挙し、設定漏れと遷移元の参照切れを落とす。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCREENS_JS = ROOT / "static" / "js" / "screens.js"
TABS_JS = ROOT / "static" / "js" / "tabs.js"
INDEX = ROOT / "static" / "index.html"
PUBLIC_PY = ROOT / "app" / "monthly" / "public.py"
MAIN_PY = ROOT / "app" / "main.py"

_JS_JSON = re.compile(
    r"const (ROUTES|BREADCRUMBS) = JSON\.parse\(String\.raw`([\s\S]*?)`\)",
)


def _fail(msg: object) -> None:
    raise AssertionError(str(msg))


def _load_screens() -> tuple[list[dict], dict]:
    src = SCREENS_JS.read_text(encoding="utf-8")
    found = dict(_JS_JSON.findall(src))
    if "ROUTES" not in found or "BREADCRUMBS" not in found:
        _fail("static/js/screens.js から ROUTES / BREADCRUMBS を読めない")
    routes = json.loads(found["ROUTES"])
    crumbs = json.loads(found["BREADCRUMBS"])
    if not isinstance(routes, list) or not isinstance(crumbs, dict):
        _fail("ROUTES は配列、BREADCRUMBS はオブジェクトである必要がある")
    return routes, crumbs


def _js_str_list(src: str, name: str) -> list[str]:
    m = re.search(rf"const {name} = \[([^\]]*)\]", src)
    if not m:
        _fail(f"const {name} が見つからない")
    return re.findall(r'"([^"]+)"', m.group(1))


def _route_ids(routes: list[dict]) -> set[str]:
    ids: set[str] = set()
    for row in routes:
        rid = row.get("id")
        if not rid or not isinstance(rid, str):
            _fail(f"ROUTES に id が無い: {row!r}")
        ids.add(rid)
    return ids


def test_every_route_has_breadcrumb_config() -> None:
    """ルート定義の全画面にパンくずがある。手書きの画面リストは使わない。"""
    routes, crumbs = _load_screens()
    missing = sorted(_route_ids(routes) - set(crumbs))
    extra = sorted(set(crumbs) - _route_ids(routes))
    if missing:
        _fail("パンくず設定がない画面: " + ", ".join(missing))
    if extra:
        _fail("ROUTES に無いパンくず設定: " + ", ".join(extra))
    for sid, cfg in crumbs.items():
        if not isinstance(cfg, dict):
            _fail(f"{sid}: 設定がオブジェクトではない")
        if not cfg.get("label"):
            _fail(f"{sid}: label が無い")
        if "defaultFrom" not in cfg:
            _fail(f"{sid}: defaultFrom が無い")
        if not isinstance(cfg.get("from"), list):
            _fail(f"{sid}: from が配列ではない")


def test_breadcrumb_from_points_at_existing_screens() -> None:
    routes, crumbs = _load_screens()
    ids = _route_ids(routes)
    for sid, cfg in crumbs.items():
        parent = cfg.get("defaultFrom")
        if parent is not None and parent not in ids:
            _fail(f"{sid}: defaultFrom={parent!r} は実在する画面ではない")
        if parent == sid:
            _fail(f"{sid}: defaultFrom が自分自身")
        for src in cfg.get("from") or []:
            if src not in ids:
                _fail(f"{sid}: from={src!r} は実在する画面ではない")
            if src == sid:
                _fail(f"{sid}: from に自分自身がある")


def test_routes_cover_tabs_js_and_public_paths() -> None:
    """tabs.js のルート定数と公開パスが ROUTES に載っている（画面名は書かない）。"""
    routes, _ = _load_screens()
    tabs_src = TABS_JS.read_text(encoding="utf-8")
    tabs = _js_str_list(tabs_src, "TABS")
    stock_subs = _js_str_list(tabs_src, "STOCK_SUBS")
    ranking_subs = _js_str_list(tabs_src, "RANKING_SUBS")
    route_tabs = {row["tab"] for row in routes}
    missing_tabs = [t for t in tabs if t not in route_tabs]
    if missing_tabs:
        _fail("ROUTES に tab が無い: " + ", ".join(missing_tabs))
    stock_paths = [row["path"] for row in routes if row["tab"] == "stock"]
    for sub in stock_subs:
        if not any(p == f":code/{sub}" or p.endswith(f"/{sub}") or p.endswith(f"/{sub}/:newsId") for p in stock_paths):
            _fail(f"STOCK_SUBS {sub!r} に対応する ROUTES path が無い")
    if "news" in stock_subs and not any(":newsId" in p or p.count("/") >= 2 for p in stock_paths):
        _fail("ニュース詳細の ROUTES path が無い")
    ranking_paths = [row["path"] for row in routes if row["tab"] == "ranking"]
    for sub in ranking_subs:
        if sub not in ranking_paths:
            _fail(f"RANKING_SUBS {sub!r} に対応する ROUTES path が無い")
    monthly_paths = [row["path"] for row in routes if row["tab"] == "monthly"]
    if not any(":code" in p for p in monthly_paths):
        _fail("monthly のパラメータ付き ROUTES path が無い")
    holders_paths = [row["path"] for row in routes if row["tab"] == "holders"]
    if not any(":edinet" in p for p in holders_paths):
        _fail("holders のパラメータ付き ROUTES path が無い")
    main = MAIN_PY.read_text(encoding="utf-8")
    if '@app.get("/monthly")' not in main or '@app.get("/monthly/{code}")' not in main:
        _fail("公開月次のルートが main.py に無い")
    if not any(row["tab"] == "monthly" and row["path"] == "" for row in routes):
        _fail("公開 /monthly に対応する monthly ルートが無い")
    if not any(row["tab"] == "monthly" and ":code" in row["path"] for row in routes):
        _fail("公開 /monthly/{code} に対応する monthly-detail ルートが無い")


def test_breadcrumb_markup_is_on_every_html_shell() -> None:
    html = INDEX.read_text(encoding="utf-8")
    public = PUBLIC_PY.read_text(encoding="utf-8")
    if 'id="crumbs"' not in html or 'aria-label="パンくず"' not in html:
        _fail("SPA に nav#crumbs が無い")
    if "/static/js/screens.js" not in html:
        _fail("SPA が screens.js を読んでいない")
    if html.index("/static/js/screens.js") > html.index("/static/js/tabs.js"):
        _fail("screens.js は tabs.js より前に置く")
    if 'id="crumbs"' not in public or "/static/js/screens.js" not in public:
        _fail("公開月次に nav#crumbs か screens.js が無い")


def test_trail_uses_from_and_falls_back_to_default() -> None:
    proc = subprocess.run(
        ["node", "-e", r"""
const s = require(process.argv[1]);
const price = s.breadcrumbTrail('stock-price', 'earnings').map(x => x.label).join(' > ');
const pts = s.breadcrumbTrail('stock-price', 'ranking-pts').map(x => x.label).join(' > ');
const def = s.breadcrumbTrail('stock-price', 'nope').map(x => x.label).join(' > ');
const bogus = s.breadcrumbTrail('stock-price', 'monthly').map(x => x.label).join(' > ');
const news = s.breadcrumbTrail('stock-news-detail', '').map(x => x.label).join(' > ');
const holder = s.breadcrumbTrail('holders-detail', 'stock-holders').map(x => x.label).join(' > ');
if (s.resolveFrom('stock-price', 'earnings') !== 'earnings') process.exit(2);
if (s.resolveFrom('stock-price', 'monthly') !== '') process.exit(3);
if (s.screenIdFromTabCode('stock', '72030/price') !== 'stock-price') process.exit(4);
if (s.screenIdFromTabCode('stock', '72030') !== 'stock-summary') process.exit(5);
if (s.screenIdFromTabCode('supply', 'ethylene') !== 'supply') process.exit(6);
console.log(JSON.stringify({price, pts, def, bogus, news, holder}));
""", str(SCREENS_JS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"node trail: {proc.returncode} {proc.stderr or proc.stdout}")
    got = json.loads(proc.stdout)
    if got["price"] != "トップ > 決算速報 > 株価":
        _fail(f"from=earnings: {got['price']!r}")
    if got["pts"] != "トップ > ランキング > PTS > 株価":
        _fail(f"from=ranking-pts: {got['pts']!r}")
    if got["def"] != "トップ > 銘柄 > 株価":
        _fail(f"default: {got['def']!r}")
    if got["bogus"] != "トップ > 銘柄 > 株価":
        _fail(f"from=monthly (not allowed): {got['bogus']!r}")
    if got["news"] != "トップ > 銘柄 > ニュース > ニュース詳細":
        _fail(f"news-detail default: {got['news']!r}")
    if got["holder"] != "トップ > 銘柄 > 株主 > 保有者":
        _fail(f"from=stock-holders: {got['holder']!r}")

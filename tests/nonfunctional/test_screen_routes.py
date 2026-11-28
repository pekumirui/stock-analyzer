from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "static" / "js"
SCREENS_JS = JS_DIR / "screens.js"
TABS_JS = JS_DIR / "tabs.js"
INDEX = ROOT / "static" / "index.html"
PUBLIC_PY = ROOT / "app" / "monthly" / "public.py"
MAIN_PY = ROOT / "app" / "main.py"
CSS = ROOT / "static" / "css" / "app.css"

_JS_JSON = re.compile(
    r"const (ROUTES|SCREEN_LABELS) = JSON\.parse\(String\.raw`([\s\S]*?)`\)",
)


def _fail(msg: object) -> None:
    raise AssertionError(str(msg))


def _load_screens() -> tuple[list[dict], dict]:
    found = dict(_JS_JSON.findall(SCREENS_JS.read_text(encoding="utf-8")))
    if "ROUTES" not in found or "SCREEN_LABELS" not in found:
        _fail("static/js/screens.js から ROUTES / SCREEN_LABELS を読めない")
    routes = json.loads(found["ROUTES"])
    labels = json.loads(found["SCREEN_LABELS"])
    if not isinstance(routes, list) or not isinstance(labels, dict):
        _fail("ROUTES は配列、SCREEN_LABELS はオブジェクトである必要がある")
    return routes, labels


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


def test_every_route_has_a_screen_label() -> None:
    routes, labels = _load_screens()
    ids = _route_ids(routes)
    missing = sorted(ids - set(labels))
    extra = sorted(set(labels) - ids)
    if missing:
        _fail("画面名がない画面: " + ", ".join(missing))
    if extra:
        _fail("ROUTES に無い画面名: " + ", ".join(extra))
    for sid, label in labels.items():
        if not isinstance(label, str) or not label:
            _fail(f"{sid}: 画面名が空")


def test_routes_cover_tabs_js_and_public_paths() -> None:
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


def test_spa_loads_screens_js_before_tabs_js() -> None:
    html = INDEX.read_text(encoding="utf-8")
    if "/static/js/screens.js" not in html:
        _fail("SPA が screens.js を読んでいない")
    if html.index("/static/js/screens.js") > html.index("/static/js/tabs.js"):
        _fail("screens.js は tabs.js より前に置く")


def test_no_breadcrumbs_or_from_query_anywhere() -> None:
    shells = {"static/index.html": INDEX, "app/monthly/public.py": PUBLIC_PY, "static/css/app.css": CSS}
    shells.update({f"static/js/{p.name}": p for p in sorted(JS_DIR.glob("*.js"))})
    for name, path in shells.items():
        text = path.read_text(encoding="utf-8")
        if "crumbs" in text.lower():
            _fail(f"{name} にパンくずが残っている")
        if "?from=" in text:
            _fail(f"{name} に ?from= が残っている")

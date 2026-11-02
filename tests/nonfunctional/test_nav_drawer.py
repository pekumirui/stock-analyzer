"""スマホドロワーの幕が iOS Safari の枠をグレーに残さない（KAB-317）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "static" / "css" / "app.css"
INDEX = ROOT / "static" / "index.html"
TABS_JS = ROOT / "static" / "js" / "tabs.js"


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def test_theme_color_is_white() -> None:
    html = INDEX.read_text(encoding="utf-8")
    if 'name="theme-color" content="#ffffff"' not in html:
        _fail("theme-color は #ffffff")


def test_html_background_is_canvas_white() -> None:
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"^  html \{([^}]+)\}", css, re.M)
    if not m or "background:var(--bg)" not in m.group(1):
        _fail("html の背景は --bg（Safari の枠と overscroll）")


def test_nav_backdrop_background_color_is_white() -> None:
    css = CSS.read_text(encoding="utf-8")
    visible = hidden = after = None
    for m in re.finditer(r"#nav-backdrop(::after|\[hidden\])? \{([^}]+)\}", css):
        body = m.group(2)
        kind = m.group(1) or ""
        if kind == "::after":
            after = body
        elif kind == "[hidden]":
            hidden = body
        elif "position:fixed" in body:
            visible = body
    if not visible:
        _fail("#nav-backdrop の fixed ルールが無い")
    compact = re.sub(r"\s+", "", visible)
    if "background-color:var(--bg)" not in compact:
        _fail("幕本体の background-color は --bg（Safari が枠に使う）")
    if "rgba(0,0,0" in compact:
        _fail("幕本体に半透明の黒を置かない")
    if not after or "rgba(0,0,0,.35)" not in re.sub(r"\s+", "", after):
        _fail("暗いのは #nav-backdrop::after")
    after_c = re.sub(r"\s+", "", after)
    if "top:48px" not in after_c or "bottom:48px" not in after_c:
        _fail("::after は上下端から外す")
    if not hidden or "display:none" not in hidden:
        _fail("#nav-backdrop[hidden] は display:none")


def test_set_menu_hides_drawer_from_render_tree() -> None:
    js = TABS_JS.read_text(encoding="utf-8")
    if "nav.hidden = false" not in js or "nav.hidden = true" not in js:
        _fail("setMenu はモバイルで #app-nav を hidden にして描画ツリーから外す")
    if "bd.hidden = false" not in js or "bd.hidden = true" not in js:
        _fail("setMenu は #nav-backdrop.hidden で開閉する")
    css = CSS.read_text(encoding="utf-8")
    if "#app-nav[hidden] { display:none; }" not in css:
        _fail("#app-nav[hidden] は display:none")

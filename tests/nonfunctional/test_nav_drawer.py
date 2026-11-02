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


def test_nav_backdrop_does_not_paint_viewport_edges() -> None:
    css = CSS.read_text(encoding="utf-8")
    visible = hidden = None
    for m in re.finditer(r"#nav-backdrop(\[hidden\])? \{([^}]+)\}", css):
        body = m.group(2)
        if m.group(1):
            hidden = body
        elif "position:fixed" in body:
            visible = body
    if not visible:
        _fail("#nav-backdrop の fixed ルールが無い")
    compact = re.sub(r"\s+", "", visible)
    if "inset:0" in compact:
        _fail("#nav-backdrop は inset:0 で画面端を塗らない")
    if "top:1px" not in compact or "bottom:1px" not in compact:
        _fail("#nav-backdrop は top/bottom 1px で上下端を空ける")
    if not hidden or "display:none" not in hidden:
        _fail("#nav-backdrop[hidden] は display:none")


def test_mobile_edge_sentinels_are_white() -> None:
    css = CSS.read_text(encoding="utf-8")
    media = css.split("@media (max-width:720px)", 1)
    if len(media) < 2:
        _fail("720px のメディアクエリが無い")
    block = media[1]
    if "html::before" not in block or "html::after" not in block:
        _fail("スマホは html::before/after で画面上下端を白にする")
    if "background:var(--bg)" not in block.split("html::before", 1)[1][:400]:
        _fail("端の 1px は --bg")


def test_set_menu_hides_backdrop_with_hidden() -> None:
    js = TABS_JS.read_text(encoding="utf-8")
    if "nav-backdrop" not in js or ".hidden = !open" not in js:
        _fail("setMenu は #nav-backdrop.hidden で開閉する")

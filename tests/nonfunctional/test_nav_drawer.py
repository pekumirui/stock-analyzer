"""スマホのメニューが iOS Safari の枠をグレーにしない（KAB-317）。

Safari 26 は画面上下端の fixed の background-color を枠の色にし、閉じても残す。
メニューはヘッダーの下に流れで開き、fixed の幕やドロワーを置かない。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "static" / "css" / "app.css"
INDEX = ROOT / "static" / "index.html"
TABS_JS = ROOT / "static" / "js" / "tabs.js"


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _mobile_block(css: str) -> str:
    parts = css.split("@media (max-width:720px){", 1)
    if len(parts) < 2:
        _fail("720px のメディアクエリが無い")
    return parts[1].split("\n  }\n", 1)[0]


def test_theme_color_is_white() -> None:
    html = INDEX.read_text(encoding="utf-8")
    if 'name="theme-color" content="#ffffff"' not in html:
        _fail("theme-color は #ffffff")


def test_html_background_is_canvas_white() -> None:
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"^  html \{([^}]+)\}", css, re.M)
    if not m or "background:var(--bg)" not in m.group(1):
        _fail("html の背景は --bg（Safari の枠と overscroll）")


def test_no_fixed_overlay_for_menu() -> None:
    html = INDEX.read_text(encoding="utf-8")
    if "nav-backdrop" in html:
        _fail("幕 #nav-backdrop を置かない")
    block = _mobile_block(CSS.read_text(encoding="utf-8"))
    if "position:fixed" in block:
        _fail("スマホのヘッダー・メニューに position:fixed を使わない（Safari が枠の色に使う）")


def test_menu_opens_below_header_in_flow() -> None:
    block = _mobile_block(CSS.read_text(encoding="utf-8"))
    nav = re.search(r"#app-nav \{([^}]+)\}", block)
    if not nav:
        _fail("スマホの #app-nav ルールが無い")
    body = re.sub(r"\s+", "", nav.group(1))
    for want in ("display:none", "flex:10100%"):
        if want not in body:
            _fail(f"#app-nav は閉じて {want}（ヘッダーの下の 1 行を占める）")
    if not re.search(r"#app-nav\.open \{\s*display:flex;", block):
        _fail("#app-nav.open で開く")
    if not re.search(r"header \{[^}]*flex-wrap:wrap", block):
        _fail("ヘッダーは折り返してメニューを下の行に出す")


def test_set_menu_toggles_open_class() -> None:
    js = TABS_JS.read_text(encoding="utf-8")
    if '$("#app-nav").classList.toggle("open", open)' not in js:
        _fail("setMenu は #app-nav.open で開閉する")
    if "aria-expanded" not in js:
        _fail("setMenu は #menu-btn の aria-expanded を更新する")

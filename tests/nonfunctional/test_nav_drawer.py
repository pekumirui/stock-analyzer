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


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r" \{([^}]+)\}", css)
    if not m:
        _fail(f"{selector} のルールが無い")
    return re.sub(r"\s+", "", m.group(1))


def test_bar_tint_strips_keep_safari_bars_white() -> None:
    """Safari 26 は端の少し内側の一番手前の fixed（10px 超）の background-color を枠に使う。"""
    html = INDEX.read_text(encoding="utf-8")
    for pos in ("top", "bottom"):
        if f'<div class="bar-tint {pos}" aria-hidden="true"></div>' not in html:
            _fail(f".bar-tint.{pos} を index.html に置く")
    css = CSS.read_text(encoding="utf-8")
    tint = _rule(css, ".bar-tint")
    for want in ("position:fixed", "background-color:var(--bg)", "background-clip:content-box",
                 "height:0", "pointer-events:none"):
        if want not in tint:
            _fail(f".bar-tint に {want}")
    pad = re.search(r"padding-top:(\d+)px", tint)
    if not pad or int(pad.group(1)) <= 10:
        _fail(".bar-tint は 10px より高くする（Safari は 10px 以下を見ない）")
    z = int(re.search(r"z-index:(\d+)", tint).group(1))
    for sel in ("#app-nav", "#nav-backdrop"):
        for m in re.finditer(re.escape(sel) + r" \{[^}]*z-index:(\d+)", css):
            if int(m.group(1)) >= z:
                _fail(f".bar-tint は {sel} より手前")


def test_nav_backdrop_has_no_white_bands() -> None:
    css = CSS.read_text(encoding="utf-8")
    if "#nav-backdrop::" in css:
        _fail("幕に疑似要素の帯を作らない（白い帯が見える）")
    if re.search(r"html::(before|after)", css):
        _fail("html の疑似要素で端に線を引かない（幕を開くと白い線が見える）")
    m = re.search(r"#nav-backdrop \{\s*(display:block;[^}]+)\}", css)
    body = re.sub(r"\s+", "", m.group(1)) if m else ""
    if "inset:0" not in body or "rgba(0,0,0,.35)" not in body:
        _fail("幕は inset:0 で全面を暗くする")
    if "display:none" not in _rule(css, "#nav-backdrop[hidden]"):
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

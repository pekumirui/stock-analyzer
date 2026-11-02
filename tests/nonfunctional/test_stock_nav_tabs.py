"""銘柄の中のタブはフォルダ型 .nav-tabs。塗りつぶしの .fin-tabs は他の切替のまま（KAB-315）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

ST_TABS_IDS = ("st-tabs",)
FIN_TABS_IDS = ("earn-view", "st-fin-tabs", "tf", "st-zoom", "rk-subs", "rk-kind", "pts-ranking")
ST_SUBS = ("summary", "price", "news", "holders")


def _block(src: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", src)
    assert m, f"missing CSS rule {selector}"
    return m.group(1)


def test_st_tabs_is_nav_tabs_not_fin_tabs() -> None:
    m = re.search(r'<div\b[^>]*\bid="st-tabs"[^>]*>', HTML)
    if not m:
        m = re.search(r'<div\b[^>]*\bclass="[^"]*nav-tabs[^"]*"[^>]*\bid="st-tabs"', HTML)
    assert m, "missing #st-tabs"
    tag = m.group(0)
    assert "nav-tabs" in tag, tag
    assert "fin-tabs" not in tag, tag
    inner = HTML[HTML.index('id="st-tabs"'):HTML.index('id="st-metrics"')]
    for sub in ST_SUBS:
        assert f'data-sub="{sub}"' in inner
    assert inner.count("class=\"ftab") == 4


def test_other_fin_tabs_stay_segmented() -> None:
    for eid in FIN_TABS_IDS:
        m = re.search(rf'<div\b[^>]*\bid="{eid}"[^>]*>', HTML)
        assert m, f"missing #{eid}"
        assert "fin-tabs" in m.group(0), m.group(0)
        assert "nav-tabs" not in m.group(0), m.group(0)
    fin = _block(CSS, ".fin-tabs")
    assert "border:1px solid" in fin.replace(" ", "") or "border:1px solid var(--border)" in fin
    assert "border-radius:8px" in fin
    on = _block(CSS, ".ftab.on")
    assert "background:var(--accent)" in on
    nav_on = _block(CSS, ".nav-tabs .ftab.on")
    assert "background:var(--panel)" in nav_on
    assert "var(--accent)" not in nav_on
    assert "var(--border) var(--border) var(--panel)" in nav_on


def test_nav_tabs_folder_css() -> None:
    nav = _block(CSS, ".nav-tabs")
    assert "flex-wrap:nowrap" in nav
    assert "border-bottom:1px solid var(--border)" in nav
    tab = _block(CSS, ".nav-tabs .ftab")
    assert "margin-bottom:-1px" in tab
    assert "white-space:nowrap" in tab
    assert "flex:0 0 auto" in tab
    pad = re.search(r"padding:(\d+)px (\d+)px", tab)
    assert pad, tab
    v, h = int(pad.group(1)), int(pad.group(2))
    fs = re.search(r"font-size:(\d+)px", tab)
    assert fs and int(fs.group(1)) == 13, tab
    assert v >= 5 and h >= 8
    mobile = CSS[CSS.index("@media (max-width:640px)"):]
    mtab = _block(mobile, ".nav-tabs .ftab")
    assert "flex:1 1 0" in mtab
    mpad = re.search(r"padding:(\d+)px (\d+)px", mtab)
    assert mpad, mtab
    mh = int(mpad.group(2))
    # 390px: main 6+6 + panel 12+12 = 36。残り 354px を 4 等分 ≈ 88px。
    # 最長ラベル「ニュース」4 字 × 13px + padding でも収まる。
    longest = 4 * 13 + 2 * mh
    assert longest <= 88, f"ニュース tab {longest}px exceeds 390px slot"

"""一覧表（決算速報・スクリーナー・ランキング）が table.sheet を共有する（KAB-196）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_sheet_css_is_shared() -> None:
    css = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
    assert "table.sheet" in css
    assert "table.sheet tbody tr:nth-child(even) td" in css
    assert "table.sheet tbody tr:hover td" in css
    assert "#rk-tbl.sheet" in css
    assert "#sc-tbl.sheet" in css
    assert "#earn-tbl.sheet" in css


def test_ranking_table_matches_sheet() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="rk-tbl" class="sheet"' in html
    src = (ROOT / "static/js/ranking.js").read_text(encoding="utf-8")
    assert "<thead>" in src
    assert "pin1" in src
    assert "rkSession" in src
    assert "日数</th>" not in src
    assert "class='l'>発表</th>" not in src
    assert 'under(`<span class="sub">' in src
    assert "pinCols($(\"#rk-tbl\"), 2)" in src


def test_screener_table_matches_sheet() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="sc-tbl" class="sheet"' in html
    src = (ROOT / "static/js/screener.js").read_text(encoding="utf-8")
    assert "<thead>" in src
    assert "pin0" in src
    assert 'under(`<span class="sub">' in src
    assert "scEsc" in src
    assert "<small>" not in src
    assert "pinCols($(\"#sc-tbl\"), 1)" in src


def test_earnings_keeps_sheet_and_shared_pin() -> None:
    src = (ROOT / "static/js/earnings.js").read_text(encoding="utf-8")
    assert 'id="earn-tbl" class="sheet"' in src
    assert "pinEarn" in src
    assert "pinCols($(\"#earn-tbl\"), 3)" in src
    api = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    assert "function pinCols(table, n)" in api
    assert "const under =" in api

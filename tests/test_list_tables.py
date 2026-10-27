"""一覧表（決算速報・スクリーナー・ランキング）が table.sheet を共有し、スマホ幅に収まる（KAB-196 / KAB-197）。"""
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
    assert "table.sheet td.name, table.sheet th.name { max-width:8em; }" in css


def test_ranking_table_matches_sheet() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="rk-tbl" class="sheet"' in html
    assert 'data-dir="up"' in html
    assert 'data-dir="down"' in html
    assert ">上昇率</button>" in html
    assert ">下落率</button>" in html
    assert 'data-kind="earnings-perf"' not in html
    src = (ROOT / "static/js/ranking.js").read_text(encoding="utf-8")
    assert "<thead>" in src
    assert "pin1" in src
    assert "rkSession" in src
    assert "rkView" in src
    assert "rkElapsed" in src
    assert ">経過日数</th>" in src
    assert ">日数</th>" not in src
    assert "class='l'>発表</th>" not in src
    assert ">対TOPIX</th>" not in src
    assert ">保有期間</th>" not in src
    assert "保有中" not in src
    assert 'under(`<span class="sub">(対TOPIX)</span>`)' in src
    assert 'under(`<span class="sub">' in src
    assert "shortName(raw)" in src
    assert "pinCols($(\"#rk-tbl\"), 2)" in src
    assert '["対TOPIX"' not in src
    assert "return_to_next > 0" in src
    assert "return_to_next < 0" in src
    assert "上昇率ランキングTOP" in src
    assert "下落率ランキングTOP" in src
    assert "パフォーマンスランキングTOP" not in src
    assert "計測方法は決算発表後初値〜" in src
    assert "※場中決算は翌営業日初値" in src
    assert "（次の決算の前日終値まで）" not in src
    assert "決算発表後パフォーマンス 上位" not in src
    assert "この月の上昇はありません" in src
    assert "この月の下落はありません" in src


def test_screener_table_matches_sheet() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="sc-tbl" class="sheet"' in html
    src = (ROOT / "static/js/screener.js").read_text(encoding="utf-8")
    assert "<thead>" in src
    assert "pin0" in src
    assert 'under(`<span class="sub">' in src
    assert "scEsc" in src
    assert "shortName(raw)" in src
    assert ">安値比</th>" in src
    assert ">高値比</th>" in src
    assert "52週安値比</th>" not in src
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
    assert "const shortName =" in api
    assert "const shortName" not in src

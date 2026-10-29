"""一覧表（決算速報・スクリーナー・ランキング・保有者）が table.sheet を共有し、スマホ幅に収まる（KAB-196 / KAB-197 / KAB-270）。"""
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
    assert "#rk-tbl th.grp { white-space:nowrap; }" in css
    assert "#earn-tbl .pin2, #sc-tbl .pin0, #hd-feed .pin2, #hd-holdings .pin0, #st-holders-large .pin0, #hd-activity .pin1 { box-shadow:inset -1px 0 0 var(--border); }" in css
    assert "#rk-tbl .pin1" not in css
    assert "#hd-feed.sheet" in css
    assert "#tab-holders .tbl-wrap" in css


def test_ranking_table_matches_sheet() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert 'id="rk-tbl" class="sheet"' in html
    assert 'data-dir="up"' in html
    assert 'data-dir="down"' in html
    assert ">上昇率</button>" in html
    assert ">下落率</button>" in html
    assert 'id="rk-subs"' in html
    assert 'data-rksub="perf"' in html
    assert 'data-rksub="pts"' in html
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
    assert "<th class='pin0'>順位</th><th class='l name pin1'>銘柄</th>" in src
    assert src.find("pin1'>銘柄") < src.find(">市場</th>") < src.find(">業種</th>")
    assert '["銘柄", X.name, "left"], ["市場", X.mkt, "left"]' in src
    assert '["対TOPIX"' not in src
    assert "return_to_next > 0" in src
    assert "return_to_next < 0" in src
    assert "${RK_DIR[rkDir]}ランキングTOP" in src
    assert 'up: "上昇率"' in src
    assert 'down: "下落率"' in src
    assert ">騰落率" in src
    assert '["騰落率", X.perf, "right"]' in src
    assert ">パフォーマンス" not in src
    assert "パフォーマンスランキングTOP" not in src
    assert "計測方法は決算発表後初値〜" in src
    assert "※場中決算は翌営業日初値" in src
    assert 'g.fillText("計測方法は決算発表後初値〜"' not in src
    assert 'g.fillText("※場中決算は翌営業日初値"' not in src
    assert "（次の決算の前日終値まで）" not in src
    assert "決算発表後パフォーマンス 上位" not in src
    assert "この月の${RK_DIR[rkDir]}はありません" in src


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
    assert "latestPeriodLabel" in src
    assert "qb-act" in src
    assert "metricCells(latest" in src
    assert "metricCells(r, r.period)" in src

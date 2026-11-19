"""銘柄タブの共通ヘッダーと指標（KAB-178。docs/screen/stock.md）。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.db import SessionLocal
from app.models import DailyPrice, EarningsDatePub, Statement, Stock

ROOT = Path(__file__).resolve().parents[2]


def _st(no: str, day: str, doc: str, **raw) -> Statement:
    return Statement(disclosure_no=no, code="99990", disclosed_date=date.fromisoformat(day),
                     disclosed_time="15:30:00", type_of_document=doc, raw_json=json.dumps(raw))


def _seed() -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99990", name="テスト工業", market="プライム", sector33="金属製品"))
        db.add(Stock(code="99980", name="株価なし"))
        db.add_all([
            DailyPrice(code="99990", date=date(2026, 10, 22), close=1000.0, adj_close=500.0, market_cap=24500.0),
            # 1:2 の分割の翌日。前日比は調整後の終値で出す（+2%）
            DailyPrice(code="99990", date=date(2026, 10, 23), close=510.0, adj_close=510.0, market_cap=24990.0),
        ])
        db.add_all([
            _st("1", "2026-05-12", "FYFinancialStatements_Consolidated_JP",
                NxFEPS="40.0", NxFDivAnn="15.0", BPS="600.0", ROE="0.071", EqAR="0.45"),
            _st("2", "2026-08-05", "1QFinancialStatements_Consolidated_JP",
                FEPS="40.0", FDivAnn="15.0", BPS="", EqAR="0.46"),
            # 予想修正だけの開示は値があるときだけ使う
            _st("3", "2026-09-30", "EarnForecastRevision", FEPS="51.0"),
            _st("4", "2026-10-01", "DividendForecastRevision", FDivAnn="", FEPS=""),
        ])
        db.add_all([
            EarningsDatePub(code="99990", fq_name="2Q", pub_date=date(2026, 10, 1), sch_date=date(2026, 11, 10),
                            fye="0331", co_name="テスト工業"),
            # 同じ四半期は新しい pub_date が勝つ
            EarningsDatePub(code="99990", fq_name="2Q", pub_date=date(2026, 10, 20), sch_date=date(2026, 11, 7),
                            fye="0331", co_name="テスト工業"),
            EarningsDatePub(code="99980", fq_name="2Q", pub_date=date(2026, 10, 20), sch_date=None,
                            fye="0331", co_name="株価なし"),
        ])
        db.commit()
    finally:
        db.close()


def test_overview(c) -> None:
    _seed()
    r = c.get("/api/stock/9999/overview?today=2026-10-26")
    assert r.status_code == 200
    d = r.json()
    assert (d["code"], d["name"], d["market"], d["sector33"]) == ("99990", "テスト工業", "プライム", "金属製品")
    q = d["quote"]
    assert q["date"] == "2026-10-23" and q["close"] == 510.0 and q["market_cap"] == 24990.0
    assert abs(q["change_pct"] - 0.02) < 1e-9 and abs(q["change"] - 10.0) < 1e-6
    assert d["next_earnings"] == {"date": "2026-11-07", "fq": "2Q"}
    m = d["metrics"]
    assert m["per"] == 510.0 / 51.0
    assert m["pbr"] == 510.0 / 600.0
    assert m["dividend_yield"] == 15.0 / 510.0
    assert m["roe"] == 0.071 and m["equity_ratio"] == 0.46

    assert c.get("/api/stock/9999/overview?today=2026-11-08").json()["next_earnings"] is None

    d = c.get("/api/stock/9998/overview?today=2026-10-26").json()
    assert d["quote"] is None
    assert d["next_earnings"] == {"date": None, "fq": None}
    assert d["metrics"] == {"per": None, "pbr": None, "dividend_yield": None, "roe": None, "equity_ratio": None}

    assert c.get("/api/stock/1234/overview").status_code == 404
    assert c.get("/api/stock/xx/overview").status_code == 400


def test_fy_actual_without_forecast_hides_per() -> None:
    from app.api.stock import _forecast

    fy = _st("9", "2026-05-12", "FYFinancialStatements_Consolidated_JP", NxFEPS="", FEPS="")
    q3 = _st("8", "2026-02-10", "3QFinancialStatements_Consolidated_JP", FEPS="30.0")
    assert _forecast([(fy, json.loads(fy.raw_json)), (q3, json.loads(q3.raw_json))], "EPS")[0] is None


def test_overview_after_split(c) -> None:
    """本決算のあとに 1:10 の分割。BPS・予想EPS・配当予想を分割後の株数にそろえる（KAB-406）。"""
    db = SessionLocal()
    try:
        db.add(Stock(code="99970", name="分割工業"))
        db.add_all([
            DailyPrice(code="99970", date=date(2026, 5, 12), close=40000.0, adj_factor=1.0),
            DailyPrice(code="99970", date=date(2026, 6, 29), close=4000.0, adj_factor=0.1),
            DailyPrice(code="99970", date=date(2026, 10, 23), close=4100.0, adj_factor=1.0),
        ])
        fy = _st("97", "2026-05-12", "FYFinancialStatements_Consolidated_JP",
                 NxFEPS="400.0", NxFDivAnn="100.0", BPS="6000.0")
        fy.code, fy.type_of_period, fy.fiscal_year_end = "99970", "FY", date(2026, 3, 31)
        db.add(fy)
        db.commit()
    finally:
        db.close()
    m = c.get("/api/stock/9997/overview?today=2026-10-26").json()["metrics"]
    assert abs(m["pbr"] - 4100.0 / 600.0) < 1e-9
    assert abs(m["per"] - 4100.0 / 40.0) < 1e-9
    assert abs(m["dividend_yield"] - 10.0 / 4100.0) < 1e-9


def test_spa_stock_sub_tabs() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    tabs = (ROOT / "static/js/tabs.js").read_text(encoding="utf-8")
    js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
    for sub in ("summary", "price", "news", "holders"):
        assert f'data-sub="{sub}"' in html and f'id="st-sub-{sub}"' in html
    assert 'data-sub="decks"' not in html and 'id="st-sub-decks"' in html
    assert 'id="st-deck-list"' not in html and "← ニュース" in html and "← 説明資料" not in html
    st_tabs = html[html.index('id="st-tabs"') - 80:html.index('id="st-tabs"') + 80]
    assert 'class="nav-tabs"' in st_tabs and "fin-tabs" not in st_tabs
    assert 'id="st-quote"' in html and 'id="st-news" class="sheet"' in html and 'id="st-back"' in html
    assert 'id="st-filing"' not in html
    assert "/api/stock/${code}/overview" in js
    assert 'writeRoute("stock", stockRoutePath(), "stock-news", true)' in js
    assert '"popstate"' in tabs
    assert "history.pushState" in tabs


def test_chart_tab_moved_into_stock_price() -> None:
    """チャートタブは銘柄の株価タブになった（KAB-181）。古い #chart/{code} は株価タブへ読み替える。"""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    tabs = (ROOT / "static/js/tabs.js").read_text(encoding="utf-8")
    js = (ROOT / "static/js/stock-price.js").read_text(encoding="utf-8")
    assert 'data-tab="chart"' not in html and 'id="tab-chart"' not in html
    assert not (ROOT / "static/js/chart-tab.js").exists()
    sub = html[html.index('id="st-sub-price"'):html.index('id="st-sub-news"')]
    for el in ('id="tf"', 'id="mas"', 'id="chart-sub"', 'id="chart-fund"', 'id="chart-hl"', 'id="chart-band"',
               'id="chart-log"', 'id="rel"',
               'id="chart"', 'id="perf-tbl" class="sheet"', 'class="evt-toggles"'):
        assert el in sub
    assert 'onclick="loadChart()"' not in html
    assert '"chart"' not in tabs.split("const TABS")[1].split(";")[0]
    assert 'if(tab === "chart")' in tabs and "`${code}/price`" in tabs
    assert 'showStock(code, "price", "", from)' in js
    assert "#stock/${c.code}/price" in (ROOT / "static/js/supply.js").read_text(encoding="utf-8")


def test_summary_reuses_earnings_tables() -> None:
    """概要の業績は決算速報と同じ表（KAB-179）。決算速報の .ftab[data-fin] と切り替えが混ざらない。"""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
    sub = html[html.index('id="st-sub-summary"'):html.index('id="st-sub-price"')]
    for t in ("cum", "q", "prog", "rev"):
        assert f'data-stfin="{t}"' in sub and f'class="sheet fin" id="st-{t}-tbl"' in sub
    assert "data-fin=" not in sub
    assert "/api/earnings/detail/${code}`" in js
    for fn in ("renderFin($(\"#st-cum-tbl\")", "renderFin($(\"#st-q-tbl\")", "renderProg(", "renderRev("):
        assert fn in js

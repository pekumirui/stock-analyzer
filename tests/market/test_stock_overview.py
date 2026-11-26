"""銘柄タブの共通ヘッダーと指標（KAB-178。docs/screen/stock.md）。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tests.market.jquants_columns import fy_columns
from app.db import SessionLocal
from app.models import DailyPrice, EarningsDatePub, Statement, Stock

ROOT = Path(__file__).resolve().parents[2]


def _st(no: str, day: str, doc: str, **raw) -> Statement:
    d = date.fromisoformat(day)
    period = doc[:2] if "FinancialStatements" in doc else "FY"
    closed = doc.startswith("FYFinancialStatements") or d.month <= 3
    fy_end = date(d.year if closed else d.year + 1, 3, 31)
    return Statement(disclosure_no=no, code="99990", disclosed_date=d, disclosed_time="15:30:00",
                     type_of_document=doc, type_of_period=period, fiscal_year_end=fy_end,
                     raw_json=json.dumps(raw), **fy_columns(raw))


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


def test_fy_actual_without_forecast_hides_per(c) -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99940", name="予想なし工業"))
        db.add(DailyPrice(code="99940", date=date(2026, 10, 23), close=1000.0, adj_factor=1.0))
        q3 = _st("8", "2026-02-10", "3QFinancialStatements_Consolidated_JP", FEPS="30.0")
        fy = _st("9", "2026-05-12", "FYFinancialStatements_Consolidated_JP", NxFEPS="", FEPS="")
        q3.type_of_period, fy.type_of_period = "3Q", "FY"
        for r in (q3, fy):
            r.code, r.fiscal_year_end = "99940", date(2026, 3, 31)
        db.add_all([q3, fy])
        db.commit()
    finally:
        db.close()
    assert c.get("/api/stock/9994/overview").json()["metrics"]["per"] is None


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


def test_withdrawn_dividend_forecast_hides_yield(c) -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99960", name="取り下げ工業"))
        db.add(DailyPrice(code="99960", date=date(2026, 10, 23), close=1000.0, adj_factor=1.0))
        rows = [
            _st("961", "2026-05-12", "FYFinancialStatements_Consolidated_JP", NxFEPS="50.0", NxFDivAnn="25.0"),
            _st("962", "2026-08-14", "EarnForecastRevision", FDivAnn="", FEPS=""),
        ]
        for r in rows:
            r.code = "99960"
        db.add_all(rows)
        db.commit()
        assert c.get("/api/stock/9996/overview").json()["metrics"]["dividend_yield"] == 25.0 / 1000.0
        rows[1].withdrawn_forecasts = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
                                       "div_annual")
        db.commit()
    finally:
        db.close()
    m = c.get("/api/stock/9996/overview").json()["metrics"]
    assert m["dividend_yield"] is None and m["per"] is None


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


def test_same_day_reports_pick_the_newest_period(c) -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99950", name="訂正工業"))
        db.add(DailyPrice(code="99950", date=date(2026, 11, 12), close=1000.0, adj_factor=1.0))
        old_1q = _st("20261112576239", "2026-11-12", "1QFinancialStatements_Consolidated_JP",
                     BPS="1800.0", EqAR="0.60")
        new_2q = _st("20261112576228", "2026-11-12", "2QFinancialStatements_Consolidated_JP",
                     BPS="1100.0", EqAR="0.64")
        old_1q.type_of_period, old_1q.fiscal_year_end = "1Q", date(2026, 3, 31)
        new_2q.type_of_period, new_2q.fiscal_year_end = "2Q", date(2027, 3, 31)
        for r in (old_1q, new_2q):
            r.code = "99950"
        db.add_all([old_1q, new_2q])
        db.commit()
    finally:
        db.close()
    m = c.get("/api/stock/9995/overview").json()["metrics"]
    assert m["pbr"] == 1000.0 / 1100.0 and m["equity_ratio"] == 0.64


def test_per_and_pbr_use_shares_after_buyback(c) -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99930", name="買戻し工業"))
        db.add(DailyPrice(code="99930", date=date(2026, 11, 25), close=5710.0, adj_factor=1.0))
        rows = [
            _st("20260420506537", "2026-04-27", "FYFinancialStatements_Consolidated_JP", BPS="3340.44",
                ShEq="", ShOutFY="199954180", TrShFY="1966478", NxFNp="70000000000", NxFEPS="353.31"),
            _st("20261028557458", "2026-10-30", "EarnForecastRevision", FNP="75000000000", FEPS="435.5"),
            _st("20261028557709", "2026-10-30", "2QFinancialStatements_Consolidated_JP", BPS="",
                ShEq="449691000000", ShOutFY="166454180", TrShFY="1968286", AvgSh="178842968",
                NP="31321000000", EPS="175.13", FNP="75000000000", FEPS="435.5"),
            _st("20261112576755", "2026-11-13", "2QFinancialStatements_Consolidated_JP", BPS="", ShEq="",
                ShOutFY="", TrShFY="", FNP="", FEPS=""),
        ]
        for r in rows:
            r.code = "99930"
        db.add_all(rows)
        db.commit()
    finally:
        db.close()
    m = c.get("/api/stock/9993/overview").json()["metrics"]
    shares = 166454180 - 1968286
    assert abs(m["per"] - 5710.0 / round(75000000000 / shares, 2)) < 1e-9 and round(m["per"], 1) == 12.5
    assert abs(m["pbr"] - 5710.0 / round(449691000000 / shares, 2)) < 1e-9 and round(m["pbr"], 2) == 2.09


def test_per_keeps_company_eps_when_eps_is_not_profit_over_shares(c) -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="99920", name="優先株工業"))
        db.add(DailyPrice(code="99920", date=date(2026, 11, 25), close=1000.0, adj_factor=1.0))
        q2 = _st("9921", "2026-11-10", "2QFinancialStatements_Consolidated_JP", ShOutFY="10000000",
                 NP="500000000", EPS="30.0", AvgSh="10000000", FNP="1000000000", FEPS="80.0")
        q2.code = "99920"
        db.add(q2)
        db.commit()
    finally:
        db.close()
    assert c.get("/api/stock/9992/overview").json()["metrics"]["per"] == 1000.0 / 80.0

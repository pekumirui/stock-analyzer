"""Q単独・決算 analytics と /api/earnings。合成デモは使わず、小さな Statement フィクスチャだけ。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-analytics-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.api.earnings import prefetch_list_history  # noqa: E402
from app.config import EARNINGS_PARAMS as EP  # noqa: E402
from app.market.analytics import (  # noqa: E402
    calendar_label,
    calendar_span,
    compute_earnings_events,
    cumulative_rows,
    disclosure_state,
    earnings_detail,
    find_statement,
    growth_cell,
    growth_map,
    half_forecast_status,
    indicators_current,
    metrics,
    period_label,
    prev_quarter,
    progress_map,
    progress_rows,
    quarter_summary,
    qoq_map,
    quarterly_rows,
    shift_year,
    single_from,
    single_metrics,
    statement_history,
)
from app.db import SessionLocal, init_db  # noqa: E402
from app.db import upsert  # noqa: E402
from app.main import app  # noqa: E402
from app.models import DailyPrice, EarningsEvent, Indicator, Statement, Stock, Topix  # noqa: E402

DOC = "FYFinancialStatements_Consolidated_JP"
CODE = "72030"
FY26 = date(2026, 3, 31)
FY25 = date(2025, 3, 31)
TOL = 45


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _stmt(
    no: str,
    period: str,
    fy_end: date,
    disclosed: date,
    sales: float,
    *,
    op: float | None = None,
    ordinary: float | None = None,
    profit: float | None = None,
    eps: float | None = None,
    is_revision: bool = False,
    doc: str = DOC,
    f2_op: float | None = None,
    fy_op: float | None = None,
) -> Statement:
    return Statement(
        disclosure_no=no,
        code=CODE,
        disclosed_date=disclosed,
        disclosed_time="15:00",
        type_of_period=period,
        type_of_document=doc,
        fiscal_year_end=fy_end,
        net_sales=sales,
        operating_profit=op if op is not None else sales / 10,
        ordinary_profit=ordinary if ordinary is not None else sales / 12,
        profit=profit if profit is not None else sales / 20,
        eps=eps if eps is not None else sales / 1000,
        is_revision=is_revision,
        f2_operating_profit=f2_op,
        fy_operating_profit=fy_op,
    )


def _hist() -> list[Statement]:
    return [
        _stmt("p1q", "1Q", FY25, date(2024, 8, 1), 80),
        _stmt("p2q", "2Q", FY25, date(2024, 11, 1), 180),
        _stmt("p3q", "3Q", FY25, date(2025, 2, 1), 270),
        _stmt("pfy", "FY", FY25, date(2025, 5, 1), 360),
        _stmt("c1q", "1Q", FY26, date(2025, 8, 1), 100),
        _stmt("c2q", "2Q", FY26, date(2025, 11, 1), 250, f2_op=30, fy_op=70),
        _stmt("c3q", "3Q", FY26, date(2026, 2, 1), 400),
        _stmt("cfy", "FY", FY26, date(2026, 5, 1), 600),
    ]


def test_shift_year_and_labels() -> None:
    if shift_year(date(2026, 3, 31), -1) != date(2025, 3, 31):
        _fail("shift_year")
    if shift_year(date(2024, 2, 29), -1) != date(2023, 2, 28):
        _fail("leap day")
    if period_label(None) != "–":
        _fail("empty period label")
    if period_label(FY26, "2Q") != "FY26.3_2Q":
        _fail(period_label(FY26, "2Q"))
    if period_label(FY26, "FY") != "FY26.3":
        _fail("FY label")
    if period_label(FY26, "FY", quarterly=True) != "FY26.3_4Q":
        _fail("FY quarterly label")
    span = calendar_span(FY26, "1Q")
    if span != (date(2025, 4, 1), date(2025, 6, 30)):
        _fail(f"1Q span {span}")
    if calendar_span(date(2026, 5, 31), "3Q") != (date(2025, 12, 1), date(2026, 2, 28)):
        _fail(f"span ending in February: {calendar_span(date(2026, 5, 31), '3Q')}")
    if calendar_span(FY26, "5Q") is not None:
        _fail("5Q span")
    if calendar_label(FY26, "2Q", quarterly=True) != "25.09":
        _fail(calendar_label(FY26, "2Q", quarterly=True))
    if calendar_label(date(2026, 8, 31), "2Q", quarterly=True) != "26.02":
        _fail(calendar_label(date(2026, 8, 31), "2Q", quarterly=True))
    if calendar_label(date(2026, 8, 31), "4Q", quarterly=True) != "26.08":
        _fail(calendar_label(date(2026, 8, 31), "4Q", quarterly=True))
    if calendar_label(FY26, "2Q") != "26.3":
        _fail(calendar_label(FY26, "2Q"))
    if calendar_label(FY26, "3Q") != "26.3":
        _fail(calendar_label(FY26, "3Q"))
    if calendar_label(FY26, "FY") != "26.3":
        _fail(calendar_label(FY26, "FY"))
    if calendar_label(FY26) != "26.3":
        _fail(calendar_label(FY26))
    if calendar_label(FY26, "5Q") != "26.3":
        _fail(calendar_label(FY26, "5Q"))
    if prev_quarter(FY26, "2Q") != (FY26, "1Q"):
        _fail("prev 2Q")
    if prev_quarter(FY26, "1Q") != (FY25, "FY"):
        _fail("prev 1Q")
    if prev_quarter(FY26, "5Q") is not None:
        _fail("prev 5Q")
    if prev_quarter(None, "2Q") is not None:
        _fail("prev none")


def test_growth_and_metrics() -> None:
    hist = _hist()
    q2 = hist[5]
    m = metrics(q2)
    if m is None or m["net_sales"] != 250:
        _fail(m)
    cell = growth_cell(250, 180, signed=False)
    if cell != {"pct": 38.9, "state": "up"}:
        _fail(f"yoy sales {cell}")
    if growth_cell(-10, 20)["state"] != "turn_loss":
        _fail("turn_loss")
    if growth_cell(10, -5)["state"] != "turn_profit":
        _fail("turn_profit")
    if growth_cell(-1, -2)["state"] != "loss":
        _fail("loss")
    if growth_cell(None, 1)["state"] != "na":
        _fail("na")
    gm = growth_map({"net_sales": 250, "operating_profit": 25, "ordinary_profit": 20,
                     "profit": 12, "eps": 0.25},
                    {"net_sales": 180, "operating_profit": 18, "ordinary_profit": 15,
                     "profit": 9, "eps": 0.18})
    if gm is None or gm["net_sales"]["pct"] != 38.9:
        _fail(f"growth_map {gm}")
    if growth_map(None, m) is not None:
        _fail("empty growth_map")


def test_single_quarter_from_cumulative() -> None:
    hist = _hist()
    q1 = next(s for s in hist if s.disclosure_no == "c1q")
    q2 = next(s for s in hist if s.disclosure_no == "c2q")
    q4 = next(s for s in hist if s.disclosure_no == "cfy")
    if single_from(hist, None, TOL) is not None:
        _fail("none stmt")
    q1s = single_from(hist, q1, TOL)
    if q1s is None or q1s["net_sales"] != 100:
        _fail(f"1Q is cumulative {q1s}")
    q2s = single_from(hist, q2, TOL)
    if q2s is None or q2s["net_sales"] != 150:
        _fail(f"2Q single {q2s}")
    q4s = single_from(hist, q4, TOL)
    if q4s is None or q4s["net_sales"] != 200:
        _fail(f"4Q single {q4s}")
    got = single_metrics(hist, FY26, "2Q", TOL)
    if got is None or got["net_sales"] != 150:
        _fail(f"single_metrics {got}")
    if find_statement(hist, None, "2Q", TOL) is not None:
        _fail("find none")
    qoq = qoq_map(hist, q2, TOL)
    if qoq is None or qoq["net_sales"]["pct"] != 50.0:
        _fail(f"qoq {qoq}")


def test_disclosure_and_rows() -> None:
    hist = _hist()
    q2 = next(s for s in hist if s.disclosure_no == "c2q")
    if disclosure_state(hist, q2) != "first":
        _fail(disclosure_state(hist, q2))
    restated = _stmt("c2q-b", "2Q", FY26, date(2025, 11, 2), 260)
    dup = _stmt("c2q-c", "2Q", FY26, date(2025, 11, 3), 260)
    hist2 = hist + [restated, dup]
    if disclosure_state(hist2, restated) != "restated":
        _fail("restated")
    if disclosure_state(hist2, dup) != "duplicate":
        _fail("duplicate")
    cum = cumulative_rows(hist, q2, 2, TOL)
    kinds = [r["kind"] for r in cum]
    if "actual_current" not in kinds:
        _fail(kinds)
    cur = next(r for r in cum if r["kind"] == "actual_current")
    if cur["values"]["net_sales"] != 250:
        _fail(cur)
    if cur["growth"]["net_sales"]["pct"] != 38.9:
        _fail(cur["growth"])
    if cur["range"] != "26.3":
        _fail(cur["range"])
    h = next(r for r in cum if r["kind"] == "forecast_h")
    if h["range"] != "26.3":
        _fail(h["range"])
    fyf = next(r for r in cum if r["kind"] == "forecast_fy")
    if fyf["range"] != "26.3":
        _fail(fyf["range"])
    fy = next(s for s in hist if s.disclosure_no == "cfy")
    fy.raw_json = json.dumps({"NxtFYEn": "2027-03-31", "NxFSales2Q": "350", "NxFSales": "700",
                              "NxFOP": "", "NxFNp": "50"})
    got = [(r["kind"], r["label"], r["values"]["net_sales"], (r["growth"] or {}).get("net_sales"))
           for r in cumulative_rows(hist, fy, 2, TOL)]
    if got != [
        ("actual", "FY25.3", 360, None),
        ("actual_current", "FY26.3", 600, {"pct": 66.7, "state": "up"}),
        ("forecast_h", "FY27.3", 350, {"pct": 40.0, "state": "up"}),
        ("forecast_fy", "FY27.3", 700, {"pct": 16.7, "state": "up"}),
    ]:
        _fail(f"fy forecast rows {got}")
    fy.raw_json = None
    if [r["kind"] for r in cumulative_rows(hist, fy, 2, TOL)] != ["actual", "actual_current"]:
        _fail("fy without raw_json must not invent forecasts")
    qtr = quarterly_rows(hist, q2, 4, TOL)
    got = [(r["label"], r["values"]["net_sales"], (r["growth"] or {}).get("net_sales")) for r in qtr]
    if got != [
        ("FY25.3_3Q", 90, None),
        ("FY25.3_4Q", 90, None),
        ("FY26.3_1Q", 100, {"pct": 25.0, "state": "up"}),
        ("FY26.3_2Q", 150, {"pct": 50.0, "state": "up"}),
    ]:
        _fail(f"quarterly singles {got}")


def test_half_forecast_status() -> None:
    hist = _hist()
    q1 = next(s for s in hist if s.disclosure_no == "c1q")
    q2 = next(s for s in hist if s.disclosure_no == "c2q")
    fy = next(s for s in hist if s.disclosure_no == "cfy")
    if half_forecast_status(q2, cumulative_rows(hist, q2, 2, TOL)) is not None:
        _fail("2Q has no half forecast status")
    q1.raw_json = None
    if half_forecast_status(q1, cumulative_rows(hist, q1, 2, TOL)) != "unknown":
        _fail("1Q without raw keys is unknown")
    q1.raw_json = json.dumps({"FSales2Q": "", "FOP2Q": "", "FSales": "500"})
    q1.fy_operating_profit = 70
    if half_forecast_status(q1, cumulative_rows(hist, q1, 2, TOL)) != "fy_only":
        _fail("1Q with empty half and full-year forecast is fy_only")
    q1.fy_operating_profit = None
    if half_forecast_status(q1, cumulative_rows(hist, q1, 2, TOL)) != "none":
        _fail("1Q with no forecasts is none")
    q1.f2_operating_profit = 30
    if half_forecast_status(q1, cumulative_rows(hist, q1, 2, TOL)) != "given":
        _fail("1Q with half forecast is given")
    fy.raw_json = json.dumps({"NxtFYEn": "2027-03-31", "NxFSales2Q": "", "NxFSales": "700"})
    if half_forecast_status(fy, cumulative_rows(hist, fy, 2, TOL)) != "fy_only":
        _fail("FY uses next-year half keys")
    fy.raw_json = json.dumps({"NxtFYEn": "2027-03-31", "NxFSales2Q": "350", "NxFSales": "700"})
    if half_forecast_status(fy, cumulative_rows(hist, fy, 2, TOL)) != "given":
        _fail("FY with next-year half forecast is given")


def test_progress() -> None:
    if progress_map({"net_sales": 50, "operating_profit": -5, "ordinary_profit": 1, "profit": None, "eps": 1},
                    {"net_sales": 200, "operating_profit": 20, "ordinary_profit": 0, "profit": 10, "eps": None}) \
            != {"net_sales": 25.0, "operating_profit": -25.0, "ordinary_profit": None, "profit": None, "eps": None}:
        _fail("progress_map")
    hist = _hist()
    q2 = next(s for s in hist if s.disclosure_no == "c2q")
    q2.fy_net_sales = 500
    got = [(r["kind"], r["range"], r["progress"]["net_sales"], r["base"]["net_sales"])
           for r in progress_rows(hist, q2, 2, TOL)]
    if got != [("actual", "25.3", 50.0, 360), ("actual_current", "26.3", 50.0, 500)]:
        _fail(f"progress rows {got}")
    fy = next(s for s in hist if s.disclosure_no == "cfy")
    if progress_rows(hist, fy, 2, TOL) != []:
        _fail("fy has no progress rows")
    summ = quarter_summary(None, q2, hist=[s for s in hist if s.disclosed_date <= q2.disclosed_date])
    if summ["progress"]["cur"]["net_sales"] != 50.0 or summ["progress"]["prev"]["net_sales"] != 50.0:
        _fail(summ["progress"])
    if quarter_summary(None, fy, hist=hist)["progress"] is not None:
        _fail("fy summary progress")


def _seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, Stock, [{"code": CODE, "name": "トヨタ自動車", "product_category": "011"}])
        rows = []
        for s in _hist():
            rows.append({
                "disclosure_no": s.disclosure_no,
                "code": s.code,
                "disclosed_date": s.disclosed_date,
                "disclosed_time": s.disclosed_time,
                "type_of_period": s.type_of_period,
                "type_of_document": s.type_of_document,
                "fiscal_year_end": s.fiscal_year_end,
                "net_sales": s.net_sales,
                "operating_profit": s.operating_profit,
                "ordinary_profit": s.ordinary_profit,
                "profit": s.profit,
                "eps": s.eps,
                "is_revision": s.is_revision,
                "f2_operating_profit": s.f2_operating_profit,
                "fy_operating_profit": s.fy_operating_profit,
            })
        upsert(db, Statement, rows)
        px = []
        tp = []
        start = date(2025, 10, 31)
        for i in range(10):
            d = start + timedelta(days=i)
            close = 1000.0 + i
            px.append({
                "code": CODE, "date": d,
                "open": close - 0.5, "high": close, "low": close - 0.5, "close": close,
                "volume": 1, "turnover": 1, "adj_factor": 1.0,
                "adj_open": close - 0.5, "adj_high": close, "adj_low": close - 0.5,
                "adj_close": close, "adj_volume": 1, "market_cap": 1,
            })
            tp.append({"date": d, "open": 2000, "high": 2000, "low": 2000, "close": 2000})
        upsert(db, DailyPrice, px)
        upsert(db, Topix, tp)
        compute_earnings_events(db)
        ev = db.get(EarningsEvent, (CODE, date(2025, 11, 1)))
        if ev is None or ev.return_1d is None:
            _fail(f"earnings event {ev}")
        # 15:00 は大引け（15:30）前なので当日の終値で買い、翌営業日の終値まで
        want = round((1002 / 1001 - 1) * 100, 2)
        if ev.return_1d != want:
            _fail(f"return_1d {ev.return_1d} != {want} base={ev.base_date} {ev.base_price}")
        hist = statement_history(db, CODE, date(2026, 5, 1), 800)
        if len(hist) < 8:
            _fail(f"history {len(hist)}")
        q2 = next(s for s in hist if s.disclosure_no == "c2q")
        detail = earnings_detail(db, q2)
        if detail["label"] != "FY26.3_2Q":
            _fail(detail["label"])
        cur = next(r for r in detail["quarterly"] if r["label"] == "FY26.3_2Q")
        if cur["values"]["net_sales"] != 150:
            _fail(cur)
    finally:
        db.close()


def test_earnings_api() -> None:
    _seed()
    c = TestClient(app)
    r = c.get(f"/api/earnings/detail/{CODE}", params={"disclosure_no": "c2q"})
    if r.status_code != 200:
        _fail(f"detail {r.status_code} {r.text}")
    body = r.json()
    if body["code"] != CODE or body["period"] != "2Q":
        _fail(body)
    q2 = next(row for row in body["quarterly"] if row["label"] == "FY26.3_2Q")
    if q2["values"]["net_sales"] != 150:
        _fail(q2)
    for params, want in (
        ({"disclosure_no": "missing"}, "cfy"),
        ({}, "cfy"),
        ({"date": "2025-11-01"}, "c2q"),
    ):
        r = c.get(f"/api/earnings/detail/{CODE}", params=params)
        if r.status_code != 200 or r.json()["disclosure_no"] != want:
            _fail(f"detail {params}: {r.status_code} {r.text[:200]}")
    for q in ("とよた", "トヨタ", CODE[:4], "７２０３"):
        hits = c.get("/api/stocks/search", params={"q": q}).json()
        if [h["code"] for h in hits] != [CODE]:
            _fail(f"search {q}: {hits}")
    if c.get("/api/stocks/search", params={"q": " "}).json() != []:
        _fail("blank search must be empty")
    r = c.get("/api/earnings/detail/99990")
    if r.status_code != 404:
        _fail(f"unknown code {r.status_code}")
    r = c.get(f"/api/earnings-performance/{CODE}")
    if r.status_code != 200:
        _fail(f"perf {r.status_code} {r.text}")
    perf = r.json()
    hit = next((e for e in perf if e["disclosed_date"] == "2025-11-01"), None)
    want = round((1002 / 1001 - 1) * 100, 2)
    if hit is None or hit["return_1d"] != want or hit["alpha_1d"] != want:
        _fail(f"perf 2025-11-01: {hit}")
    if [e["disclosed_date"] for e in perf] != sorted((e["disclosed_date"] for e in perf), reverse=True):
        _fail("performance must be newest first")

    day = date(2025, 11, 1)
    r = c.get("/api/earnings", params={"date": day.isoformat(), "today": "2026-05-02"})
    if r.status_code != 200:
        _fail(f"list {r.status_code} {r.text}")
    actuals = r.json()["actuals"]
    if len(actuals) != 1 or actuals[0]["disclosure_no"] != "c2q":
        _fail(f"list actuals: {actuals!r}")
    qoq = (actuals[0].get("qoq") or {}).get("net_sales") or {}
    yoy = (actuals[0].get("yoy") or {}).get("net_sales") or {}
    if qoq.get("pct") != 50.0 or yoy.get("pct") != 38.9:
        _fail(f"list qoq/yoy: {actuals[0]!r}")

    db = SessionLocal()
    try:
        codes = select(Statement.code).where(Statement.disclosed_date == day)
        batched = prefetch_list_history(db, codes, day)
        if list(batched) != [CODE]:
            _fail(f"prefetch codes: {list(batched)!r}")
        per = statement_history(db, CODE, day, EP["list_history_days"])
        if [s.disclosure_no for s in batched[CODE]] != [s.disclosure_no for s in per]:
            _fail(f"prefetch { [s.disclosure_no for s in batched[CODE]] } != {[s.disclosure_no for s in per]}")
    finally:
        db.close()


def test_upsert_mixed_columns_and_freshness() -> None:
    init_db()
    db = SessionLocal()
    try:
        d1, d2 = date(2030, 1, 6), date(2030, 1, 7)
        upsert(db, Topix, [
            {"date": d1, "open": 1, "close": 10},
            {"date": d2, "close": 20},
            {"close": 11, "date": d1},
            {"date": d2},
        ])
        a, b = db.get(Topix, d1), db.get(Topix, d2)
        if (a.open, a.close, b.open, b.close) != (1, 11, None, 20):
            _fail(f"upsert {a.open} {a.close} {b.open} {b.close}")
        db.query(Topix).filter(Topix.date >= d1).delete()
        db.commit()

        last = db.query(DailyPrice.date).order_by(DailyPrice.date.desc()).first()[0]
        db.query(Indicator).delete()
        db.commit()
        if indicators_current(db):
            _fail("indicators empty but reported current")
        upsert(db, Indicator, [{"code": CODE, "date": last, "close": 1.0}])
        if not indicators_current(db):
            _fail("indicators at last price date but reported stale")
    finally:
        db.close()


def main() -> None:
    test_shift_year_and_labels()
    test_growth_and_metrics()
    test_single_quarter_from_cumulative()
    test_disclosure_and_rows()
    test_half_forecast_status()
    test_progress()
    test_earnings_api()
    test_upsert_mixed_columns_and_freshness()
    print("ok: analytics")


if __name__ == "__main__":
    main()

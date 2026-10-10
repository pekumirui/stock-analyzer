"""Q単独・決算 analytics と /api/earnings。合成デモは使わず、小さな Statement フィクスチャだけ。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-analytics-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.analytics import (  # noqa: E402
    calendar_label,
    calendar_span,
    compute_earnings_events,
    cumulative_rows,
    disclosure_state,
    earnings_detail,
    find_statement,
    growth_cell,
    growth_map,
    indicators_current,
    metrics,
    period_label,
    prev_quarter,
    qoq_map,
    quarterly_rows,
    shift_year,
    single_from,
    single_metrics,
    statement_history,
)
from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest import upsert  # noqa: E402
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
    # 累計。Q単独は差: FY26 1Q=100, 2Q=150, 3Q=150, 4Q=200
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
    if calendar_label(FY26, "2Q", quarterly=True) != "25.07-09":
        _fail(calendar_label(FY26, "2Q", quarterly=True))
    if calendar_label(FY26, "FY") != "26.3期":
        _fail(calendar_label(FY26, "FY"))
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
    qtr = quarterly_rows(hist, q2, 4, TOL)
    got = [(r["label"], r["values"]["net_sales"], (r["growth"] or {}).get("net_sales")) for r in qtr]
    # 直近 4 四半期の単独値。前年同期がある 1Q / 2Q だけ前年比が付く
    if got != [
        ("FY25.3_3Q", 90, None),
        ("FY25.3_4Q", 90, None),
        ("FY26.3_1Q", 100, {"pct": 25.0, "state": "up"}),
        ("FY26.3_2Q", 150, {"pct": 50.0, "state": "up"}),
    ]:
        _fail(f"quarterly singles {got}")


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
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1, "turnover": 1, "adj_factor": 1.0,
                "adj_open": close, "adj_high": close, "adj_low": close,
                "adj_close": close, "adj_volume": 1, "market_cap": 1,
            })
            tp.append({"date": d, "open": 2000, "high": 2000, "low": 2000, "close": 2000})
        upsert(db, DailyPrice, px)
        upsert(db, Topix, tp)
        compute_earnings_events(db)
        ev = db.get(EarningsEvent, (CODE, date(2025, 11, 1)))
        if ev is None or ev.return_1d is None:
            _fail(f"earnings event {ev}")
        # 15:00 発表なので基準は 11-02 (1002)、1d は 11-03 (1003)
        want = round((1003 / 1002 - 1) * 100, 2)
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
    # 無い disclosure_no と指定なしは最新の実績、date はその日の開示
    for params, want in (
        ({"disclosure_no": "missing"}, "cfy"),
        ({}, "cfy"),
        ({"date": "2025-11-01"}, "c2q"),
    ):
        r = c.get(f"/api/earnings/detail/{CODE}", params=params)
        if r.status_code != 200 or r.json()["disclosure_no"] != want:
            _fail(f"detail {params}: {r.status_code} {r.text[:200]}")
    # 決算速報の検索: ひらがな・全角コードでも引ける
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
    want = round((1003 / 1002 - 1) * 100, 2)
    # TOPIX は横ばいなので alpha はリターンと同じ
    if hit is None or hit["return_1d"] != want or hit["alpha_1d"] != want:
        _fail(f"perf 2025-11-01: {hit}")
    if [e["disclosed_date"] for e in perf] != sorted((e["disclosed_date"] for e in perf), reverse=True):
        _fail("performance must be newest first")


def test_upsert_mixed_columns_and_freshness() -> None:
    init_db()
    db = SessionLocal()
    try:
        d1, d2 = date(2030, 1, 6), date(2030, 1, 7)
        upsert(db, Topix, [
            {"date": d1, "open": 1, "close": 10},
            {"date": d2, "close": 20},
            {"close": 11, "date": d1},  # 列の組が違う区間をまたいでも後勝ち
            {"date": d2},                # 主キーだけは既存行を変えない
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
    test_earnings_api()
    test_upsert_mixed_columns_and_freshness()
    print("ok: analytics")


if __name__ == "__main__":
    main()

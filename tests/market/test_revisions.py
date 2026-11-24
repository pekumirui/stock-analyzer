"""修正履歴 (期初予想 → 修正 → 実績)。J-Quants (statements) と XBRL (xbrl_financials) の両データ源。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='stock-analyzer-rev-')}/stock.db")

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db
from app.main import app
from app.market.revisions import statement_points
from app.models import Statement, Stock, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.revisions import revision_cell, revision_rows
from app.xbrl.revisions import xbrl_points

CODE = "96970"
FY25, FY26, FY27 = date(2025, 3, 31), date(2026, 3, 31), date(2027, 3, 31)
M = 1_000_000


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _pl(sales, op, odp, np_, eps) -> dict:
    return {"net_sales": sales * M, "operating_profit": op * M, "ordinary_profit": odp * M,
            "profit": np_ * M, "eps": eps}


def _stmt(no: str, period: str, fy_end: date, disclosed: date, *, actual: dict | None = None,
          fy: dict | None = None, nx: dict | None = None,
          doc: str = "FinancialStatements_Consolidated_JP") -> Statement:
    s = Statement(disclosure_no=no, code=CODE, disclosed_date=disclosed, disclosed_time="15:00",
                  type_of_period=period, type_of_document=doc, fiscal_year_end=fy_end)
    for k, v in (actual or {}).items():
        setattr(s, k, v)
    for k, v in (fy or {}).items():
        setattr(s, "fy_" + k, v)
    raw = {}
    if nx:
        raw = {"NxtFYEn": nx.pop("fy_end").isoformat(), "NxFSales": nx["net_sales"],
               "NxFOP": nx["operating_profit"], "NxFOdP": nx["ordinary_profit"],
               "NxFNp": nx["profit"], "NxFEPS": nx["eps"]}
    s.raw_json = json.dumps(raw)
    return s


INIT26 = _pl(100_000, 42_000, 42_000, 30_000, 140.0)
REV26 = _pl(110_000, 42_800, 44_300, 32_000, 150.0)
ACT26 = _pl(110_054, 42_909, 44_330, 32_553, 152.5)
INIT27 = _pl(120_000, 50_000, 50_000, 35_000, 160.0)


def _statements() -> list[Statement]:
    return [
        _stmt("pfy", "FY", FY25, date(2025, 5, 10), actual=_pl(95_000, 40_000, 41_000, 29_000, 135.0),
              nx={"fy_end": FY26, **INIT26}),
        _stmt("c1q", "1Q", FY26, date(2025, 7, 30), actual=_pl(25_000, 9_000, 9_100, 6_000, 28.0),
              fy=INIT26),
        _stmt("rev", "FY", FY26, date(2026, 4, 21), fy=REV26, doc="EarnForecastRevision"),
        _stmt("cfy", "FY", FY26, date(2026, 5, 11), actual=ACT26, nx={"fy_end": FY27, **INIT27}),
    ]


def test_revision_cell() -> None:
    if revision_cell(10, 10) != {"pct": 0.0, "state": "flat"}:
        _fail("same value must be flat")
    if revision_cell(110, 100)["state"] != "up" or revision_cell(None, 1)["state"] != "na":
        _fail("revision_cell falls back to growth_cell")


def test_rows_from_statements() -> None:
    rows = revision_rows(statement_points(_statements()), 4)
    got = [(r["range"], r["kind"], r["disclosed_date"]) for r in rows]
    want = [("25.3", "actual_fy", "2025-05-10"),
            ("26.3", "forecast_initial", "2025-05-10"),
            ("26.3", "forecast_revised", "2026-04-21"),
            ("26.3", "actual_fy", "2026-05-11"),
            ("27.3", "forecast_initial", "2026-05-11")]
    if got != want:
        _fail(f"rows {got}")
    if rows[1]["growth"] is not None:
        _fail("initial forecast has no growth")
    rev = rows[2]
    if rev["values"]["net_sales"] != 110_000 * M or rev["growth"]["net_sales"] != {"pct": 10.0, "state": "up"}:
        _fail(f"revised {rev}")
    if rows[3]["growth"]["operating_profit"]["pct"] != 0.3:
        _fail(f"actual vs last forecast {rows[3]['growth']}")
    if [r["range"] for r in revision_rows(statement_points(_statements()), 2)] != ["26.3"] * 3 + ["27.3"]:
        _fail("years keeps the latest fiscal years")


def test_unchanged_forecast_is_not_a_revision() -> None:
    hist = _statements()[:2]
    kinds = [r["kind"] for r in revision_rows(statement_points(hist), 4)]
    if kinds != ["actual_fy", "forecast_initial"]:
        _fail(kinds)


def test_first_forecast_after_start_is_not_initial() -> None:
    hist = _statements()[1:3]
    kinds = [r["kind"] for r in revision_rows(statement_points(hist), 4)]
    if kinds != ["forecast", "forecast_revised"]:
        _fail(kinds)


def test_quarter_flash_revision_is_not_a_forecast_point() -> None:
    fy27 = date(2027, 3, 31)
    init27 = _pl(23_000, 1_800, 1_800, 1_100, 46.85)
    hist = [
        _stmt("pfy", "FY", FY26, date(2026, 5, 12), actual=ACT26, nx={"fy_end": fy27, **init27}),
        _stmt("flash", "1Q", fy27, date(2026, 7, 30), fy=_pl(5_949, 650, 660, 439, 9.88),
              doc="EarnForecastRevision"),
        _stmt("c1q", "1Q", fy27, date(2026, 8, 10), actual=_pl(5_949, 650, 660, 439, 9.88), fy=init27),
    ]
    rows = revision_rows(statement_points(hist), 4)
    got = [(r["range"], r["kind"], r["values"]["net_sales"]) for r in rows]
    if got != [("26.3", "actual_fy", ACT26["net_sales"]), ("27.3", "forecast_initial", 23_000 * M)]:
        _fail(f"quarter flash rows {got}")


def _seed_xbrl(db) -> None:
    items = {}
    for code in ("net_sales", "operating_income", "ordinary_income", "net_income", "eps_basic"):
        items[code] = XbrlStdItem(code=code, statement="PL", label_ja=code)
        db.add(items[code])
    db.flush()

    def filing(doc_id, doc_type, period, at):
        f = XbrlFiling(code=CODE, source="tdnet", doc_id=doc_id, doc_type=doc_type, period_type=period,
                       submitted_at=at, raw_path=f"{doc_id}.zip", parse_status="parsed", created_at=at)
        db.add(f)
        db.flush()
        return f

    def put(f, values, fy_end, kind, *, side="point", consolidated=True):
        std = {"net_sales": "net_sales", "operating_profit": "operating_income",
               "ordinary_profit": "ordinary_income", "profit": "net_income", "eps": "eps_basic"}
        for k, v in values.items():
            db.add(XbrlFinancial(code=CODE, filing_id=f.id, std_item_id=items[std[k]].id,
                                 is_consolidated=consolidated, period_type=f.period_type, period_end=fy_end,
                                 value=v, value_kind=kind, range_side=side, target_period="FY",
                                 generated_at=f.submitted_at))

    a25 = filing("a25", "annual", "FY", datetime.combine(date(2025, 5, 10), time(15)))
    put(a25, INIT26, FY26, "forecast")
    put(a25, {k: v * 2 for k, v in INIT26.items()}, FY26, "forecast", consolidated=False)
    r26 = filing("r26", "forecast_revision", "FY", datetime.combine(date(2026, 4, 21), time(15)))
    put(r26, {k: v - 1000 for k, v in REV26.items()}, FY26, "forecast", side="lower")
    put(r26, {k: v + 1000 for k, v in REV26.items()}, FY26, "forecast", side="upper")
    put(r26, INIT26, FY26, "forecast_previous")
    put(r26, _pl(1, 1, 1, 1, 1.0), FY25, "actual")
    a26 = filing("a26", "annual", "FY", datetime.combine(date(2026, 5, 11), time(15)))
    put(a26, ACT26, FY26, "actual")
    put(a26, INIT27, FY27, "forecast")
    db.commit()


def test_rows_from_xbrl() -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed_xbrl(db)
        rows = revision_rows(xbrl_points(db, CODE, date(2026, 5, 11)), 4)
        got = [(r["range"], r["kind"], r["disclosed_date"]) for r in rows]
        want = [("26.3", "forecast_initial", "2025-05-10"),
                ("26.3", "forecast_revised", "2026-04-21"),
                ("26.3", "actual_fy", "2026-05-11"),
                ("27.3", "forecast_initial", "2026-05-11")]
        if got != want:
            _fail(f"xbrl rows {got}")
        if rows[0]["values"]["net_sales"] != INIT26["net_sales"]:
            _fail(f"consolidated wins {rows[0]['values']}")
        if rows[1]["values"] != REV26:
            _fail(f"range midpoint {rows[1]['values']}")
        early = revision_rows(xbrl_points(db, CODE, date(2026, 4, 30)), 4)
        if [r["kind"] for r in early] != ["forecast_initial", "forecast_revised"]:
            _fail(f"upto cuts later filings {early}")
    finally:
        db.close()


def test_detail_api_sources() -> None:
    init_db()
    db = SessionLocal()
    try:
        db.merge(Stock(code=CODE, name="カプコン"))
        for s in _statements():
            db.merge(s)
        if db.query(XbrlFiling).count() == 0:
            _seed_xbrl(db)
        db.commit()
    finally:
        db.close()
    c = TestClient(app)
    body = c.get(f"/api/earnings/detail/{CODE}", params={"disclosure_no": "cfy"}).json()
    if body["revision_source"] != "jquants" or len(body["revisions"]) != 5:
        _fail(f"jquants {body.get('revision_source')} {body.get('revisions')}")
    body = c.get(f"/api/earnings/detail/{CODE}", params={"disclosure_no": "c1q"}).json()
    if [r["kind"] for r in body["revisions"]] != ["actual_fy", "forecast_initial"]:
        _fail(f"upto selected disclosure {body['revisions']}")
    body = c.get(f"/api/earnings/detail/{CODE}",
                 params={"disclosure_no": "cfy", "rev_source": "xbrl"}).json()
    if body["revision_source"] != "xbrl" or len(body["revisions"]) != 4:
        _fail(f"xbrl {body.get('revisions')}")
    r = c.get(f"/api/earnings/detail/{CODE}", params={"rev_source": "edinet"})
    if r.status_code != 422:
        _fail(f"unknown source {r.status_code}")


def main() -> None:
    init_db()
    test_revision_cell()
    test_rows_from_statements()
    test_unchanged_forecast_is_not_a_revision()
    test_first_forecast_after_start_is_not_initial()
    test_quarter_flash_revision_is_not_a_forecast_point()
    test_rows_from_xbrl()
    test_detail_api_sources()
    print("ok: revisions")


if __name__ == "__main__":
    main()

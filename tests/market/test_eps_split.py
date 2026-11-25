"""株式分割をまたぐ EPS の増減率と修正率（KAB-407）。比べる前に選んだ開示の日の株数にそろえる。"""
from __future__ import annotations

import json
from datetime import date

from app.db import SessionLocal
from app.models import DailyPrice, Statement, Stock

FY26, FY27 = date(2026, 3, 31), date(2027, 3, 31)


def _st(no: str, day: str, period: str, fy_end: date, profit: float, eps: float,
        fy_profit: float | None = None, fy_eps: float | None = None, **raw) -> Statement:
    doc = ("FY" if period == "FY" else period) + "FinancialStatements_Consolidated_JP"
    return Statement(disclosure_no=no, code="99960", disclosed_date=date.fromisoformat(day),
                     disclosed_time="15:30:00", type_of_period=period, type_of_document=doc,
                     fiscal_year_end=fy_end, net_sales=profit * 10, operating_profit=profit * 2,
                     profit=profit, eps=eps, fy_net_sales=None if fy_profit is None else fy_profit * 10,
                     fy_operating_profit=None if fy_profit is None else fy_profit * 2,
                     fy_profit=fy_profit, fy_eps=fy_eps, raw_json=json.dumps({**_forecast_raw(fy_profit, fy_eps), **raw}))


def _forecast_raw(fy_profit: float | None, fy_eps: float | None) -> dict:
    if fy_profit is None:
        return {}
    return {"FSales": str(fy_profit * 10), "FOP": str(fy_profit * 2), "FNP": str(fy_profit), "FEPS": str(fy_eps)}


def _seed() -> None:
    db = SessionLocal()
    try:
        if db.get(Stock, "99960") is not None:
            return
        db.add(Stock(code="99960", name="分割電工"))
        db.add_all([
            DailyPrice(code="99960", date=date(2026, 5, 12), close=4000.0, adj_factor=1.0),
            DailyPrice(code="99960", date=date(2026, 6, 29), close=400.0, adj_factor=0.1),
            DailyPrice(code="99960", date=date(2026, 11, 9), close=420.0, adj_factor=1.0),
        ])
        db.add_all([
            _st("a1", "2025-08-07", "1Q", FY26, 500, 50.0, 2000, 200.0),
            _st("a2", "2025-11-10", "2Q", FY26, 1000, 100.0, 2000, 200.0),
            # 分割を発表した本決算。来期予想は分割後の株数（100 株）で出している
            _st("a4", "2026-05-12", "FY", FY26, 2200, 220.0, NxFEPS="25.0", NxFNp="2500"),
            _st("b1", "2026-08-06", "1Q", FY27, 600, 6.0, 2500, 25.0),
            _st("b2", "2026-11-09", "2Q", FY27, 1200, 12.0, 2500, 25.0),
        ])
        db.commit()
    finally:
        db.close()


def _rows(d: dict, section: str, kind: str) -> list[dict]:
    return [r for r in d[section] if r["kind"] == kind]


def test_detail_compares_eps_on_current_shares(c) -> None:
    _seed()
    d = c.get("/api/earnings/detail/99960").json()
    assert d["disclosure_no"] == "b2"

    cur = _rows(d, "cumulative", "actual_current")[0]
    assert cur["values"]["eps"] == 12.0
    assert cur["growth"]["eps"] == {"pct": 20.0, "state": "up"}
    assert cur["growth"]["profit"]["pct"] == 20.0
    last_year = _rows(d, "cumulative", "actual")[-1]
    assert abs(last_year["values"]["eps"] - 10.0) < 1e-9

    q = {r["range"]: r for r in d["quarterly"]}
    assert abs(q["25.09"]["values"]["eps"] - 5.0) < 1e-9
    assert q["26.09"]["growth"]["eps"]["pct"] == 20.0

    prog = _rows(d, "progress", "actual_current")[0]
    assert prog["progress"]["eps"] == 48.0

    revs = d["revisions"]
    fy26 = [r for r in revs if r["fiscal_year_end"] == "2026-03-31"]
    assert abs(fy26[0]["values"]["eps"] - 20.0) < 1e-9
    assert abs(fy26[-1]["values"]["eps"] - 22.0) < 1e-9
    assert fy26[-1]["growth"]["eps"]["pct"] == 10.0
    fy27 = [r for r in revs if r["fiscal_year_end"] == "2027-03-31"]
    assert fy27[0]["values"]["eps"] == 25.0


def test_detail_before_split_keeps_raw_eps(c) -> None:
    _seed()
    d = c.get("/api/earnings/detail/99960?disclosure_no=a4").json()
    cur = _rows(d, "cumulative", "actual_current")[0]
    assert cur["values"]["eps"] == 220.0
    assert _rows(d, "cumulative", "forecast_fy")[0]["values"]["eps"] == 25.0


def test_list_yoy_eps(c) -> None:
    _seed()
    rows = c.get("/api/earnings?date=2026-11-09&today=2026-11-10").json()["actuals"]
    r = next(x for x in rows if x["code"] == "99960")
    assert r["yoy"]["eps"] == {"pct": 20.0, "state": "up"}
    assert r["qoq"]["eps"]["pct"] == 0.0
    assert r["progress"]["cur"]["eps"] == 48.0

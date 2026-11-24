"""株価チャートの業績の階段（KAB-301。app/market/chart_fund.py、docs/screen/stock.md「業績の数字の作り方」）。"""
from __future__ import annotations

import json
from datetime import date, timedelta

from app.market.chart_fund import fund_steps
from app.models import Statement

FY25, FY26 = date(2025, 3, 31), date(2026, 3, 31)


def _st(no: str, day: str, time: str, period: str, fy_end: date, op: float | None = None,
        doc: str | None = None, **raw) -> Statement:
    kind = "FY" if period == "FY" else period
    return Statement(disclosure_no=no, code="99990", disclosed_date=date.fromisoformat(day), disclosed_time=time,
                     type_of_period=period, fiscal_year_end=fy_end,
                     type_of_document=doc or f"{kind}FinancialStatements_Consolidated_JP",
                     operating_profit=op, is_revision=doc is not None and "Revision" in doc,
                     raw_json=json.dumps({k: str(v) for k, v in raw.items()}))


def _days(start: str, end: str) -> list[date]:
    d, e, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= e:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _rows(f: dict) -> list[tuple]:
    return list(zip(f["t"], f["eps"], f["op_q"], f["op_ttm"]))


def test_steps_are_point_in_time() -> None:
    days = _days("2024-07-01", "2025-12-31")
    stmts = [
        # 前期: 1Q〜FY。FY は来期予想 NxFEPS を使う
        _st("1", "2024-07-31", "14:00:00", "1Q", FY25, 100, FEPS=50),
        _st("2", "2024-10-31", "14:00:00", "2Q", FY25, 220, FEPS=50),
        _st("3", "2025-01-31", "15:00:00", "3Q", FY25, 330, FEPS=52),
        _st("4", "2025-05-09", "15:00:00", "FY", FY25, 450, NxFEPS=60),
        # 引け後の発表は翌営業日（金曜の引け後 → 月曜）から
        _st("5", "2025-08-01", "16:00:00", "1Q", FY26, 130, FEPS=60),
        # 予想修正だけの開示は値があるときだけ
        _st("6", "2025-09-01", "15:00:00", "FY", FY26, doc="EarnForecastRevision", FEPS=66),
        _st("7", "2025-09-02", "15:00:00", "FY", FY26, doc="DividendForecastRevision", FEPS=""),
        # 前の期の出し直しは使わない
        _st("8", "2025-09-10", "15:00:00", "2Q", FY25, 999, FEPS=1),
    ]
    rows = _rows(fund_steps(stmts, days, [1.0] * len(days)))
    assert rows == [
        ("2024-07-31", 50, 100, None),
        ("2024-10-31", 50, 120, None),
        ("2025-01-31", 52, 110, None),
        ("2025-05-09", 60, 120, 450),
        # 12 か月 = 当期 1Q 累計 + 前期通期 − 前期 1Q 累計
        ("2025-08-04", 60, 130, 480),
        ("2025-09-01", 66, 130, 480),
    ]


def test_eps_follows_splits_after_the_disclosure() -> None:
    days = _days("2025-04-01", "2026-03-31")
    split1 = days.index(date(2025, 12, 29))
    factors = [1.0] * len(days)
    factors[split1] = 0.2
    stmts = [
        _st("1", "2025-05-02", "15:00:00", "FY", FY25, 450, NxFEPS=600),
        # 分割の発表後、分割後の株数で出し直した予想（600 / 5 = 120 付近）
        _st("2", "2025-11-05", "15:00:00", "2Q", FY26, 300, FEPS=125),
        _st("3", "2026-02-06", "15:00:00", "3Q", FY26, 450, FEPS=126),
    ]
    eps = fund_steps(stmts, days, factors)["eps"]
    assert eps == [120, 125, 126]

    late = [_st("1", "2025-05-02", "15:00:00", "FY", FY25, 450, NxFEPS=600),
            _st("2", "2025-06-02", "15:00:00", "FY", FY26, doc="EarnForecastRevision", FEPS=130)]
    assert fund_steps(late, days, factors)["eps"] == [120, 26]


def test_eps_from_forecast_profit_when_eps_is_blank() -> None:
    days = _days("2024-04-01", "2025-03-31")
    stmts = [
        _st("1", "2024-04-26", "15:00:00", "FY", date(2024, 3, 31), 450, NP=1000, EPS=10, NxFNp=1500, NxFEPS=""),
        _st("2", "2024-07-31", "15:00:00", "1Q", FY25, 100, AvgSh=120, NP=300, EPS=2, FNP=1800, FEPS=""),
        _st("3", "2024-09-02", "15:00:00", "FY", FY25, doc="EarnForecastRevision", FNP=2400, FEPS=""),
        _st("4", "2024-10-31", "15:00:00", "2Q", FY25, 220, ShOutFY=130, TrShFY=10, FNP=2400, FEPS=""),
        _st("5", "2025-01-31", "15:00:00", "3Q", FY25, 330, NP=900, EPS=7.5, FNP="", FEPS=""),
    ]
    assert fund_steps(stmts, days, [1.0] * len(days))["eps"] == [15, 15, None, 20, None]

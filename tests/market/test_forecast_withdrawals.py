from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.api.stock import _dividend_forecast
from app.market import withdrawals
from app.market.chart_fund import fund_steps
from app.market.withdrawals import load_withdrawals, withdrawn_items
from app.models import Statement

FY20, FY21 = date(2020, 3, 31), date(2021, 3, 31)


def _write(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "forecast_withdrawals.json"
    p.write_text(json.dumps({"_meta": {}, "withdrawals": entries}, ensure_ascii=False), encoding="utf-8")
    return p


def test_shipped_list_is_valid() -> None:
    table = load_withdrawals()
    assert all(isinstance(v, frozenset) and v for v in table.values())


def test_list_rejects_unknown_items_duplicates_and_empty(tmp_path: Path) -> None:
    ok = _write(tmp_path, [{"disclosure_no": "20200814000001", "code": "70610", "items": ["eps", "div_annual"],
                            "evidence": "取り下げのお知らせ"}])
    assert load_withdrawals(ok) == {"20200814000001": frozenset({"eps", "div_annual"})}
    with pytest.raises(ValueError, match="unknown items"):
        load_withdrawals(_write(tmp_path, [{"disclosure_no": "1", "items": ["eps", "dividend"]}]))
    with pytest.raises(ValueError, match="duplicate"):
        load_withdrawals(_write(tmp_path, [{"disclosure_no": "1", "items": ["eps"]},
                                           {"disclosure_no": "1", "items": ["profit"]}]))
    with pytest.raises(ValueError, match="no items"):
        load_withdrawals(_write(tmp_path, [{"disclosure_no": "1", "items": []}]))


def _st(no: str, day: str, period: str, fy_end: date, doc: str, **raw) -> Statement:
    return Statement(disclosure_no=no, code="99990", disclosed_date=date.fromisoformat(day), disclosed_time="15:30:00",
                     type_of_period=period, fiscal_year_end=fy_end, type_of_document=doc,
                     raw_json=json.dumps({k: str(v) for k, v in raw.items()}))


def _days() -> list[date]:
    d, out = date(2020, 5, 1), []
    while d <= date(2020, 12, 31):
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_listed_disclosure_behaves_like_the_column(tmp_path: Path, monkeypatch) -> None:
    stmts = [
        _st("20200512000001", "2020-05-12", "FY", FY20, "FYFinancialStatements_Consolidated_JP",
            NxFEPS=71.2, NxFDivAnn=25),
        _st("20200814000001", "2020-08-14", "FY", FY21, "EarnForecastRevision"),
    ]
    days = _days()
    factors = [1.0] * len(days)
    before = fund_steps(stmts, days, factors)
    assert before["eps"] == [71.2] and _dividend_forecast(stmts, days, factors) == 25

    table = load_withdrawals(_write(tmp_path, [{"disclosure_no": "20200814000001", "code": "99990",
                                                "items": ["net_sales", "operating_profit", "ordinary_profit",
                                                          "profit", "eps", "div_annual"]}]))
    monkeypatch.setattr(withdrawals, "default_withdrawals", lambda: table)
    listed = fund_steps(stmts, days, factors)
    assert list(zip(listed["t"], listed["eps"])) == [("2020-05-13", 71.2), ("2020-08-17", None)]
    assert _dividend_forecast(stmts, days, factors) is None

    monkeypatch.setattr(withdrawals, "default_withdrawals", dict)
    stmts[1].withdrawn_forecasts = tuple(table["20200814000001"])
    assert fund_steps(stmts, days, factors) == listed
    assert withdrawn_items(stmts[1], {}) == table["20200814000001"]

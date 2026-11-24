from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from tests.market.jquants_columns import fy_columns
from app.market.forecasts import IGNORED, REPORT, REVISION, forecast_points, latest_forecast
from app.market.withdrawals import no_withdrawals
from app.models import Statement

FY26, FY27 = date(2026, 3, 31), date(2027, 3, 31)
ALL = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")


def _st(no: str, day: str, period: str, fy_end: date, doc: str | None = None, withdrawn=None, **raw):
    kind = "FY" if period == "FY" else period
    return SimpleNamespace(disclosure_no=no, code="99990", disclosed_date=date.fromisoformat(day),
                           disclosed_time="15:30:00", type_of_period=period, fiscal_year_end=fy_end,
                           type_of_document=doc or f"{kind}FinancialStatements_Consolidated_JP",
                           withdrawn_forecasts=withdrawn,
                           raw_json=json.dumps({k: str(v) for k, v in raw.items()}), **fy_columns(raw))


def _rev(no: str, day: str, period: str = "FY", fy_end: date = FY27, withdrawn=None, **raw):
    return _st(no, day, period, fy_end, doc="EarnForecastRevision", withdrawn=withdrawn, **raw)


def _values(p) -> dict:
    return {k: v.value for k, v in p.values.items()}


def _last(stmts, **kw) -> dict:
    return _values(forecast_points(stmts, **kw)[-1])


def test_report_replaces_every_item_and_blank_is_undecided() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFOP=100, NxFOdP=110, NxFNp=70, NxFEPS=7,
            NxFDivAnn=20),
        _st("2", "2026-08-05", "1Q", FY27, FSales=1100, FOP="", FOdP=120, FNP=80, FEPS=8, FDivAnn=""),
    ]
    first, second = forecast_points(stmts, withdrawn=no_withdrawals)
    assert first.role == REPORT and _values(first) == {
        "net_sales": 1000, "operating_profit": 100, "ordinary_profit": 110, "profit": 70, "eps": 7,
        "div_annual": 20}
    assert second.role == REPORT and _values(second) == {
        "net_sales": 1100, "operating_profit": None, "ordinary_profit": 120, "profit": 80, "eps": 8,
        "div_annual": None}
    assert second.values["eps"].disclosure_no == "2" and second.values["eps"].disclosed_date == date(2026, 8, 5)


def test_older_period_report_is_ignored() -> None:
    stmts = [
        _st("1", "2026-08-05", "1Q", FY27, FSales=1100, FEPS=8),
        _st("2", "2026-09-10", "3Q", FY26, FSales=1, FEPS=1),
    ]
    late = forecast_points(stmts)[-1]
    assert late.role == IGNORED and late.values["eps"].value == 8 and late.values["net_sales"].disclosure_no == "1"


def test_half_year_only_revision_keeps_full_year() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFOP=100, NxFNp=70, NxFEPS=7),
        _rev("2", "2026-07-01", "2Q", FSales2Q=520, FOP2Q=60),
    ]
    p = forecast_points(stmts)[-1]
    assert p.role == REVISION and p.touched == frozenset()
    assert _values(p)["net_sales"] == 1000 and p.values["net_sales"].disclosure_no == "1"


def test_revision_overwrites_only_given_items() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFOP=100, NxFOdP=110, NxFNp=70, NxFEPS=7),
        _rev("2", "2026-07-01", FOP=130, FOdP=140),
    ]
    p = forecast_points(stmts)[-1]
    assert p.touched == {"operating_profit", "ordinary_profit"}
    assert _values(p) == {"net_sales": 1000, "operating_profit": 130, "ordinary_profit": 140, "profit": 70,
                          "eps": 7, "div_annual": None}
    assert p.values["operating_profit"].disclosure_no == "2" and p.values["profit"].disclosure_no == "1"


def test_profit_only_revision_recomputes_eps_from_report_shares() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NP=600, EPS=6, NxFNp=700, NxFEPS=7),
        _rev("2", "2026-07-01", FNP=900, FEPS=""),
    ]
    p = forecast_points(stmts)[-1]
    assert _values(p)["eps"] == 9 and p.values["eps"].disclosure_no == "2"
    no_shares = [_st("1", "2026-05-12", "FY", FY26, NxFNp=700, NxFEPS=7), _rev("2", "2026-07-01", FNP=900)]
    assert _last(no_shares)["eps"] is None


def test_withdrawal_of_every_item_and_of_some_items() -> None:
    report = _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFOP=100, NxFOdP=110, NxFNp=70, NxFEPS=7,
                 NxFDivAnn=20)
    every = _last([report, _rev("2", "2026-08-14", withdrawn=ALL)])
    assert every == {"net_sales": None, "operating_profit": None, "ordinary_profit": None, "profit": None,
                     "eps": None, "div_annual": 20}
    some = forecast_points([report, _rev("2", "2026-08-14", withdrawn=("ordinary_profit", "profit", "eps"),
                                         FSales=900, FOP=80)])[-1]
    assert _values(some) == {"net_sales": 900, "operating_profit": 80, "ordinary_profit": None, "profit": None,
                             "eps": None, "div_annual": 20}
    assert some.touched == {"net_sales", "operating_profit", "ordinary_profit", "profit", "eps"}
    assert some.values["eps"].disclosure_no == "2"
    assert _last([report, _rev("2", "2026-08-14", withdrawn=ALL)], withdrawn=no_withdrawals)["eps"] == 7


def test_next_report_brings_forecast_back_after_withdrawal() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFEPS=7),
        _rev("2", "2026-08-14", withdrawn=ALL),
        _rev("3", "2026-09-01", FSales2Q=500),
        _st("4", "2026-11-10", "2Q", FY27, FSales=950, FEPS=6),
    ]
    points = forecast_points(stmts)
    assert [p.values["eps"].value for p in points] == [7, None, None, 6]
    assert [p.values["net_sales"].value for p in points] == [1000, None, None, 950]


def test_quarter_flash_revision_is_ignored_even_with_marks() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFEPS=7),
        _rev("2", "2026-07-30", "1Q", FSales=230, FEPS=1.5, withdrawn=("eps",)),
    ]
    p = forecast_points(stmts)[-1]
    assert p.role == IGNORED and p.touched == frozenset()
    assert _values(p)["eps"] == 7 and _values(p)["net_sales"] == 1000


def test_individual_forecasts_are_not_mixed() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFEPS=7),
        _rev("2", "2026-07-01", FNCSales=300, FNCEPS=3),
    ]
    assert _last(stmts)["net_sales"] == 1000 and _last(stmts)["eps"] == 7


def test_dividend_rules_and_withdrawal() -> None:
    stmts = [
        _st("1", "2026-05-12", "FY", FY26, NxFDivAnn=20),
        _st("2", "2026-08-05", "1Q", FY27, FDivAnn=20),
        _st("3", "2026-09-01", "FY", FY27, doc="DividendForecastRevision", FDivAnn=25),
        _rev("4", "2026-09-15", FDivAnn=""),
    ]
    points = forecast_points(stmts)
    assert [p.values["div_annual"].value for p in points] == [20, 20, 25, 25]
    assert latest_forecast(points, "div_annual").disclosure_no == "3"
    gone = [*stmts, _st("5", "2026-10-01", "FY", FY27, doc="DividendForecastRevision", withdrawn=("div_annual",))]
    assert latest_forecast(forecast_points(gone), "div_annual").value is None
    back = [*gone, _st("6", "2026-11-10", "2Q", FY27, FDivAnn=30)]
    assert latest_forecast(forecast_points(back), "div_annual").value == 30
    assert latest_forecast([], "div_annual").value is None


def test_orm_rows_and_column_marks() -> None:
    s = Statement(disclosure_no="9", code="99990", disclosed_date=date(2026, 8, 14), disclosed_time=None,
                  type_of_period="FY", type_of_document="EarnForecastRevision", fiscal_year_end=FY27,
                  raw_json=None, withdrawn_forecasts=("eps",))
    report = Statement(disclosure_no="1", code="99990", disclosed_date=date(2026, 5, 12), disclosed_time="15:30:00",
                       type_of_period="FY", type_of_document="FYFinancialStatements_Consolidated_JP",
                       fiscal_year_end=FY26, raw_json=json.dumps({"NxFEPS": "7"}))
    assert [p.values["eps"].value for p in forecast_points([s, report])] == [7, None]


def test_current_forecast_is_read_from_the_columns_when_raw_json_was_replaced() -> None:
    report = _st("1", "2026-05-12", "FY", FY26, NxFSales=1000, NxFOP=100, NxFOdP=110, NxFNp=70, NxFEPS=7,
                 AvgSh=10)
    xbrl_row = _rev("2", "2026-10-16", FSales="", FOP="", FNP="")
    xbrl_row.fy_net_sales, xbrl_row.fy_operating_profit, xbrl_row.fy_profit = 900.0, -15.0, -50.0
    assert _last([report, xbrl_row], withdrawn=no_withdrawals) == {
        "net_sales": 900, "operating_profit": -15, "ordinary_profit": 110, "profit": -50, "eps": -5.0,
        "div_annual": None}

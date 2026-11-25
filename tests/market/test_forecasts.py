from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from tests.market.jquants_columns import fy_columns
from app.market.forecasts import (CORRECTION, IGNORED, REPORT, REVISION, STALE, Sourced, forecast_points,
                                  latest_forecast)
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


def _from(start: str, s):
    s.fiscal_year_start = date.fromisoformat(start)
    return s


def test_shortened_fiscal_year_report_is_used() -> None:
    feb26, feb27 = date(2026, 2, 28), date(2027, 2, 28)
    stmts = [
        _from("2025-04-01", _st("1", "2026-02-16", "3Q", FY26, FSales=900, FEPS=30)),
        _from("2025-04-01", _st("2", "2026-04-17", "FY", feb26, NxtFYEn=feb27.isoformat(), NxFOP=48_000_000,
                                NxFEPS=40)),
        _from("2026-03-01", _st("3", "2026-07-15", "1Q", feb27, FOP=49_000_000, FEPS=41)),
    ]
    points = forecast_points(stmts)
    assert [p.role for p in points] == [REPORT, REPORT, REPORT]
    assert _values(points[1])["operating_profit"] == 48_000_000 and _values(points[1])["eps"] == 40


def test_quarters_after_the_year_end_moved_earlier_are_used() -> None:
    nov22, aug22 = date(2022, 11, 30), date(2022, 8, 31)
    stmts = [
        _from("2021-12-01", _st("1", "2022-04-14", "1Q", nov22, FEPS=10)),
        _from("2021-12-01", _st("2", "2022-07-15", "2Q", aug22, FEPS=12)),
        _from("2021-12-01", _st("3", "2022-10-14", "FY", aug22, NxFEPS=15)),
    ]
    points = forecast_points(stmts)
    assert [p.role for p in points] == [REPORT, REPORT, REPORT] and _values(points[-1])["eps"] == 15


def test_old_year_report_filed_again_or_late_is_ignored() -> None:
    again = [
        _from("2024-07-01", _st("1", "2025-02-12", "2Q", date(2025, 6, 30), FEPS=20)),
        _from("2023-07-01", _st("2", "2025-02-14", "FY", date(2024, 6, 30), NxFEPS=1)),
    ]
    assert [p.role for p in forecast_points(again)] == [REPORT, IGNORED]
    late = [
        _from("2021-04-01", _st("1", "2021-10-27", "1Q", date(2022, 3, 31), FEPS=20)),
        _from("2020-04-01", _st("2", "2021-10-27", "FY", date(2021, 3, 31), NxFEPS=1)),
    ]
    late[1].disclosure_no = "3"
    assert [p.role for p in forecast_points(late)] == [REPORT, IGNORED]
    assert _values(forecast_points(late)[-1])["eps"] == 20


def test_report_after_a_correction_with_a_wrong_year_start_is_used() -> None:
    mar24 = date(2024, 3, 31)
    stmts = [
        _from("2023-04-01", _st("1", "2024-02-14", "3Q", mar24, FEPS=250)),
        _from("2023-10-02", _st("2", "2024-02-22", "3Q", mar24, FEPS=250)),
        _from("2023-04-01", _st("3", "2024-05-15", "FY", mar24, NxFEPS=284.31)),
        _from("2024-04-01", _st("4", "2024-08-09", "1Q", date(2025, 3, 31), FEPS=290)),
    ]
    points = forecast_points(stmts)
    assert [p.role for p in points] == [REPORT, CORRECTION, REPORT, REPORT]
    assert _values(points[2])["eps"] == 284.31


def test_report_with_a_wrong_year_start_after_a_fiscal_year_change_is_used() -> None:
    aug25 = date(2025, 8, 31)
    stmts = [
        _from("2025-04-01", _st("1", "2025-08-08", "1Q", aug25, FEPS=10)),
        _from("2024-09-01", _st("2", "2025-10-14", "FY", aug25, NxFEPS=20)),
        _from("2025-04-01", _st("3", "2025-12-01", "FY", aug25, NxFEPS=21)),
    ]
    points = forecast_points(stmts)
    assert [p.role for p in points] == [REPORT, REPORT, CORRECTION]
    assert points[1].values["eps"].disclosure_no == "2" and _values(points[1])["eps"] == 20
    assert points[2].touched == {"eps"} and _values(points[2])["eps"] == 21


def test_correction_of_an_earlier_quarter_of_the_same_year_is_ignored() -> None:
    mar27 = date(2027, 3, 31)
    stmts = [
        _from("2026-04-01", _st("1", "2026-08-12", "1Q", mar27, FEPS=10)),
        _from("2026-04-01", _st("2", "2026-11-11", "2Q", mar27, FEPS=12)),
        _from("2026-04-01", _st("3", "2026-11-12", "1Q", mar27, FEPS=1)),
    ]
    assert [p.role for p in forecast_points(stmts)] == [REPORT, REPORT, IGNORED]


def test_without_the_year_start_the_year_end_decides() -> None:
    stmts = [
        _st("1", "2026-02-16", "3Q", FY26, FEPS=30),
        _st("2", "2026-04-17", "FY", date(2026, 2, 28), NxFEPS=40),
    ]
    assert [p.role for p in forecast_points(stmts)] == [REPORT, IGNORED]


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


def test_older_period_report_does_not_touch_the_dividend() -> None:
    newer = [
        _st("1", "2026-05-12", "FY", FY26, NxFDivAnn=38),
        _st("2", "2026-08-05", "1Q", FY27, FDivAnn=38),
    ]
    for late in (_st("3", "2026-10-29", "FY", date(2025, 3, 31), NxFDivAnn=60),
                 _st("3", "2026-10-29", "2Q", FY26, FDivAnn=""),
                 _st("3", "2026-10-29", "3Q", FY26, withdrawn=("div_annual",))):
        p = forecast_points([*newer, late])[-1]
        assert p.role == IGNORED and p.values["div_annual"] == Sourced(38, "2", date(2026, 8, 5))


def test_revision_of_the_finished_year_does_not_touch_the_next_year_forecast() -> None:
    report = _st("1", "2026-05-14", "FY", FY26, NxFSales=1000, NxFEPS=154.64, NxFDivAnn=40)
    stale = _rev("2", "2026-05-14", fy_end=FY26, FSales=900, FEPS=214.75, FDivAnn=50)
    for no in ("0", "2"):
        stale.disclosure_no = no
        points = forecast_points([report, stale])
        assert _values(points[-1]) == {"net_sales": 1000, "operating_profit": None, "ordinary_profit": None,
                                       "profit": None, "eps": 154.64, "div_annual": 40}
    assert points[-1].role == STALE and points[-1].stated["eps"].value == 214.75
    withdrawn = _rev("2", "2026-06-01", fy_end=FY26, withdrawn=ALL)
    assert _last([report, withdrawn])["eps"] == 154.64


def test_revision_of_the_current_year_after_a_quarter_is_used() -> None:
    stmts = [
        _st("1", "2026-08-05", "1Q", FY27, FEPS=8),
        _rev("2", "2026-09-01", fy_end=FY26, FEPS=1),
        _rev("3", "2026-09-02", fy_end=FY27, FEPS=9),
    ]
    assert [p.role for p in forecast_points(stmts)] == [REPORT, STALE, REVISION]
    assert _last(stmts)["eps"] == 9


def test_blank_correction_keeps_the_forecast() -> None:
    stmts = [
        _st("1", "2026-10-30", "2Q", FY27, FSales=5000, FOP=800, FEPS=61.95, FDivAnn=30),
        _st("2", "2026-11-11", "2Q", FY27, Sales=4900),
    ]
    p = forecast_points(stmts)[-1]
    assert p.role == CORRECTION and p.touched == frozenset()
    assert _values(p) == {"net_sales": 5000, "operating_profit": 800, "ordinary_profit": None, "profit": None,
                          "eps": 61.95, "div_annual": 30}
    assert p.values["eps"].disclosure_no == "1"


def test_correction_restating_the_report_keeps_the_revision_between() -> None:
    dec25 = date(2025, 12, 31)
    report = _st("20251112597046", "2025-11-12", "3Q", dec25, FOP=4_729_000_000, FEPS=396.09, FDivAnn=100)
    revision = _rev("20251127510152", "2025-11-27", fy_end=dec25, FOP=5_600_000_000, FEPS=443.49, FDivAnn=120)
    correction = _st("20251127510228", "2025-11-27", "3Q", dec25, FOP=4_729_000_000, FEPS=396.09, FDivAnn=100)
    for order in ([report, revision, correction], [report, correction, revision]):
        p = forecast_points(order)[-1]
        assert _values(p)["eps"] == 443.49 and _values(p)["operating_profit"] == 5_600_000_000
        assert _values(p)["div_annual"] == 120 and p.values["eps"].disclosure_no == "20251127510152"


def test_correction_that_changes_the_forecast_is_used() -> None:
    stmts = [
        _st("1", "2026-08-05", "1Q", FY27, FSales=1000, FOP=100, FEPS=7),
        _rev("2", "2026-09-01", FOP=120),
        _st("3", "2026-09-10", "1Q", FY27, FSales=1000, FOP=100, FEPS=8),
        _st("4", "2026-09-20", "1Q", FY27, FSales=1000, FOP=100, FEPS=8),
    ]
    points = forecast_points(stmts)
    assert points[2].touched == {"eps"} and _values(points[2])["eps"] == 8
    assert _values(points[2])["operating_profit"] == 120
    assert points[3].touched == frozenset() and points[3].values["eps"].disclosure_no == "3"


def test_withdrawal_mark_on_a_correction_still_withdraws() -> None:
    stmts = [
        _st("1", "2026-08-05", "1Q", FY27, FSales=1000, FEPS=7),
        _st("2", "2026-08-20", "1Q", FY27, withdrawn=ALL),
    ]
    assert _last(stmts)["eps"] is None and _last(stmts)["net_sales"] is None

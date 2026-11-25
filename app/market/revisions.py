"""修正履歴のデータ源: J-Quants の短信サマリ (statements)。

予想は共通の層 (`app/market/forecasts.py` の `forecast_points`) で開示を古い順に畳み、年度ごとの
状態にした値。年度は本決算の短信が来期 (`raw_json` の `NxtFYEn`)、それ以外の開示は `fiscal_year_end`。
取り下げの印がある開示は withdrawn の点。実績は本決算 (FY / 4Q) の累計。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..config import EARNINGS_PARAMS as EP
from ..models import FORECAST_ITEMS, Statement
from ..revisions import ACTUAL, FORECAST, WITHDRAWN, RevisionPoint
from .analytics import FORECAST_KEYS, _scale_eps, is_actual, metrics, next_year_end, statement_history
from .forecasts import (CORRECTION, IGNORED, REPORT, ForecastPoint, Sourced, current_forecast, forecast_points,
                        next_forecast)
from .withdrawals import Withdrawn, withdrawn_items

ANNUAL_REPORT_PERIODS = ("FY", "4Q")


def _is_next_year(p: ForecastPoint) -> bool:
    return p.role in (REPORT, CORRECTION) and (p.statement.type_of_document or "").startswith("FY")


def forecast_year(p: ForecastPoint) -> date | None:
    s = p.statement
    return next_year_end(p.raw, s.fiscal_year_end) if _is_next_year(p) else s.fiscal_year_end


def stated_eps(p: ForecastPoint, withdrawn: frozenset[str]) -> Sourced:
    stated = next_forecast(p.raw) if _is_next_year(p) else current_forecast(p.statement)
    value = None if "eps" in withdrawn else stated["eps"]
    return Sourced(value, p.values["eps"].disclosure_no, p.values["eps"].disclosed_date)


def _year_values(state: dict[str, Sourced], by_no: dict) -> dict[str, float | None]:
    out = {k: state[k].value if k in state else None for k in FORECAST_KEYS}
    eps = state.get("eps")
    if eps is not None and eps.value is not None:
        source = by_no.get(eps.disclosure_no)
        _scale_eps(out, getattr(source, "fc_eps_scale", 1.0))
    return out


def statement_points(hist: list[Statement], withdrawn: Withdrawn = withdrawn_items) -> list[RevisionPoint]:
    by_no = {s.disclosure_no: s for s in hist}
    years: dict[date, dict[str, Sourced]] = {}
    points = []
    for p in forecast_points(hist, withdrawn):
        s = p.statement
        order = (s.disclosed_time or "", s.disclosure_no or "")
        if s.type_of_period in ANNUAL_REPORT_PERIODS and is_actual(s) and s.fiscal_year_end:
            points.append(RevisionPoint(s.fiscal_year_end, ACTUAL, s.disclosed_date, metrics(s), order))
        items = p.touched & set(FORECAST_ITEMS)
        fy_end = forecast_year(p)
        if p.role == IGNORED or not items or fy_end is None:
            continue
        state = years.setdefault(fy_end, {})
        if p.role == REPORT:
            state.clear()
        state.update({k: p.values[k] for k in items})
        gone = withdrawn(s) & items
        if "eps" in items:
            state["eps"] = stated_eps(p, gone)
        kind = WITHDRAWN if gone else FORECAST
        points.append(RevisionPoint(fy_end, kind, s.disclosed_date, _year_values(state, by_no), order))
    return points


def jquants_points(db: Session, stmt: Statement) -> list[RevisionPoint]:
    """選んだ開示の日までの開示から。前期本決算の来期予想を拾うため 1 年余分に遡る。"""
    hist = statement_history(db, stmt.code, stmt.disclosed_date,
                             366 * (EP["revision_history_years"] + 1), with_raw_json=True)
    return statement_points(hist)

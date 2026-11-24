from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from ..models import DIVIDEND_ITEM, FORECAST_ITEMS, WITHDRAWABLE_ITEMS
from .analytics import ORDER, is_actual, is_quarter_flash_revision
from .withdrawals import Withdrawn, withdrawn_items

REPORT = "report"
REVISION = "revision"
IGNORED = "ignored"

CURRENT_KEYS = {"net_sales": "FSales", "operating_profit": "FOP", "ordinary_profit": "FOdP",
                "profit": "FNP", "eps": "FEPS"}
NEXT_KEYS = {"net_sales": "NxFSales", "operating_profit": "NxFOP", "ordinary_profit": "NxFOdP",
             "profit": "NxFNp", "eps": "NxFEPS"}
CURRENT_DIVIDEND_KEY = "FDivAnn"
NEXT_DIVIDEND_KEY = "NxFDivAnn"


@dataclass(frozen=True)
class Sourced:
    value: float | None = None
    disclosure_no: str | None = None
    disclosed_date: date | None = None


@dataclass(frozen=True)
class ForecastPoint:
    statement: Any
    raw: dict
    role: str
    touched: frozenset[str]
    values: Mapping[str, Sourced]


def num(raw: dict, key: str) -> float | None:
    v = raw.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def report_shares(raw: dict) -> float | None:
    average = num(raw, "AvgSh")
    if average and average > 0:
        return average
    profit, eps = num(raw, "NP"), num(raw, "EPS")
    if profit and eps and profit / eps > 0:
        return profit / eps
    issued, treasury = num(raw, "ShOutFY"), num(raw, "TrShFY")
    return issued - (treasury or 0) if issued else None


def eps_or_from_profit(eps: float | None, profit: float | None, shares: float | None) -> float | None:
    if eps is not None:
        return eps
    return round(profit / shares, 2) if profit is not None and shares else None


def statement_order(s) -> tuple:
    return (s.disclosed_date, s.disclosed_time or "", s.disclosure_no)


def parse_raw(s) -> dict:
    try:
        raw = json.loads(s.raw_json) if s.raw_json else {}
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


CURRENT_COLUMN_PREFIX = "fy_"


def current_forecast(s) -> dict[str, float | None]:
    out = {}
    for item in FORECAST_ITEMS:
        v = getattr(s, CURRENT_COLUMN_PREFIX + item, None)
        out[item] = None if v is None else float(v)
    return out


def next_forecast(raw: dict) -> dict[str, float | None]:
    return {item: num(raw, NEXT_KEYS[item]) for item in FORECAST_ITEMS}


def _report_values(given: dict[str, float | None], shares: float | None) -> dict[str, float | None]:
    out = dict(given)
    out["eps"] = eps_or_from_profit(given["eps"], given["profit"], shares)
    return out


def _revision_values(given: dict[str, float | None], shares: float | None) -> dict[str, float | None]:
    out = {item: v for item, v in given.items() if item != "eps" and v is not None}
    if given["eps"] is not None or given["profit"] is not None:
        out["eps"] = eps_or_from_profit(given["eps"], given["profit"], shares)
    return out


def forecast_points(stmts: Iterable, withdrawn: Withdrawn = withdrawn_items) -> list[ForecastPoint]:
    state = {item: Sourced() for item in WITHDRAWABLE_ITEMS}
    latest: tuple[date, int] | None = None
    shares: float | None = None
    out: list[ForecastPoint] = []
    for s in sorted(stmts, key=statement_order):
        raw = parse_raw(s)
        found: dict[str, float | None] = {}
        role = IGNORED
        if is_actual(s):
            full_year = (s.type_of_document or "").startswith("FY")
            found[DIVIDEND_ITEM] = num(raw, NEXT_DIVIDEND_KEY if full_year else CURRENT_DIVIDEND_KEY)
            n = ORDER.get(s.type_of_period or "")
            key = (s.fiscal_year_end, n)
            if n and s.fiscal_year_end is not None and (latest is None or key >= latest):
                latest, role = key, REPORT
                shares = report_shares(raw)
                found.update(_report_values(next_forecast(raw) if full_year else current_forecast(s), shares))
        else:
            dividend = num(raw, CURRENT_DIVIDEND_KEY)
            if dividend is not None:
                found[DIVIDEND_ITEM] = dividend
            if not is_quarter_flash_revision(s):
                role = REVISION
                found.update(_revision_values(current_forecast(s), shares))
        for item in withdrawn(s):
            if item == DIVIDEND_ITEM or role != IGNORED:
                found[item] = None
        here = (s.disclosure_no, s.disclosed_date)
        for item, v in found.items():
            state[item] = Sourced(v, *here)
        out.append(ForecastPoint(s, raw, role, frozenset(found), dict(state)))
    return out


def latest_forecast(points: list[ForecastPoint], item: str) -> Sourced:
    return points[-1].values[item] if points else Sourced()

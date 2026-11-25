from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from ..models import DIVIDEND_ITEM, FORECAST_ITEMS, WITHDRAWABLE_ITEMS
from .analytics import ORDER, is_actual, is_quarter_flash_revision, report_kind, shift_year
from .withdrawals import Withdrawn, withdrawn_items

REPORT = "report"
CORRECTION = "correction"
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


def year_start(s) -> date | None:
    start = getattr(s, "fiscal_year_start", None)
    if start is not None or s.fiscal_year_end is None:
        return start
    return shift_year(s.fiscal_year_end, -1) + timedelta(days=1)


ReportKeys = tuple[tuple[date, int], tuple[date, int]]


def report_keys(s) -> ReportKeys | None:
    n = ORDER.get(s.type_of_period or "")
    if not n or s.fiscal_year_end is None:
        return None
    return (year_start(s), n), (s.fiscal_year_end, n)


def is_older_report(keys: ReportKeys, latest: ReportKeys | None) -> bool:
    return latest is not None and keys[0] < latest[0] and keys[1] < latest[1]


def newest_keys(keys: ReportKeys, latest: ReportKeys | None) -> ReportKeys:
    return keys if latest is None else (max(keys[0], latest[0]), max(keys[1], latest[1]))


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


def _changed_from(stated: dict[str, float | None], given: dict[str, float | None]) -> dict[str, float | None]:
    return {item: v for item, v in given.items() if v is not None and v != stated.get(item)}


def forecast_points(stmts: Iterable, withdrawn: Withdrawn = withdrawn_items) -> list[ForecastPoint]:
    state = {item: Sourced() for item in WITHDRAWABLE_ITEMS}
    latest: ReportKeys | None = None
    stated_by_period: dict[tuple, dict[str, float | None]] = {}
    shares: float | None = None
    out: list[ForecastPoint] = []
    for s in sorted(stmts, key=statement_order):
        raw = parse_raw(s)
        found: dict[str, float | None] = {}
        role = IGNORED
        if is_actual(s):
            full_year = (s.type_of_document or "").startswith("FY")
            given = {DIVIDEND_ITEM: num(raw, NEXT_DIVIDEND_KEY if full_year else CURRENT_DIVIDEND_KEY)}
            keys = report_keys(s)
            if keys is not None and not is_older_report(keys, latest):
                latest = newest_keys(keys, latest)
                shares = report_shares(raw)
                given.update(_report_values(next_forecast(raw) if full_year else current_forecast(s), shares))
                role = REPORT
            period = (s.fiscal_year_end, *report_kind(s))
            stated = stated_by_period.get(period)
            if stated is None:
                found.update(given)
            else:
                found.update(_changed_from(stated, given))
                role = CORRECTION if role == REPORT else role
            stated_by_period[period] = {**(stated or {}), **{k: v for k, v in given.items() if v is not None}}
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

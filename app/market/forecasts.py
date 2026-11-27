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
STALE = "stale"
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
    stated: Mapping[str, Sourced]


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
    return outstanding_shares(raw)


def outstanding_shares(raw: dict) -> float | None:
    issued, treasury = num(raw, "ShOutFY"), num(raw, "TrShFY")
    shares = issued - (treasury or 0) if issued else None
    return shares if shares and shares > 0 else None


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


HeldYear = tuple[date, bool]


def is_stale(s, held: HeldYear | None) -> bool:
    if held is None or s.fiscal_year_end is None:
        return False
    end, next_year = held
    return s.fiscal_year_end <= end if next_year else s.fiscal_year_end < end


def newest_keys(keys: ReportKeys, latest: ReportKeys | None) -> ReportKeys:
    return keys if latest is None else (max(keys[0], latest[0]), max(keys[1], latest[1]))


def parse_raw(s) -> dict:
    try:
        raw = json.loads(s.raw_json) if getattr(s, "raw_json", None) else {}
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
    held: HeldYear | None = None
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
            outdated = keys is not None and is_older_report(keys, latest)
            if keys is not None and not outdated:
                latest = newest_keys(keys, latest)
                held = (s.fiscal_year_end, full_year)
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
            outdated = is_stale(s, held)
            dividend = num(raw, CURRENT_DIVIDEND_KEY)
            if dividend is not None:
                found[DIVIDEND_ITEM] = dividend
            if not is_quarter_flash_revision(s):
                role = STALE if outdated else REVISION
                found.update(_revision_values(current_forecast(s), shares))
        for item in withdrawn(s):
            if item == DIVIDEND_ITEM or role != IGNORED:
                found[item] = None
        here = (s.disclosure_no, s.disclosed_date)
        sourced = {item: Sourced(v, *here) for item, v in found.items()}
        if not outdated:
            state.update(sourced)
        out.append(ForecastPoint(s, raw, role, frozenset(found), dict(state), sourced))
    return out


def stated_value(s, item: str) -> float | None:
    if s is None:
        return None
    if is_actual(s) and (s.type_of_document or "").startswith("FY"):
        return num(parse_raw(s), NEXT_KEYS[item])
    return getattr(s, CURRENT_COLUMN_PREFIX + item, None)


def correction_overrides(stmts: Iterable, stmt) -> dict[str, Any]:
    upto = statement_order(stmt)
    point = next((p for p in forecast_points([s for s in stmts if statement_order(s) <= upto])
                  if p.statement is stmt), None)
    if point is None or point.role != CORRECTION:
        return {}
    by_no = {s.disclosure_no: s for s in stmts}
    own_scale = getattr(stmt, "fc_eps_scale", 1.0) or 1.0
    values = {}
    for item in FORECAST_ITEMS:
        v = point.values[item]
        source = by_no.get(v.disclosure_no)
        if v.disclosure_no == stmt.disclosure_no or (v.value is not None and stated_value(source, item) is None):
            continue
        if v.value is not None and item == "eps":
            values[item] = v.value * getattr(source, "fc_eps_scale", 1.0) / own_scale
        else:
            values[item] = v.value
    if not (stmt.type_of_document or "").startswith("FY"):
        return {CURRENT_COLUMN_PREFIX + item: v for item, v in values.items()}
    raw = {**point.raw, **{NEXT_KEYS[item]: "" if v is None else str(v) for item, v in values.items()}}
    return {"raw_json": json.dumps(raw)}


def latest_forecast(points: list[ForecastPoint], item: str) -> Sourced:
    return points[-1].values[item] if points else Sourced()

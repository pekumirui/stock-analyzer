from __future__ import annotations

from typing import Callable, Iterable

from .analytics import ORDER, is_actual
from .forecasts import Sourced, num, outstanding_shares, parse_raw, statement_order

PER_SHARE_MATCH_TOLERANCE = 0.02
MIN_OUTSTANDING_TO_AVERAGE = 2 / 3


def period_newest_last(s) -> tuple:
    return (s.fiscal_year_end, ORDER.get(s.type_of_period or "", 0), statement_order(s))


def latest_reported(stmts: Iterable, read: Callable[[dict], float | None]) -> Sourced:
    found = [(s, read(parse_raw(s))) for s in stmts if is_actual(s) and s.fiscal_year_end is not None]
    found = [(s, v) for s, v in found if v is not None]
    if not found:
        return Sourced()
    s, v = max(found, key=lambda sv: period_newest_last(sv[0]))
    return Sourced(v, s.disclosure_no, s.disclosed_date)


def reported_bps(raw: dict) -> float | None:
    return num(raw, "BPS")


def equity_per_share(raw: dict) -> float | None:
    equity, shares = num(raw, "ShEq"), outstanding_shares(raw)
    return equity / shares if equity is not None and shares else None


def book_value_per_share(raw: dict) -> float | None:
    bps = reported_bps(raw)
    return bps if bps is not None else equity_per_share(raw)


def equity_matches_bps(raw: dict) -> float | None:
    bps, computed = reported_bps(raw), equity_per_share(raw)
    if bps is None or computed is None or bps <= 0:
        return None
    return float(abs(computed / bps - 1) <= PER_SHARE_MATCH_TOLERANCE)


def latest_book_value(stmts: list) -> Sourced:
    if latest_reported(stmts, equity_matches_bps).value == 0.0:
        return latest_reported(stmts, reported_bps)
    return latest_reported(stmts, book_value_per_share)


def eps_matches_profit(raw: dict) -> float | None:
    eps, profit, average = num(raw, "EPS"), num(raw, "NP"), num(raw, "AvgSh")
    if eps is None or profit is None or not average or eps <= 0:
        return None
    return float(abs(profit / average / eps - 1) <= PER_SHARE_MATCH_TOLERANCE)


def outstanding_covers_average(raw: dict) -> float | None:
    shares, average = outstanding_shares(raw), num(raw, "AvgSh")
    if not shares or not average or average <= 0:
        return None
    return float(shares / average >= MIN_OUTSTANDING_TO_AVERAGE)


def profit_over_shares_is_eps(stmts: list) -> bool:
    return (latest_reported(stmts, eps_matches_profit).value != 0.0
            and latest_reported(stmts, outstanding_covers_average).value != 0.0)

from __future__ import annotations

from typing import Callable, Iterable

from .analytics import ORDER, is_actual
from .forecasts import Sourced, num, outstanding_shares, parse_raw, statement_order

PER_SHARE_MATCH_TOLERANCE = 0.02
MIN_OUTSTANDING_TO_AVERAGE = 2 / 3


def period_newest_last(s) -> tuple:
    return (s.fiscal_year_end, ORDER.get(s.type_of_period or "", 0), statement_order(s))


class Newest:
    def __init__(self, read: Callable[[dict], float | None]) -> None:
        self.read = read
        self.key: tuple | None = None
        self.found = Sourced()

    def add(self, s, raw: dict | None = None) -> None:
        if not is_actual(s) or s.fiscal_year_end is None:
            return
        v = self.read(parse_raw(s) if raw is None else raw)
        key = period_newest_last(s)
        if v is not None and (self.key is None or key > self.key):
            self.key, self.found = key, Sourced(v, s.disclosure_no, s.disclosed_date)


def latest_reported(stmts: Iterable, read: Callable[[dict], float | None]) -> Sourced:
    newest = Newest(read)
    for s in stmts:
        newest.add(s)
    return newest.found


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


class ProfitPerShare:
    def __init__(self) -> None:
        self.shares = Newest(outstanding_shares)
        self.checks = (Newest(eps_matches_profit), Newest(outstanding_covers_average))

    def add(self, s, raw: dict | None = None) -> None:
        raw = parse_raw(s) if raw is None else raw
        for newest in (self.shares, *self.checks):
            newest.add(s, raw)

    def eps(self, profit: Sourced, company_eps: Sourced) -> Sourced:
        shares = self.shares.found
        if profit.value is None or not shares.value or any(c.found.value == 0.0 for c in self.checks):
            return company_eps
        return Sourced(profit.value / shares.value, shares.disclosure_no, shares.disclosed_date)

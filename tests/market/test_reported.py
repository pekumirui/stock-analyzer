from __future__ import annotations

import json
from datetime import date
from itertools import combinations, permutations

from app.market.forecasts import outstanding_shares
from app.market.reported import book_value_per_share, eps_matches_profit, latest_book_value, latest_reported
from app.models import Statement

Y0, Y1 = date(2026, 3, 31), date(2027, 3, 31)


def _st(no: str, day: str, doc: str, period: str, fy_end: date, **raw) -> Statement:
    return Statement(disclosure_no=no, code="99990", disclosed_date=date.fromisoformat(day),
                     disclosed_time="15:30:00", type_of_document=doc, type_of_period=period,
                     fiscal_year_end=fy_end, raw_json=json.dumps(raw))


DISCLOSURES = {
    "通期の短信": _st("1", "2026-05-12", "FYFinancialStatements_Consolidated_JP", "FY", Y0, ShOutFY="200"),
    "1Q の短信": _st("2", "2026-08-05", "1QFinancialStatements_Consolidated_JP", "1Q", Y1, ShOutFY="190"),
    "予想の修正": _st("3", "2026-10-30", "EarnForecastRevision", "FY", Y1, ShOutFY="1"),
    "2Q の短信": _st("4", "2026-11-10", "2QFinancialStatements_Consolidated_JP", "2Q", Y1,
                    ShOutFY="172", TrShFY="2"),
    "2Q の株数が空欄の訂正": _st("5", "2026-11-20", "2QFinancialStatements_Consolidated_JP", "2Q", Y1,
                             ShOutFY="", TrShFY=""),
    "2Q の株数を直した訂正": _st("6", "2026-11-25", "2QFinancialStatements_Consolidated_JP", "2Q", Y1,
                             ShOutFY="168"),
    "後から届いた 1Q の訂正": _st("7", "2026-12-01", "1QFinancialStatements_Consolidated_JP", "1Q", Y1,
                              ShOutFY="185"),
}

WINS_FIRST = ("2Q の株数を直した訂正", "2Q の短信", "後から届いた 1Q の訂正", "1Q の短信", "通期の短信")


def expected(names: tuple[str, ...]) -> float | None:
    winner = next((n for n in WINS_FIRST if n in names), None)
    return outstanding_shares(json.loads(DISCLOSURES[winner].raw_json)) if winner else None


def test_latest_shares_for_every_order_of_disclosures() -> None:
    checked = 0
    for k in range(len(DISCLOSURES) + 1):
        for names in combinations(DISCLOSURES, k):
            want = expected(names)
            for order in permutations(names):
                got = latest_reported([DISCLOSURES[n] for n in order], outstanding_shares)
                assert got.value == want, order
                checked += 1
    assert checked == sum(len(list(permutations(c))) for k in range(8) for c in combinations(range(7), k))


def test_book_value_prefers_reported_bps_then_equity_over_shares() -> None:
    assert book_value_per_share({"ShEq": "449691000000", "ShOutFY": "166454180", "TrShFY": "1968286",
                                 "BPS": ""}) == 449691000000 / 164485894
    assert book_value_per_share({"ShEq": "661360000000", "ShOutFY": "199954180", "TrShFY": "1966478",
                                 "BPS": "3340.44"}) == 3340.44
    assert book_value_per_share({"ShEq": "100", "ShOutFY": "", "BPS": ""}) is None


def test_book_value_keeps_reported_bps_when_equity_over_shares_disagrees() -> None:
    q2 = _st("12", "2026-06-11", "2QFinancialStatements_Consolidated_JP", "2Q", Y1,
             ShEq="1476000000", ShOutFY="16214400", TrShFY="603480", BPS="-43.92")
    q3 = _st("13", "2026-09-10", "3QFinancialStatements_Consolidated_JP", "3Q", Y1,
             ShEq="2265000000", ShOutFY="16214400", TrShFY="603480", BPS="8.58")
    fy = _st("14", "2026-12-10", "FYFinancialStatements_Consolidated_JP", "FY", Y1,
             ShEq="2500000000", ShOutFY="16214400", TrShFY="603480", BPS="")
    assert latest_book_value([q2, q3, fy]).found.value == 8.58
    assert latest_book_value([q2, fy]).found.value == 2500000000 / (16214400 - 603480)


def test_eps_matches_profit_over_average_shares() -> None:
    assert eps_matches_profit({"EPS": "175.13", "NP": "31321000000", "AvgSh": "178842968"}) == 1.0
    assert eps_matches_profit({"EPS": "52.02", "NP": "812000000", "AvgSh": "11000000"}) == 0.0
    assert eps_matches_profit({"EPS": "-2.25", "NP": "-878000000", "AvgSh": "389841764"}) is None

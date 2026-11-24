from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

from app.market.forecasts import forecast_points
from app.market.withdrawals import withdrawn_items
from app.models import DIVIDEND_ITEM, FORECAST_ITEMS

YEAR = date(2025, 3, 31)
PRIOR = date(2024, 3, 31)
ITEMS = ("profit", "eps")
ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4, "FY": 4}


@dataclass(frozen=True)
class Kind:
    name: str
    document: str
    period: str
    year: date
    target: date


KINDS = (
    Kind("fy_report_prior", "FYFinancialStatements_Consolidated_JP", "FY", PRIOR, YEAR),
    Kind("q3_report_prior", "3QFinancialStatements_Consolidated_JP", "3Q", PRIOR, PRIOR),
    Kind("q1_report", "1QFinancialStatements_Consolidated_JP", "1Q", YEAR, YEAR),
    Kind("q2_report", "2QFinancialStatements_Consolidated_JP", "2Q", YEAR, YEAR),
    Kind("revision", "EarnForecastRevision", "FY", YEAR, YEAR),
    Kind("revision_prior", "EarnForecastRevision", "FY", PRIOR, PRIOR),
    Kind("quarter_flash", "EarnForecastRevision", "1Q", YEAR, YEAR),
)
WITHDRAWALS = ((), ("profit",), (DIVIDEND_ITEM,))


@dataclass(frozen=True)
class Spec:
    kind: Kind
    given: tuple[str, ...]
    dividend: bool
    withdrawn: tuple[str, ...]


def specs(kinds=KINDS) -> list[Spec]:
    out = []
    for kind in kinds:
        for n in range(len(ITEMS) + 1):
            for given in itertools.combinations(ITEMS, n):
                for dividend in (False, True):
                    for withdrawn in WITHDRAWALS:
                        out.append(Spec(kind, given, dividend, withdrawn))
    return out


def is_report(kind: Kind) -> bool:
    return "FinancialStatements" in kind.document


def is_flash(kind: Kind) -> bool:
    return kind.document == "EarnForecastRevision" and kind.period in ("1Q", "3Q")


def stated(index: int, item: str) -> float:
    return float(100 * (index + 1) + FORECAST_ITEMS.index(item) + 1) if item in FORECAST_ITEMS else float(index + 1)


def build(seq: tuple[Spec, ...]) -> list[SimpleNamespace]:
    out = []
    for i, spec in enumerate(seq):
        k = spec.kind
        next_year = is_report(k) and k.period == "FY"
        raw: dict = {"NxtFYEn": k.target.isoformat()} if next_year else {}
        fy = {item: None for item in FORECAST_ITEMS}
        for item in spec.given:
            if next_year:
                raw[{"profit": "NxFNp", "eps": "NxFEPS"}[item]] = str(stated(i, item))
            else:
                fy[item] = stated(i, item)
        if spec.dividend:
            raw["NxFDivAnn" if next_year else "FDivAnn"] = str(stated(i, DIVIDEND_ITEM))
        if is_report(k):
            raw["AvgSh"] = "1000"
        s = SimpleNamespace(
            disclosure_no=f"2025{i:02d}",
            disclosed_date=date(2024, 5, 1) + timedelta(days=i),
            disclosed_time="15:00",
            type_of_document=k.document,
            type_of_period=k.period,
            fiscal_year_end=k.year,
            raw_json=json.dumps(raw),
            withdrawn_forecasts=spec.withdrawn or None,
            **{f"fy_{item}": v for item, v in fy.items()},
        )
        out.append(s)
    return out


def no_list(s) -> frozenset[str]:
    return withdrawn_items(s, {})


def snapshot(points) -> list[tuple]:
    return [(p.statement.disclosure_no, p.role, tuple(sorted(p.touched)),
             tuple((k, v.value, v.disclosure_no) for k, v in sorted(p.values.items()))) for p in points]


def violations(seq: tuple[Spec, ...]) -> list[tuple[str, int]]:
    stmts = build(seq)
    points = forecast_points(stmts, no_list)
    found = []
    shuffled = stmts[1:] + stmts[:1]
    if snapshot(forecast_points(list(reversed(stmts)), no_list)) != snapshot(points) or \
            snapshot(forecast_points(shuffled, no_list)) != snapshot(points):
        found.append(("InputOrderDoesNotMatter", len(seq) - 1))
    by_no = {s.disclosure_no: (i, seq[i]) for i, s in enumerate(stmts)}
    prev = {item: None for item in points[0].values} if points else {}
    latest = None
    for i, (p, spec) in enumerate(zip(points, seq)):
        k = spec.kind
        now = {item: v.value for item, v in p.values.items()}
        changed = {item for item in now if now[item] != prev[item]}
        key = (k.year, ORDER[k.period])
        stale = is_report(k) and latest is not None and key < latest
        if is_report(k) and not stale:
            latest = key
        if is_flash(k) and changed & set(FORECAST_ITEMS):
            found.append(("QuarterFlashLeavesForecastAlone", i))
        if stale and changed:
            found.append(("OlderReportChangesNothing", i))
        target = latest_target(seq[:i + 1])
        if target is not None and k.target < target and changed and not stale:
            found.append(("OnlyTheCurrentYearIsOverlaid", i))
        if k.document == "EarnForecastRevision" and not is_flash(k):
            for item in set(FORECAST_ITEMS) - set(spec.given) - set(spec.withdrawn) - {"eps"}:
                if item in changed:
                    found.append(("BlankInRevisionCarriesOver", i))
        if not (is_flash(k) or stale):
            for item in spec.withdrawn:
                if now.get(item) is not None:
                    found.append(("WithdrawnBecomesUndecided", i))
        for item, v in p.values.items():
            if v.disclosure_no is None:
                continue
            j, src = by_no[v.disclosure_no]
            ok = v.value is None or v.value == stated(j, item) or (
                item == "eps" and "profit" in src.given)
            if not ok:
                found.append(("ValueComesFromItsSource", i))
        prev = now
    return sorted(set(found))


def latest_target(seq: tuple[Spec, ...]) -> date | None:
    latest, target = None, None
    for spec in seq:
        k = spec.kind
        if is_report(k):
            key = (k.year, ORDER[k.period])
            if latest is None or key >= latest:
                latest, target = key, k.target
    return target


def describe(seq: tuple[Spec, ...]) -> str:
    parts = []
    for i, spec in enumerate(seq):
        bits = [spec.kind.name]
        bits += [f"{item}={stated(i, item):g}" for item in spec.given]
        if spec.dividend:
            bits.append(f"div={stated(i, DIVIDEND_ITEM):g}")
        if spec.withdrawn:
            bits.append("withdrawn=" + "+".join(spec.withdrawn))
        parts.append(f"{i}:" + " ".join(bits))
    return " -> ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", type=int, default=3)
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--expect", action="append", default=[])
    ap.add_argument("--kinds", default="")
    args = ap.parse_args(argv)
    names = set(args.kinds.split(",")) - {""}
    pool = specs(tuple(k for k in KINDS if not names or k.name in names))
    shortest: dict[str, tuple] = {}
    checked = 0
    for n in range(1, args.length + 1):
        for seq in itertools.product(pool, repeat=n):
            checked += 1
            for rule, at in violations(seq):
                if args.only and rule not in args.only:
                    continue
                if rule not in shortest:
                    shortest[rule] = (seq, at)
    print(f"checked {checked} sequences up to length {args.length}")
    for rule in sorted(shortest):
        seq, at = shortest[rule]
        print(f"VIOLATED {rule} at step {at}: {describe(seq)}")
    unexpected = set(shortest) - set(args.expect)
    missing = set(args.expect) - set(shortest)
    for rule in sorted(missing):
        print(f"EXPECTED BUT HELD {rule}")
    return 1 if unexpected or missing else 0


if __name__ == "__main__":
    sys.exit(main())

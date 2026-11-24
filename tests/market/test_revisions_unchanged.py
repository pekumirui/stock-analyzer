from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from app.config import EARNINGS_PARAMS as EP
from app.market.analytics import align_eps, forecast_revision
from app.market.revisions import statement_points
from app.market.withdrawals import no_withdrawals
from app.models import Statement
from app.revisions import revision_rows

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "db"
BEFORE = Path(__file__).resolve().parent / "testdata" / "revisions_before_kab551.json"
DATES = ("disclosed_date", "period_start", "period_end", "fiscal_year_start", "fiscal_year_end")
NUMBERS = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
           "f2_net_sales", "f2_operating_profit", "f2_ordinary_profit", "f2_profit", "f2_eps",
           "fy_net_sales", "fy_operating_profit", "fy_ordinary_profit", "fy_profit", "fy_eps")
TEXTS = ("disclosure_no", "code", "disclosed_time", "type_of_period", "type_of_document", "raw_json")
FY_RAW = {"net_sales": "FSales", "operating_profit": "FOP", "ordinary_profit": "FOdP", "profit": "FNP",
          "eps": "FEPS"}
NX_RAW = {"net_sales": "NxFSales", "operating_profit": "NxFOP", "ordinary_profit": "NxFOdP", "profit": "NxFNp",
          "eps": "NxFEPS"}
ALL_YEARS = 1000
SEED, CASES = 5512, 60
UPTO = date(2028, 1, 1)
PARTIAL_REVISION = "82670"
AFTER_UNDECIDED_REPORT = "26780"


def build(rows: list[dict]) -> list[Statement]:
    out = []
    for r in rows:
        kw = {k: r.get(k) for k in (*TEXTS, *NUMBERS)}
        for k in DATES:
            kw[k] = date.fromisoformat(r[k]) if r.get(k) else None
        kw["is_revision"] = bool(r.get("is_revision"))
        out.append(Statement(**kw))
    return out


def golden_inputs() -> dict[str, tuple[list[dict], list[tuple[date, float]]]]:
    stmts: dict[str, list[dict]] = {}
    with (GOLDEN / "statements.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            row = {k: (r[k] or None) for k in (*TEXTS, *DATES)}
            for k in NUMBERS:
                row[k] = float(r[k]) if r[k] else None
            row["is_revision"] = r["is_revision"] in ("1", "True", "true")
            stmts.setdefault(r["code"], []).append(row)
    splits: dict[str, list[tuple[date, float]]] = {}
    with (GOLDEN / "daily_prices.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["adj_factor"] and float(r["adj_factor"]) != 1.0:
                splits.setdefault(r["code"], []).append((date.fromisoformat(r["date"][:10]), float(r["adj_factor"])))
    return {code: (rows, sorted(splits.get(code, []))) for code, rows in sorted(stmts.items())}


def _row(no: int, day: date, period: str, doc: str, fy_end: date, raw: dict, **cols) -> dict:
    row = {"disclosure_no": f"{no:06d}", "code": "99990", "disclosed_date": day.isoformat(),
           "disclosed_time": "15:30:00", "type_of_period": period, "type_of_document": doc,
           "fiscal_year_end": fy_end.isoformat(), "is_revision": "FinancialStatements" not in doc,
           "raw_json": json.dumps({k: str(v) for k, v in raw.items() if v is not None})}
    row.update(cols)
    return row


def random_case(rnd: random.Random, i: int) -> tuple[list[dict], list[tuple[date, float]]]:
    ifrs = rnd.random() < 0.2
    silent = rnd.random() < 0.08
    base = rnd.uniform(50, 150)
    events: list[tuple[date, int, str, str, date, dict, dict]] = []

    def forecast(scale: float) -> dict[str, float | None]:
        if silent:
            return {k: None for k in FY_RAW}
        v = {"net_sales": round(base * 100 * scale), "operating_profit": round(base * 10 * scale) - 300,
             "ordinary_profit": None if ifrs else round(base * 11 * scale), "profit": round(base * 7 * scale),
             "eps": round(base / 10 * scale, 2)}
        return v

    for fy_year in (2024, 2025, 2026, 2027):
        fy = date(fy_year, 3, 31)
        for q, (month, yr) in enumerate(((8, fy_year - 1), (11, fy_year - 1), (2, fy_year), (5, fy_year)), 1):
            day = date(yr, month, rnd.randint(1, 14))
            if day > UPTO:
                continue
            base *= rnd.uniform(0.85, 1.2)
            period = "FY" if q == 4 else f"{q}Q"
            doc = f"{period}FinancialStatements_Consolidated_{'IFRS' if ifrs else 'JP'}"
            actual = {"net_sales": round(base * 25 * q), "operating_profit": round(base * 3 * q),
                      "ordinary_profit": None if ifrs else round(base * 3 * q), "profit": round(base * 2 * q),
                      "eps": round(base / 40 * q, 2)}
            fc = forecast(1.0)
            raw = {"NP": actual["profit"], "EPS": actual["eps"], "AvgSh": rnd.choice([None, 700])}
            cols = dict(actual)
            if q == 4:
                raw.update({NX_RAW[k]: v for k, v in fc.items()})
                raw["NxtFYEn"] = date(fy_year + 1, 3, 31).isoformat()
            else:
                raw.update({FY_RAW[k]: v for k, v in fc.items()})
                cols.update({"fy_" + k: v for k, v in fc.items()})
            events.append((day, 0, period, doc, fy, raw, cols))
            for n in range(rnd.choice([0, 0, 1, 2])):
                rday = day + timedelta(days=rnd.choice([0, rnd.randint(1, 40)]))
                rper = rnd.choice(["FY", "FY", "2Q", "1Q", "3Q"])
                rfy = fy if q < 4 else date(fy_year + 1, 3, 31)
                if rnd.random() < 0.15:
                    events.append((rday, n + 1, "FY", "DividendForecastRevision", rfy,
                                   {"FDivAnn": round(base / 6)}, {}))
                    continue
                rfc = forecast(rnd.uniform(0.7, 1.3))
                rraw = {FY_RAW[k]: v for k, v in rfc.items()}
                events.append((rday, n + 1, rper, "EarnForecastRevision", rfy, rraw,
                               {"fy_" + k: v for k, v in rfc.items()}))
    events.sort(key=lambda e: (e[0], e[1]))
    rows = [_row(no, e[0], *e[2:5], e[5], **e[6]) for no, e in enumerate(events, 1)]
    splits = []
    if rnd.random() < 0.3:
        splits.append((date(2025, rnd.randint(1, 12), 1), rnd.choice([0.5, 0.2, 0.1, 2.0])))
    return [r for r in rows if r["disclosed_date"] <= UPTO.isoformat()], splits


def random_inputs() -> list[tuple[list[dict], list[tuple[date, float]]]]:
    rnd = random.Random(SEED)  # nosec B311
    return [random_case(rnd, i) for i in range(CASES)]


def outputs(rows: list[dict], splits: list[tuple[date, float]], **kw) -> dict:
    stmts = build(rows)
    align_eps(stmts, splits, UPTO)
    window = timedelta(days=EP["list_history_days"])
    history = revision_rows(statement_points(stmts, **kw), ALL_YEARS)
    badges = []
    for s in stmts:
        hist = [h for h in stmts if s.disclosed_date - window <= h.disclosed_date <= s.disclosed_date]
        badges.append([s.disclosure_no, forecast_revision(hist, s, **kw)])
    return json.loads(json.dumps({"history": history, "badges": badges}))


def test_without_marks_matches_the_revisions_before_kab551() -> None:
    before = json.loads(BEFORE.read_text(encoding="utf-8"))
    golden = golden_inputs()
    assert sorted(golden) == sorted(before["golden"])
    for code, (rows, splits) in golden.items():
        if code in (PARTIAL_REVISION, AFTER_UNDECIDED_REPORT):
            continue
        assert outputs(rows, splits, withdrawn=no_withdrawals) == before["golden"][code], code
    cases = random_inputs()
    assert len(cases) == len(before["random"])
    for i, ((rows, splits), want) in enumerate(zip(cases, before["random"])):
        assert outputs(rows, splits, withdrawn=no_withdrawals) == want, i


def test_golden_partial_revision_keeps_profit_and_eps_from_the_report_before() -> None:
    before = json.loads(BEFORE.read_text(encoding="utf-8"))["golden"][PARTIAL_REVISION]
    got = outputs(*golden_inputs()[PARTIAL_REVISION], withdrawn=no_withdrawals)
    assert got["badges"] == before["badges"]
    other_years = [r for r in before["history"] if r["fiscal_year_end"] != "2026-02-28"]
    assert [r for r in got["history"] if r["fiscal_year_end"] != "2026-02-28"] == other_years
    rows = [(r["kind"], r["disclosed_date"], r["values"]["profit"], r["values"]["eps"])
            for r in got["history"] if r["fiscal_year_end"] == "2026-02-28"]
    old = [(r["kind"], r["disclosed_date"], r["values"]["profit"], r["values"]["eps"])
           for r in before["history"] if r["fiscal_year_end"] == "2026-02-28"]
    revised = ("forecast_revised", "2026-01-07")
    assert [x for x in old if x[:2] == revised] == [(*revised, None, None)]
    assert [x for x in rows if x[:2] == revised] == [(*revised, 40_000_000_000.0, 15.49)]
    assert [x for x in rows if x[:2] == ("forecast_revised", "2026-01-08")] == [
        ("forecast_revised", "2026-01-08", None, None)]
    assert [x for x in rows if x[1] not in ("2026-01-07", "2026-01-08")] == [
        x for x in old if x[1] not in ("2026-01-07", "2026-01-08")]


def test_golden_badge_after_an_undecided_report_is_dropped() -> None:
    before = json.loads(BEFORE.read_text(encoding="utf-8"))["golden"][AFTER_UNDECIDED_REPORT]
    got = outputs(*golden_inputs()[AFTER_UNDECIDED_REPORT], withdrawn=no_withdrawals)
    assert got["history"] == before["history"]
    changed = [(no, old, new) for (no, old), (_, new) in zip(before["badges"], got["badges"]) if old != new]
    assert changed == [("20260326590557", {"direction": "down", "pct": -286.4}, None)]

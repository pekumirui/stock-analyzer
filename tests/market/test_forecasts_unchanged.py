from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

from app.api.stock import _dividend_forecast
from tests.market.jquants_columns import fy_columns
from app.market.chart_fund import fund_steps
from app.market.withdrawals import no_withdrawals
from app.models import Statement

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "db"
PINNED = Path(__file__).resolve().parent / "testdata" / "forecasts_pinned.json"
FIELDS = ("disclosure_no", "disclosed_date", "disclosed_time", "type_of_period", "type_of_document",
          "fiscal_year_end", "net_sales", "operating_profit", "ordinary_profit", "profit", "eps", "is_revision")
NUMBERS = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")
START, END = date(2024, 4, 1), date(2027, 6, 30)
SEED, CASES = 551, 50


def build(rows: list[dict]) -> list[Statement]:
    out = []
    for r in rows:
        kw = {k: r.get(k) for k in FIELDS}
        for k in ("disclosed_date", "fiscal_year_end"):
            kw[k] = date.fromisoformat(kw[k]) if kw[k] else None
        out.append(Statement(code="99990", raw_json=json.dumps(r["raw"]), **kw, **fy_columns(r["raw"])))
    return out


def business_days(a: date, b: date) -> list[date]:
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def golden_inputs() -> dict[str, tuple[list[dict], list[date], list[float]]]:
    stmts: dict[str, list[dict]] = {}
    with (GOLDEN / "statements.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            row = {k: (r[k] or None) for k in FIELDS}
            for k in NUMBERS:
                row[k] = float(row[k]) if row[k] else None
            row["is_revision"] = r["is_revision"] in ("1", "True", "true")
            row["raw"] = json.loads(r["raw_json"]) if r["raw_json"] else {}
            stmts.setdefault(r["code"], []).append(row)
    px: dict[str, list[tuple[date, float | None]]] = {}
    with (GOLDEN / "daily_prices.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            px.setdefault(r["code"], []).append(
                (date.fromisoformat(r["date"][:10]), float(r["adj_factor"]) if r["adj_factor"] else None))
    out = {}
    for code, rows in sorted(stmts.items()):
        p = sorted(px.get(code, []))
        out[code] = (rows, [d for d, _ in p], [x for _, x in p])
    return out


def random_case(rnd: random.Random, i: int, n_days: int) -> tuple[list[dict], list[float]]:
    rows: list[dict] = []

    def maybe(v, p=0.75):
        return str(v) if rnd.random() < p else ""

    def add(day, period, fy, doc, raw, op=None):
        rows.append({"disclosure_no": f"{i:03d}{len(rows) + 1:03d}", "disclosed_date": day.isoformat(),
                     "disclosed_time": rnd.choice(["15:30:00", "15:30:00", "15:30:00", None, "11:00:00"]),
                     "type_of_period": period, "type_of_document": doc,
                     "fiscal_year_end": fy.isoformat() if fy else None, "net_sales": None, "operating_profit": op,
                     "ordinary_profit": None, "profit": None, "eps": None,
                     "is_revision": "FinancialStatements" not in doc,
                     "raw": {k: v for k, v in raw.items() if v != ""}})

    base = rnd.uniform(50, 150)
    for fy_year in (2025, 2026, 2027):
        fy = date(fy_year, 3, 31)
        for q, month in ((1, 8), (2, 11), (3, 2), (4, 5)):
            yr = fy_year if q == 4 or month < 8 else fy_year - 1
            day = date(yr, month, rnd.randint(1, 14))
            if day < START or day > END:
                continue
            base *= rnd.uniform(0.85, 1.2)
            period = "FY" if q == 4 else f"{q}Q"
            kind = "Consolidated_JP" if rnd.random() < 0.9 else "NonConsolidated_IFRS"
            pre = "NxF" if q == 4 else "F"
            raw = {pre + "Sales": maybe(round(base * 100)), pre + "OP": maybe(round(base * 10)),
                   pre + "OdP": maybe(round(base * 11)), pre + ("Np" if q == 4 else "NP"): maybe(round(base * 7)),
                   pre + "EPS": maybe(round(base / 10, 2), 0.7), pre + "DivAnn": maybe(round(base / 5), 0.7),
                   "AvgSh": maybe(70, 0.4), "NP": maybe(700, 0.5), "EPS": maybe(10, 0.5),
                   "ShOutFY": maybe(80, 0.4), "TrShFY": maybe(5, 0.4)}
            add(day, period, fy, f"{period}FinancialStatements_{kind}", raw, op=round(base * 3 * q))
            for _ in range(rnd.choice([0, 0, 1, 2])):
                rday = day + timedelta(days=rnd.randint(-3, 40))
                doc = rnd.choice(["EarnForecastRevision"] * 4 + ["DividendForecastRevision", "REITEarnForecastRevision"])
                rper = rnd.choice(["FY", "FY", "2Q", "1Q", "3Q"])
                rfy = fy if rnd.random() < 0.8 else date(fy_year + 1, 3, 31)
                rraw = {k: maybe(v, 0.4) for k, v in (
                    ("FSales", round(base * 95)), ("FOP", round(base * 9)), ("FOdP", round(base * 10)),
                    ("FNP", round(base * 6)), ("FEPS", round(base / 11, 2)), ("FDivAnn", round(base / 6)),
                    ("FNCSales", 5), ("FSales2Q", 3))}
                add(rday, rper, rfy, doc, rraw)
            if rnd.random() < 0.1:
                add(day + timedelta(days=rnd.randint(1, 30)), rnd.choice(["2Q", "3Q", "FY"]), date(fy_year - 1, 3, 31),
                    "3QFinancialStatements_Consolidated_JP",
                    {"FEPS": "1.5", "FDivAnn": "2", "NxFEPS": "3", "NxFDivAnn": "4"}, op=5)
    rnd.shuffle(rows)
    factors = [1.0] * n_days
    if rnd.random() < 0.3:
        factors[rnd.randrange(n_days)] = rnd.choice([0.5, 0.2, 0.1, 2.0])
    return rows, factors


def random_inputs() -> list[tuple[list[dict], list[date], list[float]]]:
    days = business_days(START, END)
    rnd = random.Random(SEED)  # nosec B311
    out = []
    for i in range(CASES):
        rows, factors = random_case(rnd, i, len(days))
        out.append((rows, days, factors))
    return out


def outputs(stmts: list[Statement], days: list[date], factors: list[float]) -> dict:
    f = fund_steps(stmts, days, factors, withdrawn=no_withdrawals)
    return {"steps": [list(x) for x in zip(f["t"], f["eps"], f["op_q"], f["op_ttm"])],
            "dividend": _dividend_forecast(stmts, days, factors, withdrawn=no_withdrawals)}


def test_without_marks_matches_the_pinned_outputs() -> None:
    before = json.loads(PINNED.read_text(encoding="utf-8"))
    golden = golden_inputs()
    assert sorted(golden) == sorted(before["golden"])
    for code, (rows, days, factors) in golden.items():
        assert outputs(build(rows), days, factors) == before["golden"][code], code
    cases = random_inputs()
    assert len(cases) == len(before["random"])
    for i, ((rows, days, factors), want) in enumerate(zip(cases, before["random"])):
        assert outputs(build(rows), days, factors) == want, i

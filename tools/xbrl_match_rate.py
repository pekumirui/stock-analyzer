#!/usr/bin/env python3
"""Offline match-rate: xbrl_financials vs IRBANK golden JSON. No network.

Tolerance (default 1 million_jpy): convert DB JPY→million (value/1e6 if unit is
JPY / Yen / empty; million_jpy as-is), round to nearest integer million, then
require abs(db-golden) <= tolerance. Exit 0 on success even if rate < 100%.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
METRICS = (
    "net_sales", "operating_income", "ordinary_income", "net_income",
    "total_assets", "net_assets", "total_liabilities",
    "operating_cf", "investing_cf",
)
Q_TYPES = {"Q1", "Q2", "Q3"}
FY_TYPES = {"FY", "ANNUAL", "FULL_YEAR", "FYE"}


def parse_ym(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    s = str(raw).strip().replace("/", "-")
    parts = s.split("-")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def to_million(value: Any, unit: str | None) -> int:
    n = float(value)
    u = (unit or "").strip().lower()
    if "million" in u or u in {"百万円", "million_jpy"}:
        scaled = n
    elif u in {"", "jpy", "yen", "円"} or "jpy" in u or "yen" in u:
        scaled = n / 1e6
    else:
        scaled = n / 1e6
    return int(round(scaled))


def load_golden(directory: Path, codes: set[str] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    files = sorted(p for p in directory.glob("*.json") if p.name != "INDEX.json")
    if not files:
        raise SystemExit(f"no golden JSON in {directory}")
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"golden IO/JSON error: {path}: {exc}") from exc
        if not isinstance(data, dict) or "metrics" not in data:
            raise SystemExit(f"golden schema error: {path.name} (need metrics)")
        pt = str(data.get("period_type") or "")
        if pt.lower() in {"full_year"}:
            continue
        if codes and str(data.get("code")) not in codes:
            continue
        data["_file"] = path.name
        out.append(data)
    return out


def open_db(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"verify DB not found: {path}")
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    names = {
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    need = {"xbrl_financials", "xbrl_std_items", "xbrl_filings"}
    missing = sorted(need - names)
    if missing:
        con.close()
        raise SystemExit(f"schema error: missing tables {missing} in {path}")
    return con


def fetch_rows(con: sqlite3.Connection, code: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(METRICS))
    return list(con.execute(
        f"""
        SELECT f.value, f.unit, f.is_consolidated, f.period_type AS fin_pt,
               f.period_end, s.code AS std_code,
               fil.period_type AS fil_pt, fil.fiscal_year_end, fil.submitted_at
        FROM xbrl_financials f
        JOIN xbrl_std_items s ON s.id = f.std_item_id
        JOIN xbrl_filings fil ON fil.id = f.filing_id
        WHERE f.code = ? AND s.code IN ({placeholders})
        """,
        (code, *METRICS),
    ))


def period_ok(row: sqlite3.Row, want_ym: tuple[int, int], quarter: str | None) -> bool:
    end_ym = parse_ym(row["period_end"])
    fy_ym = parse_ym(row["fiscal_year_end"])
    fin_pt = (row["fin_pt"] or "").upper()
    fil_pt = (row["fil_pt"] or "").upper()
    pt = fin_pt or fil_pt
    if quarter:
        if pt != quarter.upper() or pt in FY_TYPES:
            return False
        return fy_ym == want_ym or end_ym == want_ym
    if pt in Q_TYPES:
        return False
    return end_ym == want_ym


def pick_db_value(rows: list[sqlite3.Row], metric: str) -> tuple[int, str] | None:
    cands = [r for r in rows if r["std_code"] == metric]
    if not cands:
        return None

    def key(r: sqlite3.Row) -> tuple:
        consol = 1 if r["is_consolidated"] in (1, True, "1") else 0
        return (consol, str(r["submitted_at"] or ""))

    best = max(cands, key=key)
    return to_million(best["value"], best["unit"]), best["unit"] or ""


def fmt_rate(n: int, d: int) -> str:
    if d == 0:
        return "n/a"
    return f"{n}/{d} = {n / d:.4f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ROOT / "stock_verify.db"))
    ap.add_argument("--golden-dir", default=str(ROOT / "data" / "xbrl_golden"))
    ap.add_argument(
        "--tolerance", type=int, default=1,
        help="abs million_jpy after JPY→million round (default 1)",
    )
    ap.add_argument("--codes", nargs="*", default=None, help="optional 5-digit filter")
    args = ap.parse_args()
    codes = {c.strip() for item in (args.codes or []) for c in item.split(",") if c.strip()} or None
    golden_dir = Path(args.golden_dir)
    if not golden_dir.is_dir():
        print(f"golden dir not found: {golden_dir}", file=sys.stderr)
        return 1
    goldens = load_golden(golden_dir, codes)
    con = open_db(Path(args.db))
    try:
        matches = mismatches = 0
        per: dict[str, list[int]] = {m: [0, 0] for m in METRICS}
        miss_rows: list[str] = []
        mm_rows: list[tuple] = []
        by_code: dict[str, list[sqlite3.Row]] = {}
        for g in goldens:
            code = str(g.get("code") or "")
            want = parse_ym(g.get("fiscal_year"))
            if want is None:
                raise SystemExit(f"schema error: bad fiscal_year in {g.get('_file')}")
            q = g.get("quarter")
            if str(g.get("period_type") or "").lower() == "quarterly":
                q = (q or "").upper() or None
            else:
                q = None
            if code not in by_code:
                by_code[code] = fetch_rows(con, code)
            hit = [r for r in by_code[code] if period_ok(r, want, q)]
            metrics = g.get("metrics") or {}
            fy = g.get("fiscal_year")
            qlabel = q or "FY"
            for metric in METRICS:
                gv = metrics.get(metric)
                if gv is None:
                    continue
                dbv = pick_db_value(hit, metric)
                if dbv is None:
                    miss_rows.append(f"{code} {fy} {qlabel} {metric} (db missing)")
                    continue
                db_m, _unit = dbv
                golden_m = int(round(float(gv)))
                diff = db_m - golden_m
                if abs(diff) <= args.tolerance:
                    matches += 1
                    per[metric][0] += 1
                else:
                    mismatches += 1
                    per[metric][1] += 1
                    mm_rows.append((code, fy, qlabel, metric, db_m, golden_m, diff))
    finally:
        con.close()

    denom = matches + mismatches
    print(f"overall match_rate: {fmt_rate(matches, denom)}")
    print(f"tolerance: {args.tolerance} million_jpy  golden files: {len(goldens)}")
    print("\nper-metric:")
    for metric in METRICS:
        m, mm = per[metric]
        print(f"  {metric:20s} {fmt_rate(m, m + mm)}")
    print(f"\nmismatches ({len(mm_rows)}):")
    if mm_rows:
        print(
            f"  {'code':6s} {'fiscal_year':12s} {'q':4s} {'metric':20s} "
            f"{'db':>12s} {'golden':>12s} {'diff':>12s}"
        )
        for row in mm_rows:
            print(
                f"  {row[0]:6s} {str(row[1]):12s} {row[2]:4s} {row[3]:20s} "
                f"{row[4]:12d} {row[5]:12d} {row[6]:12d}"
            )
    else:
        print("  (none)")
    print(f"\nmissing (excluded from denominator): {len(miss_rows)}")
    for line in miss_rows:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

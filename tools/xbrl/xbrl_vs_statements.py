#!/usr/bin/env python3
"""Compare J-Quants `statements` with normalized `xbrl_financials`.

No network. sqlite3 + stdlib. Does not write the DB (read-only).

  python tools/xbrl/xbrl_vs_statements.py --db PATH [--out PATH] [--code 72030]

Join is code + disclosed_date == date(submitted_at), then period_end.
Prefer consolidated, point forecasts (not previous / not range).

Scale: both sides are yen (or yen/share). Trust unit; yen-like units are
compared as-is. Per-share items use a slightly wider absolute tolerance.

Missing XBRL for a populated statements field is `missing` (excluded from
the match-rate denominator), same idea as xbrl_match_rate.py.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ABS_MONEY = Decimal("1")
ABS_PER_SHARE = Decimal("0.05")
REL_TOL = Decimal("0.001")
EPS = Decimal("1e-9")

YEN_UNITS = {
    "",
    "jpy",
    "yen",
    "円",
    "iso4217:jpy",
    "iso4217/jpy",
}
PER_SHARE_HINTS = ("share", "pershare", "per_share", "jpy/shares", "eps")

# statements column / raw_json key → (std_item, value_kind, target_period, per_share)
PAIRS: list[tuple[str, str, str, str, bool]] = [
    ("net_sales", "net_sales", "actual", "period", False),
    ("operating_profit", "operating_income", "actual", "period", False),
    ("ordinary_profit", "ordinary_income", "actual", "period", False),
    ("profit", "net_income", "actual", "period", False),
    ("eps", "eps_basic", "actual", "period", True),
    ("fy_net_sales", "net_sales", "forecast", "FY", False),
    ("fy_operating_profit", "operating_income", "forecast", "FY", False),
    ("fy_ordinary_profit", "ordinary_income", "forecast", "FY", False),
    ("fy_profit", "net_income", "forecast", "FY", False),
    ("fy_eps", "eps_basic", "forecast", "FY", True),
    ("f2_net_sales", "net_sales", "forecast", "H1", False),
    ("f2_operating_profit", "operating_income", "forecast", "H1", False),
    ("f2_ordinary_profit", "ordinary_income", "forecast", "H1", False),
    ("f2_profit", "net_income", "forecast", "H1", False),
    ("f2_eps", "eps_basic", "forecast", "H1", True),
]
RAW_PAIRS: list[tuple[str, str, str, str, bool]] = [
    ("DEPS", "eps_diluted", "actual", "period", True),
    ("Div1Q", "dps", "actual", "Q1", True),
    ("Div2Q", "dps", "actual", "Q2", True),
    ("Div3Q", "dps", "actual", "Q3", True),
    ("DivFY", "dps", "actual", "year_end", True),
    ("DivAnn", "dps", "actual", "annual", True),
    ("FDiv1Q", "dps", "forecast", "Q1", True),
    ("FDiv2Q", "dps", "forecast", "Q2", True),
    ("FDiv3Q", "dps", "forecast", "Q3", True),
    ("FDivFY", "dps", "forecast", "year_end", True),
    ("FDivAnn", "dps", "forecast", "annual", True),
]


def _dec(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def norm_date(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def period_token(raw: Any) -> str:
    s = str(raw or "").upper()
    if s in {"1Q", "Q1"}:
        return "Q1"
    if s in {"2Q", "Q2"}:
        return "Q2"
    if s in {"3Q", "Q3"}:
        return "Q3"
    if s in {"FY", "4Q", "Q4"}:
        return "FY"
    return s or "FY"


def values_match(a: Decimal, b: Decimal, *, per_share: bool) -> bool:
    diff = abs(a - b)
    if per_share and diff <= ABS_PER_SHARE:
        return True
    if not per_share and diff <= ABS_MONEY:
        return True
    denom = max(abs(a), abs(b), EPS)
    return (diff / denom) <= REL_TOL


@dataclass
class CmpRow:
    code: str
    disclosure_no: str
    field: str
    std_item: str
    value_kind: str
    target_period: str
    stmt: str | None
    xbrl: str | None
    status: str
    reason: str = ""


@dataclass
class Report:
    matched: int = 0
    compared: int = 0
    missing: int = 0
    mismatches: int = 0
    skipped: int = 0
    rows: list[CmpRow] = field(default_factory=list)
    per_field: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def rate(self) -> float | None:
        if self.compared == 0:
            return None
        return self.matched / self.compared


def _as_bool(raw: Any) -> bool:
    return str(raw).strip() in {"1", "True", "true"}


def load_statements(conn: sqlite3.Connection, code: str | None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM statements"
    args: tuple = ()
    if code:
        sql += " WHERE code = ?"
        args = (code,)
    rows = []
    for rec in conn.execute(sql, args):
        item = dict(rec)
        raw = item.get("raw_json")
        if isinstance(raw, str) and raw:
            try:
                item["_raw"] = json.loads(raw)
            except json.JSONDecodeError:
                item["_raw"] = {}
        else:
            item["_raw"] = {}
        rows.append(item)
    return rows


def load_financials(conn: sqlite3.Connection, code: str | None) -> list[dict[str, Any]]:
    fin_cols = {r[1] for r in conn.execute("PRAGMA table_info(xbrl_financials)")}
    kind_sql = "COALESCE(f.value_kind, 'actual')" if "value_kind" in fin_cols else "'actual'"
    range_sql = "COALESCE(f.range_side, 'point')" if "range_side" in fin_cols else "'point'"
    target_sql = "COALESCE(f.target_period, f.period_type, 'FY')" if "target_period" in fin_cols else "COALESCE(f.period_type, 'FY')"
    sql = f"""
        SELECT
          f.code AS code,
          f.filing_id AS filing_id,
          f.is_consolidated AS is_consolidated,
          f.period_end AS period_end,
          f.value AS value,
          f.unit AS unit,
          {kind_sql} AS value_kind,
          {range_sql} AS range_side,
          {target_sql} AS target_period,
          s.code AS std_item,
          substr(fl.submitted_at, 1, 10) AS submitted_date
        FROM xbrl_financials f
        JOIN xbrl_std_items s ON s.id = f.std_item_id
        LEFT JOIN xbrl_filings fl ON fl.id = f.filing_id
    """
    args: tuple = ()
    if code:
        sql += " WHERE f.code = ?"
        args = (code,)
    out = []
    for rec in conn.execute(sql, args):
        if str(rec["range_side"]) not in {"point", ""}:
            continue
        if str(rec["value_kind"]) == "forecast_previous":
            continue
        out.append(dict(rec))
    return out


def _pick(
    rows: list[dict[str, Any]],
    *,
    std_item: str,
    value_kind: str,
    target_period: str,
    period_end: str | None,
) -> dict[str, Any] | None:
    cands = [
        r for r in rows
        if r["std_item"] == std_item
        and r["value_kind"] == value_kind
        and str(r["target_period"]) == target_period
    ]
    if period_end and value_kind == "actual" and target_period in {"Q1", "Q2", "Q3", "FY", "period"}:
        dated = [r for r in cands if norm_date(r["period_end"]) == period_end]
        if dated:
            cands = dated
    if not cands:
        return None
    cands.sort(key=lambda r: (0 if _as_bool(r["is_consolidated"]) else 1, -(r["filing_id"] or 0)))
    return cands[0]


def compare_db(db_path: Path, *, code: str | None = None) -> Report:
    if not db_path.is_file():
        raise SystemExit(f"db is not a file: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for need in ("statements", "xbrl_financials", "xbrl_std_items"):
            if need not in tables:
                raise SystemExit(f"db missing {need}")
        stmts = load_statements(conn, code)
        fins = load_financials(conn, code)
    finally:
        conn.close()

    by_code_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fins:
        by_code[str(row["code"])].append(row)
        day = norm_date(row.get("submitted_date"))
        if day:
            by_code_date[(str(row["code"]), day)].append(row)

    report = Report()
    for st in stmts:
        code5 = str(st.get("code") or "")
        disc = str(st.get("disclosure_no") or "")
        day = norm_date(st.get("disclosed_date"))
        rows = by_code_date.get((code5, day)) or by_code.get(code5) or []
        period_end = norm_date(st.get("period_end"))
        filing_period = period_token(st.get("type_of_period"))
        pairs = list(PAIRS)
        raw = st.get("_raw") or {}
        for src, std, kind, target, per_share in RAW_PAIRS:
            pairs.append((src, std, kind, target, per_share))
        for src, std, kind, target, per_share in pairs:
            if src in st and src not in {"DEPS", "Div1Q", "Div2Q", "Div3Q", "DivFY", "DivAnn",
                                         "FDiv1Q", "FDiv2Q", "FDiv3Q", "FDivFY", "FDivAnn"}:
                stmt_val = _dec(st.get(src))
            else:
                stmt_val = _dec(raw.get(src))
            field = src
            want_target = filing_period if target == "period" else target
            stats = report.per_field.setdefault(field, {"matched": 0, "compared": 0, "missing": 0, "mismatch": 0})
            if stmt_val is None:
                report.skipped += 1
                continue
            hit = _pick(
                rows,
                std_item=std,
                value_kind=kind,
                target_period=want_target,
                period_end=period_end if kind == "actual" and target == "period" else None,
            )
            if hit is None:
                report.missing += 1
                stats["missing"] += 1
                report.rows.append(CmpRow(
                    code=code5, disclosure_no=disc, field=field, std_item=std,
                    value_kind=kind, target_period=want_target,
                    stmt=str(stmt_val), xbrl=None, status="missing",
                    reason="xbrl row not found",
                ))
                continue
            xval = _dec(hit["value"])
            if xval is None:
                report.missing += 1
                stats["missing"] += 1
                report.rows.append(CmpRow(
                    code=code5, disclosure_no=disc, field=field, std_item=std,
                    value_kind=kind, target_period=want_target,
                    stmt=str(stmt_val), xbrl=None, status="missing",
                    reason="xbrl value null",
                ))
                continue
            report.compared += 1
            stats["compared"] += 1
            ok = values_match(stmt_val, xval, per_share=per_share)
            if ok:
                report.matched += 1
                stats["matched"] += 1
                status = "match"
                reason = ""
            else:
                report.mismatches += 1
                stats["mismatch"] += 1
                status = "mismatch"
                reason = f"stmt={stmt_val} xbrl={xval}"
            report.rows.append(CmpRow(
                code=code5, disclosure_no=disc, field=field, std_item=std,
                value_kind=kind, target_period=want_target,
                stmt=str(stmt_val), xbrl=str(xval), status=status, reason=reason,
            ))
    return report


def format_stdout(report: Report) -> str:
    pct = "n/a" if report.rate is None else f"{report.rate * 100:.2f}%"
    parts = [
        (
            f"overall: {report.matched}/{report.compared} = {pct}  "
            f"(missing_excluded={report.missing}, mismatches={report.mismatches}, "
            f"skipped_empty={report.skipped})"
        ),
    ]
    fields = []
    for name, st in sorted(report.per_field.items()):
        if st["compared"] or st["missing"]:
            fields.append(f"{name}={st['matched']}/{st['compared']}")
    if fields:
        parts.append("per_field: " + " ".join(fields))
    reasons: dict[str, int] = defaultdict(int)
    for row in report.rows:
        if row.status == "mismatch":
            reasons[row.field] += 1
    if reasons:
        top = ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:8])
        parts.append("mismatch_fields: " + top)
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="sqlite with statements + xbrl_financials")
    ap.add_argument("--out", help="optional JSON report path")
    ap.add_argument("--code", help="limit to one 5-digit code")
    args = ap.parse_args(argv)
    report = compare_db(Path(args.db), code=args.code)
    print(format_stdout(report))
    if args.out:
        payload = {
            "overall": {
                "matched": report.matched,
                "compared": report.compared,
                "missing": report.missing,
                "mismatches": report.mismatches,
                "skipped": report.skipped,
                "rate": report.rate,
            },
            "per_field": report.per_field,
            "rows": [asdict(r) for r in report.rows],
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

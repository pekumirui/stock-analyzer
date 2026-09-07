#!/usr/bin/env python3
"""Offline IRBANK golden vs xbrl_financials match rate.

No network. sqlite3 + stdlib only (do not add pdfplumber).

  python tools/xbrl_match_rate.py --db PATH --golden-dir PATH [--out PATH]

Scale (see docs/XBRL_VERIFY.md):
  - Golden metrics are million JPY.
  - xbrl_financials.value with unit JPY / yen / empty / null / iso4217:JPY
    is yen → divide by 1e6 before compare.
  - Unit already million (million_jpy, JPY_MILLIONS, or 'million' in unit):
    do not divide again. Trust unit; no magnitude heuristic.

Tolerance: match if abs(diff) ≤ 1 (million JPY) OR relative ≤ 0.1%
  (|a-b| / max(|a|, |b|, eps) ≤ 0.001). Either passing = match.

Missing: golden has a metric, XBRL has no row → exclude from denominator
  (missing / excluded, not a mismatch).

Prefer consolidated when both consol and non-consol exist for the same
code × period_end × std_item.

Period basis (KPI): compare **cumulative YTD ↔ cumulative YTD only**, same as
`statements`. Goldens must have `period_basis: "cumulative"`. Single-quarter /
standalone / missing `period_basis` rows are **skipped** (reason in report;
batch continues) — never raw-compared to flash cumulative PL.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

METRIC_TO_STD: dict[str, str] = {
    "revenue": "net_sales",
    "operating_income": "operating_income",
    "ordinary_income": "ordinary_income",
    "net_income": "net_income",
    "total_assets": "total_assets",
    "equity": "net_assets",
    "total_liabilities": "total_liabilities",
    "operating_cf": "operating_cf",
    "investing_cf": "investing_cf",
}

ABS_TOL = Decimal("1")
REL_TOL = Decimal("0.001")
EPS = Decimal("1e-9")

PERIOD_BASIS_CUMULATIVE = "cumulative"
PERIOD_BASIS_SINGLE_ALIASES = {
    "single_quarter",
    "standalone",
    "single",
    "quarter_only",
    "単四半期",
    "単独四半期",
}

YEN_UNITS = {
    "",
    "jpy",
    "yen",
    "円",
    "iso4217:jpy",
    "iso4217/jpy",
}
MILLION_HINTS = ("million", "jpy_millions", "百万円", "millions")


@dataclass
class CmpRow:
    code: str
    period_end: str
    metric: str
    std_item: str
    golden: float | None
    xbrl: float | None
    status: str  # match | mismatch | missing
    diff: float | None = None


@dataclass
class MetricStats:
    matched: int = 0
    compared: int = 0
    missing: int = 0
    mismatches: int = 0

    @property
    def rate(self) -> float | None:
        if self.compared == 0:
            return None
        return self.matched / self.compared


@dataclass
class SkipRow:
    code: str
    period_end: str
    reason: str
    period_basis: str | None = None


@dataclass
class Report:
    matched: int = 0
    compared: int = 0
    missing: int = 0
    mismatches: int = 0
    skipped: int = 0
    per_metric: dict[str, MetricStats] = field(default_factory=dict)
    rows: list[CmpRow] = field(default_factory=list)
    skipped_rows: list[SkipRow] = field(default_factory=list)

    @property
    def rate(self) -> float | None:
        if self.compared == 0:
            return None
        return self.matched / self.compared


def _as_bool(v: Any) -> bool:
    if v is True or v == 1:
        return True
    if v is False or v == 0 or v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "t", "yes"}


def _dec(v: Any) -> Decimal:
    return Decimal(str(v))


def unit_is_million(unit: str | None) -> bool:
    u = (unit or "").strip().lower().replace(" ", "_")
    if not u:
        return False
    if u in {"million_jpy", "jpy_millions", "m_jpy", "jpy_million"}:
        return True
    return any(h in u for h in MILLION_HINTS)


def unit_is_yen(unit: str | None) -> bool:
    u = (unit or "").strip().lower().replace(" ", "")
    if u in YEN_UNITS:
        return True
    if u.endswith(":jpy") or u.endswith("/jpy"):
        return True
    return False


def to_million_jpy(value: Any, unit: str | None) -> Decimal:
    """Convert an xbrl_financials value to million JPY using unit only."""
    v = _dec(value)
    if unit_is_million(unit):
        return v
    if unit_is_yen(unit) or unit is None or str(unit).strip() == "":
        return v / Decimal("1000000")
    return v / Decimal("1000000")


def values_match(a: Decimal, b: Decimal) -> bool:
    diff = abs(a - b)
    if diff <= ABS_TOL:
        return True
    denom = max(abs(a), abs(b), EPS)
    return (diff / denom) <= REL_TOL


def norm_date(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if "T" in s:
        s = s.split("T", 1)[0]
    if " " in s:
        s = s.split(" ", 1)[0]
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def load_goldens(golden_dir: Path) -> list[dict[str, Any]]:
    if not golden_dir.is_dir():
        raise SystemExit(f"golden-dir is not a directory: {golden_dir}")
    rows: list[dict[str, Any]] = []
    for path in sorted(golden_dir.rglob("*.json")):
        if path.name.startswith("schema") or path.name == "schema.example.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)
            continue
        if isinstance(data, list):
            rows.extend(
                x for x in data
                if isinstance(x, dict) and ("metrics" in x or "code" in x)
            )
        elif isinstance(data, dict) and ("metrics" in data or "code" in data):
            rows.append(data)
    return rows


def load_xbrl_values(db_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Map (code, period_end, std_item) → chosen financial row."""
    if not db_path.is_file():
        raise SystemExit(f"db is not a file: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "xbrl_financials" not in tables or "xbrl_std_items" not in tables:
            raise SystemExit("db missing xbrl_financials or xbrl_std_items")
        sql = """
            SELECT
              f.code AS code,
              f.period_end AS period_end,
              f.is_consolidated AS is_consolidated,
              f.value AS value,
              f.unit AS unit,
              f.filing_id AS filing_id,
              s.code AS std_item
            FROM xbrl_financials f
            JOIN xbrl_std_items s ON s.id = f.std_item_id
        """
        chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in conn.execute(sql):
            rec = {
                "code": str(row["code"]),
                "period_end": norm_date(row["period_end"]),
                "is_consolidated": _as_bool(row["is_consolidated"]),
                "value": row["value"],
                "unit": row["unit"],
                "filing_id": row["filing_id"] or 0,
                "std_item": str(row["std_item"]),
            }
            key = (rec["code"], rec["period_end"], rec["std_item"])
            prev = chosen.get(key)
            if prev is None:
                chosen[key] = rec
                continue
            if rec["is_consolidated"] and not prev["is_consolidated"]:
                chosen[key] = rec
            elif rec["is_consolidated"] == prev["is_consolidated"] and rec["filing_id"] >= prev["filing_id"]:
                chosen[key] = rec
        return chosen
    finally:
        conn.close()


def _metric_num(metrics: dict[str, Any], key: str) -> Decimal | None:
    if key not in metrics:
        return None
    raw = metrics[key]
    if raw is None:
        return None
    try:
        return _dec(raw)
    except Exception:
        return None


def golden_period_basis(g: dict[str, Any]) -> str | None:
    """Normalize golden period_basis; None if missing/blank."""
    raw = g.get("period_basis")
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower().replace(" ", "_")
    if low in PERIOD_BASIS_SINGLE_ALIASES or s in PERIOD_BASIS_SINGLE_ALIASES:
        return "single_quarter"
    if low in {PERIOD_BASIS_CUMULATIVE, "ytd", "current_ytd", "累計"}:
        return PERIOD_BASIS_CUMULATIVE
    return low


def compare(goldens: list[dict[str, Any]], xbrl: dict[tuple[str, str, str], dict[str, Any]]) -> Report:
    report = Report()
    for std in METRIC_TO_STD.values():
        report.per_metric[std] = MetricStats()
    for g in goldens:
        code = str(g.get("code") or "")
        if not code and g.get("code4"):
            code = str(g["code4"]) + "0"
        period_end = norm_date(g.get("period_end"))
        basis = golden_period_basis(g)
        if basis != PERIOD_BASIS_CUMULATIVE:
            if basis is None:
                reason = "missing_period_basis"
            elif basis == "single_quarter":
                reason = "single_quarter_not_comparable"
            else:
                reason = f"unsupported_period_basis:{basis}"
            report.skipped += 1
            report.skipped_rows.append(
                SkipRow(
                    code=code,
                    period_end=period_end,
                    reason=reason,
                    period_basis=basis,
                )
            )
            continue
        metrics = g.get("metrics") if isinstance(g.get("metrics"), dict) else {}
        for metric, std in METRIC_TO_STD.items():
            golden_v = _metric_num(metrics, metric)
            if golden_v is None:
                continue
            stats = report.per_metric[std]
            row = xbrl.get((code, period_end, std))
            if row is None:
                stats.missing += 1
                report.missing += 1
                report.rows.append(
                    CmpRow(
                        code=code,
                        period_end=period_end,
                        metric=metric,
                        std_item=std,
                        golden=float(golden_v),
                        xbrl=None,
                        status="missing",
                    )
                )
                continue
            xbrl_v = to_million_jpy(row["value"], row.get("unit"))
            ok = values_match(golden_v, xbrl_v)
            diff = float(xbrl_v - golden_v)
            stats.compared += 1
            report.compared += 1
            status = "match" if ok else "mismatch"
            if ok:
                stats.matched += 1
                report.matched += 1
            else:
                stats.mismatches += 1
                report.mismatches += 1
            report.rows.append(
                CmpRow(
                    code=code,
                    period_end=period_end,
                    metric=metric,
                    std_item=std,
                    golden=float(golden_v),
                    xbrl=float(xbrl_v),
                    status=status,
                    diff=diff,
                )
            )
    return report


def _pct(matched: int, compared: int) -> str:
    if compared == 0:
        return "n/a"
    return f"{100.0 * matched / compared:.2f}%"


def format_summary(report: Report) -> str:
    overall = (
        f"overall: {report.matched}/{report.compared} = {_pct(report.matched, report.compared)}"
        f"  (missing_excluded={report.missing}, skipped_non_cumulative={report.skipped})"
    )
    parts = []
    for std in METRIC_TO_STD.values():
        st = report.per_metric.get(std) or MetricStats()
        parts.append(f"{std}={st.matched}/{st.compared}")
    lines = [overall, "per_metric: " + " ".join(parts)]
    bad = [r for r in report.rows if r.status == "mismatch"]
    miss = [r for r in report.rows if r.status == "missing"]
    if bad:
        lines.append("mismatches:")
        for r in bad:
            lines.append(
                f"  {r.code} {r.period_end} {r.std_item} golden={r.golden} xbrl={r.xbrl} diff={r.diff}"
            )
    if miss:
        lines.append("missing_excluded:")
        for r in miss:
            lines.append(f"  {r.code} {r.period_end} {r.std_item}")
    if report.skipped_rows:
        lines.append("skipped_non_cumulative:")
        for r in report.skipped_rows:
            lines.append(
                f"  {r.code} {r.period_end} reason={r.reason} period_basis={r.period_basis}"
            )
    return "\n".join(lines)


def report_to_json(report: Report) -> dict[str, Any]:
    per = {}
    for std, st in report.per_metric.items():
        per[std] = {
            "matched": st.matched,
            "compared": st.compared,
            "missing": st.missing,
            "mismatches": st.mismatches,
            "rate": st.rate,
        }
    return {
        "overall": {
            "matched": report.matched,
            "compared": report.compared,
            "missing": report.missing,
            "mismatches": report.mismatches,
            "skipped": report.skipped,
            "rate": report.rate,
        },
        "per_metric": per,
        "rows": [asdict(r) for r in report.rows],
        "skipped_rows": [asdict(r) for r in report.skipped_rows],
    }


def write_out(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(
                fh,
                fieldnames=["code", "period_end", "metric", "std_item", "golden", "xbrl", "diff", "status"],
            )
            w.writeheader()
            for r in report.rows:
                w.writerow(asdict(r))
        return
    path.write_text(
        json.dumps(report_to_json(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(db: Path, golden_dir: Path) -> Report:
    goldens = load_goldens(golden_dir)
    if not goldens:
        raise SystemExit(f"no golden JSON under {golden_dir}")
    xbrl = load_xbrl_values(db)
    return compare(goldens, xbrl)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="sqlite path (xbrl_financials)")
    ap.add_argument("--golden-dir", required=True, help="directory of golden JSON")
    ap.add_argument("--out", help="optional JSON or CSV (by extension)")
    args = ap.parse_args(argv)
    report = run(Path(args.db), Path(args.golden_dir))
    print(format_summary(report))
    if args.out:
        write_out(report, Path(args.out))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

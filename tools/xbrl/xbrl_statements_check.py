#!/usr/bin/env python3
"""XBRL から作る statements の行（app/xbrl/statements.py）を J-Quants の行と列ごとに比べる。読み取り専用。

  python tools/xbrl/xbrl_statements_check.py --db stock.db --since 2026-10-01 [--limit 30]

J-Quants の行（source が xbrl でないもの）と開示番号で突き合わせる。金額は 1e6 未満、
1 株あたりは 0.05 未満の差を一致とする（J-Quants は百万円単位）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.xbrl.statements import (  # noqa: E402
    FINANCIAL_DOC_TYPES,
    ITEM_CODES,
    NUMERIC_COLUMNS,
    REVISION_DOC_TYPES,
    SOURCE_XBRL,
    statement_row,
)

META = ("type_of_period", "type_of_document", "period_start", "period_end", "fiscal_year_start",
        "fiscal_year_end", "disclosed_date", "disclosed_time", "is_revision")
RAW_KEYS = ("FSales", "FOP", "FOdP", "FNP", "FEPS", "FNCSales", "FNCOP", "FNCNP", "FNCSales2Q", "NxtFYEn", "NxFSales", "NxFOP", "NxFOdP", "NxFNp", "NxFEPS",
            "NxFSales2Q", "NxFOP2Q", "NxFNp2Q", "NxFEPS2Q", "Div2Q", "DivFY", "DivAnn", "FDivFY", "FDivAnn",
            "NxFDivAnn", "BPS", "EqAR", "ROE")
PER_SHARE = {"eps", "f2_eps", "fy_eps", "FEPS", "NxFEPS", "NxFEPS2Q", "Div2Q", "DivFY", "DivAnn",
             "FDivFY", "FDivAnn", "NxFDivAnn", "BPS", "EqAR", "ROE"}


def _d(v):
    return date.fromisoformat(str(v)[:10]) if v else None


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def same(key: str, a, b) -> bool:
    if key == "is_revision":
        return bool(a) == bool(b)
    if key in META or key == "NxtFYEn":
        return str(a or "")[:10] == str(b or "")[:10] if key != "disclosed_time" else (a or "") == (b or "")
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return x is None and y is None
    tol = 0.05 if key in PER_SHARE else 1e6
    return abs(x - y) < tol


def load(conn: sqlite3.Connection, since: date):
    kinds = [*FINANCIAL_DOC_TYPES, *REVISION_DOC_TYPES]
    filings = [SimpleNamespace(**dict(r)) for r in conn.execute(
        f"SELECT id, code, doc_id, doc_type, period_type, fiscal_year_end, submitted_at, title, accounting_standard "
        f"FROM xbrl_filings WHERE source='tdnet' AND parse_status='parsed' AND submitted_at >= ? "
        f"AND doc_type IN ({','.join('?' * len(kinds))})", [since.isoformat(), *kinds])]
    for f in filings:
        f.submitted_at = datetime.fromisoformat(str(f.submitted_at)[:19])
        f.fiscal_year_end = _d(f.fiscal_year_end)
    rows = defaultdict(list)
    ids = [f.id for f in filings]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"SELECT xf.filing_id, i.code, xf.value_kind, xf.target_period, xf.period_start, xf.period_end, "
                f"xf.is_consolidated, xf.value FROM xbrl_financials xf JOIN xbrl_std_items i ON i.id = xf.std_item_id "
                f"WHERE xf.filing_id IN ({','.join('?' * len(chunk))}) AND xf.range_side='point' "
                f"AND xf.value_kind IN ('actual','forecast') AND i.code IN ({','.join('?' * len(ITEM_CODES))}) "
                f"ORDER BY xf.filing_id, xf.id", [*chunk, *ITEM_CODES]):
            rows[r["filing_id"]].append(SimpleNamespace(
                code=r["code"], value_kind=r["value_kind"], target_period=r["target_period"],
                period_start=_d(r["period_start"]), period_end=_d(r["period_end"]),
                is_consolidated=r["is_consolidated"], value=r["value"]))
    return filings, rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--since", type=date.fromisoformat, required=True)
    ap.add_argument("--limit", type=int, default=5, help="ずれの例を項目ごとに何件まで出すか")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    filings, rows = load(conn, args.since)
    built = 0
    paired = 0
    no_jq = 0
    ok = Counter()
    bad = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    for f in filings:
        standalone = False
        if f.doc_type in REVISION_DOC_TYPES:
            doc = conn.execute(
                "SELECT type_of_document FROM statements WHERE code = ? AND disclosed_date <= ? "
                "AND type_of_document LIKE '%FinancialStatements%' "
                "ORDER BY disclosed_date DESC, disclosure_no DESC LIMIT 1",
                [f.code, f.submitted_at.date().isoformat()]).fetchone()
            standalone = doc is not None and "NonConsolidated" in (doc[0] or "")
        row = statement_row(f, rows.get(f.id, []), standalone_company=standalone)
        if row is None:
            continue
        built += 1
        jq = conn.execute("SELECT * FROM statements WHERE disclosure_no = ?", [row["disclosure_no"]]).fetchone()
        if jq is None or dict(jq).get("source") == SOURCE_XBRL:
            no_jq += 1
            continue
        paired += 1
        mine = json.loads(row["raw_json"])
        theirs = json.loads(jq["raw_json"] or "{}")
        pairs = [(k, row[k], jq[k]) for k in (*NUMERIC_COLUMNS, *META)]
        pairs += [("raw." + k, mine.get(k), theirs.get(k)) for k in RAW_KEYS if k in mine]
        for key, a, b in pairs:
            if same(key.removeprefix("raw."), a, b):
                ok[key] += 1
            else:
                bad[key] += 1
                if len(samples[key]) < args.limit:
                    samples[key].append(
                        f"  {row['code']} {row['disclosure_no']} {row['type_of_document']} {f.doc_type} {key}: xbrl={a} jquants={b}")
    print(f"xbrl_statements_check since={args.since} filings={len(filings)} built={built} paired={paired} no_jquants={no_jq}")
    for key in sorted(set(ok) | set(bad), key=lambda k: (-bad[k], k)):
        total = ok[key] + bad[key]
        print(f"  {key} ok={ok[key]} diff={bad[key]} match={ok[key] / total:.1%}")
    print("samples")
    for key in sorted(samples, key=lambda k: (-bad[k], k)):
        print("\n".join(samples[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

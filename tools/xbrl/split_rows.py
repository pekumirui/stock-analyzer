#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db import sqlite_readonly_engine
from app.xbrl.three_statements import ACCUMULATED_NAMES, NET_TAIL, NEVER_MERGED_LABELS, STATEMENTS, financial_table

COLUMNS = ("kind", "code", "basis", "statement", "which", "label", "heads", "keys", "spans", "overlap_in_group")
GENERIC = "generic"
ACCUMULATED_SAME_GROSS = "accumulated_same_gross"
ACCUMULATED_OTHER_GROSS = "accumulated_other_gross"
PDF = "pdf"
ROW = "row"
NET_ABOVE_ACCUMULATED = "net_above_accumulated"


def _filled(row: dict) -> frozenset[int]:
    return frozenset(i for i, cell in enumerate(row.get("cells") or []) if cell is not None and cell.get("value") is not None)


def _heads(row: dict) -> tuple[str, ...]:
    return tuple(head["name"] for head in row.get("heads") or [])


def _accumulated(label: str) -> bool:
    return label.removesuffix(NET_TAIL).startswith(tuple(ACCUMULATED_NAMES))


def _gross_rows(rows: list[dict]) -> list[str | None]:
    out: list[str | None] = []
    gross: str | None = None
    for row in rows:
        out.append(gross)
        if not _accumulated(row["label"]):
            gross = row["key"]
    return out


def _kind(label: str, keys: list[str], grosses: tuple[str | None, str | None]) -> str:
    if label.removesuffix(NET_TAIL) in NEVER_MERGED_LABELS:
        return GENERIC
    if _accumulated(label):
        same = grosses[0] is not None and grosses[0] == grosses[1]
        return ACCUMULATED_SAME_GROSS if same else ACCUMULATED_OTHER_GROSS
    if any(k.startswith("pdf:") for k in keys):
        return PDF
    return ROW


def _span(labels: list[str], filled: frozenset[int]) -> str:
    at = sorted(filled)
    return f"{labels[at[0]]}..{labels[at[-1]]}:{len(at)}"


def split_candidates(view: dict) -> list[dict]:
    rows = view.get("rows") or []
    labels = [p["label"] for p in view.get("periods") or []]
    filled = [_filled(r) for r in rows]
    grosses = _gross_rows(rows)
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        if filled[i]:
            groups[(row["label"], _heads(row), row.get("unit"))].append(i)
    out: list[dict] = []
    for (label, heads, _), members in groups.items():
        if len(members) < 2:
            continue
        overlap = any(filled[a] & filled[b] for a, b in combinations(members, 2))
        for a, b in combinations(members, 2):
            if filled[a] & filled[b]:
                continue
            keys = [rows[a]["key"], rows[b]["key"]]
            out.append({
                "kind": _kind(label, keys, (grosses[a], grosses[b])), "label": label, "heads": " > ".join(heads), "keys": ",".join(keys),
                "spans": ",".join(_span(labels, filled[i]) for i in (a, b)), "overlap_in_group": int(overlap),
            })
    for above, below in zip(rows, rows[1:]):
        if above["label"].endswith(NET_TAIL) and _accumulated(below["label"]):
            out.append({
                "kind": NET_ABOVE_ACCUMULATED, "label": above["label"], "heads": " > ".join(_heads(above)),
                "keys": ",".join((above["key"], below["key"])),
                "spans": ",".join(_span(labels, _filled(r)) if _filled(r) else "-" for r in (above, below)),
                "overlap_in_group": 0,
            })
    return out


def _codes(session) -> list[str]:
    db = session()
    try:
        return [row[0] for row in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status = 'parsed' "
            "AND source IN ('edinet', 'tdnet', 'tdnet_pdf') "
            "AND doc_type IN ('annual', 'semiannual', 'quarterly') "
            "AND fiscal_year_end IS NOT NULL ORDER BY code"
        ))]
    finally:
        db.close()


def scan(db_path: str, out_path: str, periods: int, statements: list[str], codes: list[str] | None) -> int:
    engine = sqlite_readonly_engine(db_path)
    session = sessionmaker(bind=engine, autoflush=False, future=True)
    targets = codes or _codes(session)
    found = errors = 0
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        out = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter="\t", extrasaction="ignore")
        out.writeheader()
        for n, code in enumerate(targets, 1):
            if n % 200 == 0:
                print(f"{n}/{len(targets)} found={found} errors={errors}", file=sys.stderr, flush=True)
            for basis, consolidated in (("auto", None), ("separate", False)):
                for statement in statements:
                    db = session()
                    try:
                        table = financial_table(db, code, statement=statement, periods=periods, consolidated=consolidated)
                    except Exception as exc:
                        errors += 1
                        out.writerow({"kind": "error", "code": code, "basis": basis, "statement": statement,
                                      "label": f"{type(exc).__name__}: {exc}"})
                        continue
                    finally:
                        db.close()
                    for which, view in enumerate([table, *(table.get("earlier") or [])]):
                        if view.get("empty"):
                            continue
                        for item in split_candidates(view):
                            out.writerow({"code": code, "basis": basis, "statement": statement, "which": which, **item})
                            found += 1
            fh.flush()
    engine.dispose()
    print(f"done {len(targets)} codes found={found} errors={errors} {out_path}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--periods", type=int, default=20)
    parser.add_argument("--statements", default=",".join(STATEMENTS))
    parser.add_argument("codes", nargs="*")
    args = parser.parse_args(argv)
    return scan(args.db, args.out, args.periods, args.statements.split(","), args.codes or None)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db import sqlite_readonly_engine
from app.xbrl.three_statements import STATEMENTS, financial_table

UCHIWAKE = "（内訳）"


def _pair(head) -> tuple[str, str]:
    if isinstance(head, dict):
        return str(head["key"]), str(head["name"])
    return str(head[0]), str(head[1])


def emitted_heading_names(rows) -> list[str]:
    at: list[tuple[str, str]] = []
    names: list[str] = []
    for row in rows:
        heads = [_pair(head) for head in row["heads"]]
        same = 0
        while same < len(heads) and same < len(at) and heads[same] == at[same]:
            same += 1
        names.extend(name for _, name in heads[same:])
        at = heads
    return names


def adjacent_same_headings(rows) -> list[str]:
    at: list[tuple[str, str]] = []
    found: list[str] = []
    for row in rows:
        heads = [_pair(head) for head in row["heads"]]
        same = 0
        while same < len(heads) and same < len(at) and heads[same] == at[same]:
            same += 1
        for i in range(same, len(heads)):
            if i < len(at) and at[i][1] == heads[i][1] and at[i][0] != heads[i][0]:
                found.append(heads[i][1])
        at = heads
    return found


def uchiwake_count(rows) -> int:
    return emitted_heading_names(rows).count(UCHIWAKE)


def _pack(code: str, basis: str, statement: str, which: int, table: dict) -> dict:
    rows = []
    for row in table.get("rows") or []:
        rows.append({
            "key": row["key"],
            "label": row["label"],
            "heads": [[head["key"], head["name"]] for head in row.get("heads") or []],
            "merged": list(row.get("merged_keys") or []),
            "cells": [None if cell is None else cell["value"] for cell in row.get("cells") or []],
        })
    return {"code": code, "basis": basis, "statement": statement, "which": which, "rows": rows}


def _views(table: dict) -> list[dict]:
    return [table, *(table.get("earlier") or [])]


def dump_tables(db_path: str, out_path: str, periods: int, basis: str) -> int:
    engine = sqlite_readonly_engine(db_path)
    session = sessionmaker(bind=engine, autoflush=False, future=True)
    bases = ("consolidated", "separate") if basis == "both" else (basis,)
    db = session()
    try:
        codes = [row[0] for row in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status = 'parsed' "
            "AND source IN ('edinet', 'tdnet', 'tdnet_pdf') "
            "AND doc_type IN ('annual', 'semiannual', 'quarterly') "
            "AND fiscal_year_end IS NOT NULL ORDER BY code"
        ))]
    finally:
        db.close()
    written = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for n, code in enumerate(codes, 1):
            if n % 200 == 0:
                print(f"{n}/{len(codes)}", file=sys.stderr)
            for kind in bases:
                for statement in STATEMENTS:
                    db = session()
                    try:
                        table = financial_table(
                            db, code, statement=statement, periods=periods, consolidated=kind == "consolidated",
                        )
                    except Exception as exc:
                        out.write(json.dumps(
                            {"code": code, "basis": kind, "statement": statement, "error": str(exc)},
                            ensure_ascii=False,
                        ) + "\n")
                        written += 1
                        continue
                    finally:
                        db.close()
                    for which, view in enumerate(_views(table)):
                        if view.get("empty") or not view.get("rows"):
                            continue
                        out.write(json.dumps(
                            _pack(code, kind, statement, which, view), ensure_ascii=False,
                        ) + "\n")
                        written += 1
    engine.dispose()
    print(f"wrote {written} {out_path}", file=sys.stderr)
    return 0


def _key(row: dict) -> tuple:
    return (row.get("code"), row.get("basis"), row.get("statement"), row.get("which"))


def diff_dumps(old: list[dict], new: list[dict]) -> dict[str, int]:
    failed = sum(1 for row in (*old, *new) if row.get("error"))
    left = {_key(row): row for row in old if not row.get("error")}
    right = {_key(row): row for row in new if not row.get("error")}
    old_codes = {row["code"] for row in old}
    new_codes = {row["code"] for row in new}
    bad_values = bad_rows = bad_merged = bad_labels = bad_heads = bad_uchiwake = dup = 0
    for key in set(left) | set(right):
        before, after = left.get(key), right.get(key)
        if before is None or after is None:
            bad_rows += 1
            continue
        prev = {row["key"]: row for row in before["rows"]}
        curr = {row["key"]: row for row in after["rows"]}
        if set(prev) != set(curr):
            bad_rows += 1
        for row_key in set(prev) & set(curr):
            if prev[row_key]["cells"] != curr[row_key]["cells"]:
                bad_values += 1
            if prev[row_key]["merged"] != curr[row_key]["merged"]:
                bad_merged += 1
            if prev[row_key]["label"] != curr[row_key]["label"]:
                bad_labels += 1
            if [head[1] for head in prev[row_key]["heads"]] != [head[1] for head in curr[row_key]["heads"]]:
                bad_heads += 1
        if uchiwake_count(after["rows"]) < uchiwake_count(before["rows"]):
            bad_uchiwake += 1
        if adjacent_same_headings(after["rows"]):
            dup += 1
    return {
        "codes": len(old_codes & new_codes),
        "codes_all": len(old_codes | new_codes),
        "failed": failed,
        "bad_values": bad_values,
        "bad_rows": bad_rows,
        "bad_merged": bad_merged,
        "bad_labels": bad_labels,
        "bad_heads": bad_heads,
        "bad_uchiwake": bad_uchiwake,
        "dup": dup,
    }


def format_report(report: dict[str, int]) -> str:
    return (
        f"diff codes={report['codes']}/{report['codes_all']} failed={report['failed']} "
        f"bad_values={report['bad_values']} bad_rows={report['bad_rows']} "
        f"bad_merged={report['bad_merged']} bad_labels={report['bad_labels']} "
        f"bad_heads={report['bad_heads']} bad_uchiwake={report['bad_uchiwake']} dup={report['dup']}"
    )


def load_dump(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="cmd", required=True)
    dump = commands.add_parser("dump")
    dump.add_argument("--db", required=True)
    dump.add_argument("--out", required=True)
    dump.add_argument("--periods", type=int, default=20)
    dump.add_argument("--basis", choices=("consolidated", "separate", "both"), default="consolidated")
    diff = commands.add_parser("diff")
    diff.add_argument("old")
    diff.add_argument("new")
    args = parser.parse_args(argv)
    if args.cmd == "dump":
        return dump_tables(args.db, args.out, args.periods, args.basis)
    print(format_report(diff_dumps(load_dump(args.old), load_dump(args.new))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

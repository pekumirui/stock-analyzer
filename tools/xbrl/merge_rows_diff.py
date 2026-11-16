"""財務3表の行をまとめる変更（KAB-476）の新旧比較。全社で、まとまった行のほかが変わらないことを確かめる（DB は読み取り専用。マージしない）。

  PYTHONPATH=. python /tmp/merge_rows_diff.py --new /tmp/new_ts.py --exclusions /tmp/row_merge_exclusions.json --db /path/to/stock.db
  PYTHONPATH=. python /tmp/merge_rows_diff.py --new ... --exclusions ... --db ... --code 76850

旧はその場のリポジトリの app.xbrl.three_statements、新は --new のファイル。DB は旧の load_both で銘柄ごとに 1 回だけ読み、
旧の 3 表を作ってから新の 3 表を作る（periods は画面の上限）。表ごとに次を確かめ、合わない表を bad として出す。
  cells     まとめた行: 新の各セルが、旧の（残した行 + まとめられた行）のうち値のある 1 つと同じ。旧のセルは全部どこかに入る
  same      まとめに関わらない行: キー・行名・区分・単位・セルが新旧で同じ
  order     新の行の並びが、旧の並びからまとめられた行を抜いたものと同じ
  pdf       pdf: の行の数が新旧で同じ
merge の行は、まとまった行 1 つにつき 1 行（確かめと目で見る用）。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.config as app_config
from app.xbrl import three_statements as old


DETAILS: list[tuple] = []


def load_new(path: str, exclusions: str):
    app_config.XBRL_ROW_MERGE_EXCLUSIONS_PATH = Path(exclusions)
    spec = importlib.util.spec_from_file_location("ts_new", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ts_new"] = module
    spec.loader.exec_module(module)
    return module


def shape(row: dict) -> tuple:
    return (
        row["key"], row["label"], row["group"], row["section"], row["unit"],
        tuple(None if c is None else (c["value"], c["source"]) for c in row["cells"]),
    )


def compare(a: dict, b: dict) -> tuple[list[str], list[tuple]]:
    bad: list[str] = []
    merges: list[tuple] = []
    if [p["period_end"] for p in a["periods"]] != [p["period_end"] for p in b["periods"]]:
        return ["periods"], merges
    labels = [p["label"] for p in a["periods"]]
    standards = [str(p["accounting_standard"]) for p in a["periods"]]
    rows_a = {r["key"]: r for r in a["rows"]}
    gone: set[str] = set()
    touched: set[str] = set()
    for row in b["rows"]:
        rest = row.get("merged_keys") or []
        if not rest:
            continue
        touched.add(row["key"])
        gone.update(rest)
        parts = [rows_a[k] for k in [row["key"], *rest] if k in rows_a]
        ok = True
        spans = []
        for part in parts:
            idx = [i for i, c in enumerate(part["cells"]) if c is not None]
            spans.append(idx)
            for i in idx:
                ok = ok and row["cells"][i] == part["cells"][i]
        filled = sum(len(s) for s in spans)
        ok = ok and filled == sum(c is not None for c in row["cells"]) and len({i for s in spans for i in s}) == filled
        if not ok:
            bad.append("cells")
        newest = [i for i, c in enumerate(row["cells"]) if c is not None]
        used = {standards[i] for i in newest}
        merges.append((
            row["group"] or "-", row["section"] or "-", row["label"], row["key"], ",".join(rest),
            len(parts), int(len(used) > 1), len(newest),
            ";".join(",".join(labels[i] for i in s) for s in spans[1:]),
        ))
    fields = ("key", "label", "group", "section", "unit", "cells")
    for row in b["rows"]:
        if row["key"] in touched:
            continue
        before = rows_a.get(row["key"])
        if before is None or shape(before) != shape(row):
            bad.append("same")
            if before is None:
                DETAILS.append(("same", row["key"], row["label"], "new_only", "", ""))
            else:
                diff = [f for f, x, y in zip(fields, shape(before), shape(row)) if x != y]
                DETAILS.append((
                    "same", row["key"], row["label"], ",".join(diff),
                    f"{before['group']}>{before['section']}", f"{row['group']}>{row['section']}",
                ))
    order_a = [r["key"] for r in a["rows"] if r["key"] not in gone]
    order_b = [r["key"] for r in b["rows"]]
    if order_a != order_b:
        bad.append("order" if sorted(order_a) == sorted(order_b) else "rows")
        prev_a = dict(zip(order_a, [None, *order_a]))
        prev_b = dict(zip(order_b, [None, *order_b]))
        rows_b = {r["key"]: r for r in b["rows"]}
        last = len(a["periods"]) - 1
        for key in order_b:
            if key in prev_a and prev_a[key] != prev_b[key]:
                cells = rows_b[key]["cells"]
                newest = max((i for i, c in enumerate(cells) if c is not None), default=-1)
                DETAILS.append((
                    "order", key, rows_b[key]["label"],
                    f"merged={int(key in touched)},in_newest={int(newest == last)}",
                    f"prev_old={prev_a[key]}", f"prev_new={prev_b[key]}",
                ))
    if sum(r["key"].startswith("pdf:") for r in a["rows"]) != sum(r["key"].startswith("pdf:") for r in b["rows"]):
        bad.append("pdf")
    return sorted(set(bad)), merges


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--codes-file", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-seconds", type=float, default=9000.0)
    args = parser.parse_args(argv)
    new = load_new(args.new, args.exclusions)
    db = Session(create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true"))
    listed = [c for c in Path(args.codes_file).read_text().split() if c] if args.codes_file else []
    codes = args.code or listed or [
        r[0] for r in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status = 'parsed' AND code IS NOT NULL ORDER BY code"
        ))
    ]
    if args.limit and not args.code:
        codes = codes[: args.limit]

    cache: dict[str, dict] = {}
    original = old.load_both

    def load_once(session, code):
        if code not in cache:
            cache.clear()
            cache[code] = original(session, code)
        return cache[code]

    old.load_both = load_once
    new.load_both = load_once

    totals: Counter = Counter()
    members: dict[str, set[str]] = {}
    bad_rows: list[tuple] = []
    merge_rows: list[tuple] = []
    detail_rows: list[tuple] = []
    started = time.monotonic()
    done = failed = 0
    for code in codes:
        if time.monotonic() - started > args.max_seconds:
            break
        done += 1
        try:
            before = {s: old.financial_table(db, code, statement=s, periods=old.MAX_PERIODS) for s in old.STATEMENTS}
            after = {s: new.financial_table(db, code, statement=s, periods=new.MAX_PERIODS) for s in new.STATEMENTS}
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"diff failed code={code} error={type(exc).__name__}: {exc}", flush=True)
            continue
        for statement in old.STATEMENTS:
            a, b = before[statement], after[statement]
            if not a["rows"] and not b["rows"]:
                continue
            totals["tables"] += 1
            DETAILS.clear()
            bad, merges = compare(a, b)
            detail_rows.extend((code, statement, *d) for d in DETAILS)
            for kind in bad:
                totals[f"bad_{kind}"] += 1
                members.setdefault(f"bad_{kind}", set()).add(code)
                bad_rows.append((code, statement, kind))
            for m in merges:
                switch = m[6]
                for key in ("merged_rows", f"merged_rows_{statement}", *(["merged_rows_switch"] if switch else [])):
                    totals[key] += 1
                    members.setdefault(key, set()).add(code)
                totals["merged_away_keys"] += len(m[4].split(","))
                merge_rows.append((code, statement, *m))
        if done % 200 == 0:
            print(f"diff progress codes={done}/{len(codes)} sec={time.monotonic() - started:.0f}", flush=True)
    print(f"diff codes={done}/{len(codes)} failed={failed} sec={time.monotonic() - started:.0f}")
    print("## summary\tkey\tcount\tcodes")
    for key in sorted(totals):
        print(f"summary\t{key}\t{totals[key]}\t{len(members.get(key, ()))}")
    print("## bad\tcode\tstatement\tkind")
    for row in bad_rows:
        print("bad\t" + "\t".join(str(v) for v in row))
    print("## detail\tcode\tstatement\tkind\tkey\tlabel\twhat\told\tnew")
    for row in detail_rows:
        print("detail\t" + "\t".join(str(v) for v in row))
    print("## merge\tcode\tstatement\tgroup\tsection\tlabel\tkeep\tmerged_keys\tparts_in_old\tswitch\tfilled\tmerged_periods")
    for row in merge_rows:
        print("merge\t" + "\t".join(str(v) for v in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

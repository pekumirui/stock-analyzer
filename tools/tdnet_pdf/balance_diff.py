"""three_statements.py の 2 つの版で、PDF だけの期の対応と業績詳細の表を比べる（KAB-461 の前後比較。DB は読み取り専用）。

  PYTHONPATH=. python /tmp/balance_diff.py --old /tmp/old_ts.py --new /tmp/new_ts.py --db /path/to/stock.db

銘柄ごとに load_both を 1 回だけ読み、写しを両方の版に渡す。新しい版の _foreign_balance が止めた組
（表, 要素）を銘柄ごとに覚え、銘柄を 2 つに分ける:
  blocked  1 つでも止めた銘柄。対応・表が変わってよい。止めた組と違いを全部出す
  other    それ以外。対応（lost / changed / gained）も表（行・値・並び・期）も違いが 0 であることがマージの条件
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import sys
import time
from collections import Counter

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sig(ms) -> dict:
    return {
        (m.consolidated, m.statement, m.filing.id, m.fact.id): (
            m.fact.local, m.filing.period_end.isoformat(),
            None if m.mapped is None else (m.mapped.key, m.mapped.sign, m.mapped.by),
        )
        for m in ms
    }


def table_diffs(ta: dict, tb: dict) -> list[str]:
    out: list[str] = []
    if ta["periods"] != tb["periods"] or ta["consolidated"] != tb["consolidated"]:
        out.append("periods")
    ra = {r["key"]: r for r in ta["rows"]}
    rb = {r["key"]: r for r in tb["rows"]}
    out += [f"row- {k} {ra[k]['label']}" for k in ra if k not in rb]
    out += [f"row+ {k} {rb[k]['label']}" for k in rb if k not in ra]
    for k, r in rb.items():
        if k not in ra:
            continue
        if r["cells"] != ra[k]["cells"]:
            out.append(f"cells {k} {r['label']}")
        if (r["group"], r["section"], r["label"]) != (ra[k]["group"], ra[k]["section"], ra[k]["label"]):
            out.append(f"meta {k} {ra[k]['label']} -> {r['label']}")
    if [k for k in (r["key"] for r in ta["rows"]) if k in rb] != [k for k in (r["key"] for r in tb["rows"]) if k in ra]:
        out.append("order")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    old, new = load("ts_old", args.old), load("ts_new", args.new)
    db = Session(create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true"))
    codes = args.code or [r[0] for r in db.execute(text(
        "SELECT DISTINCT code FROM xbrl_filings WHERE source = 'tdnet_pdf' ORDER BY code"))]
    if args.limit and not args.code:
        codes = codes[: args.limit]
    loader = new.load_both
    rule = new._foreign_balance
    stopped: Counter = Counter()

    def spy(statement, fact, own):
        got = rule(statement, fact, own)
        if got:
            stopped[(statement, fact.key)] += 1
        return got

    new._foreign_balance = spy
    totals: Counter = Counter()
    blocked_lines: list[str] = []
    other_lines: list[str] = []
    stopped_all: Counter = Counter()
    started = time.monotonic()
    for n, code in enumerate(codes, 1):
        stopped.clear()
        lines: list[str] = []
        counts: Counter = Counter()
        try:
            both = loader(db, code)
            for mod in (old, new):
                mod.load_both = lambda _db, _code, both=both: copy.deepcopy(both)
            a, b = sig(old.pdf_only_matches(db, code)), sig(new.pdf_only_matches(db, code))
            for k in sorted(set(a) | set(b), key=str):
                va, vb = a.get(k), b.get(k)
                if va == vb:
                    continue
                ma, mb = va and va[2], vb and vb[2]
                if va is None or vb is None:
                    kind = "rows"
                elif ma is None:
                    kind = "gained"
                elif mb is None:
                    kind = "lost"
                else:
                    kind = "changed"
                counts[kind] += 1
                name, end = (va or vb)[0], (va or vb)[1]
                lines.append(f"{kind}\t{code}\t{k[1]}\t{end}\t{name}\t{ma} -> {mb}")
            for st in ("BS", "PL", "CF"):
                for d in table_diffs(old.financial_table(db, code, statement=st, periods=20),
                                     new.financial_table(db, code, statement=st, periods=20)):
                    counts["table"] += 1
                    lines.append(f"table\t{code}\t{st}\t{d}")
        except Exception as exc:
            counts["failed"] += 1
            lines.append(f"failed\t{code}\t{exc!r}")
        finally:
            old.load_both = new.load_both = loader
        group = "blocked" if stopped else "other"
        totals[f"{group}_codes"] += 1
        for kind, c in counts.items():
            totals[f"{group}_{kind}"] += c
        if stopped:
            for (st, key), c in sorted(stopped.items()):
                blocked_lines.append(f"stopped\t{code}\t{st}\t{key}\t{c}")
                stopped_all[(st, key)] += 1
            blocked_lines += lines
        else:
            other_lines += lines
        if n % 200 == 0:
            print(f"progress {n}/{len(codes)} {time.monotonic() - started:.0f}s", file=sys.stderr, flush=True)
    print("## totals")
    for group in ("other", "blocked"):
        for kind in ("codes", "failed", "lost", "changed", "gained", "rows", "table"):
            print(f"{group}_{kind}\t{totals[f'{group}_{kind}']}")
    print("## stopped pairs (statement, element, codes)")
    for (st, key), c in stopped_all.most_common():
        print(f"{st}\t{key}\t{c}")
    print("## other (must be empty)")
    for line in other_lines:
        print(line)
    print("## blocked")
    for line in blocked_lines:
        print(line)
    print(f"## seconds {time.monotonic() - started:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

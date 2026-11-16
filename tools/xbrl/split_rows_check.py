"""財務3表で、同じ科目が要素違いで 2 行に割れている会社を全社で数える（KAB-475。DB は読み取り専用で開く。マージしない）。

  PYTHONPATH=. python /tmp/split_rows_check.py --db /path/to/stock.db
  PYTHONPATH=. python /tmp/split_rows_check.py --db ... --code 76850

画面と同じ financial_table（periods は画面の上限 MAX_PERIODS）を銘柄 × 表（BS / PL / CF）で作り、
同じ（大区分, 区分, 行名）の行が 2 つ以上あって、値の入る期が 1 つも重ならない 2 行の組を「割れ」として出す。
期が重なる同名の行（区分ごとの「貸倒引当金」など、別の科目）は数えない。
組の種類:
  std   2 行とも標準要素（バイセルの売掛金: NotesAndAccountsReceivableTrade と AccountsReceivableTrade）
  ext   どちらかが会社独自の要素（ext:）で、pdf: は無い
  pdf   どちらかが対応の付かない PDF の行（pdf:）
"""
from __future__ import annotations

import argparse
import time
from collections import Counter
from itertools import combinations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.xbrl import three_statements as ts


def kind_of(a: str, b: str) -> str:
    if a.startswith("pdf:") or b.startswith("pdf:"):
        return "pdf"
    if a.startswith("ext:") or b.startswith("ext:"):
        return "ext"
    return "std"


def splits(table: dict) -> list[tuple]:
    periods = table["periods"]
    groups: dict[tuple, list[dict]] = {}
    for row in table["rows"]:
        groups.setdefault((row["group"], row["section"], row["label"]), []).append(row)
    out = []
    for (group, section, label), rows in groups.items():
        if len(rows) < 2:
            continue
        for a, b in combinations(rows, 2):
            ia = {i for i, c in enumerate(a["cells"]) if c is not None}
            ib = {i for i, c in enumerate(b["cells"]) if c is not None}
            if not ia or not ib or ia & ib:
                continue
            minor = ia if len(ia) <= len(ib) else ib
            sources = Counter(periods[i]["source"] for i in minor)
            types = Counter(periods[i]["period_type"] for i in minor)
            out.append((
                kind_of(a["key"], b["key"]), group or "-", section or "-", label, a["key"], b["key"],
                len(ia), len(ib),
                ",".join(periods[i]["label"] for i in sorted(minor)),
                ",".join(f"{k}:{n}" for k, n in sorted(sources.items())),
                ",".join(f"{k}:{n}" for k, n in sorted(types.items())),
            ))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None, help="--code が無いとき、先頭 N 銘柄だけ")
    parser.add_argument("--max-seconds", type=float, default=5400.0)
    parser.add_argument("--top", type=int, default=60, help="要素の組の上位を何行出すか")
    args = parser.parse_args(argv)
    db = Session(create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true"))
    codes = args.code or [
        r[0] for r in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status = 'parsed' AND code IS NOT NULL ORDER BY code"
        ))
    ]
    if args.limit and not args.code:
        codes = codes[: args.limit]

    cache: dict[str, dict] = {}
    original = ts.load_both

    def load_once(session, code):
        if code not in cache:
            cache.clear()
            cache[code] = original(session, code)
        return cache[code]

    ts.load_both = load_once

    totals: Counter = Counter()
    by_kind: Counter = Counter()
    by_pair: Counter = Counter()
    pair_codes: dict[tuple, set[str]] = {}
    codes_by_kind: dict[str, set[str]] = {}
    rows: list[tuple] = []
    started = time.monotonic()
    done = failed = 0
    for code in codes:
        if time.monotonic() - started > args.max_seconds:
            break
        done += 1
        try:
            found = []
            has_table = False
            for statement in ts.STATEMENTS:
                table = ts.financial_table(db, code, statement=statement, periods=ts.MAX_PERIODS)
                has_table = has_table or bool(table["rows"])
                found += [(statement, *s) for s in splits(table)]
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"split failed code={code} error={type(exc).__name__}: {exc}", flush=True)
            continue
        totals["codes_with_table"] += has_table
        totals["codes_split"] += bool(found)
        for row in found:
            statement, kind, _, _, label, a, b = row[:7]
            by_kind[(statement, kind)] += 1
            codes_by_kind.setdefault(kind, set()).add(code)
            codes_by_kind.setdefault(f"{statement}_{kind}", set()).add(code)
            pair = (statement, kind, label, *sorted((a, b)))
            by_pair[pair] += 1
            pair_codes.setdefault(pair, set()).add(code)
            rows.append((code, *row))
        if done % 200 == 0:
            print(f"split progress codes={done}/{len(codes)} sec={time.monotonic() - started:.0f}", flush=True)
    print(f"split codes={done}/{len(codes)} failed={failed} sec={time.monotonic() - started:.0f}")
    print("## summary\tkey\tcount")
    print(f"summary\tcodes_with_table\t{totals['codes_with_table']}")
    print(f"summary\tcodes_split\t{totals['codes_split']}")
    for key in sorted(codes_by_kind):
        print(f"summary\tcodes_{key}\t{len(codes_by_kind[key])}")
    print("## kind\tstatement\tkind\tpairs")
    for (statement, kind), n in sorted(by_kind.items()):
        print(f"kind\t{statement}\t{kind}\t{n}")
    print("## pair\tstatement\tkind\tlabel\tkey_a\tkey_b\tcodes")
    ranked = sorted(pair_codes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for pair, members in ranked[: args.top]:
        print("pair\t" + "\t".join(pair) + f"\t{len(members)}")
    print(
        "## row\tcode\tstatement\tkind\tgroup\tsection\tlabel\tkey_a\tkey_b\tperiods_a\tperiods_b"
        "\tminor_periods\tminor_sources\tminor_period_types"
    )
    for row in rows:
        print("row\t" + "\t".join(str(v) for v in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

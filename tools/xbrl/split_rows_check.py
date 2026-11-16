"""財務3表で 2 行に割れた同じ科目を「行名でまとめる」案を、全社で確かめる（KAB-475。DB は読み取り専用で開く。マージしない）。

  PYTHONPATH=. python /tmp/split_rows_check.py --db /path/to/stock.db
  PYTHONPATH=. python /tmp/split_rows_check.py --db ... --code 76850 --code 28740

画面と同じ financial_table（periods は画面の上限 MAX_PERIODS）を銘柄 × 表（BS / PL / CF）で作り、3 種類の行を出す。

cand     案でまとめる候補。同じ（大区分, 区分, 行名）で、値の入る期が 1 つも重ならない 2 行の組
  rule     merge          案どおりまとめる
           pdf            どちらかが対応の付かない PDF の行（pdf:）。対象外
           unit           単位の種類が違う（円と 1 株当たり）。対象外
           pl_section     PL で、タグの末尾の区分（NOI / NOE / EI / EL / SGA）が違う。別の科目
           pl_other       PL の「その他」。別の科目
           ambiguous      同じ行名の行が 3 つ以上あり、期が重なる行と重ならない行が混ざる（どれとまとめるか決まらない）
  kind     std / ext_std / ext_ext（標準どうし / 会社独自と標準 / 会社独自どうし）
  switch   1 なら 2 行の期の会計基準が違う（日本基準 → IFRS など）
  same / diff   同じ日付（同じ連結区分・同じ期間）に両方のタグの値がある回数。same は値が一致、diff は食い違い。
                当期以外の列（短信 XBRL の前期の列など）と、表で主にならなかった出どころの値も使う。
                EDINET は当期の値しか DB に無いので、両方 0 の組が多い（裏が取れないだけで、誤りではない）
  jump     BS だけ。タグが替わる隣り合った期で、値が 5 倍以上動くか符号が変わった回数

variant  表記揺れ。同じ（大区分, 区分）で期が重ならず、行名が違う 2 行の組のうち
  by=norm    行名をそろえると同じ（NFKC、空白、括弧の全角半角、「（純額）」、および/及び、たな卸/棚卸 など）
  by=value   行名は違うが、同じ日付の値が一致し、食い違いが無い（same >= 1, diff == 0）

dup      同じ（大区分, 区分, 行名）で期が重なり、重なった期の値が全部同じ 2 行の組（同じ値が 2 行に出ている）
"""
from __future__ import annotations

import argparse
import re
import time
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

from app.xbrl import three_statements as ts

PL_TAIL_RE = re.compile(r"(NOI|NOE|EI|EL|SGA)$")
NORM_DROP_RE = re.compile(r"[\s　・､、,]|\(純額\)|\(△[^)]*\)")
NORM_SWAPS = (("および", "及び"), ("ならびに", "並びに"), ("たな卸", "棚卸"), ("たな", "棚"), ("ソフトウェア", "ソフトウエア"))
JUMP_RATIO = 5.0


def norm(label: str) -> str:
    out = unicodedata.normalize("NFKC", label)
    out = NORM_DROP_RE.sub("", out)
    for a, b in NORM_SWAPS:
        out = out.replace(a, b)
    return out


def kind_of(a: str, b: str) -> str:
    n = a.startswith("ext:") + b.startswith("ext:")
    return ("std", "ext_std", "ext_ext")[n]


def grain(v: float) -> float:
    v = abs(v)
    for g in (1e6, 1e3):
        if v >= g and v % g == 0:
            return g
    return 1.0


def same_value(a: float, b: float) -> bool:
    g = max(grain(a), grain(b))
    return abs(abs(a) - abs(b)) <= (0.5 if g == 1 else g)


def load_evidence(db: Session, code: str) -> dict[str, dict[tuple, set[float]]]:
    ids = [
        int(r[0]) for r in db.execute(
            text(
                "SELECT id FROM xbrl_filings WHERE code = :code AND parse_status = 'parsed' "
                "AND source IN ('edinet', 'tdnet') AND doc_type IN :types AND fiscal_year_end IS NOT NULL"
            ).bindparams(bindparam("types", expanding=True)),
            {"code": code, "types": list(ts.DOC_TYPES)},
        )
    ]
    out: dict[str, dict[tuple, set[float]]] = defaultdict(lambda: defaultdict(set))
    sql = text(
        "SELECT e.namespace_key, e.local_name, e.is_custom, c.is_consolidated, c.has_dimensions, c.dimensions_json, "
        "c.start_date, c.end_date, f.value_num "
        "FROM xbrl_facts f "
        "JOIN xbrl_contexts c ON c.id = f.context_id "
        "JOIN xbrl_elements e ON e.id = f.element_id "
        "WHERE f.filing_id IN :ids AND f.value_num IS NOT NULL AND e.namespace_key NOT IN :excluded"
    ).bindparams(bindparam("ids", expanding=True), bindparam("excluded", expanding=True))
    for start in range(0, len(ids), 500):
        for r in db.execute(sql, {"ids": ids[start:start + 500], "excluded": list(ts.EXCLUDED_NS)}):
            ns = str(r[0])
            if ns in ts.PDF_NS or (ns not in ts.STANDARD_NS and not r[2]):
                continue
            if not ts._primary_dims(r[4], r[5]) or r[7] is None:
                continue
            key = str(r[1]) if ns in ts.STANDARD_NS else f"ext:{r[1]}"
            out[key][(bool(r[3]), str(r[6] or "")[:10], str(r[7])[:10])].add(float(r[8]))
    return out


def evidence(ev: dict, a: str, b: str) -> tuple[int, int, float]:
    ea, eb = ev.get(a), ev.get(b)
    if not ea or not eb:
        return 0, 0, 0.0
    same = diff = 0
    top = 0.0
    for ctx in ea.keys() & eb.keys():
        va, vb = ea[ctx], eb[ctx]
        if any(x != 0 and same_value(x, y) for x in va for y in vb):
            same += 1
            top = max(top, max(abs(x) for x in va))
        elif any(x != 0 for x in va) or any(y != 0 for y in vb):
            diff += 1
    return same, diff, top


def filled(row: dict) -> set[int]:
    return {i for i, c in enumerate(row["cells"]) if c is not None}


def jumps(a: dict, b: dict) -> int:
    seq = sorted(
        [(i, 0, a["cells"][i]["value"]) for i in filled(a)] + [(i, 1, b["cells"][i]["value"]) for i in filled(b)]
    )
    n = 0
    for (_, sa, va), (_, sb, vb) in zip(seq, seq[1:]):
        if sa == sb:
            continue
        lo, hi = sorted((abs(va), abs(vb)))
        if (va < 0) != (vb < 0) or (lo > 0 and hi / lo >= JUMP_RATIO) or (lo == 0 and hi > 0):
            n += 1
    return n


def standards(table: dict, idx: set[int]) -> set[str]:
    return {str(table["periods"][i]["accounting_standard"]) for i in idx}


def rule_of(statement: str, label: str, a: dict, b: dict, clean: bool) -> str:
    if a["key"].startswith("pdf:") or b["key"].startswith("pdf:"):
        return "pdf"
    if a["unit"] != b["unit"]:
        return "unit"
    if statement == "PL":
        if label in ts.GENERIC_LABELS:
            return "pl_other"
        ta, tb = PL_TAIL_RE.search(a["key"]), PL_TAIL_RE.search(b["key"])
        if ta and tb and ta.group(1) != tb.group(1):
            return "pl_section"
    return "merge" if clean else "ambiguous"


def clean_pairs(rows: list[dict]) -> dict[tuple[int, int], bool]:
    sets = [filled(r) for r in rows]
    n = len(rows)
    disjoint = {(i, j): bool(sets[i]) and bool(sets[j]) and not (sets[i] & sets[j]) for i, j in combinations(range(n), 2)}
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j), ok in disjoint.items():
        if ok:
            parent[find(i)] = find(j)
    comp: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        comp[find(i)].append(i)
    clique = {root: all(disjoint[p] for p in combinations(members, 2)) for root, members in comp.items()}
    return {p: clique[find(p[0])] for p, ok in disjoint.items() if ok}


def check(statement: str, table: dict, ev: dict) -> tuple[list[tuple], list[tuple], list[tuple]]:
    periods = table["periods"]
    labels = [p["label"] for p in periods]
    by_label: dict[tuple, list[dict]] = defaultdict(list)
    by_section: dict[tuple, list[dict]] = defaultdict(list)
    for row in table["rows"]:
        by_label[(row["group"], row["section"], row["label"])].append(row)
        by_section[(row["group"], row["section"])].append(row)
    cands, variants, dups = [], [], []
    for (group, section, label), rows in by_label.items():
        if len(rows) < 2:
            continue
        clean = clean_pairs(rows)
        for i, j in combinations(range(len(rows)), 2):
            a, b = rows[i], rows[j]
            ia, ib = filled(a), filled(b)
            if not ia or not ib:
                continue
            if ia & ib:
                if all(same_value(a["cells"][k]["value"], b["cells"][k]["value"]) for k in ia & ib):
                    dups.append((statement, group or "-", section or "-", label, a["key"], b["key"], len(ia & ib)))
                continue
            rule = rule_of(statement, label, a, b, clean[(i, j)])
            same, diff, _ = evidence(ev, a["key"], b["key"]) if rule != "pdf" else (0, 0, 0.0)
            minor = ia if len(ia) <= len(ib) else ib
            cands.append((
                statement, rule, kind_of(a["key"], b["key"]) if rule != "pdf" else "-",
                int(standards(table, ia).isdisjoint(standards(table, ib))),
                same, diff, jumps(a, b) if statement == "BS" else 0,
                group or "-", section or "-", label, a["key"], b["key"], len(ia), len(ib),
                ",".join(labels[k] for k in sorted(minor)),
            ))
    for (group, section), rows in by_section.items():
        rows = [r for r in rows if not r["key"].startswith("pdf:")]
        sets = [filled(r) for r in rows]
        for i, j in combinations(range(len(rows)), 2):
            a, b = rows[i], rows[j]
            if a["label"] == b["label"] or a["unit"] != b["unit"] or not sets[i] or not sets[j] or sets[i] & sets[j]:
                continue
            same, diff, top = evidence(ev, a["key"], b["key"])
            by = "norm" if norm(a["label"]) == norm(b["label"]) else "value" if same and not diff else None
            if by is None:
                continue
            variants.append((
                statement, by, kind_of(a["key"], b["key"]), same, diff, f"{top:.0f}",
                group or "-", section or "-", a["label"], b["label"], a["key"], b["key"], len(sets[i]), len(sets[j]),
            ))
    return cands, variants, dups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None, help="--code が無いとき、先頭 N 銘柄だけ")
    parser.add_argument("--max-seconds", type=float, default=9000.0)
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

    pairs: Counter = Counter()
    members: dict[tuple, set[str]] = defaultdict(set)
    out: dict[str, list[tuple]] = {"cand": [], "variant": [], "dup": []}
    started = time.monotonic()
    done = failed = with_table = 0
    for code in codes:
        if time.monotonic() - started > args.max_seconds:
            break
        done += 1
        try:
            ev = load_evidence(db, code)
            found: dict[str, list[tuple]] = {"cand": [], "variant": [], "dup": []}
            has_table = False
            for statement in ts.STATEMENTS:
                table = ts.financial_table(db, code, statement=statement, periods=ts.MAX_PERIODS)
                has_table = has_table or bool(table["rows"])
                for name, rows in zip(("cand", "variant", "dup"), check(statement, table, ev)):
                    found[name] += rows
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"check failed code={code} error={type(exc).__name__}: {exc}", flush=True)
            continue
        with_table += has_table
        for row in found["cand"]:
            statement, rule, kind, switch, same, diff, jump = row[:7]
            proof = "diff" if diff else "same" if same else "none"
            for key in (
                ("cand", "all", "all"), ("cand", rule, "all"), ("cand", rule, kind),
                ("cand", rule, f"{statement}"), ("cand", rule, f"proof_{proof}"),
                *([("cand", rule, "switch")] if switch else []),
                *([("cand", rule, "jump")] if jump else []),
            ):
                pairs[key] += 1
                members[key].add(code)
        for row in found["variant"]:
            for key in (("variant", "all", "all"), ("variant", row[1], "all"), ("variant", row[1], row[0])):
                pairs[key] += 1
                members[key].add(code)
        for row in found["dup"]:
            for key in (("dup", "all", "all"), ("dup", "all", row[0])):
                pairs[key] += 1
                members[key].add(code)
        for name, rows in found.items():
            out[name] += [(code, *row) for row in rows]
        if done % 200 == 0:
            print(f"check progress codes={done}/{len(codes)} sec={time.monotonic() - started:.0f}", flush=True)
    print(f"check codes={done}/{len(codes)} failed={failed} with_table={with_table} sec={time.monotonic() - started:.0f}")
    print("## summary\ttype\trule\tslice\tpairs\tcodes")
    for key in sorted(pairs):
        print("summary\t" + "\t".join(key) + f"\t{pairs[key]}\t{len(members[key])}")
    print(
        "## cand\tcode\tstatement\trule\tkind\tswitch\tsame\tdiff\tjump\tgroup\tsection\tlabel\tkey_a\tkey_b"
        "\tperiods_a\tperiods_b\tminor_periods"
    )
    print(
        "## variant\tcode\tstatement\tby\tkind\tsame\tdiff\ttop_value\tgroup\tsection\tlabel_a\tlabel_b\tkey_a\tkey_b"
        "\tperiods_a\tperiods_b"
    )
    print("## dup\tcode\tstatement\tgroup\tsection\tlabel\tkey_a\tkey_b\toverlap_periods")
    for name, rows in out.items():
        for row in rows:
            print(f"{name}\t" + "\t".join(str(v) for v in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

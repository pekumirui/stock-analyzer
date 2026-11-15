"""KAB-463 3 本目: 振り分けの前後（推定 → 表示リンク）と、短信 PDF の対応の前後を出す。コミットしない。

リポジトリ直下から:
  git show origin/main:app/xbrl/three_statements.py > /tmp/ts_old.py
  PYTHONPATH=. .venv/bin/python /tmp/presentation_diff.py --db /path/stock.db \
      --old /tmp/ts_old.py --new app/xbrl/three_statements.py --out /tmp/presentation_diff.tsv
  （--codes 9101,4502 で絞る。--skip-pdf で (b) を飛ばす）

旧と新はそれぞれの版の load_both で読む（片方で読んだ結果を両方に渡さない）。DB は読み取り専用で開く。
出力: 標準出力に件数、--out に 1 行 1 件の違い（タブ区切り）。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from collections import Counter

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def split_diff(old_f, new_f, status, count: Counter, lines: list[str], code: str, basis: bool) -> None:
    for st in ("BS", "PL", "CF"):
        a = [f.key for f in old_f.by_statement.get(st, [])] if old_f else []
        b = [f.key for f in new_f.by_statement.get(st, [])] if new_f else []
        if not a and not b:
            continue
        tag = f"{status or 'null'}:{st}"
        count[f"split_total:{tag}"] += 1
        sa, sb = set(a), set(b)
        if a == b:
            kind = "same"
        elif sa == sb:
            kind = "order_only"
        elif sa < sb:
            kind = "new_more"
        elif sb < sa:
            kind = "old_more"
        else:
            kind = "both_differ"
        count[f"split_{kind}:{tag}"] += 1
        if new_f is not None:
            if st == "BS":
                count[f"mix_bs_duration:{tag}"] += sum(1 for f in new_f.by_statement.get(st, []) if not f.instant)
            if st == "PL":
                count[f"mix_pl_instant:{tag}"] += sum(1 for f in new_f.by_statement.get(st, []) if f.instant)
        if kind != "same":
            doc = (new_f or old_f).doc_id
            lines.append("\t".join([
                f"split_{kind}", code, str(int(basis)), doc, st, status or "null",
                ",".join(sorted(sa - sb)), ",".join(sorted(sb - sa)),
            ]))


def learn_run(mod, filings):
    learned = [mod.learn_pair(pdf, xbrl) for pdf, xbrl in mod._pairs(filings)]
    maps = {
        (item.period_end, statement, name): m.key
        for item in learned for statement, by_name in item.maps.items() for name, m in by_name.items()
    }
    xbrl_ends = {f.period_end for f in filings if not f.is_pdf and f.by_statement}
    applied = {}
    extras = {}
    for statement in mod.STATEMENTS:
        names = mod.name_index(filings, statement)
        for filing in filings:
            if filing.is_pdf:
                if filing.period_end in xbrl_ends or statement not in filing.by_statement:
                    continue
                facts = filing.by_statement[statement]
                got = mod.apply_learned(facts, learned, statement, filing.period_end, filing.cls, names)
                for fact, m in got:
                    applied[(filing.id, statement, fact.id, fact.local)] = None if m is None else (m.key, m.sign)
            elif filing.by_statement:
                own = {f.key for f in filing.by_statement.get(statement, [])}
                for f in mod._with_extras(filing, statement, learned):
                    if f.key not in own:
                        extras[(filing.id, statement, f.key)] = filing.period_end
    return maps, applied, extras


def learn_diff(old, new, both_old, both_new, count: Counter, lines: list[str], code: str) -> None:
    for basis in (True, False):
        o_maps, o_app, o_ext = learn_run(old, both_old[basis])
        n_maps, n_app, n_ext = learn_run(new, both_new[basis])
        count["learned_rows"] += len(o_maps)
        count["applied_rows"] += len(o_app)
        count["extras_old"] += len(o_ext)
        for k in sorted(set(o_maps) | set(n_maps), key=str):
            a, b = o_maps.get(k), n_maps.get(k)
            if a == b:
                continue
            kind = "learned_lost" if b is None else "learned_new" if a is None else "learned_changed"
            count[f"{kind}:{k[1]}"] += 1
            lines.append(f"{kind}\t{code}\t{int(basis)}\t{k[0]}\t{k[1]}\t{k[2]}\t{a}\t{b}")
        for k in sorted(set(o_app) | set(n_app), key=str):
            a, b = o_app.get(k), n_app.get(k)
            if a == b:
                continue
            kind = "applied_lost" if b is None else "applied_new" if a is None else "applied_changed"
            count[f"{kind}:{k[1]}"] += 1
            lines.append(f"{kind}\t{code}\t{int(basis)}\t{k[0]}\t{k[1]}\t{k[3]}\t{a}\t{b}")
        for k in sorted(set(o_ext) ^ set(n_ext), key=str):
            kind = "extra_removed" if k in o_ext else "extra_added"
            count[f"{kind}:{k[1]}"] += 1
            lines.append(f"{kind}\t{code}\t{int(basis)}\t{o_ext.get(k) or n_ext.get(k)}\t{k[1]}\t{k[2]}\t{k[0]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--codes", default="")
    ap.add_argument("--skip-pdf", action="store_true")
    args = ap.parse_args()
    old, new = load("ts_old", args.old), load("ts_new", args.new)
    engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true", poolclass=NullPool)
    count: Counter = Counter()
    lines: list[str] = []
    t_old = t_new = 0.0
    started = time.time()
    with Session(engine) as db:
        status = dict(db.execute(text("SELECT id, presentation_status FROM xbrl_filings")).all())
        if args.codes:
            codes = args.codes.split(",")
        else:
            codes = [r[0] for r in db.execute(text(
                "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status = 'parsed' "
                "AND source IN ('edinet', 'tdnet') ORDER BY 1"
            ))]
        pdf_codes = {r[0] for r in db.execute(text("SELECT DISTINCT code FROM xbrl_filings WHERE source = 'tdnet_pdf'"))}
        for n, code in enumerate(codes, 1):
            try:
                t0 = time.perf_counter()
                both_old = old.load_both(db, code)
                t1 = time.perf_counter()
                both_new = new.load_both(db, code)
                t2 = time.perf_counter()
            except Exception as exc:
                count["failed"] += 1
                lines.append(f"FAILED\t{code}\t{exc!r}")
                continue
            t_old += t1 - t0
            t_new += t2 - t1
            for basis in (True, False):
                o = {f.id: f for f in both_old[basis] if not f.is_pdf}
                nw = {f.id: f for f in both_new[basis] if not f.is_pdf}
                for fid in sorted(set(o) | set(nw)):
                    split_diff(o.get(fid), nw.get(fid), status.get(fid), count, lines, code, basis)
            if not args.skip_pdf and code in pdf_codes:
                count["pdf_codes"] += 1
                learn_diff(old, new, both_old, both_new, count, lines, code)
            if n % 200 == 0:
                print(f"{n}/{len(codes)} {time.time() - started:.0f}s", file=sys.stderr, flush=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"codes={len(codes)} seconds={time.time() - started:.0f} load_both_old={t_old:.1f}s load_both_new={t_new:.1f}s")
    for k in sorted(count):
        print(f"{k}={count[k]}")


if __name__ == "__main__":
    main()

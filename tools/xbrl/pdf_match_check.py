from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Sequence, TextIO

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.xbrl import three_statements
from tools.xbrl import label_dictionary

SUBSET_TABLES = ("stocks", "xbrl_filings", "xbrl_contexts", "xbrl_facts", "xbrl_elements", "xbrl_presentation_roles")
BY_FILING = ("xbrl_contexts", "xbrl_facts", "xbrl_presentation_roles")
CHANGE_COLUMNS = (
    "change", "code", "consolidated", "period_end", "statement", "row", "value",
    "old_key", "old_by", "new_key", "new_by", "truth_key",
)
WRONG_COLUMNS = (
    "code", "consolidated", "period_end", "cls", "standard", "statement", "row", "base", "value", "matched_by",
    "key", "key_label", "key_in_hidden", "key_hidden_value", "truth_key", "truth_label", "truth_in_others",
)
REMAIN_COLUMNS = (
    "code", "consolidated", "period_end", "cls", "standard", "statement", "row", "base", "value", "kind",
    "truth_key", "truth_label", "section", "prev_row", "prev_key", "next_row", "next_key",
    "same_name_key", "same_name_value", "value_elsewhere",
)


def load_module(name: str, path: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_only(path: str) -> Session:
    return Session(create_engine(f"sqlite:///file:{path}?mode=ro&uri=true"))


def cell(value: object) -> str:
    return "" if value is None else str(value).replace("\t", " ").replace("\n", " ")


def write_row(out: TextIO | None, values: Iterable[object]) -> None:
    if out is not None:
        out.write("\t".join(cell(v) for v in values) + "\n")


def default_books(*modules: ModuleType | None) -> dict[int, Any]:
    return {id(m): m._standard_book for m in modules if m is not None and hasattr(m, "_standard_book")}


def no_book(module: ModuleType, defaults: dict[int, Any]) -> None:
    if id(module) in defaults:
        book = module.StandardBook()
        module._standard_book = lambda: book


def use_book(module: ModuleType, defaults: dict[int, Any], raw: dict[str, Any] | None) -> None:
    if id(module) not in defaults:
        return
    if raw is None:
        module._standard_book = defaults[id(module)]
        return
    book = module.standard_book(raw)
    module._standard_book = lambda: book


def restore_books(defaults: dict[int, Any], *modules: ModuleType | None) -> None:
    for module in modules:
        if module is not None and id(module) in defaults:
            module._standard_book = defaults[id(module)]


def hidden_view(filings: Sequence[Any], end: Any, before_only: bool) -> list[Any]:
    if before_only:
        return [f for f in filings if f.period_end < end or (f.period_end == end and f.is_pdf)]
    return [f for f in filings if f.period_end != end or f.is_pdf]


def matches_of(module: ModuleType, db: Session, code: str, basis: bool, filings: Sequence[Any], pdf_id: int) -> list[Any]:
    original = module.load_both
    module.load_both = lambda _db, _code: {basis: list(filings)}
    try:
        return [m for m in module.pdf_only_matches(db, code) if m.filing.id == pdf_id]
    finally:
        module.load_both = original


def truth_of(module: ModuleType, pdf: Any, xbrl: Any) -> dict[int, str | None]:
    learned = [module.learn_pair(pdf, xbrl)]
    out: dict[int, str | None] = {}
    for statement, facts in pdf.by_statement.items():
        for fact, mapped in module.apply_learned(facts, learned, statement, pdf.period_end, pdf.cls, None):
            out[fact.id] = None if mapped is None else mapped.key
    return out


def verdict(key: str | None, truth: str | None) -> str:
    if truth is None:
        return "no_truth"
    return "ok" if key == truth else "wrong"


def unmatched_kind(truth: str | None, seen: set[str]) -> str:
    if truth is None:
        return "not_in_xbrl"
    known = "known" if truth in seen else "new"
    return f"ext_{known}" if truth.startswith("ext:") else f"std_{known}"


def change_kind(old_key: str | None, new_key: str | None, truth: str | None) -> str:
    if old_key is None:
        return f"gained_{verdict(new_key, truth)}"
    if new_key is None:
        return f"lost_{verdict(old_key, truth)}"
    if truth is not None and new_key == truth:
        return "fixed"
    if truth is not None and old_key == truth:
        return "broke"
    return "changed_other"


def remain_rows(
    module: ModuleType, code: str, basis: bool, pdf: Any, xbrl: Any, rows: Sequence[Any],
    truth: dict[int, str | None], seen: set[str],
) -> Iterable[tuple[object, ...]]:
    labels = {f.key: f.label for f in xbrl.seq}
    values = {abs(f.value) for f in xbrl.seq}
    for statement in module.STATEMENTS:
        ms = [m for m in rows if m.statement == statement]
        by_name: dict[str, list[Any]] = {}
        for fact in xbrl.by_statement.get(statement, []):
            by_name.setdefault(module._canon_name(module._clean_label(fact.label)), []).append(fact)
        for i, m in enumerate(ms):
            if m.mapped is not None:
                continue
            t = truth.get(m.fact.id)
            base = module._base(m.fact, statement)
            section = next(
                (x.mapped.key for x in ms[i + 1:] if x.mapped is not None and module._is_marker(x.mapped.key, statement)),
                None,
            )
            prev = ms[i - 1] if i else None
            nxt = ms[i + 1] if i + 1 < len(ms) else None
            same = by_name.get(module._canon_name(base)) or []
            stem = module._total_stem(base)
            if not same and stem:
                same = by_name.get(module._canon_name(stem)) or []
            yield (
                code, int(basis), pdf.period_end, pdf.cls, pdf.standard, statement, m.fact.local, base, m.fact.value,
                unmatched_kind(t, seen), t, labels.get(t) if t else None, section,
                prev.fact.local if prev else None, prev.mapped.key if prev and prev.mapped else None,
                nxt.fact.local if nxt else None, nxt.mapped.key if nxt and nxt.mapped else None,
                "|".join(x.key for x in same), "|".join(str(x.value) for x in same),
                int(abs(m.fact.value) in values),
            )


def wrong_rows(
    module: ModuleType, code: str, basis: bool, pdf: Any, xbrl: Any, view: Sequence[Any], rows: Sequence[Any],
    truth: dict[int, str | None], seen: set[str],
) -> Iterable[tuple[object, ...]]:
    hidden = {}
    for fact in xbrl.seq:
        hidden.setdefault(fact.key, fact)
    known = {}
    for filing in view:
        if not filing.is_pdf:
            for fact in filing.seq:
                known.setdefault(fact.key, fact.label)
    for m in rows:
        t = truth.get(m.fact.id)
        if m.mapped is None or t is None or m.mapped.key == t:
            continue
        key = m.mapped.key
        there = hidden.get(key)
        yield (
            code, int(basis), pdf.period_end, pdf.cls, pdf.standard, m.statement, m.fact.local,
            module._base(m.fact, m.statement), m.fact.value, m.mapped.by,
            key, known.get(key) or (there.label if there else None), int(there is not None),
            there.value if there else None, t, hidden[t].label if t in hidden else None, int(t in seen),
        )


def check_code(
    db: Session, code: str, new: ModuleType, old: ModuleType | None, raw: dict[str, Any] | None,
    defaults: dict[int, Any], periods: int, before_only: bool, stat: Counter,
    changes: TextIO | None, remain: TextIO | None, wrong: TextIO | None,
) -> None:
    both_new = new.load_both(db, code)
    both_old = old.load_both(db, code) if old is not None else {}
    for basis, filings in both_new.items():
        pairs = {pdf.period_end: (pdf, xbrl) for pdf, xbrl in new._pairs(filings)}
        for end in sorted(pairs)[-periods:] if periods else sorted(pairs):
            pdf, xbrl = pairs[end]
            no_book(new, defaults)
            truth = truth_of(new, pdf, xbrl)
            use_book(new, defaults, raw)
            view = hidden_view(filings, end, before_only)
            seen = {f.key for g in view if not g.is_pdf for f in g.seq}
            rows = matches_of(new, db, code, basis, view, pdf.id)
            for m in rows:
                t = truth.get(m.fact.id)
                if m.mapped is None:
                    stat[f"new_unmatched_{unmatched_kind(t, seen)}"] += 1
                else:
                    stat[f"new_matched_{m.mapped.by}_{verdict(m.mapped.key, t)}"] += 1
            for row in remain_rows(new, code, basis, pdf, xbrl, rows, truth, seen):
                write_row(remain, row)
            for row in wrong_rows(new, code, basis, pdf, xbrl, view, rows, truth, seen):
                write_row(wrong, row)
            if old is None:
                continue
            use_book(old, defaults, raw)
            old_rows = {
                m.fact.id: m
                for m in matches_of(old, db, code, basis, hidden_view(both_old.get(basis, []), end, before_only), pdf.id)
            }
            if set(old_rows) != {m.fact.id for m in rows}:
                stat["row_sets_differ"] += 1
            for m in rows:
                before = old_rows.get(m.fact.id)
                t = truth.get(m.fact.id)
                o = before.mapped if before is not None else None
                n = m.mapped
                if o is None:
                    stat[f"old_unmatched_{unmatched_kind(t, seen)}"] += 1
                else:
                    stat[f"old_matched_{o.by}_{verdict(o.key, t)}"] += 1
                old_key = None if o is None else o.key
                new_key = None if n is None else n.key
                if old_key == new_key:
                    continue
                kind = change_kind(old_key, new_key, t)
                stat[f"change_{kind}"] += 1
                write_row(changes, (
                    kind, code, int(basis), end, m.statement, m.fact.local, m.fact.value,
                    old_key, None if o is None else o.by, new_key, None if n is None else n.by, t,
                ))


def run_check(args: argparse.Namespace) -> int:
    new = load_module("pdf_match_check_new", args.new) if args.new else three_statements
    old = load_module("pdf_match_check_old", args.old) if args.old else None
    db = read_only(args.db)
    codes = args.code or [str(r[0]) for r in db.execute(text(
        "SELECT DISTINCT code FROM xbrl_filings WHERE source = 'tdnet_pdf' ORDER BY code"
    ))]
    seen = None
    raw: dict[str, Any] | None = None
    if args.exclude_own:
        seen, _ = label_dictionary.collect(db, label_dictionary.target_codes(db), args.batch)
    elif args.labels:
        raw = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    defaults = default_books(new, old)
    stat: Counter = Counter()
    started = time.monotonic()
    done = failed = 0
    with contextlib.ExitStack() as stack:
        changes = stack.enter_context(open(args.changes, "w", encoding="utf-8")) if args.changes else None
        remain = stack.enter_context(open(args.remain, "w", encoding="utf-8")) if args.remain else None
        wrong = stack.enter_context(open(args.wrong, "w", encoding="utf-8")) if args.wrong else None
        stack.callback(restore_books, defaults, new, old)
        write_row(changes, CHANGE_COLUMNS)
        write_row(remain, REMAIN_COLUMNS)
        write_row(wrong, WRONG_COLUMNS)
        for code in codes:
            if time.monotonic() - started > args.max_seconds:
                break
            if seen is not None:
                raw = label_dictionary.build(seen, args.min_codes, args.min_share, code)
            try:
                check_code(
                    db, code, new, old, raw, defaults, args.periods, args.before_only, stat, changes, remain, wrong,
                )
            except Exception as exc:
                failed += 1
                print(f"pdf_match_check failed code={code} err={type(exc).__name__}: {exc}", flush=True)
            done += 1
            if done % args.batch == 0:
                print(
                    f"pdf_match_check batch done={done}/{len(codes)} failed={failed} "
                    f"sec={time.monotonic() - started:.0f}",
                    flush=True,
                )
    print(f"pdf_match_check done codes={done}/{len(codes)} failed={failed} sec={time.monotonic() - started:.0f}")
    for key in sorted(stat):
        print(f"stat\t{key}\t{stat[key]}")
    return 1 if failed else 0


def run_subset(args: argparse.Namespace) -> int:
    codes = list(args.code)
    if args.codes_file:
        codes += [line.strip() for line in Path(args.codes_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not codes:
        raise SystemExit("--code か --codes-file が要る")
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} は既にある")
    con = sqlite3.connect(out.resolve().as_uri(), uri=True)
    try:
        con.execute("ATTACH DATABASE ? AS src", (f"file:{args.db}?mode=ro",))
        con.execute("CREATE TEMP TABLE want(code TEXT PRIMARY KEY)")
        con.executemany("INSERT OR IGNORE INTO want VALUES (?)", [(c,) for c in codes])
        for name in SUBSET_TABLES:
            (sql,) = con.execute("SELECT sql FROM src.sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
            con.execute(sql)
        con.execute("INSERT INTO stocks SELECT * FROM src.stocks WHERE code IN (SELECT code FROM want)")
        con.execute("INSERT INTO xbrl_filings SELECT * FROM src.xbrl_filings WHERE code IN (SELECT code FROM want)")
        for name in BY_FILING:
            con.execute(f"INSERT INTO {name} SELECT * FROM src.{name} WHERE filing_id IN (SELECT id FROM xbrl_filings)")  # nosec B608
        con.execute("CREATE INDEX subset_facts_filing ON xbrl_facts(filing_id)")
        con.execute(
            "INSERT INTO xbrl_elements SELECT * FROM src.xbrl_elements "
            "WHERE id IN (SELECT DISTINCT element_id FROM xbrl_facts)"
        )
        con.commit()
        for name in SUBSET_TABLES:
            (n,) = con.execute(f"SELECT count(*) FROM {name}").fetchone()  # nosec B608
            print(f"pdf_match_check subset {name}={n}")
    finally:
        con.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--db", required=True)
    check.add_argument("--new", help="比べる新しい版の three_statements.py。無ければこのチェックアウトの版")
    check.add_argument("--old", help="比べる古い版の three_statements.py。渡すと変わった行を数える")
    check.add_argument("--labels", help="標準ラベルの辞書の JSON。無ければ版ごとの既定の辞書")
    check.add_argument("--exclude-own", action="store_true", help="判定する銘柄を除いて作った辞書で測る（全銘柄を先に読む）")
    check.add_argument("--min-codes", type=int, default=label_dictionary.MIN_CODES)
    check.add_argument("--min-share", type=float, default=label_dictionary.MIN_SHARE)
    check.add_argument("--code", action="append", default=[])
    check.add_argument("--periods", type=int, default=3, help="伏せる期の数（新しい方から）。0 で全部")
    check.add_argument("--before-only", action="store_true", help="伏せた期より後の XBRL も使わない")
    check.add_argument("--changes", help="古い版と変わった行の TSV")
    check.add_argument("--remain", help="付かなかった行の TSV")
    check.add_argument("--wrong", help="答えと別の要素に付いた行の TSV")
    check.add_argument("--max-seconds", type=float, default=float("inf"))
    check.add_argument("--batch", type=int, default=200)
    check.set_defaults(run=run_check)
    subset = sub.add_parser("subset")
    subset.add_argument("--db", required=True)
    subset.add_argument("--out", required=True)
    subset.add_argument("--code", action="append", default=[])
    subset.add_argument("--codes-file")
    subset.set_defaults(run=run_subset)
    args = parser.parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.config import EARNINGS_PARAMS as EP
from app.market.analytics import ORDER, align_eps, forecast_revision, is_actual, split_factors
from app.market.revisions import statement_points
from app.market.withdrawals import withdrawn_items
from app.models import Statement
from app.revisions import revision_rows
from tools.market.compare_quarter_flash import readonly_session
from tools.market.compare_withdrawals import column_marks, load_statements, marks_from_xbrl, statement_columns

ALL_YEARS = 1000
EVERY_DAY = date(1900, 1, 1)
RAW_ITEMS = ("FSales", "FOP", "FOdP", "FNP", "FEPS")
FLASH = ("1Q", "3Q")
CATEGORIES = ("marked", "partial_revision", "after_undecided_report", "old_period_report", "other")


def _raw(s) -> dict:
    try:
        raw = json.loads(s.raw_json or "{}")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _given(raw: dict) -> set[str]:
    return {k for k in RAW_ITEMS if raw.get(k) not in (None, "")}


def _is_flash(s) -> bool:
    return s.type_of_document == "EarnForecastRevision" and s.type_of_period in FLASH


def _order(s) -> tuple:
    return (s.disclosed_date, s.disclosed_time or "", s.disclosure_no)


def partial_revisions(stmts) -> list[str]:
    reported: set[str] = set()
    for s in stmts:
        if is_actual(s):
            reported |= _given(_raw(s))
    out = []
    for s in stmts:
        if is_actual(s) or _is_flash(s):
            continue
        given = _given(_raw(s))
        if given and reported - given:
            out.append(s.disclosure_no)
    return out


def old_period_reports(stmts) -> list[str]:
    latest, out = None, []
    for s in sorted(stmts, key=_order):
        n = ORDER.get(s.type_of_period or "")
        if not is_actual(s) or not n or s.fiscal_year_end is None:
            continue
        key = (s.fiscal_year_end, n)
        if latest is not None and key < latest:
            out.append(s.disclosure_no)
        else:
            latest = key
    return out


def after_undecided_report(stmts) -> list[str]:
    out = []
    ordered = sorted(stmts, key=lambda s: (s.disclosed_date, s.disclosure_no or ""))
    for i, stmt in enumerate(ordered):
        undecided = False
        for s in ordered[:i]:
            if s.fiscal_year_end != stmt.fiscal_year_end or _is_flash(s):
                continue
            if s.disclosed_date == stmt.disclosed_date and s.is_revision:
                continue
            if s.fy_operating_profit is not None:
                undecided = False
            elif is_actual(s):
                undecided = True
        if undecided:
            out.append(stmt.disclosure_no)
    return out


def badges(stmts) -> list[list]:
    window = timedelta(days=EP["list_history_days"])
    out = []
    for s in stmts:
        if not is_actual(s):
            continue
        hist = [h for h in stmts if s.disclosed_date - window <= h.disclosed_date <= s.disclosed_date]
        out.append([s.disclosure_no, forecast_revision(hist, s)])
    return out


def export(db, out_path: Path) -> int:
    cols = statement_columns(db)
    marks = column_marks(db, cols)
    source = "withdrawn_forecasts"
    if marks is None:
        marks, source = marks_from_xbrl(db), "xbrl_financials"
    codes = [c for (c,) in db.query(Statement.code).distinct().order_by(Statement.code)]
    upto = date.today()
    splits = split_factors(db, codes, EVERY_DAY, upto)
    with out_path.open("w", encoding="utf-8") as f:
        for code in codes:
            stmts = load_statements(db, cols, code, marks)
            align_eps(stmts, splits.get(code, []), upto)
            row = {"code": code,
                   "history": revision_rows(statement_points(stmts), ALL_YEARS),
                   "badges": badges(stmts),
                   "marked": [s.disclosure_no for s in stmts if withdrawn_items(s)],
                   "marked_operating_profit": [s.disclosure_no for s in stmts
                                               if "operating_profit" in withdrawn_items(s)],
                   "partial_revision": partial_revisions(stmts),
                   "after_undecided_report": after_undecided_report(stmts),
                   "old_period_report": old_period_reports(stmts)}
            f.write(json.dumps(row, ensure_ascii=False, default=str, sort_keys=True) + "\n")
            db.expire_all()
    print(f"codes={len(codes)} marks_source={source} marked_disclosures={len(marks)} out={out_path}")
    return 0


def _rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {r["code"]: r for r in map(json.loads, f)}


def _strs(rows: list) -> list[str]:
    return [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]


def classify(old: dict, new: dict) -> tuple[str, list[tuple[str, str, str]]]:
    lines = []
    for part in ("history", "badges"):
        a, b = _strs(old[part]), _strs(new[part])
        lines += [(part, "old", x) for x in a if x not in b]
        lines += [(part, "new", x) for x in b if x not in a]
    history = any(p == "history" for p, _, _ in lines)
    changed_badges = {json.loads(x)[0] for p, _, x in lines if p == "badges"}
    if new["marked"]:
        return "marked", lines
    if changed_badges - set(new["after_undecided_report"]):
        return "other", lines
    if not history:
        return "after_undecided_report", lines
    if new["partial_revision"]:
        return "partial_revision", lines
    if new["old_period_report"]:
        return "old_period_report", lines
    return "other", lines


def compare(old_path: Path, new_path: Path, show: bool) -> int:
    old, new = _rows(old_path), _rows(new_path)
    if sorted(old) != sorted(new):
        print(f"codes differ: only_old={sorted(set(old) - set(new))[:20]} only_new={sorted(set(new) - set(old))[:20]}")
        return 1
    found: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for code in sorted(new):
        if old[code]["history"] == new[code]["history"] and old[code]["badges"] == new[code]["badges"]:
            continue
        category, lines = classify(old[code], new[code])
        found[category].append(code)
        if show:
            print(f"changed code={code} category={category}")
            for part, side, line in lines:
                print(f"  {part} {side} {line}")
    print(f"codes={len(new)} changed={sum(len(v) for v in found.values())}")
    for category in CATEGORIES:
        codes = found[category]
        print(f"{category}={len(codes)} codes={','.join(codes)}")
    return 1 if found["other"] else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="KAB-551: 全銘柄の修正履歴と一覧の予想修正バッジを 1 行 1 銘柄の JSON Lines で書き出し、"
                    "載せ替え前後の 2 つを比べる。書き出しは PYTHONPATH の木の statement_points・revision_rows・"
                    "forecast_revision を使い、DB は読み取り専用で開く。")
    sub = ap.add_subparsers(dest="mode", required=True)
    ex = sub.add_parser("export", help="書き出す")
    ex.add_argument("db", type=Path, help="SQLite の stock.db のパス")
    ex.add_argument("out", type=Path, help="書き出す JSON Lines のパス")
    cmp = sub.add_parser("compare", help="2 つの書き出しを比べる。分けられない違いがあれば終了コード 1")
    cmp.add_argument("old", type=Path)
    cmp.add_argument("new", type=Path)
    cmp.add_argument("--show", action="store_true", help="違いの行を出す")
    args = ap.parse_args()
    if args.mode == "compare":
        sys.exit(compare(args.old, args.new, args.show))
    db = readonly_session(args.db)
    try:
        sys.exit(export(db, args.out))
    finally:
        db.close()


if __name__ == "__main__":
    main()

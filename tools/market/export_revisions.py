from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import EARNINGS_PARAMS as EP
from app.market.analytics import ORDER, align_eps, forecast_revision, is_actual, split_factors
from app.market.chart_fund import fund_steps
from app.market.revisions import statement_points
from app.models import DailyPrice, Statement
from app.revisions import revision_rows

ALL_YEARS = 1000
EVERY_DAY = date(1900, 1, 1)
MARK_COLUMN = "withdrawn_forecasts"
FORECAST_COLUMNS = ("fy_net_sales", "fy_operating_profit", "fy_ordinary_profit", "fy_profit", "fy_eps")
RAW_CURRENT = dict(zip(FORECAST_COLUMNS, ("FSales", "FOP", "FOdP", "FNP", "FEPS")))
FLASH = ("1Q", "3Q")
PARTS = ("history", "badges", "chart")
CATEGORIES = ("marked", "columns_without_raw", "partial_revision", "after_undecided_report", "old_period_report",
              "same_day_order", "fiscal_year_change", "other")


def readonly_session(path: Path) -> Session:
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    engine = create_engine(f"sqlite:///file:{path.resolve()}?mode=ro&uri=true", connect_args={"uri": True})
    return Session(engine, autoflush=False)


def statement_columns(db: Session) -> list:
    have = {r[1] for r in db.execute(text("PRAGMA table_info(statements)"))}
    return [c for c in Statement.__table__.columns if c.name in have]


def marks_from_xbrl(db: Session) -> dict[str, tuple[str, ...]] | None:
    try:
        from app.xbrl.statements import (REVISION_DOC_TYPES, disclosure_no_of, filings_since,
                                         notice_filings_since, withdrawals_by_filing)
    except ImportError:
        return None
    filings = [f for f in filings_since(db, EVERY_DAY) if f.doc_type in REVISION_DOC_TYPES]
    filings += notice_filings_since(db, None)
    by_id = withdrawals_by_filing(db, filings)
    return {disclosure_no_of(f.doc_id): by_id[f.id] for f in filings if f.id in by_id}


def column_marks(db: Session, cols) -> dict[str, tuple[str, ...]] | None:
    if MARK_COLUMN not in {c.name for c in cols}:
        return None
    col = Statement.__table__.c[MARK_COLUMN]
    rows = db.execute(select(Statement.disclosure_no, col).where(col.isnot(None))).all()
    return {no: items for no, items in rows if items} or None


def load_marks(db: Session, cols) -> tuple[dict[str, tuple[str, ...]], str]:
    marks = column_marks(db, cols)
    if marks is not None:
        return marks, MARK_COLUMN
    marks = marks_from_xbrl(db)
    return ({}, "none") if marks is None else (marks, "xbrl_financials")


def load_statements(db: Session, cols, code: str, marks: dict[str, tuple[str, ...]]) -> list[SimpleNamespace]:
    names = [c.name for c in Statement.__table__.columns]
    rows = db.execute(select(*cols).where(Statement.code == code)
                      .order_by(Statement.disclosed_date, Statement.disclosure_no)).mappings().all()
    out = []
    for r in rows:
        s = SimpleNamespace(**{n: r.get(n) for n in names})
        s.withdrawn_forecasts = marks.get(s.disclosure_no)
        out.append(s)
    return out


def load_prices(db: Session, code: str) -> tuple[list[date], list[float | None]]:
    rows = db.execute(select(DailyPrice.date, DailyPrice.adj_factor).where(DailyPrice.code == code)
                      .order_by(DailyPrice.date)).all()
    return [d for d, _ in rows], [f for _, f in rows]


def withdrawn_reader():
    try:
        from app.market.withdrawals import withdrawn_items
    except ImportError:
        return lambda s: frozenset(getattr(s, MARK_COLUMN, None) or ())
    return withdrawn_items


def columns_without_raw(stmts) -> list[str]:
    out = []
    for s in stmts:
        if "FinancialStatements" in (s.type_of_document or ""):
            continue
        try:
            raw = json.loads(s.raw_json or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        if any(getattr(s, col, None) is not None and raw.get(key) in (None, "") for col, key in RAW_CURRENT.items()):
            out.append(s.disclosure_no)
    return out


def _given(s) -> set[str]:
    return {c for c in FORECAST_COLUMNS if getattr(s, c, None) is not None}


def _is_flash(s) -> bool:
    return s.type_of_document == "EarnForecastRevision" and s.type_of_period in FLASH


def _order(s) -> tuple:
    return (s.disclosed_date, s.disclosed_time or "", s.disclosure_no)


def partial_revisions(stmts) -> list[str]:
    reported: set[str] = set()
    for s in stmts:
        if is_actual(s):
            reported |= _given(s)
    out = []
    for s in stmts:
        if is_actual(s) or _is_flash(s):
            continue
        given = _given(s)
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


def fiscal_year_changes(stmts) -> list[str]:
    ends: dict[date, set[date]] = {}
    for s in stmts:
        start = getattr(s, "fiscal_year_start", None)
        if is_actual(s) and start is not None and s.fiscal_year_end is not None:
            ends.setdefault(start, set()).add(s.fiscal_year_end)
    return [s.disclosure_no for s in sorted(stmts, key=_order)
            if is_actual(s) and len(ends.get(getattr(s, "fiscal_year_start", None), ())) > 1]


def same_day_out_of_order(stmts) -> list[str]:
    by_day: dict[date, list] = {}
    for s in stmts:
        by_day.setdefault(s.disclosed_date, []).append(s)
    out = []
    for day_stmts in by_day.values():
        by_no = sorted(day_stmts, key=lambda s: s.disclosure_no or "")
        if by_no != sorted(by_no, key=lambda s: s.disclosed_time or ""):
            out += [s.disclosure_no for s in by_no]
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


def chart_steps(stmts, days, factors) -> list[list]:
    f = fund_steps(stmts, days, factors)
    return [[t, eps] for t, eps in zip(f["t"], f["eps"])]


def export(db, out_path: Path) -> int:
    cols = statement_columns(db)
    marks, source = load_marks(db, cols)
    withdrawn = withdrawn_reader()
    codes = [c for (c,) in db.query(Statement.code).distinct().order_by(Statement.code)]
    upto = date.today()
    splits = split_factors(db, codes, EVERY_DAY, upto)
    with out_path.open("w", encoding="utf-8") as f:
        for code in codes:
            stmts = load_statements(db, cols, code, marks)
            chart = chart_steps(stmts, *load_prices(db, code))
            align_eps(stmts, splits.get(code, []), upto)
            row = {"code": code,
                   "history": revision_rows(statement_points(stmts), ALL_YEARS),
                   "badges": badges(stmts),
                   "chart": chart,
                   "marked": [s.disclosure_no for s in stmts if withdrawn(s)],
                   "columns_without_raw": columns_without_raw(stmts),
                   "partial_revision": partial_revisions(stmts),
                   "after_undecided_report": after_undecided_report(stmts),
                   "old_period_report": old_period_reports(stmts),
                   "same_day_order": same_day_out_of_order(stmts),
                   "fiscal_year_change": fiscal_year_changes(stmts)}
            f.write(json.dumps(row, ensure_ascii=False, default=str, sort_keys=True) + "\n")
            db.expire_all()
    print(f"codes={len(codes)} marks_source={source} marked_disclosures={len(marks)} out={out_path}")
    return 0


def _rows(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as f:
        return {r["code"]: r for r in map(json.loads, f)}


def _strs(rows: list) -> list[str]:
    return [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]


def changed_lines(old: dict, new: dict) -> list[tuple[str, str, str]]:
    lines = []
    for part in PARTS:
        a, b = _strs(old.get(part, [])), _strs(new.get(part, []))
        lines += [(part, "old", x) for x in a if x not in b]
        lines += [(part, "new", x) for x in b if x not in a]
    return lines


def classify(new: dict, lines: list[tuple[str, str, str]]) -> str:
    parts = {p for p, _, _ in lines}
    if new["marked"]:
        return "marked"
    changed_badges = {json.loads(x)[0] for p, _, x in lines if p == "badges"}
    if changed_badges - set(new["after_undecided_report"]):
        return "other"
    chart_reasons = [c for c in ("columns_without_raw", "fiscal_year_change") if new.get(c)]
    if "chart" in parts and not chart_reasons:
        return "other"
    history_reasons = [c for c in ("partial_revision", "old_period_report", "same_day_order", "fiscal_year_change")
                       if new.get(c)]
    if "history" in parts and not history_reasons:
        return "other"
    if "chart" in parts:
        return chart_reasons[0]
    if "history" in parts:
        return history_reasons[0]
    return "after_undecided_report"


def compare(old_path: Path, new_path: Path, show: bool) -> int:
    old, new = _rows(old_path), _rows(new_path)
    if sorted(old) != sorted(new):
        print(f"codes differ: only_old={sorted(set(old) - set(new))[:20]} only_new={sorted(set(new) - set(old))[:20]}")
        return 1
    found: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    by_part: dict[str, int] = {p: 0 for p in PARTS}
    for code in sorted(new):
        lines = changed_lines(old[code], new[code])
        if not lines:
            continue
        category = classify(new[code], lines)
        found[category].append(code)
        parts = [p for p in PARTS if any(x[0] == p for x in lines)]
        for p in parts:
            by_part[p] += 1
        print(f"changed code={code} category={category} parts={','.join(parts)}")
        if show:
            for part, side, line in lines:
                print(f"  {part} {side} {line}")
    print(f"codes={len(new)} changed={sum(len(v) for v in found.values())} "
          + " ".join(f"{p}_codes={n}" for p, n in by_part.items()))
    for category in CATEGORIES:
        codes = found[category]
        print(f"{category}={len(codes)} codes={','.join(codes)}")
    return 1 if found["other"] else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="KAB-551: 全銘柄の修正履歴・一覧の予想修正バッジ・株価チャートの予想 EPS の階段を 1 行 1 銘柄の "
                    "JSON Lines で書き出し、載せ替え前後の 2 つを比べる。書き出しは PYTHONPATH の木の "
                    "statement_points・revision_rows・forecast_revision・fund_steps を使い、DB は読み取り専用で開く。")
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

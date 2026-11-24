from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.stock import _dividend_forecast
from app.market.chart_fund import fund_steps
from app.market.withdrawals import no_withdrawals, withdrawn_items
from app.models import WITHDRAWABLE_ITEMS, DailyPrice, Statement
from app.xbrl.statements import (REVISION_DOC_TYPES, disclosure_no_of, filings_since, notice_filings_since,
                                 withdrawals_by_filing)
from tools.market.compare_quarter_flash import diff_count, readonly_session

COLUMN = "withdrawn_forecasts"
EVERY_FILING = date(1900, 1, 1)


def statement_columns(db: Session) -> list:
    have = {r[1] for r in db.execute(text("PRAGMA table_info(statements)"))}
    return [c for c in Statement.__table__.columns if c.name in have]


def marks_from_xbrl(db: Session) -> dict[str, tuple[str, ...]]:
    filings = [f for f in filings_since(db, EVERY_FILING) if f.doc_type in REVISION_DOC_TYPES]
    filings += notice_filings_since(db, None)
    by_id = withdrawals_by_filing(db, filings)
    return {disclosure_no_of(f.doc_id): by_id[f.id] for f in filings if f.id in by_id}


def column_marks(db: Session, cols) -> dict[str, tuple[str, ...]] | None:
    if COLUMN not in {c.name for c in cols}:
        return None
    col = Statement.__table__.c[COLUMN]
    rows = db.execute(select(Statement.disclosure_no, col).where(col.isnot(None))).all()
    return {no: items for no, items in rows if items} or None


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


def compute(stmts, days, factors, withdrawn) -> tuple[list[tuple], float | None]:
    f = fund_steps(stmts, days, factors, withdrawn)
    return list(zip(f["t"], f["eps"])), _dividend_forecast(stmts, days, factors, withdrawn)


def compare(db: Session, show: bool) -> int:
    cols = statement_columns(db)
    marks = column_marks(db, cols)
    source = COLUMN
    if marks is None:
        marks, source = marks_from_xbrl(db), "xbrl_financials"
    print(f"marks_source={source} column={'yes' if COLUMN in {c.name for c in cols} else 'no'} "
          f"marked_disclosures={len(marks)}")
    codes = [c for (c,) in db.query(Statement.code).distinct().order_by(Statement.code)]
    marked: dict[str, list] = {}
    changed: dict[str, dict[str, int]] = {}
    for code in codes:
        stmts = load_statements(db, cols, code, marks)
        for s in stmts:
            items = withdrawn_items(s)
            if items:
                marked.setdefault(code, []).append((s.disclosure_no, s.disclosed_date, items))
        px = (db.query(DailyPrice.date, DailyPrice.adj_factor)
              .filter(DailyPrice.code == code).order_by(DailyPrice.date).all())
        days, factors = [d for d, _ in px], [f for _, f in px]
        old_eps, old_div = compute(stmts, days, factors, no_withdrawals)
        new_eps, new_div = compute(stmts, days, factors, withdrawn_items)
        counts = {"eps": diff_count(old_eps, new_eps), "dividend": int(old_div != new_div)}
        if any(counts.values()):
            changed[code] = counts
            print(f"changed code={code} eps_steps={counts['eps']} dividend={old_div}->{new_div} "
                  f"marked={len(marked.get(code, []))}")
            if show:
                for row in sorted(set(old_eps) - set(new_eps), key=str):
                    print(f"  eps old {row}")
                for row in sorted(set(new_eps) - set(old_eps), key=str):
                    print(f"  eps new {row}")
        db.expire_all()
    for code, rows in sorted(marked.items()):
        for no, day, items in rows:
            print(f"marked code={code} disclosure_no={no} disclosed={day} "
                  f"items={','.join(i for i in WITHDRAWABLE_ITEMS if i in items)}")
    eps_total = sum(c["eps"] for c in changed.values())
    div_total = sum(c["dividend"] for c in changed.values())
    print(f"codes={len(codes)} changed_codes={len(changed)} eps_steps={eps_total} dividend_codes={div_total}")
    outside = sorted(set(changed) - set(marked))
    print(f"changed_codes_within_marked_codes={not outside} changed={len(changed)} marked={len(marked)} "
          f"outside={len(outside)}")
    for code in outside:
        print(f"outside code={code}")
    return 1 if outside else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="KAB-551: 取り下げの印を使わない（載せ替え前と同じ）予想と、印を使う予想で、"
                    "予想EPS の階段と年間配当予想を全銘柄で比べる。DB は読み取り専用で開く。"
                    "statements に印の列が無い・空のときは xbrl_financials から取込と同じ判定で印を作る。")
    ap.add_argument("db", type=Path, help="SQLite の stock.db のパス")
    ap.add_argument("--show", action="store_true", help="違いの行を出す")
    args = ap.parse_args()
    db = readonly_session(args.db)
    try:
        sys.exit(compare(db, args.show))
    finally:
        db.close()


if __name__ == "__main__":
    main()

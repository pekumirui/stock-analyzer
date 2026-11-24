from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.orm import Session

from app.api.stock import _dividend_forecast
from app.market.chart_fund import fund_steps
from app.market.withdrawals import no_withdrawals, withdrawn_items
from app.models import WITHDRAWABLE_ITEMS, DailyPrice, Statement
from tools.market.compare_quarter_flash import diff_count, readonly_session
from tools.market.export_revisions import (MARK_COLUMN, column_marks, columns_without_raw, load_statements,
                                           marks_from_xbrl, statement_columns)

def compute(stmts, days, factors, withdrawn) -> tuple[list[tuple], float | None]:
    f = fund_steps(stmts, days, factors, withdrawn)
    return list(zip(f["t"], f["eps"])), _dividend_forecast(stmts, days, factors, withdrawn)


def compare(db: Session, show: bool) -> int:
    cols = statement_columns(db)
    marks = column_marks(db, cols)
    source = MARK_COLUMN
    if marks is None:
        marks, source = marks_from_xbrl(db), "xbrl_financials"
    print(f"marks_source={source} column={'yes' if MARK_COLUMN in {c.name for c in cols} else 'no'} "
          f"marked_disclosures={len(marks)}")
    codes = [c for (c,) in db.query(Statement.code).distinct().order_by(Statement.code)]
    marked: dict[str, list] = {}
    unread: dict[str, list[str]] = {}
    changed: dict[str, dict[str, int]] = {}
    for code in codes:
        stmts = load_statements(db, cols, code, marks)
        for s in stmts:
            items = withdrawn_items(s)
            if items:
                marked.setdefault(code, []).append((s.disclosure_no, s.disclosed_date, items))
        found = columns_without_raw(stmts)
        if found:
            unread[code] = found
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
    for code, nos in sorted(unread.items()):
        print(f"columns_without_raw code={code} disclosure_no={','.join(nos)}")
    eps_total = sum(c["eps"] for c in changed.values())
    div_total = sum(c["dividend"] for c in changed.values())
    print(f"codes={len(codes)} changed_codes={len(changed)} eps_steps={eps_total} dividend_codes={div_total}")
    in_marked = sorted(set(changed) & set(marked))
    in_columns = sorted(set(changed) - set(marked) & set(unread))
    outside = sorted(set(changed) - set(marked) - set(unread))
    print(f"changed_codes_within_marked_codes={not outside} changed={len(changed)} marked={len(marked)} "
          f"outside={len(outside)}")
    print(f"changed_marked={len(in_marked)} changed_columns_without_raw={len(in_columns)} "
          f"columns_without_raw_codes={len(unread)} changed_other={len(outside)}")
    for code in in_columns:
        print(f"columns_without_raw_changed code={code}")
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

from __future__ import annotations

import argparse
import sys
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import EARNINGS_PARAMS as EP
from app.market import analytics, chart_fund, revisions
from app.market.analytics import forecast_revision, is_quarter_flash_revision
from app.market.chart_fund import fund_steps
from app.market.revisions import statement_points
from app.models import DailyPrice, Statement
from app.revisions import revision_rows

PREDICATE_USERS = (analytics, chart_fund, revisions)
ALL_YEARS = 1000


@contextmanager
def flash_as_full_year_forecast():
    with ExitStack() as stack:
        for module in PREDICATE_USERS:
            stack.enter_context(patch.object(module, "is_quarter_flash_revision", lambda s: False))
        yield


def readonly_session(path: Path) -> Session:
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    engine = create_engine(f"sqlite:///file:{path.resolve()}?mode=ro&uri=true",
                           connect_args={"uri": True})
    return Session(engine, autoflush=False)


def eps_steps(stmts, days, factors) -> list[tuple]:
    f = fund_steps(stmts, days, factors)
    return list(zip(f["t"], f["eps"]))


def history_rows(stmts) -> list[tuple]:
    return [(r["fiscal_year_end"], r["kind"], r["disclosed_date"], tuple(sorted(r["values"].items())))
            for r in revision_rows(statement_points(stmts), ALL_YEARS)]


def revision_marks(stmts) -> list[tuple]:
    window = timedelta(days=EP["list_history_days"])
    out = []
    for s in stmts:
        hist = [h for h in stmts if s.disclosed_date - window <= h.disclosed_date <= s.disclosed_date]
        out.append((s.disclosure_no, repr(forecast_revision(hist, s))))
    return out


def compute(stmts, days, factors) -> dict[str, list[tuple]]:
    return {"eps": eps_steps(stmts, days, factors),
            "history": history_rows(stmts),
            "revision": revision_marks(stmts)}


def diff_count(old: list[tuple], new: list[tuple]) -> int:
    a, b = Counter(old), Counter(new)
    return sum(((a - b) + (b - a)).values())


def compare(db: Session, show: bool) -> int:
    codes = [c for (c,) in db.query(Statement.code).distinct().order_by(Statement.code)]
    target_rows: Counter[str] = Counter()
    changed: dict[str, dict[str, int]] = {}
    for code in codes:
        stmts = (db.query(Statement).filter(Statement.code == code)
                 .order_by(Statement.disclosed_date, Statement.disclosure_no).all())
        for s in stmts:
            if is_quarter_flash_revision(s):
                target_rows[code] += 1
        px = (db.query(DailyPrice.date, DailyPrice.adj_factor)
              .filter(DailyPrice.code == code).order_by(DailyPrice.date).all())
        days, factors = [d for d, _ in px], [f for _, f in px]
        new = compute(stmts, days, factors)
        with flash_as_full_year_forecast():
            old = compute(stmts, days, factors)
        counts = {k: diff_count(old[k], new[k]) for k in new}
        if any(counts.values()):
            changed[code] = counts
            print(f"changed code={code} eps_steps={counts['eps']} history_rows={counts['history']} "
                  f"forecast_revision={counts['revision']} flash_rows={target_rows[code]}")
            if show:
                for k in new:
                    for row in sorted(set(old[k]) - set(new[k])):
                        print(f"  {k} old {row}")
                    for row in sorted(set(new[k]) - set(old[k])):
                        print(f"  {k} new {row}")
        db.expunge_all()
    totals = {k: sum(c[k] for c in changed.values()) for k in ("eps", "history", "revision")}
    print(f"codes={len(codes)} changed_codes={len(changed)} eps_steps={totals['eps']} "
          f"history_rows={totals['history']} forecast_revision={totals['revision']}")
    print(f"flash_rows={sum(target_rows.values())} flash_codes={len(target_rows)}")
    outside = sorted(set(changed) - set(target_rows))
    print(f"changed_codes_within_flash_codes={not outside} changed={len(changed)} flash={len(target_rows)} "
          f"outside={len(outside)}")
    for code in outside:
        print(f"outside code={code}")
    return 1 if outside else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="KAB-553: 1Q・3Q 型の EarnForecastRevision を通期予想として使う修正前と修正後で、"
                    "予想EPS の階段・修正履歴・forecast_revision を全銘柄で比べる。DB は読み取り専用で開く。")
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

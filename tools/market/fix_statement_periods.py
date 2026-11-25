"""config/market/statement_period_fixes.json の開示の期の日付を、DB の raw_json から作り直して直す。

J-Quants は引き直さない。直すのは一覧にある開示の period_start / period_end / fiscal_year_start /
fiscal_year_end だけ（ほかの列・ほかの行は触らない）。`"drop": true` の開示は statements の行を消し、
同じ銘柄・同じ開示日の statements がほかに無くなれば earnings_events の行も消す。短信 PDF の本文は残す。
既定は変わる行・消す行を出すだけ。

  python tools/market/fix_statement_periods.py               # 変わる行を出す（書かない）
  python tools/market/fix_statement_periods.py --write       # 書く
  python tools/market/fix_statement_periods.py --candidates  # DB 全体から期が 1 年前の疑いがある開示を出す（書かない）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.market.ingest import _d, _statement_row
from app.market.period_fixes import (
    LATE_DAYS,
    PERIOD_COLUMNS,
    default_dropped_disclosures,
    default_period_fixes,
    period_candidates,
)
from app.models import EarningsEvent, Statement
from app.xbrl.statements import is_xbrl_raw


@dataclass(frozen=True)
class Planned:
    disclosure_no: str
    code: str | None
    status: str
    before: dict[str, date | None]
    after: dict[str, date | None]

    def line(self) -> str:
        cols = " ".join(
            f"{c}={_iso(self.before.get(c))}->{_iso(self.after.get(c))}"
            for c in PERIOD_COLUMNS if self.before.get(c) != self.after.get(c)
        )
        return f"{self.status} disclosure_no={self.disclosure_no} code={self.code or '-'} {cols}".rstrip()


def _iso(d: date | None) -> str:
    return d.isoformat() if d else "-"


def plan(
    db: Session, fixes: dict[str, date] | None = None, dropped: frozenset[str] | None = None,
) -> list[Planned]:
    """日付補正の開示ごとに changed / unchanged / missing / xbrl-raw / mismatch、drop の開示ごとに drop / missing。"""
    table = default_period_fixes() if fixes is None else fixes
    drops = default_dropped_disclosures() if dropped is None else dropped
    out: list[Planned] = []
    for no, recorded in table.items():
        st = db.get(Statement, no)
        if st is None:
            out.append(Planned(no, None, "missing", {}, {}))
            continue
        before = {c: getattr(st, c) for c in PERIOD_COLUMNS}
        if not st.raw_json or is_xbrl_raw(st.raw_json):
            out.append(Planned(no, st.code, "xbrl-raw", before, before))
            continue
        raw = json.loads(st.raw_json)
        if _d(raw.get("CurPerEn")) != recorded:
            out.append(Planned(no, st.code, "mismatch", before, before))
            continue
        row = _statement_row(raw, table)
        after = {c: row[c] for c in PERIOD_COLUMNS}
        out.append(Planned(no, st.code, "changed" if after != before else "unchanged", before, after))
    for no in sorted(drops):
        st = db.get(Statement, no)
        out.append(Planned(no, None, "missing", {}, {}) if st is None else Planned(no, st.code, "drop", {}, {}))
    return out


def _delete_dropped(db: Session, disclosure_no: str) -> None:
    st = db.get(Statement, disclosure_no)
    code, disclosed = st.code, st.disclosed_date
    db.delete(st)
    db.flush()
    left = db.query(Statement.disclosure_no).filter(
        Statement.code == code, Statement.disclosed_date == disclosed).first()
    if left is None:
        db.query(EarningsEvent).filter(
            EarningsEvent.code == code, EarningsEvent.disclosed_date == disclosed).delete()


def write(db: Session, planned: list[Planned]) -> int:
    n = 0
    for p in planned:
        if p.status == "drop":
            _delete_dropped(db, p.disclosure_no)
            n += 1
            continue
        if p.status != "changed":
            continue
        st = db.get(Statement, p.disclosure_no)
        for col, value in p.after.items():
            setattr(st, col, value)
        n += 1
    db.commit()
    return n


def scan_candidates(db: Session, fixes: dict[str, date] | None = None):
    """提出日が期末から LATE_DAYS 日以上あとの行だけ raw_json を読んで、取込の WARNING と同じ条件で拾う。"""
    S = Statement
    late = (
        db.query(S.disclosure_no, S.raw_json)
        .filter(S.period_end.is_not(None), S.disclosed_date.is_not(None),
                func.julianday(S.disclosed_date) - func.julianday(S.period_end) >= LATE_DAYS)
        .order_by(S.disclosed_date, S.disclosure_no)
        .all()
    )
    items = []
    for _no, raw_json in late:
        if not raw_json or is_xbrl_raw(raw_json):
            continue
        raw = json.loads(raw_json)
        row = _statement_row(raw, {})
        if row["disclosed_date"] is None:
            continue
        items.append((row, _d(raw.get("NxtFYSt"))))
    return period_candidates(db, items, fixes=fixes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="変わる行を DB に書く")
    ap.add_argument("--candidates", action="store_true", help="DB 全体から候補を出す（書かない）")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        if args.candidates:
            found = scan_candidates(db)
            for c in found:
                print(c.line().removeprefix("WARNING "), flush=True)
            print(f"statement_period_candidates n={len(found)}", flush=True)
            return 0
        planned = plan(db)
        for p in planned:
            print(p.line(), flush=True)
        counts = {s: sum(1 for p in planned if p.status == s)
                  for s in ("changed", "unchanged", "missing", "xbrl-raw", "mismatch", "drop")}
        written = write(db, planned) if args.write else 0
        parts = " ".join(f"{k.replace('-', '_')}={v}" for k, v in counts.items())
        print(f"statement_period_fixes listed={len(planned)} {parts} written={written}", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

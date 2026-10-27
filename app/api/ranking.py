"""ランキングタブ。/ranking/earnings-perf。決算月ごとの決算発表後パフォーマンス（現在まで）。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..market.analytics import is_after_close
from ..models import DailyPrice, EarningsEvent, Stock


router = APIRouter()

MONTHS_LIMIT = 36


def _month_ok(s: str) -> bool:
    try:
        y, m = s.split("-")
        date(int(y), int(m), 1)
    except ValueError:
        return False
    return len(y) == 4 and len(m) == 2


@router.get("/ranking/earnings-perf")
def earnings_perf(month: str | None = None, db: Session = Depends(get_session)):
    """その期の最初の決算短信（earnings_events.is_first。日次バッチが入れる）だけを並べる。

    表示のたびに statements（raw_json を持つ重い表）を読まない。
    """
    if month is not None and not _month_ok(month):
        raise HTTPException(400, "month は YYYY-MM")
    ym = func.strftime("%Y-%m", EarningsEvent.disclosed_date)
    months = [m for (m,) in db.query(ym).filter(EarningsEvent.is_first.is_(True))
              .distinct().order_by(ym.desc()).limit(MONTHS_LIMIT)]
    pick = month or (months[0] if months else None)
    as_of = db.query(func.max(DailyPrice.date)).scalar()
    out = {"months": months, "month": pick, "as_of": as_of.isoformat() if as_of else None, "rows": []}
    if pick is None:
        return out
    y, m = (int(x) for x in pick.split("-"))
    start, end = date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)
    evs = (db.query(EarningsEvent)
           .filter(EarningsEvent.is_first.is_(True),
                   EarningsEvent.disclosed_date >= start, EarningsEvent.disclosed_date < end).all())
    names = dict(db.query(Stock.code, Stock.name).filter(Stock.code.in_({e.code for e in evs})).all())
    today = date.today()
    rows = [{
        "code": e.code, "name": names.get(e.code) or e.code,
        "disclosed_date": e.disclosed_date.isoformat(), "disclosed_time": (e.disclosed_time or "")[:5] or None,
        "session": "after" if is_after_close(e.disclosed_date, e.disclosed_time) else "intraday",
        "base_date": e.base_date.isoformat() if e.base_date else None, "base_price": e.base_price,
        "return_to_date": e.return_to_date, "alpha_to_date": e.alpha_to_date,
        "days": (today - e.disclosed_date).days,
    } for e in evs]
    rows.sort(key=lambda r: (r["return_to_date"] is None, -(r["return_to_date"] or 0), r["code"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {**out, "rows": rows}

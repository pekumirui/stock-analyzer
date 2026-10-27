"""ランキングタブ。/ranking/earnings-perf。決算月ごとの決算発表後パフォーマンス（現在まで）。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..market.analytics import is_after_close
from ..models import DailyPrice, EarningsEvent, Statement, Stock


router = APIRouter()

MONTHS_LIMIT = 36


def _first_disclosures(db: Session):
    """1 期（銘柄 × 決算期 × 四半期）につき最初の決算短信の発表日。訂正は起点にしない。"""
    return (db.query(Statement.code, Statement.fiscal_year_end, Statement.type_of_period,
                     func.min(Statement.disclosed_date).label("d"))
            .filter(Statement.is_revision.isnot(True))
            .group_by(Statement.code, Statement.fiscal_year_end, Statement.type_of_period)
            .subquery())


def _month_ok(s: str) -> bool:
    try:
        y, m = s.split("-")
        date(int(y), int(m), 1)
    except ValueError:
        return False
    return len(y) == 4 and len(m) == 2


@router.get("/ranking/earnings-perf")
def earnings_perf(month: str | None = None, db: Session = Depends(get_session)):
    first = _first_disclosures(db)
    ym = func.strftime("%Y-%m", first.c.d)
    months = [m for (m,) in db.query(ym).distinct().order_by(ym.desc()).limit(MONTHS_LIMIT)]
    if month is not None and not _month_ok(month):
        raise HTTPException(400, "month は YYYY-MM")
    pick = month or (months[0] if months else None)
    as_of = db.query(func.max(DailyPrice.date)).scalar()
    out = {"months": months, "month": pick, "as_of": as_of.isoformat() if as_of else None, "rows": []}
    if pick is None:
        return out
    y, m = (int(x) for x in pick.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    days = (db.query(first.c.code, first.c.d).filter(first.c.d >= start, first.c.d < end)
            .distinct().subquery())
    times = (db.query(Statement.code, Statement.disclosed_date,
                      func.min(Statement.disclosed_time).label("t"))
             .filter(Statement.is_revision.isnot(True))
             .group_by(Statement.code, Statement.disclosed_date).subquery())
    recs = (db.query(EarningsEvent, Stock.name, times.c.t)
            .join(days, (days.c.code == EarningsEvent.code) & (days.c.d == EarningsEvent.disclosed_date))
            .outerjoin(Stock, Stock.code == EarningsEvent.code)
            .outerjoin(times, (times.c.code == EarningsEvent.code)
                       & (times.c.disclosed_date == EarningsEvent.disclosed_date))
            .all())
    today = date.today()
    rows = [{
        "code": e.code, "name": name or e.code,
        "disclosed_date": e.disclosed_date.isoformat(), "disclosed_time": (t or "")[:5] or None,
        "session": "after" if is_after_close(e.disclosed_date, t) else "intraday",
        "base_date": e.base_date.isoformat() if e.base_date else None, "base_price": e.base_price,
        "return_to_date": e.return_to_date, "alpha_to_date": e.alpha_to_date,
        "days": (today - e.disclosed_date).days,
    } for e, name, t in recs]
    rows.sort(key=lambda r: (r["return_to_date"] is None, -(r["return_to_date"] or 0), r["code"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {**out, "rows": rows}

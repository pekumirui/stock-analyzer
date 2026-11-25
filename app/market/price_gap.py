from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import DailyPrice

PRIOR_DAYS = 5
MIN_RATIO = 0.5


def price_gap_line(db: Session, day: date, *, zero_is_gap: bool) -> str | None:
    if day.weekday() >= 5:
        return None
    count = int(db.query(func.count()).filter(DailyPrice.date == day).scalar() or 0)
    if count == 0 and not zero_is_gap:
        return None
    rows = (
        db.query(DailyPrice.date, func.count())
        .filter(DailyPrice.date < day)
        .group_by(DailyPrice.date)
        .order_by(DailyPrice.date.desc())
        .limit(PRIOR_DAYS)
        .all()
    )
    if not rows:
        return None
    counts = sorted(int(n) for _, n in rows)
    median = counts[len(counts) // 2]
    if median <= 0 or count >= median * MIN_RATIO:
        return None
    return (
        f"gap daily_prices date={day.isoformat()} count={count} "
        f"prior_median={median} prior_days={len(counts)}"
    )


def report_price_gap(db: Session, day: date, *, zero_is_gap: bool) -> str | None:
    if zero_is_gap:
        db.rollback()
    line = price_gap_line(db, day, zero_is_gap=zero_is_gap)
    if line:
        print(line, flush=True)
    return line

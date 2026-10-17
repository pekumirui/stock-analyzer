"""PTS タブ。/pts。株探の夜間 PTS ランキングと翌営業日の始値とのギャップ。"""
from datetime import date, datetime
from statistics import median

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..market.pts import RANKINGS
from ..models import DailyPrice, PtsSnapshot


router = APIRouter()

DATES_LIMIT = 60


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return round((a / b - 1) * 100, 2)


def _next_opens(db: Session, codes: list[str], after: date) -> dict[str, tuple[date, float | None]]:
    """コードごとに after より後の最初の日足の (日付, 始値)。"""
    if not codes:
        return {}
    first = (db.query(DailyPrice.code, func.min(DailyPrice.date).label("d"))
             .filter(DailyPrice.code.in_(codes), DailyPrice.date > after)
             .group_by(DailyPrice.code).subquery())
    rows = (db.query(DailyPrice.code, DailyPrice.date, DailyPrice.open)
            .join(first, (first.c.code == DailyPrice.code) & (first.c.d == DailyPrice.date))
            .all())
    return {code: (d, o) for code, d, o in rows}


@router.get("/pts")
def pts(date: date | None = None, ranking: str = "night_up", at: datetime | None = None,
        db: Session = Depends(get_session)):
    if ranking not in RANKINGS:
        raise HTTPException(400, f"ranking は {', '.join(RANKINGS)} のどれか")
    dates = [d for (d,) in db.query(PtsSnapshot.session_date).distinct()
             .order_by(PtsSnapshot.session_date.desc()).limit(DATES_LIMIT)]
    session = date or (dates[0] if dates else None)
    empty = {"dates": [d.isoformat() for d in dates], "ranking": ranking,
             "date": session.isoformat() if session else None,
             "snapshots": [], "fetched_at": None, "as_of": None, "rows": [], "summary": None}
    if session is None:
        return empty
    snaps = (db.query(PtsSnapshot.fetched_at, PtsSnapshot.as_of)
             .filter(PtsSnapshot.session_date == session, PtsSnapshot.ranking == ranking)
             .distinct().order_by(PtsSnapshot.fetched_at.desc()).all())
    if not snaps:
        return empty
    pick = next((s for s in snaps if at is not None and s[0] == at), snaps[0])
    recs = (db.query(PtsSnapshot)
            .filter(PtsSnapshot.fetched_at == pick[0], PtsSnapshot.ranking == ranking)
            .order_by(PtsSnapshot.rank).all())
    opens = _next_opens(db, [r.code for r in recs], session)
    rows = []
    for r in recs:
        nd, no = opens.get(r.code, (None, None))
        follow = None
        if no is not None and r.pts_price is not None and r.base_close is not None \
                and r.pts_price != r.base_close:
            follow = round((no - r.base_close) / (r.pts_price - r.base_close), 2)
        rows.append({
            "rank": r.rank, "code": r.code, "name": r.name, "market": r.market,
            "base_close": r.base_close, "pts_price": r.pts_price,
            "pts_chg_pct": r.pts_chg_pct, "volume": r.volume,
            "next_date": nd.isoformat() if nd else None, "next_open": no,
            "open_gap_pct": _pct(no, r.base_close),
            "open_vs_pts_pct": _pct(no, r.pts_price),
            "follow_ratio": follow,
        })
    gaps = [x["open_vs_pts_pct"] for x in rows if x["open_vs_pts_pct"] is not None]
    follows = [x["follow_ratio"] for x in rows if x["follow_ratio"] is not None]
    summary = {
        "n": len(gaps),
        "avg_open_vs_pts_pct": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "median_follow_ratio": round(median(follows), 2) if follows else None,
    }
    return {**empty,
            "snapshots": [{"fetched_at": f.isoformat(sep=" "), "as_of": a} for f, a in snaps],
            "fetched_at": pick[0].isoformat(sep=" "), "as_of": pick[1],
            "rows": rows, "summary": summary}

"""保有者タブ（KAB-267 / KAB-272。docs/screen/holders.md）。読み取りのみ。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_session
from ..holders import ACTIVITY_MAX, activity_feed, holder_page, parse_edinet, search_holders
from .monthly._common import ApiError

router = APIRouter()


@router.get("/holders/search")
def holders_search(q: str = "", db: Session = Depends(get_session)):
    return search_holders(db, q)


@router.get("/holders/activity")
def holders_activity(
    limit: int = Query(80, ge=1, le=ACTIVITY_MAX),
    offset: int = Query(0, ge=0),
    file_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    q: str = "",
    db: Session = Depends(get_session),
):
    day = date.fromisoformat(file_date) if file_date else None
    return activity_feed(db, limit=limit, offset=offset, file_date=day, q=q)


@router.get("/holders/{edinet}")
def holders_page(
    edinet: str,
    today: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_session),
):
    if parse_edinet(edinet) is None:
        raise ApiError(400, "INVALID_PARAM", "edinet は E と数字 5 桁です。")
    day = date.fromisoformat(today) if today else None
    data = holder_page(db, edinet, today=day)
    if data is None:
        raise ApiError(404, "NOT_FOUND", "保有者がありません。")
    return data

"""株価チャート /chart と /topix、決算後リターン /earnings-performance。"""

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Stock, DailyPrice, Topix, EarningsEvent


router = APIRouter()


def _px(v: float) -> float:
    r = round(v, 1)
    return int(r) if r == int(r) else r


def _etag_json(request: Request, body: dict) -> Response:
    """中身のハッシュを ETag にして返す。合えば 304。足・移動平均はブラウザで作るので日足は一度だけ送る"""
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"ETag": '"' + hashlib.sha256(raw).hexdigest()[:32] + '"',
               "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(raw, media_type="application/json", headers=headers)


@router.get("/chart/{code}")
def chart(code: str, request: Request, db: Session = Depends(get_session)):
    """日足を列ごとの配列で。四本値と出来高は調整後（無い日は生の値）、売買代金は円"""
    rows = (db.query(DailyPrice.date, DailyPrice.adj_open, DailyPrice.adj_high, DailyPrice.adj_low,
                     DailyPrice.adj_close, DailyPrice.adj_volume,
                     DailyPrice.open, DailyPrice.high, DailyPrice.low,
                     DailyPrice.close, DailyPrice.volume, DailyPrice.turnover)
            .filter(DailyPrice.code == code).order_by(DailyPrice.date).all())
    t, o, h, lo, c, v, tv = [], [], [], [], [], [], []
    for d, ao, ah, al, ac, av, ro, rh, rl, rc, rv, rtv in rows:
        close = ac if ac is not None else rc
        if close is None:
            continue
        op = ao if ao is not None else ro
        hi = ah if ah is not None else rh
        low = al if al is not None else rl
        vol = av if av is not None else rv
        t.append(d.isoformat())
        o.append(_px(op if op is not None else close))
        h.append(_px(hi if hi is not None else close))
        lo.append(_px(low if low is not None else close))
        c.append(_px(close))
        v.append(round(vol or 0))
        tv.append(round(rtv or 0))
    if not t:
        raise HTTPException(404, "no data")
    stock = db.get(Stock, code)
    events = sorted(e.disclosed_date.isoformat() for e in
                    db.query(EarningsEvent.disclosed_date).filter(EarningsEvent.code == code).all())
    return _etag_json(request, {"code": code, "name": stock.name if stock else code,
                                "t": t, "o": o, "h": h, "l": lo, "c": c, "v": v, "tv": tv,
                                "earnings_dates": events})


@router.get("/topix")
def topix(request: Request, db: Session = Depends(get_session)):
    """TOPIX の終値。対TOPIX 相対はブラウザで作る"""
    rows = db.query(Topix.date, Topix.close).filter(Topix.close.isnot(None)).order_by(Topix.date).all()
    return _etag_json(request, {"t": [d.isoformat() for d, _ in rows], "c": [round(x, 2) for _, x in rows]})


@router.get("/earnings-performance/{code}")
def earnings_performance(code: str, db: Session = Depends(get_session)):
    evs = (db.query(EarningsEvent).filter(EarningsEvent.code == code)
           .order_by(EarningsEvent.disclosed_date.desc()).all())
    return [{"disclosed_date": e.disclosed_date.isoformat(),
             "return_1d": e.return_1d, "alpha_1d": e.alpha_1d,
             "return_5d": e.return_5d, "alpha_5d": e.alpha_5d,
             "return_20d": e.return_20d, "alpha_20d": e.alpha_20d,
             "return_60d": e.return_60d, "alpha_60d": e.alpha_60d,
             "return_to_date": e.return_to_date, "alpha_to_date": e.alpha_to_date}
            for e in evs]

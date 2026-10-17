"""チャートタブ。/chart と決算後リターン /earnings-performance。"""

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Stock, DailyPrice, Topix, EarningsEvent


router = APIRouter()


@router.get("/chart/{code}")
def chart(code: str, tf: str = "D", ma: str = "50,150,200", rel: bool = False,
          db: Session = Depends(get_session)):
    q = (db.query(DailyPrice.date, DailyPrice.adj_open, DailyPrice.adj_high, DailyPrice.adj_low,
                  DailyPrice.adj_close, DailyPrice.adj_volume,
                  DailyPrice.open, DailyPrice.high, DailyPrice.low,
                  DailyPrice.close, DailyPrice.volume)
         .filter(DailyPrice.code == code).order_by(DailyPrice.date))
    df = pd.DataFrame(q.all(), columns=["date", "adj_open", "adj_high", "adj_low", "adj_close",
                                        "adj_volume", "open", "high", "low", "close", "volume"])
    if df.empty:
        raise HTTPException(404, "no data")
    for a, b in (("adj_open", "open"), ("adj_high", "high"), ("adj_low", "low"),
                 ("adj_close", "close"), ("adj_volume", "volume")):
        df[b] = df[a].fillna(df[b])
    df = df.dropna(subset=["close"])
    if df.empty:
        raise HTTPException(404, "no data")

    mas = {}
    for w in [int(x) for x in ma.split(",") if x.strip().isdigit()]:
        mas[w] = df["close"].rolling(w).mean()

    relative = None
    if rel:
        tp = pd.DataFrame(db.query(Topix.date, Topix.close).all(), columns=["date", "tclose"])
        m = df.merge(tp, on="date", how="inner")
        if not m.empty:
            base = m.iloc[0]
            relative = [{"time": d.isoformat(), "value": round((c / base["close"]) / (t / base["tclose"]) * 100, 2)}
                        for d, c, t in zip(m["date"], m["close"], m["tclose"])]

    if tf in ("W", "M"):
        if tf == "M":
            df["_k"] = df["date"].map(lambda d: (d.year, d.month))
            ma_div = 20  # 日数→月数 (約20営業日/月)
        else:
            df["_k"] = df["date"].map(lambda d: d.isocalendar()[:2])
            ma_div = 5   # 日数→週数 (約5営業日/週)
        agg = df.groupby("_k").agg(
            date=("date", "last"), open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index(drop=True)
        df = agg
        mas = {w: df["close"].rolling(min(w // ma_div or 1, len(df))).mean() for w in mas}

    candles = [{"time": d.isoformat(), "open": round(o, 1), "high": round(h, 1),
                "low": round(l, 1), "close": round(c, 1), "volume": v}
               for d, o, h, l, c, v in zip(df["date"], df["open"], df["high"], df["low"], df["close"], df["volume"])]
    ma_out = {str(w): [{"time": d.isoformat(), "value": round(v, 1)}
                       for d, v in zip(df["date"], s) if pd.notna(v)]
              for w, s in mas.items()}
    stock = db.get(Stock, code)

    events = [e.disclosed_date.isoformat() for e in
              db.query(EarningsEvent).filter(EarningsEvent.code == code).all()]
    return {"code": code, "name": stock.name if stock else code,
            "candles": candles, "ma": ma_out, "relative": relative, "earnings_dates": events}


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

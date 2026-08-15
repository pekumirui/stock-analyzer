from datetime import date, datetime

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import get_session
from .models import Stock, DailyPrice, Topix, Statement, Indicator, EarningsEvent
from .analytics import quarter_summary

router = APIRouter(prefix="/api")


def _pd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@router.get("/earnings/calendar")
def earnings_calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      db: Session = Depends(get_session)):
    rows = (db.query(Statement.disclosed_date, func.count(func.distinct(Statement.code)))
            .filter(func.strftime("%Y-%m", Statement.disclosed_date) == month)
            .group_by(Statement.disclosed_date).all())
    return {d.isoformat(): n for d, n in rows}


@router.get("/earnings")
def earnings_by_date(date_: str = Query(..., alias="date"), db: Session = Depends(get_session)):
    day = _pd(date_)
    stmts = (db.query(Statement).filter(Statement.disclosed_date == day)
             .order_by(Statement.code).all())
    out = []
    for s in stmts:
        stock = db.get(Stock, s.code)
        ind = (db.query(Indicator).filter(Indicator.code == s.code)
               .order_by(Indicator.date.desc()).first())
        ev = db.get(EarningsEvent, {"code": s.code, "disclosed_date": day})
        out.append({
            "code": s.code, "name": stock.name if stock else s.code,
            "period": s.type_of_period,
            "fiscal_year_end": s.fiscal_year_end.isoformat() if s.fiscal_year_end else None,
            "disclosed_time": s.disclosed_time,
            "summary": quarter_summary(db, s),
            "rs_rank": ind.rs_rank if ind else None,
            "stage": ind.stage if ind else None,
            "alpha_to_date": ev.alpha_to_date if ev else None,
        })
    return out


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
    # 分析・表示は調整済を使用 (欠損時のみ調整前で代替)
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

    if tf == "M":
        df["ym"] = df["date"].map(lambda d: (d.year, d.month))
        agg = df.groupby("ym").agg(
            date=("date", "last"), open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum")).reset_index(drop=True)
        df = agg
        mas = {w: df["close"].rolling(min(w // 20 or 1, len(df))).mean() for w in mas}  # 月足MA: 日数→月数換算(約20営業日/月)

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


@router.get("/screener")
def screener(stage: int | None = None, rs_min: int = 0, sort: str = "rs",
             limit: int = 100, db: Session = Depends(get_session)):
    latest = db.query(func.max(Indicator.date)).scalar()
    if latest is None:
        return []
    q = db.query(Indicator, Stock).join(Stock, Stock.code == Indicator.code) \
          .filter(Indicator.date == latest)
    if stage is not None:
        q = q.filter(Indicator.stage == stage)
    if rs_min:
        q = q.filter(Indicator.rs_rank >= rs_min)
    q = q.order_by(Indicator.rs_rank.desc() if sort == "rs" else Indicator.code)
    out = []
    for ind, st in q.limit(limit).all():
        out.append({"code": ind.code, "name": st.name, "sector": st.sector33,
                    "close": ind.close, "rs_rank": ind.rs_rank, "stage": ind.stage,
                    "ma50": ind.ma50, "ma150": ind.ma150, "ma200": ind.ma200,
                    "pct_from_low52": round((ind.close / ind.low_52w - 1) * 100, 1) if ind.low_52w else None,
                    "pct_from_high52": round((ind.close / ind.high_52w - 1) * 100, 1) if ind.high_52w else None})
    return {"date": latest.isoformat(), "results": out}


@router.get("/stocks/search")
def search(q: str, db: Session = Depends(get_session)):
    rows = (db.query(Stock).filter(
        (Stock.name.contains(q)) | (Stock.code.startswith(q))).limit(20).all())
    return [{"code": s.code, "name": s.name, "market": s.market} for s in rows]

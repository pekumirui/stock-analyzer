from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, defer

from .config import EARNINGS_PARAMS as EP, EARNINGS_EXCLUDE_PRODUCT_CATEGORIES, RS_PRODUCT_CATEGORIES
from .db import get_session
from .models import Stock, DailyPrice, Topix, Statement, Indicator, EarningsEvent
from .analytics import (METRIC_KEYS, PL_KEYS, quarter_summary, is_actual, period_label,
                        disclosure_state, earnings_detail as build_earnings_detail)

router = APIRouter(prefix="/api")

# 予想修正のみの開示は実績値を持たない (速報一覧に出すと全欄が空になる)
_ACTUAL_DOC = Statement.type_of_document.contains("FinancialStatements")


def _pd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _not_duplicate(a):
    """既出期間を数値そのまま再提出した開示を除く条件 (analytics.disclosure_state のSQL版)。"""
    b = aliased(Statement)
    same_values = and_(*[getattr(b, k).is_not_distinct_from(getattr(a, k)) for k in PL_KEYS])
    return ~(select(1).where(
        b.code == a.code,
        b.fiscal_year_end.is_not_distinct_from(a.fiscal_year_end),
        b.type_of_period == a.type_of_period,
        b.type_of_document.contains("FinancialStatements"),
        or_(b.disclosed_date < a.disclosed_date,
            and_(b.disclosed_date == a.disclosed_date, b.disclosure_no < a.disclosure_no)),
        same_values).exists())


def _not_excluded_product(code_col):
    """決算速報から REIT 等を除外。ProdCat 未設定の行は残す (マスタ取込前)。"""
    return ~code_col.in_(
        select(Stock.code).where(Stock.product_category.in_(EARNINGS_EXCLUDE_PRODUCT_CATEGORIES))
    )


@router.get("/earnings/calendar")
def earnings_calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      db: Session = Depends(get_session)):
    a = aliased(Statement)
    rows = (db.query(a.disclosed_date, func.count(func.distinct(a.code)))
            .filter(func.strftime("%Y-%m", a.disclosed_date) == month,
                    a.type_of_document.contains("FinancialStatements"),
                    _not_duplicate(a),          # 一覧の行数と件数を一致させる
                    _not_excluded_product(a.code))
            .group_by(a.disclosed_date).all())
    return {d.isoformat(): n for d, n in rows}


@router.get("/earnings")
def earnings_by_date(date_: str = Query(..., alias="date"), db: Session = Depends(get_session)):
    """その日の決算速報一覧 (QoQ=Q単独の前四半期比 / YoY=累計の前年同期比)。"""
    day = _pd(date_)
    codes = select(Statement.code).where(
        Statement.disclosed_date == day, _ACTUAL_DOC, _not_excluded_product(Statement.code))
    # 開示1件ごとに履歴を引くとピーク日(約1,000件)で数千クエリになるため、まとめて取って配る
    hist_by_code: dict[str, list[Statement]] = defaultdict(list)
    for s in (db.query(Statement).options(defer(Statement.raw_json))
              .filter(Statement.code.in_(codes),
                      Statement.disclosed_date <= day,
                      Statement.disclosed_date >= day - timedelta(days=EP["list_history_days"]))
              .order_by(Statement.code, Statement.disclosed_date).all()):
        hist_by_code[s.code].append(s)
    names = dict(db.query(Stock.code, Stock.name).filter(Stock.code.in_(codes)).all())

    out = []
    for code, hist in hist_by_code.items():
        for s in hist:
            if s.disclosed_date != day or not is_actual(s):
                continue
            # 既出期間の数値そのままの再提出は速報ではないので出さない
            state = disclosure_state(hist, s)
            if state == "duplicate":
                continue
            summary = quarter_summary(db, s, hist=hist)
            out.append({
                "restated": state == "restated",
                "disclosure_no": s.disclosure_no,
                "code": code, "name": names.get(code, code),
                "disclosed_time": s.disclosed_time,
                "period": s.type_of_period,
                "fiscal_year_end": s.fiscal_year_end.isoformat() if s.fiscal_year_end else None,
                "fy_label": period_label(s.fiscal_year_end),
                "qoq": summary["qoq"],
                "yoy": summary["yoy"],
            })
    out.sort(key=lambda r: (r["disclosed_time"] or "", r["code"]))
    return out


@router.get("/earnings/detail/{code}")
def earnings_detail(code: str, date_: str | None = Query(None, alias="date"),
                    disclosure_no: str | None = None,
                    db: Session = Depends(get_session)):
    """累計テーブル・3か月決算テーブル。

    同一銘柄が同じ日に複数開示する日があるため、一覧からは disclosure_no で一意に引く。
    """
    stmt = None
    if disclosure_no:
        stmt = db.get(Statement, disclosure_no)
        if stmt is not None and stmt.code != code:
            stmt = None
    if stmt is None:
        q = db.query(Statement).filter(Statement.code == code, _ACTUAL_DOC)
        if date_:
            day = _pd(date_)
            stmt = (q.filter(Statement.disclosed_date == day, Statement.is_revision.isnot(True))
                    .order_by(Statement.fiscal_year_end.desc()).first())
            q = q.filter(Statement.disclosed_date <= day)
        if stmt is None:
            # 同日に複数期を開示する銘柄があるので、年度末の新しい方を既定にする
            stmt = q.order_by(Statement.disclosed_date.desc(),
                              Statement.fiscal_year_end.desc()).first()
    if stmt is None:
        raise HTTPException(404, "no statement")

    stock = db.get(Stock, code)
    return {
        "code": code, "name": stock.name if stock else code,
        "disclosure_no": stmt.disclosure_no,
        "disclosed_date": stmt.disclosed_date.isoformat(),
        "disclosed_time": stmt.disclosed_time,
        "period": stmt.type_of_period,
        "fiscal_year_end": stmt.fiscal_year_end.isoformat() if stmt.fiscal_year_end else None,
        "unit": "yen", "metrics": list(METRIC_KEYS),
        **build_earnings_detail(db, stmt),
    }


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
    if db.query(Stock.code).filter(Stock.product_category.in_(RS_PRODUCT_CATEGORIES)).first():
        q = q.filter(Stock.product_category.in_(RS_PRODUCT_CATEGORIES))
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

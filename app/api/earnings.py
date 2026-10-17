"""決算タブ。/earnings の速報一覧・カレンダー・詳細。"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..config import EARNINGS_PARAMS as EP, EARNINGS_EXCLUDE_PRODUCT_CATEGORIES
from ..db import get_session
from ..models import Stock, Statement
from ..market.analytics import (METRIC_KEYS, PL_KEYS, quarter_summary, is_actual, period_label,
                        disclosure_state, earnings_detail as build_earnings_detail)
from ..market.earnings_schedule import ActualMonth, day_payload, month_payload


router = APIRouter()

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


def resolve_today(today: str | None) -> date:
    """SPA が送った今日。省略時は date.today()。"""
    if today is None:
        return date.today()
    return _pd(today)


def actual_month_facts(db: Session, month: str, today: date) -> ActualMonth:
    """カレンダーの開示件数。今日が月の外なら codes_on_today は空。"""
    a = aliased(Statement)
    first = _pd(f"{month}-01")
    after = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    rows = (db.query(a.disclosed_date, func.count(func.distinct(a.code)))
            .filter(a.disclosed_date >= first, a.disclosed_date < after,  # strftime だと索引が効かない
                    a.type_of_document.contains("FinancialStatements"),
                    _not_duplicate(a),          # 一覧の行数と件数を一致させる
                    _not_excluded_product(a.code))
            .group_by(a.disclosed_date).all())
    codes: frozenset[str] = frozenset()
    if today.strftime("%Y-%m") == month:
        code_rows = (db.query(a.code)
                     .filter(a.disclosed_date == today,
                             a.type_of_document.contains("FinancialStatements"),
                             _not_duplicate(a),
                             _not_excluded_product(a.code))
                     .distinct().all())
        codes = frozenset(code for (code,) in code_rows)
    return ActualMonth({d: int(n) for d, n in rows}, codes)


_HIST_COLS = tuple(c for c in Statement.__table__.columns if c.key != "raw_json")


def earnings_rows(db: Session, day: date) -> list[dict]:
    """その日の決算速報一覧 (QoQ=Q単独の前四半期比 / YoY=累計の前年同期比)。"""
    codes = select(Statement.code).where(
        Statement.disclosed_date == day, _ACTUAL_DOC, _not_excluded_product(Statement.code))
    # 開示1件ごとに履歴を引くとピーク日(約1,000件)で数千クエリになるため、まとめて取って配る
    hist_by_code: dict[str, list[Statement]] = defaultdict(list)
    # 読むだけなので ORM の実体ではなく Row で受ける (ピーク日は数万行あり、実体化と属性アクセスが支配的)
    for s in db.execute(
            select(*_HIST_COLS)
            .where(Statement.code.in_(codes),
                   Statement.disclosed_date <= day,
                   Statement.disclosed_date >= day - timedelta(days=EP["list_history_days"]))
            .order_by(Statement.code, Statement.disclosed_date)):
        hist_by_code[s.code].append(s)
    names = dict(db.query(Stock.code, Stock.name).filter(Stock.code.in_(codes)).all())

    out = []
    for code, hist in hist_by_code.items():
        for s in hist:
            if s.disclosed_date != day or not is_actual(s):
                continue
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
                # 一覧に金額も出す。QoQ の相手は Q単独、YoY の相手は累計
                "single": summary["single"],
                "cumulative": summary["cumulative"],
            })
    out.sort(key=lambda r: (r["disclosed_time"] or "", r["code"]))
    return out


@router.get("/earnings/calendar")
def earnings_calendar(month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                      today: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                      db: Session = Depends(get_session)):
    today_d = resolve_today(today)
    return month_payload(db, month, today_d, actual_month_facts(db, month, today_d))


@router.get("/earnings")
def earnings_by_date(date_: str = Query(..., alias="date"),
                     today: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                     db: Session = Depends(get_session)):
    day = _pd(date_)
    return day_payload(db, day, resolve_today(today), earnings_rows(db, day))


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

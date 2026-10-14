from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import re
import unicodedata
from typing import Any, Sequence

import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from .config import (
    EARNINGS_PARAMS as EP, EARNINGS_EXCLUDE_PRODUCT_CATEGORIES, MONTHLY_NEW_DAYS,
    RS_PRODUCT_CATEGORIES,
)
from .db import (
    delete_monthly_element, get_session, rename_monthly_element,
    run_sqlite_write,
)
from .models import (
    Stock, DailyPrice, Topix, Statement, Indicator, EarningsEvent, MaterialMonthly,
    MonthlyIssuer, MonthlyTable, MonthlyElement, MonthlyValue,
)
from .analytics import (METRIC_KEYS, PL_KEYS, quarter_summary, is_actual, period_label,
                        disclosure_state, earnings_detail as build_earnings_detail)
from .earnings_schedule import ActualMonth, day_payload, month_payload
from .material_stats import TRADE_SOURCE
from .supply_chain import monthly_payload, supply_graph
from .monthly_tree import (
    MONTHLY_TREES, assign_tree_rows, auto_tree_payload, build_auto_tree, monthly_display_years, tree_payload,
)
from .monthly_ingest import (
    MonthlyIngestError, apply_issuer_aliases, canonicalize_element_label,
    coerce_monthly_storage, map_pdf_columns, monthly_element_label,
    monthly_series_kind, parse_as_of, parse_monthly_unit,
    reject_prior_period_element, same_yoy_element, serialize_column_mapping,
    uncovered_yoy_number_twins,
)

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

    if tf in ("W", "M"):
        if tf == "M":
            df["_k"] = df["date"].map(lambda d: (d.year, d.month))
            ma_div = 20  # 日数→月数 (約20営業日/月)
        else:
            df["_k"] = df["date"].map(lambda d: d.isocalendar()[:2])  # ISO週 (月曜始まり)
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
    q = q.strip()
    if not q:
        return []
    # 社名はカタカナ表記が多いので、ひらがな入力もカタカナに寄せて引く。社名自体のひらがなは q のままで当たる
    folded = unicodedata.normalize("NFKC", q)
    kata = "".join(chr(ord(ch) + 0x60) if "\u3041" <= ch <= "\u3096" else ch for ch in folded)
    rows = (db.query(Stock).filter(
        Stock.name.contains(q) | Stock.name.contains(folded) | Stock.name.contains(kata)
        | Stock.code.startswith(folded.upper())).order_by(Stock.code).limit(20).all())
    return [{"code": s.code, "name": s.name, "market": s.market} for s in rows]


@router.get("/supply/graph")
def supply_graph_api():
    return supply_graph()


@router.get("/supply/monthly/{material_id}")
def supply_monthly_api(material_id: str, db: Session = Depends(get_session)):
    if material_id not in {m["id"] for m in supply_graph()["materials"]}:
        raise HTTPException(404, "unknown material")
    rows = db.query(MaterialMonthly).filter(MaterialMonthly.material_id == material_id).all()
    return monthly_payload(rows, material_id, _trade_months(db))


def _trade_months(db: Session) -> dict[str, dict[str, int]]:
    """貿易統計の公表済みの月。どれかの素材に行がある月を公表済みとみなす"""
    out: dict[str, dict[str, int]] = {}
    for metric, month, rev in (db.query(MaterialMonthly.metric, MaterialMonthly.month,
                                        func.min(MaterialMonthly.revision))
                               .filter(MaterialMonthly.source == TRADE_SOURCE)
                               .group_by(MaterialMonthly.metric, MaterialMonthly.month)):
        out.setdefault(metric, {})[month] = rev
    return out


router_v1 = APIRouter(prefix="/api/v1")

_INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
_SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"
_YOY_NUMBER_MSG = (
    "同じ指標の number と前年比が別行になります。"
    "既存行を同じ POST に含めるか、force=true で登録してください。"
)
_NOT_IN_MASTER_MSG = "銘柄マスタにありません。"
_JST = timezone(timedelta(hours=9))
_INDUSTRY_MSG = "industry は1〜50文字の文字列です。"
_DEFAULT_INDUSTRY = "その他"


class ApiError(Exception):
    """機械可読な error.code / error.message を返す。既存の HTTPException とは混ぜない。"""

    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **(extra or {})}}


def _normalize_element(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


# 5桁正規形は先頭・末尾が数字、途中は 0-9A-Z (72030 / 262A0)。
# 4桁 (9279 / 7203 / 262A) は末尾0。fy_end 参照と月次の保存キーは同じ 5桁。
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_PUBLIC_GET_CACHE = "public, max-age=60"


def forbid_proxied_monthly_write(request: Request) -> None:
    """nginx 経由の書き込みを拒否する。MCP / ジョブは 127.0.0.1 直叩き。"""
    if request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise ApiError(403, "FORBIDDEN", "月次の更新は loopback のみです。")


def _public_get_cache(response: Response) -> None:
    response.headers["Cache-Control"] = _PUBLIC_GET_CACHE


def _parse_code(code: str) -> str:
    if not isinstance(code, str):
        raise ApiError(400, "INVALID_PARAM", "code は4〜5桁の英数字です。")
    s = code.upper()
    if not _CODE_RE.fullmatch(s) or "." in s or "/" in s:
        raise ApiError(400, "INVALID_PARAM", "code は4〜5桁の英数字です。")
    if len(s) == 4:
        s = s + "0"
    return s


def _parse_year(year: str) -> int:
    if not isinstance(year, str) or not re.fullmatch(r"\d{4}", year):
        raise ApiError(400, "INVALID_PARAM", "year は西暦4桁の整数です。")
    return int(year)


def _parse_element(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ApiError(400, "INVALID_PARAM", "element は1〜50文字の文字列です。")
    s = raw.strip()
    if not s or len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", "element は1〜50文字の文字列です。")
    return s


def _canonical_element(
    raw: Any, unit: str | None, code: str,
    *, year: int | None = None, as_of: date | None = None,
) -> str:
    """ingest / POST は先に正規化してから既存行を探す。銘柄エイリアスもここで掛ける。"""
    raw_name = _parse_element(raw)
    try:
        reject_prior_period_element(raw_name)
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc
    s = apply_issuer_aliases(
        code, canonicalize_element_label(raw_name, unit),
        year=year, as_of=as_of,
    )
    s = canonicalize_element_label(s, unit)
    if not s or len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", "element は1〜50文字の文字列です。")
    return s


def _year_alias(code: str, element: str, unit: str | None, year: int) -> str:
    """正規化済みの入ってくる名前に、列の暦年の銘柄エイリアスを掛ける。"""
    return canonicalize_element_label(apply_issuer_aliases(code, element, year=year), unit)


def _lookup_element(raw: Any, code: str, *, year: int | None = None) -> str:
    """PUT は保存名に合わせる。トークン正規化と銘柄エイリアスを掛ける。"""
    raw_name = _parse_element(raw)
    unit = "yoy_index" if any(f in raw_name for f in ("(前年比)", "（前年比）", "前年比")) else "number"
    s = apply_issuer_aliases(
        code, canonicalize_element_label(raw_name, unit), year=year,
    )
    s = canonicalize_element_label(s, unit)
    if not s or len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", "element は1〜50文字の文字列です。")
    return s


def _parse_unit(raw: Any, *, omitted_ok: bool = False) -> str | None:
    try:
        return parse_monthly_unit(raw, omitted_ok=omitted_ok)
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc


def _parse_month(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1 or raw > 12:
        raise ApiError(400, "INVALID_PARAM", "month は1〜12の整数です。")
    return raw


def _parse_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ApiError(400, "INVALID_VALUE", _INVALID_VALUE_MSG)
    v = float(raw)
    if v != v or v in (float("inf"), float("-inf")):
        raise ApiError(400, "INVALID_VALUE", _INVALID_VALUE_MSG)
    return v


def _require_object(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ApiError(400, "INVALID_PARAM", "JSONオブジェクトのリクエストボディが必要です。")
    return payload


def _require_stock(db: Session, code: str) -> Stock:
    """月次の書き込みは stocks 行が先。FK は付けないのでここで見る。"""
    row = db.get(Stock, code)
    if row is None:
        raise ApiError(404, "NOT_IN_MASTER", _NOT_IN_MASTER_MSG)
    return row


def _jst_now() -> datetime:
    return datetime.now(_JST).replace(tzinfo=None)


def _jst_today() -> date:
    return datetime.now(_JST).date()


def _as_jst_date(dt: datetime) -> date:
    if dt.tzinfo is not None:
        return dt.astimezone(_JST).date()
    return dt.date()


def _parse_industry(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ApiError(400, "INVALID_PARAM", _INDUSTRY_MSG)
    s = raw.strip()
    if not s or len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", _INDUSTRY_MSG)
    return s


def _require_any_monthly_table(db: Session, code: str) -> None:
    if db.query(MonthlyTable.code).filter(MonthlyTable.code == code).first() is None:
        raise ApiError(404, "TABLE_NOT_FOUND", "銘柄×年度の表がありません。")


def _touch_monthly_issuer(db: Session, code: str) -> None:
    now = _jst_now()
    row = db.get(MonthlyIssuer, code)
    if row is None:
        db.add(MonthlyIssuer(code=code, industry=_DEFAULT_INDUSTRY, last_ingested_at=now))
    else:
        row.last_ingested_at = now


# 行名の解析は重く、同じ行名が年・銘柄をまたいで何度も出る。結果は (element, unit) だけで決まる
_series_kind = lru_cache(maxsize=16384)(monthly_series_kind)


def _headline_yoy_tier(el: MonthlyElement) -> int:
    """0=会社計の売上高/連結売上高。1=客数・客単価やセグメント付き。"""
    return _headline_tier_of(el.code, el.element, el.unit)


@lru_cache(maxsize=16384)
def _headline_tier_of(code: str, element: str, unit: str | None) -> int:
    label = monthly_element_label(element, unit, code)
    company_wide = not (
        label["group"] or label["segment"] or label["country"]
        or label["channel"] or label["breakdown"]
    )
    sales = label["metric"] in {"売上高", "連結売上高"}
    return 0 if company_wide and sales else 1


def _latest_yoy_for_kind(
    elems: list[MonthlyElement], vals: list[MonthlyValue], kind: str,
) -> float | None:
    matched: dict[tuple[int, str], MonthlyElement] = {}
    tier_of: dict[tuple[int, str], int] = {}
    for e in elems:
        if _series_kind(e.element, e.unit) != kind:
            continue
        key = (e.year, e.element)
        matched[key] = e
        tier_of[key] = _headline_yoy_tier(e)
    best: tuple[tuple[int, int, int, int, str], float] | None = None
    for v in vals:
        el = matched.get((v.year, v.element))
        if el is None:
            continue
        rank = (tier_of[(v.year, v.element)], -v.year, -v.month, el.sort_order, el.element)
        if best is None or rank < best[0]:
            best = (rank, float(v.value))
    if best is None:
        return None
    return best[1]


def _new_flags(last_ingested_at: datetime | None) -> tuple[bool, bool]:
    if last_ingested_at is None:
        return False, False
    ingested = _as_jst_date(last_ingested_at)
    today = _jst_today()
    is_new = 0 <= (today - ingested).days < MONTHLY_NEW_DAYS
    return is_new, ingested == today


def _get_table(db: Session, code: str, year: int) -> MonthlyTable:
    table = db.get(MonthlyTable, (code, year))
    if table is None:
        raise ApiError(404, "TABLE_NOT_FOUND", "銘柄×年度の表がありません。")
    return table


def _get_element(db: Session, code: str, year: int, element: str) -> MonthlyElement:
    row = db.get(MonthlyElement, (code, year, element))
    if row is None:
        raise ApiError(404, "ELEMENT_NOT_FOUND", "要素が登録されていません。")
    return row


def _upsert_value(db: Session, code: str, year: int, element: str, month: int, value: float | None) -> bool:
    """セルが変わったら True。"""
    row = db.get(MonthlyValue, (code, year, element, month))
    if value is None:
        if row is None:
            return False
        db.delete(row)
        return True
    if row is None:
        db.add(MonthlyValue(code=code, year=year, element=element, month=month, value=value))
        return True
    if row.value == value:
        return False
    row.value = value
    return True


def _element_unit(row: MonthlyElement) -> str:
    return row.unit or "number"


def _apply_incoming_unit(row: MonthlyElement, unit: str, force: bool) -> None:
    existing = row.unit or "number"
    if existing == unit:
        return
    if not force:
        raise ApiError(
            409, "UNIT_CONFLICT",
            "既存の unit と異なります。force=true で上書きできます。",
            extra={"existing": existing, "incoming": unit},
        )
    row.unit = unit


def _ensure_element(
    db: Session, code: str, year_i: int, element: str, force: bool, unit: str,
    posted: Sequence[tuple[str, str]] | None = None,
) -> bool:
    _require_stock(db, code)
    # 保存名は凍結。正規化した名前と完全一致する行だけを同じ行とみなし、既存行は作り直さない（#545）
    same = db.get(MonthlyElement, (code, year_i, element))
    if same is not None:
        _apply_incoming_unit(same, unit, force)
        return False
    existing = (db.query(MonthlyElement)
                .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
                .all())
    similar = [
        row.element for row in existing
        if _normalize_element(row.element) == _normalize_element(element)
        and not same_yoy_element(row.element, row.unit, element, unit)
    ]
    if similar and not force:
        raise ApiError(409, "ELEMENT_SIMILAR_EXISTS", _SIMILAR_MSG, extra={"similar": similar})
    # ingest だけ。POST elements は posted=None。既存行の更新（fold 済み）はここまで来ない。
    if posted is not None and not force:
        twins = uncovered_yoy_number_twins(
            [(row.element, row.unit) for row in existing],
            element, unit, posted,
        )
        if twins:
            raise ApiError(
                409, "YOY_NUMBER_CONFLICT", _YOY_NUMBER_MSG,
                extra={"existing": twins, "incoming": element},
            )
    if db.get(MonthlyTable, (code, year_i)) is None:
        db.add(MonthlyTable(code=code, year=year_i))
        db.flush()
    max_order = (db.query(func.max(MonthlyElement.sort_order))
                 .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
                 .scalar())
    db.add(MonthlyElement(
        code=code, year=year_i, element=element,
        sort_order=(max_order or 0) + 1, unit=unit,
    ))
    db.flush()
    return True


def _parse_months(raw: Any) -> list[int]:
    """1〜12 の整数配列。年跨ぎの Genky 型では同じ月が2回出てよい。"""
    if not isinstance(raw, list) or len(raw) < 1:
        raise ApiError(400, "INVALID_PARAM", "months は1〜12の整数の配列です。")
    return [_parse_month(item) for item in raw]


def _parse_month_key(raw: Any) -> int:
    if isinstance(raw, str) and raw.isdigit():
        return _parse_month(int(raw))
    return _parse_month(raw)


def _months_have_duplicates(months: list[int]) -> bool:
    return len(months) != len(set(months))


_ONLY_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _parse_dry_run(raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise ApiError(400, "INVALID_PARAM", "dry_run は true / false です。")
    return raw


def _parse_only_months(raw: Any) -> frozenset[tuple[int, int]]:
    """暦年の YYYY-MM。mapping のあと、この (year, month) の列だけ書く。"""
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "INVALID_PARAM", "only_months は YYYY-MM の配列です。")
    found: set[tuple[int, int]] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ApiError(400, "INVALID_PARAM", "only_months の各要素は YYYY-MM です。")
        match = _ONLY_MONTH_RE.fullmatch(item)
        if match is None:
            raise ApiError(400, "INVALID_PARAM", "only_months の各要素は YYYY-MM です。")
        year = int(match.group(1))
        month = int(match.group(2))
        if month < 1 or month > 12:
            raise ApiError(400, "INVALID_PARAM", "only_months の月は 1〜12 です。")
        found.add((year, month))
    return frozenset(found)


def _parse_ingest_values_by_column(
    values_raw: Any, months: list[int],
) -> dict[int, float | None]:
    """列インデックス → 値。重複月があるときは配列（または列番号キー）必須。"""
    n = len(months)
    has_dup = _months_have_duplicates(months)
    if isinstance(values_raw, list):
        if len(values_raw) != n:
            raise ApiError(
                400, "INVALID_PARAM",
                f"values 配列は months と同じ長さ（{n}）にしてください。",
            )
        return {i: _parse_value(val) for i, val in enumerate(values_raw)}

    if not isinstance(values_raw, dict):
        raise ApiError(
            400, "INVALID_PARAM",
            "values は月キーのオブジェクト、または列順の配列です。",
        )

    if has_dup:
        by_col: dict[int, float | None] = {}
        for key, val in values_raw.items():
            if isinstance(key, str) and key.isdigit():
                idx = int(key)
            elif isinstance(key, int) and not isinstance(key, bool):
                idx = key
            else:
                raise ApiError(
                    400, "INVALID_PARAM",
                    'months に重複があるとき values は配列か列番号キー（"0"…）のオブジェクトです。',
                )
            if idx < 0 or idx >= n:
                raise ApiError(400, "INVALID_PARAM", "values の列番号が months の範囲外です。")
            by_col[idx] = _parse_value(val)
        # 月番号キー {"6": …} は曖昧なので拒否（0 始まりの列番号だけ許可）
        if (
            by_col
            and 0 not in by_col
            and set(by_col) <= set(months)
            and all(1 <= k <= 12 for k in by_col)
        ):
            raise ApiError(
                400, "INVALID_PARAM",
                'months に重複があるとき values は配列か列番号キー（"0"…）のオブジェクトです。',
            )
        return by_col

    month_set = set(months)
    by_col: dict[int, float | None] = {}
    for key, val in values_raw.items():
        month = _parse_month_key(key)
        if month not in month_set:
            raise ApiError(400, "INVALID_PARAM", "values の月が months にありません。")
        by_col[months.index(month)] = _parse_value(val)
    return by_col


def _parse_ingest_rows(
    raw: Any, months: list[int], code: str, *, as_of: date | None = None,
) -> list[tuple[str, bool, str, dict[int, float | None]]]:
    """各行の values は列インデックス → 値（null 可）。unit は必須。"""
    if not isinstance(raw, list):
        raise ApiError(400, "INVALID_PARAM", "rows は {element, values} の配列です。")
    parsed: list[tuple[str, bool, str, dict[int, float | None]]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ApiError(400, "INVALID_PARAM", "rows の各要素は {element, values} です。")
        force = item.get("force") is True
        unit = _parse_unit(item.get("unit"))
        if unit is None:
            raise ApiError(
                400, "UNIT_REQUIRED",
                "unit は number / yoy_index / yoy_pct です。省略できません。",
            )
        element = _canonical_element(item.get("element"), unit, code, as_of=as_of)
        values = _parse_ingest_values_by_column(item.get("values"), months)
        stored_unit = coerce_monthly_storage(unit, None)[0]
        stored: dict[int, float | None] = {}
        for col, val in values.items():
            _, stored_val = coerce_monthly_storage(unit, val)
            stored[col] = stored_val
        parsed.append((element, force, stored_unit, stored))
    return _merge_same_ingest_rows(parsed)


def _merge_same_ingest_rows(
    parsed: list[tuple[str, bool, str, dict[int, float | None]]],
) -> list[tuple[str, bool, str, dict[int, float | None]]]:
    """銘柄エイリアスで同じ保存名になった行（別表の同じ数字）を 1 行に寄せる。

    同じ列に違う値があれば 409。後勝ちで黙って上書きしない。
    """
    merged: dict[str, tuple[str, bool, str, dict[int, float | None]]] = {}
    for element, force, unit, values in parsed:
        prev = merged.get(element)
        if prev is None:
            merged[element] = (element, force, unit, dict(values))
            continue
        if prev[2] != unit:
            raise ApiError(409, "ROW_MERGE_CONFLICT",
                           f"{element}: 同じ行名に寄る行の unit が違います。")
        vals = prev[3]
        for col, v in values.items():
            old = vals.get(col)
            if v is None:
                vals.setdefault(col, None)
            elif old is None:
                vals[col] = v
            elif abs(old - v) > 1e-9:
                raise ApiError(409, "ROW_MERGE_CONFLICT",
                               f"{element}: 同じ行名に寄る行の値が食い違います（列 {col}: {old} / {v}）。")
        merged[element] = (element, prev[1] or force, unit, vals)
    return list(merged.values())


def _ingest_error(exc: MonthlyIngestError) -> ApiError:
    extra = exc.extra or None
    return ApiError(exc.status, exc.code, exc.message, extra=extra)


@router_v1.post("/tables/{code}/{year}/elements")
def post_element(code: str, year: str, response: Response,
                 payload: dict | None = Body(None), db: Session = Depends(get_session),
                 _: None = Depends(forbid_proxied_monthly_write)):
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    force = body.get("force") is True
    unit = _parse_unit(body.get("unit"))
    if unit is None:
        raise ApiError(
            400, "UNIT_REQUIRED",
            "unit は number / yoy_index / yoy_pct です。省略できません。",
        )
    element = _canonical_element(body.get("element"), unit, code, year=year_i)
    unit = coerce_monthly_storage(unit, None)[0]
    _require_stock(db, code)
    created = False

    def _write() -> None:
        nonlocal created
        created = _ensure_element(db, code, year_i, element, force, unit)

    run_sqlite_write(db, _write)
    row = _get_element(db, code, year_i, element)
    payload_out = {
        "code": code, "year": year_i, "element": element,
        "unit": _element_unit(row), "created": created,
    }
    if created:
        response.status_code = 201
        return payload_out
    response.status_code = 200
    return payload_out


@router_v1.put("/tables/{code}/{year}/elements")
def put_element(code: str, year: str, payload: dict | None = Body(None),
                db: Session = Depends(get_session),
                _: None = Depends(forbid_proxied_monthly_write)):
    """既存行の unit だけ変える。値の再送は不要。新規作成はしない。"""
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    element = _lookup_element(body.get("element"), code, year=year_i)
    if "unit" not in body:
        raise ApiError(400, "INVALID_PARAM", "unit は number / yoy_index / yoy_pct です。")
    incoming = _parse_unit(body.get("unit"))
    if incoming is None:
        raise ApiError(400, "INVALID_PARAM", "unit は number / yoy_index / yoy_pct です。")
    unit = coerce_monthly_storage(incoming, None)[0]
    _require_stock(db, code)
    _get_table(db, code, year_i)

    # 保存名は起動時に作り直さないので、unit を変えた行はここで新しい unit の正規名へ付け替える
    renamed = canonicalize_element_label(
        apply_issuer_aliases(code, canonicalize_element_label(element, unit), year=year_i), unit,
    )

    def _write() -> None:
        row = _get_element(db, code, year_i, element)
        row.unit = unit
        db.flush()
        try:
            rename_monthly_element(db, code, year_i, element, renamed, strict=True)
        except MonthlyIngestError as exc:
            raise _ingest_error(exc) from exc

    run_sqlite_write(db, _write)
    return {"code": code, "year": year_i, "element": renamed, "unit": unit}


@router_v1.delete("/tables/{code}/{year}/elements")
def delete_element(code: str, year: str, payload: dict | None = Body(None),
                   db: Session = Depends(get_session),
                   _: None = Depends(forbid_proxied_monthly_write)):
    """空の行だけ消す。値が残っていれば 409。表は残す。"""
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    element = _lookup_element(body.get("element"), code, year=year_i)
    _require_stock(db, code)
    _get_table(db, code, year_i)
    _get_element(db, code, year_i, element)
    leftover = (
        db.query(MonthlyValue)
        .filter_by(code=code, year=year_i, element=element)
        .count()
    )
    if leftover:
        raise ApiError(409, "ELEMENT_HAS_VALUES", "値が残っている行は消せません。先にセルを空にしてください。")

    def _write() -> None:
        delete_monthly_element(db, code, year_i, element)

    run_sqlite_write(db, _write)
    return {"code": code, "year": year_i, "element": element, "deleted": True}


@router_v1.post("/tables/{code}/ingest")
def ingest_monthly(code: str, payload: dict | None = Body(None),
                   db: Session = Depends(get_session),
                   _: None = Depends(forbid_proxied_monthly_write)):
    """PDF列順 + as_of 時点の最新 statements.fiscal_year_end から暦年を割り当てる。year は受け取らない。

    dry_run は mapping と書くはずのセルだけ返す。only_months は mapping のあとで暦年の列を残す。
    """
    code = _parse_code(code)
    body = _require_object(payload)
    _require_stock(db, code)
    months = _parse_months(body.get("months"))
    try:
        as_of = parse_as_of(body.get("as_of"))
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc
    dry_run = _parse_dry_run(body["dry_run"]) if "dry_run" in body else False
    only_months = _parse_only_months(body["only_months"]) if "only_months" in body else None
    rows = _parse_ingest_rows(body.get("rows"), months, code, as_of=as_of)
    fy_end_rows = [
        (disclosed, fy_end)
        for disclosed, fy_end in (
            db.query(Statement.disclosed_date, Statement.fiscal_year_end)
            .filter(Statement.code == code, Statement.fiscal_year_end.isnot(None))
            .all()
        )
    ]
    try:
        fy_end_month, layout, mapping = map_pdf_columns(months, fy_end_rows, as_of)
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc

    updated: list[dict] = []
    changed = False
    posted = [(element, unit) for element, _force, unit, _values in rows]

    def _apply() -> None:
        nonlocal updated, changed
        updated = []
        changed = False
        for element, force, unit, values_by_col in rows:
            by_year: dict[int, list[int]] = defaultdict(list)
            names: dict[int, str] = {}
            for col, (month, year_i) in enumerate(mapping):
                if col not in values_by_col:
                    continue
                if only_months is not None and (year_i, month) not in only_months:
                    continue
                # as_of の alias のあと、列の暦年の alias も入ってくる名前に掛ける（既存行は作り直さない）
                name = names.setdefault(year_i, _year_alias(code, element, unit, year_i))
                if not dry_run:
                    if year_i not in by_year:
                        _ensure_element(db, code, year_i, name, force, unit, posted=posted)
                    if _upsert_value(db, code, year_i, name, month, values_by_col[col]):
                        changed = True
                by_year[year_i].append(month)
            for year_i in sorted(by_year):
                updated.append({"year": year_i, "element": names[year_i], "months": by_year[year_i]})
        if not dry_run and changed:
            _touch_monthly_issuer(db, code)

    try:
        # 新着はセルが変わったときだけ。dry_run と rows=[] の年割当確認では動かさない
        if dry_run:
            _apply()
            db.rollback()
        else:
            run_sqlite_write(db, _apply)
    except MonthlyIngestError as exc:
        db.rollback()
        raise _ingest_error(exc) from exc

    out = {
        "code": code,
        "fy_end_month": fy_end_month,
        "layout": layout,
        "as_of": as_of.isoformat(),
        "mapping": serialize_column_mapping(mapping),
        "updated": updated,
    }
    if dry_run:
        out["dry_run"] = True
    return out


@router_v1.put("/tables/{code}/industry")
def put_industry(code: str, payload: dict | None = Body(None),
                 db: Session = Depends(get_session),
                 _: None = Depends(forbid_proxied_monthly_write)):
    code = _parse_code(code)
    industry = _parse_industry(_require_object(payload).get("industry"))
    _require_stock(db, code)
    _require_any_monthly_table(db, code)

    def _write() -> None:
        row = db.get(MonthlyIssuer, code)
        if row is None:
            db.add(MonthlyIssuer(code=code, industry=industry))
        else:
            row.industry = industry

    run_sqlite_write(db, _write)
    return {"code": code, "industry": industry}


def list_tables_payload(db: Session) -> list[dict]:
    """月次表がある銘柄。名前は stocks を LEFT JOIN。無いときは null。"""
    rows = (db.query(
                MonthlyTable.code, MonthlyTable.year,
                Stock.name, Stock.market, Stock.sector33)
            .outerjoin(Stock, Stock.code == MonthlyTable.code)
            .order_by(MonthlyTable.code, MonthlyTable.year)
            .all())
    by_code: dict[str, dict] = {}
    for code, year, name, market, sector33 in rows:
        item = by_code.get(code)
        if item is None:
            item = {
                "code": code,
                "name": name,
                "years": [],
                "market": market,
                "sector33": sector33,
            }
            by_code[code] = item
        item["years"].append(year)

    codes = list(by_code)
    issuers: dict[str, MonthlyIssuer] = {}
    elems_by: dict[str, list[Any]] = defaultdict(list)
    vals_by: dict[str, list[Any]] = defaultdict(list)
    if codes:
        issuers = {
            r.code: r for r in db.query(MonthlyIssuer).filter(MonthlyIssuer.code.in_(codes))
        }
        # 読むだけなので Row で受ける。値は行ごとの最新月しか順位に効かないのでそれだけ引く
        for e in db.execute(select(
                MonthlyElement.code, MonthlyElement.year, MonthlyElement.element,
                MonthlyElement.unit, MonthlyElement.sort_order)
                .where(MonthlyElement.code.in_(codes))):
            elems_by[e.code].append(e)
        last = (select(MonthlyValue.code, MonthlyValue.year, MonthlyValue.element,
                       func.max(MonthlyValue.month).label("month"))
                .where(MonthlyValue.code.in_(codes))
                .group_by(MonthlyValue.code, MonthlyValue.year, MonthlyValue.element)
                .subquery())
        for v in db.execute(select(
                MonthlyValue.code, MonthlyValue.year, MonthlyValue.element,
                MonthlyValue.month, MonthlyValue.value)
                .join(last, and_(last.c.code == MonthlyValue.code,
                                 last.c.year == MonthlyValue.year,
                                 last.c.element == MonthlyValue.element,
                                 last.c.month == MonthlyValue.month))):
            vals_by[v.code].append(v)

    out = []
    for code, item in by_code.items():
        issuer = issuers.get(code)
        last_at = issuer.last_ingested_at if issuer is not None else None
        is_new, ingested_today = _new_flags(last_at)
        elems = elems_by.get(code, [])
        vals = vals_by.get(code, [])
        item["industry"] = issuer.industry if issuer is not None else _DEFAULT_INDUSTRY
        item["last_ingested_at"] = last_at.isoformat() if last_at is not None else None
        item["is_new"] = is_new
        item["ingested_today"] = ingested_today
        item["all_store_yoy"] = _latest_yoy_for_kind(elems, vals, "all_store")
        item["existing_store_yoy"] = _latest_yoy_for_kind(elems, vals, "existing")
        out.append(item)
    return out


@router_v1.get("/tables")
def list_tables(db: Session = Depends(get_session),
                _: None = Depends(_public_get_cache)):
    return list_tables_payload(db)


def _calendar_month_values(stored: dict[str, float]) -> dict[str, float | None]:
    """GET の values。1〜12 を必ず出し、無い月は null（0 は実ゼロ）。"""
    return {str(m): stored.get(str(m)) for m in range(1, 13)}


def _auto_tree_years(db: Session, code: str, year_i: int,
                     current: list[tuple[str, str]]) -> dict[int, list[tuple[str, str]]]:
    """自動の木に使う年: 表示する 3 年と要求の年。各年は値のある行の (保存名, unit) を sort_order 順。"""
    all_years = [y for (y,) in db.query(MonthlyTable.year).filter(MonthlyTable.code == code).all()]
    want = set(monthly_display_years(all_years)) - {year_i}
    out: dict[int, list[tuple[str, str]]] = {year_i: current}
    if not want:
        return out
    with_values = set(db.query(MonthlyValue.year, MonthlyValue.element)
                      .filter(MonthlyValue.code == code, MonthlyValue.year.in_(want))
                      .distinct().all())
    for e in (db.query(MonthlyElement)
              .filter(MonthlyElement.code == code, MonthlyElement.year.in_(want))
              .order_by(MonthlyElement.year, MonthlyElement.sort_order)
              .all()):
        if (e.year, e.element) in with_values:
            out.setdefault(e.year, []).append((e.element, _element_unit(e)))
    return out


def table_year_payload(db: Session, code: str, year_i: int) -> dict:
    _get_table(db, code, year_i)
    elems = (db.query(MonthlyElement)
             .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
             .order_by(MonthlyElement.sort_order)
             .all())
    vals = (db.query(MonthlyValue)
            .filter(MonthlyValue.code == code, MonthlyValue.year == year_i)
            .all())
    by_el: dict[str, dict[str, float]] = defaultdict(dict)
    for v in vals:
        by_el[v.element][str(v.month)] = v.value
    shown = [e for e in elems if e.element in by_el]
    names = [e.element for e in shown]
    if code in MONTHLY_TREES:
        tree_rows = assign_tree_rows(code, names)
        tree_out = tree_payload(code, names) or {}
    else:
        auto = build_auto_tree(code, _auto_tree_years(db, code, year_i, [(e.element, _element_unit(e)) for e in shown]))
        tree_rows = {n: auto.rows[n] for n in names}
        tree_out = auto_tree_payload(auto, names)
    out_elements = []
    for e in shown:
        row = {
            "element": e.element,
            "unit": _element_unit(e),
            "label": monthly_element_label(e.element, _element_unit(e), code),
            "values": _calendar_month_values(by_el[e.element]),
        }
        if e.element in tree_rows:
            row["tree"] = tree_rows[e.element]
        out_elements.append(row)
    out = {"code": code, "year": year_i, "elements": out_elements}
    out.update(tree_out)
    return out


@router_v1.get("/tables/{code}/{year}")
def get_table(code: str, year: str, db: Session = Depends(get_session),
              _: None = Depends(_public_get_cache)):
    code = _parse_code(code)
    year_i = _parse_year(year)
    return table_year_payload(db, code, year_i)


def list_years_payload(db: Session, code: str) -> dict:
    years = [y for (y,) in (db.query(MonthlyTable.year)
                            .filter(MonthlyTable.code == code)
                            .order_by(MonthlyTable.year)
                            .all())]
    return {"code": code, "years": years}


@router_v1.get("/tables/{code}")
def list_years(code: str, db: Session = Depends(get_session),
               _: None = Depends(_public_get_cache)):
    code = _parse_code(code)
    return list_years_payload(db, code)


@router_v1.put("/tables/{code}/{year}/values")
def put_value(code: str, year: str, payload: dict | None = Body(None),
              db: Session = Depends(get_session),
              _: None = Depends(forbid_proxied_monthly_write)):
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    element = _lookup_element(body.get("element"), code, year=year_i)
    if "month" not in body:
        raise ApiError(400, "INVALID_PARAM", "month は1〜12の整数です。")
    if "value" not in body:
        raise ApiError(400, "INVALID_PARAM", "value がありません。")
    month = _parse_month(body.get("month"))
    value = _parse_value(body.get("value"))
    _require_stock(db, code)
    _get_table(db, code, year_i)
    _get_element(db, code, year_i, element)

    def _write() -> None:
        _upsert_value(db, code, year_i, element, month, value)

    run_sqlite_write(db, _write)
    return {"code": code, "year": year_i, "element": element, "updated": [month]}


@router_v1.put("/tables/{code}/{year}/values/batch")
def put_values_batch(code: str, year: str, payload: dict | None = Body(None),
                     db: Session = Depends(get_session),
                     _: None = Depends(forbid_proxied_monthly_write)):
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    element = _lookup_element(body.get("element"), code, year=year_i)
    items = body.get("values")
    if not isinstance(items, list):
        raise ApiError(400, "INVALID_PARAM", "values は {month, value} の配列です。")

    ops: dict[int, float | None] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ApiError(400, "INVALID_PARAM", "values の各要素は {month, value} です。")
        if "month" not in item:
            raise ApiError(400, "INVALID_PARAM", "month は1〜12の整数です。")
        if "value" not in item:
            raise ApiError(400, "INVALID_PARAM", "value がありません。")
        month = _parse_month(item.get("month"))
        value = _parse_value(item.get("value"))
        ops[month] = value

    _require_stock(db, code)
    _get_table(db, code, year_i)
    _get_element(db, code, year_i, element)

    def _write() -> None:
        for month, value in ops.items():
            _upsert_value(db, code, year_i, element, month, value)

    run_sqlite_write(db, _write)
    return {"code": code, "year": year_i, "element": element, "updated": list(ops.keys())}


# --- 年の表を 1 枚の文書として読み書きする（MCP の monthly_get_doc / monthly_put_doc） ---

_DOC_UNITS = ("number", "yoy_index")


def _doc_state(db: Session, code: str, year_i: int) -> list[tuple[MonthlyElement, dict[int, float]]]:
    """空の行も含めて sort_order 順。"""
    elems = (db.query(MonthlyElement)
             .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
             .order_by(MonthlyElement.sort_order, MonthlyElement.element)
             .all())
    vals: dict[str, dict[int, float]] = defaultdict(dict)
    for v in db.query(MonthlyValue).filter(MonthlyValue.code == code, MonthlyValue.year == year_i):
        vals[v.element][v.month] = v.value
    return [(e, vals.get(e.element, {})) for e in elems]


def _doc_version(state: list[tuple[MonthlyElement, dict[int, float]]]) -> str:
    raw = [
        [e.element, _element_unit(e), e.sort_order, sorted(v.items())]
        for e, v in state
    ]
    blob = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def table_doc_payload(db: Session, code: str, year_i: int) -> dict:
    _get_table(db, code, year_i)
    state = _doc_state(db, code, year_i)
    tree_by_el = {
        r["element"]: r["tree"]["path"]
        for r in table_year_payload(db, code, year_i)["elements"] if "tree" in r
    }
    rows = []
    for e, v in state:
        row = {
            "element": e.element,
            "unit": _element_unit(e),
            "values": {str(m): v.get(m) for m in range(1, 13)},
        }
        if e.element in tree_by_el:
            row["tree"] = tree_by_el[e.element]
        rows.append(row)
    return {"code": code, "year": year_i, "version": _doc_version(state), "elements": rows}


@router_v1.get("/tables/{code}/{year}/doc")
def get_table_doc(code: str, year: str, db: Session = Depends(get_session)):
    """PUT /doc の元になる文書。空の行も返す。version は PUT にそのまま渡す。"""
    code = _parse_code(code)
    year_i = _parse_year(year)
    return table_doc_payload(db, code, year_i)


def _doc_name(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(400, "INVALID_PARAM", f"{field} は1〜50文字の文字列です。")
    s = raw.strip()
    if len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", f"{field} は1〜50文字の文字列です。")
    return s


def _doc_values(raw: Any, element: str) -> dict[int, float | None]:
    if not isinstance(raw, dict):
        raise ApiError(400, "INVALID_PARAM", f"{element}: values は {{\"1\": 値, …, \"12\": 値}} です。")
    out: dict[int, float | None] = {m: None for m in range(1, 13)}
    for k, v in raw.items():
        try:
            month = int(k)
        except (TypeError, ValueError):
            raise ApiError(400, "INVALID_PARAM", f"{element}: 月のキーは 1〜12 です。") from None
        if str(month) != str(k).strip() or not 1 <= month <= 12:
            raise ApiError(400, "INVALID_PARAM", f"{element}: 月のキーは 1〜12 です。")
        out[month] = _parse_value(v)
    return out


def _doc_plan(db: Session, code: str, year_i: int, body: dict) -> dict:
    """PUT /doc の差分。書かない。version が合わなければ 409。"""
    version = body.get("version")
    if not isinstance(version, str) or not version:
        raise ApiError(400, "INVALID_PARAM", "version は GET /doc の version です。")
    items = body.get("elements")
    if not isinstance(items, list):
        raise ApiError(400, "INVALID_PARAM", "elements は行の配列です。")
    _require_stock(db, code)
    _get_table(db, code, year_i)
    state = _doc_state(db, code, year_i)
    current = _doc_version(state)
    if version != current:
        raise ApiError(409, "VERSION_MISMATCH",
                       "GET のあとに表が変わりました。もう一度 GET /doc からやり直してください。",
                       extra={"version": current})
    cur = {e.element: (e, v) for e, v in state}

    rows: list[dict] = []
    seen_was: set[str] = set()
    seen_new: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ApiError(400, "INVALID_PARAM", "elements の各要素は {element, was?, unit?, values?} です。")
        new = _doc_name(item.get("element"), "element")
        was = _doc_name(item["was"], "was") if item.get("was") is not None else (new if new in cur else None)
        if was is not None and was not in cur:
            raise ApiError(404, "ELEMENT_NOT_FOUND", f"{was}: was の行がありません。")
        if was is not None:
            if was in seen_was:
                raise ApiError(400, "INVALID_PARAM", f"{was}: 同じ行が 2 回あります。")
            seen_was.add(was)
        if new in seen_new:
            raise ApiError(400, "INVALID_PARAM", f"{new}: 行名が重なります。")
        seen_new.add(new)
        unit = None
        if item.get("unit") is not None:
            if item["unit"] not in _DOC_UNITS:
                raise ApiError(400, "INVALID_PARAM", f"{new}: unit は number / yoy_index です（保存形）。")
            unit = item["unit"]
        if was is None and unit is None:
            raise ApiError(400, "UNIT_REQUIRED", f"{new}: 新しい行には unit が要ります。")
        values = _doc_values(item["values"], new) if "values" in item and item["values"] is not None else None
        rows.append({"was": was, "element": new, "unit": unit, "values": values})

    deleted = [n for n in cur if n not in seen_was]

    renamed, created, unit_changed = [], [], []
    cells_set = cells_cleared = 0
    for r in rows:
        if r["was"] is None:
            created.append({"element": r["element"], "unit": r["unit"]})
            old_vals: dict[int, float] = {}
        else:
            e, old_vals = cur[r["was"]]
            if r["was"] != r["element"]:
                renamed.append({"was": r["was"], "element": r["element"], "cells": len(old_vals)})
            if r["unit"] is not None and r["unit"] != _element_unit(e):
                unit_changed.append({"element": r["element"], "from": _element_unit(e), "to": r["unit"]})
        if r["values"] is not None:
            for m, v in r["values"].items():
                old = old_vals.get(m)
                if v is None and old is not None:
                    cells_cleared += 1
                elif v is not None and (old is None or old != v):
                    cells_set += 1
    changes = {
        "renamed": renamed,
        "created": created,
        "deleted": [{"element": n, "cells": len(cur[n][1])} for n in deleted],
        "unit_changed": unit_changed,
        "cells": {"set": cells_set, "cleared": cells_cleared},
    }
    structural = bool(renamed or created or deleted or unit_changed)

    final = [r["element"] for r in rows]
    warnings = []
    if code in MONTHLY_TREES:
        assigned = assign_tree_rows(code, final)
        missing = [n for n in final if n not in assigned]
        if missing:
            warnings.append({"code": "NOT_IN_TREE", "elements": missing,
                             "message": "config/monthly_row_order.json の木に入らない行があります。"})
    return {"rows": rows, "deleted": deleted, "changes": changes,
            "structural": structural, "warnings": warnings}


def _doc_apply(db: Session, code: str, year_i: int, plan: dict) -> None:
    for name in plan["deleted"]:
        delete_monthly_element(db, code, year_i, name)
    moving = [r for r in plan["rows"] if r["was"] is not None and r["was"] != r["element"]]
    # 入れ替え（A→B, B→A）でも主キーがぶつからないように、いったん仮の名前へ逃がす
    for i, r in enumerate(moving):
        rename_monthly_element(db, code, year_i, r["was"], f"\x00doc{i}")
    for i, r in enumerate(moving):
        rename_monthly_element(db, code, year_i, f"\x00doc{i}", r["element"])
    next_order = (db.query(func.max(MonthlyElement.sort_order))
                  .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
                  .scalar() or 0) + 1
    for r in plan["rows"]:
        if r["was"] is None:
            db.add(MonthlyElement(code=code, year=year_i, element=r["element"],
                                  sort_order=next_order, unit=r["unit"]))
            next_order += 1
            db.flush()
        elif r["unit"] is not None:
            _get_element(db, code, year_i, r["element"]).unit = r["unit"]
        if r["values"] is not None:
            for m, v in r["values"].items():
                _upsert_value(db, code, year_i, r["element"], m, v)
    db.flush()


@router_v1.put("/tables/{code}/{year}/doc")
def put_table_doc(code: str, year: str, payload: dict | None = Body(None),
                  db: Session = Depends(get_session),
                  _: None = Depends(forbid_proxied_monthly_write)):
    """GET /doc を直して丸ごと返す。行名はそのまま使う（正規化・エイリアスを掛けない）。

    was で改名、文書に無い行は削除、was の無い新しい名前は作成。values を省いた行は値をそのまま残す。
    改名・作成・削除・unit 変更は allow_structure_change=true のときだけ。dry_run=true は書かずに差分を返す。
    """
    code = _parse_code(code)
    year_i = _parse_year(year)
    body = _require_object(payload)
    allow = body.get("allow_structure_change") is True
    dry_run = body.get("dry_run") is True
    plan = _doc_plan(db, code, year_i, body)
    out = {"code": code, "year": year_i, "dry_run": dry_run,
           "changes": plan["changes"], "warnings": plan["warnings"]}
    if plan["structural"] and not allow and not dry_run:
        raise ApiError(409, "STRUCTURE_CHANGED",
                       "行の改名・作成・削除・unit 変更があります。allow_structure_change=true で書きます。",
                       extra={"changes": plan["changes"]})
    if dry_run:
        db.rollback()
        return out

    def _write() -> None:
        _doc_apply(db, code, year_i, _doc_plan(db, code, year_i, body))

    run_sqlite_write(db, _write)
    out["version"] = _doc_version(_doc_state(db, code, year_i))
    return out

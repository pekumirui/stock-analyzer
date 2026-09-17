from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, defer

from .config import (
    EARNINGS_PARAMS as EP, EARNINGS_EXCLUDE_PRODUCT_CATEGORIES, MONTHLY_NEW_DAYS,
    RS_PRODUCT_CATEGORIES,
)
from .db import delete_monthly_element, fold_aliased_element, fold_same_yoy_element, get_session
from .models import (
    Stock, DailyPrice, Topix, Statement, Indicator, EarningsEvent,
    MonthlyIssuer, MonthlyTable, MonthlyElement, MonthlyValue,
)
from .analytics import (METRIC_KEYS, PL_KEYS, quarter_summary, is_actual, period_label,
                        disclosure_state, earnings_detail as build_earnings_detail)
from .monthly_ingest import (
    MonthlyIngestError, apply_issuer_aliases, canonicalize_element_label,
    coerce_monthly_storage, map_pdf_columns, monthly_series_kind, parse_as_of,
    parse_monthly_unit, reject_prior_period_element, same_yoy_element,
    serialize_column_mapping, yoy_display_number,
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
    rows = (db.query(Stock).filter(
        (Stock.name.contains(q)) | (Stock.code.startswith(q))).limit(20).all())
    return [{"code": s.code, "name": s.name, "market": s.market} for s in rows]


router_v1 = APIRouter(prefix="/api/v1")

_INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
_SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"
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


def _latest_yoy_for_kind(
    elems: list[MonthlyElement], vals: list[MonthlyValue], kind: str,
) -> float | None:
    matched = {
        (e.year, e.element): e
        for e in elems
        if monthly_series_kind(e.element, e.unit) == kind
    }
    best: tuple[int, int, float, str] | None = None
    for v in vals:
        el = matched.get((v.year, v.element))
        if el is None:
            continue
        if best is None or (v.year, v.month) > (best[0], best[1]):
            best = (v.year, v.month, float(v.value), el.unit or "number")
    if best is None:
        return None
    return yoy_display_number(best[2], best[3])


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


def _upsert_value(db: Session, code: str, year: int, element: str, month: int, value: float | None) -> None:
    row = db.get(MonthlyValue, (code, year, element, month))
    if value is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        db.add(MonthlyValue(code=code, year=year, element=element, month=month, value=value))
    else:
        row.value = value


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


def _ensure_element(db: Session, code: str, year_i: int, element: str, force: bool,
                    unit: str) -> bool:
    _require_stock(db, code)
    try:
        folded = fold_same_yoy_element(
            db, code, year_i, element, unit, strict=True,
        )
        if folded is not None:
            _apply_incoming_unit(folded, unit, force)
            return False
        folded = fold_aliased_element(
            db, code, year_i, element, unit, strict=True,
        )
        if folded is not None:
            _apply_incoming_unit(folded, unit, force)
            return False
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc
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
    return parsed


def _ingest_error(exc: MonthlyIngestError) -> ApiError:
    extra = exc.extra or None
    return ApiError(exc.status, exc.code, exc.message, extra=extra)


@router_v1.post("/tables/{code}/{year}/elements")
def post_element(code: str, year: str, response: Response,
                 payload: dict | None = Body(None), db: Session = Depends(get_session)):
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
    created = _ensure_element(db, code, year_i, element, force, unit)
    row = _get_element(db, code, year_i, element)
    db.commit()
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
                db: Session = Depends(get_session)):
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
    row = _get_element(db, code, year_i, element)
    row.unit = unit
    db.commit()
    return {"code": code, "year": year_i, "element": element, "unit": unit}


@router_v1.delete("/tables/{code}/{year}/elements")
def delete_element(code: str, year: str, payload: dict | None = Body(None),
                   db: Session = Depends(get_session)):
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
    delete_monthly_element(db, code, year_i, element)
    db.commit()
    return {"code": code, "year": year_i, "element": element, "deleted": True}


@router_v1.post("/tables/{code}/ingest")
def ingest_monthly(code: str, payload: dict | None = Body(None), db: Session = Depends(get_session)):
    """PDF列順 + as_of 時点の最新 statements.fiscal_year_end から暦年を割り当てる。year は受け取らない。"""
    code = _parse_code(code)
    body = _require_object(payload)
    _require_stock(db, code)
    months = _parse_months(body.get("months"))
    try:
        as_of = parse_as_of(body.get("as_of"))
    except MonthlyIngestError as exc:
        raise _ingest_error(exc) from exc
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
    try:
        for element, force, unit, values_by_col in rows:
            by_year: dict[int, list[int]] = defaultdict(list)
            for col, (month, year_i) in enumerate(mapping):
                if col not in values_by_col:
                    continue
                _ensure_element(db, code, year_i, element, force, unit)
                _upsert_value(db, code, year_i, element, month, values_by_col[col])
                by_year[year_i].append(month)
            for year_i in sorted(by_year):
                updated.append({"year": year_i, "element": element, "months": by_year[year_i]})
        _touch_monthly_issuer(db, code)
        db.commit()
    except MonthlyIngestError as exc:
        db.rollback()
        raise _ingest_error(exc) from exc
    except Exception:
        db.rollback()
        raise

    return {
        "code": code,
        "fy_end_month": fy_end_month,
        "layout": layout,
        "as_of": as_of.isoformat(),
        "mapping": serialize_column_mapping(mapping),
        "updated": updated,
    }


@router_v1.put("/tables/{code}/industry")
def put_industry(code: str, payload: dict | None = Body(None),
                 db: Session = Depends(get_session)):
    code = _parse_code(code)
    industry = _parse_industry(_require_object(payload).get("industry"))
    _require_stock(db, code)
    _require_any_monthly_table(db, code)
    row = db.get(MonthlyIssuer, code)
    if row is None:
        db.add(MonthlyIssuer(code=code, industry=industry))
    else:
        row.industry = industry
    db.commit()
    return {"code": code, "industry": industry}


@router_v1.get("/tables")
def list_tables(db: Session = Depends(get_session)):
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
    elems_by: dict[str, list[MonthlyElement]] = defaultdict(list)
    vals_by: dict[str, list[MonthlyValue]] = defaultdict(list)
    if codes:
        issuers = {
            r.code: r for r in db.query(MonthlyIssuer).filter(MonthlyIssuer.code.in_(codes))
        }
        for e in db.query(MonthlyElement).filter(MonthlyElement.code.in_(codes)):
            elems_by[e.code].append(e)
        for v in db.query(MonthlyValue).filter(MonthlyValue.code.in_(codes)):
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


def _calendar_month_values(stored: dict[str, float]) -> dict[str, float | None]:
    """GET の values。1〜12 を必ず出し、無い月は null（0 は実ゼロ）。"""
    return {str(m): stored.get(str(m)) for m in range(1, 13)}


@router_v1.get("/tables/{code}/{year}")
def get_table(code: str, year: str, db: Session = Depends(get_session)):
    code = _parse_code(code)
    year_i = _parse_year(year)
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
    return {
        "code": code,
        "year": year_i,
        "elements": [
            {
                "element": e.element,
                "unit": _element_unit(e),
                "values": _calendar_month_values(by_el[e.element]),
            }
            for e in elems
            if e.element in by_el
        ],
    }


@router_v1.get("/tables/{code}")
def list_years(code: str, db: Session = Depends(get_session)):
    code = _parse_code(code)
    years = [y for (y,) in (db.query(MonthlyTable.year)
                            .filter(MonthlyTable.code == code)
                            .order_by(MonthlyTable.year)
                            .all())]
    return {"code": code, "years": years}


@router_v1.put("/tables/{code}/{year}/values")
def put_value(code: str, year: str, payload: dict | None = Body(None),
              db: Session = Depends(get_session)):
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
    _upsert_value(db, code, year_i, element, month, value)
    db.commit()
    return {"code": code, "year": year_i, "element": element, "updated": [month]}


@router_v1.put("/tables/{code}/{year}/values/batch")
def put_values_batch(code: str, year: str, payload: dict | None = Body(None),
                     db: Session = Depends(get_session)):
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
    try:
        for month, value in ops.items():
            _upsert_value(db, code, year_i, element, month, value)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"code": code, "year": year_i, "element": element, "updated": list(ops.keys())}

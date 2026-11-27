"""月次 API の共通部分。ApiError、入力の検査、行と値の書き込み。"""
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import re
import unicodedata
from typing import Any, Sequence

from fastapi import Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...config import MONTHLY_NEW_DAYS
from ...models import Stock, MonthlyIssuer, MonthlyTable, MonthlyElement, MonthlyValue
from ...monthly.ingest import (
    MonthlyIngestError, apply_issuer_aliases, canonicalize_element_label, coerce_monthly_storage,
    monthly_element_label, monthly_series_kind, parse_monthly_unit, reject_prior_period_element,
    same_yoy_element, uncovered_yoy_number_twins,
)
from ...monthly.publications import PublicationError, parse_publication, record_manual, record_written


def publication_from_body(body: dict):
    if "publication" not in body:
        return None
    try:
        return parse_publication(body["publication"])
    except PublicationError as exc:
        raise ApiError(400, "INVALID_PARAM", exc.message) from exc


def stamp_new_months(db: Session, code: str, before: set[tuple[int, int]], pub) -> None:
    if pub is None:
        record_manual(db, code, before)
    else:
        record_written(db, code, before, pub)


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
        code, canonicalize_element_label(raw_name, unit, code),
        year=year, as_of=as_of,
    )
    s = canonicalize_element_label(s, unit, code)
    if not s or len(s) > 50:
        raise ApiError(400, "INVALID_PARAM", "element は1〜50文字の文字列です。")
    return s


def _year_alias(code: str, element: str, unit: str | None, year: int) -> str:
    """正規化済みの入ってくる名前に、列の暦年の銘柄エイリアスを掛ける。"""
    return canonicalize_element_label(
        apply_issuer_aliases(code, element, year=year), unit, code,
    )


def _lookup_element(raw: Any, code: str, *, year: int | None = None) -> str:
    """PUT は保存名に合わせる。トークン正規化と銘柄エイリアスを掛ける。"""
    raw_name = _parse_element(raw)
    unit = "yoy_index" if any(f in raw_name for f in ("(前年比)", "（前年比）", "前年比")) else "number"
    s = apply_issuer_aliases(
        code, canonicalize_element_label(raw_name, unit, code), year=year,
    )
    s = canonicalize_element_label(s, unit, code)
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


def _latest_yoy_by_kind(
    elems: list[MonthlyElement], vals: list[MonthlyValue],
) -> dict[str, float]:
    """系列の種類ごとに、会社計を優先して最新月の前年比。値が無い種類は入れない。

    一覧は銘柄数 × 種類ぶん呼ばれるので、行と値を 1 回ずつだけ回す。
    """
    matched: dict[tuple[int, str], tuple[str, int, MonthlyElement]] = {}
    for e in elems:
        kind = _series_kind(e.element, e.unit)
        if kind is None:
            continue
        matched[(e.year, e.element)] = (kind, _headline_yoy_tier(e), e)
    best: dict[str, tuple[tuple[int, int, int, int, str], float]] = {}
    for v in vals:
        hit = matched.get((v.year, v.element))
        if hit is None:
            continue
        kind, tier, el = hit
        rank = (tier, -v.year, -v.month, el.sort_order, el.element)
        cur = best.get(kind)
        if cur is None or rank < cur[0]:
            best[kind] = (rank, float(v.value))
    return {kind: value for kind, (_rank, value) in best.items()}


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
        and not same_yoy_element(row.element, row.unit, element, unit, code=code)
    ]
    if similar and not force:
        raise ApiError(409, "ELEMENT_SIMILAR_EXISTS", _SIMILAR_MSG, extra={"similar": similar})
    # ingest だけ。POST elements は posted=None。既存行の更新（fold 済み）はここまで来ない。
    if posted is not None and not force:
        twins = uncovered_yoy_number_twins(
            [(row.element, row.unit) for row in existing],
            element, unit, posted, code=code,
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


IngestRow = tuple[str, bool, str, dict[int, float | None], list[tuple[str, str]]]


def _parse_ingest_rows(
    raw: Any, months: list[int], code: str, *, as_of: date | None = None,
) -> list[IngestRow]:
    """各行の values は列インデックス → 値（null 可）。unit は必須。

    末尾は送られてきたままの (行名, unit)。同じ保存名に寄った行の分を全部持つ。
    """
    if not isinstance(raw, list):
        raise ApiError(400, "INVALID_PARAM", "rows は {element, values} の配列です。")
    parsed: list[IngestRow] = []
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
        parsed.append((element, force, stored_unit, stored,
                       [(_parse_element(item.get("element")), unit)]))
    return _merge_same_ingest_rows(parsed)


def _merge_same_ingest_rows(parsed: list[IngestRow]) -> list[IngestRow]:
    """銘柄エイリアスで同じ保存名になった行（別表の同じ数字）を 1 行に寄せる。

    同じ列に違う値があれば 409。後勝ちで黙って上書きしない。
    """
    merged: dict[str, IngestRow] = {}
    for element, force, unit, values, sources in parsed:
        prev = merged.get(element)
        if prev is None:
            merged[element] = (element, force, unit, dict(values), list(sources))
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
        srcs = prev[4] + [x for x in sources if x not in prev[4]]
        merged[element] = (element, prev[1] or force, unit, vals, srcs)
    return list(merged.values())


def _ingest_error(exc: MonthlyIngestError) -> ApiError:
    extra = exc.extra or None
    return ApiError(exc.status, exc.code, exc.message, extra=extra)

"""月次の行（element）の作成・unit 変更・削除と、値の訂正（values）。"""

from fastapi import APIRouter, Body, Depends, Response
from sqlalchemy.orm import Session

from ...db import get_session, run_sqlite_write
from ...monthly.store import delete_monthly_element, record_element_source, rename_monthly_element
from ...models import MonthlyValue
from ...monthly.ingest import (
    MonthlyIngestError, apply_issuer_aliases, canonicalize_element_label, coerce_monthly_storage,
)
from ._common import (
    ApiError, _canonical_element, _element_unit, _ensure_element, _get_element, _get_table,
    _ingest_error, _lookup_element, _parse_code, _parse_element, _parse_month, _parse_unit,
    _parse_value, _parse_year, _require_object, _require_stock, _upsert_value,
    forbid_proxied_monthly_write,
)

router = APIRouter()


@router.post("/tables/{code}/{year}/elements")
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
    source_label, source_unit = _parse_element(body.get("element")), unit
    unit = coerce_monthly_storage(unit, None)[0]
    _require_stock(db, code)
    created = False

    def _write() -> None:
        nonlocal created
        created = _ensure_element(db, code, year_i, element, force, unit)
        record_element_source(db, code, year_i, source_label, source_unit, element, "api")

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


@router.put("/tables/{code}/{year}/elements")
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
        apply_issuer_aliases(
            code, canonicalize_element_label(element, unit, code), year=year_i,
        ),
        unit,
        code,
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


@router.delete("/tables/{code}/{year}/elements")
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


@router.put("/tables/{code}/{year}/values")
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


@router.put("/tables/{code}/{year}/values/batch")
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

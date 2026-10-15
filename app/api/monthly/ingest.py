"""月次の新規登録 POST /tables/{code}/ingest。PDF 列順から暦年を割り当てる。"""
from collections import defaultdict

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ...db import get_session, run_sqlite_write
from ...models import Statement
from ...monthly.ingest import (
    MonthlyIngestError, map_pdf_columns, parse_as_of, serialize_column_mapping,
)
from ._common import (
    _ensure_element, _ingest_error, _parse_code, _parse_dry_run, _parse_ingest_rows, _parse_months,
    _parse_only_months, _require_object, _require_stock, _touch_monthly_issuer, _upsert_value,
    _year_alias, forbid_proxied_monthly_write,
)

router = APIRouter()


@router.post("/tables/{code}/ingest")
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

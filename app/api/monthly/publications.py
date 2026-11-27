from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ...db import get_session, run_sqlite_write
from ...models import MonthlyPublication
from ...monthly.publications import NoMonthValues, PublicationError, assign
from ._common import ApiError, _parse_code, _parse_year, _require_object, forbid_proxied_monthly_write, publication_from_body

router = APIRouter()

_PAGE = 50
_PAGE_MAX = 200


def _year_query(raw: int | None, name: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1900 or raw > 2100:
        raise ApiError(400, "INVALID_PARAM", f"{name} は西暦4桁の整数です。")
    return raw


def _confidence_query(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in ("estimated", "unknown"):
        raise ApiError(400, "INVALID_PARAM", "confidence は estimated か unknown です。")
    return raw


def _page(limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > _PAGE_MAX:
        raise ApiError(400, "INVALID_PARAM", f"limit は 1〜{_PAGE_MAX} です。")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ApiError(400, "INVALID_PARAM", "offset は 0 以上の整数です。")
    return limit, offset


def _row_out(row: MonthlyPublication) -> dict:
    return {
        "code": row.code,
        "year": row.year,
        "month": row.month,
        "published_on": row.published_on.isoformat() if row.published_on is not None else None,
        "published_time": row.published_time,
        "basis": row.basis,
        "confidence": row.confidence,
        "unknown_reason": row.unknown_reason,
        "source_ref": row.source_ref,
    }


@router.get("/tables/publications")
def list_open_publications(
    code: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    confidence: str | None = None,
    limit: int = Query(_PAGE),
    offset: int = Query(0),
    db: Session = Depends(get_session),
):
    limit_i, offset_i = _page(limit, offset)
    start = _year_query(year_from, "year_from")
    end = _year_query(year_to, "year_to")
    if start is not None and end is not None and start > end:
        raise ApiError(400, "INVALID_PARAM", "year_from は year_to 以下です。")
    level = _confidence_query(confidence)
    q = db.query(MonthlyPublication).filter(MonthlyPublication.confidence != "exact")
    if code is not None and code != "":
        q = q.filter(MonthlyPublication.code == _parse_code(code))
    if start is not None:
        q = q.filter(MonthlyPublication.year >= start)
    if end is not None:
        q = q.filter(MonthlyPublication.year <= end)
    if level is not None:
        q = q.filter(MonthlyPublication.confidence == level)
    total = q.count()
    rows = (
        q.order_by(MonthlyPublication.code, MonthlyPublication.year, MonthlyPublication.month)
        .offset(offset_i)
        .limit(limit_i)
        .all()
    )
    next_offset = offset_i + len(rows)
    return {
        "rows": [_row_out(row) for row in rows],
        "limit": limit_i,
        "offset": offset_i,
        "total": total,
        "next_offset": next_offset if next_offset < total else None,
    }


@router.put("/tables/publications/{code}/{year}/{month}")
def put_publication(code: str, year: str, month: str, payload: dict | None = Body(None),
                    db: Session = Depends(get_session),
                    _: None = Depends(forbid_proxied_monthly_write)):
    code = _parse_code(code)
    year_i = _parse_year(year)
    if not isinstance(month, str) or not month.isdigit():
        raise ApiError(400, "INVALID_PARAM", "month は1〜12の整数です。")
    month_i = int(month)
    if month_i < 1 or month_i > 12:
        raise ApiError(400, "INVALID_PARAM", "month は1〜12の整数です。")
    body = _require_object(payload)
    if "publication" not in body:
        raise ApiError(400, "INVALID_PARAM", "publication が要ります。")
    if "override" in body and not isinstance(body.get("override"), bool):
        raise ApiError(400, "INVALID_PARAM", "override は true か false です。")
    pub = publication_from_body(body)
    override = body.get("override") is True

    def _write() -> str:
        try:
            return assign(db, code, year_i, month_i, pub, override=override)
        except NoMonthValues as exc:
            raise ApiError(404, "NO_VALUES", exc.message) from exc
        except PublicationError as exc:
            raise ApiError(400, "INVALID_PARAM", exc.message) from exc

    status = run_sqlite_write(db, _write)
    row = db.get(MonthlyPublication, (code, year_i, month_i))
    out = _row_out(row)
    out["status"] = {"insert": "inserted", "replace": "updated", "keep": "kept"}[status]
    return out

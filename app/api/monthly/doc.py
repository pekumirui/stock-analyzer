"""年の表を 1 枚の文書として読み書きする（MCP の monthly_get_doc / monthly_put_doc）。"""
from collections import defaultdict
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...db import get_session, run_sqlite_write
from ...monthly.store import delete_monthly_element, record_element_source, remap_monthly_elements
from ...models import MonthlyElement, MonthlyValue
from ...monthly.tree import MONTHLY_TREES, assign_tree_rows
from ._common import (
    ApiError, _element_unit, _get_element, _get_table, _parse_code, _parse_value, _parse_year,
    _require_object, _require_stock, _upsert_value, forbid_proxied_monthly_write,
)
from .tables import table_year_payload

router = APIRouter()

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


@router.get("/tables/{code}/{year}/doc")
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
                             "message": "config/monthly/monthly_row_order.json の木に入らない行があります。"})
    return {"rows": rows, "deleted": deleted, "changes": changes,
            "structural": structural, "warnings": warnings}


def _doc_apply(db: Session, code: str, year_i: int, plan: dict) -> None:
    for name in plan["deleted"]:
        delete_monthly_element(db, code, year_i, name)
    remap_monthly_elements(db, code, year_i, {
        r["was"]: r["element"]
        for r in plan["rows"]
        if r["was"] is not None and r["was"] != r["element"]
    })
    next_order = (db.query(func.max(MonthlyElement.sort_order))
                  .filter(MonthlyElement.code == code, MonthlyElement.year == year_i)
                  .scalar() or 0) + 1
    for r in plan["rows"]:
        if r["was"] is None:
            db.add(MonthlyElement(code=code, year=year_i, element=r["element"],
                                  sort_order=next_order, unit=r["unit"]))
            next_order += 1
            db.flush()
            record_element_source(db, code, year_i, r["element"], r["unit"], r["element"], "doc")
        elif r["unit"] is not None:
            _get_element(db, code, year_i, r["element"]).unit = r["unit"]
        if r["values"] is not None:
            for m, v in r["values"].items():
                _upsert_value(db, code, year_i, r["element"], m, v)
    db.flush()


@router.put("/tables/{code}/{year}/doc")
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

"""月次の読み出し（一覧・年・年の一覧）と業種の設定。"""
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ...db import get_session, monthly_list_version, run_sqlite_write
from ...edinet.codes import yomi_key
from ...models import EdinetFiler, Stock, MonthlyIssuer, MonthlyTable, MonthlyElement, MonthlyValue
from ...monthly import list_cache
from ...monthly.tree import (
    MONTHLY_TREES, assign_tree_rows, auto_tree_payload, build_auto_tree, monthly_display_years,
    tree_payload,
)
from ...monthly.ingest import _YOY_UNITS, monthly_element_label
from . import _common
from ._common import (
    _DEFAULT_INDUSTRY, _element_unit, _get_table, _latest_yoy_by_kind, _new_flags, _parse_code,
    _parse_industry, _parse_year, _public_get_cache, _require_any_monthly_table, _require_object,
    _require_stock, forbid_proxied_monthly_write,
)

router = APIRouter()


@router.put("/tables/{code}/industry")
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


def clear_list_tables_cache() -> None:
    list_cache.clear()


def list_tables_payload(db: Session, only_code: str | None = None) -> list[dict]:
    """月次表がある銘柄。名前は stocks を LEFT JOIN。無いときは null。only_code で 1 銘柄に絞る。

    全銘柄の結果は (版, JST の今日) でこのプロセスに置く。is_new / ingested_today は日付で変わる。
    呼び元は返したリストを書き換えない。only_code は、全銘柄の結果が既にあればそこから取る。
    """
    if only_code is None:
        return _cached_tables(db)
    cached = _cached_tables(db, store=False)
    if cached is not None:
        return [row for row in cached if row["code"] == only_code]
    return _build_tables(db, only_code)


def _cached_tables(db: Session, *, store: bool = True) -> list[dict] | None:
    version = monthly_list_version(db)
    today = _common._jst_today()
    hit = list_cache.get(version, today)
    if hit is not None:
        return hit
    if not store:
        return None
    payload = _build_tables(db, None)
    list_cache.put(version, today, payload)
    return payload


def _build_tables(db: Session, only_code: str | None) -> list[dict]:
    q = (db.query(
             MonthlyTable.code, MonthlyTable.year,
             Stock.name, Stock.market, Stock.sector33,
             EdinetFiler.filer_name_yomi, EdinetFiler.filer_name_en)
         .outerjoin(Stock, Stock.code == MonthlyTable.code)
         .outerjoin(EdinetFiler, EdinetFiler.edinet_code == Stock.edinet_code))
    if only_code is not None:
        q = q.filter(MonthlyTable.code == only_code)
    rows = q.order_by(MonthlyTable.code, MonthlyTable.year).all()
    by_code: dict[str, dict] = {}
    for code, year, name, market, sector33, yomi, name_en in rows:
        item = by_code.get(code)
        if item is None:
            item = {
                "code": code,
                "name": name,
                "years": [],
                "market": market,
                "sector33": sector33,
                "name_yomi": yomi_key(yomi) or None,
                "name_en": name_en,
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
        # 一覧の前年比は all_store / existing だけ。monthly_series_kind がそれを返すのは
        # yoy の unit だけなので、number（店舗数を含む）は読まない。
        for e in db.execute(select(
                MonthlyElement.code, MonthlyElement.year, MonthlyElement.element,
                MonthlyElement.unit, MonthlyElement.sort_order)
                .where(MonthlyElement.code.in_(codes),
                       MonthlyElement.unit.in_(tuple(_YOY_UNITS)))):
            elems_by[e.code].append(e)
        # 値は行ごとの最新月しか順位に効かないのでそれだけ引く。
        elem = MonthlyElement
        last = (select(MonthlyValue.code, MonthlyValue.year, MonthlyValue.element,
                       func.max(MonthlyValue.month).label("month"))
                .join(elem, and_(elem.code == MonthlyValue.code,
                                 elem.year == MonthlyValue.year,
                                 elem.element == MonthlyValue.element))
                .where(MonthlyValue.code.in_(codes),
                       elem.unit.in_(tuple(_YOY_UNITS)))
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
        yoy = _latest_yoy_by_kind(elems, vals)
        item["all_store_yoy"] = yoy.get("all_store")
        item["existing_store_yoy"] = yoy.get("existing")
        out.append(item)
    return out


@router.get("/tables")
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


@router.get("/tables/{code}/{year}")
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


@router.get("/tables/{code}")
def list_years(code: str, db: Session = Depends(get_session),
               _: None = Depends(_public_get_cache)):
    code = _parse_code(code)
    return list_years_payload(db, code)

"""月次データ登録 API (/api/v1/tables) の仕様テスト。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient

from app.api.monthly._common import ApiError, _apply_incoming_unit, _parse_code
from app.main import app
from app.monthly.ingest import (
    MonthlyIngestError,
    canonicalize_element_label,
    format_monthly_cell,
    monthly_display_label,
    monthly_row_scope_rank,
    monthly_row_segment,
    monthly_row_slot,
    parse_monthly_unit,
    union_monthly_elements,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_apply_incoming_unit_conflict_and_force() -> None:
    from app.models import MonthlyElement

    row = MonthlyElement(code="72030", year=2026, element="売上高", sort_order=1, unit="number")
    _apply_incoming_unit(row, "number", False)
    if row.unit != "number":
        _fail(f"same unit must no-op: {row.unit!r}")
    try:
        _apply_incoming_unit(row, "yoy_index", False)
    except ApiError as exc:
        if exc.status != 409 or exc.payload["error"]["code"] != "UNIT_CONFLICT":
            _fail(f"conflict: {exc.status} {exc.payload!r}")
        extra = exc.payload["error"]
        if extra.get("existing") != "number" or extra.get("incoming") != "yoy_index":
            _fail(f"conflict extra: {extra!r}")
    else:
        _fail("unit mismatch without force must 409")
    _apply_incoming_unit(row, "yoy_index", True)
    if row.unit != "yoy_index":
        _fail(f"force must overwrite unit: {row.unit!r}")


def test_startup_keeps_names_and_job_renames() -> None:
    """保存名は凍結（#545）。起動時は書き換えずドリフトを数えるだけ。付け替えは jobs/monthly/monthly_names.py。"""
    from app.db import SessionLocal, init_db
    from app.monthly.maintenance import merge_yoy_duplicate_elements, monthly_name_drift, report_monthly_name_drift
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    # Isolated year so later 72030/2026 API cases keep their own rows.
    code, year = "69810", 1991
    init_db()
    _ensure_stock(code, "村田製作所")
    db = SessionLocal()
    try:
        if db.get(MonthlyTable, (code, year)) is None:
            db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(
            code=code, year=year, element="売上高前年比",
            sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyValue(
            code=code, year=year, element="売上高前年比", month=4, value=105.0,
        ))
        db.commit()
    finally:
        db.close()
    try:
        init_db()
        if (code, year, "売上高前年比", "売上高(前年比)") not in monthly_name_drift():
            _fail(f"drift must list the short name: {monthly_name_drift()!r}")
        if report_monthly_name_drift(verbose=False) < 1:
            _fail("drift report must count the short name")
        merge_yoy_duplicate_elements(verbose=False, dry_run=True)
        db = SessionLocal()
        try:
            if db.get(MonthlyElement, (code, year, "売上高前年比")) is None:
                _fail("startup / dry_run must not rename stored names")
        finally:
            db.close()
        merge_yoy_duplicate_elements(verbose=False)
        db = SessionLocal()
        try:
            if db.get(MonthlyElement, (code, year, "売上高前年比")) is not None:
                _fail("job must rename the short yoy name")
            val = db.get(MonthlyValue, (code, year, "売上高(前年比)", 4))
            if val is None or float(val.value) != 105.0:
                _fail(f"renamed value: {val!r}")
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(MonthlyValue).filter_by(code=code, year=year).delete()
            db.query(MonthlyElement).filter_by(code=code, year=year).delete()
            tbl = db.get(MonthlyTable, (code, year))
            if tbl is not None:
                db.delete(tbl)
            db.commit()
        finally:
            db.close()

def test_migrate_restores_dropped_columns() -> None:
    from sqlalchemy import text

    from app.db import engine, init_db, migrate_add_columns, missing_columns

    init_db()
    if missing_columns():
        _fail(f"fresh db must have no missing columns: {missing_columns()}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE stocks DROP COLUMN product_category"))
        conn.execute(text("ALTER TABLE monthly_elements DROP COLUMN unit"))
    got = missing_columns()
    if got != {"stocks": ["product_category"], "monthly_elements": ["unit"]}:
        _fail(f"missing_columns after drop: {got}")
    if migrate_add_columns(verbose=False) != 2 or missing_columns():
        _fail(f"migrate must add both columns back: {missing_columns()}")
    # NOT NULL 列は server_default ごと戻らないと INSERT できない
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO monthly_elements (code, year, element, sort_order) "
            "VALUES ('69810', 1990, 'migrate-probe', 1)"
        ))
        unit = conn.execute(text(
            "SELECT unit FROM monthly_elements WHERE element = 'migrate-probe'"
        )).scalar()
        conn.execute(text("DELETE FROM monthly_elements WHERE element = 'migrate-probe'"))
    if unit != "number":
        _fail(f"restored unit column default: {unit!r}")


def _assert_calendar_values(vals: dict, present: dict[str, float], label: str) -> None:
    """GET values は 1〜12。present 以外は null。"""
    keys = [str(m) for m in range(1, 13)]
    if list(vals.keys()) != keys:
        _fail(f"{label} months: {list(vals.keys())!r}")
    for k in keys:
        got = vals[k]
        if k in present:
            if got is None or float(got) != float(present[k]):
                _fail(f"{label} {k}: want {present[k]!r} got {got!r}")
        elif got is not None:
            _fail(f"{label} {k}: want null got {got!r}")


def _err(resp) -> dict:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        _fail(f"error shape: {resp.status_code} {body!r}")
    return err


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _ensure_stock(code: str, name: str | None = None) -> None:
    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        if db.get(Stock, code) is None:
            db.add(Stock(code=code, name=name))
            db.commit()
    finally:
        db.close()


def _add_fy_end(code: str, disclosed: date, fy_end: date, disclosure_no: str) -> None:
    from app.db import SessionLocal
    from app.models import Statement

    _ensure_stock(code)
    db = SessionLocal()
    try:
        db.add(Statement(
            disclosure_no=disclosure_no,
            code=code,
            disclosed_date=disclosed,
            fiscal_year_end=fy_end,
        ))
        db.commit()
    finally:
        db.close()


def _assert_jfront_tree_and_merge(c) -> None:
    """J.フロント: 表1と表2の同じ数字は 1 行に寄る。食い違いは 409。GET は木を返す。"""
    code = "30860"
    _add_fy_end(code, date(2026, 4, 10), date(2026, 2, 28), "tree-30860")
    rows = [
        {"element": "百貨店事業 売上収益(IFRS)", "unit": "yoy_pct", "values": [-0.3]},
        {"element": "百貨店事業 総額 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "大丸 心斎橋店 売上高", "unit": "yoy_pct", "values": [2.1]},
        {"element": "大丸 心斎橋店 入店 客数", "unit": "yoy_pct", "values": [-7.1]},
        {"element": "百貨店事業合計 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "百貨店事業合計 入店 客数", "unit": "yoy_pct", "values": [-4.4]},
    ]
    body = {"months": [8], "as_of": "2026-09-15", "rows": rows}
    bad = {**body, "rows": [*rows[:-2], {**rows[-2], "values": [-0.9]}]}
    r = c.post(f"/api/v1/tables/{code}/ingest", json=bad)
    _expect(r, 409, "merge conflict")
    if r.json()["error"]["code"] != "ROW_MERGE_CONFLICT":
        _fail(f"merge conflict code: {r.text}")
    r = c.post(f"/api/v1/tables/{code}/ingest", json=body)
    _expect(r, 200, "jfront ingest")
    got = r.json()
    names = [u["element"] for u in got["updated"]]
    if names.count("百貨店事業 総額 売上高(前年比)") != 1 or any("百貨店事業合計" in n for n in names):
        _fail(f"merged rows: {names!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "jfront GET")
    d = r.json()
    by = {e["element"]: e for e in d["elements"]}
    if len(by) != 5 or by["百貨店事業 総額 売上高(前年比)"]["values"]["8"] != 99.2:
        _fail(f"jfront rows: {sorted(by)!r}")
    if by["百貨店事業 入店 客数(前年比)"]["values"]["8"] != 95.6:
        _fail("百貨店事業合計 入店 客数 must land on 百貨店事業 入店 客数")
    shin = by["大丸 心斎橋店 入店 客数(前年比)"]["tree"]
    if shin != {"path": ["連結", "百貨店事業", "大丸松坂屋百貨店", "店計", "大丸", "心斎橋店"],
                "leaf": "入店 客数(前年比)", "metric": "入店客数"}:
        _fail(f"心斎橋店 tree: {shin!r}")
    if by["百貨店事業 売上収益(IFRS)(前年比)"]["tree"]["metric"] != "売上収益(IFRS)":
        _fail("IFRS は百貨店事業の子ではなく同じ節の別の指標")
    if d.get("tree_metrics") != ["総額売上高", "売上収益(IFRS)", "入店客数"]:
        _fail(f"tree_metrics: {d.get('tree_metrics')!r}")
    tree = d.get("tree") or []
    if [n["name"] for n in tree] != ["連結"]:
        _fail(f"tree roots: {tree!r}")
    dept = tree[0]["children"][0]
    if dept["name"] != "百貨店事業" or dept["elements"][0] != "百貨店事業 総額 売上高(前年比)":
        _fail(f"百貨店事業 node: {dept!r}")
    r = c.get("/api/v1/tables/31800/2026")
    d = r.json()
    if not d.get("tree_auto") or [n["name"] for n in d.get("tree") or []] != ["全体"]:
        _fail(f"issuer without config tree must get the auto tree: {d.get('tree')!r}")
    if not all(e.get("tree", {}).get("path", [None])[0] == "全体" for e in d["elements"]):
        _fail("auto tree must place every row")
    if "tree_auto" in by["百貨店事業 総額 売上高(前年比)"] or "tree_auto" in d["elements"][0]:
        _fail("tree_auto is a table key")


def _assert_ingest_dry_run_only_months(c) -> None:
    """dry_run は書かない。only_months は mapping 後の暦年だけ書く。"""
    from app.db import SessionLocal
    from app.models import MonthlyIssuer, MonthlyTable, MonthlyValue

    code = "31800"
    _add_fy_end(code, date(2026, 5, 8), date(2026, 3, 31), "dry-31800")
    months = [4, 5, 6, 7, 8]
    values = {str(m): float(m) for m in months}
    body = {
        "months": months,
        "rows": [{"element": "売上高", "unit": "number", "values": values}],
        "as_of": "2026-08-05",
    }
    r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "dry_run": True})
    _expect(r, 200, "dry_run")
    got = r.json()
    if got.get("dry_run") is not True or got.get("fy_end_month") != 3 or got.get("layout") != "fiscal":
        _fail(f"dry_run header: {got!r}")
    if got.get("mapping") != {str(m): 2026 for m in months}:
        _fail(f"dry_run mapping: {got.get('mapping')!r}")
    if got.get("updated") != [{"year": 2026, "element": "売上高", "months": months}]:
        _fail(f"dry_run updated: {got.get('updated')!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 404, "dry_run writes nothing")
    db = SessionLocal()
    try:
        if db.get(MonthlyIssuer, code) is not None or db.get(MonthlyTable, (code, 2026)) is not None:
            _fail("dry_run created monthly rows")
    finally:
        db.close()

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        **body, "only_months": ["2026-07", "2026-08"],
    })
    _expect(r, 200, "only_months")
    got = r.json()
    if "dry_run" in got:
        _fail(f"write must not set dry_run: {got!r}")
    if got.get("updated") != [{"year": 2026, "element": "売上高", "months": [7, 8]}]:
        _fail(f"only_months updated: {got.get('updated')!r}")
    if got.get("mapping") != {str(m): 2026 for m in months}:
        _fail("only_months must keep the full mapping")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET after only_months")
    vals = r.json()["elements"][0]["values"]
    _assert_calendar_values(vals, {"7": 7.0, "8": 8.0}, "only_months")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        **body,
        "dry_run": True,
        "only_months": ["2026-04"],
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 99.0, "7": 70.0}}],
    })
    _expect(r, 200, "dry_run only_months")
    if r.json().get("updated") != [{"year": 2026, "element": "売上高", "months": [4]}]:
        _fail(f"dry_run only_months updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    vals = r.json()["elements"][0]["values"]
    if float(vals["7"]) != 7.0 or vals["4"] is not None:
        _fail(f"dry_run changed cells: {vals!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "only_months": ["2025-07"]})
    _expect(r, 200, "only_months miss")
    if r.json().get("updated") != []:
        _fail(f"miss updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 404, "miss year")

    for bad, label in (
        ({"dry_run": "yes"}, "dry_run type"),
        ({"dry_run": 1}, "dry_run int"),
        ({"only_months": []}, "empty only_months"),
        ({"only_months": "2026-07"}, "only_months string"),
        ({"only_months": ["2026-13"]}, "only_months month"),
        ({"only_months": ["2026-7"]}, "only_months pad"),
    ):
        r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, **bad})
        _expect(r, 400, label)
        if _err(r)["code"] != "INVALID_PARAM":
            _fail(f"{label}: {r.json()!r}")

    wrap = "31820"
    _add_fy_end(wrap, date(2026, 5, 1), date(2026, 2, 28), "dry-31820")
    r = c.post(f"/api/v1/tables/{wrap}/ingest", json={
        "months": [11, 12, 1, 2],
        "rows": [{"element": "売上高", "unit": "number", "values": {"11": 11, "12": 12, "1": 1, "2": 2}}],
        "as_of": "2026-08-31",
        "only_months": ["2026-01"],
    })
    _expect(r, 200, "year wrap only_months")
    if r.json().get("layout") != "partial":
        _fail(f"wrap layout: {r.json()!r}")
    if r.json().get("updated") != [{"year": 2026, "element": "売上高", "months": [1]}]:
        _fail(f"wrap updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{wrap}/2025")
    _expect(r, 404, "wrap must not write 2025")
    r = c.get(f"/api/v1/tables/{wrap}/2026")
    _expect(r, 200, "wrap 2026")
    _assert_calendar_values(r.json()["elements"][0]["values"], {"1": 1.0}, "wrap january")

    dup = "31830"
    _add_fy_end(dup, date(2026, 5, 1), date(2026, 2, 28), "dry-31830")
    dup_months = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    r = c.post(f"/api/v1/tables/{dup}/ingest", json={
        "months": dup_months,
        "rows": [{"element": "売上高", "unit": "number", "values": [float(i) for i in range(13)]}],
        "as_of": "2026-08-31",
        "only_months": ["2026-03", "2026-01"],
    })
    _expect(r, 200, "duplicate months only_months")
    updated = r.json().get("updated")
    if updated != [{"year": 2026, "element": "売上高", "months": [1, 3]}]:
        _fail(f"dup updated: {updated!r}")
    if not isinstance(r.json().get("mapping"), list) or len(r.json()["mapping"]) != 13:
        _fail(f"dup mapping: {r.json().get('mapping')!r}")
    r = c.get(f"/api/v1/tables/{dup}/2026")
    _expect(r, 200, "dup 2026")
    _assert_calendar_values(r.json()["elements"][0]["values"], {"1": 10.0, "3": 12.0}, "dup kept")
    r = c.get(f"/api/v1/tables/{dup}/2025")
    _expect(r, 404, "dup must not write the earlier March")
    db = SessionLocal()
    try:
        early = db.get(MonthlyValue, (dup, 2025, "売上高", 3))
        if early is not None:
            _fail(f"earlier March written: {early.value}")
    finally:
        db.close()


def test_tables_api() -> None:
    if format_monthly_cell(None, "yoy_index") != "-":
        _fail("empty cell")
    if format_monthly_cell(105.2, "yoy_index") != "105.2":
        _fail(f"yoy_index 105.2: {format_monthly_cell(105.2, 'yoy_index')!r}")
    if format_monthly_cell(125.8, "yoy_index") != "125.8":
        _fail(f"yoy_index 125.8: {format_monthly_cell(125.8, 'yoy_index')!r}")
    if format_monthly_cell(94.8, "yoy_index") != "94.8":
        _fail(f"yoy_index 94.8: {format_monthly_cell(94.8, 'yoy_index')!r}")
    if format_monthly_cell(92.9, "yoy_index") != "92.9":
        _fail(f"yoy_index 92.9: {format_monthly_cell(92.9, 'yoy_index')!r}")
    if format_monthly_cell(303, "number") != "303":
        _fail(f"number 303: {format_monthly_cell(303, 'number')!r}")
    if format_monthly_cell(None, "number") != "-":
        _fail("empty number")
    if format_monthly_cell(0, "number") != "0":
        _fail(f"zero number: {format_monthly_cell(0, 'number')!r}")
    if format_monthly_cell(29296.646549, "number", "受注高") != "29297":
        _fail(f"受注高 display: {format_monthly_cell(29296.646549, 'number', '受注高')!r}")
    if format_monthly_cell(97.48, "number", "家賃ベース入居率") != "97.48":
        _fail(f"入居率 display: {format_monthly_cell(97.48, 'number', '家賃ベース入居率')!r}")
    if format_monthly_cell(97.48, "number", "家賃ベース入居率 事業用") != "97.48":
        _fail("入居率 事業用 must use the same digits")
    if format_monthly_cell(1.2640801936119992, "number", "空室在庫") != "1.26":
        _fail(f"空室在庫 display: {format_monthly_cell(1.2640801936119992, 'number', '空室在庫')!r}")
    if format_monthly_cell(30470.0, "number", "空室数 既存") != "30470":
        _fail(f"空室数 既存 display: {format_monthly_cell(30470.0, 'number', '空室数 既存')!r}")
    if format_monthly_cell(2884.77, "number", "営業人員数") != "2885":
        _fail(f"営業人員数 display: {format_monthly_cell(2884.77, 'number', '営業人員数')!r}")
    if format_monthly_cell(1326.1196840995988, "number", "1人当たり受注高") != "1326":
        _fail(f"1人当たり受注高 display: {format_monthly_cell(1326.1196840995988, 'number', '1人当たり受注高')!r}")
    if format_monthly_cell(125.8, "yoy_index", "受注高") != "125.8":
        _fail("yoy_index must ignore display units")
    if format_monthly_cell(303, "number", "店舗数") != "303":
        _fail("unknown number rows stay stored")
    if monthly_display_label("受注高", "number") != "受注高（百万円）":
        _fail(f"受注高 label: {monthly_display_label('受注高', 'number')!r}")
    if monthly_display_label("受注高（百万円）", "number") != "受注高（百万円）":
        _fail("display label must be idempotent")
    if monthly_display_label("空室数 （戸） 既存（戸）", "number") != "空室数 既存（戸）":
        _fail("display label must strip a duplicated unit before appending")
    if monthly_display_label("家賃ベース入居率 事業用", "number") != "家賃ベース入居率 事業用（%）":
        _fail("入居率 label")
    if monthly_display_label("空室数 既存", "number") != "空室数 既存（戸）":
        _fail("空室数 既存 label")
    if monthly_display_label("1人当たり受注高", "number") != "1人当たり受注高（万円）":
        _fail("1人当たり受注高 must not take 受注高 百万円")
    if monthly_display_label("全店 売上高(前年比)", "yoy_index") != "全店 売上高(前年比)":
        _fail("yoy label must stay stored")
    if monthly_display_label("店舗数", "number") != "店舗数":
        _fail("unknown number labels stay stored")
    try:
        parse_monthly_unit("percent")
    except MonthlyIngestError as exc:
        if exc.status != 400 or exc.code != "INVALID_PARAM":
            _fail(f"invalid unit: {exc.status} {exc.code}")
    else:
        _fail("invalid unit should 400")
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "売上高(前年比)":
        _fail("canonicalize strip+suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("canonicalize no double")
    if canonicalize_element_label("売上高前年同月比", "number") != "売上高":
        _fail("canonicalize number tokens")

    c = TestClient(app)
    code, year = "72030", "2024"
    base = f"/api/v1/tables/{code}/{year}"

    r = c.get(base)
    _expect(r, 404, "GET missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"GET missing table code: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables empty")
    if r.json() != []:
        _fail(f"empty tables: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years empty")
    if r.json() != {"code": code, "years": []}:
        _fail(f"empty years: {r.json()!r}")

    r = c.post("/api/v1/tables/72/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad code short")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad code: {r.json()!r}")

    r = c.post("/api/v1/tables/720300/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad code long")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad code long: {r.json()!r}")

    r = c.post("/api/v1/tables/7203/24/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad year")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad year: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "", "unit": "number"})
    _expect(r, 400, "empty element")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"empty element: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "あ" * 51, "unit": "number"})
    _expect(r, 400, "element too long")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"long element: {r.json()!r}")

    r = c.post("/api/v1/tables/30320/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 404, "POST without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"POST without master: {r.json()!r}")

    _ensure_stock(code)

    r = c.post(f"{base}/elements", json={"element": " 売上高 ", "unit": "number"})
    _expect(r, 201, "create element")
    if r.json() != {"code": code, "year": 2024, "element": "売上高",
                    "unit": "number", "created": True}:
        _fail(f"create: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET after auto-create")
    if r.json()["code"] != code or r.json()["year"] != 2024:
        _fail(f"get table: {r.json()!r}")
    if r.json()["elements"]:
        _fail(f"empty year row must be omitted: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years after create")
    if r.json() != {"code": code, "years": [2024]}:
        _fail(f"years: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables one code")
    body = r.json()
    if not isinstance(body, list) or len(body) != 1:
        _fail(f"tables one code: {body!r}")
    one = body[0]
    if one.get("code") != code or one.get("name") is not None or one.get("years") != [2024]:
        _fail(f"tables one code core: {one!r}")
    if one.get("industry") != "その他":
        _fail(f"default industry: {one!r}")
    if one.get("last_ingested_at") is not None:
        _fail(f"no ingest yet: {one!r}")
    if one.get("is_new") is not False or one.get("ingested_today") is not False:
        _fail(f"new flags before ingest: {one!r}")
    if "all_store_yoy" not in one or "existing_store_yoy" not in one:
        _fail(f"missing yoy fields: {one!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 200, "idempotent exact")
    if r.json() != {"code": code, "year": 2024, "element": "売上高",
                    "unit": "number", "created": False}:
        _fail(f"idempotent: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": " 売上高 ", "unit": "number"})
    _expect(r, 200, "idempotent trimmed")
    if r.json()["created"] is not False:
        _fail(f"idempotent trimmed: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "number drops 前年比 token")
    if r.json()["element"] != "売上高" or r.json()["created"] is not False:
        _fail(f"売上高前年比 number folds: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "世界観ビジネス", "unit": "number"})
    _expect(r, 201, "segment create")
    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス", "unit": "number"})
    _expect(r, 409, "similar")
    err = _err(r)
    if err["code"] != "ELEMENT_SIMILAR_EXISTS" or err["message"] != SIMILAR_MSG:
        _fail(f"similar err: {r.json()!r}")
    if err.get("similar") != ["世界観ビジネス"]:
        _fail(f"similar list: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス", "force": True, "unit": "number"})
    _expect(r, 201, "force create")
    if r.json() != {"code": code, "year": 2024, "element": "世界観 ビジネス",
                    "unit": "number", "created": True}:
        _fail(f"force: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "営業利益", "unit": "number"})
    _expect(r, 201, "third element")

    r = c.put("/api/v1/tables/7203/2025/values",
              json={"element": "営業利益", "month": 4, "value": 1})
    _expect(r, 404, "PUT missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"PUT missing table: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "未登録要素", "month": 4, "value": 1.0})
    _expect(r, 404, "unregistered element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"ELEMENT_NOT_FOUND: {r.json()!r}")
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if "未登録要素" in names:
        _fail("values PUT must not auto-register element")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "105.2%"})
    _expect(r, 400, "percent string")
    err = _err(r)
    if err["code"] != "INVALID_VALUE" or err["message"] != INVALID_VALUE_MSG:
        _fail(f"105.2%: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "1,234百万円"})
    _expect(r, 400, "unit string")
    if _err(r)["code"] != "INVALID_VALUE" or _err(r)["message"] != INVALID_VALUE_MSG:
        _fail(f"1,234百万円: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 0, "value": 1})
    _expect(r, 400, "month 0")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 0: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 13, "value": 1})
    _expect(r, 400, "month 13")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 13: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": "4", "value": 1})
    _expect(r, 400, "month string")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month string: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 105.2})
    _expect(r, 200, "put value")
    if r.json() != {"code": code, "year": 2024, "element": "売上高", "updated": [4]}:
        _fail(f"put: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 6, "value": 110})
    _expect(r, 200, "put month 6")

    r = c.get(base)
    _expect(r, 200, "GET values")
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["売上高"], {"4": 105.2, "6": 110}, "omit empty")
    if "世界観ビジネス" in by_name:
        _fail(f"empty element must be omitted: {by_name!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 99.9})
    _expect(r, 200, "overwrite")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if float(by_name["売上高"]["4"]) != 99.9:
        _fail(f"overwrite: {by_name['売上高']!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": None})
    _expect(r, 200, "delete cell")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["売上高"], {"6": 110}, "null delete")
    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": 10}, {"month": 2, "value": "105.2%"}],
    })
    _expect(r, 400, "batch invalid")
    if _err(r)["code"] != "INVALID_VALUE":
        _fail(f"batch invalid: {r.json()!r}")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if "営業利益" in by_name:
        _fail(f"batch leaked: {by_name.get('営業利益')!r}")

    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": 105.2}, {"month": 2, "value": 98.7}],
    })
    _expect(r, 200, "batch ok")
    if r.json()["updated"] != [1, 2]:
        _fail(f"batch updated: {r.json()!r}")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    ov = by_name["営業利益"]
    if float(ov["1"]) != 105.2 or float(ov["2"]) != 98.7:
        _fail(f"batch values: {ov!r}")

    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": None}, {"month": 3, "value": 1.5}],
    })
    _expect(r, 200, "batch mixed")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(
        by_name["営業利益"], {"2": 98.7, "3": 1.5}, "batch null")
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高", "営業利益"]:
        _fail(f"registration order: {names!r}")

    r = c.post("/api/v1/tables/7203/2025/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 201, "other year")
    r = c.get(f"/api/v1/tables/{code}")
    if r.json()["years"] != [2024, 2025]:
        _fail(f"years multi: {r.json()!r}")

    r = c.post("/api/v1/tables/72030/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "5-digit same issuer")
    r = c.get("/api/v1/tables/7203")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"4-digit years: {r.json()!r}")
    r = c.get("/api/v1/tables/72030")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"5-digit years: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables list")
    body = r.json()
    if not isinstance(body, list):
        _fail(f"tables should be a list: {body!r}")
    if [x.get("code") for x in body] != ["72030"]:
        _fail(f"tables sort by code: {body!r}")
    t72030 = body[0]
    if t72030["years"] != [2024, 2025]:
        _fail(f"years sorted: {t72030!r}")
    if t72030.get("name") not in (None, ""):
        _fail(f"missing stock name should be null/empty: {t72030!r}")

    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        row = db.get(Stock, "72030")
        if row is None:
            db.add(Stock(code="72030", name="トヨタ自動車"))
        else:
            row.name = "トヨタ自動車"
        db.commit()
    finally:
        db.close()

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables with stock name")
    by = {x["code"]: x for x in r.json()}
    if set(by) != {"72030"}:
        _fail(f"unique codes after stock: {list(by)!r}")
    if by["72030"]["name"] != "トヨタ自動車":
        _fail(f"name from stocks: {by['72030']!r}")
    if by["72030"]["years"] != [2024, 2025]:
        _fail(f"years after stock join: {by!r}")

    # JPX alphanumeric (Intermestic 262A / canonical 262A0)
    if _parse_code("262A") != "262A0":
        _fail("parse 262A")
    if _parse_code("262A0") != "262A0":
        _fail("parse 262A0")
    if _parse_code("262a") != "262A0":
        _fail("parse 262a")
    if _parse_code("7203") != "72030":
        _fail("parse 7203")
    if _parse_code("72030") != "72030":
        _fail("72030")
    if _parse_code("9279") != "92790":
        _fail("parse 9279")
    if _parse_code("92790") != "92790":
        _fail("92790")
    if _parse_code("7649") != "76490":
        _fail("parse 7649")
    for bad in ("abc", "12", "262A/../x", "262A.0", "7203/../etc", "A2620"):
        try:
            _parse_code(bad)
        except ApiError as exc:
            if exc.status != 400 or exc.payload["error"]["code"] != "INVALID_PARAM":
                _fail(f"bad code {bad!r}: {exc.payload!r}")
        else:
            _fail(f"should reject {bad!r}")

    _ensure_stock("262A0")
    r = c.post("/api/v1/tables/262A/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 201, "262A create")
    if r.json()["code"] != "262A0":
        _fail(f"262A should canonicalize: {r.json()!r}")
    if r.json()["element"] != "売上高":
        _fail(f"262A token name: {r.json()!r}")
    r = c.get("/api/v1/tables/262A0/2024")
    _expect(r, 200, "GET 262A0 empty")
    if r.json()["code"] != "262A0" or r.json()["elements"]:
        _fail(f"GET 262A0 empty: {r.json()!r}")
    r = c.put("/api/v1/tables/262A0/2024/values",
              json={"element": "売上高", "month": 1, "value": 1.0})
    _expect(r, 200, "262A0 seed value")
    r = c.get("/api/v1/tables/262A0/2024")
    _expect(r, 200, "GET 262A0")
    if r.json()["code"] != "262A0" or [e["element"] for e in r.json()["elements"]] != ["売上高"]:
        _fail(f"GET 262A0: {r.json()!r}")
    r = c.get("/api/v1/tables/262A/2024")
    _expect(r, 200, "GET 262A display")
    if r.json()["code"] != "262A0":
        _fail(f"GET 262A display: {r.json()!r}")
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "262A0 idempotent")
    if r.json() != {"code": "262A0", "year": 2024, "element": "売上高",
                    "unit": "number", "created": False}:
        _fail(f"262A0 idempotent: {r.json()!r}")

    r = c.post("/api/v1/tables/abc/2024/elements", json={"element": "x"})
    _expect(r, 400, "abc")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"abc: {r.json()!r}")
    r = c.post("/api/v1/tables/12/2024/elements", json={"element": "x"})
    _expect(r, 400, "12")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"12: {r.json()!r}")
    r = c.post("/api/v1/tables/262A/../x/2024/elements", json={"element": "x"})
    if r.status_code == 201:
        _fail(f"traversal created: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET units default")
    for el in r.json()["elements"]:
        if el.get("unit") != "number":
            _fail(f"7203 default unit: {el!r}")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高", "unit": "yoy_index"})
    _expect(r, 201, "create yoy_index")
    if r.json()["element"] != "全店 売上高(前年比)":
        _fail(f"yoy_index saved name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index" or r.json()["created"] is not True:
        _fail(f"yoy_index create: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "全店売上高(前年比)", "month": 11, "value": 125.8})
    _expect(r, 200, "put yoy_index 125.8")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "売上変化", "unit": "yoy_pct"})
    _expect(r, 201, "create yoy_pct")
    if r.json()["element"] != "売上変化(前年比)":
        _fail(f"yoy_pct saved name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "売上変化(前年比)", "month": 11, "value": 92.9})
    _expect(r, 200, "put stored yoy_index 92.9")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "create number")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "put number 303")

    r = c.get("/api/v1/tables/7203/2026")
    _expect(r, 200, "GET 2026 units")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"store yoy_index: {by['全店 売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店 売上高(前年比)"]["values"]["11"]), by["全店 売上高(前年比)"]["unit"]) != "125.8":
        _fail("display yoy_index 125.8")
    if by["売上変化(前年比)"]["unit"] != "yoy_index" or float(by["売上変化(前年比)"]["values"]["11"]) != 92.9:
        _fail(f"PUT stored yoy_index 92.9: {by['売上変化(前年比)']!r}")
    if format_monthly_cell(float(by["売上変化(前年比)"]["values"]["11"]), by["売上変化(前年比)"]["unit"]) != "92.9":
        _fail("display yoy_index 92.9")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"store number: {by['店舗数']!r}")
    if format_monthly_cell(float(by["店舗数"]["values"]["11"]), by["店舗数"]["unit"]) != "303":
        _fail("display number 303")

    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_pct"})
    _expect(r, 200, "PUT unit only")
    if r.json() != {"code": "72030", "year": 2026, "element": "全店 売上高(前年比)", "unit": "yoy_index"}:
        _fail(f"PUT yoy_pct must store yoy_index: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"PUT unit must not convert value: {by['全店 売上高(前年比)']!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit restore")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高(前年比)"})
    _expect(r, 400, "POST omit unit")
    if _err(r)["code"] != "UNIT_REQUIRED":
        _fail(f"omit unit: {r.json()!r}")

    _ensure_stock("60590", "ウチヤマ")
    r = c.post("/api/v1/tables/60590/2026/elements", json={"element": "売上高", "unit": "yoy_index"})
    _expect(r, 201, "60590 yoy 売上高")
    if r.json()["element"] != "売上高(前年比)" or r.json()["created"] is not True:
        _fail(f"60590 yoy short saved: {r.json()!r}")
    r = c.post("/api/v1/tables/60590/2026/elements",
               json={"element": "売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "60590 yoy canonical same")
    if r.json()["created"] is not False or r.json()["element"] != "売上高(前年比)":
        _fail(f"60590 yoy canonical: {r.json()!r}")
    r = c.get("/api/v1/tables/60590/2026")
    if r.json()["elements"]:
        _fail(f"60590 empty rows omitted: {r.json()!r}")

    r = c.post("/api/v1/tables/60590/2026/elements", json={"element": "カラオケ全店 売上高", "unit": "number"})
    _expect(r, 201, "60590 karaoke number")
    r = c.put("/api/v1/tables/60590/2026/elements",
              json={"element": "カラオケ全店 売上高", "unit": "yoy_index"})
    _expect(r, 200, "60590 karaoke PUT unit")
    r = c.post("/api/v1/tables/60590/2026/elements",
               json={"element": "カラオケ全店 売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "60590 karaoke rename")
    if r.json()["created"] is not False or r.json()["element"] != "カラオケ 全店 売上高(前年比)":
        _fail(f"60590 karaoke POST rename: {r.json()!r}")
    r = c.get("/api/v1/tables/60590/2026")
    names = [e["element"] for e in r.json()["elements"]]
    if names:
        _fail(f"60590 after karaoke rename empty: {names!r}")

    r = c.post("/api/v1/tables/7203/2026/elements",
               json={"element": "大きい指数", "unit": "yoy_pct"})
    _expect(r, 201, "yoy_pct with 125.8")
    if r.json()["element"] != "大きい指数(前年比)":
        _fail(f"yoy_pct 大きい指数 name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "大きい指数(前年比)", "month": 1, "value": 125.8})
    _expect(r, 200, "put 125.8 as stored index")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["大きい指数(前年比)"]["unit"] != "yoy_index" or float(by["大きい指数(前年比)"]["values"]["1"]) != 125.8:
        _fail(f"PUT must not convert 125.8: {by['大きい指数(前年比)']!r}")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "x", "unit": "percent"})
    _expect(r, 400, "invalid unit POST")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"invalid unit POST: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "店舗数", "unit": "percent"})
    _expect(r, 400, "invalid unit PUT")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"invalid unit PUT: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 400, "PUT missing unit")
    r = c.put("/api/v1/tables/7203/2099/elements",
              json={"element": "店舗数", "unit": "number"})
    _expect(r, 404, "PUT unit missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"PUT missing table: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "未登録", "unit": "number"})
    _expect(r, 404, "PUT unit missing element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"PUT missing element: {r.json()!r}")

    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 409, "DELETE with values")
    if _err(r)["code"] != "ELEMENT_HAS_VALUES":
        _fail(f"DELETE with values: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values/batch", json={
        "element": "店舗数",
        "values": [{"month": 11, "value": None}],
    })
    _expect(r, 200, "clear 店舗数 before DELETE")
    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 200, "DELETE empty element")
    if r.json() != {"code": "72030", "year": 2026, "element": "店舗数", "deleted": True}:
        _fail(f"DELETE body: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    names = [e["element"] for e in r.json()["elements"]]
    if "店舗数" in names:
        _fail(f"DELETE leftover: {names!r}")
    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 404, "DELETE missing element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"DELETE missing: {r.json()!r}")
    r = c.request("DELETE", "/api/v1/tables/7203/2099/elements", json={"element": "店舗数"})
    _expect(r, 404, "DELETE missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"DELETE missing table: {r.json()!r}")

    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(
        by_name["営業利益"], {"2": 98.7, "3": 1.5}, "7203 numeric")
    _assert_monthly_row_order(c)
    _assert_known_prod_units(c)
    _assert_ingest_dry_run_only_months(c)
    _assert_jfront_tree_and_merge(c)

    print("ok: monthly tables api")


def _post_monthly_elements(c, code: str, year: int, rows: list[tuple[str, str]]) -> None:
    for name, unit in rows:
        r = c.post(f"/api/v1/tables/{code}/{year}/elements",
                   json={"element": name, "unit": unit})
        if r.status_code not in (200, 201):
            _fail(f"POST {code}/{year} {name!r}: {r.status_code} {r.text[:200]}")
        stored = r.json()["element"]
        r = c.put(f"/api/v1/tables/{code}/{year}/values",
                  json={"element": stored, "month": 1, "value": 1.0})
        if r.status_code != 200:
            _fail(f"PUT {code}/{year} {stored!r}: {r.status_code} {r.text[:200]}")


_MONTHLY_YEAR_COLORS = ("#0077b6", "#2e8b57", "#b8860b", "#8e4ec6")


def _monthly_display_years(years: list[int]) -> list[int]:
    ys = sorted({int(y) for y in years}, reverse=True)
    if not ys:
        return []
    latest = ys[0]
    return [y for y in ys if y >= latest - 2]


def _monthly_year_color(year: int) -> str:
    n = len(_MONTHLY_YEAR_COLORS)
    return _MONTHLY_YEAR_COLORS[int(year) % n]


def _assert_monthly_row_order(c) -> None:
    """全店→全社→既存店→FC店→セグメント。各塊は 売上高→客数→客単価→出店→退店→店舗数。全社は別スコープ。"""
    if monthly_row_segment("カラオケ全店 売上高(前年比)") != "カラオケ":
        _fail("segment karaoke all-store")
    if monthly_row_segment("飲食既存店 店舗数") != "飲食":
        _fail("segment food existing stores")
    if monthly_row_segment("全店売上高(前年比)") != "":
        _fail("segment empty prefix")
    if monthly_row_segment("全社 売上高(前年比)") != "":
        _fail("全社 is company-wide, not a brand")
    if monthly_row_slot("カラオケ全店 売上高(前年比)", "yoy_index") != 1:
        _fail("slot sales")
    if monthly_row_slot("カラオケ既存店 売上高(前年比)", "yoy_index") != 1:
        _fail("existing sales is still 売上高")
    if monthly_row_slot("カラオケ全店 来客数(前年比)", "yoy_index") != 2:
        _fail("slot customers")
    if monthly_row_slot("カラオケ全店 客単価(前年比)", "yoy_index") != 3:
        _fail("slot unit price")
    if monthly_row_slot("平均単価", "number") != 3:
        _fail("slot 単価")
    if monthly_row_slot("カラオケ全店 店舗数", "number") != 7:
        _fail("slot stores")
    if monthly_row_slot("カラオケ既存店 店舗数", "number") != 7:
        _fail("stores must not be 売上")
    if monthly_row_slot("出店", "number") != 4:
        _fail("slot 出店")
    if monthly_row_slot("退店", "number") != 5:
        _fail("slot 退店")
    if monthly_row_slot("100円ショップ 閉鎖", "number") != 5:
        _fail("閉鎖 folds to 退店 slot")
    if monthly_row_slot("純増 店舗数", "number") != 6:
        _fail("slot 純増 before 店舗数")
    if monthly_row_slot("新設店舗", "number") != 6:
        _fail("slot 新設")
    if monthly_row_slot("改装", "number") != 6:
        _fail("slot 改装")
    if monthly_row_slot("業態転換", "number") != 6:
        _fail("slot 業態転換")
    if monthly_row_slot("全店売上高(前年比)", "yoy_index") != 1:
        _fail("slot MONTHLY_SERIES 全店売上高")
    if monthly_row_slot("全社 売上高(前年比)", "yoy_index") != 1:
        _fail("全社 売上高 is sales")
    if monthly_row_scope_rank("全店 売上高(前年比)") != 0:
        _fail("全店 is scope 0")
    if monthly_row_scope_rank("全社 売上高(前年比)") != 1:
        _fail("全社 is scope 1")
    if monthly_row_scope_rank("既存店 売上高(前年比)") != 2:
        _fail("既存店 is scope 2")
    if monthly_row_scope_rank("売上高") != 0:
        _fail("unlabeled 売上高 stays 全店")
    if monthly_row_scope_rank("全店 店舗数") != 0:
        _fail("全店 店舗数 stays in 全店")
    if monthly_row_scope_rank("既存店 店舗数") != 2:
        _fail("既存店 店舗数 stays in 既存店")
    if monthly_row_scope_rank("合計 店舗数") != 3:
        _fail("合計 店舗数 must not fold into 全店")
    if monthly_row_scope_rank("店舗数") != 3:
        _fail("bare 店舗数 must not fold into 全店")
    if monthly_row_scope_rank("当期計店舗数") != 3:
        _fail("当期計店舗数 must not fold into 全店")

    code = "60590"
    _ensure_stock(code, "ウチヤマ")
    _post_monthly_elements(c, code, 2025, [
        ("カラオケ全店 店舗数", "number"),
        ("カラオケ既存店 店舗数", "number"),
        ("カラオケ全店 売上高", "yoy_index"),
        ("カラオケ既存店 売上高", "yoy_index"),
        ("カラオケ全店 客単価", "yoy_index"),
        ("飲食全店 売上高", "yoy_index"),
        ("飲食既存店 売上高", "yoy_index"),
        ("飲食全店 店舗数", "number"),
        ("飲食既存店 店舗数", "number"),
        ("飲食全店 客単価", "yoy_index"),
    ])
    _post_monthly_elements(c, code, 2024, [
        ("飲食全店 店舗数", "number"),
        ("飲食既存店 店舗数", "number"),
        ("飲食全店 売上高", "yoy_index"),
        ("飲食既存店 売上高", "yoy_index"),
        ("飲食全店 客単価", "yoy_index"),
        ("カラオケ全店 店舗数", "number"),
        ("カラオケ既存店 店舗数", "number"),
        ("カラオケ全店 売上高", "yoy_index"),
        ("カラオケ既存店 売上高", "yoy_index"),
        ("カラオケ全店 客単価", "yoy_index"),
        ("カラオケ全店 来客数", "yoy_index"),
    ])
    r = c.put(f"/api/v1/tables/{code}/2024/values",
              json={"element": "カラオケ全店 来客数(前年比)", "month": 1, "value": 104.6})
    _expect(r, 200, "60590 2024 visitors")

    y2025 = c.get(f"/api/v1/tables/{code}/2025")
    y2024 = c.get(f"/api/v1/tables/{code}/2024")
    _expect(y2025, 200, "GET 60590 2025")
    _expect(y2024, 200, "GET 60590 2024")
    ingest_2025 = [e["element"] for e in y2025.json()["elements"]]
    ingest_2024 = [e["element"] for e in y2024.json()["elements"]]
    if ingest_2025[0] != "カラオケ 全店 店舗数":
        _fail(f"API 2025 stays registration order: {ingest_2025!r}")
    if ingest_2024[0] != "飲食 全店 店舗数":
        _fail(f"API 2024 stays registration order: {ingest_2024!r}")
    if ingest_2025 == ingest_2024:
        _fail("fixture years must differ in ingest order")

    order = [row["element"] for row in union_monthly_elements([y2025.json(), y2024.json()])]
    swapped = [row["element"] for row in union_monthly_elements([y2024.json(), y2025.json()])]
    if order != swapped:
        _fail(f"newest-first must ignore input list order: {order!r} vs {swapped!r}")

    want = [
        "カラオケ 全店 売上高(前年比)",
        "カラオケ 全店 客数(前年比)",
        "カラオケ 全店 客単価(前年比)",
        "カラオケ 全店 店舗数",
        "カラオケ 既存 売上高(前年比)",
        "カラオケ 既存 店舗数",
        "飲食 全店 売上高(前年比)",
        "飲食 全店 客単価(前年比)",
        "飲食 全店 店舗数",
        "飲食 既存 売上高(前年比)",
        "飲食 既存 店舗数",
    ]
    if order != want:
        _fail(f"60590 union order: {order!r}")

    k_names = [n for n in order if n.startswith("カラオケ")]
    f_names = [n for n in order if n.startswith("飲食")]
    if not k_names or not f_names:
        _fail(f"missing segment block: {order!r}")
    if order.index(k_names[-1]) >= order.index(f_names[0]):
        _fail(f"karaoke block must precede food: {order!r}")
    if any(n.startswith("飲食") for n in order[:order.index(k_names[-1]) + 1]):
        _fail(f"segments interleaved: {order!r}")
    if (order.index("カラオケ 全店 売上高(前年比)")
            >= order.index("カラオケ 既存 売上高(前年比)")
            or order.index("カラオケ 既存 売上高(前年比)")
            >= order.index("カラオケ 既存 店舗数")):
        _fail(f"karaoke 全店 before 既存, 売上 before 店舗数: {order!r}")
    if (order.index("飲食 全店 売上高(前年比)")
            >= order.index("飲食 既存 売上高(前年比)")
            or order.index("飲食 既存 売上高(前年比)")
            >= order.index("飲食 既存 店舗数")):
        _fail(f"food 全店 before 既存, 売上 before 店舗数: {order!r}")

    present_2025 = {e["element"] for e in y2025.json()["elements"]}
    if "カラオケ 全店 客数(前年比)" in present_2025:
        _fail("2025 must omit 来客数 so the union row is empty")
    if "カラオケ 全店 客数(前年比)" not in order:
        _fail("missing-year row must stay in the union")

    default = next(
        (row["element"] for row in union_monthly_elements([y2025.json(), y2024.json()])
         if monthly_row_slot(row["element"], row["unit"]) == 1
         and "全店" in row["element"] and "既存" not in row["element"]),
        "",
    )
    if default != "カラオケ 全店 売上高(前年比)":
        _fail(f"default 全店売上: {default!r}")

    mixed = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "焼肉 既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "FC 月末店舗数", "unit": "number"},
            {"element": "全社 売上高(前年比)", "unit": "yoy_index"},
            {"element": "既存直営店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "全社 客数(前年比)", "unit": "yoy_index"},
        ],
    }])
    mixed_names = [row["element"] for row in mixed]
    want_mixed = [
        "全社 売上高(前年比)",
        "全社 客数(前年比)",
        "直営 既存 客単価(前年比)",
        "FC 店舗数",
        "焼肉 全店 売上高(前年比)",
        "焼肉 既存 売上高(前年比)",
    ]
    if mixed_names != want_mixed:
        _fail(f"company-wide before segment, 全社 stays 全社: {mixed_names!r}")

    aoki = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "全店 客数(前年比)", "unit": "yoy_index"},
            {"element": "既存店 客数(前年比)", "unit": "yoy_index"},
            {"element": "全店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "既存店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "合計 店舗数", "unit": "number"},
        ],
    }])
    aoki_names = [row["element"] for row in aoki]
    want_aoki = [
        "全店 売上高(前年比)",
        "全店 客数(前年比)",
        "全店 客単価(前年比)",
        "既存 売上高(前年比)",
        "既存 客数(前年比)",
        "既存 客単価(前年比)",
        "店舗数",
    ]
    if aoki_names != want_aoki:
        _fail(f"合計店舗数 must follow 既存店, not sit in 全店: {aoki_names!r}")
    if aoki_names.index("店舗数") < aoki_names.index("既存 客単価(前年比)"):
        _fail(f"店舗数 in the middle: {aoki_names!r}")

    monogatari = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "焼肉 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 直営店舗数", "unit": "number"},
            {"element": "焼肉 既存店 店舗数", "unit": "number"},
        ],
    }])
    monogatari_names = [row["element"] for row in monogatari]
    want_monogatari = [
        "焼肉 全店 売上高(前年比)",
        "焼肉 既存 売上高(前年比)",
        "焼肉 既存 店舗数",
        "焼肉 直営 店舗数",
    ]
    if monogatari_names != want_monogatari:
        _fail(f"直営 is channel, not 全店: {monogatari_names!r}")

    watts = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "100円ショップ 出店 店舗数", "unit": "number"},
            {"element": "100円ショップ 閉鎖 店舗数", "unit": "number"},
            {"element": "100円ショップ 店舗数", "unit": "number"},
            {"element": "100円ショップ 直営 既存(前年比)", "unit": "yoy_index"},
            {"element": "100円ショップ 直営 全店(前年比)", "unit": "yoy_index"},
        ],
    }])
    watts_names = [row["element"] for row in watts]
    want_watts = [
        "全店 売上高(前年比)",
        "100円ショップ 直営 全店(前年比)",
        "100円ショップ 直営 既存(前年比)",
        "100円ショップ 出店",
        "100円ショップ 退店",
        "100円ショップ 店舗数",
    ]
    if watts_names != want_watts:
        _fail(f"出店/退店 must precede 店舗数, not split as segments: {watts_names!r}")

    adj = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "店舗数", "unit": "number"},
            {"element": "純増 店舗数", "unit": "number"},
            {"element": "出店", "unit": "number"},
            {"element": "退店", "unit": "number"},
            {"element": "改装", "unit": "number"},
            {"element": "新設店舗", "unit": "number"},
            {"element": "業態転換", "unit": "number"},
            {"element": "M&A", "unit": "number"},
        ],
    }])
    adj_names = [row["element"] for row in adj]
    want_adj = [
        "出店", "退店", "純増 店舗数", "改装", "新設店舗", "業態転換", "店舗数", "M&A",
    ]
    if monthly_row_segment("純増 店舗数") != "":
        _fail("純増 is not a segment")
    if monthly_row_segment("ジンズ 純増 店舗数") != "ジンズ":
        _fail("純増 must not steal the brand")
    if monthly_row_segment("トライアル 新規 全店 出店") != "トライアル":
        _fail("新規 must not steal the brand")
    if monthly_row_slot("トライアル 出店", "number") != 4:
        _fail("出店 slot")
    if monthly_row_scope_rank("トライアル 全店 出店") != 0:
        _fail("全店 出店 stays 全店")
    if monthly_row_scope_rank("トライアル 出店") != 3:
        _fail("bare 出店 stays after 既存")
    if adj_names != want_adj:
        _fail(f"純増/新設/改装/業態転換 before 店舗数, names unchanged: {adj_names!r}")

    trial = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "トライアル 既存 売上高(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 既存 客数(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 既存 客単価(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 売上高(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 客数(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 客単価(前年比)", "unit": "yoy_index"},
            {"element": "西友 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 新規 全店 出店", "unit": "number"},
            {"element": "トライアル 全店 退店", "unit": "number"},
            {"element": "トライアル 全店 店舗数", "unit": "number"},
            {"element": "西友 新規 全店 出店", "unit": "number"},
            {"element": "西友 全店 退店", "unit": "number"},
            {"element": "西友 全店 店舗数", "unit": "number"},
            {"element": "西友 業態転換数 全店", "unit": "number"},
            {"element": "西友 業態転換数", "unit": "number"},
        ],
    }])
    trial_names = [row["element"] for row in trial]
    want_trial = [
        "トライアル 全店 売上高(前年比)",
        "トライアル 全店 出店",
        "トライアル 全店 退店",
        "トライアル 全店 店舗数",
        "トライアル 既存 売上高(前年比)",
        "トライアル 既存 客数(前年比)",
        "トライアル 既存 客単価(前年比)",
        "西友 全店 売上高(前年比)",
        "西友 全店 出店",
        "西友 全店 退店",
        "西友 全店 業態転換数",
        "西友 全店 店舗数",
        "西友 既存 売上高(前年比)",
        "西友 既存 客数(前年比)",
        "西友 既存 客単価(前年比)",
        "西友 業態転換数",
    ]
    if trial_names != want_trial:
        _fail(f"出店/退店 keep 全店: {trial_names!r}")


def _assert_known_prod_units(c) -> None:
    from app.db import SessionLocal
    from app.monthly.maintenance import apply_known_monthly_element_units
    from app.models import MonthlyElement, MonthlyValue

    _ensure_stock("92790")
    _ensure_stock("262A0")
    db = SessionLocal()
    try:
        if (not db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
                and not db.query(MonthlyElement).filter(MonthlyElement.code == "262A0").all()):
            return
    finally:
        db.close()

    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "全店売上高", "unit": "number"})
    if r.status_code not in (200, 201):
        _fail(f"seed 92790: {r.status_code} {r.text[:200]}")
    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "seed 92790 stores")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "全店売上高", "month": 11, "value": 125.8})
    _expect(r, 200, "seed gift 125.8")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "seed gift stores")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code == 200:
        r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "店舗数", "unit": "number"})
        if r.status_code not in (200, 201):
            _fail(f"seed 262A0 stores: {r.status_code} {r.text[:200]}")
        r = c.put("/api/v1/tables/262A0/2024/values",
                  json={"element": "売上高", "month": 1, "value": -7.1})
        _expect(r, 200, "seed intermestic -7.1")

    apply_known_monthly_element_units()

    db = SessionLocal()
    try:
        gift = db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
        inter = db.query(MonthlyElement).filter(MonthlyElement.code == "262A0").all()
        if not gift and not inter:
            return
        for row in gift:
            want = "number" if "店舗数" in row.element else "yoy_index"
            if row.unit != want:
                _fail(f"92790 {row.element!r} unit {row.unit!r} != {want}")
        for row in inter:
            if "店舗数" in row.element and row.unit != "number":
                _fail(f"262A0 店舗数 unit {row.unit!r}")
            if row.unit == "yoy_pct":
                _fail(f"262A0 must not force yoy_pct: {row.element!r} {row.unit!r}")
        v = db.get(MonthlyValue, ("92790", 2025, "全店 売上高", 11))
        if v is None or float(v.value) != 125.8:
            _fail(f"gift value converted: {v}")
        v = db.get(MonthlyValue, ("262A0", 2024, "売上高", 1))
        if v is not None and float(v.value) != -7.1:
            _fail(f"intermestic value converted: {v.value}")
    finally:
        db.close()

    r = c.get("/api/v1/tables/92790/2025")
    _expect(r, 200, "GET 92790 after unit assign")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高"]["unit"] != "yoy_index" or float(by["全店 売上高"]["values"]["11"]) != 125.8:
        _fail(f"92790 GET: {by['全店 売上高']!r}")
    if format_monthly_cell(125.8, by["全店 売上高"]["unit"]) != "125.8":
        _fail("92790 display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"92790 stores GET: {by['店舗数']!r}")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code != 200:
        return
    by = {e["element"]: e for e in r.json()["elements"]}
    if "売上高" in by:
        vals = by["売上高"]["values"]
        raw = vals.get("1", vals.get(1))
        if raw is not None and float(raw) != -7.1:
            _fail(f"262A0 value converted: {vals!r}")

def main() -> None:
    test_apply_incoming_unit_conflict_and_force()
    test_startup_keeps_names_and_job_renames()
    test_migrate_restores_dropped_columns()
    test_tables_api()


if __name__ == "__main__":
    main()

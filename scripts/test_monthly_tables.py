"""月次データ登録 API (/api/v1/tables) の仕様テスト。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.api import ApiError, _parse_code  # noqa: E402
from app.main import app  # noqa: E402
from app.monthly_ingest import (  # noqa: E402
    MONTHLY_DISPLAY_UNITS,
    MonthlyIngestError,
    apply_issuer_aliases,
    canonicalize_element_label,
    format_monthly_cell,
    monthly_display_label,
    monthly_element_label,
    monthly_row_scope_rank,
    monthly_row_segment,
    monthly_row_slot,
    parse_monthly_unit,
    union_monthly_elements,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _spa_source() -> str:
    """Markup + CSS + monthly JS. Guards used to read static/index.html as one file."""
    files = [
        ROOT / "static" / "index.html",
        ROOT / "static" / "css" / "app.css",
        ROOT / "static" / "js" / "monthly.js",
    ]
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    if missing:
        _fail("SPA source missing: " + ", ".join(missing))
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


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


def _select_tags(html: str) -> list[str]:
    out: list[str] = []
    low = html.lower()
    start = 0
    while True:
        i = low.find("<select", start)
        if i < 0:
            break
        j = html.find(">", i)
        if j < 0:
            break
        out.append(html[i : j + 1])
        start = j + 1
    return out


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


def main() -> None:
    # display is index 100=flat (not +X.X% / ▲). number rows round; unit goes on the label.
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
    _assert_monthly_year_window()
    _assert_monthly_year_color_stable()
    _assert_union_collapses_yoy_display_names()
    _assert_segment_tail_row_order()
    html = _spa_source()
    if "fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell")
    if "function fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell function")
    if "function monthlyDisplayUnit" not in html:
        _fail("index.html missing monthlyDisplayUnit")
    if "const MONTHLY_DISPLAY_UNITS" not in html:
        _fail("index.html missing MONTHLY_DISPLAY_UNITS")
    if "fmtMonthlyCell(v, unit, e.element)" not in html:
        _fail("year table cells must pass element into fmtMonthlyCell")
    js_fn = html.find("const MONTHLY_DISPLAY_UNITS")
    js_end = html.find("function monthlyDisplayUnit")
    if js_fn < 0 or js_end <= js_fn:
        _fail("MONTHLY_DISPLAY_UNITS / monthlyDisplayUnit bounds")
    js_rows = re.findall(r'\["([^"]+)", "([^"]+)", (\d+)\]', html[js_fn:js_end])
    py_rows = [(token, suffix, str(digits)) for token, suffix, digits in MONTHLY_DISPLAY_UNITS]
    if js_rows != py_rows:
        _fail(f"JS/Python display units drifted: {js_rows} vs {py_rows}")
    cell_fn = html.find("function fmtMonthlyCell")
    if cell_fn < 0:
        _fail("fmtMonthlyCell bounds")
    cell_end = html.find("window.setMonthlyGroup")
    if cell_end <= cell_fn:
        _fail("fmtMonthlyCell / window.setMonthlyGroup bounds")
    cell_body = html[cell_fn:cell_end]
    if 'return "-"' not in cell_body:
        _fail("fmtMonthlyCell empty must display -")
    if 'return ""' in cell_body:
        _fail("fmtMonthlyCell must not return blank for empty")
    if "+= 100" in cell_body or "n+100" in cell_body.replace(" ", ""):
        _fail("fmtMonthlyCell must not convert leftover yoy_pct")
    if "spec.suffix" in cell_body:
        _fail("fmtMonthlyCell must not append the unit to the number")
    yoy_fn = html.find("function fmtMonthlyYoyList")
    if yoy_fn < 0:
        _fail("fmtMonthlyYoyList missing")
    yoy_body = html[yoy_fn:yoy_fn + 220]
    if 'return "-"' not in yoy_body:
        _fail("fmtMonthlyYoyList empty must display -")
    if "function fmtMonthlyElement" in html:
        _fail("SPA must not re-parse row names with fmtMonthlyElement")
    if "function parseMonthlyLayers" in html:
        _fail("SPA must not keep parseMonthlyLayers")
    if "L.display" not in html:
        _fail("monthlyRowOrder must fold on label.display")
    if "▲" in html or "+X.X%" in html:
        _fail("index.html still has percent/▲ monthly display")
    _assert_monthly_spa(html)
    _assert_monthly_layout_js()
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "全店 売上高(前年比)":
        _fail("canonicalize strip+suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("canonicalize no double")
    if canonicalize_element_label("売上高前年同月比", "number") != "全店 売上高":
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
    if r.json() != {"code": code, "year": 2024, "element": "全店 売上高",
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
    if r.json() != {"code": code, "year": 2024, "element": "全店 売上高",
                    "unit": "number", "created": False}:
        _fail(f"idempotent: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": " 売上高 ", "unit": "number"})
    _expect(r, 200, "idempotent trimmed")
    if r.json()["created"] is not False:
        _fail(f"idempotent trimmed: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "number drops 前年比 token")
    if r.json()["element"] != "全店 売上高" or r.json()["created"] is not False:
        _fail(f"売上高前年比 number folds: {r.json()!r}")

    # similar-element 409 (空白差。トークン正規化後も別名)
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
    if r.json() != {"code": code, "year": 2024, "element": "全店 売上高", "updated": [4]}:
        _fail(f"put: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 6, "value": 110})
    _expect(r, 200, "put month 6")

    r = c.get(base)
    _expect(r, 200, "GET values")
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["全店 売上高"], {"4": 105.2, "6": 110}, "omit empty")
    if "世界観ビジネス" in by_name:
        _fail(f"empty element must be omitted: {by_name!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 99.9})
    _expect(r, 200, "overwrite")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if float(by_name["全店 売上高"]["4"]) != 99.9:
        _fail(f"overwrite: {by_name['全店 売上高']!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": None})
    _expect(r, 200, "delete cell")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["全店 売上高"], {"6": 110}, "null delete")
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
    if names != ["全店 売上高", "営業利益"]:
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
    if r.json()["element"] != "全店 売上高":
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
    if r.json()["code"] != "262A0" or [e["element"] for e in r.json()["elements"]] != ["全店 売上高"]:
        _fail(f"GET 262A0: {r.json()!r}")
    r = c.get("/api/v1/tables/262A/2024")
    _expect(r, 200, "GET 262A display")
    if r.json()["code"] != "262A0":
        _fail(f"GET 262A display: {r.json()!r}")
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "262A0 idempotent")
    if r.json() != {"code": "262A0", "year": 2024, "element": "全店 売上高",
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
    if r.json()["element"] != "全店 売上高(前年比)" or r.json()["created"] is not True:
        _fail(f"60590 yoy short saved: {r.json()!r}")
    r = c.post("/api/v1/tables/60590/2026/elements",
               json={"element": "売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "60590 yoy canonical same")
    if r.json()["created"] is not False or r.json()["element"] != "全店 売上高(前年比)":
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


def _assert_monthly_year_window() -> None:
    """最新暦年とその前2年。2023–2026 なら 2026/2025/2024。新しい年が先。"""
    got = _monthly_display_years([2023, 2024, 2025, 2026])
    if got != [2026, 2025, 2024]:
        _fail(f"last-3-year window 2023-2026: {got!r}")
    if 2023 in got:
        _fail("years older than latest-2 must not render")
    if _monthly_display_years([2026, 2023, 2024, 2025]) != [2026, 2025, 2024]:
        _fail("window must ignore input order")
    if _monthly_display_years([2026, 2023]) != [2026]:
        _fail("gap: only years in [latest, latest-2]")
    if _monthly_display_years([2024]) != [2024]:
        _fail("single year stays")
    if _monthly_display_years([]) != []:
        _fail("empty years")


def _assert_monthly_year_color_stable() -> None:
    """色は暦年。2025 は 2026 の有無で変わらない。"""
    if _monthly_year_color(2025) != _MONTHLY_YEAR_COLORS[2025 % 4]:
        _fail("2025 must use year % 4, not display index")
    colors_with = {y: _monthly_year_color(y) for y in _monthly_display_years([2024, 2025, 2026])}
    colors_without = {y: _monthly_year_color(y) for y in _monthly_display_years([2023, 2024, 2025])}
    if colors_with[2025] != colors_without[2025]:
        _fail(f"2025 color moved when 2026 dropped: {colors_with[2025]!r} vs {colors_without[2025]!r}")
    if colors_with[2024] != colors_without[2024]:
        _fail("2024 color must stay when the window slides")


def _assert_union_collapses_yoy_display_names() -> None:
    """年Aの短い yoy 名と年Bの正規名は表示名で1行。number は分けたまま。"""
    short = "全店 売上高前期比"
    canon = "全店 売上高(前年比)"
    leftover = [
        {"year": 2026, "elements": [
            {"element": short, "unit": "yoy_index", "values": {"1": 110.0}},
            {"element": "店舗数", "unit": "number", "values": {"1": 50.0}},
            {"element": "売上高", "unit": "number", "values": {"1": 1000.0}},
        ]},
        {"year": 2025, "elements": [
            {"element": canon, "unit": "yoy_index", "values": {"1": 105.0}},
            {"element": "店舗数", "unit": "number", "values": {"1": 48.0}},
            {"element": "売上高", "unit": "number", "values": {"1": 900.0}},
        ]},
    ]
    rows = union_monthly_elements(leftover)
    order = [row["element"] for row in rows]
    if order != [canon, "全店 売上高", "店舗数"]:
        _fail(f"cross-year yoy short/canonical must be one row: {order!r}")
    if monthly_row_slot(rows[0]["element"], rows[0]["unit"]) != 1:
        _fail("collapsed yoy 全店売上 stays slot 1")
    if monthly_row_slot("店舗数", "number") != 7:
        _fail("number 店舗数 stays slot 7")
    hit = next(
        e for e in leftover[0]["elements"]
        if canonicalize_element_label(e["element"], e["unit"]) == canon
    )
    if float(hit["values"]["1"]) != 110.0:
        _fail("2026 values come from the short stored row")
    swapped = [row["element"] for row in union_monthly_elements(list(reversed(leftover)))]
    if swapped != order:
        _fail(f"display-name union must ignore input order: {swapped!r}")


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
    # 新しい年: カラオケ先だが店舗数が売上より前
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
    # 古い年: 飲食先・店舗数先。来客数はこの年だけ
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
    if monthly_row_scope_rank("トライアル 全店 出店") != 3:
        _fail("出店 drops 全店, after 既存")
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
        "トライアル 全店 店舗数",
        "トライアル 既存 売上高(前年比)",
        "トライアル 既存 客数(前年比)",
        "トライアル 既存 客単価(前年比)",
        "トライアル 出店",
        "トライアル 退店",
        "西友 全店 売上高(前年比)",
        "西友 全店 業態転換数",
        "西友 全店 店舗数",
        "西友 既存 売上高(前年比)",
        "西友 既存 客数(前年比)",
        "西友 既存 客単価(前年比)",
        "西友 出店",
        "西友 退店",
        "西友 業態転換数",
    ]
    if trial_names != want_trial:
        _fail(f"出店/退店 are not 全店 scope: {trial_names!r}")



def _assert_segment_tail_row_order() -> None:
    """76830: 内アパレルは segment_tail で非 tail の後。他銘柄は初出順のまま。"""
    app_cfg = ROOT / "app" / "monthly_row_order.json"
    static_cfg = ROOT / "static" / "monthly_row_order.json"
    if not app_cfg.is_file() or not static_cfg.is_file():
        _fail("monthly_row_order.json missing under app/ or static/")
    if app_cfg.read_text(encoding="utf-8") != static_cfg.read_text(encoding="utf-8"):
        _fail("app/monthly_row_order.json and static/monthly_row_order.json must match")

    elements = [
        {"element": "内アパレル 全店 売上高", "unit": "number"},
        {"element": "内アパレル 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "店舗販売 全店 売上高", "unit": "number"},
        {"element": "店舗販売 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "オンライン販売 全店 売上高", "unit": "number"},
        {"element": "オンライン販売 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "その他 全店 売上高", "unit": "number"},
        {"element": "その他 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "全店 売上高", "unit": "number"},
        {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
    ]
    table = {"code": "76830", "year": 2026, "elements": elements}
    order = [row["element"] for row in union_monthly_elements([table])]
    want = [
        "全店 売上高",
        "全店 売上高(前年比)",
        "店舗販売 全店 売上高",
        "店舗販売 全店 売上高(前年比)",
        "オンライン販売 全店 売上高",
        "オンライン販売 全店 売上高(前年比)",
        "その他 全店 売上高",
        "その他 全店 売上高(前年比)",
        "内アパレル 全店 売上高",
        "内アパレル 全店 売上高(前年比)",
    ]
    if order != want:
        _fail(f"76830 segment_tail order: {order!r}")
    via_kw = [row["element"] for row in union_monthly_elements(
        [{"year": 2026, "elements": elements}], code="76830",
    )]
    if via_kw != want:
        _fail(f"76830 code= kw order: {via_kw!r}")

    other = [row["element"] for row in union_monthly_elements(
        [{"code": "99999", "year": 2026, "elements": elements}],
    )]
    other_named = [n for n in other if monthly_row_segment(n)]
    if not other_named or not other_named[0].startswith("内アパレル"):
        _fail(f"other issuer must keep first-seen 内アパレル first: {other!r}")

    # Lane B: 内 MM → 内アパレル（canonicalize 後キー）
    for src, unit, want_name in [
        ("内 MM 全店 売上高", "number", "内アパレル 全店 売上高"),
        ("内 MM 売上", "number", "内アパレル 全店 売上高"),
        ("内 MM 全店 売上高(前年比)", "yoy_index", "内アパレル 全店 売上高(前年比)"),
        ("内 MM 売上高(前年比)", "yoy_index", "内アパレル 全店 売上高(前年比)"),
    ]:
        got = apply_issuer_aliases(
            "76830", canonicalize_element_label(src, unit), year=2026,
        )
        got = canonicalize_element_label(got, unit)
        if got != want_name:
            _fail(f"76830 alias {src!r} → {got!r}, want {want_name!r}")


def _assert_monthly_spa(html: str) -> None:
    """月次 SPA: TOP はチップ+6列。Detail はグラフと暦年表。年ドロップダウン無し。"""
    start = html.find('id="tab-monthly"')
    if start < 0:
        _fail("index.html missing monthly tab")
    end = html.find("</section>", start)
    if end < 0:
        _fail("index.html monthly tab unclosed")
    section = html[start:end]
    if 'id="monthly-year"' in html or "monthly-year" in html:
        _fail("SPA still has #monthly-year")
    if "<select" not in section.lower() or 'id="monthly-series"' not in section:
        _fail("Detail series control must be <select id=\"monthly-series\">")
    for chunk in _select_tags(section):
        if "monthly-year" in chunk:
            _fail("monthly tab still has a year select")
        if 'id="monthly-series"' not in chunk and 'id="monthly-layout-mode"' not in chunk:
            _fail("monthly tab select must be #monthly-series or TEMP #monthly-layout-mode")
    if 'id="monthly-layout-debug"' not in section or 'id="monthly-layout-mode"' not in section:
        _fail("TEMP flat/gated compare control #monthly-layout-debug must be in Detail")
    if ">項目<" in html:
        _fail("monthly thead still uses 項目")
    if 'id="monthly-groups"' not in section:
        _fail("TOP must have #monthly-groups")
    if 'data-group="__gate__"' not in html and "setMonthlyGroup('__gate__')" not in html:
        _fail("TEMP gate list chip __gate__ must exist on TOP")
    if "ゲート（一時）" not in html:
        _fail("TEMP gate list chip label ゲート（一時） missing")
    if "function ensureMonthlyGateIndex" not in html:
        _fail("TEMP gate list must build ensureMonthlyGateIndex")
    if "MONTHLY_GATE_CODES_KEY" not in html:
        _fail("TEMP gate list must cache codes under MONTHLY_GATE_CODES_KEY")
    if 'id="monthly-chart"' not in section:
        _fail("Detail must have #monthly-chart")
    if 'id="monthly-back"' not in section:
        _fail("Detail must have #monthly-back")
    if "function uniqueMonthlyIssuers" not in html:
        _fail("index.html missing uniqueMonthlyIssuers")
    if "uniqueMonthlyIssuers(monthlyRows)" not in html:
        _fail("list must uniqueMonthlyIssuers(monthlyRows)")
    if "seen.has(code)" not in html:
        _fail("uniqueMonthlyIssuers must skip duplicate codes")
    if "function drawMonthlyChart" not in html:
        _fail("index.html missing drawMonthlyChart")
    if "monthly-grid" in html.replace(" ", "") and "grid-template-columns:minmax(240px,1fr)" in html.replace(" ", ""):
        _fail("monthly tab must not use 1100px two-column grid")
    list_fn = html.find("function renderMonthlyList")
    show_fn = html.find("function showMonthlyTop")
    if list_fn < 0 or show_fn < 0:
        _fail("renderMonthlyList / showMonthlyTop bounds")
    list_body = html[list_fn:show_fn] if show_fn > list_fn else html[list_fn:list_fn + 1800]
    if "r.year" in list_body or "${r.year}" in list_body:
        _fail("TOP list must not show year")
    if '<th class="l">コード</th><th class="l">銘柄</th>' in list_body:
        _fail("TOP list must not be code + name only")
    if "会社名" not in list_body or "月次業種" not in list_body:
        _fail("TOP headers must include 会社名 and 月次業種")
    if "function fmtMonthlyIssuerLabel" not in html:
        _fail("TOP name must use fmtMonthlyIssuerLabel")
    if "fmtMonthlyIssuerLabel(r.code, r.name)" not in list_body:
        _fail("TOP name cell must be code + truncated company name")
    if "function fmtMonthlyMarket" not in html:
        _fail("TOP market must use fmtMonthlyMarket")
    if "東証P" not in html or "東証S" not in html or "東証G" not in html:
        _fail("TOP market labels must be 東証P / 東証S / 東証G")
    if "mkt-p" not in html or "mkt-s" not in html or "mkt-g" not in html:
        _fail("TOP market cells must have colored mkt classes")
    if "td.mkt::before" not in html:
        _fail("TOP market cells must have a color square")
    if "function fmtMonthlySector" not in html:
        _fail("TOP sector must use fmtMonthlySector")
    if 'replace(/業$/, "")' not in html and "replace(/業$/," not in html:
        _fail("TOP sector must drop trailing 業")
    if "fmtMonthlySector(r.sector33)" not in list_body:
        _fail("TOP sector cell must be truncated sector33")
    if "${r.market||\"\"}" in list_body or "${r.market||''}" in list_body:
        _fail("TOP must not dump raw market names")
    if "${r.sector33||\"\"}" in list_body:
        _fail("TOP must not dump raw sector33")
    if ".slice(0, 5)" not in html and ".slice(0,5)" not in html:
        _fail("TOP company name must be first 5 characters")
    label_fn = html.find("function fmtMonthlyIssuerLabel")
    if label_fn < 0:
        _fail("fmtMonthlyIssuerLabel missing")
    label_body = html[label_fn:label_fn + 280]
    if "「" in label_body or "」" in label_body:
        _fail("TOP name must not wrap the code in 「」")
    if "`${code} ${short}`" not in label_body:
        _fail("TOP name is code, space, then 5-char name")
    if "全店(前年比)" not in list_body or "既存店(前年比)" not in list_body:
        _fail("TOP headers must include yoy columns")
    top_start = html.find('id="monthly-top"')
    det_start = html.find('id="monthly-detail"')
    if top_start < 0 or det_start < 0 or det_start <= top_start:
        _fail("monthly-top / monthly-detail bounds")
    if 'class="panel"' in html[top_start:det_start]:
        _fail("TOP must not wrap in .panel")
    tbl_css = html.find("#monthly-tbl th")
    if tbl_css < 0:
        tbl_css = html.find("#monthly-tbl td")
    if tbl_css < 0:
        _fail("TOP table cell CSS missing")
    tbl_block = html[tbl_css:tbl_css + 220]
    if "1px solid" not in tbl_block and "1pxsolid" not in tbl_block.replace(" ", ""):
        _fail("TOP table cells must have full grid lines")
    if "function loadMonthlyTables" not in html:
        _fail("index.html missing loadMonthlyTables")
    if "function monthlyDisplayYears" not in html:
        _fail("index.html missing monthlyDisplayYears")
    disp_fn = html.find("function monthlyDisplayYears")
    disp_end = html.find("function monthlyYearColor")
    if disp_fn < 0 or disp_end <= disp_fn:
        _fail("monthlyDisplayYears / monthlyYearColor bounds")
    disp_body = html[disp_fn:disp_end]
    if "latest - 2" not in disp_body and "latest-2" not in disp_body.replace(" ", ""):
        _fail("monthlyDisplayYears must keep latest and previous 2 calendar years")
    if "function monthlyYearColor" not in html:
        _fail("index.html missing monthlyYearColor")
    if 'const MONTHLY_YEAR_COLORS = ["#0077b6","#2e8b57","#b8860b","#8e4ec6"]' not in html:
        _fail("SPA MONTHLY_YEAR_COLORS palette")
    if "`/api/v1/tables/${selMonthlyCode}`" not in html:
        _fail("SPA must GET /api/v1/tables/{code}")
    if "`/api/v1/tables/${selMonthlyCode}/${y}`" not in html:
        _fail("SPA must GET /api/v1/tables/{code}/{year}")
    if "Promise.all" not in html:
        _fail("year tables must be fetched in parallel")
    if ".sort((a,b)=>b-a)" not in html:
        _fail("calendar-year tables must stack newest first")
    if "monthlyDisplayYears((info && info.years) || [])" not in html:
        _fail("stacked year tables must use the last-3-year window")
    if '<th class="l year">${d.year}</th>' not in html:
        _fail("thead top-left cell must be the calendar year")
    if "table-layout:fixed" not in html.replace(" ", ""):
        _fail("monthly tables must use table-layout:fixed")
    if "<colgroup>" not in html:
        _fail("monthly tables must emit colgroup")
    if 'col class="monthly-m"' not in html:
        _fail("month cols must share col.monthly-m")
    if "--monthly-m-w" not in html:
        _fail("one shared --monthly-m-w for all month cols")
    wrap_rule = html.find("#monthly-table-wrap")
    if wrap_rule < 0:
        _fail("#monthly-table-wrap CSS missing")
    wrap_block = html[wrap_rule:wrap_rule + 180]
    if "overflow-x:auto" not in wrap_block.replace(" ", ""):
        _fail("#monthly-table-wrap must overflow-x:auto")
    name_rule = html.find("table.monthly thead th:first-child")
    if name_rule < 0:
        name_rule = html.find("table.monthly td:first-child")
    if name_rule < 0:
        _fail("monthly first-child column CSS missing")
    name_block = html[name_rule:name_rule + 280]
    if "min-width:16em" not in name_block.replace(" ", "") and "min-width:14em" not in name_block.replace(" ", ""):
        _fail("monthly first column must have a readable min-width")
    if "z-index:2" not in name_block.replace(" ", "") and "z-index:3" not in name_block.replace(" ", ""):
        _fail("sticky first cells need z-index so month numbers stay underneath")
    if "var(--panel)" not in name_block:
        _fail("sticky first cells need opaque panel background")
    if "function monthlyRowOrder" not in html:
        _fail("index.html missing monthlyRowOrder")
    if "function monthlyLayoutLeaf" not in html:
        _fail("index.html missing monthlyLayoutLeaf")
    if "function monthlyRowScopeRank" not in html:
        _fail("index.html missing monthlyRowScopeRank")
    scope_fn = html.find("function monthlyRowScopeRank")
    scope_end = html.find("function isAllStoreSalesYoy")
    if scope_fn < 0 or scope_end <= scope_fn:
        _fail("monthlyRowScopeRank / isAllStoreSalesYoy bounds")
    scope_body = html[scope_fn:scope_end]
    compact = scope_body.replace(" ", "")
    if 'label.metric_kind==="stores"' not in compact:
        _fail("SPA must not fold unlabeled 店舗数 into 全店")
    row_fn = html.find("function monthlyRowOrder")
    row_end = html.find("function escHtml")
    if row_fn < 0 or row_end <= row_fn:
        _fail("monthlyRowOrder / escHtml bounds")
    row_body = html[row_fn:row_end]
    if "L.display" not in row_body:
        _fail("monthlyRowOrder must fold on label.display")
    if "label: L" not in row_body and "label:L" not in row_body:
        _fail("monthlyRowOrder must keep label on items")
    if "seen.has(name)" in row_body:
        _fail("monthlyRowOrder must not key by raw stored element name")
    if "MONTHLY_BREAKDOWN_RANK[L.breakdown]" not in row_body:
        _fail("monthlyRowOrder must sort 売上高 内訳 after the parent")
    if "function monthlySeriesKind" in html:
        _fail("SPA must not keep monthlySeriesKind string parser")
    if "function monthlySeriesChoices" not in html:
        _fail("index.html missing monthlySeriesChoices")
    choices_fn = html.find("function monthlySeriesChoices")
    choices_end = html.find("function pickDefaultMonthlySeries")
    if choices_fn < 0 or choices_end <= choices_fn:
        _fail("monthlySeriesChoices / pickDefaultMonthlySeries bounds")
    choices_body = html[choices_fn:choices_end]
    if "return MONTHLY_SERIES" in choices_body:
        _fail("select options must be the table's element names, not MONTHLY_SERIES")
    if "e.label.display" not in choices_body:
        _fail("#monthly-series ids must use label.display")
    if "monthlyRowOrder(" not in choices_body:
        _fail("#monthly-series options must follow year-table row order")
    load_fn = html.find("function loadMonthlyTables")
    if load_fn < 0:
        _fail("loadMonthlyTables missing")
    load_body = html[load_fn:load_fn + 1200]
    if "await loadMonthlyRowOrderConfig()" not in load_body:
        _fail("loadMonthlyTables must await monthly_row_order.json")
    if "function loadMonthlyRowOrderConfig" not in html:
        _fail("SPA missing loadMonthlyRowOrderConfig")
    if "function issuerSegmentTail" not in html:
        _fail("SPA missing issuerSegmentTail")
    if "/static/monthly_row_order.json" not in html:
        _fail("SPA must fetch /static/monthly_row_order.json")
    if "MONTHLY_SEGMENT_TAIL_BASE" not in html:
        _fail("SPA missing MONTHLY_SEGMENT_TAIL_BASE for segment_tail")
    if "monthlyDisplayYears" not in load_body:
        _fail("loadMonthlyTables must window to the last 3 calendar years")
    if "renderMonthlyYearTables(monthlyYearTables)" not in load_body:
        _fail("loadMonthlyTables must render via renderMonthlyYearTables")
    render_fn = html.find("function renderMonthlyYearTables")
    if render_fn < 0:
        _fail("renderMonthlyYearTables missing")
    render_body = html[render_fn:render_fn + 4500]
    if "monthlyRowOrder(" not in render_body:
        _fail("year tables must share one union row order")
    if "for(const e of rowOrder)" not in render_body:
        _fail("each year table must iterate the shared row order")
    if "Object.keys(vals).length" not in render_body:
        _fail("year table must skip rows with no values that year")
    if "byName[src.element]" in render_body:
        _fail("year table cells must map stored names through label.display")
    if "src.label.display" not in render_body:
        _fail("year table cells must look up rows by label.display")
    if "monthlyTableLayout(rowOrder" not in render_body:
        _fail("renderMonthlyYearTables must apply monthlyTableLayout after monthlyRowOrder")
    pick_fn = html.find("function pickDefaultMonthlySeries")
    pick_end = html.find("function monthlySeriesElement")
    if pick_fn < 0 or pick_end <= pick_fn:
        _fail("pickDefaultMonthlySeries / monthlySeriesElement bounds")
    pick_body = html[pick_fn:pick_end]
    if "isAllStoreSalesYoy" not in pick_body:
        _fail("default series must prefer 全店/全社 売上 yoy")
    if "series_kind" not in pick_body:
        _fail("default series must still fall back to all_store")
    if "function monthlySeriesElement" not in html:
        _fail("chart must resolve the selected row by element name")
    series_el_fn = html.find("function monthlySeriesElement")
    series_el_end = html.find("function fmtMonthlyYoyList")
    if series_el_fn < 0 or series_el_end <= series_el_fn:
        _fail("monthlySeriesElement / fmtMonthlyYoyList bounds")
    series_el_body = html[series_el_fn:series_el_end]
    if "e.label.display" not in series_el_body:
        _fail("chart row lookup must match leftover short names via label.display")
    if "e.element===seriesId" in series_el_body.replace(" ", ""):
        _fail("monthlySeriesElement must not require stored name == series id")
    if "renderMonthlySeriesSelect" not in html:
        _fail("series <select> must be filled from actual table elements")
    if 'data-series="${escHtml(e.element)}"' not in html:
        _fail("every table row must be selectable by element name")
    if "onclick=\"setMonthlySeries(this.dataset.series)\"" not in html:
        _fail("table row click must still switch the chart series")
    if "maxNumLen" not in html or "txt.length" not in html:
        _fail("month col width must come from fmtMonthlyCell text length")
    year_rule = html.find("table.monthly thead th.l")
    if year_rule < 0:
        _fail("year cell CSS missing (table.monthly thead th.l)")
    year_block = html[year_rule:year_rule + 280]
    if "var(--up)" not in year_block:
        _fail("year cell must be red fill")
    if "color:#fff" not in year_block.replace(" ", "") and "color:#ffffff" not in year_block.replace(" ", ""):
        _fail("year cell must be white text")
    if "font-weight:bold" not in year_block.replace(" ", "") and "font-weight:700" not in year_block.replace(" ", ""):
        _fail("year cell must be bold")
    if "font-size:11px" in year_block.replace(" ", ""):
        _fail("year cell must be larger than 11px month headers")
    if "font-size:13px" not in year_block.replace(" ", "") and "font-size:14px" not in year_block.replace(" ", ""):
        _fail("year cell must be slightly larger than month headers")
    month_hdr = html.find("background:#eceff2")
    if month_hdr < 0:
        month_hdr = html.find("background: #eceff2")
    if month_hdr < 0:
        _fail("month header cells must use light gray #eceff2")
    month_hdr_block = html[max(0, month_hdr - 180):month_hdr + 140]
    if "thead th:not(:first-child)" not in month_hdr_block:
        _fail("light gray must apply to month header cells, not the year cell")
    month_hdr_compact = month_hdr_block.replace(" ", "")
    if "font-weight:700" not in month_hdr_compact and "font-weight:bold" not in month_hdr_compact:
        _fail("month labels must be bold")
    if "text-align:center" not in month_hdr_compact:
        _fail("month labels must be center-aligned")
    if "データなし" not in html:
        _fail("empty copy データなし missing")
    chart_fn = html.find("function drawMonthlyChart")
    chart_end = html.find("function fmtMonthlyCell")
    if chart_fn < 0 or chart_end < 0 or chart_end <= chart_fn:
        _fail("drawMonthlyChart bounds")
    chart_body = html[chart_fn:chart_end]
    if "monthlyDisplayYears" not in chart_body:
        _fail("chart must use the same last-3-year window as the tables")
    if "monthlyYearColor(year)" not in chart_body:
        _fail("bars and legend must use monthlyYearColor(year)")
    if "MONTHLY_YEAR_COLORS[yi" in chart_body:
        _fail("chart colors must not follow display order")
    if "fill-opacity" in chart_body or 'fill="#d32f2f"' in chart_body:
        _fail("no red-below-100 overlay in JS")
    if "v<100" in chart_body.replace(" ", ""):
        _fail("must not special-case yoy under 100 for bar fill")
    if "y100" not in chart_body or "stroke-dasharray" not in chart_body:
        _fail("keep yoy 100 horizontal line")
    if "n+100" in chart_body.replace(" ", "") or "+=100" in chart_body.replace(" ", ""):
        _fail("chart must plot stored values, not leftover yoy_pct+100")
    color_fn = html.find("function monthlyYearColor")
    color_end = html.find("function uniqueMonthlyIssuers")
    if color_fn < 0 or color_end <= color_fn:
        _fail("monthlyYearColor / uniqueMonthlyIssuers bounds")
    color_body = html[color_fn:color_end]
    if "Number(year)" not in color_body:
        _fail("monthlyYearColor must key off the calendar year")
    if "% n" not in color_body and "%n" not in color_body.replace(" ", ""):
        _fail("monthlyYearColor must map year % palette length")
    if 'text-anchor="end"' not in chart_body:
        _fail("Y-axis tick numbers must be drawn (text-anchor=end in the left gutter)")
    if "monthlyYTicks" not in chart_body and "function monthlyYTicks" not in html:
        _fail("Y-axis ticks must be generated, not left as empty L pad")
    if "monthlyIsPhone" not in html and 'matchMedia("(max-width:640px)")' not in chart_body:
        _fail("chart must use max-width:640px (or similar) so phone fonts/gutters differ from PC")
    if "fsAxis" not in chart_body and 'font-size="11"' in chart_body and "280" not in chart_body:
        _fail("phone chart text must be larger than desktop font-size 11")
    if "fsTitle" in chart_body:
        _fail("chart must not draw a series title; #monthly-series is the label")
    if "spec.label)}</text>" in chart_body:
        _fail("chart SVG must not render spec.label as a title")
    mq = html.find("@media (max-width:640px)")
    if mq < 0:
        _fail("phone/PC split must use @media (max-width:640px)")
    mq_block = html[mq:mq + 1400]
    if "table.monthly" not in mq_block:
        _fail("phone monthly table rules must live in max-width:640px")
    mq_compact = mq_block.replace(" ", "").replace("\n", "")
    if "position:static" not in mq_compact:
        _fail("phone must turn off first-column sticky (or equivalent static position)")
    if "min-width:8em" not in mq_compact and "width:8em" not in mq_compact:
        _fail("phone name column must be much shorter than 16em so months are visible")
    if "right:0" in mq_compact:
        _fail("do not sticky-fix the rightmost month column on phone")
    monthly_css_start = html.find("table.monthly")
    monthly_css = html[monthly_css_start:mq] if mq > monthly_css_start else html[monthly_css_start:monthly_css_start + 2500]
    if "right:0" in monthly_css.replace(" ", ""):
        _fail("do not sticky-fix the rightmost month column")
    if "right:auto" not in monthly_css.replace(" ", ""):
        _fail("month columns must not be right-pinned (right:auto)")
    if "function monthlySegmentGate" not in html:
        _fail("index.html missing monthlySegmentGate")
    if "function monthlyTableLayout" not in html:
        _fail("index.html missing monthlyTableLayout")
    if "monthly-sec" not in html:
        _fail("SPA missing monthly-sec section row class")
    if "monthlyTableLayout(rowOrder" not in html:
        _fail("SPA must apply monthlyTableLayout after monthlyRowOrder")
    if 'id="monthly-layout-debug"' not in html:
        _fail("TEMP debug control #monthly-layout-debug must exist for flat vs gated compare")
    if "setMonthlyLayoutMode" not in html:
        _fail("SPA missing setMonthlyLayoutMode for TEMP flat layout toggle")
    if "MONTHLY_LAYOUT_MODE_KEY" not in html:
        _fail("TEMP layout mode must use a named sessionStorage key")
    if "forceFlat" not in html:
        _fail("monthlyTableLayout must accept forceFlat / flat mode for TEMP debug")
    if "<optgroup" not in html:
        _fail("#monthly-series must support optgroup for gated brands")
    if "monthly-gated" not in html:
        _fail("gated year tables must use a monthly-gated class")
    if "monthly-stub-ch" not in html or "monthly-stub-sc" not in html or "monthly-stub-leaf" not in html:
        _fail("gated tables must emit channel/scope/leaf stub columns")
    if "monthly-bd" not in html:
        _fail("売上高 内訳 leaf must use monthly-bd")
    if "td.monthly-stub-leaf.monthly-bd" not in html:
        _fail("内訳 leaf CSS must stay on the existing stub-leaf column")
    if "└" not in html:
        _fail("内訳 leaf must indent with └")
    if "font-size:12px" not in html[html.find("td.monthly-stub-leaf.monthly-bd"):html.find("td.monthly-stub-leaf.monthly-bd") + 180]:
        _fail("内訳 leaf must be 12px")
    if "colspan=\"3\"" not in html and "colspan='3'" not in html:
        _fail("gated year header must span the three stub columns")
    if '<tr class="monthly-sec"><td class="monthly-sec-label" colspan="3">' not in html:
        _fail("section row label must span only the three stub columns so the month grid continues")
    if "monthly-sec-label" not in html:
        _fail("section label cell must use monthly-sec-label (not bare :first-child)")
    if "monthly-sec-month" not in html:
        _fail("section row must emit monthly-sec-month cells with 1月…12月")
    if 'colspan="15"' in html:
        _fail("section row must not be one full-width cell (it cannot stick and it breaks the month grid)")
    gate_fn = html.find("function monthlySegmentGate")
    gate_end = html.find("function monthlyStubChannelText")
    if gate_fn < 0 or gate_end <= gate_fn:
        _fail("monthlySegmentGate / monthlyStubChannelText bounds")
    gate_body = html[gate_fn:gate_end]
    if "monthlyLatestYearTable" not in gate_body and "latest" not in gate_body:
        _fail("monthlySegmentGate must look at the latest display year")
    if ">= 2" not in gate_body and ">=2" not in gate_body.replace(" ", ""):
        _fail("monthlySegmentGate must require the leftover on at least 2 rows")
    layout_fn = html.find("function monthlyTableLayout")
    layout_end = html.find("function monthlyAssignStubSpans")
    if layout_fn < 0 or layout_end <= layout_fn:
        _fail("monthlyTableLayout / monthlyAssignStubSpans bounds")
    layout_body = html[layout_fn:layout_end]
    if "monthlyReorderBlock" not in layout_body:
        _fail("gated sections must share one channel-first reorder")
    reorder_fn = html.find("function monthlyReorderBlock")
    if reorder_fn < 0 or reorder_fn > layout_fn:
        _fail("monthlyReorderBlock missing")
    reorder_body = html[reorder_fn:layout_fn]
    chan_at = reorder_body.find("channelRank")
    scope_at = reorder_body.find("scopeRank")
    if chan_at < 0 or scope_at < 0 or chan_at > scope_at:
        _fail("gated blocks must reorder by channel before scope")
    if "既存店" not in html:
        _fail("scope stub text must map 既存 to 既存店")


def _assert_monthly_layout_js() -> None:
    """Gate + second layout pass. Calls the SPA functions, not a Python reimplementation."""
    import json
    import subprocess

    js_path = ROOT / "static" / "js" / "monthly.js"
    if not js_path.is_file():
        _fail("static/js/monthly.js missing")

    def _vals() -> dict:
        return {str(m): (1 if m == 1 else None) for m in range(1, 13)}

    def _el(name: str, unit: str = "yoy_index") -> dict:
        return {
            "element": name,
            "unit": unit,
            "label": monthly_element_label(name, unit),
            "values": _vals(),
        }

    fixtures = {
        "gatedTables": [{
            "year": 2026,
            "code": "30970",
            "elements": [
                _el("直営+FC 全店 売上高(前年比)"),
                _el("焼肉 直営+FC 既存 売上高(前年比)"),
                _el("焼肉 直営 既存 売上高(前年比)"),
                _el("日本+海外 直営+FC 全店 店舗数", "number"),
                _el("ラーメン 直営+FC 既存 売上高(前年比)"),
                _el("ラーメン 直営 既存 店舗数", "number"),
            ],
        }],
        "flatTables": [{
            "year": 2026,
            "code": "72030",
            "elements": [
                _el("全店 売上高(前年比)"),
                _el("既存 売上高(前年比)"),
            ],
        }],
        "matsukiyo": [{
            "year": 2026,
            "code": "30880",
            "elements": [
                _el("全店 売上高 ドラッグ(前年比)"),
                _el("MKG ドラッグ 全店 売上高(前年比)"),
                _el("MKG 全店 売上高 調剤(前年比)"),
                _el("MKG 全店 客数(前年比)"),
                _el("MKG 全店 売上高(前年比)"),
                _el("全店 売上高(前年比)"),
                _el("既存 売上高(前年比)"),
            ],
        }],
        "leftoverMkg": _el("MKG ドラッグ 全店 売上高(前年比)"),
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const jsPath = process.argv[1];
const fixtures = """ + json.dumps(fixtures, ensure_ascii=False) + r""";
const code = fs.readFileSync(jsPath, "utf8");
const el = {
  innerHTML: "",
  value: "",
  textContent: "",
  style: { display: "", setProperty() {}, removeProperty() {} },
  classList: { toggle() {} },
  dataset: {},
  clientWidth: 720,
  addEventListener() {},
};
const sandbox = {
  console,
  fetch: async () => ({ ok: false, json: async () => ({}) }),
  $: () => el,
  document: {
    querySelector: () => el,
    querySelectorAll: () => [],
    documentElement: { clientWidth: 720 },
  },
  window: {
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener() {},
  },
};
sandbox.window = sandbox.window;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const gatedTables = fixtures.gatedTables;
const flatTables = fixtures.flatTables;
const matsukiyo = fixtures.matsukiyo;
const rowOrder = sandbox.monthlyRowOrder(gatedTables);
const layout = sandbox.monthlyTableLayout(rowOrder, gatedTables);
const gated = [...sandbox.monthlySegmentGate(gatedTables)].sort();
const summary = layout.rows.map((r) => {
  if (r.kind === "section") return { kind: "section", label: r.label };
  return {
    kind: "data",
    element: r.element,
    group: r.group,
    channel: r.channel,
    scope: r.scope,
    leaf: r.leaf,
  };
});
const flatForce = sandbox.monthlyTableLayout(rowOrder, gatedTables, {forceFlat: true});
sandbox.setMonthlyLayoutMode("flat");
const flatMode = sandbox.monthlyTableLayout(rowOrder, gatedTables);
sandbox.setMonthlyLayoutMode("gated");
const flatOrder = sandbox.monthlyRowOrder(flatTables);
const flat = sandbox.monthlyTableLayout(flatOrder, flatTables);
const bdOrder = sandbox.monthlyRowOrder(matsukiyo);
const bdLayout = sandbox.monthlyTableLayout(bdOrder, matsukiyo);
const drugRow = matsukiyo[0].elements.find((e) => e.element === "全店 売上高 ドラッグ(前年比)");
console.log(JSON.stringify({
  gated,
  gatedFlag: layout.gated,
  summary,
  rowOrderIds: rowOrder.map((r) => r.element),
  flatForceGated: flatForce.gated,
  flatForceKinds: flatForce.rows.map((r) => r.kind),
  flatForceIds: flatForce.rows.map((r) => r.element),
  flatModeGated: flatMode.gated,
  flatModeIds: flatMode.rows.map((r) => r.element),
  flatGated: flat.gated,
  flatIds: flat.rows.map((r) => r.element),
  flatKinds: flat.rows.map((r) => r.kind),
  bdCanon: fixtures.leftoverMkg.label.display,
  bdGate: [...sandbox.monthlySegmentGate(matsukiyo)].sort(),
  bdDefault: sandbox.pickDefaultMonthlySeries(matsukiyo),
  bdCompanyKind: drugRow && drugRow.label.series_kind,
  bdCols: bdLayout.rows.some((r) => r.rail || r.col4 || r.stub4) ? 4 : 3,
  bdSummary: bdLayout.rows.map((r) => {
    if (r.kind === "section") return { kind: "section", label: r.label };
    return {
      kind: "data",
      element: r.element,
      group: r.group,
      channel: r.channel,
      scope: r.scope,
      leaf: r.leaf,
      breakdown: r.breakdown || "",
    };
  }),
}));
"""
    proc = subprocess.run(
        ["node", "-e", harness, str(js_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        _fail(f"monthly layout js: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}")
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        _fail(f"monthly layout js stdout: {proc.stdout[:400]!r}")
    if got.get("gated") != ["ラーメン", "焼肉"]:
        _fail(f"gate leftovers: {got.get('gated')!r}")
    if got.get("gatedFlag") is not True:
        _fail("30970 fixture must be gated")
    want = [
        {"kind": "data", "element": "直営+FC 全店 売上高(前年比)", "group": "", "channel": "直営+FC", "scope": "全店", "leaf": "売上高(前年比)"},
        {"kind": "data", "element": "日本+海外 直営+FC 全店 店舗数", "group": "", "channel": "直営+FC", "scope": "全店", "leaf": "日本+海外 店舗数"},
        {"kind": "section", "label": "焼肉"},
        {"kind": "data", "element": "焼肉 直営+FC 既存 売上高(前年比)", "group": "焼肉", "channel": "直営+FC", "scope": "既存", "leaf": "売上高(前年比)"},
        {"kind": "data", "element": "焼肉 直営 既存 売上高(前年比)", "group": "焼肉", "channel": "直営", "scope": "既存", "leaf": "売上高(前年比)"},
        {"kind": "section", "label": "ラーメン"},
        {"kind": "data", "element": "ラーメン 直営+FC 既存 売上高(前年比)", "group": "ラーメン", "channel": "直営+FC", "scope": "既存", "leaf": "売上高(前年比)"},
        {"kind": "data", "element": "ラーメン 直営 既存 店舗数", "group": "ラーメン", "channel": "直営", "scope": "既存", "leaf": "店舗数"},
    ]
    if got.get("summary") != want:
        _fail(f"gated layout: {got.get('summary')!r}")
    if got.get("flatForceGated") is not False:
        _fail("forceFlat must disable gate layout")
    if "section" in (got.get("flatForceKinds") or []):
        _fail("forceFlat must not emit section rows")
    if got.get("flatForceIds") != got.get("rowOrderIds"):
        _fail(f"forceFlat must keep monthlyRowOrder ids: {got.get('flatForceIds')!r}")
    if got.get("flatModeGated") is not False:
        _fail("monthlyLayoutMode=flat must disable gate layout")
    if got.get("flatModeIds") != got.get("rowOrderIds"):
        _fail(f"monthlyLayoutMode=flat must keep monthlyRowOrder ids: {got.get('flatModeIds')!r}")
    if got.get("flatGated") is not False:
        _fail("simple issuer must stay ungated")
    if got.get("flatKinds") != ["data", "data"]:
        _fail(f"ungated kinds: {got.get('flatKinds')!r}")
    if "section" in (got.get("flatKinds") or []):
        _fail("ungated issuer must not grow section rows")
    if got.get("flatIds") != ["全店 売上高(前年比)", "既存 売上高(前年比)"]:
        _fail(f"ungated ids stay full names: {got.get('flatIds')!r}")
    if got.get("bdCanon") != "MKG 全店 売上高 ドラッグ(前年比)":
        _fail(f"old leftover ドラッグ must display-canonicalize: {got.get('bdCanon')!r}")
    if got.get("bdGate") != ["MKG"]:
        _fail(f"30880 gate leftover must stay MKG: {got.get('bdGate')!r}")
    if got.get("bdDefault") != "全店 売上高(前年比)":
        _fail(f"TOP series must ignore 内訳: {got.get('bdDefault')!r}")
    if got.get("bdCompanyKind") is not None:
        _fail(f"会社 ドラッグ must not be all_store: {got.get('bdCompanyKind')!r}")
    if got.get("bdCols") != 3:
        _fail(f"内訳 must not add a stub column: {got.get('bdCols')!r}")
    want_bd = [
        {"kind": "data", "element": "全店 売上高(前年比)", "group": "", "channel": "", "scope": "全店", "leaf": "売上高(前年比)", "breakdown": ""},
        {"kind": "data", "element": "全店 売上高 ドラッグ(前年比)", "group": "", "channel": "", "scope": "全店", "leaf": "└ ドラッグ(前年比)", "breakdown": "ドラッグ"},
        {"kind": "data", "element": "既存 売上高(前年比)", "group": "", "channel": "", "scope": "既存", "leaf": "売上高(前年比)", "breakdown": ""},
        {"kind": "section", "label": "MKG"},
        {"kind": "data", "element": "MKG 全店 売上高(前年比)", "group": "MKG", "channel": "", "scope": "全店", "leaf": "売上高(前年比)", "breakdown": ""},
        {"kind": "data", "element": "MKG 全店 売上高 ドラッグ(前年比)", "group": "MKG", "channel": "", "scope": "全店", "leaf": "└ ドラッグ(前年比)", "breakdown": "ドラッグ"},
        {"kind": "data", "element": "MKG 全店 売上高 調剤(前年比)", "group": "MKG", "channel": "", "scope": "全店", "leaf": "└ 調剤(前年比)", "breakdown": "調剤"},
        {"kind": "data", "element": "MKG 全店 客数(前年比)", "group": "MKG", "channel": "", "scope": "全店", "leaf": "客数(前年比)", "breakdown": ""},
    ]
    if got.get("bdSummary") != want_bd:
        _fail(f"30880 内訳 layout: {got.get('bdSummary')!r}")



def _assert_known_prod_units(c) -> None:
    from app.db import SessionLocal, apply_known_monthly_element_units
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
        v = db.get(MonthlyValue, ("262A0", 2024, "全店 売上高", 1))
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
    if "全店 売上高" in by:
        vals = by["全店 売上高"]["values"]
        raw = vals.get("1", vals.get(1))
        if raw is not None and float(raw) != -7.1:
            _fail(f"262A0 value converted: {vals!r}")

if __name__ == "__main__":
    main()

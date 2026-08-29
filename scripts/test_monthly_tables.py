"""月次データ登録 API (/api/v1/tables) の仕様テスト。手元の stock.db は触らない。"""
from __future__ import annotations

import os
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
    MonthlyIngestError,
    canonicalize_element_label,
    format_monthly_cell,
    parse_monthly_unit,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _err(resp) -> dict:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        _fail(f"error shape: {resp.status_code} {body!r}")
    return err


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def main() -> None:
    # display is index 100=flat (not +X.X% / ▲). unused third arg: value+unit only.
    if format_monthly_cell(None, "yoy_index") != "":
        _fail("empty cell")
    if format_monthly_cell(105.2, "yoy_index") != "105.2":
        _fail(f"yoy_index 105.2: {format_monthly_cell(105.2, 'yoy_index')!r}")
    if format_monthly_cell(125.8, "yoy_index") != "125.8":
        _fail(f"yoy_index 125.8: {format_monthly_cell(125.8, 'yoy_index')!r}")
    if format_monthly_cell(94.8, "yoy_index") != "94.8":
        _fail(f"yoy_index 94.8: {format_monthly_cell(94.8, 'yoy_index')!r}")
    if format_monthly_cell(-7.1, "yoy_pct") != "92.9":
        _fail(f"yoy_pct -7.1: {format_monthly_cell(-7.1, 'yoy_pct')!r}")
    if format_monthly_cell(5.2, "yoy_pct") != "105.2":
        _fail(f"yoy_pct 5.2: {format_monthly_cell(5.2, 'yoy_pct')!r}")
    if format_monthly_cell(13, "yoy_pct") != "113":
        _fail(f"yoy_pct +13: {format_monthly_cell(13, 'yoy_pct')!r}")
    if format_monthly_cell(303, "number") != "303":
        _fail(f"number 303: {format_monthly_cell(303, 'number')!r}")
    if format_monthly_cell(None, "number") != "":
        _fail("empty number")
    try:
        parse_monthly_unit("percent")
    except MonthlyIngestError as exc:
        if exc.status != 400 or exc.code != "INVALID_PARAM":
            _fail(f"invalid unit: {exc.status} {exc.code}")
    else:
        _fail("invalid unit should 400")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if "fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell")
    if "function fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell function")
    if "function fmtMonthlyElement" not in html:
        _fail("index.html missing fmtMonthlyElement function")
    if "function stripYoyFragments" not in html:
        _fail("index.html missing stripYoyFragments")
    if "fmtMonthlyElement(e.element, unit)" not in html:
        _fail("index.html item column must use fmtMonthlyElement")
    if "▲" in html or "+X.X%" in html:
        _fail("index.html still has percent/▲ monthly display")
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "売上高(前年比)":
        _fail("canonicalize strip+suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存売上高(前年比)":
        _fail("canonicalize no double")
    if canonicalize_element_label("売上高前年同月比", "number") != "売上高前年同月比":
        _fail("canonicalize number unchanged")

    c = TestClient(app)
    code, year = "72030", "2024"
    base = f"/api/v1/tables/{code}/{year}"

    # TABLE_NOT_FOUND on GET missing table
    r = c.get(base)
    _expect(r, 404, "GET missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"GET missing table code: {r.json()!r}")

    # GET /tables empty (銘柄がまだ無い)
    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables empty")
    if r.json() != []:
        _fail(f"empty tables: {r.json()!r}")

    # GET /tables/{code} with no years
    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years empty")
    if r.json() != {"code": code, "years": []}:
        _fail(f"empty years: {r.json()!r}")

    # INVALID_PARAM: bad code / year / element
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

    r = c.post(f"{base}/elements", json={"element": ""})
    _expect(r, 400, "empty element")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"empty element: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "あ" * 51})
    _expect(r, 400, "element too long")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"long element: {r.json()!r}")

    # table auto-create on POST elements
    r = c.post(f"{base}/elements", json={"element": " 売上高 前年比 "})
    _expect(r, 201, "create element")
    if r.json() != {"code": code, "year": 2024, "element": "売上高 前年比",
                    "unit": "number", "created": True}:
        _fail(f"create: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET after auto-create")
    if r.json()["code"] != code or r.json()["year"] != 2024:
        _fail(f"get table: {r.json()!r}")
    if [e["element"] for e in r.json()["elements"]] != ["売上高 前年比"]:
        _fail(f"elements after create: {r.json()!r}")
    if r.json()["elements"][0].get("unit") != "number":
        _fail(f"GET default unit: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years after create")
    if r.json() != {"code": code, "years": [2024]}:
        _fail(f"years: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables one code")
    if r.json() != [{"code": code, "name": None, "years": [2024]}]:
        _fail(f"tables one code: {r.json()!r}")

    # idempotent element create
    r = c.post(f"{base}/elements", json={"element": "売上高 前年比"})
    _expect(r, 200, "idempotent exact")
    if r.json() != {"code": code, "year": 2024, "element": "売上高 前年比",
                    "unit": "number", "created": False}:
        _fail(f"idempotent: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": " 売上高 前年比 "})
    _expect(r, 200, "idempotent trimmed")
    if r.json()["created"] is not False:
        _fail(f"idempotent trimmed: {r.json()!r}")

    # similar-element 409 (空白差)
    r = c.post(f"{base}/elements", json={"element": "売上高前年比"})
    _expect(r, 409, "similar")
    err = _err(r)
    if err["code"] != "ELEMENT_SIMILAR_EXISTS" or err["message"] != SIMILAR_MSG:
        _fail(f"similar err: {r.json()!r}")
    if err.get("similar") != ["売上高 前年比"]:
        _fail(f"similar list: {r.json()!r}")

    # force=true
    r = c.post(f"{base}/elements", json={"element": "売上高前年比", "force": True})
    _expect(r, 201, "force create")
    if r.json() != {"code": code, "year": 2024, "element": "売上高前年比",
                    "unit": "number", "created": True}:
        _fail(f"force: {r.json()!r}")

    # 3つ目 (登録順確認用)
    r = c.post(f"{base}/elements", json={"element": "営業利益"})
    _expect(r, 201, "third element")

    # PUT missing table (別年度)
    r = c.put("/api/v1/tables/7203/2025/values",
              json={"element": "営業利益", "month": 4, "value": 1})
    _expect(r, 404, "PUT missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"PUT missing table: {r.json()!r}")

    # ELEMENT_NOT_FOUND, 自動登録しない
    r = c.put(f"{base}/values", json={"element": "未登録要素", "month": 4, "value": 1.0})
    _expect(r, 404, "unregistered element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"ELEMENT_NOT_FOUND: {r.json()!r}")
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if "未登録要素" in names:
        _fail("values PUT must not auto-register element")

    # INVALID_VALUE
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 4, "value": "105.2%"})
    _expect(r, 400, "percent string")
    err = _err(r)
    if err["code"] != "INVALID_VALUE" or err["message"] != INVALID_VALUE_MSG:
        _fail(f"105.2%: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 4, "value": "1,234百万円"})
    _expect(r, 400, "unit string")
    if _err(r)["code"] != "INVALID_VALUE" or _err(r)["message"] != INVALID_VALUE_MSG:
        _fail(f"1,234百万円: {r.json()!r}")

    # INVALID_PARAM month
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 0, "value": 1})
    _expect(r, 400, "month 0")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 0: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 13, "value": 1})
    _expect(r, 400, "month 13")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 13: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": "4", "value": 1})
    _expect(r, 400, "month string")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month string: {r.json()!r}")

    # 正常 upsert + 空月は GET から省略
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 4, "value": 105.2})
    _expect(r, 200, "put value")
    if r.json() != {"code": code, "year": 2024, "element": "売上高前年比", "updated": [4]}:
        _fail(f"put: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 6, "value": 110})
    _expect(r, 200, "put month 6")

    r = c.get(base)
    _expect(r, 200, "GET values")
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    v = by_name["売上高前年比"]
    if set(str(k) for k in v.keys()) != {"4", "6"}:
        _fail(f"omit empty months: {v!r}")
    if float(v["4"]) != 105.2 or float(v["6"]) != 110:
        _fail(f"values: {v!r}")
    if by_name["売上高 前年比"] != {}:
        _fail(f"empty element should have empty values: {by_name!r}")

    # last-write-wins overwrite
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 4, "value": 99.9})
    _expect(r, 200, "overwrite")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if float(by_name["売上高前年比"]["4"]) != 99.9:
        _fail(f"overwrite: {by_name['売上高前年比']!r}")

    # null deletes a cell
    r = c.put(f"{base}/values", json={"element": "売上高前年比", "month": 4, "value": None})
    _expect(r, 200, "delete cell")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if "4" in by_name["売上高前年比"] or 4 in by_name["売上高前年比"]:
        _fail(f"null did not delete: {by_name['売上高前年比']!r}")
    if "6" not in {str(k) for k in by_name["売上高前年比"].keys()}:
        _fail(f"unrelated month dropped: {by_name['売上高前年比']!r}")

    # batch all-or-nothing: 1件目は妥当、2件目は不正 → どちらも書かない
    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": 10}, {"month": 2, "value": "105.2%"}],
    })
    _expect(r, 400, "batch invalid")
    if _err(r)["code"] != "INVALID_VALUE":
        _fail(f"batch invalid: {r.json()!r}")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if by_name["営業利益"] != {}:
        _fail(f"batch leaked: {by_name['営業利益']!r}")

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

    # batch null delete + all-or-nothing on month
    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": None}, {"month": 3, "value": 1.5}],
    })
    _expect(r, 200, "batch mixed")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    ov = {str(k): v for k, v in by_name["営業利益"].items()}
    if "1" in ov or "3" not in ov or "2" not in ov:
        _fail(f"batch null: {ov!r}")

    # elements in registration order
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高 前年比", "売上高前年比", "営業利益"]:
        _fail(f"registration order: {names!r}")

    # 別年度
    r = c.post("/api/v1/tables/7203/2025/elements", json={"element": "売上高前年比"})
    _expect(r, 201, "other year")
    r = c.get(f"/api/v1/tables/{code}")
    if r.json()["years"] != [2024, 2025]:
        _fail(f"years multi: {r.json()!r}")

    # TDnet 4桁と J-Quants 5桁は同じ発行体 (7203 → 72030)
    r = c.post("/api/v1/tables/72030/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 200, "5-digit same issuer")
    r = c.get("/api/v1/tables/7203")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"4-digit years: {r.json()!r}")
    r = c.get("/api/v1/tables/72030")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"5-digit years: {r.json()!r}")

    # GET /tables: unique codes, years 昇順, code 順。stocks が無い name は null
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
        db.add(Stock(code="72030", name="トヨタ自動車"))
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

    r = c.post("/api/v1/tables/262A/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 201, "262A create")
    if r.json()["code"] != "262A0":
        _fail(f"262A should canonicalize: {r.json()!r}")
    r = c.get("/api/v1/tables/262A0/2024")
    _expect(r, 200, "GET 262A0")
    if r.json()["code"] != "262A0" or [e["element"] for e in r.json()["elements"]] != ["売上高前年比"]:
        _fail(f"GET 262A0: {r.json()!r}")
    r = c.get("/api/v1/tables/262A/2024")
    _expect(r, 200, "GET 262A display")
    if r.json()["code"] != "262A0":
        _fail(f"GET 262A display: {r.json()!r}")
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 200, "262A0 idempotent")
    if r.json() != {"code": "262A0", "year": 2024, "element": "売上高前年比",
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

    # unit: ingest/POST accept it, PUT changes only unit, GET returns it.
    # Storage is PDF original. Display is index 100=flat.
    r = c.get(base)
    _expect(r, 200, "GET units default")
    for el in r.json()["elements"]:
        if el.get("unit") != "number":
            _fail(f"7203 default unit: {el!r}")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高", "unit": "yoy_index"})
    _expect(r, 201, "create yoy_index")
    if r.json()["element"] != "全店売上高(前年比)":
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
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "売上変化(前年比)", "month": 11, "value": -7.1})
    _expect(r, 200, "put yoy_pct -7.1")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "create number")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "put number 303")

    r = c.get("/api/v1/tables/7203/2026")
    _expect(r, 200, "GET 2026 units")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"store yoy_index: {by['全店売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店売上高(前年比)"]["values"]["11"]), by["全店売上高(前年比)"]["unit"]) != "125.8":
        _fail("display yoy_index 125.8")
    if by["売上変化(前年比)"]["unit"] != "yoy_pct" or float(by["売上変化(前年比)"]["values"]["11"]) != -7.1:
        _fail(f"store yoy_pct: {by['売上変化(前年比)']!r}")
    if format_monthly_cell(float(by["売上変化(前年比)"]["values"]["11"]), by["売上変化(前年比)"]["unit"]) != "92.9":
        _fail("display yoy_pct -7.1")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"store number: {by['店舗数']!r}")
    if format_monthly_cell(float(by["店舗数"]["values"]["11"]), by["店舗数"]["unit"]) != "303":
        _fail("display number 303")

    # PUT unit only; values stay. Lookup is stored name (no rename).
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_pct"})
    _expect(r, 200, "PUT unit only")
    if r.json() != {"code": "72030", "year": 2026, "element": "全店売上高(前年比)", "unit": "yoy_pct"}:
        _fail(f"PUT unit: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店売上高(前年比)"]["unit"] != "yoy_pct" or float(by["全店売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"PUT must not convert value: {by['全店売上高(前年比)']!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit restore")

    # idempotent POST without unit keeps existing unit (stored canonical name)
    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高(前年比)"})
    _expect(r, 200, "POST omit unit")
    if r.json()["element"] != "全店売上高(前年比)":
        _fail(f"omit unit name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index" or r.json()["created"] is not False:
        _fail(f"omit unit must keep: {r.json()!r}")

    # do not guess unit from magnitude
    r = c.post("/api/v1/tables/7203/2026/elements",
               json={"element": "大きい指数", "unit": "yoy_pct"})
    _expect(r, 201, "yoy_pct with 125.8")
    if r.json()["element"] != "大きい指数(前年比)":
        _fail(f"yoy_pct 大きい指数 name: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "大きい指数(前年比)", "month": 1, "value": 125.8})
    _expect(r, 200, "put 125.8 as yoy_pct")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["大きい指数(前年比)"]["unit"] != "yoy_pct" or float(by["大きい指数(前年比)"]["values"]["1"]) != 125.8:
        _fail(f"must not guess/convert 125.8: {by['大きい指数(前年比)']!r}")

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

    # 7203 numeric values from earlier tests still intact
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    ov = {str(k): v for k, v in by_name["営業利益"].items()}
    if "2" not in ov or "3" not in ov or float(ov["2"]) != 98.7:
        _fail(f"7203 numeric values broken: {ov!r}")

    _assert_known_prod_units(c)

    print("ok: monthly tables api")


def _assert_known_prod_units(c) -> None:
    """Gift / Intermestic がテストDBにあれば unit を付ける。値は変換しない。無ければ skip。"""
    from app.db import SessionLocal, apply_known_monthly_element_units
    from app.models import MonthlyElement, MonthlyValue

    db = SessionLocal()
    try:
        if (not db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
                and not db.query(MonthlyElement).filter(MonthlyElement.code == "262A0").all()):
            return
    finally:
        db.close()

    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "全店売上高"})
    if r.status_code not in (200, 201):
        _fail(f"seed 92790: {r.status_code} {r.text[:200]}")
    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "店舗数"})
    _expect(r, 201, "seed 92790 stores")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "全店売上高", "month": 11, "value": 125.8})
    _expect(r, 200, "seed gift 125.8")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "seed gift stores")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code == 200:
        r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "店舗数"})
        if r.status_code not in (200, 201):
            _fail(f"seed 262A0 stores: {r.status_code} {r.text[:200]}")
        r = c.put("/api/v1/tables/262A0/2024/values",
                  json={"element": "売上高前年比", "month": 1, "value": -7.1})
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
            if row.element == "売上高前年比" and row.unit != "yoy_pct":
                _fail(f"262A0 売上高前年比 unit {row.unit!r}")
            if "店舗数" in row.element and row.unit != "number":
                _fail(f"262A0 店舗数 unit {row.unit!r}")
        v = db.get(MonthlyValue, ("92790", 2025, "全店売上高", 11))
        if v is None or float(v.value) != 125.8:
            _fail(f"gift value converted: {v}")
        v = db.get(MonthlyValue, ("262A0", 2024, "売上高前年比", 1))
        if v is not None and float(v.value) != -7.1:
            _fail(f"intermestic value converted: {v.value}")
    finally:
        db.close()

    r = c.get("/api/v1/tables/92790/2025")
    _expect(r, 200, "GET 92790 after unit assign")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店売上高"]["unit"] != "yoy_index" or float(by["全店売上高"]["values"]["11"]) != 125.8:
        _fail(f"92790 GET: {by['全店売上高']!r}")
    if format_monthly_cell(125.8, by["全店売上高"]["unit"]) != "125.8":
        _fail("92790 display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"92790 stores GET: {by['店舗数']!r}")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code != 200:
        return
    by = {e["element"]: e for e in r.json()["elements"]}
    if "売上高前年比" in by:
        if by["売上高前年比"]["unit"] != "yoy_pct":
            _fail(f"262A0 GET unit: {by['売上高前年比']!r}")
        vals = by["売上高前年比"]["values"]
        if "1" in vals or 1 in vals:
            raw = float(vals.get("1", vals.get(1)))
            if raw != -7.1:
                _fail(f"262A0 value converted: {vals!r}")
            if format_monthly_cell(raw, "yoy_pct") != "92.9":
                _fail("262A0 display")


if __name__ == "__main__":
    main()

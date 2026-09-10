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
    # display is index 100=flat (not +X.X% / ▲). unused third arg: value+unit only.
    if format_monthly_cell(None, "yoy_index") != "-":
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
    if format_monthly_cell(None, "number") != "-":
        _fail("empty number")
    if format_monthly_cell(0, "number") != "0":
        _fail(f"zero number: {format_monthly_cell(0, 'number')!r}")
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
    html = _spa_source()
    if "fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell")
    if "function fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell function")
    cell_fn = html.find("function fmtMonthlyCell")
    if cell_fn < 0:
        _fail("fmtMonthlyCell bounds")
    cell_body = html[cell_fn:cell_fn + 280]
    if 'return "-"' not in cell_body:
        _fail("fmtMonthlyCell empty must display -")
    if 'return ""' in cell_body:
        _fail("fmtMonthlyCell must not return blank for empty")
    yoy_fn = html.find("function fmtMonthlyYoyList")
    if yoy_fn < 0:
        _fail("fmtMonthlyYoyList missing")
    yoy_body = html[yoy_fn:yoy_fn + 220]
    if 'return "-"' not in yoy_body:
        _fail("fmtMonthlyYoyList empty must display -")
    if "function fmtMonthlyElement" not in html:
        _fail("index.html missing fmtMonthlyElement function")
    if "function stripYoyFragments" not in html:
        _fail("index.html missing stripYoyFragments")
    if "fmtMonthlyElement(e.element, unit)" not in html:
        _fail("index.html item column must use fmtMonthlyElement")
    if "▲" in html or "+X.X%" in html:
        _fail("index.html still has percent/▲ monthly display")
    _assert_monthly_spa(html)
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "全店 売上高(前年比)":
        _fail("canonicalize strip+suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("canonicalize no double")
    if canonicalize_element_label("売上高前年同月比", "number") != "全店 売上高":
        _fail("canonicalize number tokens")

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

    r = c.post("/api/v1/tables/30320/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 404, "POST without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"POST without master: {r.json()!r}")

    _ensure_stock(code)

    # table auto-create on POST elements
    r = c.post(f"{base}/elements", json={"element": " 売上高 "})
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

    # idempotent element create
    r = c.post(f"{base}/elements", json={"element": "売上高"})
    _expect(r, 200, "idempotent exact")
    if r.json() != {"code": code, "year": 2024, "element": "全店 売上高",
                    "unit": "number", "created": False}:
        _fail(f"idempotent: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": " 売上高 "})
    _expect(r, 200, "idempotent trimmed")
    if r.json()["created"] is not False:
        _fail(f"idempotent trimmed: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高前年比"})
    _expect(r, 200, "number drops 前年比 token")
    if r.json()["element"] != "全店 売上高" or r.json()["created"] is not False:
        _fail(f"売上高前年比 number folds: {r.json()!r}")

    # similar-element 409 (空白差。トークン正規化後も別名)
    r = c.post(f"{base}/elements", json={"element": "世界観ビジネス"})
    _expect(r, 201, "segment create")
    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス"})
    _expect(r, 409, "similar")
    err = _err(r)
    if err["code"] != "ELEMENT_SIMILAR_EXISTS" or err["message"] != SIMILAR_MSG:
        _fail(f"similar err: {r.json()!r}")
    if err.get("similar") != ["世界観ビジネス"]:
        _fail(f"similar list: {r.json()!r}")

    # force=true
    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス", "force": True})
    _expect(r, 201, "force create")
    if r.json() != {"code": code, "year": 2024, "element": "世界観 ビジネス",
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
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "105.2%"})
    _expect(r, 400, "percent string")
    err = _err(r)
    if err["code"] != "INVALID_VALUE" or err["message"] != INVALID_VALUE_MSG:
        _fail(f"105.2%: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "1,234百万円"})
    _expect(r, 400, "unit string")
    if _err(r)["code"] != "INVALID_VALUE" or _err(r)["message"] != INVALID_VALUE_MSG:
        _fail(f"1,234百万円: {r.json()!r}")

    # INVALID_PARAM month
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

    # 正常 upsert + 空月は GET から省略
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 105.2})
    _expect(r, 200, "put value")
    if r.json() != {"code": code, "year": 2024, "element": "全店 売上高", "updated": [4]}:
        _fail(f"put: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 6, "value": 110})
    _expect(r, 200, "put month 6")

    r = c.get(base)
    _expect(r, 200, "GET values")
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    v = by_name["全店 売上高"]
    if set(str(k) for k in v.keys()) != {"4", "6"}:
        _fail(f"omit empty months: {v!r}")
    if float(v["4"]) != 105.2 or float(v["6"]) != 110:
        _fail(f"values: {v!r}")
    if "世界観ビジネス" in by_name:
        _fail(f"empty element must be omitted: {by_name!r}")

    # last-write-wins overwrite
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 99.9})
    _expect(r, 200, "overwrite")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if float(by_name["全店 売上高"]["4"]) != 99.9:
        _fail(f"overwrite: {by_name['全店 売上高']!r}")

    # null deletes a cell
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": None})
    _expect(r, 200, "delete cell")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if "4" in by_name["全店 売上高"] or 4 in by_name["全店 売上高"]:
        _fail(f"null did not delete: {by_name['全店 売上高']!r}")
    if "6" not in {str(k) for k in by_name["全店 売上高"].keys()}:
        _fail(f"unrelated month dropped: {by_name['全店 売上高']!r}")

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
    if names != ["全店 売上高", "営業利益"]:
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
    r = c.post("/api/v1/tables/262A/2024/elements", json={"element": "売上高前年比"})
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
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高前年比"})
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

    # unit: ingest/POST accept it, PUT changes only unit, GET returns it.
    # Storage is PDF original. Display is index 100=flat.
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
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"store yoy_index: {by['全店 売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店 売上高(前年比)"]["values"]["11"]), by["全店 売上高(前年比)"]["unit"]) != "125.8":
        _fail("display yoy_index 125.8")
    if by["売上変化(前年比)"]["unit"] != "yoy_pct" or float(by["売上変化(前年比)"]["values"]["11"]) != -7.1:
        _fail(f"store yoy_pct: {by['売上変化(前年比)']!r}")
    if format_monthly_cell(float(by["売上変化(前年比)"]["values"]["11"]), by["売上変化(前年比)"]["unit"]) != "92.9":
        _fail("display yoy_pct -7.1")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"store number: {by['店舗数']!r}")
    if format_monthly_cell(float(by["店舗数"]["values"]["11"]), by["店舗数"]["unit"]) != "303":
        _fail("display number 303")

    # PUT unit only; values stay. Lookup はトークン正規化後の保存名。
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_pct"})
    _expect(r, 200, "PUT unit only")
    if r.json() != {"code": "72030", "year": 2026, "element": "全店 売上高(前年比)", "unit": "yoy_pct"}:
        _fail(f"PUT unit: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高(前年比)"]["unit"] != "yoy_pct" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"PUT must not convert value: {by['全店 売上高(前年比)']!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit restore")

    # idempotent POST without unit keeps existing unit (stored canonical name)
    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高(前年比)"})
    _expect(r, 200, "POST omit unit")
    if r.json()["element"] != "全店 売上高(前年比)":
        _fail(f"omit unit name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index" or r.json()["created"] is not False:
        _fail(f"omit unit must keep: {r.json()!r}")

    # POST yoy: canonicalize first, short then canonical is one row
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

    r = c.post("/api/v1/tables/60590/2026/elements", json={"element": "カラオケ全店 売上高"})
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

    # 7203 numeric values from earlier tests still intact
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    ov = {str(k): v for k, v in by_name["営業利益"].items()}
    if "2" not in ov or "3" not in ov or float(ov["2"]) != 98.7:
        _fail(f"7203 numeric values broken: {ov!r}")

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


# SPA 年表・チャート用。ingest には置かない。static/js/monthly.js と同じ規則。
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
    """全店→既存店→FC店→セグメント。各塊は 売上高→客数→客単価→出店→退店→店舗数。全社は全店。"""
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
    if monthly_row_scope_rank("既存店 売上高(前年比)") != 1:
        _fail("既存店 is scope 1")
    if monthly_row_scope_rank("売上高") != 0:
        _fail("unlabeled 売上高 stays 全店")
    if monthly_row_scope_rank("全店 店舗数") != 0:
        _fail("全店 店舗数 stays in 全店")
    if monthly_row_scope_rank("既存店 店舗数") != 1:
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
        "全店 売上高(前年比)",
        "全店 客数(前年比)",
        "直営 既存 客単価(前年比)",
        "FC 店舗数",
        "焼肉 全店 売上高(前年比)",
        "焼肉 既存 売上高(前年比)",
    ]
    if mixed_names != want_mixed:
        _fail(f"company-wide before segment, 全社=全店: {mixed_names!r}")

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
    if adj_names != want_adj:
        _fail(f"純増/新設/改装/業態転換 before 店舗数, names unchanged: {adj_names!r}")


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
        if 'id="monthly-series"' not in chunk:
            _fail("monthly tab select must be #monthly-series, not a year picker")
    if ">項目<" in html:
        _fail("monthly thead still uses 項目")
    if 'id="monthly-groups"' not in section:
        _fail("TOP must have #monthly-groups")
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
    if "function monthlyRowSlot" not in html:
        _fail("index.html missing monthlyRowSlot")
    if "function monthlyRowSegment" not in html:
        _fail("index.html missing monthlyRowSegment")
    if "function monthlyRowScopeRank" not in html:
        _fail("index.html missing monthlyRowScopeRank")
    scope_fn = html.find("function monthlyRowScopeRank")
    scope_end = html.find("function isAllStoreSalesYoy")
    if scope_fn < 0 or scope_end <= scope_fn:
        _fail("monthlyRowScopeRank / isAllStoreSalesYoy bounds")
    scope_body = html[scope_fn:scope_end]
    compact = scope_body.replace(" ", "")
    if 'p.metric==="店舗数"' not in compact:
        _fail("SPA must not fold unlabeled 店舗数 into 全店")
    row_fn = html.find("function monthlyRowOrder")
    row_end = html.find("function escHtml")
    if row_fn < 0 or row_end <= row_fn:
        _fail("monthlyRowOrder / escHtml bounds")
    row_body = html[row_fn:row_end]
    if "fmtMonthlyElement(" not in row_body:
        _fail("monthlyRowOrder must key by fmtMonthlyElement display name")
    if "seen.has(name)" in row_body:
        _fail("monthlyRowOrder must not key by raw stored element name")
    if "function monthlySeriesChoices" not in html:
        _fail("index.html missing monthlySeriesChoices")
    choices_fn = html.find("function monthlySeriesChoices")
    choices_end = html.find("function pickDefaultMonthlySeries")
    if choices_fn < 0 or choices_end <= choices_fn:
        _fail("monthlySeriesChoices / pickDefaultMonthlySeries bounds")
    choices_body = html[choices_fn:choices_end]
    if "return MONTHLY_SERIES" in choices_body:
        _fail("select options must be the table's element names, not MONTHLY_SERIES")
    if "e.element" not in choices_body:
        _fail("choices must come from table elements")
    if "monthlyRowOrder(" not in choices_body:
        _fail("#monthly-series options must follow year-table row order")
    if "fmtMonthlyElement(" not in choices_body:
        _fail("#monthly-series ids must use fmtMonthlyElement")
    load_fn = html.find("function loadMonthlyTables")
    if load_fn < 0:
        _fail("loadMonthlyTables missing")
    load_body = html[load_fn:load_fn + 4000]
    if "monthlyRowOrder(monthlyYearTables)" not in load_body:
        _fail("year tables must share one union row order")
    if "monthlyDisplayYears" not in load_body:
        _fail("loadMonthlyTables must window to the last 3 calendar years")
    if "for(const e of rowOrder)" not in load_body:
        _fail("each year table must iterate the shared row order")
    if "Object.keys(vals).length" not in load_body:
        _fail("year table must skip rows with no values that year")
    if "byName[src.element]" in load_body:
        _fail("year table cells must map stored names through fmtMonthlyElement")
    if "fmtMonthlyElement(src.element" not in load_body:
        _fail("year table cells must look up rows by fmtMonthlyElement")
    pick_fn = html.find("function pickDefaultMonthlySeries")
    pick_end = html.find("function monthlySeriesElement")
    if pick_fn < 0 or pick_end <= pick_fn:
        _fail("pickDefaultMonthlySeries / monthlySeriesElement bounds")
    pick_body = html[pick_fn:pick_end]
    if "isAllStoreSalesYoy" not in pick_body:
        _fail("default series must prefer 全店/全社 売上 yoy")
    if "all_store" not in pick_body:
        _fail("default series must still fall back to all_store")
    if "function monthlySeriesElement" not in html:
        _fail("chart must resolve the selected row by element name")
    series_el_fn = html.find("function monthlySeriesElement")
    series_el_end = html.find("function fmtMonthlyYoyList")
    if series_el_fn < 0 or series_el_end <= series_el_fn:
        _fail("monthlySeriesElement / fmtMonthlyYoyList bounds")
    series_el_body = html[series_el_fn:series_el_end]
    if "fmtMonthlyElement(" not in series_el_body:
        _fail("chart row lookup must match leftover short names via fmtMonthlyElement")
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


def _assert_known_prod_units(c) -> None:
    """Gift / Intermestic がテストDBにあれば unit を付ける。値は変換しない。無ければ skip。"""
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
        if "1" in vals or 1 in vals:
            raw = float(vals.get("1", vals.get(1)))
            if raw != -7.1:
                _fail(f"262A0 value converted: {vals!r}")


if __name__ == "__main__":
    main()

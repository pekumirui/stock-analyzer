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

from app.main import app  # noqa: E402

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
    c = TestClient(app)
    code, year = "7203", "2024"
    base = f"/api/v1/tables/{code}/{year}"

    # TABLE_NOT_FOUND on GET missing table
    r = c.get(base)
    _expect(r, 404, "GET missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"GET missing table code: {r.json()!r}")

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
    if r.json() != {"code": code, "year": 2024, "element": "売上高 前年比", "created": True}:
        _fail(f"create: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET after auto-create")
    if r.json()["code"] != code or r.json()["year"] != 2024:
        _fail(f"get table: {r.json()!r}")
    if [e["element"] for e in r.json()["elements"]] != ["売上高 前年比"]:
        _fail(f"elements after create: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years after create")
    if r.json() != {"code": code, "years": [2024]}:
        _fail(f"years: {r.json()!r}")

    # idempotent element create
    r = c.post(f"{base}/elements", json={"element": "売上高 前年比"})
    _expect(r, 200, "idempotent exact")
    if r.json() != {"code": code, "year": 2024, "element": "売上高 前年比", "created": False}:
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
    if r.json() != {"code": code, "year": 2024, "element": "売上高前年比", "created": True}:
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

    # 5桁コードも可
    r = c.post("/api/v1/tables/72030/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 201, "5-digit code")
    r = c.get("/api/v1/tables/72030")
    if r.json() != {"code": "72030", "years": [2024]}:
        _fail(f"5-digit years: {r.json()!r}")

    print("ok: monthly tables api")


if __name__ == "__main__":
    main()

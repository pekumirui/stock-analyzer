"""monthly_issuers: 月次業種・新着・系列マップ。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-issuers-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import MONTHLY_NEW_DAYS  # noqa: E402
from app.main import app  # noqa: E402
from app.monthly_ingest import monthly_series_kind, yoy_display_number  # noqa: E402


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


def _ensure_stock(code: str, name: str | None = None, market: str | None = None,
                  sector33: str | None = None) -> None:
    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        row = db.get(Stock, code)
        if row is None:
            db.add(Stock(code=code, name=name, market=market, sector33=sector33))
        else:
            if name is not None:
                row.name = name
            if market is not None:
                row.market = market
            if sector33 is not None:
                row.sector33 = sector33
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


def test_series_kind() -> None:
    if monthly_series_kind("全店売上高(前年比)", "yoy_index") != "all_store":
        _fail("all_store")
    if monthly_series_kind("全社 売上高(前年比)", "yoy_index") != "all_store":
        _fail("全社 is all_store")
    if monthly_series_kind("全店舗 売上高(前年比)", "yoy_index") != "all_store":
        _fail("全店舗 is all_store")
    if monthly_series_kind("既存店売上高(前年比)", "yoy_pct") != "existing":
        _fail("existing")
    if monthly_series_kind("全店既存店合算(前年比)", "yoy_index") != "existing":
        _fail("全店+既存 is existing, not all_store")
    if monthly_series_kind("店舗数", "number") != "stores":
        _fail("stores")
    if monthly_series_kind("純増 店舗数", "number") is not None:
        _fail("純増 店舗数 is not stores")
    if monthly_series_kind("新設店舗", "number") is not None:
        _fail("新設 is not stores")
    if monthly_series_kind("出店店舗数", "number") is not None:
        _fail("出店店舗数 is not stores")
    if monthly_series_kind("100円ショップ 閉鎖 店舗数", "number") is not None:
        _fail("閉鎖 店舗数 is not stores")
    if monthly_series_kind("売上高(前年比)", "yoy_index") is not None:
        _fail("generic yoy is not mapped")
    if yoy_display_number(-7.1, "yoy_pct") != 92.9:
        _fail("yoy_pct display")
    if yoy_display_number(125.8, "yoy_index") != 125.8:
        _fail("yoy_index display")


def test_put_and_list() -> None:
    c = TestClient(app)
    _ensure_stock("72030", "トヨタ自動車", "東証P", "輸送用機器")
    r = c.put("/api/v1/tables/72030/industry", json={"industry": "アパレル"})
    _expect(r, 404, "PUT industry without table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"no table: {r.json()!r}")

    r = c.post("/api/v1/tables/72030/2026/elements", json={
        "element": "全店売上高", "unit": "yoy_index",
    })
    _expect(r, 201, "seed all_store")
    r = c.put("/api/v1/tables/72030/2026/values", json={
        "element": "全店売上高(前年比)", "month": 11, "value": 125.8,
    })
    _expect(r, 200, "seed all_store value")
    r = c.post("/api/v1/tables/72030/2026/elements", json={
        "element": "既存店売上高", "unit": "yoy_pct",
    })
    _expect(r, 201, "seed existing")
    r = c.put("/api/v1/tables/72030/2026/values", json={
        "element": "既存店売上高(前年比)", "month": 11, "value": -7.1,
    })
    _expect(r, 200, "seed existing value")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET after elements")
    row = r.json()[0]
    if row["industry"] != "その他":
        _fail(f"default industry: {row!r}")
    if row["market"] != "東証P" or row["sector33"] != "輸送用機器":
        _fail(f"stock fields: {row!r}")
    if row["all_store_yoy"] != 125.8:
        _fail(f"all_store_yoy: {row!r}")
    if row["existing_store_yoy"] != 92.9:
        _fail(f"existing_store_yoy must be 100-flat: {row!r}")
    if row["is_new"] is not False:
        _fail(f"POST elements is not ingest: {row!r}")

    r = c.put("/api/v1/tables/72030/industry", json={"industry": "アパレル"})
    _expect(r, 200, "PUT industry")
    if r.json() != {"code": "72030", "industry": "アパレル"}:
        _fail(f"PUT body: {r.json()!r}")
    r = c.get("/api/v1/tables")
    if r.json()[0]["industry"] != "アパレル":
        _fail(f"GET after PUT: {r.json()!r}")

    r = c.put("/api/v1/tables/30320/industry", json={"industry": "アパレル"})
    _expect(r, 404, "PUT missing stock")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"missing stock: {r.json()!r}")


def test_ingest_timestamp() -> None:
    c = TestClient(app)
    _add_fy_end("262A0", date(2026, 5, 8), date(2026, 3, 31), "iss-fy-1")
    r = c.post("/api/v1/tables/262A0/ingest", json={
        "months": [4],
        "rows": [{"element": "全店売上高", "unit": "yoy_index", "values": {"4": 101.0}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest")
    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET after ingest")
    by = {x["code"]: x for x in r.json()}
    row = by["262A0"]
    if not row["last_ingested_at"]:
        _fail(f"last_ingested_at: {row!r}")
    if row["is_new"] is not True or row["ingested_today"] is not True:
        _fail(f"today ingest: {row!r}")
    if row["industry"] != "その他":
        _fail(f"ingest must not wipe industry default: {row!r}")

    r = c.put("/api/v1/tables/262A0/industry", json={"industry": "アパレル"})
    _expect(r, 200, "PUT after ingest")
    r = c.post("/api/v1/tables/262A0/ingest", json={
        "months": [5],
        "rows": [{"element": "全店売上高", "unit": "yoy_index", "values": {"5": 102.0}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "second ingest")
    r = c.get("/api/v1/tables")
    row = {x["code"]: x for x in r.json()}["262A0"]
    if row["industry"] != "アパレル":
        _fail(f"second ingest must keep industry: {row!r}")


def test_new_window() -> None:
    from app.db import SessionLocal
    from app.models import MonthlyIssuer

    c = TestClient(app)
    _ensure_stock("92790", "ギフト")
    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "seed stores")
    old = datetime.now() - timedelta(days=MONTHLY_NEW_DAYS + 1)
    db = SessionLocal()
    try:
        row = db.get(MonthlyIssuer, "92790")
        if row is None:
            db.add(MonthlyIssuer(code="92790", industry="その他", last_ingested_at=old))
        else:
            row.last_ingested_at = old
        db.commit()
    finally:
        db.close()
    r = c.get("/api/v1/tables")
    by = {x["code"]: x for x in r.json()}
    row = by["92790"]
    if row["is_new"] is not False or row["ingested_today"] is not False:
        _fail(f"eight days ago is not 新着: {row!r}")


def test_year_payload_unchanged() -> None:
    c = TestClient(app)
    r = c.get("/api/v1/tables/72030/2026")
    _expect(r, 200, "GET year")
    body = r.json()
    if set(body) != {"code", "year", "elements"}:
        _fail(f"year keys: {body!r}")


def main() -> None:
    test_series_kind()
    test_put_and_list()
    test_ingest_timestamp()
    test_new_window()
    test_year_payload_unchanged()
    print("ok: monthly issuers")


if __name__ == "__main__":
    main()

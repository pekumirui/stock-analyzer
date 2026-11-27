"""月次は stocks にある code だけ。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-master-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient

from app.main import app


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


def _add_orphan_monthly(code: str, year: int = 2024) -> None:
    from datetime import datetime

    from app.db import SessionLocal
    from app.models import MonthlyElement, MonthlyIssuer, MonthlyPublication, MonthlyTable, MonthlyValue

    db = SessionLocal()
    try:
        if db.get(MonthlyTable, (code, year)) is None:
            db.add(MonthlyTable(code=code, year=year))
        if db.get(MonthlyElement, (code, year, "売上高")) is None:
            db.add(MonthlyElement(
                code=code, year=year, element="売上高", sort_order=1, unit="number",
            ))
        if db.get(MonthlyValue, (code, year, "売上高", 1)) is None:
            db.add(MonthlyValue(
                code=code, year=year, element="売上高", month=1, value=1.0,
            ))
        if db.get(MonthlyPublication, (code, year, 1)) is None:
            db.add(MonthlyPublication(
                code=code, year=year, month=1, basis="unknown", unknown_reason="no_source",
                confidence="unknown", recorded_at=datetime(2024, 1, 2, 3, 4, 5),
            ))
        if db.get(MonthlyIssuer, code) is None:
            db.add(MonthlyIssuer(code=code, industry="その他"))
        db.commit()
    finally:
        db.close()


def test_ingest_without_master() -> None:
    c = TestClient(app)
    r = c.post("/api/v1/tables/30320/ingest", json={
        "months": [11],
        "rows": [{"element": "売上高", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 404, "ingest without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"ingest without master: {r.json()!r}")
    r = c.get("/api/v1/tables/30320/2024")
    _expect(r, 404, "no table after rejected ingest")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"rejected ingest must not create table: {r.json()!r}")


def test_ingest_with_master() -> None:
    c = TestClient(app)
    _add_fy_end("72030", date(2026, 5, 8), date(2026, 3, 31), "master-fy-1")
    r = c.post("/api/v1/tables/72030/ingest", json={
        "months": [4, 5],
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 10.0, "5": 11.0}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest with master")
    if r.json().get("code") != "72030":
        _fail(f"ingest with master body: {r.json()!r}")
    r = c.get("/api/v1/tables/72030/2026")
    _expect(r, 200, "GET after ingest with master")
    els = r.json().get("elements") or []
    if not els or els[0].get("element") != "売上高":
        _fail(f"ingest with master saved: {r.json()!r}")


def test_post_elements_without_and_with_master() -> None:
    c = TestClient(app)
    r = c.post("/api/v1/tables/30660/2024/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 404, "POST elements without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"POST without master: {r.json()!r}")

    _ensure_stock("262A0", "インターメスティック")
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 201, "POST elements with master")
    if r.json().get("code") != "262A0" or r.json().get("created") is not True:
        _fail(f"POST with master: {r.json()!r}")


def test_put_rejects_when_master_missing() -> None:
    c = TestClient(app)
    _add_orphan_monthly("30320", 2024)
    r = c.put("/api/v1/tables/30320/2024/values",
              json={"element": "売上高", "month": 1, "value": 2.0})
    _expect(r, 404, "PUT values without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"PUT values: {r.json()!r}")
    r = c.put("/api/v1/tables/30320/2024/values/batch", json={
        "element": "売上高",
        "values": [{"month": 1, "value": 3.0}],
    })
    _expect(r, 404, "PUT batch without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"PUT batch: {r.json()!r}")
    r = c.put("/api/v1/tables/30320/2024/elements",
              json={"element": "売上高", "unit": "number"})
    _expect(r, 404, "PUT unit without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"PUT unit: {r.json()!r}")


def test_orphan_delete() -> None:
    from app.db import SessionLocal, init_db
    from app.monthly.maintenance import delete_orphan_monthly_rows, run_deploy_maintenance
    from app.models import (
        MonthlyElement, MonthlyIssuer, MonthlyPublication, MonthlyTable, MonthlyValue, Stock,
    )

    _ensure_stock("72030")
    _add_orphan_monthly("72030", 2025)
    _add_orphan_monthly("30320", 2024)
    _add_orphan_monthly("30660", 2024)

    deleted = delete_orphan_monthly_rows()
    if deleted.get("monthly_publications") != 2:
        _fail(f"orphan publications: {deleted!r}")
    if deleted.get("monthly_values") != 2:
        _fail(f"orphan values: {deleted!r}")
    if deleted.get("monthly_elements") != 2:
        _fail(f"orphan elements: {deleted!r}")
    if deleted.get("monthly_tables") != 2:
        _fail(f"orphan tables: {deleted!r}")
    if deleted.get("monthly_issuers") != 2:
        _fail(f"orphan issuers: {deleted!r}")

    db = SessionLocal()
    try:
        if db.get(Stock, "72030") is None:
            _fail("master 72030 must remain")
        if db.get(MonthlyTable, ("72030", 2025)) is None:
            _fail("72030 monthly must remain")
        if db.get(MonthlyElement, ("72030", 2025, "売上高")) is None:
            _fail("72030 element must remain")
        if db.get(MonthlyValue, ("72030", 2025, "売上高", 1)) is None:
            _fail("72030 value must remain")
        if db.get(MonthlyPublication, ("72030", 2025, 1)) is None:
            _fail("72030 publication must remain")
        if db.get(MonthlyIssuer, "72030") is None:
            _fail("72030 issuer must remain")
        for code in ("30320", "30660"):
            if db.get(MonthlyTable, (code, 2024)) is not None:
                _fail(f"{code} table should be gone")
            if db.get(MonthlyElement, (code, 2024, "売上高")) is not None:
                _fail(f"{code} element should be gone")
            if db.get(MonthlyValue, (code, 2024, "売上高", 1)) is not None:
                _fail(f"{code} value should be gone")
            if db.get(MonthlyIssuer, code) is not None:
                _fail(f"{code} issuer should be gone")
            if db.get(MonthlyPublication, (code, 2024, 1)) is not None:
                _fail(f"{code} publication should be gone")
    finally:
        db.close()

    again = delete_orphan_monthly_rows()
    if any(again.get(k, 0) for k in (
        "monthly_publications", "monthly_values", "monthly_elements",
        "monthly_tables", "monthly_issuers",
    )):
        _fail(f"orphan delete must be idempotent: {again!r}")

    # init_db はスキーマだけ。孤立行はデプロイごとの jobs/migrate.py（run_deploy_maintenance）が消す
    _add_orphan_monthly("30320", 2024)
    init_db()
    db = SessionLocal()
    try:
        if db.get(MonthlyTable, ("30320", 2024)) is None:
            _fail("init_db must not touch monthly rows")
    finally:
        db.close()
    run_deploy_maintenance(verbose=False)
    db = SessionLocal()
    try:
        if db.get(MonthlyTable, ("30320", 2024)) is not None:
            _fail("run_deploy_maintenance must delete 30320-like orphans")
        if db.get(MonthlyTable, ("72030", 2025)) is None:
            _fail("run_deploy_maintenance must keep rows with stocks")
    finally:
        db.close()


def main() -> None:
    test_ingest_without_master()
    test_ingest_with_master()
    test_post_elements_without_and_with_master()
    test_put_rejects_when_master_missing()
    test_orphan_delete()
    print("ok: monthly require stock master")


if __name__ == "__main__":
    main()

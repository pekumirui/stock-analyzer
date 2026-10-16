"""送られてきたままの行名 (monthly_element_sources) の記録。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-sources-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import MonthlyElement, MonthlyElementSource, Statement, Stock  # noqa: E402
from app.monthly.maintenance import backfill_legacy_element_sources  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _setup(code: str, fy_end: date | None = None) -> None:
    db = SessionLocal()
    try:
        if db.get(Stock, code) is None:
            db.add(Stock(code=code, name=f"test {code}"))
        if fy_end is not None:
            db.add(Statement(disclosure_no=f"src-{code}", code=code,
                             disclosed_date=date(2026, 4, 10), fiscal_year_end=fy_end))
        db.commit()
    finally:
        db.close()


def _sources(code: str) -> dict[tuple[str, str], tuple[int, str, str]]:
    db = SessionLocal()
    try:
        return {
            (r.source_label, r.unit): (r.year, r.element, r.via)
            for r in db.query(MonthlyElementSource).filter_by(code=code)
        }
    finally:
        db.close()


def test_post_element_records_raw_label(c: TestClient) -> None:
    code = "69810"
    _setup(code)
    r = c.post(f"/api/v1/tables/{code}/2026/elements",
               json={"element": "  売上高前年比 ", "unit": "yoy_pct"})
    _expect(r, 201, "POST elements")
    stored = r.json()["element"]
    got = _sources(code)
    # 前後の空白だけ落とした形と、送られた unit（保存形にする前）で残す
    if got != {("売上高前年比", "yoy_pct"): (2026, stored, "api")}:
        _fail(f"POST source: {got!r}")
    db = SessionLocal()
    try:
        first = db.get(MonthlyElementSource, (code, 2026, "売上高前年比", "yoy_pct"))
        first_seen = first.first_seen_at
    finally:
        db.close()
    r = c.post(f"/api/v1/tables/{code}/2026/elements",
               json={"element": "売上高前年比", "unit": "yoy_pct"})
    _expect(r, 200, "POST elements again")
    db = SessionLocal()
    try:
        again = db.get(MonthlyElementSource, (code, 2026, "売上高前年比", "yoy_pct"))
        if again.first_seen_at != first_seen or again.last_seen_at < first_seen:
            _fail("re-POST must keep first_seen_at and move last_seen_at")
        if db.query(MonthlyElementSource).filter_by(code=code).count() != 1:
            _fail("re-POST must not add a second source row")
    finally:
        db.close()


def test_ingest_records_every_raw_label(c: TestClient) -> None:
    """表1と表2で同じ保存名に寄る行は、元の名前を両方残す。dry_run は書かない。"""
    code = "30860"
    _setup(code, date(2026, 2, 28))
    rows = [
        {"element": "百貨店事業 総額 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "百貨店事業合計 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "大丸 心斎橋店 売上高", "unit": "yoy_pct", "values": [2.1]},
    ]
    body = {"months": [8], "as_of": "2026-09-15", "rows": rows, "via": "ir_html"}
    r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "dry_run": True})
    _expect(r, 200, "ingest dry_run")
    if _sources(code):
        _fail("dry_run must not record sources")
    for bad in ("nope", ["ir_html"], 1):
        r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "via": bad})
        _expect(r, 400, f"ingest bad via {bad!r}")
    r = c.post(f"/api/v1/tables/{code}/ingest", json=body)
    _expect(r, 200, "ingest")
    names = {u["element"] for u in r.json()["updated"]}
    got = _sources(code)
    if set(got) != {(row["element"], "yoy_pct") for row in rows}:
        _fail(f"ingest sources: {got!r}")
    if {v[1] for v in got.values()} != names:
        _fail(f"sources must point at stored names {names!r}: {got!r}")
    if {v[2] for v in got.values()} != {"ir_html"}:
        _fail(f"ingest via: {got!r}")
    # 合計の行は総額の行に寄る（同じ保存名を指す）
    if got[("百貨店事業 総額 売上高", "yoy_pct")][1] != got[("百貨店事業合計 売上高", "yoy_pct")][1]:
        _fail(f"merged rows must share the stored name: {got!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={k: v for k, v in body.items() if k != "via"})
    _expect(r, 200, "ingest without via")
    if {v[2] for v in _sources(code).values()} != {"ir_html"}:
        _fail("re-ingest must keep the first via")


def test_rename_and_delete_follow_stored_name(c: TestClient) -> None:
    """付け替えで element は付いていき、source_label は変わらない。行を消すと一緒に消える。"""
    code = "99830"
    _setup(code)
    r = c.post(f"/api/v1/tables/{code}/2026/elements",
               json={"element": "既存店 売上高", "unit": "number"})
    _expect(r, 201, "POST number row")
    old = r.json()["element"]
    r = c.put(f"/api/v1/tables/{code}/2026/values", json={"element": old, "month": 1, "value": 1.0})
    _expect(r, 200, "PUT value")

    r = c.get(f"/api/v1/tables/{code}/2026/doc")
    _expect(r, 200, "GET doc")
    doc = r.json()
    elements = [{"element": "既存店 売上高（手直し）", "was": old},
                {"element": "新しい行", "unit": "number"}]
    r = c.put(f"/api/v1/tables/{code}/2026/doc",
              json={"version": doc["version"], "elements": elements,
                    "allow_structure_change": True})
    _expect(r, 200, "PUT doc rename + create")
    got = _sources(code)
    want = {
        ("既存店 売上高", "number"): (2026, "既存店 売上高（手直し）", "api"),
        ("新しい行", "number"): (2026, "新しい行", "doc"),
    }
    if got != want:
        _fail(f"doc rename/create sources: {got!r}")

    r = c.request("DELETE", f"/api/v1/tables/{code}/2026/elements", json={"element": "新しい行"})
    _expect(r, 200, "DELETE empty row")
    if ("新しい行", "number") in _sources(code):
        _fail(f"delete must drop sources: {_sources(code)!r}")


def test_legacy_backfill_is_idempotent() -> None:
    code = "13010"
    _setup(code)
    db = SessionLocal()
    try:
        from app.models import MonthlyTable

        db.add(MonthlyTable(code=code, year=2025))
        db.add(MonthlyElement(code=code, year=2025, element="全店 売上高(前年比)",
                              sort_order=1, unit="yoy_index"))
        db.commit()
    finally:
        db.close()
    first = backfill_legacy_element_sources(verbose=False)
    if first < 1:
        _fail(f"legacy backfill added {first}")
    if _sources(code) != {("全店 売上高(前年比)", "yoy_index"): (2025, "全店 売上高(前年比)", "legacy")}:
        _fail(f"legacy source: {_sources(code)!r}")
    if backfill_legacy_element_sources(verbose=False) != 0:
        _fail("legacy backfill must be idempotent")
    # 既に api の記録がある行には legacy を足さない
    if any(v[2] == "legacy" for v in _sources("69810").values()):
        _fail("rows with a recorded source must not get a legacy row")


def test_sync_copies_sources_when_present() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_monthly_from_vps", ROOT / ".cursor" / "sync_monthly_from_vps.py",
    )
    assert spec and spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)

    src = Path(_tmp) / "stock.db"
    work = Path(tempfile.mkdtemp(prefix="stock-analyzer-sources-sync-"))
    slice_path = work / "slice.sqlite"
    counts = sync.dump_monthly_slice(src, slice_path)
    if counts.get("monthly_element_sources", 0) < 1:
        _fail(f"dump must copy sources: {counts!r}")

    # 手元に表があって本番に無いとき: 手元の古い分は消える
    dest = work / "dest.db"
    con = sqlite3.connect(dest)
    for table in (*sync.MONTHLY_TABLES, "stocks", "monthly_element_sources"):
        con.execute(sync._table_sql(sqlite3.connect(src), table))
    con.execute("INSERT INTO monthly_element_sources VALUES "
                "('00000', 2020, 'stale', 'number', 'stale', 'api', '2020-01-01', '2020-01-01')")
    con.commit()
    con.close()
    old = work / "old_slice.sqlite"
    s = sqlite3.connect(old)
    srcc = sqlite3.connect(src)
    for table in (*sync.MONTHLY_TABLES, "stocks"):
        s.execute(sync._table_sql(srcc, table))
    s.commit()
    s.close()
    sync.replace_monthly(dest, old)
    con = sqlite3.connect(dest)
    if con.execute("SELECT COUNT(*) FROM monthly_element_sources").fetchone()[0] != 0:
        _fail("replace must clear local sources when the slice has none")
    con.close()

    got = sync.replace_monthly(dest, slice_path)
    if got.get("monthly_element_sources") != counts["monthly_element_sources"]:
        _fail(f"replace must copy sources: {got!r}")


def main() -> None:
    init_db()
    c = TestClient(app)
    test_post_element_records_raw_label(c)
    test_ingest_records_every_raw_label(c)
    test_rename_and_delete_follow_stored_name(c)
    test_legacy_backfill_is_idempotent()
    test_sync_copies_sources_when_present()
    print("ok: monthly element sources")


if __name__ == "__main__":
    main()

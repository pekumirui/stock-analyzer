"""monthly_issuers: 月次業種・新着・系列マップ。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-issuers-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import MONTHLY_NEW_DAYS  # noqa: E402
from app.main import app  # noqa: E402
from app.monthly.ingest import monthly_series_kind  # noqa: E402


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
    if monthly_series_kind("売上高(前年比)", "yoy_index") != "all_store":
        _fail("unlabeled yoy 売上高 is all_store")
    if monthly_series_kind("客数(前年比)", "yoy_index") != "all_store":
        _fail("unlabeled yoy 客数 is all_store")
    if monthly_series_kind("客単価(前年比)", "yoy_index") != "all_store":
        _fail("unlabeled yoy 客単価 is all_store")
    if monthly_series_kind("取扱高(前年比)", "yoy_index") is not None:
        _fail("取扱高 yoy is not a series")
    if monthly_series_kind("客室収入(前年比)", "yoy_index") is not None:
        _fail("客室収入 yoy is not a series")
    if monthly_series_kind("ホテル 客室収入(前年比)", "yoy_index") is not None:
        _fail("ホテル 客室収入 yoy is not a series")
    if monthly_series_kind("全店 売上高 ドラッグ(前年比)", "yoy_index") is not None:
        _fail("売上高 内訳 is not all_store")
    if monthly_series_kind("MKG 全店 売上高 調剤(前年比)", "yoy_index") is not None:
        _fail("brand 内訳 is not a series")
    if monthly_series_kind("会員数 既存(前年比)", "yoy_index") is not None:
        _fail("会員数 既存 yoy is not existing")
    if monthly_series_kind("会員数(前年比)", "yoy_index") is not None:
        _fail("会員数 yoy is not a series")
    if monthly_series_kind("会員数 既存 売上高(前年比)", "yoy_index") is not None:
        _fail("old 会員数 既存 売上高 yoy is not existing")
    if monthly_series_kind("客数 既存(前年比)", "yoy_index") != "existing":
        _fail("客数 既存 yoy stays existing")


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
    if r.json().get("unit") != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.put("/api/v1/tables/72030/2026/values", json={
        "element": "既存店売上高(前年比)", "month": 11, "value": 92.9,
    })
    _expect(r, 200, "seed existing stored yoy_index 92.9")

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


def _set_ingested_at(code: str, when: datetime) -> None:
    from app.db import SessionLocal
    from app.models import MonthlyIssuer

    db = SessionLocal()
    try:
        row = db.get(MonthlyIssuer, code)
        if row is None:
            _fail(f"{code} has no monthly_issuers row")
        row.last_ingested_at = when
        db.commit()
    finally:
        db.close()


def _issuer_row(c: TestClient, code: str) -> dict:
    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables")
    return {x["code"]: x for x in r.json()}[code]


def test_ingest_touch_only_on_change() -> None:
    from app.db import SessionLocal
    from app.models import MonthlyIssuer

    c = TestClient(app)
    code = "33910"
    _add_fy_end(code, date(2026, 5, 8), date(2026, 3, 31), "iss-fy-touch")

    def post(values: dict | None) -> None:
        rows = [] if values is None else [
            {"element": "全店売上高", "unit": "yoy_index", "values": values},
        ]
        r = c.post(f"/api/v1/tables/{code}/ingest", json={
            "months": [4, 5], "rows": rows, "as_of": "2026-08-05",
        })
        _expect(r, 200, f"ingest {values!r}")

    post(None)
    db = SessionLocal()
    try:
        if db.get(MonthlyIssuer, code) is not None:
            _fail("rows=[] probe must not create monthly_issuers")
    finally:
        db.close()

    post({"4": 101.0})
    if _issuer_row(c, code)["ingested_today"] is not True:
        _fail("first values must be 新着")

    old = datetime.now() - timedelta(days=MONTHLY_NEW_DAYS + 1)
    _set_ingested_at(code, old)
    post({"4": 101.0})
    post(None)
    row = _issuer_row(c, code)
    if row["is_new"] is not False:
        _fail(f"same values / probe must not be 新着: {row!r}")

    post({"4": 101.0, "5": 99.5})
    if _issuer_row(c, code)["ingested_today"] is not True:
        _fail("new month must be 新着")

    _set_ingested_at(code, old)
    post({"5": 98.0})
    if _issuer_row(c, code)["ingested_today"] is not True:
        _fail("changed value must be 新着")

    _set_ingested_at(code, old)
    post({"5": None})
    if _issuer_row(c, code)["ingested_today"] is not True:
        _fail("deleted cell must be 新着")


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
    # tree / tree_metrics / tree_auto は全発行体に付く（config の木が無ければ自動の木。#434）
    if set(body) != {"code", "year", "elements", "tree", "tree_metrics", "tree_metric_parent", "tree_auto"}:
        _fail(f"year keys: {body!r}")


def _seed_yoy_rows(
    code: str, name: str, year: int, month: int,
    rows: list[tuple[str, int, float]],
) -> None:
    """rows: (element, sort_order, value). sort_order を明示するため DB 直書き。"""
    from app.db import SessionLocal
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    _ensure_stock(code, name)
    db = SessionLocal()
    try:
        if db.get(MonthlyTable, (code, year)) is None:
            db.add(MonthlyTable(code=code, year=year))
        for element, sort_order, value in rows:
            db.add(MonthlyElement(
                code=code, year=year, element=element,
                sort_order=sort_order, unit="yoy_index",
            ))
            db.add(MonthlyValue(
                code=code, year=year, element=element, month=month, value=value,
            ))
        db.commit()
    finally:
        db.close()


def test_headline_yoy_prefers_company_wide() -> None:
    """一覧はセグメントなしの売上高を優先。同条件なら sort_order。#336"""
    c = TestClient(app)

    # (a) シュッピン相当: 同じ月の EC より全社の全店売上高
    _seed_yoy_rows("31790", "シュッピン", 2026, 8, [
        ("EC 全店 売上高(前年比)", 1, 85.9),
        ("全店 売上高(前年比)", 2, 94.5),
    ])
    row = _issuer_row(c, "31790")
    if row["all_store_yoy"] != 94.5:
        _fail(f"(a) company-wide all_store: {row!r}")

    # (b) TSI 相当: セグメント行の sort_order が先でも全社を選ぶ
    _seed_yoy_rows("36080", "TSIホールディングス", 2026, 8, [
        ("小売店 全店 売上高(前年比)", 1, 210.9),
        ("全店 売上高(前年比)", 2, 143.1),
    ])
    row = _issuer_row(c, "36080")
    if row["all_store_yoy"] != 143.1:
        _fail(f"(b) sort_order must not beat company-wide: {row!r}")

    # (c) 大黒天相当: 一品 客単価より全店売上高
    _seed_yoy_rows("27910", "大黒天物産", 2026, 8, [
        ("一品 全店 客単価(前年比)", 1, 100.5),
        ("全店 売上高(前年比)", 2, 108.9),
    ])
    row = _issuer_row(c, "27910")
    if row["all_store_yoy"] != 108.9:
        _fail(f"(c) sales over 客単価: {row!r}")

    # (d) セグメントなしが無いときは sort_order 最小（文字コード順に頼らない）
    _seed_yoy_rows("141A0", "トライアル", 2026, 8, [
        ("EC 全店 売上高(前年比)", 2, 85.9),
        ("オンラインショップ 全店 売上高(前年比)", 1, 210.9),
    ])
    row = _issuer_row(c, "141A0")
    if row["all_store_yoy"] != 210.9:
        _fail(f"(d) segmented min sort_order: {row!r}")

    # (e) 既存店欄も同じ。サンドラッグ相当
    _seed_yoy_rows("99890", "サンドラッグ", 2026, 8, [
        ("ディスカウントストア事業 既存 売上高(前年比)", 1, 102.6),
        ("既存 売上高(前年比)", 2, 100.3),
        ("ディスカウントストア事業 全店 売上高(前年比)", 3, 105.3),
        ("全店 売上高(前年比)", 4, 102.7),
    ])
    row = _issuer_row(c, "99890")
    if row["all_store_yoy"] != 102.7:
        _fail(f"(e) existing-col all_store: {row!r}")
    if row["existing_store_yoy"] != 100.3:
        _fail(f"(e) existing-col existing: {row!r}")


def test_member_count_yoy_not_headline() -> None:
    """212A: 会員数の前年比は existing_store_yoy に混ざらない。#366"""
    c = TestClient(app)
    _add_fy_end("212A0", date(2026, 5, 8), date(2026, 3, 31), "iss-212a-fy")
    r = c.post("/api/v1/tables/212A0/ingest", json={
        "months": [9],
        "rows": [
            {"element": "会員数 既存(前年比)", "unit": "yoy_index", "values": {"9": 109.0}},
            {"element": "会員数(前年比)", "unit": "yoy_index", "values": {"9": 108.0}},
        ],
        "as_of": "2026-09-30",
    })
    _expect(r, 200, "ingest 会員数")
    updated = r.json().get("updated")
    if not isinstance(updated, list) or not updated:
        _fail(f"ingest updated: {r.json()!r}")
    stored = {u["element"] for u in updated}
    if "会員数 既存 売上高(前年比)" in stored:
        _fail(f"stored 会員数 must not become 売上高: {stored!r}")
    if stored != {"会員数 既存(前年比)", "会員数(前年比)"}:
        _fail(f"stored 会員数 names: {stored!r}")
    row = _issuer_row(c, "212A0")
    if row["existing_store_yoy"] is not None:
        _fail(f"会員数 must not fill existing_store_yoy: {row!r}")
    if row["all_store_yoy"] is not None:
        _fail(f"会員数 must not fill all_store_yoy: {row!r}")
    year = updated[0]["year"]
    r = c.get(f"/api/v1/tables/212A0/{year}")
    _expect(r, 200, "GET 212A0 year")
    by = {el["element"]: el for el in r.json()["elements"]}
    existing = by["会員数 既存(前年比)"]["label"]
    if existing["metric_kind"] == "sales" or existing["series_kind"] is not None:
        _fail(f"会員数 既存 label: {existing!r}")
    bare = by["会員数(前年比)"]["label"]
    if bare["metric_kind"] == "sales" or bare["series_kind"] is not None:
        _fail(f"会員数 label: {bare!r}")


def test_year_label_block() -> None:
    from app.db import SessionLocal
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    c = TestClient(app)
    code, year = "33820", 2026
    leftover_name = "売上高"
    mkg_name = "MKG 全店 売上高 ドラッグ(前年比)"
    _ensure_stock(code, "クスリのアオキ")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(
            code=code, year=year, element=leftover_name, sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyElement(
            code=code, year=year, element=mkg_name, sort_order=2, unit="yoy_index",
        ))
        db.add(MonthlyValue(code=code, year=year, element=leftover_name, month=1, value=101.0))
        db.add(MonthlyValue(code=code, year=year, element=mkg_name, month=1, value=103.2))
        db.commit()
    finally:
        db.close()

    r = c.get(f"/api/v1/tables/{code}/{year}")
    _expect(r, 200, "GET leftover label")
    body = r.json()
    if set(body) != {"code", "year", "elements", "tree", "tree_metrics", "tree_metric_parent", "tree_auto"}:
        _fail(f"year keys: {body!r}")
    els = body["elements"]
    if [e["element"] for e in els] != [leftover_name, mkg_name]:
        _fail(f"GET order must follow sort_order: {[e['element'] for e in els]!r}")
    for e in els:
        if set(e) != {"element", "unit", "label", "values", "tree"}:
            _fail(f"element keys: {e!r}")
    leftover = els[0]
    if leftover["element"] != leftover_name or leftover["unit"] != "yoy_index":
        _fail(f"leftover stored row rewritten: {leftover!r}")
    want_left = {
        "display": "売上高(前年比)",
        "group": "",
        "metric_kind": "sales",
        "series_kind": "all_store",
        "country": "",
        "segment": "",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "売上高",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if leftover["label"] != want_left:
        _fail(f"leftover 売上高 label: {leftover['label']!r}")
    want_mkg = {
        "display": "MKG 全店 売上高 ドラッグ(前年比)",
        "group": "MKG",
        "metric_kind": "sales",
        "series_kind": None,
        "country": "",
        "segment": "MKG",
        "time_axis": "",
        "channel": "",
        "scope": "全店",
        "metric": "売上高",
        "breakdown": "ドラッグ",
        "breakdown_depth": 1,
    }
    if els[1]["label"] != want_mkg:
        _fail(f"MKG label: {els[1]['label']!r}")
    db = SessionLocal()
    try:
        if db.get(MonthlyElement, (code, year, leftover_name)) is None:
            _fail("leftover 売上高 must stay in DB")
    finally:
        db.close()


def main() -> None:
    test_series_kind()
    test_put_and_list()
    test_ingest_timestamp()
    test_ingest_touch_only_on_change()
    test_new_window()
    test_year_payload_unchanged()
    test_year_label_block()
    test_headline_yoy_prefers_company_wide()
    test_member_count_yoy_not_headline()
    print("ok: monthly issuers")


if __name__ == "__main__":
    main()

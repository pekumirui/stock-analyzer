"""月次一覧の版。書き込むと版が上がって一覧が追いつき、日付が変わると作り直す。

トリガー名やキャッシュの入れ物は見ない。手元の stock.db は触らない。
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-ver-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient
from sqlalchemy import and_, func, select, text

from app.api.monthly._common import _latest_yoy_by_kind
from app.api.monthly.tables import list_tables_payload
from app.db import SessionLocal, engine, ensure_data_versions, init_db, monthly_list_version
from app.main import app
from app.models import MonthlyElement, MonthlyValue


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _version() -> int:
    db = SessionLocal()
    try:
        return monthly_list_version(db)
    finally:
        db.close()


def _tables() -> list[dict]:
    db = SessionLocal()
    try:
        return list_tables_payload(db)
    finally:
        db.close()


def _by_code() -> dict[str, dict]:
    return {row["code"]: row for row in _tables()}


def _exec(sql: str, params: dict | None = None) -> None:
    db = SessionLocal()
    try:
        db.execute(text(sql), params or {})
        db.commit()
    finally:
        db.close()


def _seed_issuer(code: str, name: str, *, yoy: float = 110.0, industry: str = "その他") -> None:
    _exec(
        "INSERT INTO stocks (code, name, market, sector33) VALUES (:c, :n, 'プライム', '小売業')",
        {"c": code, "n": name},
    )
    _exec(
        "INSERT INTO monthly_tables (code, year) VALUES (:c, 2024)",
        {"c": code},
    )
    _exec(
        "INSERT INTO monthly_issuers (code, industry, last_ingested_at) "
        "VALUES (:c, :i, :t)",
        {"c": code, "i": industry, "t": "2026-11-04 08:00:00"},
    )
    _exec(
        "INSERT INTO monthly_elements (code, year, element, sort_order, unit) "
        "VALUES (:c, 2024, '全店 売上高(前年比)', 1, 'yoy_index')",
        {"c": code},
    )
    _exec(
        "INSERT INTO monthly_values (code, year, element, month, value) "
        "VALUES (:c, 2024, '全店 売上高(前年比)', 3, :v)",
        {"c": code, "v": yoy},
    )


def test_hand_sql_bumps_version_and_list_follows() -> None:
    """API を通さない SQL でも版が上がり、使い回した一覧が新しい中身になる。"""
    init_db()
    before = _version()
    _seed_issuer("72030", "AAAAA自動車", yoy=110.0)
    if _version() <= before:
        _fail(f"seed must bump version: before={before} after={_version()}")

    first = _by_code()["72030"]
    if first["name"] != "AAAAA自動車" or first["all_store_yoy"] != 110.0:
        _fail(f"seeded list: {first!r}")
    cached = _version()

    _exec("UPDATE stocks SET name = :n WHERE code = '72030'", {"n": "BBBBB自動車"})
    if _version() <= cached:
        _fail("stocks UPDATE must bump version")
    renamed = _by_code()["72030"]
    if renamed["name"] != "BBBBB自動車":
        _fail(f"list kept the old name: {renamed!r}")

    cached = _version()
    _exec(
        "UPDATE monthly_values SET value = 123.4 "
        "WHERE code = '72030' AND element = '全店 売上高(前年比)'",
    )
    if _version() <= cached:
        _fail("monthly_values UPDATE must bump version")
    if _by_code()["72030"]["all_store_yoy"] != 123.4:
        _fail(f"list kept the old yoy: {_by_code()['72030']!r}")

    cached = _version()
    _exec(
        "UPDATE monthly_issuers SET industry = '外食' WHERE code = '72030'",
    )
    if _version() <= cached:
        _fail("monthly_issuers UPDATE must bump version")
    if _by_code()["72030"]["industry"] != "外食":
        _fail(f"list kept the old industry: {_by_code()['72030']!r}")

    cached = _version()
    _exec(
        "INSERT INTO monthly_elements (code, year, element, sort_order, unit) "
        "VALUES ('72030', 2025, '全店 客数(前年比)', 1, 'yoy_index')",
    )
    if _version() <= cached:
        _fail("monthly_elements INSERT must bump version")

    cached = _version()
    _exec("INSERT INTO monthly_tables (code, year) VALUES ('72030', 2026)")
    if _version() <= cached:
        _fail("monthly_tables INSERT must bump version")
    if 2026 not in _by_code()["72030"]["years"]:
        _fail(f"new year missing: {_by_code()['72030']!r}")

    cached = _version()
    _exec(
        "DELETE FROM monthly_values WHERE code = '72030' AND element = '全店 売上高(前年比)'",
    )
    if _version() <= cached:
        _fail("monthly_values DELETE must bump version")
    if _by_code()["72030"]["all_store_yoy"] is not None:
        _fail(f"deleted yoy still listed: {_by_code()['72030']!r}")


def test_public_pages_follow_the_list() -> None:
    """/monthly と /sitemap.xml と /api/v1/tables は同じ一覧を見る。"""
    init_db()
    _seed_issuer("73030", "CCCCC自動車")
    c = TestClient(app)

    page = c.get("/monthly")
    if page.status_code != 200 or "CCCCC" not in page.text:
        _fail(f"monthly html: {page.status_code} {page.text[:200]}")
    site = c.get("/sitemap.xml")
    if site.status_code != 200 or "/monthly/7303" not in site.text:
        _fail(f"sitemap before: {site.status_code} {site.text[:300]}")

    _exec("UPDATE stocks SET name = :n WHERE code = '73030'", {"n": "DDDDD自動車"})
    page = c.get("/monthly")
    if "DDDDD" not in page.text or "CCCCC" in page.text:
        _fail("monthly html kept the old name")
    api = c.get("/api/v1/tables")
    row = next(x for x in api.json() if x["code"] == "73030")
    if row["name"] != "DDDDD自動車":
        _fail(f"api list: {row!r}")

    _exec(
        "INSERT INTO stocks (code, name) VALUES ('11110', 'EEEEE社')",
    )
    _exec("INSERT INTO monthly_tables (code, year) VALUES ('11110', 2024)")
    site = c.get("/sitemap.xml")
    if "/monthly/1111" not in site.text:
        _fail(f"sitemap missed the new issuer: {site.text[:400]}")

    db = SessionLocal()
    try:
        one = list_tables_payload(db, "11110")
        full = list_tables_payload(db)
    finally:
        db.close()
    picked = [row for row in full if row["code"] == "11110"]
    if one != picked:
        _fail(f"only_code diverged: {one!r} vs {picked!r}")


def test_list_rebuilds_when_jst_date_changes() -> None:
    """版が同じでも JST の今日が変われば is_new の日付フラグを作り直す。"""
    init_db()
    _seed_issuer("33820", "FFFFF物産")
    from app.api.monthly import _common

    original = _common._jst_today
    _common._jst_today = lambda: date(2026, 11, 4)
    try:
        row = _by_code()["33820"]
        if row["ingested_today"] is not True:
            _fail(f"ingested today: {row!r}")
        _common._jst_today = lambda: date(2026, 11, 5)
        again = _by_code()["33820"]
        if again["ingested_today"] is not False:
            _fail(f"next JST day must not stay ingested_today: {again!r}")
        if again["name"] != row["name"] or again["all_store_yoy"] != row["all_store_yoy"]:
            _fail(f"date roll changed the facts: {row!r} -> {again!r}")
    finally:
        _common._jst_today = original


def test_yoy_matches_full_element_scan() -> None:
    """number 行を読まなくても、全行から選んだ前年比と一覧は同じ。"""
    init_db()
    code = "31790"
    _exec(
        "INSERT INTO stocks (code, name) VALUES (:c, 'シュッピン')",
        {"c": code},
    )
    for year in (2024, 2025):
        _exec(
            "INSERT INTO monthly_tables (code, year) VALUES (:c, :y)",
            {"c": code, "y": year},
        )
    rows = [
        (2024, "全店 売上高(前年比)", "yoy_index", 2, 3, 110.0),
        (2025, "EC 全店 売上高(前年比)", "yoy_index", 1, 12, 150.0),
        (2025, "既存 売上高(前年比)", "yoy_index", 1, 8, 101.5),
        (2025, "全店 売上高", "number", 1, 12, 999999.0),
        (2025, "店舗数", "number", 2, 12, 40.0),
    ]
    for year, element, unit, sort_order, month, value in rows:
        _exec(
            "INSERT INTO monthly_elements (code, year, element, sort_order, unit) "
            "VALUES (:c, :y, :e, :s, :u)",
            {"c": code, "y": year, "e": element, "s": sort_order, "u": unit},
        )
        _exec(
            "INSERT INTO monthly_values (code, year, element, month, value) "
            "VALUES (:c, :y, :e, :m, :v)",
            {"c": code, "y": year, "e": element, "m": month, "v": value},
        )

    db = SessionLocal()
    try:
        elems = db.execute(select(
            MonthlyElement.code, MonthlyElement.year, MonthlyElement.element,
            MonthlyElement.unit, MonthlyElement.sort_order,
        ).where(MonthlyElement.code == code)).all()
        last = (select(
            MonthlyValue.code, MonthlyValue.year, MonthlyValue.element,
            func.max(MonthlyValue.month).label("month"),
        ).where(MonthlyValue.code == code)
            .group_by(MonthlyValue.code, MonthlyValue.year, MonthlyValue.element)
            .subquery())
        vals = db.execute(select(
            MonthlyValue.code, MonthlyValue.year, MonthlyValue.element,
            MonthlyValue.month, MonthlyValue.value,
        ).join(last, and_(
            last.c.code == MonthlyValue.code,
            last.c.year == MonthlyValue.year,
            last.c.element == MonthlyValue.element,
            last.c.month == MonthlyValue.month,
        ))).all()
        full = _latest_yoy_by_kind(elems, vals)
        listed = list_tables_payload(db, code)[0]
    finally:
        db.close()
    if listed["all_store_yoy"] != full.get("all_store"):
        _fail(f"all_store {listed['all_store_yoy']!r} != full {full!r}")
    if listed["existing_store_yoy"] != full.get("existing"):
        _fail(f"existing {listed['existing_store_yoy']!r} != full {full!r}")
    if listed["all_store_yoy"] != 110.0 or listed["existing_store_yoy"] != 101.5:
        _fail(f"older company-wide must win: {listed!r}")


def test_list_yoy_ignores_a_unit_outside_the_stored_pair() -> None:
    init_db()
    code = "31780"
    _exec(
        "INSERT INTO stocks (code, name) VALUES (:c, '無視する単位')",
        {"c": code},
    )
    _exec("INSERT INTO monthly_tables (code, year) VALUES (:c, 2025)", {"c": code})
    _exec(
        "INSERT INTO monthly_elements (code, year, element, sort_order, unit) "
        "VALUES (:c, 2025, '全店 売上高(前年比)', 1, 'yoy_index')",
        {"c": code},
    )
    _exec(
        "INSERT INTO monthly_values (code, year, element, month, value) "
        "VALUES (:c, 2025, '全店 売上高(前年比)', 3, 110.0)",
        {"c": code},
    )
    _exec(
        "INSERT INTO monthly_elements (code, year, element, sort_order, unit) "
        "VALUES (:c, 2025, '既存 売上高(前年比)', 2, 'yoy_pct')",
        {"c": code},
    )
    _exec(
        "INSERT INTO monthly_values (code, year, element, month, value) "
        "VALUES (:c, 2025, '既存 売上高(前年比)', 8, 101.5)",
        {"c": code},
    )
    row = _by_code()[code]
    if row["all_store_yoy"] != 110.0 or row["existing_store_yoy"] is not None:
        _fail(f"stored yoy_pct must stay out of the list: {row!r}")


def test_ensure_restores_triggers_after_they_are_dropped() -> None:
    """トリガーが無い DB では版が動かない。ensure のあとの書き込みでまた動く。"""
    init_db()
    _seed_issuer("99890", "GGGGGドラッグ")
    with engine.begin() as conn:
        names = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        ))]
        if not names:
            _fail("fresh db has no triggers")
        for name in names:
            conn.execute(text(f'DROP TRIGGER "{name}"'))
    try:
        quiet = _version()
        _exec("UPDATE stocks SET name = 'HHHHHドラッグ' WHERE code = '99890'")
        if _version() != quiet:
            _fail(f"version moved without triggers: {_version()} from {quiet}")
        ensure_data_versions()
        if _version() != quiet:
            _fail("ensure itself must not bump the list version")
        _exec("UPDATE stocks SET sector33 = '医薬品' WHERE code = '99890'")
        if _version() <= quiet:
            _fail("write after ensure must bump version")
        if _by_code()["99890"]["name"] != "HHHHHドラッグ":
            _fail(f"name after restore: {_by_code()['99890']!r}")
        if _by_code()["99890"]["sector33"] != "医薬品":
            _fail(f"sector after restore: {_by_code()['99890']!r}")
    finally:
        ensure_data_versions()


def test_monthly_html_date_badge_follows_jst_today() -> None:
    init_db()
    _seed_issuer("27910", "IIIII物産")
    from app.api.monthly import _common

    c = TestClient(app)
    original = _common._jst_today
    _common._jst_today = lambda: date(2026, 11, 4)
    try:
        html = c.get("/monthly").text
        row = _issuer_row_html(html, "27910")
        if "badge new" not in row:
            _fail("ingested today must show NEW on /monthly")
        _common._jst_today = lambda: date(2026, 11, 5)
        html = c.get("/monthly").text
        row = _issuer_row_html(html, "27910")
        if "badge new" in row:
            _fail("the next JST day must drop NEW")
    finally:
        _common._jst_today = original


def _issuer_row_html(html: str, code: str) -> str:
    m = re.search(rf'data-code="{code}">.*?</tr>', html, re.S)
    if m is None:
        _fail(f"/monthly missing {code}")
    return m.group(0)

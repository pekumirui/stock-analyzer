from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import event

from app.db import SessionLocal, engine
from app.models import DailyPrice, MonthlyPublication, MonthlyTable, Stock

ROOT = Path(__file__).resolve().parents[2]

_LIST_JS = r"""
const fs = require("fs");
const els = {};
function box(){
  return {innerHTML: "", style: {}, value: "", dataset: {}, addEventListener(){}};
}
global.window = global;
global.addEventListener = () => {};
global.document = {
  body: {dataset: {publicMonthly: "1"}},
  addEventListener(){},
};
global.matchMedia = () => ({addEventListener(){}});
global.$ = sel => els[sel] || (els[sel] = box());
global.fmtMarket = () => ({cls: "", label: ""});
global.fmtSector = () => "";
const rows = JSON.parse(process.argv[2]);
eval(fs.readFileSync(process.argv[1], "utf8") + `
monthlyRows = ${JSON.stringify(rows)};
monthlyGroup = "__all__";
monthlySearchApplied = "";
renderMonthlyList();
process.stdout.write($("#monthly-list").innerHTML);
`);
"""


def _pub(code: str, year: int, month: int, on: date | None, confidence: str, basis: str) -> None:
    db = SessionLocal()
    try:
        db.add(MonthlyPublication(
            code=code, year=year, month=month,
            published_on=on, published_time=None,
            basis=basis, confidence=confidence,
            unknown_reason=None if on else "no_source",
            source_ref=None,
            recorded_at=datetime(2026, 6, 1, 9, 0, 0),
        ))
        db.commit()
    finally:
        db.close()


def _seed() -> None:
    db = SessionLocal()
    try:
        db.add(Stock(code="72030", name="トヨタ自動車", market="プライム", sector33="輸送用機器"))
        db.add(Stock(code="99830", name="ファーストリテイリング", market="プライム", sector33="小売業"))
        db.add(Stock(code="11110", name="日付なし商事", market="スタンダード", sector33="サービス業"))
        for code, year in (("72030", 2025), ("72030", 2026), ("99830", 2026), ("11110", 2026)):
            db.add(MonthlyTable(code=code, year=year))
        db.add(DailyPrice(
            code="72030", date=date(2026, 6, 10),
            open=100, high=110, low=90, close=105, volume=10, turnover=1000, adj_factor=1,
            adj_open=100, adj_high=110, adj_low=90, adj_close=105, adj_volume=10,
        ))
        db.commit()
    finally:
        db.close()
    _pub("72030", 2025, 12, date(2026, 8, 1), "exact", "tdnet")
    _pub("72030", 2026, 3, None, "unknown", "unknown")
    _pub("72030", 2026, 5, date(2026, 6, 10), "exact", "tdnet")
    _pub("99830", 2026, 4, date(2026, 5, 20), "estimated", "first_seen")
    _pub("99830", 2026, 6, None, "unknown", "unknown")
    _pub("11110", 2026, 1, None, "unknown", "unknown")


def _publication_sql(call) -> tuple[object, list[str]]:
    hit: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        if "monthly_publications" in statement.lower():
            hit.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        response = call()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return response, hit


@pytest.fixture(scope="module")
def seeded() -> None:
    _seed()


def test_list_latest_publication_skips_unknown_and_older_dates(c, seeded) -> None:
    response, sql = _publication_sql(lambda: c.get("/api/v1/tables"))
    assert response.status_code == 200
    assert len(sql) == 1
    by_code = {row["code"]: row for row in response.json()}
    assert by_code["72030"]["latest_publication"] == {
        "date": "2026-06-10", "confidence": "exact", "year": 2026, "month": 5,
    }
    assert by_code["99830"]["latest_publication"] == {
        "date": "2026-05-20", "confidence": "estimated", "year": 2026, "month": 4,
    }
    assert by_code["11110"]["latest_publication"] is None


def test_public_list_shows_exact_and_estimated(c, seeded) -> None:
    response = c.get("/monthly")
    assert response.status_code == 200
    html = response.text
    assert "<th>公表日</th>" in html
    assert "直近の公表日" not in html
    assert 'title="2026年5月">2026-06-10' in html
    assert "2026-06-10頃" not in html
    assert 'class="num pub-est" title="2026年4月">2026-05-20頃' in html
    assert "2026-08-01" not in html


def test_chart_marks_include_exact_and_estimated_only(c, seeded) -> None:
    response, sql = _publication_sql(lambda: c.get("/api/chart/72030"))
    assert response.status_code == 200
    assert len(sql) == 1
    body = response.json()
    assert body["earnings_dates"] == []
    assert body["monthly_publications"] == [
        {"date": "2026-06-10", "confidence": "exact", "year": 2026, "month": 5},
        {"date": "2026-08-01", "confidence": "exact", "year": 2025, "month": 12},
    ]
    other, other_sql = _publication_sql(lambda: c.get("/api/chart/99830"))
    assert other.status_code == 404
    assert other_sql == []


def test_list_publication_follows_writes_outside_the_list_cache(c, seeded) -> None:
    first = c.get("/api/v1/tables")
    assert first.json()
    db = SessionLocal()
    try:
        row = db.get(MonthlyPublication, ("72030", 2026, 5))
        row.published_on = date(2026, 6, 11)
        row.confidence = "estimated"
        db.commit()
    finally:
        db.close()
    again = {row["code"]: row for row in c.get("/api/v1/tables").json()}
    assert again["72030"]["latest_publication"]["date"] == "2026-06-11"
    assert again["72030"]["latest_publication"]["confidence"] == "estimated"


def test_monthly_list_cells_mark_estimates() -> None:
    rows = [
        {
            "code": "72030", "name": "トヨタ自動車", "industry": "自動車",
            "all_store_yoy": 103.2, "existing_store_yoy": None,
            "latest_publication": {
                "date": "2026-06-10", "confidence": "exact", "year": 2026, "month": 5,
            },
        },
        {
            "code": "99830", "name": "ファーストリテイリング", "industry": "アパレル",
            "all_store_yoy": None, "existing_store_yoy": 98.1,
            "latest_publication": {
                "date": "2026-05-20", "confidence": "estimated", "year": 2026, "month": 4,
            },
        },
        {
            "code": "11110", "name": "日付なし商事", "industry": "その他",
            "all_store_yoy": None, "existing_store_yoy": None,
            "latest_publication": None,
        },
    ]
    out = subprocess.run(
        ["node", "-e", _LIST_JS, str(ROOT / "static/js/monthly.js"), json.dumps(rows)],
        capture_output=True, text=True, check=True,
    )
    html = out.stdout
    assert "<th>公表日</th>" in html
    assert "直近の公表日" not in html
    assert 'title="2026年5月">2026-06-10</td>' in html
    assert "pub-est" in html and "2026-05-20頃" in html
    assert 'data-code="11110"' in html
    blank = html.split('data-code="11110"')[1]
    assert "<td class=\"num\"></td>" in blank
    assert "unknown" not in html
    assert "頃" not in html.split('data-code="99830"')[0]

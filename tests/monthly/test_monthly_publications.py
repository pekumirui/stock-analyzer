from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="stock-analyzer-publications-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import MonthlyPublication, Statement, Stock
from app.monthly.publications import dated, note, unknown


def _fy(code: str) -> None:
    db = SessionLocal()
    try:
        if db.get(Stock, code) is None:
            db.add(Stock(code=code, name="t"))
        db.add(Statement(
            disclosure_no=f"fy-{code}",
            code=code,
            disclosed_date=date(2026, 5, 8),
            fiscal_year_end=date(2026, 3, 31),
        ))
        db.commit()
    finally:
        db.close()


def _rows(code: str) -> dict[tuple[int, int], tuple]:
    db = SessionLocal()
    try:
        found = {}
        for row in db.query(MonthlyPublication).filter_by(code=code):
            on = row.published_on.isoformat() if row.published_on else None
            found[(row.year, row.month)] = (
                row.basis, on, row.published_time, row.unknown_reason, row.confidence,
            )
        return found
    finally:
        db.close()


def _ingest(c: TestClient, code: str, months: list[int], publication: dict | None, *, dry_run: bool = False):
    body = {
        "months": months,
        "rows": [{
            "element": "売上高",
            "unit": "number",
            "values": {str(month): float(month) for month in months},
        }],
        "as_of": "2026-08-05",
    }
    if publication is not None:
        body["publication"] = publication
    if dry_run:
        body["dry_run"] = True
    return c.post(f"/api/v1/tables/{code}/ingest", json=body)


def test_omitted_publication_is_unknown_and_as_of_is_not_copied():
    c = TestClient(app)
    code = "72031"
    _fy(code)
    r = _ingest(c, code, [7], None)
    assert r.status_code == 200, r.text
    assert _rows(code) == {(2026, 7): ("unknown", None, None, "unspecified", "unknown")}


def test_dry_run_does_not_store_a_publication():
    c = TestClient(app)
    code = "72032"
    _fy(code)
    r = _ingest(c, code, [7], {"basis": "tdnet", "on": "2026-08-05", "time": "15:00:00"}, dry_run=True)
    assert r.status_code == 200, r.text
    assert _rows(code) == {}


def test_one_disclosure_dates_only_the_newest_new_month():
    c = TestClient(app)
    code = "72033"
    _fy(code)
    pub = {"basis": "tdnet", "on": "2026-08-05", "time": "15:30", "ref": "aug.pdf"}
    r = _ingest(c, code, [6, 7], pub)
    assert r.status_code == 200, r.text
    assert _rows(code)[2026, 7] == ("tdnet", "2026-08-05", "15:30:00", None, "exact")
    assert _rows(code)[2026, 6] == ("unknown", None, None, "carried_history", "unknown")


def test_revising_an_existing_month_does_not_move_its_date():
    c = TestClient(app)
    code = "72034"
    _fy(code)
    first = _ingest(c, code, [7], {"basis": "first_seen", "on": "2026-08-01"})
    assert first.status_code == 200, first.text
    later = _ingest(c, code, [7], {"basis": "tdnet", "on": "2026-10-01", "time": "15:00:00"})
    assert later.status_code == 200, later.text
    assert _rows(code) == {(2026, 7): ("first_seen", "2026-08-01", None, None, "estimated")}


def test_a_new_month_on_a_later_filing_keeps_the_earlier_month():
    c = TestClient(app)
    code = "72035"
    _fy(code)
    first = _ingest(c, code, [6, 7], {"basis": "tdnet", "on": "2026-08-05"})
    assert first.status_code == 200, first.text
    second = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [8],
        "rows": [{"element": "売上高", "unit": "number", "values": {"8": 8.0}}],
        "as_of": "2026-09-10",
        "publication": {"basis": "tdnet", "on": "2026-09-10", "time": "16:00:00"},
    })
    assert second.status_code == 200, second.text
    rows = _rows(code)
    assert rows[2026, 7] == ("tdnet", "2026-08-05", None, None, "exact")
    assert rows[2026, 8] == ("tdnet", "2026-09-10", "16:00:00", None, "exact")
    assert rows[2026, 6][0] == "unknown"


def test_put_on_a_new_month_is_manual_and_clearing_the_month_drops_the_row():
    c = TestClient(app)
    code = "72036"
    _fy(code)
    created = _ingest(c, code, [7], {"basis": "ir_page", "on": "2026-08-04"})
    assert created.status_code == 200, created.text
    added = c.put(
        f"/api/v1/tables/{code}/2026/values",
        json={"element": "売上高", "month": 8, "value": 1.0},
    )
    assert added.status_code == 200, added.text
    assert _rows(code)[2026, 8] == ("unknown", None, None, "manual", "unknown")
    assert _rows(code)[2026, 7][0] == "ir_page"
    cleared = c.put(
        f"/api/v1/tables/{code}/2026/values",
        json={"element": "売上高", "month": 8, "value": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert (2026, 8) not in _rows(code)
    assert (2026, 7) in _rows(code)


def test_unknown_with_a_date_is_rejected():
    c = TestClient(app)
    code = "72037"
    _fy(code)
    r = _ingest(c, code, [7], {"basis": "unknown", "on": "2026-08-05", "reason": "unspecified"})
    assert r.status_code == 400, r.text
    assert _rows(code) == {}


def test_note_prefers_exact_over_an_earlier_estimate_and_keeps_the_earlier_exact_date():
    db = SessionLocal()
    try:
        assert note(db, "72038", 2026, 7, dated("tdnet", date(2026, 8, 10), "15:00:00")) == "insert"
        assert note(db, "72038", 2026, 7, dated("first_seen", date(2026, 8, 1))) == "keep"
        assert note(db, "72038", 2026, 7, dated("ir_page", date(2026, 8, 1))) == "replace"
        row = db.get(MonthlyPublication, ("72038", 2026, 7))
        assert row.basis == "ir_page" and row.published_on == date(2026, 8, 1)
        assert row.confidence == "exact"
        assert note(db, "72038", 2026, 7, dated("tdnet", date(2026, 8, 20))) == "keep"
        assert note(db, "72038", 2026, 7, unknown("no_source")) == "keep"
        assert note(db, "72039", 2026, 4, unknown("unspecified")) == "insert"
        assert note(db, "72039", 2026, 4, dated("ir_page", date(2026, 5, 2))) == "replace"
        assert note(db, "72040", 2026, 5, dated("first_seen", date(2026, 6, 5))) == "insert"
        assert note(db, "72040", 2026, 5, dated("tdnet", date(2026, 6, 5), "09:00:00")) == "replace"
        row = db.get(MonthlyPublication, ("72040", 2026, 5))
        assert row.basis == "tdnet" and row.published_time == "09:00:00"
        assert row.confidence == "exact"
        db.rollback()
    finally:
        db.close()

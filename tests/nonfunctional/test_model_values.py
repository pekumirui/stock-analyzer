from datetime import date, datetime

import pytest
from sqlalchemy import Enum, select
from sqlalchemy.exc import StatementError

from app.db import SessionLocal, init_db, upsert
from app.models import Base, PtsSnapshot


def _enum_columns():
    return [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, Enum)]


def test_value_columns_reject_strings_outside_the_list():
    for c in _enum_columns():
        assert c.type.validate_strings and not c.type.native_enum, f"{c.table.name}.{c.name}"


def test_declared_refs_point_at_existing_columns():
    for t in Base.metadata.tables.values():
        for c in t.columns:
            ref = c.info.get("ref")
            if ref:
                table, column = ref.split(".")
                assert column in Base.metadata.tables[table].columns, f"{t.name}.{c.name} → {ref}"


def test_upsert_and_filter_fail_on_an_unlisted_value():
    init_db()
    row = {"fetched_at": datetime.fromisoformat("2026-01-05T17:30:00"), "ranking": "night_up", "code": "72030",
           "session_date": date(2026, 1, 5), "rank": 1}
    with SessionLocal() as db:
        upsert(db, PtsSnapshot, [row])
        with pytest.raises(StatementError, match="night_sideways"):
            upsert(db, PtsSnapshot, [{**row, "ranking": "night_sideways"}])
        db.rollback()
        with pytest.raises(StatementError, match="night_sideways"):
            db.execute(select(PtsSnapshot).where(PtsSnapshot.ranking == "night_sideways")).all()

from datetime import date, datetime

import pytest
from sqlalchemy import Column, Enum, Integer, MetaData, String, Table, select
from sqlalchemy.exc import StatementError

from app.db import SessionLocal, init_db, upsert
from app.models import Base, PtsSnapshot
from tools.schema.render import declared_refs


def _enum_columns():
    return [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, Enum)]


def test_value_columns_reject_strings_outside_the_list():
    for c in _enum_columns():
        assert c.type.validate_strings and not c.type.native_enum, f"{c.table.name}.{c.name}"


def _ref_problems(t: Table, tables) -> list[str]:
    bad = [f"{t.name}: {cols} は 1 列。列の info に書く" for cols in t.info.get("ref", {}) if "," not in cols]
    for cols, table, to in declared_refs(t):
        where = f"{t.name}.{', '.join(cols)} → {table}.{', '.join(to)}"
        if len(cols) != len(to):
            bad.append(f"{where}: 列の数が合わない")
        if any(c not in t.columns for c in cols):
            bad.append(f"{where}: 子の列が無い")
        if table not in tables or any(c not in tables[table].columns for c in to):
            bad.append(f"{where}: 親の列が無い")
    return bad


def test_declared_refs_point_at_existing_columns():
    for t in Base.metadata.tables.values():
        assert _ref_problems(t, Base.metadata.tables) == []


def test_bad_declared_refs_are_reported():
    meta = MetaData()
    parent = Table("parent", meta, Column("code", String), Column("year", Integer))
    child = Table(
        "child", meta, Column("code", String, info={"ref": "parent.name"}), Column("year", Integer),
        info={"ref": {"code, year": "parent.code", "code, month": "parent.code, year", "year": "parent.year",
                      "code, year ": "gone.code, year"}},
    )
    assert _ref_problems(parent, meta.tables) == []
    assert _ref_problems(child, meta.tables) == [
        "child: year は 1 列。列の info に書く",
        "child.code → parent.name: 親の列が無い",
        "child.code, year → parent.code: 列の数が合わない",
        "child.code, month → parent.code, year: 子の列が無い",
        "child.code, year → gone.code, year: 親の列が無い",
    ]


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

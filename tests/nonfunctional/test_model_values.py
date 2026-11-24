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


def test_monthly_element_unit_accepts_only_number_and_yoy_index():
    from sqlalchemy.dialects import sqlite

    from app.models import MonthlyElement

    assert list(MonthlyElement.unit.type.enums) == ["number", "yoy_index"]
    assert MonthlyElement.unit.type.compile(dialect=sqlite.dialect()) == "VARCHAR(16)"
    init_db()
    base = {"code": "72030", "year": 2026, "element": "売上高", "sort_order": 1}
    with SessionLocal() as db:
        db.add(MonthlyElement(**base, unit="number"))
        db.add(MonthlyElement(**{**base, "element": "売上高(前年比)", "sort_order": 2}, unit="yoy_index"))
        db.flush()
        db.rollback()
        with pytest.raises(StatementError, match="yoy_pct"):
            db.add(MonthlyElement(**base, unit="yoy_pct"))
            db.flush()
        db.rollback()
        with pytest.raises(StatementError, match="yoy_pct"):
            db.execute(select(MonthlyElement).where(MonthlyElement.unit == "yoy_pct")).all()


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


def test_time_types_are_plain_datetime_columns():
    from sqlalchemy.dialects import sqlite

    from app.models import JstDateTime, UtcDateTime

    cols = [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, (JstDateTime, UtcDateTime))]
    assert {type(c.type) for c in cols} == {JstDateTime, UtcDateTime}
    assert {c.type.compile(dialect=sqlite.dialect()) for c in cols} == {"DATETIME"}
    init_db()
    when = datetime(2026, 1, 5, 17, 30)
    row = {"fetched_at": when, "ranking": "night_down", "code": "72030", "session_date": date(2026, 1, 5), "rank": 1}
    with SessionLocal() as db:
        upsert(db, PtsSnapshot, [row])
        got = db.execute(select(PtsSnapshot.fetched_at).where(PtsSnapshot.ranking == "night_down")).scalar_one()
    assert got == when and got.tzinfo is None


def test_meanings_belong_to_listed_values():
    for c in _enum_columns():
        assert set(c.type.meanings) <= set(c.type.enums), f"{c.table.name}.{c.name}"
        assert all(c.type.meanings.values()), f"{c.table.name}.{c.name}"


def test_one_of_keeps_the_written_order_and_meanings():
    from sqlalchemy.dialects import sqlite

    from app.models import _one_of

    mixed = _one_of("parsed", {"meta": "読まない"}, "failed", length=16)
    assert mixed.enums == ["parsed", "meta", "failed"]
    assert mixed.meanings == {"meta": "読まない"}
    assert mixed.compile(dialect=sqlite.dialect()) == "VARCHAR(16)"
    whole = _one_of({"b": "二", "a": "一"})
    assert whole.enums == ["b", "a"] and whole.meanings == {"b": "二", "a": "一"}
    assert whole.compile(dialect=sqlite.dialect()) == "VARCHAR"
    assert _one_of("x", "y").meanings == {}


def test_xbrl_config_values_are_in_the_model_lists():
    import re
    from pathlib import Path

    from app.models import XbrlElementMapping, XbrlStdItem

    root = Path(__file__).resolve().parents[2] / "config" / "xbrl"
    std = (root / "std_items.yaml").read_text(encoding="utf-8")
    mappings = "".join(p.read_text(encoding="utf-8") for p in sorted((root / "mappings").glob("*.yaml")))
    for text, key, col in (
        (std, "statement", XbrlStdItem.statement),
        (std, "sign", XbrlStdItem.sign),
        (mappings, "part", XbrlElementMapping.part),
    ):
        used = set(re.findall(rf"\b{key}:\s*([A-Za-z_]+)", text))
        assert used, key
        assert used <= set(col.type.enums), f"{key}: config/xbrl に {sorted(used - set(col.type.enums))} がある。app/models.py の一覧に足す"

"""daily_prices を WITHOUT ROWID に作り直すジョブ（KAB-289）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs" / "ops"))

import rebuild_daily_prices as job  # noqa: E402

from app.db import SessionLocal, engine, upsert  # noqa: E402
from app.models import DailyPrice  # noqa: E402


def _ddl() -> str:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'daily_prices'"
        )).scalar()


def _make_rowid_table() -> None:
    """今の本番と同じ rowid 表（ix_prices_date 付き）に戻す。"""
    with engine.begin() as conn:
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'daily_prices'"
        )).scalar()
        conn.execute(text("DROP TABLE daily_prices"))
        conn.execute(text(ddl.replace("WITHOUT ROWID", "")))
        conn.execute(text("CREATE INDEX ix_prices_date ON daily_prices (date)"))


def _rows() -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT code, date, close, adj_close, volume, ex_rt FROM daily_prices ORDER BY code, date"
        )).fetchall()


def test_new_db_is_without_rowid():
    assert "WITHOUT ROWID" in _ddl().upper()


def test_rebuild_keeps_rows_and_index(capsys):
    _make_rowid_table()
    assert "WITHOUT ROWID" not in _ddl().upper()
    db = SessionLocal()
    try:
        for d in (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)):
            upsert(db, DailyPrice, [
                {"code": code, "date": d, "close": 100.0 + d.day, "adj_close": 99.0 + d.day,
                 "volume": 1000.0, "ex_rt": "1" if code == "22220" and d.day == 6 else None}
                for code in ("33330", "11110", "22220")
            ])
    finally:
        db.close()
    before = _rows()

    assert job.main([]) == 0
    assert "WITHOUT ROWID" not in _ddl().upper()

    assert job.main(["--yes"]) == 0
    assert "WITHOUT ROWID" in _ddl().upper()
    assert _rows() == before
    with engine.connect() as conn:
        idx = [r[1] for r in conn.execute(text("PRAGMA index_list('daily_prices')")).fetchall()]
        names = {r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE name LIKE 'daily_prices%'")).fetchall()}
    assert "ix_prices_date" in idx
    assert names == {"daily_prices"}

    assert job.main(["--yes"]) == 0
    assert "既に WITHOUT ROWID" in capsys.readouterr().out
    assert _rows() == before


def test_upsert_after_rebuild():
    db = SessionLocal()
    try:
        upsert(db, DailyPrice, [{"code": "11110", "date": date(2026, 1, 6), "close": 1.0}])
        upsert(db, DailyPrice, [{"code": "11110", "date": date(2026, 1, 8), "close": 2.0}])
    finally:
        db.close()
    with engine.connect() as conn:
        got = conn.execute(text(
            "SELECT date, close FROM daily_prices WHERE code = '11110' ORDER BY date")).fetchall()
    assert [(str(d), c) for d, c in got] == [
        ("2026-01-05", 105.0), ("2026-01-06", 1.0), ("2026-01-07", 107.0), ("2026-01-08", 2.0)]


def test_refuses_when_disk_is_short(monkeypatch, capsys):
    _make_rowid_table()

    class Usage:
        free = 0
    monkeypatch.setattr(job.shutil, "disk_usage", lambda _p: Usage())
    monkeypatch.setattr(job, "table_bytes", lambda _c: 10 * 1024 * 1024)
    assert job.main(["--yes"]) == 1
    assert "空きが足りない" in capsys.readouterr().out
    assert "WITHOUT ROWID" not in _ddl().upper()

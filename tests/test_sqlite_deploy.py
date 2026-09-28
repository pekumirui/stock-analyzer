"""SQLite WAL / busy_timeout と migrate の locked 再試行。手元の stock.db は触らない。"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-sqlite-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.db import apply_sqlite_pragmas, engine, sqlite_connect_args  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _load_migrate():
    spec = importlib.util.spec_from_file_location("migrate_job", ROOT / "jobs" / "migrate.py")
    if spec is None or spec.loader is None:
        _fail("jobs/migrate.py not loadable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migrate = _load_migrate()


def test_connect_args() -> None:
    if sqlite_connect_args("sqlite:///x.db") != {"timeout": 30.0}:
        _fail(f"sqlite timeout: {sqlite_connect_args('sqlite:///x.db')!r}")
    if sqlite_connect_args("postgresql://localhost/x"):
        _fail("non-sqlite must not set connect_args")


def test_engine_pragmas() -> None:
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
        sync = conn.execute(text("PRAGMA synchronous")).scalar()
    if str(mode).lower() != "wal":
        _fail(f"journal_mode={mode!r}")
    if int(timeout) < 30000:
        _fail(f"busy_timeout={timeout!r}")
    # NORMAL == 1
    if int(sync) != 1:
        _fail(f"synchronous={sync!r}")


def test_apply_pragmas_on_raw_connection() -> None:
    path = Path(_tmp) / "raw.db"
    conn = sqlite3.connect(path)
    try:
        apply_sqlite_pragmas(conn)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    if str(mode).lower() != "wal":
        _fail(f"raw journal_mode={mode!r}")
    if int(timeout) < 30000:
        _fail(f"raw busy_timeout={timeout!r}")


def test_is_sqlite_lock() -> None:
    orig = sqlite3.OperationalError("database is locked")
    exc = OperationalError("COMMIT", {}, orig)
    if not migrate.is_sqlite_lock(exc):
        _fail("OperationalError orig=locked must match")
    if not migrate.is_sqlite_lock(orig):
        _fail("sqlite3 locked must match")
    if migrate.is_sqlite_lock(OperationalError("SELECT", {}, sqlite3.OperationalError("no such table"))):
        _fail("unrelated OperationalError must not match")


def test_migrate_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def boom_then_ok() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError(
                "COMMIT", {}, sqlite3.OperationalError("database is locked"),
            )

    with patch.object(migrate, "migrate_once", boom_then_ok), patch.object(migrate.time, "sleep"):
        migrate.main()
    if calls["n"] != 3:
        _fail(f"migrate should retry until success, got {calls['n']}")


def test_migrate_gives_up_on_persistent_lock() -> None:
    def always_locked() -> None:
        raise OperationalError(
            "COMMIT", {}, sqlite3.OperationalError("database is locked"),
        )

    with patch.object(migrate, "migrate_once", always_locked), patch.object(migrate.time, "sleep"):
        try:
            migrate.main()
        except OperationalError as exc:
            if not migrate.is_sqlite_lock(exc):
                _fail(f"expected lock error, got {exc!r}")
        else:
            _fail("persistent lock must raise")


def test_migrate_once_is_callable() -> None:
    migrate.migrate_once()


def main() -> None:
    test_connect_args()
    test_engine_pragmas()
    test_apply_pragmas_on_raw_connection()
    test_is_sqlite_lock()
    test_migrate_retries_then_succeeds()
    test_migrate_gives_up_on_persistent_lock()
    test_migrate_once_is_callable()
    print("ok: sqlite wal / migrate lock retry")


if __name__ == "__main__":
    main()

"""SQLite WAL / busy_timeout と migrate の locked 再試行。手元の stock.db は触らない。"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-sqlite-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.db import (  # noqa: E402
    apply_sqlite_pragmas,
    engine,
    is_sqlite_lock,
    run_sqlite_write,
    sqlite_connect_args,
)


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
    if not is_sqlite_lock(exc):
        _fail("OperationalError orig=locked must match")
    if not migrate.is_sqlite_lock(exc):
        _fail("migrate must re-export is_sqlite_lock")
    if not is_sqlite_lock(orig):
        _fail("sqlite3 locked must match")
    if is_sqlite_lock(OperationalError("SELECT", {}, sqlite3.OperationalError("no such table"))):
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


def test_run_sqlite_write_retries_lock() -> None:
    class Fake:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    db = Fake()
    n = {"i": 0}

    def boom_then_ok() -> int:
        n["i"] += 1
        if n["i"] < 3:
            raise OperationalError(
                "COMMIT", {}, sqlite3.OperationalError("database is locked"),
            )
        return 7

    with patch("app.db.time.sleep"):
        got = run_sqlite_write(db, boom_then_ok)
    if got != 7 or n["i"] != 3 or db.commits != 1 or db.rollbacks != 2:
        _fail(f"retry: got={got} n={n['i']} c={db.commits} r={db.rollbacks}")


def test_run_sqlite_write_gives_up() -> None:
    class Fake:
        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    def always_locked() -> None:
        raise OperationalError(
            "COMMIT", {}, sqlite3.OperationalError("database is locked"),
        )

    with patch("app.db.time.sleep"):
        try:
            run_sqlite_write(Fake(), always_locked, attempts=3)
        except OperationalError as exc:
            if not is_sqlite_lock(exc):
                _fail(f"expected lock error, got {exc!r}")
        else:
            _fail("persistent lock must raise")


def test_put_values_batch_retries_sqlite_lock() -> None:
    from app.api.monthly.elements import put_values_batch

    src = inspect.getsource(put_values_batch)
    if "run_sqlite_write" not in src:
        _fail("put_values_batch must retry sqlite locked via run_sqlite_write")


def _connect_short_timeout(path: Path, timeout_s: float) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout_s, check_same_thread=False)
    apply_sqlite_pragmas(conn)
    conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
    return conn


def test_long_write_txn_exceeds_short_busy_timeout() -> None:
    """#522 の再現: 1 本の長い書き込みトランザクションは busy_timeout を超えると locked。

    本番は timeout=30s。CI では 0.25s に縮めて同じ大小関係（保持 > timeout）を見る。
    WAL / busy_timeout 自体は効いている（旧既定 5s ではなく pragma の値で待つ）。
    """
    path = Path(_tmp) / "lock_long.db"
    timeout_s = 0.25
    hold_s = 0.9
    setup = sqlite3.connect(path)
    try:
        apply_sqlite_pragmas(setup)
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
        setup.commit()
    finally:
        setup.close()

    errors: list[str] = []
    started = threading.Event()

    def holder() -> None:
        conn = _connect_short_timeout(path, timeout_s)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO t (v) VALUES (1)")
            started.set()
            time.sleep(hold_s)
            conn.commit()
        finally:
            conn.close()

    def waiter() -> None:
        started.wait(timeout=2)
        conn = _connect_short_timeout(path, timeout_s)
        try:
            t0 = time.monotonic()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO t (v) VALUES (2)")
                conn.commit()
                errors.append("succeeded")
            except sqlite3.OperationalError as exc:
                waited = time.monotonic() - t0
                errors.append(f"{exc}|{waited:.3f}")
        finally:
            conn.close()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    if len(errors) != 1 or "locked" not in errors[0]:
        _fail(f"expected database is locked, got {errors!r}")
    waited = float(errors[0].split("|")[1])
    if waited < timeout_s * 0.6:
        _fail(f"busy_timeout did not wait: {waited:.3f}s errors={errors!r}")


def test_chunked_commits_let_other_writer_in() -> None:
    """#522 の修正側: チャンクごとに commit すれば他ライターが busy_timeout 内で入れる。"""
    path = Path(_tmp) / "lock_chunk.db"
    timeout_s = 0.3
    setup = sqlite3.connect(path)
    try:
        apply_sqlite_pragmas(setup)
        setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
        setup.commit()
    finally:
        setup.close()

    started = threading.Event()
    result: list[str] = []

    def holder() -> None:
        conn = _connect_short_timeout(path, timeout_s)
        try:
            started.set()
            for i in range(12):
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO t (v) VALUES (?)", (i,))
                conn.commit()
                time.sleep(0.04)
        finally:
            conn.close()

    def waiter() -> None:
        started.wait(timeout=2)
        time.sleep(0.02)
        conn = _connect_short_timeout(path, timeout_s)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO t (v) VALUES (99)")
            conn.commit()
            result.append("ok")
        except sqlite3.OperationalError as exc:
            result.append(str(exc))
        finally:
            conn.close()

    t1 = threading.Thread(target=holder)
    t2 = threading.Thread(target=waiter)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    if result != ["ok"]:
        _fail(f"chunked writer should let the other in, got {result!r}")


def test_delete_all_chunked_and_compute_indicators_commit() -> None:
    from app.market.analytics import compute_indicators
    from app.db import SessionLocal, init_db
    from app.db import delete_all_chunked, upsert
    from app.models import DailyPrice, Indicator, Stock

    init_db()
    db = SessionLocal()
    try:
        upsert(db, Stock, [{
            "code": "72030", "name": "トヨタ自動車", "product_category": "011",
        }])
        day0 = date(2026, 1, 5)
        prices = []
        for i in range(5):
            d = day0 + timedelta(days=i)
            prices.append({
                "code": "72030", "date": d,
                "open": 1, "high": 1, "low": 1, "close": 1.0 + i,
                "volume": 1, "turnover": 1, "adj_factor": 1.0,
                "adj_open": 1, "adj_high": 1, "adj_low": 1, "adj_close": 1.0 + i,
                "adj_volume": 1,
            })
        upsert(db, DailyPrice, prices)
        for i in range(5):
            upsert(db, Indicator, [{
                "code": "72030", "date": day0 + timedelta(days=i), "close": 1.0,
            }])
        n_del = delete_all_chunked(db, Indicator, chunk_size=2)
        if n_del != 5:
            _fail(f"delete_all_chunked removed {n_del}, want 5")
        if db.query(Indicator).count() != 0:
            _fail("indicators should be empty after chunked delete")

        commits = {"n": 0}
        orig = db.commit

        def counting() -> None:
            commits["n"] += 1
            orig()

        db.commit = counting  # type: ignore[method-assign]
        compute_indicators(db, keep_days=10, chunk_size=2)
        if commits["n"] < 3:
            _fail(f"compute_indicators should commit per chunk, got {commits['n']}")
        if db.query(Indicator).count() != 5:
            _fail(f"indicators after recompute: {db.query(Indicator).count()}")
    finally:
        db.close()


def test_put_values_batch_during_writer_lock() -> None:
    """月次 PUT は他ライターの短いロックを待ち、500 にしない。"""
    from fastapi.testclient import TestClient

    from app.db import SessionLocal, init_db
    from app.db import upsert
    from app.main import app
    from app.models import MonthlyElement, MonthlyTable, Stock

    init_db()
    db = SessionLocal()
    try:
        upsert(db, Stock, [{"code": "26540", "name": "x"}])
        if db.get(MonthlyTable, ("26540", 2026)) is None:
            db.add(MonthlyTable(code="26540", year=2026))
        if db.get(MonthlyElement, ("26540", 2026, "売上高")) is None:
            db.add(MonthlyElement(
                code="26540", year=2026, element="売上高",
                sort_order=1, unit="number",
            ))
        db.commit()
    finally:
        db.close()

    db_path = engine.url.database
    if not db_path:
        _fail("sqlite database path missing")
    ready = threading.Event()

    def holder() -> None:
        conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
        try:
            apply_sqlite_pragmas(conn)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE monthly_elements SET sort_order = sort_order WHERE code = '26540'",
            )
            ready.set()
            time.sleep(0.25)
            conn.commit()
        finally:
            conn.close()

    t = threading.Thread(target=holder)
    t.start()
    if not ready.wait(timeout=2):
        t.join()
        _fail("writer lock did not start")
    c = TestClient(app)
    r = c.put("/api/v1/tables/26540/2026/values/batch", json={
        "element": "売上高",
        "values": [{"month": 1, "value": 12.5}],
    })
    t.join()
    if r.status_code != 200:
        _fail(f"PUT during lock: {r.status_code} {r.text[:400]}")
    if r.json().get("updated") != [1]:
        _fail(f"PUT body: {r.json()!r}")


def main() -> None:
    test_connect_args()
    test_engine_pragmas()
    test_apply_pragmas_on_raw_connection()
    test_is_sqlite_lock()
    test_migrate_retries_then_succeeds()
    test_migrate_gives_up_on_persistent_lock()
    test_migrate_once_is_callable()
    test_run_sqlite_write_retries_lock()
    test_run_sqlite_write_gives_up()
    test_put_values_batch_retries_sqlite_lock()
    test_long_write_txn_exceeds_short_busy_timeout()
    test_chunked_commits_let_other_writer_in()
    test_delete_all_chunked_and_compute_indicators_commit()
    test_put_values_batch_during_writer_lock()
    print("ok: sqlite wal / migrate lock retry")


if __name__ == "__main__":
    main()

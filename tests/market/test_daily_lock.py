from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-daily-lock-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")
os.environ.setdefault("JQUANTS_API_KEY", "test-key")

from alert_patterns import should_page
from app.db import SessionLocal, apply_sqlite_pragmas, init_db
from app.market.ingest import run_daily
from app.market.price_gap import price_gap_line, report_price_gap
from app.models import Base, DailyPrice

DAY = date(2026, 11, 24)
PRIOR = date(2026, 11, 20)
N = 40


def _fail(msg) -> None:
    raise SystemExit(msg)


def _price(code: str, day: date) -> DailyPrice:
    return DailyPrice(code=code, date=day, close=1.0, adj_close=1.0, adj_factor=1.0)


def _seed(db, day: date, n: int, *, start: int = 0) -> None:
    db.add_all(_price(f"{start + i:04d}0", day) for i in range(n))
    db.commit()


def _count(db, day: date) -> int:
    return int(db.query(func.count()).filter(DailyPrice.date == day).scalar() or 0)


def _wipe(db) -> None:
    db.query(DailyPrice).delete()
    db.commit()


def _client(n: int, calls: list[str], *, boom: BaseException | None = None):
    class JQ:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def equities_master(self, date=None):
            calls.append("master")
            if boom is not None:
                raise boom
            return [{"Code": "72030", "CoName": "トヨタ", "ProdCat": "011"}]

        def bars_daily(self, date=None, **kwargs):
            calls.append(f"bars:{date}")
            return [
                {"Code": f"{i:04d}0", "Date": date, "C": "100", "AdjC": "100", "AdjFactor": "1"}
                for i in range(n)
            ]

        def topix(self, from_=None, to=None):
            calls.append("topix")
            return [{"Date": from_, "O": "1", "H": "1", "L": "1", "C": "1"}]

        def fins_summary(self, date=None, **kwargs):
            calls.append(f"fins:{date}")
            return []

    return JQ


def _engine(path: Path, timeout_s: float):
    eng = create_engine(
        f"sqlite:///{path}", future=True, poolclass=NullPool,
        connect_args={"timeout": timeout_s},
    )

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_connection, connection_record):
        apply_sqlite_pragmas(dbapi_connection)
        cur = dbapi_connection.cursor()
        try:
            cur.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        finally:
            cur.close()

    Base.metadata.create_all(eng)
    return eng, sessionmaker(bind=eng, autoflush=False, future=True)


def _hold(path: Path, timeout_s: float, release: threading.Event, started: threading.Event,
          result: dict) -> None:
    conn = sqlite3.connect(path, timeout=timeout_s, check_same_thread=False)
    try:
        apply_sqlite_pragmas(conn)
        conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS lock_hold (id INTEGER)")
        conn.execute("INSERT INTO lock_hold (id) VALUES (1)")
        started.set()
        if not release.wait(10):
            result["holder"] = "timeout"
        else:
            conn.commit()
            result["holder"] = "ok"
    except Exception as exc:
        result["holder"] = f"{type(exc).__name__}: {exc}"
        started.set()
    finally:
        conn.close()


def _gap_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "gap daily_prices " in ln]


def test_run_daily_writes_prices_when_other_writer_outlasts_one_busy_timeout() -> None:
    path = Path(tempfile.mkdtemp(prefix="daily-lock-ok-")) / "stock.db"
    timeout_s = 0.2
    eng, Session = _engine(path, timeout_s)
    db = Session()
    release = threading.Event()
    started = threading.Event()
    result: dict = {}
    sleeps: list[float] = []
    calls: list[str] = []

    def sleeper(delay: float) -> None:
        sleeps.append(delay)
        release.set()
        time.sleep(delay)

    t = threading.Thread(target=_hold, args=(path, timeout_s, release, started, result))
    try:
        got = int(db.execute(text("PRAGMA busy_timeout")).scalar())
        db.commit()
        if got > 1000:
            _fail(f"busy_timeout stayed at {got}")
        _seed(db, PRIOR, N)
        t.start()
        if not started.wait(2):
            _fail(f"holder did not lock: {result!r}")
        t0 = time.monotonic()
        with patch("app.market.ingest.JQuantsClient", _client(N, calls)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                n = run_daily(
                    db, DAY.isoformat(), attempts=4, sleep=sleeper,
                    lock_sleep_s=0.05, lock_sleep_cap_s=0.05,
                )
        waited = time.monotonic() - t0
        out = buf.getvalue()
        if waited < timeout_s * 0.6:
            _fail(f"did not wait for the lock: {waited:.3f}s")
        if not sleeps:
            _fail(f"finished without a locked retry: {out!r}")
        if n != N + 1:
            _fail(f"return {n}")
        if _count(db, DAY) != N or _count(db, PRIOR) != N:
            _fail(f"day={_count(db, DAY)} prior={_count(db, PRIOR)}")
        if f"株価{N}" not in out:
            _fail(out)
        if _gap_lines(out):
            _fail(out)
        for ln in out.splitlines():
            if "run_daily locked" in ln and should_page("batch", ln):
                _fail(ln)
    finally:
        release.set()
        if t.ident is not None:
            t.join(timeout=3)
        db.close()
        eng.dispose()
    if result.get("holder") != "ok":
        _fail(f"holder: {result!r}")


def test_run_daily_leaves_zero_prices_when_lock_outlasts_retries() -> None:
    path = Path(tempfile.mkdtemp(prefix="daily-lock-fail-")) / "stock.db"
    timeout_s = 0.15
    eng, Session = _engine(path, timeout_s)
    db = Session()
    release = threading.Event()
    started = threading.Event()
    result: dict = {}
    sleeps: list[float] = []
    t = threading.Thread(target=_hold, args=(path, timeout_s, release, started, result))
    try:
        db.execute(text("SELECT 1"))
        db.commit()
        _seed(db, PRIOR, N)
        t.start()
        if not started.wait(2):
            _fail(f"holder did not lock: {result!r}")
        buf = io.StringIO()

        def sleeper(delay: float) -> None:
            sleeps.append(delay)
            time.sleep(delay)

        with patch("app.market.ingest.JQuantsClient", _client(N, [])), redirect_stdout(buf):
            try:
                run_daily(
                    db, DAY.isoformat(), attempts=2, sleep=sleeper,
                    lock_sleep_s=0.02, lock_sleep_cap_s=0.02,
                )
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    _fail(exc)
            else:
                _fail("lock that outlasts retries must fail the daily run")
        if _count(db, DAY) != 0:
            _fail(f"prices were written despite the lock: {_count(db, DAY)}")
        if len(sleeps) != 1:
            _fail(f"retries: {sleeps} {buf.getvalue()}")
        line = report_price_gap(db, DAY, zero_is_gap=True)
        if line is None or not should_page("batch", line):
            _fail(line)
        if "count=0" not in line or "prior_median=40" not in line:
            _fail(line)
        if "database is locked" in line or line.startswith("fail cron"):
            _fail(line)
        if _count(db, DAY) != 0:
            _fail("counting the gap wrote prices")
    finally:
        release.set()
        if t.ident is not None:
            t.join(timeout=3)
        db.close()
        eng.dispose()
    if result.get("holder") != "ok":
        _fail(f"holder: {result!r}")


def test_run_daily_does_not_retry_a_non_lock_error() -> None:
    init_db()
    db = SessionLocal()
    calls: list[str] = []
    sleeps: list[float] = []
    try:
        _wipe(db)
        with patch("app.market.ingest.JQuantsClient", _client(N, calls, boom=RuntimeError("api down"))):
            try:
                run_daily(db, DAY.isoformat(), sleep=lambda delay: sleeps.append(delay))
            except RuntimeError as exc:
                if str(exc) != "api down":
                    _fail(exc)
            else:
                _fail("api failure must propagate")
        if calls != ["master"] or sleeps:
            _fail(f"retried a non-lock error: {calls} {sleeps}")
        if _count(db, DAY) != 0:
            _fail(_count(db, DAY))
    finally:
        db.close()


def test_price_gap_line_matches_a_large_hole_only() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        if price_gap_line(db, DAY, zero_is_gap=True) is not None:
            _fail("no prior days")
        _seed(db, date(2026, 11, 16), 8)
        _seed(db, date(2026, 11, 17), N)
        _seed(db, date(2026, 11, 18), N)
        _seed(db, date(2026, 11, 19), N)
        _seed(db, PRIOR, N)
        missing = price_gap_line(db, DAY, zero_is_gap=True)
        if missing != "gap daily_prices date=2026-11-24 count=0 prior_median=40 prior_days=5":
            _fail(missing)
        if not should_page("batch", missing or ""):
            _fail(missing)
        _seed(db, DAY, 19)
        below = price_gap_line(db, DAY, zero_is_gap=True)
        if below is None or "count=19" not in below or "prior_median=40" not in below:
            _fail(below)
        db.query(DailyPrice).filter(DailyPrice.date == DAY).delete()
        db.commit()
        _seed(db, DAY, 20)
        if price_gap_line(db, DAY, zero_is_gap=True) is not None:
            _fail("half the prior median is not a large hole")
        db.query(DailyPrice).filter(DailyPrice.date == DAY).delete()
        db.commit()
        _seed(db, DAY, N - 1)
        if price_gap_line(db, DAY, zero_is_gap=False) is not None:
            _fail("a nearly full day must not page")
        if price_gap_line(db, date(2026, 11, 28), zero_is_gap=True) is not None:
            _fail("saturday")
        db.query(DailyPrice).filter(DailyPrice.date == DAY).delete()
        db.commit()
        if price_gap_line(db, DAY, zero_is_gap=False) is not None:
            _fail("a successful empty day is a closed market")
    finally:
        db.close()


def _quiet(stack: ExitStack) -> None:
    stack.enter_context(patch(
        "jobs.market.run_batch.analytics.indicators_current", lambda db: True,
    ))
    stack.enter_context(patch("jobs.market.run_batch.analytics.price_frame", lambda db: None))
    stack.enter_context(patch(
        "jobs.market.run_batch.analytics.compute_indicators", lambda db, px=None: None,
    ))
    stack.enter_context(patch(
        "jobs.market.run_batch.analytics.compute_earnings_events", lambda db, px=None: None,
    ))
    stack.enter_context(patch("jobs.market.run_batch.sync", lambda db, today: 0))


def _batch(argv: list[str], run, *, quiet: bool) -> tuple[int, str]:
    from jobs.market.run_batch import main as batch_main

    buf = io.StringIO()
    code = 0
    with ExitStack() as stack:
        stack.enter_context(patch("jobs.market.run_batch.ingest.run_daily", run))
        if quiet:
            _quiet(stack)
        stack.enter_context(patch("sys.argv", argv))
        stack.enter_context(redirect_stdout(buf))
        try:
            batch_main()
        except SystemExit as exc:
            code = int(exc.code or 0)
        except RuntimeError:
            code = 1
            if "boom" not in str(sys.exc_info()[1]):
                raise
    return code, buf.getvalue()


def test_weekday_failure_before_prices_pages_the_hole() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)

        def boom(db, day=None, **kwargs):
            raise RuntimeError("boom")

        code, out = _batch(["run_batch.py", "--date", DAY.isoformat()], boom, quiet=False)
        if code != 1:
            _fail(f"exit {code}: {out}")
        lines = _gap_lines(out)
        if len(lines) != 1 or not should_page("batch", lines[0]):
            _fail(out)
        if "count=0" not in lines[0] or "prior_median=40" not in lines[0]:
            _fail(lines[0])
        if "database is locked" in lines[0]:
            _fail(lines[0])
        if _count(db, DAY) != 0:
            _fail(_count(db, DAY))
    finally:
        db.close()


def test_failure_after_a_full_day_does_not_page_a_hole() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)
        _seed(db, DAY, N)

        def boom(db, day=None, **kwargs):
            raise RuntimeError("boom")

        code, out = _batch(["run_batch.py", "--date", DAY.isoformat()], boom, quiet=False)
        if code != 1 or _gap_lines(out):
            _fail(out)
    finally:
        db.close()


def test_weekend_failure_does_not_page_a_hole() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)

        def boom(db, day=None, **kwargs):
            raise RuntimeError("boom")

        code, out = _batch(["run_batch.py", "--date", "2026-11-28"], boom, quiet=False)
        if code != 1 or _gap_lines(out):
            _fail(out)
    finally:
        db.close()


def test_successful_empty_day_does_not_page() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)
        code, out = _batch(
            ["run_batch.py", "--date", DAY.isoformat()], lambda db, day=None, **kwargs: 0, quiet=True,
        )
        if code != 0 or "完了" not in out or _gap_lines(out):
            _fail(out)
    finally:
        db.close()


def test_successful_partial_day_pages_and_exits() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)

        def partial(db, day=None, **kwargs):
            _seed(db, DAY, 10)
            return 10

        code, out = _batch(["run_batch.py", "--date", DAY.isoformat()], partial, quiet=True)
        lines = _gap_lines(out)
        if code != 1 or len(lines) != 1 or not should_page("batch", lines[0]):
            _fail(f"code={code} {out}")
        if "count=10" not in lines[0] or "prior_median=40" not in lines[0]:
            _fail(lines[0])
    finally:
        db.close()


def test_successful_full_day_exits_zero() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)

        def full(db, day=None, **kwargs):
            _seed(db, DAY, N)
            return N

        code, out = _batch(["run_batch.py", "--date", DAY.isoformat()], full, quiet=True)
        if code != 0 or "完了" not in out or _gap_lines(out):
            _fail(f"code={code} {out}")
        if _count(db, DAY) != N:
            _fail(_count(db, DAY))
    finally:
        db.close()


def test_backfill_failure_does_not_page_a_price_hole() -> None:
    init_db()
    db = SessionLocal()
    try:
        _wipe(db)
        _seed(db, PRIOR, N)

        def boom(db, years=5):
            raise RuntimeError("boom")

        from jobs.market.run_batch import main as batch_main

        buf = io.StringIO()
        with patch("jobs.market.run_batch.ingest.run_backfill", boom), \
                patch("sys.argv", ["run_batch.py", "--backfill", "1", "--date", DAY.isoformat()]), \
                redirect_stdout(buf):
            try:
                batch_main()
            except RuntimeError as exc:
                if str(exc) != "boom":
                    _fail(exc)
            else:
                _fail("backfill failure must propagate")
        if _gap_lines(buf.getvalue()):
            _fail(buf.getvalue())
    finally:
        db.close()


def test_gap_line_reaches_the_batch_log_as_its_own_page(tmp_path: Path) -> None:
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    py = bindir / "python"
    py.write_text(
        "#!/usr/bin/env python3\n"
        "print('2026-11-24: 銘柄1 / 株価0 / TOPIX0 / 決算0', flush=True)\n"
        "print('run_daily locked (attempt 1/10); retry in 5s', flush=True)\n"
        "print('gap daily_prices date=2026-11-24 count=0 prior_median=4219 prior_days=3',"
        " flush=True)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    env = os.environ.copy()
    env["CRON_APP_DIR"] = str(app)
    env["CRON_LOG_DIR"] = str(logs)
    env["CRON_METRICS_URL"] = ""
    env["TZ"] = "UTC"
    proc = subprocess.run(
        ["bash", str(ROOT / "deploy" / "cron-run.sh"), "jobs/market/run_batch.py"],
        cwd=app, env=env, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 1:
        _fail(f"exit {proc.returncode}: {proc.stdout!r} {proc.stderr!r}")
    if proc.stdout or proc.stderr:
        _fail(f"cron-run leaked stdio: {proc.stdout!r} {proc.stderr!r}")
    log = (logs / "run_batch.log").read_text(encoding="utf-8")
    gaps = _gap_lines(log)
    if len(gaps) != 1 or not should_page("batch", gaps[0]):
        _fail(log)
    if "count=0" not in gaps[0] or "prior_median=4219" not in gaps[0]:
        _fail(gaps[0])
    if "database is locked" in gaps[0] or "fail cron" in gaps[0]:
        _fail(gaps[0])
    for line in log.splitlines():
        if "株価0" in line and should_page("batch", line):
            _fail(line)
        if "run_daily locked" in line and should_page("batch", line):
            _fail(line)
    fails = [line for line in log.splitlines() if "fail cron " in line]
    if len(fails) != 1 or not should_page("batch", fails[0]):
        _fail(log)


def main() -> None:
    test_run_daily_writes_prices_when_other_writer_outlasts_one_busy_timeout()
    test_run_daily_leaves_zero_prices_when_lock_outlasts_retries()
    test_run_daily_does_not_retry_a_non_lock_error()
    test_price_gap_line_matches_a_large_hole_only()
    test_weekday_failure_before_prices_pages_the_hole()
    test_failure_after_a_full_day_does_not_page_a_hole()
    test_weekend_failure_does_not_page_a_hole()
    test_successful_empty_day_does_not_page()
    test_successful_partial_day_pages_and_exits()
    test_successful_full_day_exits_zero()
    test_backfill_failure_does_not_page_a_price_hole()
    with tempfile.TemporaryDirectory() as d:
        test_gap_line_reaches_the_batch_log_as_its_own_page(Path(d))
    print("ok: daily lock and price gap")


if __name__ == "__main__":
    main()

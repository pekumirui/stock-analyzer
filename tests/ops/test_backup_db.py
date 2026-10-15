"""backup_db の世代間引きと失敗時に残すこと。本番 VPS には出ない。"""
from __future__ import annotations

import gzip
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))

from backup_db import (  # noqa: E402
    BACKUP_NAME_RE,
    COMPRESSLEVEL,
    DAILY_KEEP,
    DEFAULT_DEST,
    WEEKLY_KEEP,
    backup_name,
    default_db_path,
    list_generations,
    main,
    prune_generations,
    quick_check,
    run,
    sqlite_backup,
    sqlite_sidecar_paths,
    tmp_dir,
)

JST = ZoneInfo("Asia/Tokyo")


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _tmp_leftovers(dest: Path) -> list[str]:
    folder = tmp_dir(dest)
    if not folder.exists():
        return []
    return sorted(p.name for p in folder.iterdir())


def _assert_tmp_empty(dest: Path) -> None:
    leftover = _tmp_leftovers(dest)
    if leftover:
        _fail(f".tmp leftover {leftover}")


def _gz_names(folder: Path) -> list[str]:
    return [p.name for p in list_generations(folder)]


def _seed_gz(folder: Path, days: list[str], payload: bytes = b"keep-me") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for day in days:
        (folder / backup_name(day)).write_bytes(payload)


def _make_db(path: Path, rows: list[str]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO items (name) VALUES (?)", [(name,) for name in rows])
        conn.commit()
    finally:
        conn.close()


def _gunzip_names(path: Path) -> list[str]:
    raw = gzip.decompress(path.read_bytes())
    tmp = path.with_name(path.name + ".check.db")
    tmp.write_bytes(raw)
    try:
        conn = sqlite3.connect(f"file:{tmp.resolve().as_posix()}?mode=ro", uri=True)
        try:
            return [row[0] for row in conn.execute("SELECT name FROM items ORDER BY id")]
        finally:
            conn.close()
    finally:
        tmp.unlink(missing_ok=True)
        for sidecar in sqlite_sidecar_paths(tmp)[1:]:
            sidecar.unlink(missing_ok=True)


def test_constants() -> None:
    if DEFAULT_DEST != Path("/home/stock/backups"):
        _fail(f"DEFAULT_DEST {DEFAULT_DEST!r}")
    if DAILY_KEEP != 7 or WEEKLY_KEEP != 4:
        _fail(f"keep daily={DAILY_KEEP} weekly={WEEKLY_KEEP}")
    if COMPRESSLEVEL != 1:
        _fail(f"COMPRESSLEVEL {COMPRESSLEVEL}")
    if backup_name("2026-10-10") != "stock-2026-10-10.db.gz":
        _fail("backup_name")
    if BACKUP_NAME_RE.fullmatch("stock-2026-10-10.db.gz") is None:
        _fail("name regex")
    if BACKUP_NAME_RE.fullmatch("notes.txt") is not None:
        _fail("notes must not match")


def test_default_db_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    got = default_db_path(environ={}, repo_root=repo)
    if got != repo / "stock.db":
        _fail(f"default {got}")
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'custom.db'}"}
    got = default_db_path(environ=env, repo_root=repo)
    if got != tmp_path / "custom.db":
        _fail(f"custom {got}")
    env = {"DATABASE_URL": "postgresql://localhost/stock"}
    got = default_db_path(environ=env, repo_root=repo)
    if got != repo / "stock.db":
        _fail(f"non-sqlite {got}")


def test_prune_keeps_newest_and_ignores_noise(tmp_path: Path) -> None:
    folder = tmp_path / "daily"
    days = [f"2026-10-{n:02d}" for n in range(3, 11)]
    _seed_gz(folder, days)
    (folder / "notes.txt").write_text("no", encoding="utf-8")
    (folder / "stock-foo.db.gz").write_bytes(b"x")
    removed = prune_generations(folder, 7)
    names = _gz_names(folder)
    if names != [backup_name(f"2026-10-{n:02d}") for n in range(10, 3, -1)]:
        _fail(f"kept {names}")
    if [p.name for p in removed] != [backup_name("2026-10-03")]:
        _fail(f"removed {[p.name for p in removed]}")
    if not (folder / "notes.txt").is_file() or not (folder / "stock-foo.db.gz").is_file():
        _fail("noise files must remain")


def test_wal_backup_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    # 最後の接続を閉じると SQLite が WAL を checkpoint するので、開きっぱなしで取る。
    live = sqlite3.connect(db)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        live.execute("INSERT INTO items (name) VALUES ('alpha')")
        live.commit()
        live.execute("INSERT INTO items (name) VALUES ('beta')")
        live.commit()
        wal = Path(str(db) + "-wal")
        if not wal.exists() or wal.stat().st_size == 0:
            _fail("expected WAL sidecar for backup coverage")
        friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
        rc = run(db, dest, now=friday)
        if rc != 0:
            _fail("roundtrip rc")
        _assert_tmp_empty(dest)
    finally:
        live.close()
    daily = dest / "daily" / backup_name("2026-10-09")
    if not daily.is_file():
        _fail("daily missing")
    if (dest / "weekly" / backup_name("2026-10-09")).exists():
        _fail("weekday must not write weekly")
    if _gunzip_names(daily) != ["alpha", "beta"]:
        _fail("roundtrip rows")
    raw = tmp_path / "raw.db"
    sqlite_backup(db, raw)
    ok, msg = quick_check(raw)
    if not ok:
        _fail(f"quick_check {msg}")
    for sidecar in sqlite_sidecar_paths(raw)[1:]:
        if sidecar.exists():
            _fail(f"backup dest sidecar {sidecar.name}")
    raw.unlink()


def test_sunday_keeps_weekly_and_prunes(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    daily_days = [f"2026-09-{n:02d}" for n in range(27, 31)] + [
        f"2026-10-{n:02d}" for n in range(1, 4)
    ]
    _seed_gz(dest / "daily", daily_days)
    weekly_days = ["2026-09-06", "2026-09-13", "2026-09-20", "2026-09-27"]
    _seed_gz(dest / "weekly", weekly_days)
    sunday = datetime(2026, 10, 4, 5, 0, tzinfo=JST)
    rc = run(db, dest, now=sunday)
    if rc != 0:
        _fail("sunday rc")
    daily_names = _gz_names(dest / "daily")
    if backup_name("2026-09-27") in daily_names:
        _fail(f"oldest daily remained {daily_names}")
    if len(daily_names) != DAILY_KEEP:
        _fail(f"daily count {daily_names}")
    if backup_name("2026-10-04") not in daily_names:
        _fail(f"today missing {daily_names}")
    weekly_names = _gz_names(dest / "weekly")
    if weekly_names != [
        backup_name("2026-10-04"),
        backup_name("2026-09-27"),
        backup_name("2026-09-20"),
        backup_name("2026-09-13"),
    ]:
        _fail(f"weekly {weekly_names}")
    daily = dest / "daily" / backup_name("2026-10-04")
    weekly = dest / "weekly" / backup_name("2026-10-04")
    if daily.stat().st_ino != weekly.stat().st_ino:
        _fail("sunday weekly should hardlink daily")
    _assert_tmp_empty(dest)


def test_failure_keeps_old_generations(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    daily_days = [f"2026-10-{n:02d}" for n in range(1, 9)]
    weekly_days = ["2026-09-13", "2026-09-20", "2026-09-27", "2026-10-04"]
    marker = b"original-generation"
    _seed_gz(dest / "daily", daily_days, marker)
    _seed_gz(dest / "weekly", weekly_days, marker)
    (dest / "daily" / "notes.txt").write_text("no", encoding="utf-8")

    def _bad_check(_path: Path) -> tuple[bool, str]:
        return False, "not ok"

    friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
    rc = run(db, dest, now=friday, check=_bad_check)
    if rc == 0:
        _fail("integrity fail must be non-zero")
    if _gz_names(dest / "daily") != [backup_name(d) for d in reversed(daily_days)]:
        _fail(f"daily pruned on fail {_gz_names(dest / 'daily')}")
    if _gz_names(dest / "weekly") != [backup_name(d) for d in reversed(weekly_days)]:
        _fail(f"weekly pruned on fail {_gz_names(dest / 'weekly')}")
    for day in daily_days:
        body = (dest / "daily" / backup_name(day)).read_bytes()
        if body != marker:
            _fail(f"daily {day} overwritten")
    if not (dest / "daily" / "notes.txt").is_file():
        _fail("noise removed on fail")
    if (dest / "daily" / backup_name("2026-10-09")).exists():
        _fail("failed run installed today's file")
    _assert_tmp_empty(dest)


def test_corrupt_backup_does_not_install(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    _seed_gz(dest / "daily", ["2026-10-08"], b"good")

    def _garbage(_src: Path, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"not a sqlite database")

    rc = run(
        db,
        dest,
        now=datetime(2026, 10, 9, 5, 0, tzinfo=JST),
        backup=_garbage,
    )
    if rc == 0:
        _fail("corrupt backup must fail")
    if _gz_names(dest / "daily") != [backup_name("2026-10-08")]:
        _fail(f"corrupt installed {_gz_names(dest / 'daily')}")
    if (dest / "daily" / backup_name("2026-10-08")).read_bytes() != b"good":
        _fail("old generation clobbered")
    _assert_tmp_empty(dest)


def test_insufficient_disk_skips(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    _seed_gz(dest / "daily", ["2026-10-08"], b"good")
    rc = run(
        db,
        dest,
        now=datetime(2026, 10, 9, 5, 0, tzinfo=JST),
        free_bytes=lambda _path: 0,
    )
    if rc == 0:
        _fail("disk short must fail")
    if _gz_names(dest / "daily") != [backup_name("2026-10-08")]:
        _fail(f"disk short pruned {_gz_names(dest / 'daily')}")
    if (dest / "daily" / backup_name("2026-10-09")).exists():
        _fail("disk short still wrote today")
    _assert_tmp_empty(dest)


def test_missing_db_keeps_generations(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    _seed_gz(dest / "daily", ["2026-10-08"], b"good")
    rc = run(tmp_path / "missing.db", dest, now=datetime(2026, 10, 9, 5, 0, tzinfo=JST))
    if rc == 0:
        _fail("missing db must fail")
    if _gz_names(dest / "daily") != [backup_name("2026-10-08")]:
        _fail("missing db pruned")
    _assert_tmp_empty(dest)


def test_main_missing_is_nonzero(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    rc = main(["--db", str(tmp_path / "no.db"), "--dest", str(dest)])
    if rc == 0:
        _fail("main missing db must be non-zero")
    _assert_tmp_empty(dest)


def test_cleans_stale_tmp_sidecars(tmp_path: Path) -> None:
    """本番初回で残った stock-*.db-wal / .db-shm を次の実行で消す。"""
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    stale = dest / ".tmp"
    stale.mkdir(parents=True)
    (stale / "stock-2026-10-10.875585.db-wal").write_bytes(b"")
    (stale / "stock-2026-10-10.875585.db-shm").write_bytes(b"\0" * 32768)
    (stale / "stock-2026-10-10.875585.db-journal").write_bytes(b"x")
    rc = run(db, dest, now=datetime(2026, 10, 10, 5, 0, tzinfo=JST))
    if rc != 0:
        _fail("stale sidecar run rc")
    _assert_tmp_empty(dest)


def test_crontab_and_docs() -> None:
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    want = "0 5 * * * cd /home/stock/stock-analyzer && deploy/cron-run.sh jobs/backup_db.py"
    if want not in crontab:
        _fail("crontab missing 05:00 JST backup_db entry")
    if "cp -a" in crontab:
        _fail("crontab must not keep the cp backup")
    if ">>" in want:
        _fail("crontab must not redirect")
    provision = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    if 'crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"' not in provision:
        _fail("provision.sh must install deploy/crontab")
    if '"$STOCK_HOME/backups"' not in provision:
        _fail("provision.sh must create /home/stock/backups")
    src = (ROOT / "jobs" / "backup_db.py").read_text(encoding="utf-8")
    for needle in ("from app", "import app", "rclone", "r2-share"):
        if needle in src:
            _fail(f"backup job must not reference {needle}")
    design = (ROOT / "docs" / "DESIGN_backup.md").read_text(encoding="utf-8")
    for needle in ("gzip -dc", "systemctl stop stock-analyzer", "/home/stock/backups/daily"):
        if needle not in design:
            _fail(f"restore doc missing {needle}")


def main_tests() -> None:
    test_constants()
    test_crontab_and_docs()
    with TemporaryDirectory() as d:
        test_default_db_path(Path(d))
    with TemporaryDirectory() as d:
        test_prune_keeps_newest_and_ignores_noise(Path(d))
    with TemporaryDirectory() as d:
        test_wal_backup_roundtrip(Path(d))
    with TemporaryDirectory() as d:
        test_sunday_keeps_weekly_and_prunes(Path(d))
    with TemporaryDirectory() as d:
        test_failure_keeps_old_generations(Path(d))
    with TemporaryDirectory() as d:
        test_corrupt_backup_does_not_install(Path(d))
    with TemporaryDirectory() as d:
        test_insufficient_disk_skips(Path(d))
    with TemporaryDirectory() as d:
        test_missing_db_keeps_generations(Path(d))
    with TemporaryDirectory() as d:
        test_main_missing_is_nonzero(Path(d))
    with TemporaryDirectory() as d:
        test_cleans_stale_tmp_sidecars(Path(d))
    print("ok: backup_db")


if __name__ == "__main__":
    main_tests()

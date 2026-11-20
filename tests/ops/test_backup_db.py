"""backup_db は送信できたら今回のローカルだけ消し、失敗なら残す。本番 VPS には出ない。"""
from __future__ import annotations

import errno
import fcntl
import gzip
import io
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.offsite_settings import (
    DEFAULT_OFFSITE_PATH,
    DEFAULT_OFFSITE_REMOTE,
    BackupError,
    OffsiteSettings,
    offsite_settings_from_env,
)
from jobs.ops.backup_db import (
    BACKUP_NAME_RE,
    COMPRESSLEVEL,
    DEFAULT_DEST,
    OFFSITE_DAILY_KEEP,
    OFFSITE_WEEKLY_KEEP,
    BackupKeep,
    BackupLimits,
    CmdResult,
    OffsiteSkip,
    backup_name,
    clip_cmd_output,
    default_db_path,
    keep_from_env,
    Deadline,
    list_generations,
    listed_remotes,
    main,
    required_bytes,
    run,
    source_bytes,
    sync_offsite,
    tmp_dir,
    write_consistent_gzip,
)
from jobs.ops.backup_store import BlockStore, StoreError

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


def _gunzip_bytes(payload: bytes, dest: Path) -> list[str]:
    path = dest / "roundtrip.db.gz"
    path.write_bytes(payload)
    try:
        return _gunzip_names(path)
    finally:
        path.unlink(missing_ok=True)


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
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(tmp) + suffix).unlink(missing_ok=True)


class MemoryOffsite:
    """rclone の代わり。remote[kind][name] = bytes。"""

    def __init__(self, *, skip: str | None = None, fail: str | None = None) -> None:
        self.remote: dict[str, dict[str, bytes]] = {"daily": {}, "weekly": {}}
        self.skip = skip
        self.fail = fail
        self.calls: list[str] = []

    def seed(self, kind: str, days: list[str], payload: bytes = b"old-remote") -> None:
        folder = self.remote.setdefault(kind, {})
        for day in days:
            folder[backup_name(day)] = payload

    def __call__(self, items: list[tuple[Path, str]], keep: BackupKeep) -> None:
        if self.skip:
            raise OffsiteSkip(self.skip)
        if self.fail == "copy":
            raise BackupError("rclone copy failed")
        for path, kind in items:
            self.calls.append(f"copy:{kind}:{path.name}")
            if self.fail == "check":
                raise BackupError("rclone check failed")
            self.remote.setdefault(kind, {})[path.name] = path.read_bytes()
            self.calls.append(f"check:{kind}:{path.name}")
        if self.fail == "prune":
            raise BackupError("rclone prune failed")
        for kind, count in (("daily", keep.offsite_daily), ("weekly", keep.offsite_weekly)):
            folder = self.remote.setdefault(kind, {})
            named: list[tuple[str, str]] = []
            for name in list(folder):
                match = BACKUP_NAME_RE.fullmatch(name)
                if match is None:
                    continue
                named.append((match.group(1), name))
            named.sort(key=lambda item: item[0], reverse=True)
            for _, name in named[count:]:
                del folder[name]
                self.calls.append(f"delete:{kind}:{name}")

    def names(self, kind: str) -> list[str]:
        folder = self.remote.get(kind, {})
        found: list[tuple[str, str]] = []
        for name in folder:
            match = BACKUP_NAME_RE.fullmatch(name)
            if match is None:
                continue
            found.append((match.group(1), name))
        found.sort(key=lambda item: item[0], reverse=True)
        return [name for _, name in found]


def _ok_offsite() -> MemoryOffsite:
    return MemoryOffsite()


def _run_out(**kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(**kwargs)
    return rc, buf.getvalue()


def test_constants() -> None:
    if DEFAULT_DEST != Path("/home/stock/backups"):
        _fail(f"DEFAULT_DEST {DEFAULT_DEST!r}")
    if OFFSITE_DAILY_KEEP != 7 or OFFSITE_WEEKLY_KEEP != 4:
        _fail(f"offsite keep daily={OFFSITE_DAILY_KEEP} weekly={OFFSITE_WEEKLY_KEEP}")
    if DEFAULT_OFFSITE_REMOTE != "onedrive" or DEFAULT_OFFSITE_PATH != "stock-analyzer-backups":
        _fail("offsite dest")
    if COMPRESSLEVEL != 1:
        _fail(f"COMPRESSLEVEL {COMPRESSLEVEL}")
    if backup_name("2026-10-10") != "stock-2026-10-10.db.gz":
        _fail("backup_name")
    if BACKUP_NAME_RE.fullmatch("stock-2026-10-10.db.gz") is None:
        _fail("name regex")
    if BACKUP_NAME_RE.fullmatch("notes.txt") is not None:
        _fail("notes must not match")


def test_keep_and_offsite_from_env() -> None:
    got = keep_from_env({})
    if got != BackupKeep():
        _fail(f"defaults {got}")
    got = keep_from_env(
        {
            "BACKUP_OFFSITE_DAILY_KEEP": "9",
            "BACKUP_OFFSITE_WEEKLY_KEEP": "5",
        }
    )
    if got != BackupKeep(offsite_daily=9, offsite_weekly=5):
        _fail(f"override {got}")
    try:
        keep_from_env({"BACKUP_OFFSITE_DAILY_KEEP": "0"})
    except BackupError:
        pass
    else:
        _fail("keep 0 must fail")
    settings = offsite_settings_from_env({})
    if settings.remote != "onedrive" or settings.root != "stock-analyzer-backups":
        _fail(f"settings {settings}")
    if not settings.enabled:
        _fail("enabled default")
    settings = offsite_settings_from_env(
        {
            "BACKUP_OFFSITE_REMOTE": "onedrive:",
            "BACKUP_OFFSITE_PATH": "/bucket/backups/",
            "BACKUP_OFFSITE": "0",
        }
    )
    if settings.remote != "onedrive" or settings.root != "bucket/backups":
        _fail(f"normalized {settings}")
    if settings.enabled:
        _fail("BACKUP_OFFSITE=0")
    if settings.dest("daily") != "onedrive:bucket/backups/daily":
        _fail(settings.dest("daily"))
    if listed_remotes("onedrive:\nr2:\n") != {"onedrive", "r2"}:
        _fail("listed_remotes")
    if clip_cmd_output("a\nb") != "a b":
        _fail("clip")


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


def test_wal_backup_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
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
        offsite = _ok_offsite()
        friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
        rc = run(db, dest, now=friday, offsite=offsite)
        if rc != 0:
            _fail("roundtrip rc")
        _assert_tmp_empty(dest)
    finally:
        live.close()
    name = backup_name("2026-10-09")
    if (dest / "daily" / name).exists():
        _fail("success must remove local daily")
    if (dest / "weekly" / name).exists():
        _fail("weekday must not write weekly")
    payload = offsite.remote["daily"].get(name)
    if payload is None:
        _fail("remote daily missing")
    if _gunzip_bytes(payload, tmp_path) != ["alpha", "beta"]:
        _fail("roundtrip rows")
    out = tmp_path / "out.db"
    out.write_bytes(gzip.decompress(payload))
    checked = sqlite3.connect(out)
    try:
        ok = checked.execute("PRAGMA quick_check").fetchone()
        if not ok or ok[0] != "ok":
            _fail(f"quick_check {ok}")
        if checked.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            _fail("journal")
    finally:
        checked.close()


def test_upload_success_drops_this_run_and_keeps_older_local(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    daily_days = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"]
    weekly_days = ["2026-09-13", "2026-09-20", "2026-09-27"]
    marker = b"already-on-vps"
    _seed_gz(dest / "daily", daily_days, marker)
    _seed_gz(dest / "weekly", weekly_days, marker)
    (dest / "daily" / "notes.txt").write_text("no", encoding="utf-8")
    offsite = MemoryOffsite()
    offsite.seed("daily", daily_days)
    offsite.seed("weekly", weekly_days)
    offsite.remote["daily"]["notes.txt"] = b"leave-me"
    keep = BackupKeep(offsite_daily=3, offsite_weekly=2)
    sunday = datetime(2026, 10, 4, 5, 0, tzinfo=JST)
    today = backup_name("2026-10-04")
    rc, out = _run_out(db=db, dest=dest, now=sunday, offsite=offsite, keep=keep)
    if rc != 0:
        _fail(f"sunday rc {out}")
    if _gz_names(dest / "daily") != [backup_name(d) for d in reversed(daily_days)]:
        _fail(f"local daily {_gz_names(dest / 'daily')}")
    if _gz_names(dest / "weekly") != [backup_name(d) for d in reversed(weekly_days)]:
        _fail(f"local weekly {_gz_names(dest / 'weekly')}")
    if (dest / "daily" / today).exists() or (dest / "weekly" / today).exists():
        _fail("success must remove this run's gzip")
    for day in daily_days:
        if (dest / "daily" / backup_name(day)).read_bytes() != marker:
            _fail(f"older daily {day} changed")
    for day in weekly_days:
        if (dest / "weekly" / backup_name(day)).read_bytes() != marker:
            _fail(f"older weekly {day} changed")
    if not (dest / "daily" / "notes.txt").is_file():
        _fail("local noise removed")
    if "weekly link" not in out:
        _fail(f"sunday should hardlink {out}")
    daily_payload = offsite.remote["daily"].get(today)
    weekly_payload = offsite.remote["weekly"].get(today)
    if daily_payload is None or daily_payload != weekly_payload:
        _fail("remote sunday copies differ")
    if offsite.names("daily") != [today, backup_name("2026-10-03"), backup_name("2026-10-02")]:
        _fail(f"remote daily {offsite.names('daily')}")
    if offsite.names("weekly") != [today, backup_name("2026-09-27")]:
        _fail(f"remote weekly {offsite.names('weekly')}")
    if offsite.remote["daily"].get("notes.txt") != b"leave-me":
        _fail("remote noise deleted")
    if "removed local daily=1 weekly=1 offsite=ok" not in out:
        _fail(f"missing removal line {out}")
    _assert_tmp_empty(dest)


def test_offsite_failure_keeps_local_generations(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    daily_days = [f"2026-10-{n:02d}" for n in range(1, 9)]
    weekly_days = ["2026-09-13", "2026-09-20", "2026-09-27", "2026-10-04"]
    marker = b"original-generation"
    _seed_gz(dest / "daily", daily_days, marker)
    _seed_gz(dest / "weekly", weekly_days, marker)
    (dest / "daily" / "notes.txt").write_text("no", encoding="utf-8")
    friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
    for stage in ("copy", "check", "prune"):
        offsite = MemoryOffsite(fail=stage)
        rc, out = _run_out(db=db, dest=dest, now=friday, offsite=offsite)
        if rc == 0:
            _fail(f"{stage} must be non-zero")
        if "fail backup_db:" not in out:
            _fail(f"{stage} must print fail {out}")
        local_daily = _gz_names(dest / "daily")
        expected = [backup_name("2026-10-09")] + [backup_name(d) for d in reversed(daily_days)]
        if local_daily != expected:
            _fail(f"{stage} local daily {local_daily}")
        if _gz_names(dest / "weekly") != [backup_name(d) for d in reversed(weekly_days)]:
            _fail(f"{stage} weekly pruned")
        for day in daily_days:
            body = (dest / "daily" / backup_name(day)).read_bytes()
            if body != marker:
                _fail(f"{stage} daily {day} overwritten")
        if not (dest / "daily" / "notes.txt").is_file():
            _fail(f"{stage} noise removed")
        today = dest / "daily" / backup_name("2026-10-09")
        if not today.is_file():
            _fail(f"{stage} today's local file missing")
        _assert_tmp_empty(dest)


def test_offsite_skip_keeps_local_and_exits_zero(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    _seed_gz(dest / "daily", ["2026-10-08"], b"old")
    friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
    offsite = MemoryOffsite(skip="rclone not installed")
    rc, out = _run_out(db=db, dest=dest, now=friday, offsite=offsite)
    if rc != 0:
        _fail(f"skip must succeed {out}")
    if "fail backup_db:" in out:
        _fail(f"skip must not fail {out}")
    if "warn backup_db offsite skipped" not in out:
        _fail(f"skip warning {out}")
    names = _gz_names(dest / "daily")
    if names != [backup_name("2026-10-09"), backup_name("2026-10-08")]:
        _fail(f"skip pruned {names}")
    _assert_tmp_empty(dest)

    rc, out = _run_out(
        db=db,
        dest=dest,
        now=datetime(2026, 10, 10, 5, 0, tzinfo=JST),
        offsite=False,
    )
    if rc != 0 or "offsite skipped" not in out:
        _fail(f"--no-offsite path {rc} {out}")
    if backup_name("2026-10-08") not in _gz_names(dest / "daily"):
        _fail("disabled offsite pruned old")


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

    def _bad(_src: Path, _part: Path) -> None:
        raise BackupError("quick_check not ok")

    friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
    rc = run(db, dest, now=friday, snapshot=_bad, offsite=_ok_offsite())
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
        snapshot=_garbage,
        offsite=_ok_offsite(),
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
        offsite=_ok_offsite(),
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
    rc = run(
        tmp_path / "missing.db",
        dest,
        now=datetime(2026, 10, 9, 5, 0, tzinfo=JST),
        offsite=_ok_offsite(),
    )
    if rc == 0:
        _fail("missing db must fail")
    if _gz_names(dest / "daily") != [backup_name("2026-10-08")]:
        _fail("missing db pruned")
    _assert_tmp_empty(dest)


def test_main_missing_is_nonzero(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    rc = main(["--db", str(tmp_path / "no.db"), "--dest", str(dest), "--no-offsite"])
    if rc == 0:
        _fail("main missing db must be non-zero")
    _assert_tmp_empty(dest)


def test_cleans_stale_tmp_sidecars(tmp_path: Path) -> None:
    """本番初回で残った stock-*.db-wal / .db-shm を次の実行で消す。"""
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    _seed_gz(dest / "daily", ["2026-10-09"], b"older")
    stale = dest / ".tmp"
    stale.mkdir(parents=True)
    (stale / "stock-2026-10-10.875585.db-wal").write_bytes(b"")
    (stale / "stock-2026-10-10.875585.db-shm").write_bytes(b"\0" * 32768)
    (stale / "stock-2026-10-10.875585.db-journal").write_bytes(b"x")
    rc = run(
        db,
        dest,
        now=datetime(2026, 10, 10, 5, 0, tzinfo=JST),
        offsite=_ok_offsite(),
    )
    if rc != 0:
        _fail("stale sidecar run rc")
    if (dest / "daily" / backup_name("2026-10-10")).exists():
        _fail("success must remove this run's gzip")
    if (dest / "daily" / backup_name("2026-10-09")).read_bytes() != b"older":
        _fail("older local gzip removed")
    _assert_tmp_empty(dest)


def test_rclone_argv_copy_check_prune(tmp_path: Path) -> None:
    gz = tmp_path / "stock-2026-10-09.db.gz"
    gz.write_bytes(b"payload-bytes")
    calls: list[list[str]] = []
    settings = OffsiteSettings(remote="remote-a", root="root-b")
    keep = BackupKeep(offsite_daily=2, offsite_weekly=2)
    listed = [backup_name(f"2026-10-0{n}") for n in (1, 2, 3)] + [gz.name]

    def fake_run(argv: list[str], timeout: float) -> CmdResult:
        calls.append(list(argv))
        cmd = argv[0]
        if cmd == "listremotes":
            return CmdResult(0, "remote-a:\nother:\n", "")
        if cmd == "copy":
            return CmdResult(0, "", "")
        if cmd == "check":
            return CmdResult(0, "", "")
        if cmd == "lsf":
            if settings.dest("weekly") in argv:
                return CmdResult(0, "notes.txt\n", "")
            extra = "\n".join(listed) + "\nnotes.txt\nstock-foo.db.gz\n"
            return CmdResult(0, extra, "")
        if cmd == "deletefile":
            return CmdResult(0, "", "")
        _fail(f"unexpected rclone {argv!r}")
        return CmdResult(1, "", "unexpected")

    sync_offsite(
        [(gz, "daily")],
        keep=keep,
        settings=settings,
        which=lambda _name: "/usr/bin/rclone",
        run=fake_run,
    )
    cmds = [c[0] for c in calls]
    if cmds[0] != "listremotes" or "copy" not in cmds or "check" not in cmds:
        _fail(f"cmds {cmds}")
    copy = next(c for c in calls if c[0] == "copy")
    if copy[1] != str(gz) or copy[2] != settings.dest("daily"):
        _fail(f"copy argv {copy}")
    check = next(c for c in calls if c[0] == "check")
    if "--include" not in check or f"/{gz.name}" not in check or "--one-way" not in check:
        _fail(f"check argv {check}")
    deletes = [c[1] for c in calls if c[0] == "deletefile"]
    if any("notes.txt" in d or "stock-foo.db.gz" in d for d in deletes):
        _fail(f"deleted noise {deletes}")
    expect_deleted = {
        f"{settings.dest('daily')}/{backup_name('2026-10-01')}",
        f"{settings.dest('daily')}/{backup_name('2026-10-02')}",
    }
    if set(deletes) != expect_deleted:
        _fail(f"prune deletes {deletes}")
    if not gz.is_file():
        _fail("rclone helper must not delete the local gzip")


def test_rclone_missing_remote_skips() -> None:
    settings = OffsiteSettings(remote="remote-a", root="root-b")

    def fake_run(argv: list[str], timeout: float) -> CmdResult:
        if argv[0] == "listremotes":
            return CmdResult(0, "other:\n", "")
        _fail(f"should not run {argv!r}")
        return CmdResult(1, "", "")

    try:
        sync_offsite(
            [],
            keep=BackupKeep(offsite_daily=2, offsite_weekly=2),
            settings=settings,
            which=lambda _name: "/usr/bin/rclone",
            run=fake_run,
        )
    except OffsiteSkip as exc:
        if "missing" not in str(exc):
            _fail(exc)
    else:
        _fail("missing remote must skip")


def test_rclone_check_falls_back_to_size(tmp_path: Path) -> None:
    gz = tmp_path / "stock-2026-10-09.db.gz"
    gz.write_bytes(b"abc")
    settings = OffsiteSettings(remote="remote-a", root="root-b")

    def fake_run(argv: list[str], timeout: float) -> CmdResult:
        cmd = argv[0]
        if cmd == "listremotes":
            return CmdResult(0, f"{settings.remote}:\n", "")
        if cmd == "copy":
            if argv[2] != settings.dest("daily"):
                _fail(f"copy dest {argv}")
            return CmdResult(0, "", "")
        if cmd == "check":
            return CmdResult(1, "", "NOTICE: hash not supported")
        if cmd == "lsjson":
            return CmdResult(0, '[{"Name":"x","Size":3}]', "")
        if cmd == "lsf":
            return CmdResult(1, "", "directory not found")
        _fail(f"unexpected {argv!r}")
        return CmdResult(1, "", "")

    sync_offsite(
        [(gz, "daily")],
        keep=BackupKeep(offsite_daily=2, offsite_weekly=2),
        settings=settings,
        which=lambda _name: "/usr/bin/rclone",
        run=fake_run,
    )


def test_provision_and_isolation() -> None:
    provision = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    if 'crontab -u "$STOCK_USER" "$APP_DIR/deploy/crontab"' not in provision:
        _fail("provision.sh must install deploy/crontab")
    if '"$STOCK_HOME/backups"' not in provision:
        _fail("provision.sh must create /home/stock/backups")
    src = (ROOT / "jobs" / "ops" / "backup_db.py").read_text(encoding="utf-8")
    for needle in ("r2-share", "rclone.conf"):
        if needle in src:
            _fail(f"backup job must not reference {needle}")
    if "rclone" not in src:
        _fail("backup job must call rclone")


def _stage_int(out: str, marker: str, key: str) -> int:
    for line in out.splitlines():
        if marker not in line:
            continue
        for part in line.split():
            if part.startswith(key + "="):
                return int(part.split("=", 1)[1])
    _fail(f"missing {marker} {key} in {out}")
    return 0


def test_required_bytes_stays_under_one_database() -> None:
    src = 17_058_856_960
    need = required_bytes(src)
    if need >= src:
        _fail(f"need {need} is not under one copy")
    if need >= 14 * 1024**3:
        _fail(f"need {need} is still too large")


def test_store_stops_at_its_budget(tmp_path: Path) -> None:
    store = BlockStore(tmp_path / "x.blk", budget=4096)
    try:
        try:
            store.write(os.urandom(256 * 1024), 0)
            store.flush()
        except StoreError as exc:
            if "budget" not in str(exc):
                _fail(exc)
        else:
            _fail("budget must trip")
    finally:
        store.close()


def test_snapshot_is_smaller_than_the_db_and_offsite_ok_deletes_local(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany(
            "INSERT INTO items (name) VALUES (?)",
            [(f"{i % 4000}|{i * 3 % 80000}|{'px' * 30}",) for i in range(80_000)],
        )
        conn.commit()
        src = source_bytes(db)
        if src < 2_000_000:
            _fail(f"fixture too small {src}")
        held: dict[str, bytes] = {}

        def fake_run(argv: list[str], timeout: float) -> CmdResult:
            cmd = argv[0]
            if cmd == "listremotes":
                return CmdResult(0, "onedrive:\n", "")
            if cmd == "copy":
                held["gz"] = Path(argv[1]).read_bytes()
                return CmdResult(0, "", "")
            if cmd in ("check", "lsf"):
                return CmdResult(0, "", "")
            _fail(f"unexpected rclone {argv}")
            return CmdResult(1, "", "")

        def offsite(items: list[tuple[Path, str]], keep: BackupKeep) -> None:
            sync_offsite(
                items,
                keep=keep,
                settings=offsite_settings_from_env({}),
                which=lambda _name: "/usr/bin/rclone",
                run=fake_run,
            )

        friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
        rc, out = _run_out(db=db, dest=dest, now=friday, offsite=offsite)
        if rc != 0:
            _fail(out)
        copy_bytes = _stage_int(out, "stage=copy done", "bytes")
        gzip_bytes = _stage_int(out, "stage=gzip done", "bytes")
        if copy_bytes >= src or gzip_bytes >= src:
            _fail(f"temp copy={copy_bytes} gzip={gzip_bytes} src={src}")
        if "stage=quick_check done ok" not in out or "offsite ok daily" not in out:
            _fail(out)
        if "removed local daily=1" not in out or "offsite=ok" not in out:
            _fail(out)
        name = backup_name("2026-10-09")
        if (dest / "daily" / name).exists():
            _fail("success must remove local daily")
        if "gz" not in held:
            _fail("copy did not see the gzip")
        raw = gzip.decompress(held["gz"])
        out_db = tmp_path / "out.db"
        out_db.write_bytes(raw)
        checked = sqlite3.connect(f"file:{out_db.resolve().as_posix()}?mode=ro", uri=True)
        try:
            ok = checked.execute("PRAGMA quick_check").fetchone()
            if not ok or ok[0] != "ok":
                _fail(f"roundtrip quick_check {ok}")
            if checked.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                _fail("roundtrip journal")
            count = checked.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            if count < 80_000:
                _fail(f"roundtrip rows {count}")
        finally:
            checked.close()
        _assert_tmp_empty(dest)
    finally:
        conn.close()


def test_writer_commits_during_backup(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    blob = os.urandom(2048)
    conn.executemany(
        "INSERT INTO items (name) VALUES (?)",
        [(f"base-{i}-{blob.hex()}",) for i in range(4_000)],
    )
    conn.commit()
    base_count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()
    script = tmp_path / "writer.py"
    script.write_text(
        "import sqlite3, time\n"
        f"db = {str(db)!r}\n"
        "c = sqlite3.connect(db, timeout=5)\n"
        "c.execute('PRAGMA busy_timeout=3000')\n"
        "n = 0\n"
        "end = time.time() + 8\n"
        "while time.time() < end:\n"
        "    try:\n"
        "        c.execute('INSERT INTO items (name) VALUES (?)', (f'live-{n}',))\n"
        "        c.commit()\n"
        "        n += 1\n"
        "    except sqlite3.OperationalError:\n"
        "        time.sleep(0.01)\n"
        "c.close()\n"
        "print(n)\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    time.sleep(0.2)
    offsite = _ok_offsite()
    limits = BackupLimits(overall_s=30, copy_s=4, check_s=20, gzip_s=20, offsite_copy_s=20, offsite_check_s=20)
    try:
        rc, out = _run_out(
            db=db,
            dest=dest,
            now=datetime(2026, 10, 9, 5, 0, tzinfo=JST),
            offsite=offsite,
            limits=limits,
        )
        still_writing = proc.poll() is None
    finally:
        stdout, _stderr = proc.communicate(timeout=15)
    if proc.returncode != 0:
        _fail(f"writer rc {proc.returncode} {stdout}")
    commits = int(stdout.strip() or "0")
    if rc != 0:
        _fail(out)
    if not still_writing:
        _fail(f"backup waited for the writer to stop {out}")
    if commits < 1:
        _fail(f"writer made no progress {stdout!r}")
    live = sqlite3.connect(db)
    try:
        if live.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            _fail("source journal changed")
    finally:
        live.close()
    payload = offsite.remote["daily"].get(backup_name("2026-10-09"))
    if payload is None:
        _fail("remote payload missing")
    out_db = tmp_path / "out.db"
    out_db.write_bytes(gzip.decompress(payload))
    checked = sqlite3.connect(out_db)
    try:
        ok = checked.execute("PRAGMA quick_check").fetchone()
        if not ok or ok[0] != "ok":
            _fail(f"concurrent quick_check {ok}")
        base = checked.execute("SELECT COUNT(*) FROM items WHERE name LIKE 'base-%'").fetchone()[0]
        if base != base_count:
            _fail(f"base rows {base}")
        snapped = checked.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        if snapped >= base_count + commits:
            _fail(f"snapshot includes commits after the writer stopped {snapped} {commits}")
    finally:
        checked.close()


def test_timeout_names_the_stage(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    _seed_gz(dest / "daily", ["2026-10-08"], b"good")
    limits = BackupLimits(overall_s=30, copy_s=0, check_s=30, gzip_s=30, offsite_copy_s=30, offsite_check_s=30)
    rc, out = _run_out(
        db=db,
        dest=dest,
        now=datetime(2026, 10, 9, 5, 0, tzinfo=JST),
        offsite=_ok_offsite(),
        limits=limits,
    )
    if rc == 0 or "timeout at copy" not in out:
        _fail(out)
    if "fail backup_db:" not in out:
        _fail(out)
    if (dest / "daily" / backup_name("2026-10-09")).exists():
        _fail("timeout installed today")
    if (dest / "daily" / backup_name("2026-10-08")).read_bytes() != b"good":
        _fail("timeout touched the older file")


def test_second_run_skips_while_locked(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    dest.mkdir()
    fh = (dest / ".backup_db.lock").open("a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from jobs.ops.backup_db import run\n"
        f"sys.exit(run(Path({str(db)!r}), Path({str(dest)!r}), offsite=False))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    if proc.returncode != 0:
        _fail(f"skip rc {proc.returncode} {proc.stdout} {proc.stderr}")
    if "backup_db skip: already running" not in proc.stdout:
        _fail(proc.stdout + proc.stderr)
    if list_generations(dest / "daily"):
        _fail("locked run wrote a generation")


def test_copy_budget_names_the_cause(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, blob BLOB)")
    conn.executemany(
        "INSERT INTO items (blob) VALUES (?)",
        [(os.urandom(4096),) for _ in range(64)],
    )
    conn.commit()
    conn.close()
    try:
        write_consistent_gzip(
            db,
            tmp_path / "work.db",
            tmp_path / "out.db.gz.part",
            budget=4096,
            deadline=Deadline(BackupLimits(), time.monotonic()),
        )
    except BackupError as exc:
        text = str(exc)
        if "compress budget exceeded" not in text or "SQL logic error" in text:
            _fail(text)
    else:
        _fail("small budget must fail")
    if (tmp_path / "work.db").exists() or (tmp_path / "work.db.blk").exists():
        _fail("budget failure left the store")
    if (tmp_path / "out.db.gz.part").exists():
        _fail("budget failure left the part")


def test_disk_full_names_the_cause(tmp_path: Path) -> None:
    import jobs.ops.backup_store as store_mod

    db = tmp_path / "stock.db"
    _make_db(db, ["ok"])
    original = store_mod.BlockStore.write

    def full(self, data: bytes, offset: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    store_mod.BlockStore.write = full
    try:
        write_consistent_gzip(
            db,
            tmp_path / "work.db",
            tmp_path / "out.db.gz.part",
            budget=8 * 1024 * 1024,
            deadline=Deadline(BackupLimits(), time.monotonic()),
        )
    except BackupError as exc:
        text = str(exc)
        if "disk full" not in text or "SQL logic error" in text:
            _fail(text)
    else:
        _fail("disk full must fail")
    finally:
        store_mod.BlockStore.write = original


def test_hash_mismatch_does_not_accept_size(tmp_path: Path) -> None:
    gz = tmp_path / "stock-2026-10-09.db.gz"
    gz.write_bytes(b"abc")
    settings = OffsiteSettings(remote="remote-a", root="root-b")

    def fake_run(argv: list[str], timeout: float) -> CmdResult:
        cmd = argv[0]
        if cmd == "listremotes":
            return CmdResult(0, f"{settings.remote}:\n", "")
        if cmd == "copy":
            return CmdResult(0, "", "")
        if cmd == "check":
            return CmdResult(1, "", "ERROR : stock-2026-10-09.db.gz: hashes differ")
        if cmd == "lsjson":
            _fail("size fallback on a hash mismatch")
        _fail(f"unexpected {argv!r}")
        return CmdResult(1, "", "")

    try:
        sync_offsite(
            [(gz, "daily")],
            keep=BackupKeep(offsite_daily=2, offsite_weekly=2),
            settings=settings,
            which=lambda _name: "/usr/bin/rclone",
            run=fake_run,
        )
    except BackupError as exc:
        if "hashes differ" not in str(exc):
            _fail(exc)
    else:
        _fail("hash mismatch must fail")


def test_offsite_timeout_uses_overall_remaining(tmp_path: Path) -> None:
    gz = tmp_path / "stock-2026-10-09.db.gz"
    gz.write_bytes(b"abc")
    settings = OffsiteSettings(remote="remote-a", root="root-b")
    seen: list[tuple[str, float]] = []

    def fake_run(argv: list[str], timeout: float) -> CmdResult:
        seen.append((argv[0], timeout))
        if argv[0] == "listremotes":
            return CmdResult(0, f"{settings.remote}:\n", "")
        if argv[0] == "lsf":
            return CmdResult(1, "", "directory not found")
        return CmdResult(0, "", "")

    started = time.monotonic() - 80
    deadline = Deadline(BackupLimits(overall_s=100, offsite_copy_s=10_000, offsite_check_s=5_000), started)
    deadline.enter("offsite")
    sync_offsite(
        [(gz, "daily"), (gz, "weekly")],
        keep=BackupKeep(offsite_daily=1, offsite_weekly=1),
        settings=settings,
        which=lambda _name: "/usr/bin/rclone",
        run=fake_run,
        copy_timeout=10_000,
        check_timeout=5_000,
        deadline=deadline,
    )
    copies = [timeout for cmd, timeout in seen if cmd == "copy"]
    checks = [timeout for cmd, timeout in seen if cmd == "check"]
    if len(copies) != 2 or len(checks) != 2:
        _fail(f"calls {seen}")
    if copies[0] >= 10_000 or copies[0] > 25:
        _fail(f"copy ignored overall remaining {seen}")
    if checks[0] >= 5_000 or checks[0] > 25:
        _fail(f"check ignored overall remaining {seen}")
    if copies[1] > copies[0] + 1:
        _fail(f"second copy grew {seen}")


def test_import_does_not_load_apsw() -> None:
    code = (
        "import sys\n"
        "import jobs.ops.backup_db\n"
        "import jobs.ops.backup_data\n"
        "import jobs.market.briefing_offsite\n"
        "import tools.briefing.irbank_ingest\n"
        "raise SystemExit(1 if 'apsw' in sys.modules or 'jobs.ops.backup_store' in sys.modules else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"import loaded apsw rc={proc.returncode} {proc.stderr}")


def main_tests() -> None:
    test_constants()
    test_keep_and_offsite_from_env()
    test_provision_and_isolation()
    test_rclone_missing_remote_skips()
    with TemporaryDirectory() as d:
        test_default_db_path(Path(d))
    with TemporaryDirectory() as d:
        test_wal_backup_roundtrip(Path(d))
    with TemporaryDirectory() as d:
        test_upload_success_drops_this_run_and_keeps_older_local(Path(d))
    with TemporaryDirectory() as d:
        test_offsite_failure_keeps_local_generations(Path(d))
    with TemporaryDirectory() as d:
        test_offsite_skip_keeps_local_and_exits_zero(Path(d))
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
    with TemporaryDirectory() as d:
        test_rclone_argv_copy_check_prune(Path(d))
    with TemporaryDirectory() as d:
        test_rclone_check_falls_back_to_size(Path(d))
    test_required_bytes_stays_under_one_database()
    with TemporaryDirectory() as d:
        test_store_stops_at_its_budget(Path(d))
    with TemporaryDirectory() as d:
        test_snapshot_is_smaller_than_the_db_and_offsite_ok_deletes_local(Path(d))
    with TemporaryDirectory() as d:
        test_writer_commits_during_backup(Path(d))
    with TemporaryDirectory() as d:
        test_timeout_names_the_stage(Path(d))
    with TemporaryDirectory() as d:
        test_second_run_skips_while_locked(Path(d))
    with TemporaryDirectory() as d:
        test_copy_budget_names_the_cause(Path(d))
    with TemporaryDirectory() as d:
        test_disk_full_names_the_cause(Path(d))
    with TemporaryDirectory() as d:
        test_hash_mismatch_does_not_accept_size(Path(d))
    with TemporaryDirectory() as d:
        test_offsite_timeout_uses_overall_remaining(Path(d))
    test_import_does_not_load_apsw()
    print("ok: backup_db")


if __name__ == "__main__":
    main_tests()

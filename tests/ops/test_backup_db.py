"""backup_db の世代間引きと失敗時に残すこと。本番 VPS には出ない。"""
from __future__ import annotations

import gzip
import io
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.ops.backup_db import (  # noqa: E402
    BACKUP_NAME_RE,
    COMPRESSLEVEL,
    DEFAULT_DEST,
    DEFAULT_OFFSITE_PATH,
    DEFAULT_OFFSITE_REMOTE,
    LOCAL_DAILY_KEEP,
    LOCAL_WEEKLY_KEEP,
    OFFSITE_DAILY_KEEP,
    OFFSITE_WEEKLY_KEEP,
    BackupError,
    BackupKeep,
    CmdResult,
    OffsiteSettings,
    OffsiteSkip,
    backup_name,
    clip_cmd_output,
    default_db_path,
    keep_from_env,
    list_generations,
    listed_remotes,
    main,
    offsite_settings_from_env,
    prune_generations,
    quick_check,
    run,
    sqlite_backup,
    sqlite_sidecar_paths,
    sync_offsite,
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


def _run_out(**kwargs):  # noqa: ANN003
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(**kwargs)
    return rc, buf.getvalue()


def test_constants() -> None:
    if DEFAULT_DEST != Path("/home/stock/backups"):
        _fail(f"DEFAULT_DEST {DEFAULT_DEST!r}")
    if LOCAL_DAILY_KEEP != 1 or LOCAL_WEEKLY_KEEP != 1:
        _fail(f"local keep daily={LOCAL_DAILY_KEEP} weekly={LOCAL_WEEKLY_KEEP}")
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
            "BACKUP_LOCAL_DAILY_KEEP": "2",
            "BACKUP_LOCAL_WEEKLY_KEEP": "3",
            "BACKUP_OFFSITE_DAILY_KEEP": "9",
            "BACKUP_OFFSITE_WEEKLY_KEEP": "5",
        }
    )
    if got != BackupKeep(local_daily=2, local_weekly=3, offsite_daily=9, offsite_weekly=5):
        _fail(f"override {got}")
    try:
        keep_from_env({"BACKUP_LOCAL_DAILY_KEEP": "0"})
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
        rc = run(db, dest, now=friday, offsite=_ok_offsite())
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


def test_sunday_offsite_success_prunes_local_to_one(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    dest = tmp_path / "backups"
    _make_db(db, ["ok"])
    daily_days = [f"2026-09-{n:02d}" for n in range(27, 31)] + [
        f"2026-10-{n:02d}" for n in range(1, 4)
    ]
    _seed_gz(dest / "daily", daily_days)
    weekly_days = ["2026-09-06", "2026-09-13", "2026-09-20", "2026-09-27"]
    _seed_gz(dest / "weekly", weekly_days)
    offsite = MemoryOffsite()
    offsite.seed("daily", daily_days)
    offsite.seed("weekly", weekly_days)
    offsite.remote["daily"]["notes.txt"] = b"leave-me"
    sunday = datetime(2026, 10, 4, 5, 0, tzinfo=JST)
    rc, out = _run_out(db=db, dest=dest, now=sunday, offsite=offsite)
    if rc != 0:
        _fail(f"sunday rc {out}")
    daily_names = _gz_names(dest / "daily")
    if daily_names != [backup_name("2026-10-04")]:
        _fail(f"local daily {daily_names}")
    weekly_names = _gz_names(dest / "weekly")
    if weekly_names != [backup_name("2026-10-04")]:
        _fail(f"local weekly {weekly_names}")
    daily = dest / "daily" / backup_name("2026-10-04")
    weekly = dest / "weekly" / backup_name("2026-10-04")
    if daily.stat().st_ino != weekly.stat().st_ino:
        _fail("sunday weekly should hardlink daily")
    if offsite.names("daily") != [
        backup_name(d)
        for d in (
            "2026-10-04",
            "2026-10-03",
            "2026-10-02",
            "2026-10-01",
            "2026-09-30",
            "2026-09-29",
            "2026-09-28",
        )
    ]:
        _fail(f"remote daily {offsite.names('daily')}")
    if offsite.names("weekly") != [
        backup_name("2026-10-04"),
        backup_name("2026-09-27"),
        backup_name("2026-09-20"),
        backup_name("2026-09-13"),
    ]:
        _fail(f"remote weekly {offsite.names('weekly')}")
    if offsite.remote["daily"].get("notes.txt") != b"leave-me":
        _fail("remote noise deleted")
    if "offsite=ok" not in out:
        _fail(f"missing offsite=ok {out}")
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

    def _bad_check(_path: Path) -> tuple[bool, str]:
        return False, "not ok"

    friday = datetime(2026, 10, 9, 5, 0, tzinfo=JST)
    rc = run(db, dest, now=friday, check=_bad_check, offsite=_ok_offsite())
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
    _assert_tmp_empty(dest)


def test_rclone_argv_copy_check_prune(tmp_path: Path) -> None:
    gz = tmp_path / "stock-2026-10-09.db.gz"
    gz.write_bytes(b"payload-bytes")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], timeout: float) -> CmdResult:  # noqa: ARG001
        calls.append(list(argv))
        cmd = argv[0]
        if cmd == "listremotes":
            return CmdResult(0, "onedrive:\nr2:\n", "")
        if cmd == "copy":
            return CmdResult(0, "", "")
        if cmd == "check":
            return CmdResult(0, "", "")
        if cmd == "lsf":
            extra = "\n".join(backup_name(f"2026-10-{n:02d}") for n in range(1, 10))
            extra += "\nnotes.txt\nstock-foo.db.gz\n"
            return CmdResult(0, extra, "")
        if cmd == "deletefile":
            return CmdResult(0, "", "")
        _fail(f"unexpected rclone {argv!r}")
        return CmdResult(1, "", "unexpected")

    sync_offsite(
        [(gz, "daily")],
        keep=BackupKeep(),
        settings=OffsiteSettings(),
        which=lambda _name: "/usr/bin/rclone",
        run=fake_run,
    )
    cmds = [c[0] for c in calls]
    if cmds[0] != "listremotes" or "copy" not in cmds or "check" not in cmds:
        _fail(f"cmds {cmds}")
    copy = next(c for c in calls if c[0] == "copy")
    if copy[1] != str(gz) or copy[2] != "onedrive:stock-analyzer-backups/daily":
        _fail(f"copy argv {copy}")
    check = next(c for c in calls if c[0] == "check")
    if "--include" not in check or f"/{gz.name}" not in check or "--one-way" not in check:
        _fail(f"check argv {check}")
    deletes = [c[1] for c in calls if c[0] == "deletefile"]
    if any("notes.txt" in d or "stock-foo.db.gz" in d for d in deletes):
        _fail(f"deleted noise {deletes}")
    if "onedrive:stock-analyzer-backups/daily/stock-2026-10-01.db.gz" not in deletes:
        _fail(f"oldest daily not deleted {deletes}")
    if any("stock-2026-10-09.db.gz" in d and "/daily/" in d for d in deletes):
        _fail(f"kept daily deleted {deletes}")


def test_rclone_missing_remote_skips() -> None:
    def fake_run(argv: list[str], timeout: float) -> CmdResult:  # noqa: ARG001
        if argv[0] == "listremotes":
            return CmdResult(0, "r2:\n", "")
        _fail(f"should not run {argv!r}")
        return CmdResult(1, "", "")

    try:
        sync_offsite(
            [],
            keep=BackupKeep(),
            settings=OffsiteSettings(),
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

    def fake_run(argv: list[str], timeout: float) -> CmdResult:  # noqa: ARG001
        cmd = argv[0]
        if cmd == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if cmd == "copy":
            return CmdResult(0, "", "")
        if cmd == "check":
            return CmdResult(1, "", "hash not supported")
        if cmd == "lsjson":
            return CmdResult(0, '[{"Name":"stock-2026-10-09.db.gz","Size":3}]', "")
        if cmd == "lsf":
            return CmdResult(1, "", "directory not found")
        _fail(f"unexpected {argv!r}")
        return CmdResult(1, "", "")

    sync_offsite(
        [(gz, "daily")],
        keep=BackupKeep(),
        settings=OffsiteSettings(),
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
    for needle in ("from app", "import app", "r2-share", "rclone.conf"):
        if needle in src:
            _fail(f"backup job must not reference {needle}")
    if "rclone" not in src:
        _fail("backup job must call rclone")


def main_tests() -> None:
    test_constants()
    test_keep_and_offsite_from_env()
    test_provision_and_isolation()
    test_rclone_missing_remote_skips()
    with TemporaryDirectory() as d:
        test_default_db_path(Path(d))
    with TemporaryDirectory() as d:
        test_prune_keeps_newest_and_ignores_noise(Path(d))
    with TemporaryDirectory() as d:
        test_wal_backup_roundtrip(Path(d))
    with TemporaryDirectory() as d:
        test_sunday_offsite_success_prunes_local_to_one(Path(d))
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
    print("ok: backup_db")


if __name__ == "__main__":
    main_tests()

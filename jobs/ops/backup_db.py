"""本番 SQLite の整合バックアップ。WAL 中の cp は不可。

使い方:
  python jobs/ops/backup_db.py
  python jobs/ops/backup_db.py --db /path/stock.db --dest /tmp/backups

保存は VPS ローカルのみ（既定 /home/stock/backups）。R2 には出さない。
失敗時は非ゼロ終了。cron-run.sh が fail cron を書き、fluent-bit が #alerts-batch へ page する。
壊れた世代では既存の正常ファイルを消さない。
"""
from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST = Path("/home/stock/backups")
DAILY_KEEP = 7
WEEKLY_KEEP = 4
COMPRESSLEVEL = 1
BUSY_TIMEOUT_MS = 30_000
BACKUP_PAGES = 100
BACKUP_SLEEP_S = 0.25
GZIP_RATIO_SLACK = 0.5
MIN_FREE_MARGIN = 256 * 1024 * 1024
BACKUP_NAME_RE = re.compile(r"^stock-(\d{4}-\d{2}-\d{2})\.db\.gz$")
COPY_CHUNK = 1024 * 1024

FreeBytesFn = Callable[[Path], int]
BackupFn = Callable[[Path, Path], None]
CheckFn = Callable[[Path], tuple[bool, str]]


class BackupError(Exception):
    """想定内の失敗（容量・破損・欠ファイル）。traceback ではなく fail 行にする。"""


def default_db_path(
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Path:
    """DATABASE_URL が sqlite ならそのファイル。それ以外は <repo>/stock.db。"""
    env = os.environ if environ is None else environ
    root = REPO_ROOT if repo_root is None else repo_root
    url = (env.get("DATABASE_URL") or "").strip()
    if url.startswith("sqlite:///"):
        rest = url[len("sqlite:///") :].split("?", 1)[0]
        if rest and rest != ":memory:":
            pth = Path(rest)
            return pth if pth.is_absolute() else root / pth
    return root / "stock.db"


def backup_name(day: str) -> str:
    return f"stock-{day}.db.gz"


def daily_dir(dest: Path) -> Path:
    return dest / "daily"


def weekly_dir(dest: Path) -> Path:
    return dest / "weekly"


def tmp_dir(dest: Path) -> Path:
    return dest / ".tmp"


def is_sunday(when: datetime) -> bool:
    local = when.astimezone(JST) if when.tzinfo else when.replace(tzinfo=JST)
    return local.weekday() == 6


def format_day(when: datetime) -> str:
    local = when.astimezone(JST) if when.tzinfo else when.replace(tzinfo=JST)
    return local.strftime("%Y-%m-%d")


def source_bytes(db: Path) -> int:
    total = db.stat().st_size
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        try:
            total += side.stat().st_size
        except OSError:
            pass
    return total


def required_bytes(src_size: int) -> int:
    return src_size + int(src_size * GZIP_RATIO_SLACK) + MIN_FREE_MARGIN


def disk_free(path: Path) -> int:
    st = os.statvfs(path)
    return int(st.f_bavail) * int(st.f_frsize)


def list_generations(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in folder.iterdir():
        match = BACKUP_NAME_RE.fullmatch(path.name)
        if match is None or not path.is_file():
            continue
        found.append((match.group(1), path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found]


def prune_generations(folder: Path, keep: int) -> list[Path]:
    """新しい keep 件を残し、それより古い世代だけ消す。失敗時は呼ばない。"""
    kept: list[Path] = []
    removed: list[Path] = []
    for index, path in enumerate(list_generations(folder)):
        if index < keep:
            kept.append(path)
            continue
        path.unlink()
        removed.append(path)
    return removed


def sqlite_sidecar_paths(path: Path) -> tuple[Path, ...]:
    """本体と SQLite が横に置く -wal / -shm / -journal。"""
    text = str(path)
    return (path, Path(text + "-wal"), Path(text + "-shm"), Path(text + "-journal"))


def unlink_sqlite(path: Path) -> None:
    """接続を閉じたあとに本体と添えファイルを消す。欠ファイルは無視。"""
    for candidate in sqlite_sidecar_paths(path):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue


def sqlite_backup(src: Path, dest: Path) -> None:
    """WAL 込みの整合コピー。pages を区切ってライターを長く止めない。

    コピー先は journal_mode=DELETE にして単一ファイルにする。ソースの WAL は触らない。
    """
    unlink_sqlite(dest)
    src_conn = sqlite3.connect(str(src), timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        src_conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        dest_conn = sqlite3.connect(str(dest), timeout=BUSY_TIMEOUT_MS / 1000)
        try:
            dest_conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            src_conn.backup(dest_conn, pages=BACKUP_PAGES, sleep=BACKUP_SLEEP_S)
            # backup はソースの WAL フラグをヘッダにコピーする。gzip 前に単一ファイルへ戻す。
            dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dest_conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    for leftover in sqlite_sidecar_paths(dest)[1:]:
        leftover.unlink(missing_ok=True)


def quick_check(path: Path) -> tuple[bool, str]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        msg = str(row[0]) if row else "empty"
        return msg == "ok", msg
    finally:
        conn.close()


def gzip_file(src: Path, dest: Path, compresslevel: int = COMPRESSLEVEL) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        with src.open("rb") as fin, gzip.open(part, "wb", compresslevel=compresslevel) as fout:
            shutil.copyfileobj(fin, fout, length=COPY_CHUNK)
        os.replace(part, dest)
    except Exception:
        if part.exists():
            part.unlink()
        raise
    dest.chmod(0o640)


def link_or_copy(src: Path, dest: Path) -> str:
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.link(src, dest)
        return "link"
    except OSError:
        shutil.copy2(src, dest)
        dest.chmod(0o640)
        return "copy"


def ensure_dest(dest: Path) -> None:
    daily_dir(dest).mkdir(parents=True, exist_ok=True)
    weekly_dir(dest).mkdir(parents=True, exist_ok=True)
    tmp_dir(dest).mkdir(parents=True, exist_ok=True)


def clean_tmp(dest: Path) -> None:
    """`.tmp` は作業用。前回の -wal / -shm 残骸も含めて空にする。"""
    folder = tmp_dir(dest)
    if folder.is_dir():
        for path in folder.iterdir():
            if path.is_file() or path.is_symlink():
                try:
                    path.unlink()
                except OSError:
                    pass
    for folder in (daily_dir(dest), weekly_dir(dest)):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if path.name.endswith(".part"):
                try:
                    path.unlink()
                except OSError:
                    pass


def run(
    db: Path,
    dest: Path,
    *,
    now: datetime | None = None,
    free_bytes: FreeBytesFn | None = None,
    backup: BackupFn | None = None,
    check: CheckFn | None = None,
) -> int:
    when = now or datetime.now(JST)
    day = format_day(when)
    print(f"backup_db start db={db} dest={dest} day={day}", flush=True)
    try:
        _run(
            db,
            dest,
            when=when,
            day=day,
            free_bytes=free_bytes or disk_free,
            backup=backup or sqlite_backup,
            check=check or quick_check,
        )
    except BackupError as exc:
        print(f"fail backup_db: {exc}", flush=True)
        return 1
    print("backup_db done", flush=True)
    return 0


def _run(
    db: Path,
    dest: Path,
    *,
    when: datetime,
    day: str,
    free_bytes: FreeBytesFn,
    backup: BackupFn,
    check: CheckFn,
) -> None:
    ensure_dest(dest)
    clean_tmp(dest)
    if not db.is_file():
        raise BackupError(f"db missing {db}")
    src_size = source_bytes(db)
    need = required_bytes(src_size)
    free = free_bytes(dest)
    if free < need:
        raise BackupError(f"insufficient disk need={need} free={free} dest={dest}")

    raw = tmp_dir(dest) / f"stock-{day}.{os.getpid()}.db"
    daily = daily_dir(dest) / backup_name(day)
    try:
        backup(db, raw)
        ok, msg = check(raw)
        if not ok:
            raise BackupError(f"quick_check {msg}")
        gzip_file(raw, daily)
        print(f"saved {daily} src_bytes={src_size} gzip_bytes={daily.stat().st_size}", flush=True)
        if is_sunday(when):
            weekly = weekly_dir(dest) / backup_name(day)
            how = link_or_copy(daily, weekly)
            print(f"weekly {how} {weekly}", flush=True)
        removed_daily = prune_generations(daily_dir(dest), DAILY_KEEP)
        removed_weekly = prune_generations(weekly_dir(dest), WEEKLY_KEEP)
        print(
            f"pruned daily={len(removed_daily)} weekly={len(removed_weekly)}",
            flush=True,
        )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(str(exc)) from exc
    finally:
        unlink_sqlite(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consistent gzip backup of the local SQLite DB")
    ap.add_argument("--db", default=str(default_db_path()), help="source SQLite path")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="backup directory")
    args = ap.parse_args(argv)
    try:
        return run(Path(args.db), Path(args.dest))
    except Exception as exc:
        print(f"fail backup_db: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

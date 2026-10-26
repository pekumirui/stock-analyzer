"""本番 SQLite の整合バックアップ。WAL 中の cp は不可。

使い方:
  python jobs/ops/backup_db.py
  python jobs/ops/backup_db.py --db /path/stock.db --dest /tmp/backups

ローカルに gzip したあと、rclone で OneDrive へ送る（既定
onedrive:stock-analyzer-backups/{daily,weekly}）。トークンは
rclone のユーザ設定（リポジトリと CI には置かない）。
VPS の古い世代を消すのは、中身チェックとオフサイトの送信・確認が
成功したあとだけ。rclone が無い / remote が無いときは警告してスキップ
（ローカルは成功のまま、古い世代は残す）。オフサイト失敗は非ゼロ終了。
cron-run.sh が fail cron を書き、fluent-bit が #alerts-batch へ page する。
ローカルの成功ファイルは消さない。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST = Path("/home/stock/backups")
DEFAULT_OFFSITE_REMOTE = "onedrive"
DEFAULT_OFFSITE_PATH = "stock-analyzer-backups"
LOCAL_DAILY_KEEP = 1
LOCAL_WEEKLY_KEEP = 1
OFFSITE_DAILY_KEEP = 7
OFFSITE_WEEKLY_KEEP = 4
COMPRESSLEVEL = 1
BUSY_TIMEOUT_MS = 30_000
BACKUP_PAGES = 100
BACKUP_SLEEP_S = 0.25
GZIP_RATIO_SLACK = 0.5
MIN_FREE_MARGIN = 256 * 1024 * 1024
BACKUP_NAME_RE = re.compile(r"^stock-(\d{4}-\d{2}-\d{2})\.db\.gz$")
COPY_CHUNK = 1024 * 1024
RCLONE_RETRIES = 3
RCLONE_COPY_TIMEOUT_S = 3600.0
RCLONE_CHECK_TIMEOUT_S = 1800.0
RCLONE_LIST_TIMEOUT_S = 120.0
RCLONE_PROBE_TIMEOUT_S = 30.0

FreeBytesFn = Callable[[Path], int]
BackupFn = Callable[[Path, Path], None]
CheckFn = Callable[[Path], tuple[bool, str]]
WhichFn = Callable[[str], str | None]


class BackupError(Exception):
    """想定内の失敗（容量・破損・欠ファイル・オフサイト）。traceback ではなく fail 行にする。"""


class OffsiteSkip(Exception):
    """rclone 欠如など。ローカルは成功のまま、世代は消さない。"""


@dataclass(frozen=True)
class BackupKeep:
    local_daily: int = LOCAL_DAILY_KEEP
    local_weekly: int = LOCAL_WEEKLY_KEEP
    offsite_daily: int = OFFSITE_DAILY_KEEP
    offsite_weekly: int = OFFSITE_WEEKLY_KEEP


@dataclass(frozen=True)
class OffsiteSettings:
    remote: str = DEFAULT_OFFSITE_REMOTE
    root: str = DEFAULT_OFFSITE_PATH
    rclone_bin: str = "rclone"
    enabled: bool = True

    def dest(self, kind: str) -> str:
        return f"{self.remote}:{self.root}/{kind}"


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str


RcloneRunFn = Callable[[Sequence[str], float], CmdResult]
OffsiteFn = Callable[[list[tuple[Path, str]], BackupKeep], None]


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


def env_flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise BackupError(f"{name}={raw!r} must be 0/1")


def env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BackupError(f"{name}={raw!r} is not an int") from exc
    if value < 1:
        raise BackupError(f"{name}={value} must be >= 1")
    return value


def keep_from_env(environ: Mapping[str, str] | None = None) -> BackupKeep:
    env = os.environ if environ is None else environ
    return BackupKeep(
        local_daily=env_int(env, "BACKUP_LOCAL_DAILY_KEEP", LOCAL_DAILY_KEEP),
        local_weekly=env_int(env, "BACKUP_LOCAL_WEEKLY_KEEP", LOCAL_WEEKLY_KEEP),
        offsite_daily=env_int(env, "BACKUP_OFFSITE_DAILY_KEEP", OFFSITE_DAILY_KEEP),
        offsite_weekly=env_int(env, "BACKUP_OFFSITE_WEEKLY_KEEP", OFFSITE_WEEKLY_KEEP),
    )


def _norm_remote(raw: str) -> str:
    return raw.strip().rstrip(":")


def offsite_settings_from_env(environ: Mapping[str, str] | None = None) -> OffsiteSettings:
    env = os.environ if environ is None else environ
    remote = _norm_remote(env.get("BACKUP_OFFSITE_REMOTE") or DEFAULT_OFFSITE_REMOTE)
    root = (env.get("BACKUP_OFFSITE_PATH") or DEFAULT_OFFSITE_PATH).strip().strip("/")
    bin_name = (env.get("BACKUP_RCLONE_BIN") or "rclone").strip() or "rclone"
    if not remote or not root:
        raise BackupError("BACKUP_OFFSITE_REMOTE and BACKUP_OFFSITE_PATH must be non-empty")
    return OffsiteSettings(
        remote=remote,
        root=root,
        rclone_bin=bin_name,
        enabled=env_flag(env, "BACKUP_OFFSITE", True),
    )


def clip_cmd_output(text: str, limit: int = 400) -> str:
    one = " ".join((text or "").split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"


def rclone_flags() -> list[str]:
    return ["--retries", str(RCLONE_RETRIES), "--stats", "0"]


def rclone_available(bin_name: str, which: WhichFn) -> bool:
    if os.path.sep in bin_name or (os.path.altsep and os.path.altsep in bin_name):
        path = Path(bin_name)
        return path.is_file() and os.access(path, os.X_OK)
    return which(bin_name) is not None


def make_rclone_runner(bin_name: str) -> RcloneRunFn:
    def _run(argv: Sequence[str], timeout: float) -> CmdResult:
        try:
            proc = subprocess.run(  # nosec B603 - 固定の実行ファイル。shell は使わない
                [bin_name, *argv],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OffsiteSkip(f"rclone missing {bin_name}") from exc
        except subprocess.TimeoutExpired as exc:
            raise BackupError(f"rclone timeout {argv[0] if argv else ''}") from exc
        return CmdResult(proc.returncode, proc.stdout or "", proc.stderr or "")

    return _run


def listed_remotes(stdout: str) -> set[str]:
    found: set[str] = set()
    for line in stdout.splitlines():
        name = line.strip().rstrip(":")
        if name:
            found.add(name)
    return found


def _not_found(stderr: str) -> bool:
    text = stderr.lower()
    return "directory not found" in text or "not found" in text or "doesn't exist" in text


def probe_offsite(
    settings: OffsiteSettings,
    *,
    which: WhichFn,
    run: RcloneRunFn,
) -> None:
    if not settings.enabled:
        raise OffsiteSkip("BACKUP_OFFSITE disabled")
    if not rclone_available(settings.rclone_bin, which):
        raise OffsiteSkip("rclone not installed")
    result = run(["listremotes"], RCLONE_PROBE_TIMEOUT_S)
    if result.returncode != 0:
        raise BackupError(
            f"rclone listremotes exit={result.returncode} {clip_cmd_output(result.stderr)}"
        )
    remotes = listed_remotes(result.stdout)
    if settings.remote not in remotes:
        raise OffsiteSkip(f"rclone remote {settings.remote!r} missing")


def _lsjson_size(payload: str) -> int | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    rec: object
    if isinstance(data, list):
        if not data:
            return None
        rec = data[0]
    else:
        rec = data
    if not isinstance(rec, dict):
        return None
    size = rec.get("Size")
    if isinstance(size, bool) or not isinstance(size, int):
        return None
    return size


def copy_and_verify(
    local: Path,
    kind: str,
    settings: OffsiteSettings,
    run: RcloneRunFn,
) -> None:
    dest_dir = settings.dest(kind)
    flags = rclone_flags()
    copied = run(["copy", str(local), dest_dir, *flags], RCLONE_COPY_TIMEOUT_S)
    if copied.returncode != 0:
        raise BackupError(
            f"rclone copy {kind} exit={copied.returncode} {clip_cmd_output(copied.stderr)}"
        )
    checked = run(
        [
            "check",
            str(local.parent),
            dest_dir,
            "--include",
            f"/{local.name}",
            "--one-way",
            *flags,
        ],
        RCLONE_CHECK_TIMEOUT_S,
    )
    if checked.returncode == 0:
        print(f"offsite ok {kind} {local.name} bytes={local.stat().st_size}", flush=True)
        return
    listed = run(
        ["lsjson", dest_dir, "--files-only", "--include", local.name, *flags],
        RCLONE_LIST_TIMEOUT_S,
    )
    remote_size = _lsjson_size(listed.stdout) if listed.returncode == 0 else None
    local_size = local.stat().st_size
    if remote_size == local_size:
        print(
            f"offsite ok {kind} {local.name} bytes={local_size} via size",
            flush=True,
        )
        return
    raise BackupError(
        f"rclone check {kind} exit={checked.returncode} "
        f"local={local_size} remote={remote_size} {clip_cmd_output(checked.stderr)}"
    )


def prune_offsite(
    kind: str,
    keep: int,
    settings: OffsiteSettings,
    run: RcloneRunFn,
) -> list[str]:
    dest_dir = settings.dest(kind)
    flags = rclone_flags()
    listed = run(["lsf", "--files-only", dest_dir, *flags], RCLONE_LIST_TIMEOUT_S)
    if listed.returncode != 0:
        if _not_found(listed.stderr):
            return []
        raise BackupError(
            f"rclone lsf {kind} exit={listed.returncode} {clip_cmd_output(listed.stderr)}"
        )
    names: list[tuple[str, str]] = []
    for line in listed.stdout.splitlines():
        name = line.strip().rstrip("/")
        match = BACKUP_NAME_RE.fullmatch(name)
        if match is None:
            continue
        names.append((match.group(1), name))
    names.sort(key=lambda item: item[0], reverse=True)
    removed: list[str] = []
    for _, name in names[keep:]:
        deleted = run(["deletefile", f"{dest_dir}/{name}", *flags], RCLONE_LIST_TIMEOUT_S)
        if deleted.returncode != 0:
            raise BackupError(
                f"rclone deletefile {kind}/{name} exit={deleted.returncode} "
                f"{clip_cmd_output(deleted.stderr)}"
            )
        removed.append(name)
    return removed


def sync_offsite(
    items: list[tuple[Path, str]],
    *,
    keep: BackupKeep,
    settings: OffsiteSettings,
    which: WhichFn,
    run: RcloneRunFn,
) -> None:
    probe_offsite(settings, which=which, run=run)
    for path, kind in items:
        copy_and_verify(path, kind, settings, run)
    for kind, count in (("daily", keep.offsite_daily), ("weekly", keep.offsite_weekly)):
        removed = prune_offsite(kind, count, settings, run)
        print(f"offsite pruned {kind}={len(removed)}", flush=True)


def default_offsite(
    items: list[tuple[Path, str]],
    keep: BackupKeep,
    *,
    settings: OffsiteSettings,
    which: WhichFn | None = None,
    run: RcloneRunFn | None = None,
) -> None:
    runner = run or make_rclone_runner(settings.rclone_bin)
    sync_offsite(
        items,
        keep=keep,
        settings=settings,
        which=which or shutil.which,
        run=runner,
    )


def run(
    db: Path,
    dest: Path,
    *,
    now: datetime | None = None,
    free_bytes: FreeBytesFn | None = None,
    backup: BackupFn | None = None,
    check: CheckFn | None = None,
    offsite: OffsiteFn | bool | None = None,
    keep: BackupKeep | None = None,
    environ: Mapping[str, str] | None = None,
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
            offsite=offsite,
            keep=keep,
            environ=environ,
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
    offsite: OffsiteFn | bool | None,
    keep: BackupKeep | None,
    environ: Mapping[str, str] | None,
) -> None:
    env = os.environ if environ is None else environ
    policy = keep if keep is not None else keep_from_env(env)
    settings = offsite_settings_from_env(env)
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
    weekly: Path | None = None
    try:
        backup(db, raw)
        ok, msg = check(raw)
        if not ok:
            raise BackupError(f"quick_check {msg}")
        gzip_file(raw, daily)
        print(f"saved {daily} src_bytes={src_size} gzip_bytes={daily.stat().st_size}", flush=True)
        items: list[tuple[Path, str]] = [(daily, "daily")]
        if is_sunday(when):
            weekly = weekly_dir(dest) / backup_name(day)
            how = link_or_copy(daily, weekly)
            print(f"weekly {how} {weekly}", flush=True)
            items.append((weekly, "weekly"))
        try:
            _push_offsite(items, policy, settings, offsite)
        except OffsiteSkip as exc:
            print(f"warn backup_db offsite skipped: {exc}", flush=True)
            print("pruned daily=0 weekly=0 offsite=skip", flush=True)
            return
        removed_daily = prune_generations(daily_dir(dest), policy.local_daily)
        removed_weekly = prune_generations(weekly_dir(dest), policy.local_weekly)
        print(
            f"pruned daily={len(removed_daily)} weekly={len(removed_weekly)} offsite=ok",
            flush=True,
        )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(str(exc)) from exc
    finally:
        unlink_sqlite(raw)


def _push_offsite(
    items: list[tuple[Path, str]],
    policy: BackupKeep,
    settings: OffsiteSettings,
    offsite: OffsiteFn | bool | None,
) -> None:
    if offsite is False:
        raise OffsiteSkip("offsite disabled")
    if offsite is None:
        default_offsite(items, policy, settings=settings)
        return
    offsite(items, policy)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consistent gzip backup of the local SQLite DB")
    ap.add_argument("--db", default=str(default_db_path()), help="source SQLite path")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="backup directory")
    ap.add_argument(
        "--no-offsite",
        action="store_true",
        help="skip OneDrive copy (local success, do not prune old generations)",
    )
    args = ap.parse_args(argv)
    try:
        return run(
            Path(args.db),
            Path(args.dest),
            offsite=False if args.no_offsite else None,
        )
    except Exception as exc:
        print(f"fail backup_db: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

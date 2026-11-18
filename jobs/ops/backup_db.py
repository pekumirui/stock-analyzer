"""本番 SQLite の整合バックアップ。WAL 中の cp は不可。

使い方:
  python jobs/ops/backup_db.py
  python jobs/ops/backup_db.py --db /path/stock.db --dest /tmp/backups

sqlite3 backup API のソースと宛先はどちらも APSW。宛先は圧縮 VFS
（非圧縮の複製は置かない）。quick_check のあと gzip 圧縮レベル 1 で保存し、
rclone で OneDrive へ送る
（既定 onedrive:stock-analyzer-backups/{daily,weekly}）。トークンは
rclone のユーザ設定（リポジトリと CI には置かない）。
今回書いた gzip を消すのは、送信・確認と OneDrive 側の世代整理が
成功したあとだけ。失敗したときはそのファイルを残して非ゼロ終了する。
それより前からあるローカルの世代は触らない。
rclone が無い / remote が無いときは警告してスキップ
（今回のファイルも古い世代も残す）。
cron-run.sh が fail cron を書き、fluent-bit が #alerts-batch へ page する。
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import errno
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST = Path("/home/stock/backups")
DEFAULT_OFFSITE_REMOTE = "onedrive"
DEFAULT_OFFSITE_PATH = "stock-analyzer-backups"
OFFSITE_DAILY_KEEP = 7
OFFSITE_WEEKLY_KEEP = 4
COMPRESSLEVEL = 1
BUSY_TIMEOUT_MS = 30_000
BACKUP_PAGES = 100
BACKUP_SLEEP_S = 0.25
# 本番の gzip はソースの約 0.46。ブロック圧縮も同程度。予算はそれより上、ソース未満。
COMPRESS_FREE_RATIO = 0.75
MIN_FREE_MARGIN = 256 * 1024 * 1024
COPY_TIMEOUT_S = 2 * 60 * 60
CHECK_TIMEOUT_S = 90 * 60
GZIP_TIMEOUT_S = 60 * 60
OVERALL_TIMEOUT_S = 6 * 60 * 60
OFFSITE_COPY_TIMEOUT_S = 3 * 60 * 60
OFFSITE_CHECK_TIMEOUT_S = 90 * 60
BACKUP_NAME_RE = re.compile(r"^stock-(\d{4}-\d{2}-\d{2})\.db\.gz$")
COPY_CHUNK = 1024 * 1024
RCLONE_RETRIES = 3
RCLONE_COPY_TIMEOUT_S = 3600.0
RCLONE_CHECK_TIMEOUT_S = 1800.0
RCLONE_LIST_TIMEOUT_S = 120.0
RCLONE_PROBE_TIMEOUT_S = 30.0
_LOCK_NAME = ".backup_db.lock"

FreeBytesFn = Callable[[Path], int]
SnapshotFn = Callable[[Path, Path], None]
WhichFn = Callable[[str], str | None]


class BackupError(Exception):
    """想定内の失敗（容量・破損・欠ファイル・オフサイト）。traceback ではなく fail 行にする。"""


class OffsiteSkip(Exception):
    """rclone 欠如など。ローカルは成功のまま、世代は消さない。"""


@dataclass(frozen=True)
class BackupKeep:
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


def compress_budget(src_size: int) -> int:
    """圧縮ストアと gzip が同時に乗っても超えない量。小さい DB はヘッダ分を足す。"""
    scaled = int(src_size * COMPRESS_FREE_RATIO)
    floor = min(src_size + (1 << 20), 64 << 20)
    return max(scaled, floor)


def required_bytes(src_size: int) -> int:
    return compress_budget(src_size) + MIN_FREE_MARGIN


def disk_free(path: Path) -> int:
    st = os.statvfs(path)
    return int(st.f_bavail) * int(st.f_frsize)


@dataclass(frozen=True)
class BackupLimits:
    overall_s: float = OVERALL_TIMEOUT_S
    copy_s: float = COPY_TIMEOUT_S
    check_s: float = CHECK_TIMEOUT_S
    gzip_s: float = GZIP_TIMEOUT_S
    offsite_copy_s: float = OFFSITE_COPY_TIMEOUT_S
    offsite_check_s: float = OFFSITE_CHECK_TIMEOUT_S


@dataclass
class Deadline:
    limits: BackupLimits
    started: float
    stage: str = "start"
    stage_started: float = 0.0

    def __post_init__(self) -> None:
        self.stage_started = self.started

    def enter(self, stage: str) -> None:
        self.stage = stage
        self.stage_started = time.monotonic()
        self.check()

    def over(self) -> bool:
        now = time.monotonic()
        if now - self.started >= self.limits.overall_s:
            return True
        limit = {
            "copy": self.limits.copy_s,
            "quick_check": self.limits.check_s,
            "gzip": self.limits.gzip_s,
            "gzip-test": self.limits.gzip_s,
        }.get(self.stage)
        return limit is not None and now - self.stage_started >= limit

    def check(self) -> None:
        if self.over():
            raise BackupError(f"timeout at {self.stage}")

    def allow(self, cap: float) -> float:
        """この呼び出しの上限と、全体の残り時間の短い方。"""
        self.check()
        left = self.limits.overall_s - (time.monotonic() - self.started)
        return min(cap, max(left, 0.0))


def _elapsed(started: float) -> str:
    return f"{time.monotonic() - started:.1f}"


@contextmanager
def dest_lock(dest: Path) -> Iterator[bool]:
    """取れなければ False。同じ dest の 2 本目はバックアップしない。"""
    dest.mkdir(parents=True, exist_ok=True)
    fh = (dest / _LOCK_NAME).open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        acquired = True
        yield True
    finally:
        if acquired:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


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


def drop_uploaded(items: list[tuple[Path, str]]) -> tuple[int, int]:
    """今回送った gzip だけ消す。別の日のファイルは渡さない。"""
    daily = 0
    weekly = 0
    for path, kind in items:
        path.unlink(missing_ok=True)
        if kind == "weekly":
            weekly += 1
        else:
            daily += 1
    return daily, weekly


def _remove_work(path: Path) -> None:
    from jobs.ops.backup_store import remove_work

    remove_work(path)


def _disk_message(exc: OSError) -> str:
    if exc.errno == errno.ENOSPC:
        return f"disk full {exc.strerror or exc}"
    return f"disk write failed {exc.strerror or exc}"


def _gzip_store(store: object, part: Path, *, budget: int, deadline: Deadline) -> None:
    store.flush()
    logical = store.logical
    with gzip.open(part, "wb", compresslevel=COMPRESSLEVEL) as fout:
        pos = 0
        while pos < logical:
            deadline.check()
            amount = min(COPY_CHUNK, logical - pos)
            fout.write(store.read(amount, pos))
            fout.flush()
            pos += amount
            store.discard_through(pos)
            used = store.allocated() + part.stat().st_size
            if used > budget:
                raise BackupError(f"compress budget exceeded used={used} budget={budget}")
    part.chmod(0o640)


def _validate_gzip(path: Path, deadline: Deadline) -> None:
    try:
        with gzip.open(path, "rb") as fin:
            header = fin.read(100)
            if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
                raise BackupError("gzip is not a sqlite database")
            if header[18] != 1 or header[19] != 1:
                raise BackupError("gzip is not a single-file snapshot")
            while fin.read(COPY_CHUNK):
                deadline.check()
    except BackupError:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise BackupError(f"gzip test {exc}") from exc


def _raise_vfs(vfs: object, exc: BaseException) -> None:
    from jobs.ops.backup_store import StoreError

    remembered = getattr(vfs, "failure", None)
    if isinstance(remembered, StoreError):
        raise BackupError(str(remembered)) from exc
    if isinstance(remembered, OSError):
        raise BackupError(_disk_message(remembered)) from exc
    if remembered is not None:
        raise BackupError(str(remembered)) from exc
    raise BackupError(str(exc)) from exc


def _release_source(conn: object) -> None:
    rollback = getattr(conn, "execute", None)
    close = getattr(conn, "close", None)
    if rollback is not None:
        try:
            list(rollback("ROLLBACK"))
        except Exception:
            pass
    if close is not None:
        try:
            close()
        except Exception:
            pass


def write_consistent_gzip(
    src: Path,
    work: Path,
    part: Path,
    *,
    budget: int,
    deadline: Deadline,
) -> None:
    """backup API の宛先を圧縮し、quick_check してから gzip にする。

    ソースは読み取り専用。copy のあいだ読み取りトランザクションを開けたままにする。
    閉じると別プロセスの commit のたびにページ 1 からやり直す。
    """
    import apsw

    from jobs.ops.backup_store import CompressVFS, StoreError

    work.parent.mkdir(parents=True, exist_ok=True)
    _remove_work(work)
    vfs = CompressVFS(f"backup{os.getpid()}{time.monotonic_ns()}", budget)
    src_conn = None
    dest = None
    try:
        deadline.enter("copy")
        print("backup_db stage=copy start", flush=True)
        started = time.monotonic()
        src_conn = apsw.Connection(str(src), flags=apsw.SQLITE_OPEN_READONLY)
        src_conn.set_busy_timeout(BUSY_TIMEOUT_MS)
        list(src_conn.execute("PRAGMA query_only=ON"))
        list(src_conn.execute("BEGIN"))
        list(src_conn.execute("SELECT 1 FROM sqlite_master LIMIT 1"))
        flags = apsw.SQLITE_OPEN_READWRITE | apsw.SQLITE_OPEN_CREATE
        dest = apsw.Connection(str(work), flags=flags, vfs=vfs.vfs_name)
        dest.set_busy_timeout(BUSY_TIMEOUT_MS)
        backup = dest.backup("main", src_conn, "main")
        busy_logged = False
        failed: BackupError | None = None
        try:
            while True:
                deadline.check()
                try:
                    done = backup.step(BACKUP_PAGES)
                except apsw.BusyError:
                    if not busy_logged:
                        print("backup_db stage=copy busy", flush=True)
                        busy_logged = True
                    time.sleep(BACKUP_SLEEP_S)
                    continue
                except apsw.Error as exc:
                    _raise_vfs(vfs, exc)
                if done:
                    break
        except BackupError as exc:
            failed = exc
        try:
            backup.finish()
        except apsw.Error as exc:
            if failed is None:
                _raise_vfs(vfs, exc)
        if failed is not None:
            raise failed
        list(src_conn.execute("COMMIT"))
        src_conn.close()
        src_conn = None
        store = vfs.main
        if store is None:
            raise BackupError("compress store missing")
        store.flush()
        print(
            f"backup_db stage=copy done bytes={store.allocated()} logical={store.logical} "
            f"elapsed_s={_elapsed(started)}",
            flush=True,
        )
        deadline.enter("quick_check")
        print("backup_db stage=quick_check start", flush=True)
        checked_at = time.monotonic()
        dest.set_progress_handler(deadline.over, 100_000)
        try:
            mode = list(dest.execute("PRAGMA journal_mode=DELETE"))
            checked = list(dest.execute("PRAGMA quick_check"))
        except apsw.InterruptError as exc:
            raise BackupError(f"timeout at {deadline.stage}") from exc
        except apsw.Error as exc:
            _raise_vfs(vfs, exc)
        msg = " ".join(str(row[0]) for row in checked) if checked else "empty"
        if msg != "ok":
            raise BackupError(f"quick_check {msg}")
        if not mode or str(mode[0][0]).lower() != "delete":
            raise BackupError(f"journal_mode {mode}")
        print(
            f"backup_db stage=quick_check done ok elapsed_s={_elapsed(checked_at)}",
            flush=True,
        )
        deadline.enter("gzip")
        print("backup_db stage=gzip start", flush=True)
        gzip_at = time.monotonic()
        _gzip_store(store, part, budget=budget, deadline=deadline)
        print(
            f"backup_db stage=gzip done bytes={part.stat().st_size} elapsed_s={_elapsed(gzip_at)}",
            flush=True,
        )
    except StoreError as exc:
        raise BackupError(str(exc)) from exc
    except OSError as exc:
        raise BackupError(_disk_message(exc)) from exc
    finally:
        if src_conn is not None:
            _release_source(src_conn)
        for close in (
            None if dest is None else dest.close,
            None if vfs.main is None else vfs.main.close,
            vfs.unregister,
        ):
            if close is None:
                continue
            try:
                close()
            except Exception:
                pass
        _remove_work(work)


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
    deadline: Deadline | None = None,
) -> None:
    if not settings.enabled:
        raise OffsiteSkip("BACKUP_OFFSITE disabled")
    if not rclone_available(settings.rclone_bin, which):
        raise OffsiteSkip("rclone not installed")
    result = _call_rclone(run, ["listremotes"], RCLONE_PROBE_TIMEOUT_S, deadline)
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


def _call_rclone(
    run: RcloneRunFn,
    argv: Sequence[str],
    cap: float,
    deadline: Deadline | None,
) -> CmdResult:
    if deadline is None:
        return run(argv, cap)
    timeout = deadline.allow(cap)
    limited_by_overall = timeout < cap
    try:
        return run(argv, timeout)
    except BackupError as exc:
        if limited_by_overall and str(exc).startswith("rclone timeout"):
            raise BackupError(f"timeout at {deadline.stage}") from exc
        raise


def _hash_unavailable(text: str) -> bool:
    low = text.lower()
    needles = (
        "hash not supported",
        "hash unsupported",
        "unsupported hash",
        "hash type not supported",
        "failed to calculate hash",
        "could not calculate hash",
        "no hash",
    )
    return any(needle in low for needle in needles)


def _check_difference(text: str) -> bool:
    low = text.lower()
    return "differ" in low or "corrupted on transfer" in low


def copy_and_verify(
    local: Path,
    kind: str,
    settings: OffsiteSettings,
    run: RcloneRunFn,
    *,
    copy_timeout: float = RCLONE_COPY_TIMEOUT_S,
    check_timeout: float = RCLONE_CHECK_TIMEOUT_S,
    deadline: Deadline | None = None,
) -> None:
    dest_dir = settings.dest(kind)
    flags = rclone_flags()
    copied = _call_rclone(run, ["copy", str(local), dest_dir, *flags], copy_timeout, deadline)
    if copied.returncode != 0:
        raise BackupError(
            f"rclone copy {kind} exit={copied.returncode} {clip_cmd_output(copied.stderr)}"
        )
    checked = _call_rclone(
        run,
        [
            "check",
            str(local.parent),
            dest_dir,
            "--include",
            f"/{local.name}",
            "--one-way",
            *flags,
        ],
        check_timeout,
        deadline,
    )
    if checked.returncode == 0:
        print(f"offsite ok {kind} {local.name} bytes={local.stat().st_size}", flush=True)
        return
    detail = f"{checked.stdout}\n{checked.stderr}"
    local_size = local.stat().st_size
    remote_size: int | None = None
    if not _check_difference(detail) and _hash_unavailable(detail):
        listed = _call_rclone(
            run,
            ["lsjson", dest_dir, "--files-only", "--include", local.name, *flags],
            RCLONE_LIST_TIMEOUT_S,
            deadline,
        )
        remote_size = _lsjson_size(listed.stdout) if listed.returncode == 0 else None
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
    *,
    deadline: Deadline | None = None,
) -> list[str]:
    dest_dir = settings.dest(kind)
    flags = rclone_flags()
    listed = _call_rclone(run, ["lsf", "--files-only", dest_dir, *flags], RCLONE_LIST_TIMEOUT_S, deadline)
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
        deleted = _call_rclone(
            run, ["deletefile", f"{dest_dir}/{name}", *flags], RCLONE_LIST_TIMEOUT_S, deadline
        )
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
    copy_timeout: float = RCLONE_COPY_TIMEOUT_S,
    check_timeout: float = RCLONE_CHECK_TIMEOUT_S,
    deadline: Deadline | None = None,
) -> None:
    probe_offsite(settings, which=which, run=run, deadline=deadline)
    for path, kind in items:
        copy_and_verify(
            path,
            kind,
            settings,
            run,
            copy_timeout=copy_timeout,
            check_timeout=check_timeout,
            deadline=deadline,
        )
    for kind, count in (("daily", keep.offsite_daily), ("weekly", keep.offsite_weekly)):
        removed = prune_offsite(kind, count, settings, run, deadline=deadline)
        print(f"offsite pruned {kind}={len(removed)}", flush=True)


def default_offsite(
    items: list[tuple[Path, str]],
    keep: BackupKeep,
    *,
    settings: OffsiteSettings,
    which: WhichFn | None = None,
    run: RcloneRunFn | None = None,
    copy_timeout: float = OFFSITE_COPY_TIMEOUT_S,
    check_timeout: float = OFFSITE_CHECK_TIMEOUT_S,
    deadline: Deadline | None = None,
) -> None:
    runner = run or make_rclone_runner(settings.rclone_bin)
    sync_offsite(
        items,
        keep=keep,
        settings=settings,
        which=which or shutil.which,
        run=runner,
        copy_timeout=copy_timeout,
        check_timeout=check_timeout,
        deadline=deadline,
    )


def run(
    db: Path,
    dest: Path,
    *,
    now: datetime | None = None,
    free_bytes: FreeBytesFn | None = None,
    snapshot: SnapshotFn | None = None,
    offsite: OffsiteFn | bool | None = None,
    keep: BackupKeep | None = None,
    environ: Mapping[str, str] | None = None,
    limits: BackupLimits | None = None,
) -> int:
    when = now or datetime.now(JST)
    day = format_day(when)
    started = time.monotonic()
    print(f"backup_db start db={db} dest={dest} day={day}", flush=True)
    try:
        with dest_lock(dest) as acquired:
            if not acquired:
                print("backup_db skip: already running", flush=True)
                return 0
            _run(
                db,
                dest,
                when=when,
                day=day,
                free_bytes=free_bytes or disk_free,
                snapshot=snapshot,
                offsite=offsite,
                keep=keep,
                environ=environ,
                limits=limits or BackupLimits(),
                started=started,
            )
    except BackupError as exc:
        print(f"fail backup_db: {exc}", flush=True)
        return 1
    print(f"backup_db done elapsed_s={_elapsed(started)}", flush=True)
    return 0


def _run(
    db: Path,
    dest: Path,
    *,
    when: datetime,
    day: str,
    free_bytes: FreeBytesFn,
    snapshot: SnapshotFn | None,
    offsite: OffsiteFn | bool | None,
    keep: BackupKeep | None,
    environ: Mapping[str, str] | None,
    limits: BackupLimits,
    started: float,
) -> None:
    env = os.environ if environ is None else environ
    policy = keep if keep is not None else keep_from_env(env)
    settings = offsite_settings_from_env(env)
    deadline = Deadline(limits, started)
    ensure_dest(dest)
    clean_tmp(dest)
    if not db.is_file():
        raise BackupError(f"db missing {db}")
    src_size = source_bytes(db)
    budget = compress_budget(src_size)
    need = required_bytes(src_size)
    free = free_bytes(dest)
    print(
        f"backup_db stage=disk need={need} free={free} src_bytes={src_size} budget={budget}",
        flush=True,
    )
    if free < need:
        raise BackupError(f"insufficient disk need={need} free={free} dest={dest}")

    work = tmp_dir(dest) / f"stock-{day}.{os.getpid()}.db"
    daily = daily_dir(dest) / backup_name(day)
    part = daily.with_name(daily.name + ".part")
    installed = False
    try:
        if snapshot is None:
            write_consistent_gzip(db, work, part, budget=budget, deadline=deadline)
        else:
            snapshot(db, part)
        deadline.enter("gzip-test")
        test_at = time.monotonic()
        _validate_gzip(part, deadline)
        print(f"backup_db stage=gzip-test ok elapsed_s={_elapsed(test_at)}", flush=True)
        os.replace(part, daily)
        installed = True
        print(f"saved {daily} src_bytes={src_size} gzip_bytes={daily.stat().st_size}", flush=True)
        items: list[tuple[Path, str]] = [(daily, "daily")]
        if is_sunday(when):
            weekly = weekly_dir(dest) / backup_name(day)
            how = link_or_copy(daily, weekly)
            print(f"weekly {how} {weekly}", flush=True)
            items.append((weekly, "weekly"))
        try:
            deadline.enter("offsite")
            print("backup_db stage=offsite start", flush=True)
            _push_offsite(items, policy, settings, offsite, limits, deadline)
            print("backup_db stage=offsite done", flush=True)
        except OffsiteSkip as exc:
            print(f"warn backup_db offsite skipped: {exc}", flush=True)
            print("removed local daily=0 weekly=0 offsite=skip", flush=True)
            return
        removed_daily, removed_weekly = drop_uploaded(items)
        print(
            f"removed local daily={removed_daily} weekly={removed_weekly} offsite=ok",
            flush=True,
        )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(str(exc)) from exc
    finally:
        if not installed:
            part.unlink(missing_ok=True)
        _remove_work(work)


def _push_offsite(
    items: list[tuple[Path, str]],
    policy: BackupKeep,
    settings: OffsiteSettings,
    offsite: OffsiteFn | bool | None,
    limits: BackupLimits,
    deadline: Deadline,
) -> None:
    if offsite is False:
        raise OffsiteSkip("offsite disabled")
    if offsite is None:
        default_offsite(
            items,
            policy,
            settings=settings,
            copy_timeout=limits.offsite_copy_s,
            check_timeout=limits.offsite_check_s,
            deadline=deadline,
        )
        return
    offsite(items, policy)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Consistent gzip backup of the local SQLite DB")
    ap.add_argument("--db", default=str(default_db_path()), help="source SQLite path")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="backup directory")
    ap.add_argument(
        "--no-offsite",
        action="store_true",
        help="skip OneDrive copy (keep the new gzip and any older local files)",
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

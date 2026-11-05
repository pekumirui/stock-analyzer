"""会社ごとに最新の決算説明資料から 1 年以上前の PDF を OneDrive へ移す（KAB-345、KAB-355）。

使い方:
  python jobs/market/briefing_offsite.py --dry-run
  python jobs/market/briefing_offsite.py --limit 20
  deploy/cron-run.sh jobs/market/briefing_offsite.py --dry-run

対象はバッチごとに取り直す。1 回の rclone は BATCH_SIZE 件を
copy --files-from（--transfers は TRANSFERS）で送り、check --one-way で
一致したものだけ索引を onedrive にしてから手元を消す。
落ちたあとは同じコマンドを再実行すれば続きから揃う。
失敗したファイルは索引も手元も変えない。行は消さない。
毎日 1 回、deploy/crontab から deploy/cron-run.sh で動く。
同じスクリプトが既に動いているときは移さず、終了コード 0 で終わる。
rclone はテストから差し替える。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from app.db import SessionLocal, init_db, migrate_add_columns  # noqa: E402
from app.market.briefing_files import local_file, safe_rel  # noqa: E402
from app.market.briefing_store import (  # noqa: E402
    STORAGE_LOCAL,
    STORAGE_ONEDRIVE,
    fill_blank_storage,
    mark_storage,
)
from app.models import BriefingDocument  # noqa: E402
from jobs.ops.backup_db import (  # noqa: E402
    RCLONE_CHECK_TIMEOUT_S,
    RCLONE_COPY_TIMEOUT_S,
    BackupError,
    OffsiteSkip,
    RcloneRunFn,
    WhichFn,
    clip_cmd_output,
    make_rclone_runner,
    offsite_settings_from_env,
    probe_offsite,
    rclone_flags,
)  # noqa: E402
from jobs.share_paths import share_path  # noqa: E402

# 1 回に送る件数。少ないと rclone の起動が支配的で、多いと落ちたときのやり直しが大きい。
BATCH_SIZE = 100
TRANSFERS = 4
_COMBINED_MARKS = frozenset("= -+*!")
_FILE_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:_|\.|$)")
# update.sh と同じく /tmp の flock。手で先に動いているプロセスはロックを持たないので、
# それとは別にコマンド行でも見る。
_LOCK_PATH = Path("/tmp/stock-analyzer-briefing_offsite.lock")
_SCRIPT_NAME = "briefing_offsite.py"
_MODULE_NAME = "jobs.market.briefing_offsite"


@dataclass(frozen=True)
class Doc:
    source: str
    document_id: str
    code: str
    rel_path: str
    pubdate: str
    storage: str
    size: int | None
    status: str


def document_day(rel_path: str, pubdate: str) -> date | None:
    """ファイル名先頭の YYYYMMDD。無ければ索引の pubdate。"""
    name = Path(rel_path.replace("\\", "/")).name
    match = _FILE_DAY.match(name)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    text = (pubdate or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def year_before(day: date) -> date:
    """暦の 1 年前。2 月 29 日は 2 月 28 日。"""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


@dataclass(frozen=True)
class LocalPdf:
    rel_path: str
    path: Path


@dataclass(frozen=True)
class Delivered:
    ok: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]


def load_docs() -> list[Doc]:
    db = SessionLocal()
    try:
        rows = db.query(BriefingDocument).all()
        return [
            Doc(
                source=row.source,
                document_id=row.document_id,
                code=row.code or "",
                rel_path=row.rel_path or "",
                pubdate=row.pubdate or "",
                storage=row.storage or STORAGE_LOCAL,
                size=row.size,
                status=row.status or "",
            )
            for row in rows
        ]
    finally:
        db.close()


def candidates(docs: Sequence[Doc], root: Path) -> list[tuple[Doc, date]]:
    """会社の最新日から 1 年以上前の PDF。最新そのものは残る。"""
    dated: dict[str, list[tuple[Doc, date]]] = defaultdict(list)
    for doc in docs:
        if doc.status != "saved" or not doc.rel_path.lower().endswith(".pdf"):
            continue
        day = document_day(doc.rel_path, doc.pubdate)
        if day is None:
            continue
        dated[doc.code].append((doc, day))
    picked: list[tuple[Doc, date]] = []
    for code in sorted(dated):
        rows = dated[code]
        latest = max(day for _, day in rows)
        cutoff = year_before(latest)
        for doc, day in rows:
            if day > cutoff:
                continue
            if doc.storage == STORAGE_ONEDRIVE:
                try:
                    path = local_file(doc.rel_path, root)
                except ValueError:
                    continue
                if not path.is_file():
                    continue
            elif doc.storage != STORAGE_LOCAL:
                continue
            picked.append((doc, day))
    picked.sort(key=lambda item: (item[0].code, item[1], item[0].rel_path, item[0].source, item[0].document_id))
    return picked


def remote_briefing_root(environ: Mapping[str, str] | None = None) -> str:
    """backup と同じ remote の data/briefing。ファイル名は rel_path のまま。"""
    return offsite_settings_from_env(environ).dest("data/briefing")


def _parse_combined(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if len(line) < 3 or line[0] not in _COMBINED_MARKS or line[1] != " ":
            continue
        rel = line[2:].replace("\\", "/").lstrip("./")
        if rel:
            found[rel] = line[0]
    return found


def _mark_of(rel: str, found: Mapping[str, str]) -> str | None:
    if rel in found:
        return found[rel]
    suffix = "/" + rel
    hits = [mark for path, mark in found.items() if path.endswith(suffix)]
    if len(hits) == 1:
        return hits[0]
    return None


def _matched_rels(combined: Path, expect: set[str], returncode: int) -> set[str]:
    """check の報告で一致した rel。報告が無く終了 0 のときは全部。失敗で報告が無ければ空。"""
    found = _parse_combined(combined)
    matched: set[str] = set()
    for rel in expect:
        mark = _mark_of(rel, found)
        if mark == "=" or (mark is None and returncode == 0):
            matched.add(rel)
    return matched


def deliver_matches(
    root: Path,
    files: Sequence[LocalPdf],
    *,
    run: RcloneRunFn,
    commit: Callable[[LocalPdf], None],
    environ: Mapping[str, str] | None = None,
) -> Delivered:
    """copy --files-from のあと check --one-way。一致したものだけ commit してから消す。

    commit が失敗したファイルは消さない。copy が非ゼロでも、check で一致した分は進める。
    check 自体が失敗して報告が無ければ、索引も手元も変えない。
    """
    failed: list[tuple[str, str]] = []
    ready: list[LocalPdf] = []
    seen: set[str] = set()
    for item in files:
        try:
            rel = safe_rel(item.rel_path)
        except ValueError as exc:
            failed.append((item.rel_path, str(exc)))
            continue
        if rel in seen:
            failed.append((rel, "duplicate"))
            continue
        seen.add(rel)
        try:
            path = local_file(rel, root)
        except ValueError as exc:
            failed.append((rel, str(exc)))
            continue
        if not path.is_file():
            failed.append((rel, f"missing {rel}"))
            continue
        ready.append(LocalPdf(rel, path))
    if not ready:
        return Delivered(ok=(), failed=tuple(failed))

    remote = remote_briefing_root(environ)
    list_fd, list_name = tempfile.mkstemp(prefix="briefing-files-", suffix=".txt")
    os.close(list_fd)
    combined_fd, combined_name = tempfile.mkstemp(prefix="briefing-check-", suffix=".txt")
    os.close(combined_fd)
    list_path = Path(list_name)
    combined_path = Path(combined_name)
    try:
        list_path.write_text("".join(f"{item.rel_path}\n" for item in ready), encoding="utf-8")
        flags = rclone_flags()
        copy_argv = [
            "copy", str(root), remote,
            "--files-from", str(list_path),
            "--transfers", str(TRANSFERS),
            *flags,
        ]
        try:
            copied = run(copy_argv, RCLONE_COPY_TIMEOUT_S)
        except (BackupError, OffsiteSkip, OSError) as exc:
            failed.extend((item.rel_path, str(exc)) for item in ready)
            return Delivered(ok=(), failed=tuple(failed))
        check_argv = [
            "check", str(root), remote,
            "--one-way",
            "--files-from", str(list_path),
            "--combined", str(combined_path),
            *flags,
        ]
        try:
            checked = run(check_argv, RCLONE_CHECK_TIMEOUT_S)
        except (BackupError, OffsiteSkip, OSError) as exc:
            failed.extend((item.rel_path, str(exc)) for item in ready)
            return Delivered(ok=(), failed=tuple(failed))
        matched = _matched_rels(combined_path, {item.rel_path for item in ready}, checked.returncode)
        copy_note = ""
        if copied.returncode != 0:
            copy_note = f" copy_exit={copied.returncode} {clip_cmd_output(copied.stderr)}".rstrip()
        ok: list[str] = []
        for item in ready:
            if item.rel_path not in matched:
                failed.append((
                    item.rel_path,
                    f"check exit={checked.returncode}{copy_note} {clip_cmd_output(checked.stderr)}".strip(),
                ))
                continue
            try:
                commit(item)
            except Exception as exc:
                failed.append((item.rel_path, f"index {type(exc).__name__}: {exc}"))
                continue
            try:
                item.path.unlink()
            except OSError as exc:
                failed.append((item.rel_path, f"unlink {exc}"))
                continue
            ok.append(item.rel_path)
        return Delivered(ok=tuple(ok), failed=tuple(failed))
    finally:
        list_path.unlink(missing_ok=True)
        combined_path.unlink(missing_ok=True)


def _is_python_argv0(argv0: str) -> bool:
    base = Path(argv0).name
    if base == "python":
        return True
    if not base.startswith("python"):
        return False
    rest = base[len("python"):]
    return bool(rest) and any(ch.isdigit() for ch in rest) and all(ch.isdigit() or ch == "." for ch in rest)


def _is_this_job(arg: str) -> bool:
    text = arg.replace("\\", "/")
    if text in {_SCRIPT_NAME, _MODULE_NAME}:
        return True
    return text.endswith("/" + _SCRIPT_NAME)


def other_offsite_pids() -> list[int]:
    """このプロセス以外で、同じジョブの Python が動いていればその pid。

    ロック導入より前に手で始めた全件移動もここで拾う。
    テストファイル名（test_briefing_offsite.py）は対象にしない。
    """
    me = os.getpid()
    found: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        argv = [part for part in raw.split(b"\0") if part]
        text = [part.decode("utf-8", "surrogateescape") for part in argv]
        if len(text) < 2 or not _is_python_argv0(text[0]):
            continue
        if any(_is_this_job(arg) for arg in text[1:]):
            found.append(pid)
    return found


@contextmanager
def instance_lock() -> Iterator[bool]:
    """排他ロックを持てたら True。他が持っている、または古いプロセスがいるときは False。

    False のときはロックを残さない。True のあいだは呼び出し側が移し終えるまで持つ。
    """
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = _LOCK_PATH.open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        acquired = True
        if other_offsite_pids():
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            acquired = False
            yield False
            return
        yield True
    finally:
        if acquired:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def run(
    dest: Path | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    environ: Mapping[str, str] | None = None,
    which: WhichFn = shutil.which,
    rclone: RcloneRunFn | None = None,
) -> int:
    with instance_lock() as acquired:
        if not acquired:
            others = other_offsite_pids()
            suffix = f" pids={','.join(str(pid) for pid in others)}" if others else ""
            print(f"briefing_offsite skip already running{suffix}", flush=True)
            return 0
        return _run_once(
            dest,
            dry_run=dry_run,
            limit=limit,
            environ=environ,
            which=which,
            rclone=rclone,
        )


def _run_once(
    dest: Path | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    environ: Mapping[str, str] | None = None,
    which: WhichFn = shutil.which,
    rclone: RcloneRunFn | None = None,
) -> int:
    root = dest if dest is not None else share_path("briefing", environ=environ)
    if not dry_run:
        init_db()
        migrate_add_columns(verbose=False)
        fill_blank_storage()
    eligible = candidates(load_docs(), root)
    print(
        f"briefing_offsite start dry_run={int(dry_run)} eligible={len(eligible)} dest={root}",
        flush=True,
    )
    if dry_run:
        batch = eligible if limit is None else eligible[:limit]
        for doc, day in batch:
            print(f"dry-run {doc.code} {day.isoformat()} {doc.rel_path}", flush=True)
        print(f"briefing_offsite dry-run listed={len(batch)}", flush=True)
        return 0
    if not eligible:
        print("briefing_offsite done moved=0 failed=0", flush=True)
        return 0
    settings = offsite_settings_from_env(environ)
    runner = rclone or make_rclone_runner(settings.rclone_bin)
    if rclone is None:
        try:
            probe_offsite(settings, which=which, run=runner)
        except OffsiteSkip as exc:
            print(f"fail briefing_offsite: {exc}", flush=True)
            return 1
        except BackupError as exc:
            print(f"fail briefing_offsite: {exc}", flush=True)
            return 1
    moved = failed = attempted = 0
    failed_ids: set[tuple[str, str]] = set()
    while True:
        pending = [
            item for item in candidates(load_docs(), root)
            if (item[0].source, item[0].document_id) not in failed_ids
        ]
        if limit is not None:
            room = limit - attempted
            if room <= 0:
                break
            pending = pending[:room]
        if not pending:
            break
        batch = pending[:BATCH_SIZE]
        attempted += len(batch)
        print(f"briefing_offsite batch files={len(batch)}", flush=True)
        by_rel: dict[str, tuple[Doc, date, tuple[str, str]]] = {}
        files: list[LocalPdf] = []
        seen_rel: set[str] = set()
        for doc, day in batch:
            key = (doc.source, doc.document_id)
            if doc.rel_path in seen_rel:
                failed += 1
                failed_ids.add(key)
                print(f"fail {doc.rel_path}: duplicate", flush=True)
                continue
            seen_rel.add(doc.rel_path)
            try:
                path = local_file(doc.rel_path, root)
            except ValueError as exc:
                failed += 1
                failed_ids.add(key)
                print(f"fail {doc.rel_path}: {exc}", flush=True)
                continue
            files.append(LocalPdf(doc.rel_path, path))
            by_rel[doc.rel_path] = (doc, day, key)
        if not files:
            continue

        def commit(item: LocalPdf, keys: Mapping[str, tuple[Doc, date, tuple[str, str]]] = by_rel) -> None:
            doc, _day, _key = keys[item.rel_path]
            mark_storage(doc.source, doc.document_id, STORAGE_ONEDRIVE)

        result = deliver_matches(root, files, run=runner, commit=commit, environ=environ)
        for rel in result.ok:
            doc, day, _key = by_rel[rel]
            moved += 1
            print(f"moved {doc.code} {day.isoformat()} {rel}", flush=True)
        for rel, reason in result.failed:
            doc, _day, key = by_rel[rel]
            failed += 1
            failed_ids.add(key)
            print(f"fail {rel}: {reason}", flush=True)
    print(f"briefing_offsite done moved={moved} failed={failed}", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Move briefing PDFs older than one year to OneDrive")
    ap.add_argument("--dest", default=None, help="data/briefing の代わり")
    ap.add_argument("--dry-run", action="store_true", help="対象を出すだけ。コピーも索引も削除もしない")
    ap.add_argument("--limit", type=int, default=None, help="この実行で手を付ける最大件数")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit は 1 以上")
    dest = Path(args.dest) if args.dest else None
    try:
        return run(dest, dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:
        print(f"fail briefing_offsite: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

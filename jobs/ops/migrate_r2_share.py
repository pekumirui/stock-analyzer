"""FUSE の共有ツリーを data/ へコピーする。

src も dest も消さない。dest にだけあるファイルは残す。
同じサイズかつ同じ sha256 は書かない。中身が違うファイルは --force のときだけ置き換える。
書き込みは sibling の `.part` に終えてから `Path.replace` する。途中で落ちても dest の完成ファイルは前の中身のまま。
`.part` の sha256 が src と違うときは dest を置き換えない。
`xbrl/_cache` は既定でコピーしない。外部コマンドは呼ばない。app は import しない。
dest が FUSE 上、または src と同じ場所なら exit 2。src が無い・読めないときは exit 1。
不一致は相対パスと src/dest の size・短い sha256 を 1 行ずつ出す。多いときは先頭 N 件と件数。
`--list-mismatches` はコピーせず不一致だけ出して終わる。

使い方:
  python jobs/ops/migrate_r2_share.py
  python jobs/ops/migrate_r2_share.py --src /home/stock/r2-share --dest /home/stock/stock-analyzer/data
  python jobs/ops/migrate_r2_share.py --list-mismatches
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from jobs._r2common import path_on_r2_share, r2_share_root
from jobs.share_paths import share_root

_CHUNK = 1024 * 1024
EXIT_IO = 1
EXIT_MISCONFIG = 2
SHA_SHORT = 12
MISMATCH_LOG_LIMIT = 50


class Mismatch:
    def __init__(
        self,
        path: str,
        src_size: int,
        dest_size: int | None,
        src_sha: str,
        dest_sha: str | None,
    ) -> None:
        self.path = path
        self.src_size = src_size
        self.dest_size = dest_size
        self.src_sha = src_sha
        self.dest_sha = dest_sha


class Summary:
    def __init__(self) -> None:
        self.files_src = 0
        self.files_dest_copied_this_run = 0
        self.files_skipped_identical = 0
        self.files_mismatched = 0
        self.bytes_copied = 0
        self.bytes_src = 0
        self.skipped_cache = 0
        self.mismatches: list[Mismatch] = []


def format_summary(summary: Summary) -> str:
    return (
        f"files_src={summary.files_src} "
        f"files_dest_copied_this_run={summary.files_dest_copied_this_run} "
        f"files_skipped_identical={summary.files_skipped_identical} "
        f"files_mismatched={summary.files_mismatched} "
        f"bytes_copied={summary.bytes_copied} "
        f"bytes_src={summary.bytes_src} "
        f"skipped_cache={summary.skipped_cache}"
    )


def _short_sha(digest: str | None) -> str:
    if not digest:
        return "-"
    return digest[:SHA_SHORT]


def format_mismatch(item: Mismatch) -> str:
    dest_size = "-" if item.dest_size is None else str(item.dest_size)
    return (
        f"mismatch path={item.path} "
        f"src_size={item.src_size} dest_size={dest_size} "
        f"src_sha={_short_sha(item.src_sha)} dest_sha={_short_sha(item.dest_sha)}"
    )


def log_mismatches(mismatches: list[Mismatch], *, limit: int = MISMATCH_LOG_LIMIT) -> None:
    for item in mismatches[:limit]:
        print(format_mismatch(item), flush=True)
    total = len(mismatches)
    if total > limit:
        print(f"mismatch_truncated showing={limit} total={total}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def _is_xbrl_cache(rel: Path) -> bool:
    parts = rel.parts
    return len(parts) >= 2 and parts[0] == "xbrl" and parts[1] == "_cache"


def _dest_stats(path: Path) -> tuple[int | None, str | None]:
    try:
        if path.is_symlink() or not path.is_file():
            return None, None
        return path.stat().st_size, _sha256(path)
    except OSError:
        return None, None


def _copy_atomic(src: Path, dest: Path, expected_sha: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        with src.open("rb") as rf, tmp.open("wb") as wf:
            for chunk in iter(lambda: rf.read(_CHUNK), b""):
                wf.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected_sha:
            raise OSError("copied bytes do not match src sha256")
        tmp.replace(dest)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _record_mismatch(
    summary: Summary,
    rel: Path,
    src_size: int,
    dest_size: int | None,
    src_sha: str,
    dest_sha: str | None,
) -> None:
    summary.files_mismatched += 1
    summary.mismatches.append(
        Mismatch(rel.as_posix(), src_size, dest_size, src_sha, dest_sha)
    )


def validate_roots(src: Path, dest: Path) -> str | None:
    src_r = Path(src).expanduser().resolve()
    dest_r = Path(dest).expanduser().resolve()
    if src_r == dest_r:
        return "dest must not equal src"
    if path_on_r2_share(dest_r):
        return "dest must not sit on the FUSE mount"
    return None


def migrate(
    src: Path,
    dest: Path,
    *,
    include_cache: bool = False,
    force: bool = False,
    list_mismatches: bool = False,
) -> tuple[int, Summary]:
    summary = Summary()
    src = Path(src)
    dest = Path(dest)
    misconfig = validate_roots(src, dest)
    if misconfig:
        print(misconfig, file=sys.stderr, flush=True)
        return EXIT_MISCONFIG, summary
    try:
        if not src.is_dir():
            return EXIT_IO, summary
    except OSError:
        return EXIT_IO, summary
    for path in _iter_files(src):
        rel = path.relative_to(src)
        size = path.stat().st_size
        summary.files_src += 1
        summary.bytes_src += size
        if not include_cache and _is_xbrl_cache(rel):
            summary.skipped_cache += 1
            continue
        target = dest / rel
        src_sha = _sha256(path)
        dest_size, dest_sha = _dest_stats(target)
        if dest_size is not None and dest_size == size and dest_sha == src_sha:
            summary.files_skipped_identical += 1
            continue
        dest_present = target.exists() or target.is_symlink()
        if list_mismatches:
            if dest_present:
                _record_mismatch(summary, rel, size, dest_size, src_sha, dest_sha)
            continue
        if dest_present and not force:
            _record_mismatch(summary, rel, size, dest_size, src_sha, dest_sha)
            continue
        try:
            _copy_atomic(path, target, src_sha)
        except OSError:
            after_size, after_sha = _dest_stats(target)
            _record_mismatch(summary, rel, size, after_size, src_sha, after_sha)
            continue
        summary.files_dest_copied_this_run += 1
        summary.bytes_copied += size
    log_mismatches(summary.mismatches)
    if summary.files_mismatched:
        return EXIT_IO, summary
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy the FUSE share tree into data/")
    ap.add_argument("--src", default=None, help="source tree (default: FUSE r2_share_root())")
    ap.add_argument("--dest", default=None, help="destination tree (default: share_root())")
    ap.add_argument("--include-cache", action="store_true", help="also copy xbrl/_cache")
    ap.add_argument("--force", action="store_true", help="replace dest files whose sha256 differs")
    ap.add_argument(
        "--list-mismatches",
        action="store_true",
        help="print mismatches and exit without copying",
    )
    args = ap.parse_args(argv)
    src = Path(args.src) if args.src else r2_share_root()
    dest = Path(args.dest) if args.dest else share_root()
    code, summary = migrate(
        src,
        dest,
        include_cache=args.include_cache,
        force=args.force,
        list_mismatches=args.list_mismatches,
    )
    print(format_summary(summary), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

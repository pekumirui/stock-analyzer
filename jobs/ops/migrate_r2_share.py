"""FUSE の共有ツリーを data/ へコピーする。

src も dest も消さない。dest にだけあるファイルは残す。
同じサイズかつ同じ sha256 は書かない。中身が違うファイルは --force のときだけ置き換える。
書き込みは sibling の `.part` に終えてから `Path.replace` する。途中で落ちても dest の完成ファイルは前の中身のまま。
`.part` の sha256 が src と違うときは dest を置き換えない。
`xbrl/_cache` は既定でコピーしない。外部コマンドは呼ばない。app は import しない。
dest が FUSE 上、または src と同じ場所なら exit 2。src が無い・読めないときは exit 1。

使い方:
  python jobs/ops/migrate_r2_share.py
  python jobs/ops/migrate_r2_share.py --src /home/stock/r2-share --dest /home/stock/stock-analyzer/data
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

from jobs._r2common import path_on_r2_share, r2_share_root  # noqa: E402
from jobs.share_paths import share_root  # noqa: E402

_CHUNK = 1024 * 1024
EXIT_IO = 1
EXIT_MISCONFIG = 2


class Summary:
    def __init__(self) -> None:
        self.files_src = 0
        self.files_dest_copied_this_run = 0
        self.files_skipped_identical = 0
        self.files_mismatched = 0
        self.bytes_copied = 0
        self.bytes_src = 0
        self.skipped_cache = 0


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
        if (
            not target.is_symlink()
            and target.is_file()
            and target.stat().st_size == size
            and _sha256(target) == src_sha
        ):
            summary.files_skipped_identical += 1
            continue
        if (target.exists() or target.is_symlink()) and not force:
            summary.files_mismatched += 1
            continue
        try:
            _copy_atomic(path, target, src_sha)
        except OSError:
            summary.files_mismatched += 1
            continue
        summary.files_dest_copied_this_run += 1
        summary.bytes_copied += size
    if summary.files_mismatched:
        return EXIT_IO, summary
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy the FUSE share tree into data/")
    ap.add_argument("--src", default=None, help="source tree (default: FUSE r2_share_root())")
    ap.add_argument("--dest", default=None, help="destination tree (default: share_root())")
    ap.add_argument("--include-cache", action="store_true", help="also copy xbrl/_cache")
    ap.add_argument("--force", action="store_true", help="replace dest files whose sha256 differs")
    args = ap.parse_args(argv)
    src = Path(args.src) if args.src else r2_share_root()
    dest = Path(args.dest) if args.dest else share_root()
    code, summary = migrate(src, dest, include_cache=args.include_cache, force=args.force)
    print(format_summary(summary), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())

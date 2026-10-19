"""FUSE 共有ツリーを data/ へコピーする。rclone も app も使わない。"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.ops.migrate_r2_share import main  # noqa: E402

_KEYS = (
    "files_src",
    "files_dest_copied_this_run",
    "files_skipped_identical",
    "files_mismatched",
    "bytes_copied",
    "bytes_src",
    "skipped_cache",
)


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _run(argv: list[str]) -> tuple[int, dict[str, int]]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if len(lines) != 1:
        _fail(f"summary lines {lines!r}")
    summary: dict[str, int] = {}
    for part in lines[0].split():
        key, _, raw = part.partition("=")
        summary[key] = int(raw)
    missing = [key for key in _KEYS if key not in summary]
    if missing:
        _fail(f"summary missing {missing}: {lines[0]}")
    return rc, summary


def _snapshot(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not root.exists():
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            out[str(path.relative_to(root))] = path.read_bytes()
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_copy_is_idempotent_and_skips_cache(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "9279" / "a.pdf", b"pdf")
    _write(src / "xbrl" / "7203" / "f.zip", b"zip!")
    _write(src / "xbrl" / "_cache" / "t.xsd", b"xsd")
    _write(src / "xbrl" / "_cache" / "nested" / "u.xsd", b"uu")
    _write(src / "xbrl" / "_cachefile.txt", b"edge")
    _write(src / "inventory" / "vps-packages.txt", b"inv")
    _write(dest / "monthly" / "extra.txt", b"new-on-dest")
    before = _snapshot(src)

    rc, summary = _run(["--src", str(src), "--dest", str(dest)])
    if rc != 0:
        _fail(f"copy rc {rc} {summary}")
    if summary["files_src"] != 6 or summary["skipped_cache"] != 2:
        _fail(f"counts {summary}")
    if summary["files_dest_copied_this_run"] != 4 or summary["bytes_copied"] != 14:
        _fail(f"copied {summary}")
    if summary["bytes_src"] != 19 or summary["files_mismatched"] != 0:
        _fail(f"src bytes {summary}")
    if _snapshot(src) != before:
        _fail("src changed")
    if (dest / "monthly" / "extra.txt").read_bytes() != b"new-on-dest":
        _fail("dest extra removed")
    if (dest / "xbrl" / "_cache").exists():
        _fail("cache was copied")
    if (dest / "xbrl" / "_cachefile.txt").read_bytes() != b"edge":
        _fail("xbrl/_cachefile.txt must copy")
    if (dest / "monthly" / "9279" / "a.pdf").read_bytes() != b"pdf":
        _fail("pdf missing")

    rc2, again = _run(["--src", str(src), "--dest", str(dest)])
    if rc2 != 0 or again["files_dest_copied_this_run"] != 0:
        _fail(f"rerun {rc2} {again}")
    if again["files_skipped_identical"] != 4 or again["skipped_cache"] != 2:
        _fail(f"rerun counts {again}")
    if _snapshot(src) != before:
        _fail("src changed on rerun")


def test_include_cache_copies_xbrl_cache(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "xbrl" / "_cache" / "t.xsd", b"xsd")
    rc, summary = _run(["--src", str(src), "--dest", str(dest), "--include-cache"])
    if rc != 0 or summary["skipped_cache"] != 0 or summary["files_dest_copied_this_run"] != 1:
        _fail(f"include-cache {rc} {summary}")
    if (dest / "xbrl" / "_cache" / "t.xsd").read_bytes() != b"xsd":
        _fail("cache file missing")


def test_mismatch_without_force_leaves_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "a.txt", b"aaaa")
    _write(src / "monthly" / "b.txt", b"bbbb")
    _write(dest / "monthly" / "a.txt", b"aaaa")
    _write(dest / "monthly" / "b.txt", b"cccc")
    before_src = _snapshot(src)
    rc, summary = _run(["--src", str(src), "--dest", str(dest)])
    if rc != 1:
        _fail(f"mismatch rc {rc}")
    if summary["files_mismatched"] != 1 or summary["files_dest_copied_this_run"] != 0:
        _fail(f"mismatch counts {summary}")
    if summary["files_skipped_identical"] != 1:
        _fail(f"identical not skipped {summary}")
    if (dest / "monthly" / "b.txt").read_bytes() != b"cccc":
        _fail("dest replaced without --force")
    if (dest / "monthly" / "b.txt.part").exists():
        _fail("partial write without --force")
    if _snapshot(src) != before_src:
        _fail("src changed on mismatch")


def test_force_replaces_mismatched_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "b.txt", b"bbbb")
    _write(dest / "monthly" / "b.txt", b"cccc")
    before_src = _snapshot(src)
    rc, summary = _run(["--src", str(src), "--dest", str(dest), "--force"])
    if rc != 0 or summary["files_mismatched"] != 0 or summary["files_dest_copied_this_run"] != 1:
        _fail(f"force {rc} {summary}")
    if (dest / "monthly" / "b.txt").read_bytes() != b"bbbb":
        _fail("force did not replace dest")
    if (dest / "monthly" / "b.txt.part").exists():
        _fail("part file left after replace")
    if _snapshot(src) != before_src:
        _fail("src changed on force")


def test_stale_part_is_replaced_into_place(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "a.txt", b"done")
    _write(dest / "monthly" / "a.txt.part", b"partial")
    rc, summary = _run(["--src", str(src), "--dest", str(dest)])
    if rc != 0 or summary["files_dest_copied_this_run"] != 1:
        _fail(f"part recovery {rc} {summary}")
    if (dest / "monthly" / "a.txt").read_bytes() != b"done":
        _fail("recovered file")
    if (dest / "monthly" / "a.txt.part").exists():
        _fail("stale part remains")


def test_cli_defaults_follow_env(tmp_path: Path) -> None:
    src = tmp_path / "fuse"
    dest = tmp_path / "share"
    _write(src / "monthly" / "a.txt", b"abc")
    old_r2 = os.environ.get("R2_SHARE_ROOT")
    old_share = os.environ.get("SHARE_ROOT")
    os.environ["R2_SHARE_ROOT"] = str(src)
    os.environ["SHARE_ROOT"] = str(dest)
    try:
        rc, summary = _run([])
    finally:
        if old_r2 is None:
            os.environ.pop("R2_SHARE_ROOT", None)
        else:
            os.environ["R2_SHARE_ROOT"] = old_r2
        if old_share is None:
            os.environ.pop("SHARE_ROOT", None)
        else:
            os.environ["SHARE_ROOT"] = old_share
    if rc != 0 or summary["files_dest_copied_this_run"] != 1:
        _fail(f"defaults {rc} {summary}")
    if (dest / "monthly" / "a.txt").read_bytes() != b"abc":
        _fail("default dest missed the file")


def test_missing_src_exits_nonzero(tmp_path: Path) -> None:
    rc, summary = _run(["--src", str(tmp_path / "missing"), "--dest", str(tmp_path / "dest")])
    if rc == 0 or summary["files_src"] != 0 or summary["files_dest_copied_this_run"] != 0:
        _fail(f"missing src {rc} {summary}")


def test_script_does_not_import_app_or_rclone() -> None:
    text = (ROOT / "jobs" / "ops" / "migrate_r2_share.py").read_text(encoding="utf-8")
    for needle in ("import app", "from app", "rclone", "subprocess"):
        if needle in text:
            _fail(f"migrate must not reference {needle}")

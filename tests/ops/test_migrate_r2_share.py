"""FUSE 共有ツリーを data/ へコピーする。rclone も app も使わない。"""
from __future__ import annotations

import hashlib
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.ops.migrate_r2_share import (
    EXIT_IO,
    EXIT_MISCONFIG,
    MISMATCH_LOG_LIMIT,
    SHA_SHORT,
    Mismatch,
    _copy_atomic,
    format_mismatch,
    main,
)

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


def _short_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:SHA_SHORT]


def _run_output(argv: list[str]) -> tuple[int, dict[str, int], str, str]:
    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = main(argv)
    out = buf.getvalue()
    err_s = err.getvalue()
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if rc == EXIT_MISCONFIG:
        return rc, {key: 0 for key in _KEYS}, out, err_s
    summary_lines = [ln for ln in lines if ln.startswith("files_src=")]
    if len(summary_lines) != 1:
        _fail(f"summary lines {lines!r} stderr={err_s!r}")
    summary: dict[str, int] = {}
    for part in summary_lines[0].split():
        key, _, raw = part.partition("=")
        summary[key] = int(raw)
    missing = [key for key in _KEYS if key not in summary]
    if missing:
        _fail(f"summary missing {missing}: {summary_lines[0]}")
    return rc, summary, out, err_s


def _run(argv: list[str]) -> tuple[int, dict[str, int]]:
    rc, summary, _out, _err = _run_output(argv)
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


def test_copy_atomic_refuses_when_temp_hash_mismatches(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dest = tmp_path / "dest.txt"
    src.write_bytes(b"hello")
    dest.write_bytes(b"keep-me")
    try:
        _copy_atomic(src, dest, "0" * 64)
    except OSError:
        pass
    else:
        _fail("hash mismatch must raise")
    if dest.read_bytes() != b"keep-me":
        _fail("dest replaced despite hash mismatch")
    if dest.with_name(dest.name + ".part").exists():
        _fail("part left after hash mismatch")


def test_mismatch_without_force_leaves_dest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "a.txt", b"aaaa")
    _write(src / "monthly" / "b.txt", b"bbbb")
    _write(dest / "monthly" / "a.txt", b"aaaa")
    _write(dest / "monthly" / "b.txt", b"cccc")
    before_src = _snapshot(src)
    rc, summary, out, _err = _run_output(["--src", str(src), "--dest", str(dest)])
    if rc != EXIT_IO:
        _fail(f"mismatch rc {rc}")
    if summary["files_mismatched"] != 1 or summary["files_dest_copied_this_run"] != 0:
        _fail(f"mismatch counts {summary}")
    if summary["files_skipped_identical"] != 1:
        _fail(f"identical not skipped {summary}")
    want = (
        f"mismatch path=monthly/b.txt src_size=4 dest_size=4 "
        f"src_sha={_short_sha(b'bbbb')} dest_sha={_short_sha(b'cccc')}"
    )
    if want not in out.splitlines():
        _fail(f"mismatch log missing {want!r}: {out!r}")
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
    if rc != EXIT_IO or summary["files_src"] != 0 or summary["files_dest_copied_this_run"] != 0:
        _fail(f"missing src {rc} {summary}")


def test_dest_on_fuse_exits_misconfig(tmp_path: Path) -> None:
    fuse = tmp_path / "fuse"
    src = tmp_path / "src"
    dest = fuse / "monthly"
    _write(src / "monthly" / "a.txt", b"abc")
    dest.mkdir(parents=True)
    old = os.environ.get("R2_SHARE_ROOT")
    os.environ["R2_SHARE_ROOT"] = str(fuse)
    try:
        rc, summary = _run(["--src", str(src), "--dest", str(dest)])
    finally:
        if old is None:
            os.environ.pop("R2_SHARE_ROOT", None)
        else:
            os.environ["R2_SHARE_ROOT"] = old
    if rc != EXIT_MISCONFIG:
        _fail(f"dest on fuse rc {rc} {summary}")
    if list(dest.iterdir()):
        _fail("dest on fuse must not be written")
    if not (src / "monthly" / "a.txt").is_file():
        _fail("src changed on dest-on-fuse")


def test_dest_equal_src_exits_misconfig(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    _write(tree / "monthly" / "a.txt", b"abc")
    before = _snapshot(tree)
    rc, summary = _run(["--src", str(tree), "--dest", str(tree)])
    if rc != EXIT_MISCONFIG:
        _fail(f"dest==src rc {rc} {summary}")
    if _snapshot(tree) != before:
        _fail("src/dest tree changed when dest equals src")


def test_script_does_not_call_rclone_or_subprocess() -> None:
    text = (ROOT / "jobs" / "ops" / "migrate_r2_share.py").read_text(encoding="utf-8")
    for needle in ("rclone", "subprocess"):
        if needle in text:
            _fail(f"migrate must not reference {needle}")


def test_list_mismatches_does_not_copy(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "xbrl" / "manifest" / "2026-10-19.jsonl", b"src-manifest")
    _write(src / "monthly" / "new.txt", b"only-src")
    _write(dest / "xbrl" / "manifest" / "2026-10-19.jsonl", b"dest-manifest")
    before_dest = _snapshot(dest)
    rc, summary, out, _err = _run_output(
        ["--src", str(src), "--dest", str(dest), "--list-mismatches", "--force"]
    )
    if rc != EXIT_IO:
        _fail(f"list-mismatches rc {rc} {summary}")
    if summary["files_mismatched"] != 1 or summary["files_dest_copied_this_run"] != 0:
        _fail(f"list-mismatches counts {summary}")
    if summary["files_src"] != 2:
        _fail(f"list-mismatches src count {summary}")
    want = (
        "mismatch path=xbrl/manifest/2026-10-19.jsonl "
        f"src_size={len(b'src-manifest')} dest_size={len(b'dest-manifest')} "
        f"src_sha={_short_sha(b'src-manifest')} dest_sha={_short_sha(b'dest-manifest')}"
    )
    if want not in out.splitlines():
        _fail(f"list-mismatches log missing {want!r}: {out!r}")
    if "monthly/new.txt" in out:
        _fail(f"missing dest is not a mismatch: {out!r}")
    if _snapshot(dest) != before_dest:
        _fail("list-mismatches copied or changed dest")
    if (dest / "monthly" / "new.txt").exists():
        _fail("list-mismatches copied a new file")


def test_list_mismatches_identical_exits_zero(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _write(src / "monthly" / "a.txt", b"same")
    _write(dest / "monthly" / "a.txt", b"same")
    before_dest = _snapshot(dest)
    rc, summary, out, _err = _run_output(
        ["--src", str(src), "--dest", str(dest), "--list-mismatches"]
    )
    if rc != 0 or summary["files_mismatched"] != 0 or summary["files_skipped_identical"] != 1:
        _fail(f"identical list {rc} {summary}")
    if any(ln.startswith("mismatch ") for ln in out.splitlines()):
        _fail(f"identical list leaked mismatch: {out!r}")
    if _snapshot(dest) != before_dest:
        _fail("identical list-mismatches changed dest")


def test_mismatch_log_truncated(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    total = MISMATCH_LOG_LIMIT + 1
    for i in range(total):
        name = f"{i:03d}.txt"
        _write(src / "n" / name, b"src")
        _write(dest / "n" / name, b"dst")
    rc, summary, out, _err = _run_output(["--src", str(src), "--dest", str(dest)])
    if rc != EXIT_IO or summary["files_mismatched"] != total:
        _fail(f"truncated counts {rc} {summary}")
    lines = [ln for ln in out.splitlines() if ln.startswith("mismatch path=")]
    if len(lines) != MISMATCH_LOG_LIMIT:
        _fail(f"logged {len(lines)} mismatch lines, want {MISMATCH_LOG_LIMIT}: {lines!r}")
    if f"mismatch_truncated showing={MISMATCH_LOG_LIMIT} total={total}" not in out.splitlines():
        _fail(f"missing truncated marker: {out!r}")
    if "mismatch path=n/000.txt" not in lines[0]:
        _fail(f"first mismatch should be n/000.txt: {lines[0]!r}")
    src_sha = _short_sha(b"src")
    dest_sha = _short_sha(b"dst")
    if f"src_sha={src_sha}" not in lines[0] or f"dest_sha={dest_sha}" not in lines[0]:
        _fail(f"short sha missing: {lines[0]!r}")
    if (dest / "n" / "000.txt").read_bytes() != b"dst":
        _fail("truncated mismatch run overwrote dest")


def test_format_mismatch_uses_dash_when_dest_missing() -> None:
    line = format_mismatch(Mismatch("a.txt", 4, None, "abcd" * 16, None))
    if line != "mismatch path=a.txt src_size=4 dest_size=- src_sha=abcdabcdabcd dest_sha=-":
        _fail(f"format {line!r}")

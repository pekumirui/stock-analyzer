"""backup_data の rclone の呼び方。本番 VPS には出ない。"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.ops.backup_data import (
    KINDS,
    MOVE_KINDS,
    RCLONE_MOVE_MAX_DURATION,
    move_limit_reason,
    run,
)
from jobs.ops.backup_db import BackupError, CmdResult


def _share(tmp_path: Path) -> dict[str, str]:
    for kind in KINDS:
        folder = tmp_path / kind / "1234"
        folder.mkdir(parents=True)
        (folder / "a.zip").write_bytes(b"zip")
    return {"SHARE_ROOT": str(tmp_path)}


def _fake(calls: list[list[str]], *, remotes: str = "onedrive:\n", fail: str | None = None):
    def _run(argv, timeout):
        calls.append(list(argv))
        if argv[0] == "listremotes":
            return CmdResult(0, remotes, "")
        if argv[0] == fail:
            return CmdResult(1, "", f"{fail} broke")
        return CmdResult(0, "", "")

    return _run


def _run_out(**kwargs):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = run(**kwargs)
    return rc, buf.getvalue()


def test_copy_then_check_each_kind_never_sync(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[list[str]] = []
    rc, out = _run_out(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake(calls))
    assert rc == 0
    cmds = [c[0] for c in calls]
    assert cmds == ["listremotes", "copy", "check", "copy", "check", "move"]
    assert all(c[0] not in ("sync", "delete", "deletefile", "purge") for c in calls)
    move = calls[-1]
    assert move[1:3] == [str(tmp_path / "edinet"), "onedrive:stock-analyzer-backups/data/edinet"]
    assert "--min-age" in move and "*.zip" in move
    assert move[move.index("--max-duration") + 1] == RCLONE_MOVE_MAX_DURATION
    assert move[move.index("--cutoff-mode") + 1] == "SOFT"
    copies = [c for c in calls if c[0] == "copy"]
    assert all("--max-duration" not in c for c in copies)
    assert copies[0][1:3] == [str(tmp_path / "xbrl"), "onedrive:stock-analyzer-backups/data/xbrl"]
    assert copies[1][1:3] == [
        str(tmp_path / "monthly"),
        "onedrive:stock-analyzer-backups/data/monthly",
    ]
    checks = [c for c in calls if c[0] == "check"]
    assert all("--one-way" in c for c in checks)
    assert "offsite ok xbrl files=1" in out and "offsite ok monthly files=1" in out
    assert "offsite ok edinet moved=" in out


def test_move_only_edinet(tmp_path: Path) -> None:
    assert MOVE_KINDS == {"edinet"}
    env = _share(tmp_path)
    calls: list[list[str]] = []
    run(["xbrl", "monthly"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake(calls))
    assert "move" not in [c[0] for c in calls]


def test_missing_edinet_dir_is_ok(tmp_path: Path) -> None:
    env = {"SHARE_ROOT": str(tmp_path)}
    calls: list[list[str]] = []
    rc, out = _run_out(kinds=["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone",
                       rclone=_fake(calls))
    assert rc == 0 and "files=0" in out and [c[0] for c in calls] == ["listremotes"]


def test_move_failure_raises(tmp_path: Path) -> None:
    env = _share(tmp_path)
    with pytest.raises(BackupError, match="rclone move edinet exit=1"):
        run(["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake([], fail="move"))


def test_move_python_timeout_still_fails(tmp_path: Path) -> None:
    env = _share(tmp_path)

    def boom(argv, timeout):
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        raise BackupError("rclone timeout move")

    with pytest.raises(BackupError, match="rclone timeout move"):
        run(["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=boom)


def test_move_duration_cap_is_ok_and_keeps_unsent(tmp_path: Path) -> None:
    env = _share(tmp_path)
    leftover = tmp_path / "edinet" / "1234" / "b.zip"
    leftover.write_bytes(b"zip2")

    def capped(argv, timeout):
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "move":
            (tmp_path / "edinet" / "1234" / "a.zip").unlink()
            return CmdResult(10, "", "max transfer duration reached as set by --max-duration")
        return CmdResult(0, "", "")

    rc, out = _run_out(
        kinds=["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=capped
    )
    assert rc == 0
    assert "offsite ok edinet moved=1 left=1" in out and "capped=max-duration" in out
    assert leftover.is_file()


def test_move_transfer_cap_is_ok(tmp_path: Path) -> None:
    env = _share(tmp_path)

    def capped(argv, timeout):
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "move":
            (tmp_path / "edinet" / "1234" / "a.zip").unlink()
            return CmdResult(8, "", "max transfer limit reached as set by --max-transfer")
        return CmdResult(0, "", "")

    rc, out = _run_out(
        kinds=["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=capped
    )
    assert rc == 0 and "capped=max-transfer" in out and "moved=1 left=0" in out


def test_move_cap_with_nothing_moved_fails(tmp_path: Path) -> None:
    env = _share(tmp_path)

    def capped(argv, timeout):
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "move":
            return CmdResult(10, "", "max transfer duration reached as set by --max-duration")
        return CmdResult(0, "", "")

    with pytest.raises(BackupError, match="max-duration moved=0 left=1"):
        run(["edinet"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=capped)


@pytest.mark.parametrize(
    ("code", "stderr", "want"),
    [
        (0, "", None),
        (1, "network broke", None),
        (7, "account suspended", None),
        (8, "", "max-transfer"),
        (10, "", "max-duration"),
        (7, "Max transfer duration reached as set by --max-duration", "max-duration"),
        (7, "max transfer limit reached as set by --max-transfer", "max-transfer"),
    ],
)
def test_move_limit_reason(code: int, stderr: str, want: str | None) -> None:
    assert move_limit_reason(CmdResult(code, "", stderr)) == want


def test_only_limits_kinds(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[list[str]] = []
    run(["xbrl"], environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake(calls))
    assert [c[2] for c in calls if c[0] == "copy"] == ["onedrive:stock-analyzer-backups/data/xbrl"]


def test_missing_remote_or_rclone_skips(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[list[str]] = []
    rc, out = _run_out(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake(calls, remotes="r2:\n"))
    assert rc == 0 and "offsite skipped" in out
    assert [c[0] for c in calls] == ["listremotes"]
    rc, out = _run_out(environ=env, which=lambda _n: None, rclone=_fake([]))
    assert rc == 0 and "offsite skipped" in out


def test_disabled_by_backup_offsite(tmp_path: Path) -> None:
    env = {**_share(tmp_path), "BACKUP_OFFSITE": "0"}
    calls: list[list[str]] = []
    rc, out = _run_out(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake(calls))
    assert rc == 0 and "offsite skipped" in out and calls == []


@pytest.mark.parametrize("fail", ["copy", "check"])
def test_rclone_failure_raises(tmp_path: Path, fail: str) -> None:
    env = _share(tmp_path)
    with pytest.raises(BackupError, match=f"rclone {fail} xbrl exit=1"):
        run(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake([], fail=fail))


def test_missing_dir_fails(tmp_path: Path) -> None:
    env = {"SHARE_ROOT": str(tmp_path)}
    with pytest.raises(BackupError, match="missing"):
        run(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake([]))


def test_crontab_runs_before_backup_db() -> None:
    lines = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines()
    line = next(x for x in lines if "jobs/ops/backup_data.py" in x and not x.startswith("#"))
    assert line.startswith("30 3 * * * ") and "deploy/cron-run.sh" in line

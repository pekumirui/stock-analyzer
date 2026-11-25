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
    CHECK_LIST_ATTEMPTS,
    CHECK_LIST_RETRY_SLEEP_S,
    KINDS,
    MOVE_KINDS,
    RCLONE_MOVE_MAX_DURATION,
    move_limit_reason,
    run,
)
from app.offsite_settings import BackupError
from jobs.ops.backup_db import CmdResult


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


PAGING = (
    "error reading destination root directory: couldn't list files: "
    "invalidRequest: invalidQueryOption: Invalid paging token: "
    "failed to parse integer value from token.\n"
    "NOTICE: 0 differences found\n"
)
MALFORMED = (
    "ERROR : 6233/a.zip: file not in OneDrive root 'stock-analyzer-backups/data/xbrl'\n"
    "ERROR : 6233: error reading destination directory: couldn't list files: "
    "invalidRequest: The provided drive id appears to be malformed, "
    "or does not represent a valid drive.\n"
)
REAL_GAP = (
    "ERROR : 6233/a.zip: file not in OneDrive root 'stock-analyzer-backups/data/xbrl'\n"
    "NOTICE: 1 differences found\n"
)


def _checks(stderrs: list[str], calls: list[str], slept: list[float]):
    pending = list(stderrs)

    def _run(argv, timeout):
        calls.append(argv[0])
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "check":
            return CmdResult(1, "", pending.pop(0))
        return CmdResult(0, "", "")

    def _sleep(seconds: float) -> None:
        slept.append(seconds)

    return _run, _sleep


def test_paging_token_retries_until_check_exits_zero(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []

    def _run(argv, timeout):
        calls.append(argv[0])
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "check":
            if calls.count("check") == 1:
                return CmdResult(1, "", PAGING)
            return CmdResult(0, "", "NOTICE: 0 differences found")
        return CmdResult(0, "", "")

    rc, out = _run_out(
        kinds=["xbrl"],
        environ=env,
        which=lambda _n: "/usr/bin/rclone",
        rclone=_run,
        sleep=lambda seconds: slept.append(seconds),
    )
    assert rc == 0 and "offsite ok xbrl" in out
    assert calls == ["listremotes", "copy", "check", "check"]
    assert slept == [CHECK_LIST_RETRY_SLEEP_S]
    assert f"list-error attempt=1/{CHECK_LIST_ATTEMPTS}" in out


def test_zero_differences_with_list_error_still_fails(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []
    rclone, sleep = _checks([PAGING] * CHECK_LIST_ATTEMPTS, calls, slept)
    with pytest.raises(BackupError, match="rclone check xbrl exit=1"):
        run(
            ["xbrl"],
            environ=env,
            which=lambda _n: "/usr/bin/rclone",
            rclone=rclone,
            sleep=sleep,
        )
    assert calls == ["listremotes", "copy", *["check"] * CHECK_LIST_ATTEMPTS]
    assert slept == [CHECK_LIST_RETRY_SLEEP_S] * (CHECK_LIST_ATTEMPTS - 1)


def test_malformed_drive_id_retries_then_ok(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []
    seen = {"n": 0}

    def _run(argv, timeout):
        calls.append(argv[0])
        if argv[0] == "listremotes":
            return CmdResult(0, "onedrive:\n", "")
        if argv[0] == "check":
            seen["n"] += 1
            if seen["n"] == 1:
                return CmdResult(1, "", MALFORMED)
            return CmdResult(0, "", "")
        return CmdResult(0, "", "")

    rc, out = _run_out(
        kinds=["xbrl"],
        environ=env,
        which=lambda _n: "/usr/bin/rclone",
        rclone=_run,
        sleep=lambda seconds: slept.append(seconds),
    )
    assert rc == 0 and "offsite ok xbrl" in out
    assert calls.count("check") == 2
    assert slept == [CHECK_LIST_RETRY_SLEEP_S]


def test_zero_differences_notice_alone_is_not_success(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []
    rclone, sleep = _checks(["NOTICE: 0 differences found\n"], calls, slept)
    with pytest.raises(BackupError, match="rclone check xbrl exit=1"):
        run(
            ["xbrl"],
            environ=env,
            which=lambda _n: "/usr/bin/rclone",
            rclone=rclone,
            sleep=sleep,
        )
    assert calls == ["listremotes", "copy", "check"]
    assert slept == []


def test_real_difference_is_not_retried(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []
    rclone, sleep = _checks([REAL_GAP], calls, slept)
    with pytest.raises(BackupError, match="rclone check xbrl exit=1"):
        run(
            ["xbrl"],
            environ=env,
            which=lambda _n: "/usr/bin/rclone",
            rclone=rclone,
            sleep=sleep,
        )
    assert calls == ["listremotes", "copy", "check"]
    assert slept == []


def test_list_error_then_real_difference_fails(tmp_path: Path) -> None:
    env = _share(tmp_path)
    calls: list[str] = []
    slept: list[float] = []
    rclone, sleep = _checks([PAGING, REAL_GAP], calls, slept)
    with pytest.raises(BackupError, match="attempts=2"):
        run(
            ["xbrl"],
            environ=env,
            which=lambda _n: "/usr/bin/rclone",
            rclone=rclone,
            sleep=sleep,
        )
    assert calls == ["listremotes", "copy", "check", "check"]
    assert slept == [CHECK_LIST_RETRY_SLEEP_S]


def test_missing_dir_fails(tmp_path: Path) -> None:
    env = {"SHARE_ROOT": str(tmp_path)}
    with pytest.raises(BackupError, match="missing"):
        run(environ=env, which=lambda _n: "/usr/bin/rclone", rclone=_fake([]))


def test_crontab_runs_before_backup_db() -> None:
    lines = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines()
    line = next(x for x in lines if "jobs/ops/backup_data.py" in x and not x.startswith("#"))
    assert line.startswith("30 3 * * * ") and "deploy/cron-run.sh" in line

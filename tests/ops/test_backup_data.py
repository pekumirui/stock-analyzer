"""backup_data の rclone の呼び方。本番 VPS には出ない。"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.ops.backup_data import KINDS, run  # noqa: E402
from jobs.ops.backup_db import BackupError, CmdResult  # noqa: E402


def _share(tmp_path: Path) -> dict[str, str]:
    for kind in KINDS:
        folder = tmp_path / kind / "1234"
        folder.mkdir(parents=True)
        (folder / "a.zip").write_bytes(b"zip")
    return {"SHARE_ROOT": str(tmp_path)}


def _fake(calls: list[list[str]], *, remotes: str = "onedrive:\n", fail: str | None = None):
    def _run(argv, timeout):  # noqa: ANN001, ANN202, ARG001
        calls.append(list(argv))
        if argv[0] == "listremotes":
            return CmdResult(0, remotes, "")
        if argv[0] == fail:
            return CmdResult(1, "", f"{fail} broke")
        return CmdResult(0, "", "")

    return _run


def _run_out(**kwargs):  # noqa: ANN003, ANN202
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
    assert cmds == ["listremotes", "copy", "check", "copy", "check"]
    assert all(c[0] not in ("sync", "move", "delete", "deletefile", "purge") for c in calls)
    copies = [c for c in calls if c[0] == "copy"]
    assert copies[0][1:3] == [str(tmp_path / "xbrl"), "onedrive:stock-analyzer-backups/data/xbrl"]
    assert copies[1][1:3] == [
        str(tmp_path / "monthly"),
        "onedrive:stock-analyzer-backups/data/monthly",
    ]
    checks = [c for c in calls if c[0] == "check"]
    assert all("--one-way" in c for c in checks)
    assert "offsite ok xbrl files=1" in out and "offsite ok monthly files=1" in out


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

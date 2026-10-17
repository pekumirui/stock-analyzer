"""R2 FUSE mount health: disconnect retry and one-line abort. ネットワークには出ない。"""
from __future__ import annotations

import errno
import getpass
import io
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# 月次 sidecar の切断テストは tests/monthly の fixture を使う
sys.path.insert(0, str(ROOT / "tests" / "monthly"))

from jobs._r2common import (  # noqa: E402
    MOUNT_RETRIES,
    MOUNT_RETRY_WAIT_SEC,
    MountDisconnected,
    abort_if_disconnected,
    check_r2_mount,
    fail_if_mount_down,
    is_mount_disconnect_error,
    path_on_r2_share,
    r2_mount_in_proc,
    r2_share_root,
    with_mount_retry,
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _capture(fn):  # noqa: ANN001
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn()
    return rc, buf.getvalue()


def test_disconnect_errnos() -> None:
    if not is_mount_disconnect_error(OSError(errno.ECONNABORTED, "Software caused connection abort")):
        _fail("ECONNABORTED must be a mount disconnect")
    if not is_mount_disconnect_error(OSError(errno.ENOTCONN, "Transport endpoint is not connected")):
        _fail("ENOTCONN must be a mount disconnect")
    stale = getattr(errno, "ESTALE", 116)
    if not is_mount_disconnect_error(OSError(stale, "Stale file handle")):
        _fail("ESTALE must be a mount disconnect")
    if is_mount_disconnect_error(OSError(errno.ENOENT, "No such file or directory")):
        _fail("ENOENT alone is not a FUSE disconnect")
    if is_mount_disconnect_error(ValueError("no")):
        _fail("ValueError is not a disconnect")
    if not is_mount_disconnect_error(MountDisconnected("x")):
        _fail("MountDisconnected is a disconnect")


def test_path_on_r2_share() -> None:
    root = "/home/stock/r2-share"
    if not path_on_r2_share("/home/stock/r2-share/monthly", root):
        _fail("monthly dest is on the share")
    if not path_on_r2_share(root, root):
        _fail("root is on the share")
    if path_on_r2_share("/tmp/monthly", root):
        _fail("tmp dest is not on the share")
    if path_on_r2_share("/home/stock/r2-share-backup/monthly", root):
        _fail("prefix sibling must not match")
    if r2_share_root("/opt/r2") != Path("/opt/r2"):
        _fail("explicit root")


def test_r2_mount_in_proc() -> None:
    text = "r2:share /home/stock/r2-share fuse.rclone rw,nosuid 0 0\n"
    if r2_mount_in_proc("/home/stock/r2-share", text) is not True:
        _fail("listed mount")
    if r2_mount_in_proc("/home/stock/r2-share", "tmpfs /tmp tmpfs rw 0 0\n") is not False:
        _fail("absent mount")


def test_check_retries_then_raises() -> None:
    sleeps: list[float] = []
    n = {"i": 0}

    def listdir(p: str) -> list[str]:
        n["i"] += 1
        raise OSError(errno.ECONNABORTED, "Software caused connection abort", p)

    try:
        check_r2_mount(
            "/tmp/monthly-not-r2",
            retries=2,
            wait_sec=5.0,
            sleep=sleeps.append,
            listdir=listdir,
        )
    except MountDisconnected as e:
        if "R2 マウント切断" not in str(e) or "Software caused connection abort" not in str(e):
            _fail(f"message {e}")
    else:
        _fail("expected MountDisconnected")
    if n["i"] != 3 or sleeps != [5.0, 5.0]:
        _fail(f"probes={n['i']} sleeps={sleeps}")


def test_check_recovers_after_wait() -> None:
    n = {"i": 0}

    def listdir(p: str) -> list[str]:
        n["i"] += 1
        if n["i"] == 1:
            raise OSError(errno.ECONNABORTED, "Software caused connection abort", p)
        return ["monthly"]

    check_r2_mount("/tmp/x", retries=2, wait_sec=0.0, sleep=lambda _s: None, listdir=listdir)
    if n["i"] != 2:
        _fail(f"recover probes {n['i']}")


def test_check_local_missing_is_oserror() -> None:
    try:
        check_r2_mount("/tmp/stock-analyzer-r2-missing-dest-xyz", retries=0, sleep=lambda _s: None)
    except MountDisconnected:
        _fail("local dest missing must not be MountDisconnected")
    except OSError:
        pass
    else:
        _fail("local dest missing must raise OSError")


def test_check_r2_dest_missing_is_disconnect() -> None:
    try:
        check_r2_mount(
            "/home/stock/r2-share/monthly",
            retries=1,
            wait_sec=0.0,
            sleep=lambda _s: None,
            listdir=lambda p: (_ for _ in ()).throw(OSError(errno.ENOENT, "No such file", p)),
            proc_mounts=lambda: "r2:share /home/stock/r2-share fuse.rclone rw 0 0\n",
            r2_root="/home/stock/r2-share",
        )
    except MountDisconnected as e:
        if "R2 マウント切断" not in str(e):
            _fail(f"message {e}")
    else:
        _fail("R2 dest ENOENT must be MountDisconnected")


def test_check_writer_allows_missing_dest_on_live_mount() -> None:
    check_r2_mount(
        "/home/stock/r2-share/monthly",
        require_existing=False,
        retries=0,
        listdir=lambda p: (_ for _ in ()).throw(OSError(errno.ENOENT, "No such file", p)),
        proc_mounts=lambda: "r2:share /home/stock/r2-share fuse.rclone rw 0 0\n",
        r2_root="/home/stock/r2-share",
    )


def test_check_unmounted_proc() -> None:
    sleeps: list[float] = []
    try:
        check_r2_mount(
            "/home/stock/r2-share/monthly",
            retries=1,
            wait_sec=5.0,
            sleep=sleeps.append,
            listdir=lambda _p: ["ok"],
            proc_mounts=lambda: "tmpfs /tmp tmpfs rw 0 0\n",
            r2_root="/home/stock/r2-share",
        )
    except MountDisconnected as e:
        if "is not mounted" not in str(e):
            _fail(f"message {e}")
    else:
        _fail("missing /proc/mounts entry must abort")
    if sleeps != [5.0]:
        _fail(f"sleeps {sleeps}")


def test_with_mount_retry_retries_fn() -> None:
    n = {"i": 0}

    def fn() -> str:
        n["i"] += 1
        if n["i"] == 1:
            raise OSError(errno.ECONNABORTED, "Software caused connection abort", "/x")
        return "ok"

    got = with_mount_retry(
        fn,
        "/tmp/dest",
        retries=0,
        sleep=lambda _s: None,
        listdir=lambda _p: ["ok"],
    )
    if got != "ok" or n["i"] != 2:
        _fail(f"got={got} n={n['i']}")


def test_with_mount_retry_unrelated_oserror() -> None:
    def fn() -> None:
        raise OSError(errno.EACCES, "Permission denied", "/x")

    try:
        with_mount_retry(fn, "/tmp/dest", retries=0, sleep=lambda _s: None, listdir=lambda _p: ["ok"])
    except OSError as e:
        if e.errno != errno.EACCES:
            _fail(f"errno {e.errno}")
    else:
        _fail("EACCES must propagate")


def test_fail_if_mount_down_prints_one_line() -> None:
    rc, out = _capture(
        lambda: fail_if_mount_down(
            "tdnet_monthly_pdf_sidecar",
            "/tmp/x",
            retries=0,
            sleep=lambda _s: None,
            listdir=lambda p: (_ for _ in ()).throw(
                OSError(errno.ECONNABORTED, "Software caused connection abort", p)
            ),
        )
    )
    if rc != 1:
        _fail(f"rc={rc}")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) != 1 or "R2 マウント切断" not in lines[0] or not lines[0].startswith("tdnet_monthly_pdf_sidecar "):
        _fail(f"one line: {out!r}")
    if "fail " in out:
        _fail("must not look like per-issuer fail")


def test_abort_if_disconnected() -> None:
    exc = OSError(errno.ECONNABORTED, "Software caused connection abort")
    rc, out = _capture(
        lambda: abort_if_disconnected(
            "tdnet_monthly_r2",
            "/tmp/x",
            exc,
            retries=0,
            sleep=lambda _s: None,
            listdir=lambda p: (_ for _ in ()).throw(exc),
        )
    )
    if rc != 1 or "R2 マウント切断" not in out:
        _fail(f"rc={rc} out={out!r}")
    if abort_if_disconnected("x", "/tmp", OSError(errno.EACCES, "no")) is not None:
        _fail("EACCES is not abort")


def test_sidecar_aborts_without_per_issuer_fail() -> None:
    import jobs.monthly.tdnet_monthly_pdf_sidecar as sc
    from test_tdnet_monthly_pdf_sidecar import (  # noqa: E402
        NEW,
        NEW_NAME,
        PREV,
        PREV_NAME,
        _db_rows,
        _dest,
        _seed_db,
    )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dest = _dest(tmp, {PREV_NAME: PREV, NEW_NAME: NEW})
        (dest / "6627").mkdir()
        (dest / "6627" / "20260804_6627_x.pdf").write_text("x", encoding="utf-8")
        db_path = _seed_db(tmp / "m.db", _db_rows())
        n = {"listdir": 0}

        def listdir(p: str) -> list[str]:
            n["listdir"] += 1
            if n["listdir"] == 1:
                return os.listdir(p)
            raise OSError(errno.ECONNABORTED, "Software caused connection abort", p)

        def read_lines(path: Path) -> list[sc.Line]:
            raise OSError(errno.ECONNABORTED, "Software caused connection abort", str(path))

        sleeps: list[float] = []
        rc, out = _capture(
            lambda: sc.run(dest, db_path, read_lines=read_lines, sleep=sleeps.append, listdir=listdir)
        )
        if rc == 0:
            _fail(f"must fail: {out}")
        if out.count("R2 マウント切断") != 1 or "tdnet_monthly_pdf_sidecar R2 マウント切断" not in out:
            _fail(f"one labeled line: {out!r}")
        if "fail 428A" in out or "fail 6627" in out or "failed=" in out:
            _fail(f"must not per-issuer fail: {out}")
        if sleeps != [MOUNT_RETRY_WAIT_SEC] * MOUNT_RETRIES:
            _fail(f"sleeps {sleeps}")


def test_sidecar_local_dest_missing_unchanged() -> None:
    import sqlite3
    import jobs.monthly.tdnet_monthly_pdf_sidecar as sc

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        db_path = tmp / "m.db"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE monthly_values (code TEXT)")
        db.close()
        rc, out = _capture(
            lambda: sc.run(tmp / "nope", db_path, read_lines=lambda _p: [], sleep=lambda _s: None)
        )
        if rc != 2 or "dest missing" not in out or "R2 マウント切断" in out:
            _fail(f"local dest missing: {out!r}")


def test_sidecar_recovers_and_keeps_normal_output() -> None:
    import jobs.monthly.tdnet_monthly_pdf_sidecar as sc
    from test_tdnet_monthly_pdf_sidecar import (  # noqa: E402
        NEW,
        NEW_NAME,
        PREV,
        PREV_NAME,
        _db_rows,
        _dest,
        _read_layout,
        _seed_db,
    )

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dest = _dest(tmp, {PREV_NAME: PREV, NEW_NAME: NEW})
        db_path = _seed_db(tmp / "m.db", _db_rows())
        n = {"i": 0}

        def read_lines(path: Path) -> list[sc.Line]:
            n["i"] += 1
            if n["i"] == 1:
                raise OSError(errno.ECONNABORTED, "Software caused connection abort", str(path))
            return _read_layout(path)

        rc, out = _capture(
            lambda: sc.run(dest, db_path, read_lines=read_lines, sleep=lambda _s: None)
        )
        if rc != 0 or "ok 428A" not in out or "R2 マウント切断" in out:
            _fail(f"recovered run: {out}")


def test_ingest_aborts_on_discover_disconnect() -> None:
    import jobs.monthly.ir_monthly_html_ingest as ing

    with tempfile.TemporaryDirectory() as raw:
        dest = Path(raw) / "monthly"
        dest.mkdir()
        n = {"i": 0}

        def listdir(p: str) -> list[str]:
            n["i"] += 1
            if n["i"] == 1:
                return os.listdir(p)
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected", p)

        orig_glob = Path.glob

        def boom_glob(self: Path, pattern: str):  # noqa: ANN202
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected", str(self))

        Path.glob = boom_glob  # type: ignore[method-assign]
        try:
            rc, out = _capture(
                lambda: ing.run(
                    dest,
                    registry=False,
                    empty_ok=True,
                    sleep=lambda _s: None,
                    listdir=listdir,
                )
            )
        finally:
            Path.glob = orig_glob  # type: ignore[method-assign]
        if rc == 0 or "R2 マウント切断" not in out:
            _fail(f"ingest abort: {out!r}")
        if "fail sidecar" in out or "done posted=" in out:
            _fail(f"must not per-sidecar fail: {out}")


_FAKE_SYSTEMCTL = r"""#!/bin/bash
log="${R2_SYSTEMCTL_LOG:?}"
printf '%s\n' "$*" >> "$log"
filtered=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) shift ;;
    -M) shift 2 ;;
    --quiet) shift ;;
    *) filtered+=("$1"); shift ;;
  esac
done
cmd="${filtered[0]:-}"
unit="${filtered[1]:-}"
flag="${R2_ACTIVE_FLAG:?}"
case "$cmd" in
  list-unit-files|enable) exit 0 ;;
  daemon-reload) echo "DAEMON_RELOAD" >> "$log"; exit 0 ;;
  is-active)
    if [[ -f "$flag" ]]; then exit 0; fi
    exit 3
    ;;
  restart)
    echo "RESTART ${unit}" >> "$log"
    touch "$flag"
    exit 0
    ;;
  start)
    echo "START ${unit}" >> "$log"
    if [[ "$unit" == r2-share.service ]]; then
      touch "$flag"
    fi
    exit 0
    ;;
  stop)
    echo "STOP ${unit}" >> "$log"
    rm -f "$flag"
    exit 0
    ;;
  *) exit 0 ;;
esac
"""


def _plan(mount_changed: str, active: str) -> str:
    script = ROOT / "deploy" / "r2-share-apply.sh"
    proc = subprocess.run(
        ["bash", "-c", f"source {script.as_posix()!r} && r2_share_plan {mount_changed} {active}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _fail(f"r2_share_plan failed: {proc.stderr or proc.stdout}")
    return proc.stdout.strip()


def test_r2_share_plan_branches() -> None:
    if _plan("0", "1") != "keep":
        _fail("unchanged + active must keep")
    if _plan("1", "1") != "restart":
        _fail("changed + active must restart")
    if _plan("0", "0") != "start":
        _fail("unchanged + inactive must start")
    if _plan("1", "0") != "start":
        _fail("changed + inactive must start")


def _write_fake_systemctl(tmp: Path) -> Path:
    exe = tmp / "systemctl"
    exe.write_text(_FAKE_SYSTEMCTL, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def _apply_env(home: Path, fake: Path, log: Path, flag: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["STOCK_USER"] = getpass.getuser()
    env["STOCK_HOME"] = str(home)
    env["APP_DIR"] = str(ROOT)
    env["R2_SYSTEMCTL"] = str(fake)
    env["R2_SYSTEMCTL_LOG"] = str(log)
    env["R2_ACTIVE_FLAG"] = str(flag)
    env["R2_SYSTEMD_READY_TRIES"] = "1"
    env["R2_SYSTEMD_READY_SLEEP"] = "0"
    return env


def _run_apply(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "deploy" / "r2-share-apply.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _assert_apply_ok(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != 0:
        _fail(f"{label} rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")


def test_apply_restart_when_mount_unit_changes() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        home = tmp / "home"
        (home / ".config" / "rclone").mkdir(parents=True)
        (home / ".config" / "rclone" / "rclone.conf").write_text("[r2]\ntype = s3\n", encoding="utf-8")
        fake = _write_fake_systemctl(tmp)
        log = tmp / "systemctl.log"
        flag = tmp / "active"
        env = _apply_env(home, fake, log, flag)
        first = _run_apply(env)
        _assert_apply_ok(first, "first apply")
        if "START r2-share.service" not in log.read_text(encoding="utf-8"):
            _fail(f"first apply must start: {first.stdout!r} {log.read_text()}")
        log.write_text("", encoding="utf-8")
        second = _run_apply(env)
        _assert_apply_ok(second, "unchanged apply")
        text = log.read_text(encoding="utf-8")
        if "RESTART r2-share.service" in text or "STOP r2-share.service" in text:
            _fail(f"unchanged must not restart/stop mount: {text!r} {second.stdout!r}")
        if "DAEMON_RELOAD" in text:
            _fail(f"unchanged must not daemon-reload: {text!r}")
        if "leave mount running" not in second.stdout:
            _fail(f"unchanged should say leave mount running: {second.stdout!r}")
        installed = home / ".config" / "systemd" / "user" / "r2-share.service"
        installed.write_text(installed.read_text(encoding="utf-8") + "# bumped\n", encoding="utf-8")
        log.write_text("", encoding="utf-8")
        third = _run_apply(env)
        _assert_apply_ok(third, "changed apply")
        text = log.read_text(encoding="utf-8")
        if "RESTART r2-share.service" not in text:
            _fail(f"unit change must restart: {text!r} {third.stdout!r}")
        if "STOP r2-share.service" in text:
            _fail(f"must not stop: {text!r}")


def test_apply_rclone_conf_change_restarts() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        home = tmp / "home"
        conf = home / ".config" / "rclone" / "rclone.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text("[r2]\ntype = s3\nendpoint = a\n", encoding="utf-8")
        fake = _write_fake_systemctl(tmp)
        log = tmp / "systemctl.log"
        flag = tmp / "active"
        env = _apply_env(home, fake, log, flag)
        _assert_apply_ok(_run_apply(env), "seed")
        log.write_text("", encoding="utf-8")
        conf.write_text("[r2]\ntype = s3\nendpoint = b\n", encoding="utf-8")
        proc = _run_apply(env)
        _assert_apply_ok(proc, "rclone.conf change")
        text = log.read_text(encoding="utf-8")
        if "RESTART r2-share.service" not in text:
            _fail(f"rclone.conf change must restart: {text!r} {proc.stdout!r}")


def test_apply_watch_only_change_does_not_restart_mount() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        home = tmp / "home"
        (home / ".config" / "rclone").mkdir(parents=True)
        (home / ".config" / "rclone" / "rclone.conf").write_text("[r2]\n", encoding="utf-8")
        fake = _write_fake_systemctl(tmp)
        log = tmp / "systemctl.log"
        flag = tmp / "active"
        env = _apply_env(home, fake, log, flag)
        _assert_apply_ok(_run_apply(env), "seed")
        timer = home / ".config" / "systemd" / "user" / "r2-share-watch.timer"
        timer.write_text(timer.read_text(encoding="utf-8") + "# watch bump\n", encoding="utf-8")
        log.write_text("", encoding="utf-8")
        proc = _run_apply(env)
        _assert_apply_ok(proc, "watch change")
        text = log.read_text(encoding="utf-8")
        if "RESTART r2-share.service" in text or "STOP r2-share.service" in text:
            _fail(f"watch-only change must not restart mount: {text!r} {proc.stdout!r}")
        if "DAEMON_RELOAD" not in text:
            _fail(f"watch unit change should daemon-reload: {text!r}")
        if "leave mount running" not in proc.stdout:
            _fail(f"watch-only should leave mount: {proc.stdout!r}")


def test_systemd_unit_matches_job_wait() -> None:
    unit = (ROOT / "deploy" / "r2-share.service").read_text(encoding="utf-8")
    if "Restart=always" not in unit:
        _fail("r2-share.service must Restart=always")
    if "RestartSec=5" not in unit:
        _fail("RestartSec must stay 5")
    chk = subprocess.run(
        ["bash", "-n", str(ROOT / "deploy" / "r2-share-apply.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n r2-share-apply.sh failed: {chk.stderr or chk.stdout}")
    if MOUNT_RETRY_WAIT_SEC != 5.0:
        _fail("job wait must match RestartSec")


def main() -> None:
    test_disconnect_errnos()
    test_path_on_r2_share()
    test_r2_mount_in_proc()
    test_check_retries_then_raises()
    test_check_recovers_after_wait()
    test_check_local_missing_is_oserror()
    test_check_r2_dest_missing_is_disconnect()
    test_check_writer_allows_missing_dest_on_live_mount()
    test_check_unmounted_proc()
    test_with_mount_retry_retries_fn()
    test_with_mount_retry_unrelated_oserror()
    test_fail_if_mount_down_prints_one_line()
    test_abort_if_disconnected()
    test_sidecar_aborts_without_per_issuer_fail()
    test_sidecar_local_dest_missing_unchanged()
    test_sidecar_recovers_and_keeps_normal_output()
    test_ingest_aborts_on_discover_disconnect()
    test_r2_share_plan_branches()
    test_apply_restart_when_mount_unit_changes()
    test_apply_rclone_conf_change_restarts()
    test_apply_watch_only_change_does_not_restart_mount()
    test_systemd_unit_matches_job_wait()
    print("ok: r2 mount")


if __name__ == "__main__":
    main()

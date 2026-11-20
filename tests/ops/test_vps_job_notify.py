from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vps-job-notify.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None,
    reason="bash と flock が要る",
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "jobs").mkdir()
    return tmp_path


def _env(home: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(home)}


def _name(tag: str) -> str:
    return f"t-{tag}-{uuid.uuid4().hex[:8]}"


def _run(home: Path, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=_env(home), capture_output=True, text=True, timeout=timeout
    )


def _start(home: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", str(SCRIPT), *args], env=_env(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _job_dir(home: Path, name: str) -> Path:
    dirs = sorted((home / "jobs").glob(f"{name}-*"))
    assert len(dirs) == 1, dirs
    return dirs[0]


def _until(cond, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _prev_job(home: Path, name: str, *, pid: int, exit_code: int | None) -> Path:
    d = home / "jobs" / f"{name}-20260101-0000"
    d.mkdir()
    (d / "pid").write_text(f"{pid}\n")
    if exit_code is not None:
        (d / "exit").write_text(f"{exit_code}\n")
    return d


def _fake_webhook(home: Path) -> tuple[dict[str, str], Path]:
    conf = home / ".config" / "grok-webhook"
    conf.mkdir(parents=True)
    (conf / "env").write_text("GROK_WEBHOOK_URL=http://example.invalid/hook\nGROK_WEBHOOK_KEY=dummy\n")
    bin_dir = home / "bin"
    bin_dir.mkdir()
    sent = home / "sent"
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" >>"{sent}"\nprintf 200\n')
    curl.chmod(0o755)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}, sent


def test_without_after_runs_and_records(home: Path) -> None:
    name = _name("plain")
    r = _run(home, name, "--", "bash", "-c", "echo hello; exit 3")
    assert r.returncode == 3
    d = _job_dir(home, name)
    assert (d / "exit").read_text().strip() == "3"
    assert "hello" in (d / "log").read_text()
    assert "exit=3" in (d / "summary").read_text()
    assert (d / "notified").exists()
    assert not (d / "waiting").exists()


def test_without_after_lock_blocks_second_run(home: Path) -> None:
    name = _name("lock")
    lock = f"/tmp/vps-job-{name}.lock"
    holder = subprocess.Popen(["flock", lock, "sleep", "30"])
    try:
        _until(lambda: subprocess.run(["flock", "-n", lock, "true"]).returncode != 0)
        r = _run(home, name, "--", "true")
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 99
    assert "already running" in (_job_dir(home, name) / "summary").read_text()


def test_after_waits_until_prev_exit(home: Path) -> None:
    prev_name, name = _name("prev"), _name("next")
    sleeper = subprocess.Popen(["sleep", "60"])
    marker = home / "ran"
    try:
        prev = _prev_job(home, prev_name, pid=sleeper.pid, exit_code=None)
        job = _start(home, name, "--after", prev_name, "--after-poll", "0.1", "--", "touch", str(marker))
        _until(lambda: list((home / "jobs").glob(f"{name}-*/waiting")))
        time.sleep(0.5)
        assert job.poll() is None
        assert not marker.exists()
        d = _job_dir(home, name)
        assert str(prev) in (d / "waiting").read_text()
        assert not (d / "started").exists()

        (prev / "exit").write_text("0\n")
        sleeper.kill()
        assert job.wait(timeout=10) == 0
    finally:
        sleeper.kill()
        sleeper.wait()
    assert marker.exists()
    assert not (d / "waiting").exists()
    assert (d / "exit").read_text().strip() == "0"
    assert (d / "started").exists()


def test_after_accepts_prev_dir(home: Path) -> None:
    prev = _prev_job(home, _name("prev"), pid=_dead_pid(), exit_code=0)
    name = _name("next")
    r = _run(home, name, "--after", str(prev), "--after-poll", "0.1", "--", "true")
    assert r.returncode == 0
    assert (_job_dir(home, name) / "exit").read_text().strip() == "0"


def test_after_does_not_start_when_prev_died_without_exit(home: Path) -> None:
    extra_env, sent = _fake_webhook(home)
    prev_name, name = _name("prev"), _name("next")
    _prev_job(home, prev_name, pid=_dead_pid(), exit_code=None)
    marker = home / "ran"
    r = subprocess.run(
        ["bash", str(SCRIPT), name, "--after", prev_name, "--after-poll", "0.1", "--", "touch", str(marker)],
        env={**_env(home), **extra_env}, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert not marker.exists()
    d = _job_dir(home, name)
    assert not (d / "waiting").exists()
    assert (d / "exit").read_text().strip() != "0"
    summary = (d / "summary").read_text()
    assert "未起動" in summary and prev_name in summary
    assert (d / "notified").read_text().strip() == "200"
    assert "未起動" in sent.read_text()


def test_after_does_not_start_when_prev_failed(home: Path) -> None:
    prev_name, name = _name("prev"), _name("next")
    _prev_job(home, prev_name, pid=_dead_pid(), exit_code=1)
    marker = home / "ran"
    r = _run(home, name, "--after", prev_name, "--after-poll", "0.1", "--", "touch", str(marker))
    assert r.returncode != 0
    assert not marker.exists()
    assert "未起動" in (_job_dir(home, name) / "summary").read_text()


def test_after_nonzero_option_continues(home: Path) -> None:
    prev_name, name = _name("prev"), _name("next")
    _prev_job(home, prev_name, pid=_dead_pid(), exit_code=1)
    marker = home / "ran"
    r = _run(home, name, "--after", prev_name, "--after-nonzero", "--after-poll", "0.1", "--", "touch", str(marker))
    assert r.returncode == 0
    assert marker.exists()


def test_after_gives_up_at_timeout(home: Path) -> None:
    prev_name, name = _name("prev"), _name("next")
    sleeper = subprocess.Popen(["sleep", "60"])
    marker = home / "ran"
    try:
        _prev_job(home, prev_name, pid=sleeper.pid, exit_code=None)
        r = _run(home, name, "--after", prev_name, "--after-timeout", "1", "--after-poll", "0.2",
                 "--", "touch", str(marker))
    finally:
        sleeper.kill()
        sleeper.wait()
    assert r.returncode != 0
    assert not marker.exists()
    d = _job_dir(home, name)
    assert not (d / "waiting").exists()
    assert "未起動" in (d / "summary").read_text()


def test_after_missing_prev_does_not_start(home: Path) -> None:
    name = _name("next")
    marker = home / "ran"
    r = _run(home, name, "--after", _name("nothing"), "--after-poll", "0.1", "--", "touch", str(marker))
    assert r.returncode != 0
    assert not marker.exists()


def test_after_ignores_processes_whose_command_line_names_prev(home: Path) -> None:
    prev_name, name = _name("prev"), _name("next")
    prev = _prev_job(home, prev_name, pid=_dead_pid(), exit_code=0)
    (prev / "cmd").write_text(f"python -m jobs.{prev_name} ")
    lookalike = subprocess.Popen(["bash", "-c", "sleep 60", f"python -m jobs.{prev_name}", prev_name])
    marker = home / "ran"
    try:
        r = _run(home, name, "--after", prev_name, "--after-poll", "0.1", "--after-timeout", "5",
                 "--", "bash", "-c", f": {prev_name}; touch {marker}")
    finally:
        lookalike.kill()
        lookalike.wait()
    assert r.returncode == 0
    assert marker.exists()


def test_chained_job_can_wait_on_a_waiting_job(home: Path) -> None:
    first, second, third = _name("a"), _name("b"), _name("c")
    sleeper = subprocess.Popen(["sleep", "60"])
    order = home / "order"
    try:
        prev = _prev_job(home, first, pid=sleeper.pid, exit_code=None)
        b = _start(home, second, "--after", first, "--after-poll", "0.1", "--", "bash", "-c", f"echo b >>{order}")
        _until(lambda: list((home / "jobs").glob(f"{second}-*/waiting")))
        c = _start(home, third, "--after", second, "--after-poll", "0.1", "--", "bash", "-c", f"echo c >>{order}")
        _until(lambda: list((home / "jobs").glob(f"{third}-*/waiting")))
        time.sleep(0.5)
        assert not order.exists()
        (prev / "exit").write_text("0\n")
        sleeper.kill()
        assert b.wait(timeout=10) == 0
        assert c.wait(timeout=10) == 0
    finally:
        sleeper.kill()
        sleeper.wait()
    assert order.read_text().split() == ["b", "c"]

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
UPDATE = ROOT / "deploy" / "update.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("flock") is None or shutil.which("git") is None,
    reason="bash と flock と git が要る",
)


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout.strip()


def _rev(cwd: Path, env: dict[str, str], ref: str) -> str:
    return _git(cwd, env, "rev-parse", ref)


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stubs(bin_dir: Path) -> None:
    bin_dir.mkdir()
    _write_exec(
        bin_dir / "sudo",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --preserve-env=*|--no-pager|--full) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "if [[ \"${1:-}\" == systemctl ]]; then\n"
        "  if [[ \"${2:-}\" == is-active ]]; then echo active; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ -n \"${1:-}\" && -x \"$1\" ]]; then exec \"$1\" \"${@:2}\"; fi\n"
        "exit 0\n",
    )
    _write_exec(
        bin_dir / "curl",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "out=\n"
        "url=\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    -w) shift; out=\"$1\" ;;\n"
        "    http*) url=\"$1\" ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        "code=200\n"
        "case \"$url\" in\n"
        "  *8010/mcp*) code=401 ;;\n"
        "esac\n"
        "if [[ \"$out\" == *http_code* ]]; then printf '%s' \"$code\"; fi\n"
        "exit 0\n",
    )
    _write_exec(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == is-active ]]; then echo active; fi\n"
        "exit 0\n",
    )


def _world(tmp_path: Path) -> tuple[dict[str, str], Path, str]:
    app = tmp_path / "app"
    home = tmp_path / "home"
    origin = tmp_path / "origin.git"
    pusher = tmp_path / "pusher"
    bin_dir = tmp_path / "bin"
    app.mkdir()
    home.mkdir()
    _stubs(bin_dir)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["APP_DIR"] = str(app)
    env["DEPLOY_LOCK"] = str(tmp_path / "deploy.lock")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        env.pop(key, None)
    (app / "jobs").mkdir()
    (app / "deploy").mkdir()
    (app / "requirements.txt").write_text("# test\n", encoding="utf-8")
    (app / "jobs" / "migrate.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (app / "deploy" / "provision.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (app / "deploy" / "provision.sh").chmod(0o755)
    shutil.copy2(ROOT / "deploy" / "write_rclone_conf.py", app / "deploy" / "write_rclone_conf.py")
    _git(app, env, "init", "-b", "main")
    _git(app, env, "config", "user.email", "deploy-test@example.com")
    _git(app, env, "config", "user.name", "deploy-test")
    _git(app, env, "config", "commit.gpgsign", "false")
    _git(app, env, "add", "requirements.txt", "jobs/migrate.py", "deploy")
    _git(app, env, "commit", "-m", "seed")
    origin.mkdir()
    _git(origin, env, "init", "--bare", "-b", "main")
    _git(app, env, "remote", "add", "origin", str(origin))
    _git(app, env, "branch", "other")
    _git(app, env, "tag", "v-test")
    _git(app, env, "push", "-u", "origin", "main", "other", "v-test")
    _git(tmp_path, env, "clone", str(origin), str(pusher))
    _git(pusher, env, "config", "user.email", "deploy-test@example.com")
    _git(pusher, env, "config", "user.name", "deploy-test")
    _git(pusher, env, "config", "commit.gpgsign", "false")
    (pusher / "ahead.txt").write_text("ahead\n", encoding="utf-8")
    _git(pusher, env, "add", "ahead.txt")
    _git(pusher, env, "commit", "-m", "ahead")
    _git(pusher, env, "push", "origin", "main")
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700)
    key = ssh / "id_github"
    key.write_text("dummy-private\n", encoding="utf-8")
    (ssh / "id_github.pub").write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIdummy stock@test\n", encoding="utf-8")
    key.chmod(0o600)
    (ssh / "id_github.pub").chmod(0o644)
    target = _rev(origin, env, "main")
    behind = _rev(app, env, "HEAD")
    if behind == target:
        raise AssertionError("worktree is already at origin main")
    return env, app, target


def _run(env: dict[str, str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["bash", str(UPDATE)],
            cwd=env["APP_DIR"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"update.sh timed out: stdout={exc.stdout!r} stderr={exc.stderr!r}"
        ) from exc


def _assert_fast_forward(proc: subprocess.CompletedProcess[str], app: Path, env: dict[str, str], target: str) -> None:
    if proc.returncode != 0:
        raise AssertionError(f"update.sh failed: rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    head = _rev(app, env, "HEAD")
    origin_main = _rev(app, env, "refs/remotes/origin/main")
    if head != origin_main or head != target:
        raise AssertionError(f"HEAD {head} origin/main {origin_main} target {target}")


def _for_merge_count(fetch_head: str) -> int:
    count = 0
    for line in fetch_head.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and cols[1] == "":
            count += 1
    return count


def _git_lock(app: Path, env: dict[str, str]) -> Path:
    raw = _git(app, env, "rev-parse", "--git-path", "deploy-git.lock")
    path = Path(raw)
    if not path.is_absolute():
        path = app / path
    return path


def test_update_head_matches_origin_main(tmp_path: Path) -> None:
    env, app, target = _world(tmp_path)
    _assert_fast_forward(_run(env, timeout=180), app, env, target)


def test_update_head_matches_origin_main_while_full_fetch_runs(tmp_path: Path) -> None:
    env, app, target = _world(tmp_path)
    count = tmp_path / "fetches"
    racer = subprocess.Popen(
        ["bash", "-c", "while true; do git fetch >/dev/null 2>&1 || true; echo x >> \"$1\"; done", "fetch-loop", str(count)],
        cwd=app,
        env=env,
    )
    try:
        time.sleep(0.05)
        proc = _run(env, timeout=180)
    finally:
        racer.kill()
        racer.wait()
    _assert_fast_forward(proc, app, env, target)
    if not count.is_file() or not count.read_text(encoding="utf-8").strip():
        raise AssertionError("concurrent git fetch did not run")


def test_update_fast_forwards_when_fetch_head_has_two_for_merge_lines(tmp_path: Path) -> None:
    env, app, target = _world(tmp_path)
    real_git = shutil.which("git")
    if not real_git:
        raise AssertionError("git missing")
    other = _rev(app, env, "other")
    wrapper = Path(env["PATH"].split(os.pathsep, 1)[0]) / "git"
    _write_exec(
        wrapper,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"real={real_git}\n"
        "if [[ \"${1:-}\" == fetch && \"${2:-}\" == origin && \"${3:-}\" == main ]]; then\n"
        "  set +e\n"
        "  \"$real\" \"$@\"\n"
        "  rc=$?\n"
        "  set -e\n"
        "  if [[ \"$rc\" -eq 0 ]]; then\n"
        f"    printf '%s\\t\\t%s\\n' {other} \"branch 'other' of poisoned\" >> .git/FETCH_HEAD\n"
        "  fi\n"
        "  exit \"$rc\"\n"
        "fi\n"
        "exec \"$real\" \"$@\"\n",
    )
    proc = _run(env, timeout=180)
    _assert_fast_forward(proc, app, env, target)
    fetch_head = (app / ".git" / "FETCH_HEAD").read_text(encoding="utf-8")
    found = _for_merge_count(fetch_head)
    if found < 2:
        raise AssertionError(f"FETCH_HEAD for-merge lines={found}\n{fetch_head}")


def test_git_lock_timeout_leaves_head(tmp_path: Path) -> None:
    env, app, target = _world(tmp_path)
    before = _rev(app, env, "HEAD")
    if before == target:
        raise AssertionError("worktree already matches origin main")
    lock = _git_lock(app, env)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch()
    env["DEPLOY_GIT_LOCK_WAIT"] = "1"
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            probe = subprocess.run(["flock", "-n", str(lock), "true"], check=False)
            if probe.returncode != 0:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("git lock was not held")
        proc = _run(env, timeout=20)
    finally:
        holder.kill()
        holder.wait()
    if proc.returncode != 1:
        raise AssertionError(f"busy git lock must exit 1: rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    if "git lock busy:" not in proc.stderr or "waited 1s" not in proc.stderr:
        raise AssertionError(f"lock wait message missing: {proc.stderr!r}")
    if "another git operation is using this worktree" not in proc.stderr:
        raise AssertionError(f"lock wait message missing worktree text: {proc.stderr!r}")
    if _rev(app, env, "HEAD") != before:
        raise AssertionError("HEAD moved while the git lock was busy")

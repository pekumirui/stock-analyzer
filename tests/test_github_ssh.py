"""GitHub deploy SSH must use port 443, and VPS_DOMAIN debug must stay off bash stdin."""
from __future__ import annotations

import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITHUB_ED25519 = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _host_github_block(text: str, label: str) -> str:
    marker = 'echo "Host github.com"'
    start = text.find(marker)
    if start < 0:
        _fail(f"{label} missing Host github.com block")
    rest = text[start:]
    end = rest.find("IdentitiesOnly yes")
    if end < 0:
        _fail(f"{label} Host github.com block has no IdentitiesOnly yes")
    return rest[: end + len("IdentitiesOnly yes")]


def test_static_contracts() -> None:
    setup = (ROOT / "deploy" / "setup-github-ssh.sh").read_text(encoding="utf-8")
    if "Port 443" not in setup:
        _fail("setup-github-ssh.sh missing Port 443")
    if "ssh.github.com" not in setup:
        _fail("setup-github-ssh.sh missing ssh.github.com")
    if "if ! grep -qE '^Host github\\.com$'" in setup:
        _fail(
            "setup-github-ssh.sh must rewrite existing Host github.com blocks; "
            "do not skip via grep -qE '^Host github.com$' early-out"
        )
    cicd = (ROOT / ".github" / "workflows" / "cicd.yml").read_text(encoding="utf-8")
    needle = "VPS_DOMAIN len="
    idx = cicd.find(needle)
    if idx < 0:
        _fail("cicd.yml missing VPS_DOMAIN len debug")
    window = cicd[max(0, idx - 120) : idx + 80]
    if "file=sys.stderr" not in window:
        _fail("cicd.yml VPS_DOMAIN debug must print(..., file=sys.stderr)")
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    if "Port 443" not in update or "ssh.github.com" not in update:
        _fail("update.sh must force GitHub SSH port 443 before fetch")
    if "git status --porcelain --untracked-files=no" not in update:
        _fail("update.sh missing git status --porcelain --untracked-files=no")
    for needle in (
        "git → CI → VPS",
        "Hand edits",
        "SCP",
        "not allowed",
        "detected to stop deploy",
    ):
        if needle not in update:
            _fail(f"update.sh missing tracked-dirty policy text {needle!r}")
    if "git reset --hard" in update:
        _fail("update.sh must not git reset --hard")
    if "git clean" in update:
        _fail("update.sh must not git clean")
    bare_status = [
        line
        for line in update.splitlines()
        if "git status --porcelain" in line and "--untracked-files=no" not in line
    ]
    if bare_status or update.count("git status --porcelain") != 1:
        _fail(
            "update.sh must invoke git status only as "
            "git status --porcelain --untracked-files=no"
        )
    for label, text in (
        ("setup-github-ssh.sh", setup),
        ("update.sh", update),
    ):
        block = _host_github_block(text, label)
        if "HostKeyAlias github.com" not in block:
            _fail(f"{label} Host github.com block missing HostKeyAlias github.com")
    for host in ("github.com", "ssh.github.com", "[ssh.github.com]:443"):
        if host not in update:
            _fail(f"update.sh must embed official host key for {host}")
    if GITHUB_ED25519 not in update:
        _fail("update.sh must embed official GitHub host keys before fetch, not only alias known_hosts")
    if '"$APP_DIR/.venv/bin/python" jobs/migrate.py' not in update:
        _fail("update.sh must run jobs/migrate.py")
    if 'if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then' not in update:
        _fail("update.sh must create venv only when .venv/bin/pip is missing")
    if 'if [ -L "$APP_DIR/.venv" ]' not in update:
        _fail("update.sh must detect a blocking .venv symlink")
    if 'rm -f "$APP_DIR/.venv"' not in update:
        _fail("update.sh must unlink a blocking .venv path")
    ensure = _venv_ensure_block(update)
    if "rm -rf" in ensure:
        _fail("venv ensure must not rm -rf")
    if 'python3 -m venv "$APP_DIR/.venv"' not in ensure:
        _fail("venv ensure must call python3 -m venv")
    migrate_py = (ROOT / "jobs" / "migrate.py").read_text(encoding="utf-8")
    if "migrate locked" not in migrate_py or "LOCK_ATTEMPTS" not in migrate_py:
        _fail("jobs/migrate.py must retry on SQLite database is locked")
    db_py = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    if "PRAGMA journal_mode=WAL" not in db_py:
        _fail("app/db.py must enable SQLite WAL")
    if "PRAGMA busy_timeout=30000" not in db_py:
        _fail("app/db.py must set SQLite busy_timeout")
    if '"timeout": 30.0' not in db_py:
        _fail("app/db.py sqlite connect_args must set timeout=30")


def test_rewrites_existing_github_host() -> None:
    setup = ROOT / "deploy" / "setup-github-ssh.sh"
    with tempfile.TemporaryDirectory(prefix="github-ssh-") as raw:
        home = Path(raw)
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        key = ssh_dir / "id_github"
        pub = ssh_dir / "id_github.pub"
        key.write_text("dummy-private\n", encoding="utf-8")
        pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIdummy stock@test\n", encoding="utf-8")
        key.chmod(stat.S_IRUSR | stat.S_IWUSR)
        pub.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        cfg = ssh_dir / "config"
        cfg.write_text(
            "Host github.com\n"
            f"  IdentityFile {key}\n"
            "  IdentitiesOnly yes\n",
            encoding="utf-8",
        )
        cfg.chmod(stat.S_IRUSR | stat.S_IWUSR)
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["APP_DIR"] = str(home / "no-app")
        env["REPO_SSH"] = "git@github.com:pekumirui/stock-analyzer.git"
        proc = subprocess.run(
            ["bash", str(setup)],
            cwd=str(home),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            _fail(
                "setup-github-ssh.sh failed on existing Host github.com block: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        text = cfg.read_text(encoding="utf-8")
        if "Port 443" not in text or "ssh.github.com" not in text:
            _fail(f"existing Host github.com block was not rewritten to port 443:\n{text}")
        if "HostKeyAlias github.com" not in text:
            _fail(f"rewritten Host github.com block missing HostKeyAlias github.com:\n{text}")
        known = (ssh_dir / "known_hosts").read_text(encoding="utf-8")
        if "ssh.github.com" not in known:
            _fail("known_hosts missing ssh.github.com host keys")
        if "[ssh.github.com]:443" not in known:
            _fail("known_hosts missing [ssh.github.com]:443 host keys")


def _git(app: Path, env: dict[str, str], *args: str) -> None:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(app),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _fail(
            f"git {' '.join(args)} failed: rc={proc.returncode} "
            f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )


def _venv_ensure_block(update: str) -> str:
    start = update.find('if [ ! -x "$APP_DIR/.venv/bin/pip" ]; then')
    if start < 0:
        _fail("update.sh missing venv ensure block")
    end = update.find('"$APP_DIR/.venv/bin/pip" install', start)
    if end < 0:
        _fail("update.sh venv ensure block has no pip install after it")
    return update[start:end]


def _run_ensure_venv(app: Path, block: str) -> subprocess.CompletedProcess[str]:
    script = f"set -euo pipefail\nAPP_DIR={shlex.quote(app.as_posix())}\n{block}"
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(app),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_update(app: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = ROOT / "deploy" / "update.sh"
    try:
        return subprocess.run(
            ["bash", str(script)],
            cwd=str(app),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        _fail(
            "deploy/update.sh timed out: "
            f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
        )


def test_tracked_dirty_stops_deploy() -> None:
    with tempfile.TemporaryDirectory(prefix="deploy-dirty-") as raw:
        root = Path(raw)
        app = root / "app"
        home = root / "home"
        app.mkdir()
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["APP_DIR"] = str(app)
        env["GIT_TERMINAL_PROMPT"] = "0"
        _git(app, env, "init")
        _git(app, env, "config", "user.email", "deploy-test@example.com")
        _git(app, env, "config", "user.name", "deploy-test")
        tracked = app / "readme.txt"
        tracked.write_text("one\n", encoding="utf-8")
        _git(app, env, "add", "readme.txt")
        _git(app, env, "commit", "-m", "init")

        tracked.write_text("two\n", encoding="utf-8")
        note = app / "note.txt"
        note.write_text("note\n", encoding="utf-8")
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(app),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if dirty.returncode != 0 or not dirty.stdout.strip():
            _fail(f"expected tracked dirt before deploy: {dirty.stderr!r}")
        proc = _run_update(app, env)
        if proc.returncode != 1:
            _fail(
                "tracked dirt must exit 1 before deploy: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        for needle in (
            "git → CI → VPS",
            "Hand edits",
            "SCP",
            "not allowed",
            "detected to stop deploy",
        ):
            if needle not in proc.stderr:
                _fail(f"tracked-dirty stderr missing {needle!r}: {proc.stderr!r}")
        if dirty.stdout.strip() not in proc.stderr:
            _fail(
                "tracked-dirty stderr missing porcelain line "
                f"{dirty.stdout.strip()!r}: {proc.stderr!r}"
            )
        if "deploy start" in proc.stdout or "deploy start" in proc.stderr:
            _fail(
                "tracked dirt reached deploy start: "
                f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        still = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(app),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if still.returncode != 0 or not still.stdout.strip():
            _fail(f"tracked file was repaired: {still.stdout!r} {still.stderr!r}")
        if not note.is_file():
            _fail("untracked note.txt was removed")

        _git(app, env, "add", "readme.txt")
        _git(app, env, "commit", "-m", "fresh")
        (app / ".env").write_text("X=1\n", encoding="utf-8")
        (app / "stock.db").write_bytes(b"")
        (app / "logs").mkdir()
        (app / "logs" / "app.log").write_text("log\n", encoding="utf-8")
        note.write_text("note\n", encoding="utf-8")
        clean = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(app),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if clean.returncode != 0 or clean.stdout.strip():
            _fail(f"case B must have no tracked dirt: {clean.stdout!r} {clean.stderr!r}")
        proc = _run_update(app, env)
        if "deploy start" not in proc.stdout:
            _fail(
                "untracked and ignored files must pass the dirty gate: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        if "Hand edits" in proc.stderr:
            _fail(f"untracked tree hit the dirty policy: {proc.stderr!r}")


def test_ensure_venv_unlinks_blocking_symlink() -> None:
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    block = _venv_ensure_block(update)
    with tempfile.TemporaryDirectory(prefix="deploy-venv-") as raw:
        root = Path(raw)
        app = root / "app"
        keep = root / "keep-venv"
        app.mkdir()
        keep.mkdir()
        marker = keep / "KEEP_ME"
        marker.write_text("safe\n", encoding="utf-8")
        venv = app / ".venv"
        venv.symlink_to(keep)
        if not venv.is_symlink():
            _fail("setup did not create a .venv symlink")
        proc = _run_ensure_venv(app, block)
        if proc.returncode != 0:
            _fail(
                "blocking .venv symlink must be replaced with a real venv: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        if venv.is_symlink():
            _fail("blocking .venv symlink was left in place")
        if not (venv / "bin" / "pip").exists():
            _fail("python -m venv did not create .venv/bin/pip")
        if not marker.is_file() or marker.read_text(encoding="utf-8") != "safe\n":
            _fail("ensure venv followed a symlink and damaged the target")
        if "venv が無いので作ります" not in proc.stdout:
            _fail(f"missing venv create message: {proc.stdout!r}")


def test_ensure_venv_keeps_real_directory() -> None:
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    block = _venv_ensure_block(update)
    with tempfile.TemporaryDirectory(prefix="deploy-venv-dir-") as raw:
        app = Path(raw) / "app"
        venv = app / ".venv"
        venv.mkdir(parents=True)
        marker = venv / "KEEP_DIR"
        marker.write_text("dir\n", encoding="utf-8")
        proc = _run_ensure_venv(app, block)
        if proc.returncode != 0:
            _fail(
                "existing .venv directory must be reused by python -m venv: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        if not marker.is_file():
            _fail("ensure venv deleted a real .venv directory")
        if not (venv / "bin" / "pip").exists():
            _fail("python -m venv did not populate existing .venv")


def main() -> None:
    test_static_contracts()
    test_rewrites_existing_github_host()
    test_tracked_dirty_stops_deploy()
    test_ensure_venv_unlinks_blocking_symlink()
    test_ensure_venv_keeps_real_directory()
    print("ok: github ssh port 443")


if __name__ == "__main__":
    main()

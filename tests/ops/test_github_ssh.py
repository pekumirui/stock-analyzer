"""GitHub deploy SSH must use port 443, and VPS_DOMAIN debug must stay off bash stdin."""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
    if "jobs/migrate.py" not in update:
        _fail("update.sh must run jobs/migrate.py")
    if ".venv.building" not in update:
        _fail("update.sh must create a missing venv in a staging directory")
    if '[ -L "$APP_DIR/.venv" ]' not in update:
        _fail("update.sh must treat a symlink .venv as broken")
    if "ln -sfn python3" not in update:
        _fail("update.sh must link .venv/bin/python to python3 when missing")
    if "ensurepip" not in update:
        _fail("update.sh must restore pip with ensurepip when missing")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    if re.search(r"^\.venv/", gitignore, flags=re.M):
        _fail(".gitignore .venv/ does not ignore a symlink .venv")
    if not re.search(r"^\.venv$", gitignore, flags=re.M):
        _fail(".gitignore must ignore .venv as a file or directory")
    tracked_venv = subprocess.check_output(
        ["git", "ls-files", "-z", "--", ".venv"],
        cwd=ROOT,
    )
    if tracked_venv:
        _fail(f".venv must not be tracked: {tracked_venv!r}")


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


def main() -> None:
    test_static_contracts()
    test_rewrites_existing_github_host()
    test_tracked_dirty_stops_deploy()
    print("ok: github ssh port 443")


if __name__ == "__main__":
    main()

"""GitHub deploy SSH must use port 443, and VPS_DOMAIN debug must stay off bash stdin."""
from __future__ import annotations

import fcntl
import os
import re
import shutil
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
    enable = (ROOT / "deploy" / "enable-cicd.sh").read_text(encoding="utf-8")
    if "Port 443" not in enable or "ssh.github.com" not in enable:
        _fail("enable-cicd.sh must force GitHub SSH port 443 before clone")
    if "if ! grep -qE '^Host github\\.com$'" in enable:
        _fail(
            "enable-cicd.sh must rewrite existing Host github.com blocks; "
            "do not skip via grep -qE '^Host github.com$' early-out"
        )
    if "<<'GITHUB_SSH_BOOTSTRAP'" not in enable:
        _fail("enable-cicd.sh must use one GITHUB_SSH_BOOTSTRAP heredoc before clone")
    if enable.count("<<'GITHUB_SSH_BOOTSTRAP'") != 1:
        _fail("enable-cicd.sh must have exactly one pre-clone SSH bootstrap")

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
    prefix = update.split("git fetch origin main", 1)[0]
    if "setup-github-ssh.sh" in prefix:
        _fail("update.sh must not run on-disk setup-github-ssh.sh before fetch")
    for label, text in (
        ("setup-github-ssh.sh", setup),
        ("update.sh", update),
        ("enable-cicd.sh", enable),
    ):
        block = _host_github_block(text, label)
        if "HostKeyAlias github.com" not in block:
            _fail(f"{label} Host github.com block missing HostKeyAlias github.com")
    for host in ("github.com", "ssh.github.com", "[ssh.github.com]:443"):
        if host not in update:
            _fail(f"update.sh must embed official host key for {host}")
        if host not in enable:
            _fail(f"enable-cicd.sh must embed official host key for {host}")
    if GITHUB_ED25519 not in update:
        _fail("update.sh must embed official GitHub host keys before fetch, not only alias known_hosts")
    if GITHUB_ED25519 not in enable:
        _fail("enable-cicd.sh must embed official GitHub host keys before clone")
    if "jobs/migrate.py" not in update:
        _fail("update.sh must run jobs/migrate.py")
    mig_at = update.find('"$venv_py" jobs/migrate.py')
    restart_at = update.find("systemctl restart stock-analyzer")
    if mig_at < 0 or restart_at < 0 or mig_at > restart_at:
        _fail("update.sh must run jobs/migrate.py before restarting stock-analyzer")
    if re.search(r"(^|[^\s#])--preload(\s|$)", update, flags=re.M):
        _fail("update.sh must not pass uvicorn --preload")
    if ".venv.building" not in update:
        _fail("update.sh must create a missing venv in a staging directory")
    if '[ -L "$APP_DIR/.venv" ]' not in update:
        _fail("update.sh must treat a symlink .venv as broken")
    if "ln -sfn python3" not in update:
        _fail("update.sh must link .venv/bin/python to python3 when missing")
    if "ensurepip" not in update:
        _fail("update.sh must restore pip with ensurepip when missing")
    if 'LOCK_FILE="${DEPLOY_LOCK:-/tmp/stock-analyzer-deploy.lock}"' not in update:
        _fail("update.sh must allow DEPLOY_LOCK override with the production default")
    if 'exec 9>"$LOCK_FILE"' not in update:
        _fail("update.sh must flock via LOCK_FILE")
    if "exec 9>/tmp/stock-analyzer-deploy.lock" in update:
        _fail("update.sh must not hardcode the production lock path on exec")
    if "install_venv()" not in update or "ensure_app_venv()" not in update:
        _fail("update.sh must name install_venv / ensure_app_venv")
    if "mv \"$APP_DIR/.venv.old\" \"$APP_DIR/.venv\"" not in update:
        _fail("update.sh must restore .venv.old if staging mv fails")
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


def _run_update(
    app: Path, env: dict[str, str], *, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    if not env.get("DEPLOY_LOCK"):
        _fail("DEPLOY_LOCK must be a tmp_path lock file")
    script = ROOT / "deploy" / "update.sh"
    try:
        return subprocess.run(
            ["bash", str(script)],
            cwd=str(app),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        _fail(
            "deploy/update.sh timed out: "
            f"stdout={exc.stdout!r} stderr={exc.stderr!r}"
        )


def test_tracked_dirty_stops_deploy(tmp_path: Path) -> None:
    root = tmp_path
    app = root / "app"
    home = root / "home"
    app.mkdir()
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["APP_DIR"] = str(app)
    env["DEPLOY_LOCK"] = str(root / "deploy.lock")
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


def _heredoc(text: str, marker: str) -> str:
    token = f"<<'{marker}'"
    start = text.find(token)
    if start < 0:
        _fail(f"missing {marker} heredoc")
    body = text[start + len(token) :]
    if body.startswith("\n"):
        body = body[1:]
    end = body.find(f"\n{marker}\n")
    if end < 0:
        end = body.find(f"\n{marker}")
    if end < 0:
        _fail(f"unclosed {marker} heredoc")
    return body[:end]


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_enable_cicd_bootstrap_rewrites_host() -> None:
    enable = (ROOT / "deploy" / "enable-cicd.sh").read_text(encoding="utf-8")
    body = _heredoc(enable, "GITHUB_SSH_BOOTSTRAP")
    if "Port 443" not in body or "HostKeyAlias github.com" not in body:
        _fail("pre-clone bootstrap missing port 443 rewrite")
    with tempfile.TemporaryDirectory(prefix="enable-cicd-ssh-") as raw:
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
            ["bash", "-s"],
            input=body,
            cwd=str(home),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            _fail(
                "enable-cicd pre-clone bootstrap failed: "
                f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
            )
        text = cfg.read_text(encoding="utf-8")
        if "Port 443" not in text or "ssh.github.com" not in text:
            _fail(f"pre-clone bootstrap did not rewrite Host github.com to port 443:\n{text}")
        if "HostKeyAlias github.com" not in text:
            _fail(f"pre-clone bootstrap missing HostKeyAlias github.com:\n{text}")
        known = (ssh_dir / "known_hosts").read_text(encoding="utf-8")
        if "ssh.github.com" not in known or "[ssh.github.com]:443" not in known:
            _fail(f"pre-clone bootstrap known_hosts incomplete:\n{known}")
        proc2 = subprocess.run(
            ["bash", "-s"],
            input=body,
            cwd=str(home),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc2.returncode != 0:
            _fail(
                "enable-cicd pre-clone bootstrap re-run failed: "
                f"rc={proc2.returncode} stderr={proc2.stderr!r}"
            )
        if cfg.read_text(encoding="utf-8") != text:
            _fail("pre-clone bootstrap is not idempotent")


def test_enable_cicd_piped_stdin_keeps_heredoc() -> None:
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo start\n"
        "bash -s <<'GITHUB_SSH_BOOTSTRAP'\n"
        "set -euo pipefail\n"
        "echo inner\n"
        "GITHUB_SSH_BOOTSTRAP\n"
        "echo after\n"
    )
    proc = subprocess.run(
        ["bash", "-s", "arg1"],
        input=script,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _fail(f"piped stdin+heredoc failed: rc={proc.returncode} stderr={proc.stderr!r}")
    if proc.stdout.splitlines() != ["start", "inner", "after"]:
        _fail(f"piped stdin+heredoc stdout={proc.stdout!r}")


def _seed_git(app: Path, env: dict[str, str], origin: Path) -> None:
    (app / "jobs").mkdir(parents=True)
    (app / "deploy").mkdir()
    (app / "requirements.txt").write_text("# test\n", encoding="utf-8")
    (app / "jobs" / "migrate.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (app / "deploy" / "provision.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nexit 0\n",
        encoding="utf-8",
    )
    (app / "deploy" / "provision.sh").chmod(0o755)
    shutil.copy2(ROOT / "deploy" / "write_rclone_conf.py", app / "deploy" / "write_rclone_conf.py")
    stale = app / "deploy" / "setup-github-ssh.sh"
    stale.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf 'stale\\n' >> \"${HOME}/stale-ssh-ran\"\n"
        "install -d -m 700 \"$HOME/.ssh\"\n"
        "cfg=\"$HOME/.ssh/config\"\n"
        "printf 'Host github.com\\n  Port 22\\n' > \"$cfg\"\n",
        encoding="utf-8",
    )
    stale.chmod(0o755)
    _git(app, env, "init", "-b", "main")
    _git(app, env, "config", "user.email", "deploy-test@example.com")
    _git(app, env, "config", "user.name", "deploy-test")
    _git(app, env, "add", "requirements.txt", "jobs/migrate.py", "deploy")
    _git(app, env, "commit", "-m", "seed")
    origin.mkdir()
    _git(origin, env, "init", "--bare", "-b", "main")
    _git(app, env, "remote", "add", "origin", str(origin))
    _git(app, env, "push", "-u", "origin", "main")


def _deploy_stubs(bin_dir: Path) -> None:
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
        "  if [[ \"${2:-}\" == is-active ]]; then\n"
        "    echo active\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ -n \"${1:-}\" && -x \"$1\" ]]; then\n"
        "  exec \"$1\" \"${@:2}\"\n"
        "fi\n"
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
        "if [[ \"$out\" == *http_code* ]]; then\n"
        "  printf '%s' \"$code\"\n"
        "fi\n"
        "exit 0\n",
    )
    _write_exec(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == is-active ]]; then\n"
        "  echo active\n"
        "fi\n"
        "exit 0\n",
    )


def test_update_sh_bootstrap_and_venv(tmp_path: Path) -> None:
    root = tmp_path
    app = root / "app"
    home = root / "home"
    origin = root / "origin.git"
    bin_dir = root / "bin"
    app.mkdir()
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["APP_DIR"] = str(app)
    env["DEPLOY_LOCK"] = str(root / "deploy.lock")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    real_py = shutil.which("python3")
    if not real_py:
        _fail("python3 not on PATH")
    _deploy_stubs(bin_dir)
    _seed_git(app, env, origin)

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

    proc = _run_update(app, env, timeout=120)
    if proc.returncode != 0:
        _fail(
            "update.sh disposable run failed: "
            f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    if "venv が無いので作ります" not in proc.stdout:
        _fail(f"expected staging venv create: stdout={proc.stdout!r}")
    stale_mark = home / "stale-ssh-ran"
    if stale_mark.exists():
        _fail("on-disk setup-github-ssh.sh ran before/during deploy")
    text = cfg.read_text(encoding="utf-8")
    if "Port 443" not in text or "ssh.github.com" not in text:
        _fail(f"piped bootstrap did not rewrite Host github.com:\n{text}")
    venv = app / ".venv"
    if venv.is_symlink() or not (venv / "bin" / "python3").is_file():
        _fail(f"expected real .venv with python3, got {venv} symlink={venv.is_symlink()}")
    canary = venv / "canary"
    canary.write_text("keep\n", encoding="utf-8")
    origin_url = subprocess.check_output(
        ["git", "-C", str(app), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    if origin_url != str(origin):
        _fail(f"local origin was rewritten: {origin_url!r}")

    proc = _run_update(app, env, timeout=120)
    if proc.returncode != 0:
        _fail(
            "update.sh re-run failed: "
            f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    if "venv が無いので作ります" in proc.stdout:
        _fail("working venv was rebuilt")
    if canary.read_text(encoding="utf-8") != "keep\n":
        _fail("re-run replaced a working .venv")

    alt = root / "venv-alt"
    subprocess.run(["python3", "-m", "venv", str(alt)], check=True, capture_output=True)
    venv.rename(root / "venv-real")
    venv.symlink_to(alt)
    proc = _run_update(app, env, timeout=120)
    if proc.returncode != 0:
        _fail(
            "update.sh symlink .venv rebuild failed: "
            f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    if "venv が無いので作ります" not in proc.stdout:
        _fail("symlink .venv was not rebuilt")
    if venv.is_symlink():
        _fail("symlink .venv was not replaced with a directory")
    if (app / ".venv.old").exists():
        _fail(".venv.old was left behind")
    if (app / ".venv.building").exists() or any(app.glob(".venv.building.*")):
        _fail("staging venv directory was left behind")

    fake_py = bin_dir / "python3"
    _write_exec(
        fake_py,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ \" $* \" == *\" -m venv \"* ]]; then\n"
        "  echo fake-venv-fail >&2\n"
        "  exit 1\n"
        "fi\n"
        f"exec {real_py!r} \"$@\"\n",
    )
    canary.write_text("keep-fail\n", encoding="utf-8")
    venv.rename(root / "venv-ok")
    venv.symlink_to(alt)
    proc = _run_update(app, env, timeout=60)
    if proc.returncode == 0:
        _fail("expected venv create failure to abort deploy")
    if "fake-venv-fail" not in proc.stderr and "fake-venv-fail" not in proc.stdout:
        _fail(f"missing venv failure: stderr={proc.stderr!r} stdout={proc.stdout!r}")
    if not venv.is_symlink():
        _fail("failed venv create replaced the existing .venv")


def test_update_sh_busy_lock_exits_1(tmp_path: Path) -> None:
    app = tmp_path / "app"
    home = tmp_path / "home"
    app.mkdir()
    home.mkdir()
    lock = tmp_path / "deploy.lock"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["APP_DIR"] = str(app)
    env["DEPLOY_LOCK"] = str(lock)
    env["GIT_TERMINAL_PROMPT"] = "0"
    _git(app, env, "init")
    _git(app, env, "config", "user.email", "deploy-test@example.com")
    _git(app, env, "config", "user.name", "deploy-test")
    tracked = app / "readme.txt"
    tracked.write_text("one\n", encoding="utf-8")
    _git(app, env, "add", "readme.txt")
    _git(app, env, "commit", "-m", "init")

    with lock.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = _run_update(app, env)
    if proc.returncode != 1:
        _fail(
            "busy deploy lock must exit 1: "
            f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    if "another deploy is running" not in proc.stderr:
        _fail(f"busy lock stderr missing message: {proc.stderr!r}")
    if "deploy start" in proc.stdout or "deploy start" in proc.stderr:
        _fail(
            "busy lock reached deploy start: "
            f"stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )


def main() -> None:
    test_static_contracts()
    test_rewrites_existing_github_host()
    with tempfile.TemporaryDirectory(prefix="deploy-dirty-") as raw:
        test_tracked_dirty_stops_deploy(Path(raw))
    test_enable_cicd_bootstrap_rewrites_host()
    test_enable_cicd_piped_stdin_keeps_heredoc()
    with tempfile.TemporaryDirectory(prefix="update-venv-") as raw:
        test_update_sh_bootstrap_and_venv(Path(raw))
    with tempfile.TemporaryDirectory(prefix="deploy-lock-") as raw:
        test_update_sh_busy_lock_exits_1(Path(raw))
    print("ok: github ssh port 443")



if __name__ == "__main__":
    main()

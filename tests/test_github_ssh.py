"""GitHub deploy SSH must use port 443, and VPS_DOMAIN debug must stay off bash stdin."""
from __future__ import annotations

import os
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


def main() -> None:
    test_static_contracts()
    test_rewrites_existing_github_host()
    print("ok: github ssh port 443")


if __name__ == "__main__":
    main()

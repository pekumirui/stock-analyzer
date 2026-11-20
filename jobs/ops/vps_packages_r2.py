"""VPS のパッケージ/バージョン一覧を data/inventory へ書く。

使い方:
  python jobs/ops/vps_packages_r2.py
  python jobs/ops/vps_packages_r2.py --dest /tmp/inventory

読み取り専用。秘密情報は書かない。無いコマンドは not installed。
ネットワークと app/ は使わない。
"""
from __future__ import annotations

import argparse
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs._r2common import MountDisconnected, fail_if_mount_down
from app.share_paths import share_path

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEST = str(share_path("inventory"))
LATEST_NAME = "vps-packages.txt"
OS_RELEASE_PATH = Path("/etc/os-release")
CMD_TIMEOUT_SEC = 60.0
NOT_INSTALLED = "not installed"
REQUIRED_DPKG = (
    "nginx",
    "openssh-server",
    "openssl",
    "certbot",
    "python3",
    "fluent-bit",
    "fuse3",
    "ufw",
)
SECTION_PIP = "pip freeze"
SECTION_DPKG = "dpkg-query -W"
SECTION_UNAME = "uname -r"
SECTION_OS_RELEASE = "/etc/os-release"
SECTION_RCLONE = "rclone version"

_CRED_URL_RE = re.compile(r"(://[^/\s:@]+):([^/\s@]+)@")
_SECRET_LINE_RE = re.compile(
    r"(?i)\b("
    r"JQUANTS_API_KEY|R2_ACCESS_KEY_ID|R2_SECRET_ACCESS_KEY|MCP_TOKEN|"
    r"SLACK_WEBHOOK(?:_URL|_URI)?(?:_[A-Z]+)?|AWS_SECRET_ACCESS_KEY|"
    r"BASIC_AUTH_PASSWORD|PRIVATE_KEY|BEGIN [A-Z ]*PRIVATE"
    r")\b"
)

WhichFn = Callable[[str], str | None]
RunCmdFn = Callable[[Sequence[str]], str]


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def sanitize_output(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = _CRED_URL_RE.sub(r"://<redacted>@", raw)
        if _SECRET_LINE_RE.search(line):
            lines.append("<redacted>")
        else:
            lines.append(line)
    return "\n".join(lines)


def run_cmd(argv: Sequence[str], timeout: float = CMD_TIMEOUT_SEC) -> str:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return NOT_INSTALLED
    except (OSError, subprocess.SubprocessError):
        return NOT_INSTALLED
    out = (proc.stdout or "").replace("\r\n", "\n").rstrip("\n")
    if proc.returncode != 0 and not out.strip():
        return NOT_INSTALLED
    return sanitize_output(out) if out else NOT_INSTALLED


def resolve_bin(name: str, which: WhichFn | None = None) -> str | None:
    finder = shutil.which if which is None else which
    found = finder(name)
    return found or None


def capture_bin(
    name: str,
    args: Sequence[str],
    *,
    run: RunCmdFn | None = None,
    which: WhichFn | None = None,
) -> str:
    exe = resolve_bin(name, which=which)
    if exe is None:
        return NOT_INSTALLED
    runner = run_cmd if run is None else run
    return runner([exe, *args])


def venv_python(repo_root: Path) -> Path | None:
    for name in ("python3", "python"):
        cand = repo_root / ".venv" / "bin" / name
        if cand.is_file():
            return cand
    return None


def capture_pip_freeze(
    repo_root: Path,
    *,
    run: RunCmdFn | None = None,
) -> str:
    py = venv_python(repo_root)
    if py is None:
        return NOT_INSTALLED
    runner = run_cmd if run is None else run
    return runner([str(py), "-m", "pip", "freeze", "--disable-pip-version-check"])


def read_os_release(path: Path = OS_RELEASE_PATH) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return NOT_INSTALLED
    text = text.replace("\r\n", "\n").rstrip("\n")
    return sanitize_output(text) if text else NOT_INSTALLED


def dpkg_package_name(line: str) -> str:
    raw = line.split("\t", 1)[0].strip()
    if not raw:
        return ""
    return raw.split(":", 1)[0]


def with_required_packages(dpkg_text: str, required: Sequence[str] = REQUIRED_DPKG) -> str:
    body = (dpkg_text or "").rstrip("\n")
    if body == NOT_INSTALLED:
        extra = "\n".join(f"{name}\t{NOT_INSTALLED}" for name in required)
        return f"{body}\n{extra}"
    present = {dpkg_package_name(line) for line in body.splitlines()}
    missing = [name for name in required if name not in present]
    if not missing:
        return body
    extra = "\n".join(f"{name}\t{NOT_INSTALLED}" for name in missing)
    return f"{body}\n{extra}" if body else extra


def format_generated_at(when: datetime) -> str:
    local = when.astimezone(JST) if when.tzinfo else when.replace(tzinfo=JST)
    return local.strftime("%Y-%m-%d %H:%M:%S JST")


def build_report(
    *,
    generated_at: str,
    hostname: str,
    pip_freeze: str,
    dpkg: str,
    uname: str,
    os_release: str,
    rclone: str,
) -> str:
    parts = [
        f"generated-at: {generated_at}",
        f"hostname: {hostname}",
        "",
        f"=== {SECTION_PIP} ===",
        pip_freeze.rstrip("\n") or NOT_INSTALLED,
        "",
        f"=== {SECTION_DPKG} ===",
        dpkg.rstrip("\n") or NOT_INSTALLED,
        "",
        f"=== {SECTION_UNAME} ===",
        uname.rstrip("\n") or NOT_INSTALLED,
        "",
        f"=== {SECTION_OS_RELEASE} ===",
        os_release.rstrip("\n") or NOT_INSTALLED,
        "",
        f"=== {SECTION_RCLONE} ===",
        rclone.rstrip("\n") or NOT_INSTALLED,
        "",
    ]
    return "\n".join(parts)


def collect_report(
    when: datetime,
    hostname: str,
    *,
    repo_root: Path | None = None,
    os_release_path: Path = OS_RELEASE_PATH,
    run: RunCmdFn | None = None,
    which: WhichFn | None = None,
) -> str:
    root = REPO_ROOT if repo_root is None else repo_root
    pip_text = capture_pip_freeze(root, run=run)
    dpkg_text = with_required_packages(
        capture_bin("dpkg-query", ["-W"], run=run, which=which)
    )
    uname_text = capture_bin("uname", ["-r"], run=run, which=which)
    rclone_text = capture_bin("rclone", ["version"], run=run, which=which)
    return build_report(
        generated_at=format_generated_at(when),
        hostname=hostname,
        pip_freeze=pip_text,
        dpkg=dpkg_text,
        uname=uname_text,
        os_release=read_os_release(os_release_path),
        rclone=rclone_text,
    )


def history_name(when: datetime) -> str:
    local = when.astimezone(JST) if when.tzinfo else when.replace(tzinfo=JST)
    return f"vps-packages-{local.strftime('%Y%m%d')}.txt"


def write_inventory(dest: Path, body: str, when: datetime) -> tuple[Path, Path]:
    data = body.encode("utf-8")
    if not data.endswith(b"\n"):
        data += b"\n"
    latest = dest / LATEST_NAME
    history = dest / "history" / history_name(when)
    atomic_write(latest, data)
    atomic_write(history, data)
    return latest, history


def run(
    dest: Path,
    *,
    now: datetime | None = None,
    hostname: str | None = None,
    repo_root: Path | None = None,
    os_release_path: Path = OS_RELEASE_PATH,
    run_cmd_fn: RunCmdFn | None = None,
    which: WhichFn | None = None,
) -> int:
    rc = fail_if_mount_down("vps_packages_r2", dest, require_existing=False)
    if rc is not None:
        return rc
    when = now or datetime.now(JST)
    host = socket.gethostname() if hostname is None else hostname
    print(f"vps_packages_r2 start dest={dest}", flush=True)
    body = collect_report(
        when,
        host,
        repo_root=repo_root,
        os_release_path=os_release_path,
        run=run_cmd_fn,
        which=which,
    )
    latest, history = write_inventory(dest, body, when)
    print(f"saved {latest}", flush=True)
    print(f"saved {history}", flush=True)
    print("vps_packages_r2 done", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Write VPS package inventory to data/inventory"
    )
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    args = ap.parse_args(argv)
    try:
        return run(Path(args.dest))
    except MountDisconnected as e:
        print(f"vps_packages_r2 {e}", flush=True)
        return 1
    except Exception as e:
        print(f"fail vps_packages_r2: {e}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

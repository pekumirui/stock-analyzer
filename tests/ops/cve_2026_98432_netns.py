"""Netns probe for the CVE-2026-98432 mitigation.

Started as a script inside a fresh network namespace. Stdlib only: CI falls
back to sudo, and that Python does not have pytest installed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX_SITE = ROOT / "deploy" / "nginx-site.sh"
SERVICE_PORTS = (22, 80, 443)


def _mcp_port() -> int:
    text = NGINX_SITE.read_text(encoding="utf-8")
    match = re.search(
        r'MCP_UPSTREAM="\$\{MCP_UPSTREAM:-127\.0\.0\.1:(\d+)\}"',
        text,
    )
    if not match:
        raise SystemExit("nginx-site.sh does not define an MCP upstream port")
    return int(match.group(1))


def _ports() -> tuple[int, ...]:
    return (*SERVICE_PORTS, _mcp_port())


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


SYSTEMCTL_STUB = r"""#!/usr/bin/env python3
import os, shlex, subprocess, sys
from pathlib import Path

log = Path(os.environ["CVE_TEST_LOG"])
state = Path(os.environ["CVE_TEST_STATE"])
args = [a for a in sys.argv[1:] if a not in ("--quiet", "--no-pager", "--system", "--no-block")]
with log.open("a", encoding="utf-8") as fh:
    fh.write(" ".join(args) + "\n")
if not args:
    sys.exit(1)
cmd, rest = args[0], args[1:]
unit = rest[-1] if rest else ""

def distro_unit(name: str) -> str:
    for base in ("/usr/lib/systemd/system", "/lib/systemd/system"):
        path = Path(base) / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""

def run_key(text: str, key: str) -> None:
    prefix = key + "="
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        val = line[len(prefix):].strip()
        ignore = val.startswith("-")
        if ignore:
            val = val[1:].strip()
        proc = subprocess.run(shlex.split(val), check=False)
        if proc.returncode != 0 and not ignore:
            sys.exit(proc.returncode)

if cmd == "cat":
    text = distro_unit(unit)
    if not text:
        sys.exit(1)
    sys.stdout.write(text)
    sys.exit(0)
if cmd == "is-enabled":
    if unit == "nftables.service" and (state / "nftables-enabled").is_file():
        print("enabled")
        sys.exit(0)
    print("disabled")
    sys.exit(1)
if cmd == "is-active":
    sys.exit(0 if (state / f"active-{unit}").is_file() else 1)
if cmd == "enable":
    sys.exit(0)
if cmd == "disable":
    if unit == "nftables.service":
        (state / "nftables-enabled").unlink(missing_ok=True)
    sys.exit(0)
if cmd == "daemon-reload":
    sys.exit(0)
if cmd in ("start", "reload", "stop"):
    text = Path("/etc/systemd/system") / unit
    if not text.is_file():
        sys.exit(1)
    body = text.read_text(encoding="utf-8")
    if cmd == "start":
        run_key(body, "ExecStart")
        (state / f"active-{unit}").write_text("1", encoding="utf-8")
    elif cmd == "reload":
        run_key(body, "ExecReload")
    else:
        run_key(body, "ExecStop")
        (state / f"active-{unit}").unlink(missing_ok=True)
    sys.exit(0)
sys.exit(1)
"""


def _listen(stop: threading.Event, port: int, family: int, addr: str) -> None:
    sock = socket.socket(family, socket.SOCK_STREAM)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((addr, port))
    sock.listen(16)
    sock.settimeout(0.2)
    try:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            conn.close()
    finally:
        sock.close()


def _connect(port: int, family: int = socket.AF_INET, addr: str = "127.0.0.1", options: bool = False) -> str:
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(0.5 if options else 1.0)
    try:
        if options:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_OPTIONS, b"\x01\x01\x01\x00")
        sock.connect((addr, port))
    except OSError as exc:
        return type(exc).__name__
    else:
        return "ok"
    finally:
        sock.close()


def _counter() -> int:
    raw = subprocess.check_output(["nft", "-j", "list", "ruleset"], text=True)
    total = 0
    for item in json.loads(raw).get("nftables", []):
        rule = item.get("rule") or {}
        for expr in rule.get("expr") or []:
            counter = expr.get("counter")
            if isinstance(counter, dict):
                total += int(counter.get("packets") or 0)
    return total


def _tables() -> list[str]:
    raw = subprocess.check_output(["nft", "-j", "list", "tables"], text=True)
    names: list[str] = []
    for item in json.loads(raw).get("nftables", []):
        table = item.get("table") or {}
        if table.get("family") == "inet" and table.get("name"):
            names.append(table["name"])
    return sorted(names)


def _apply(app: Path) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/cve-2026-98432.sh\"\n"
        "cve_2026_98432_apply\n"
    )
    env = os.environ.copy()
    env["APP_DIR"] = str(app)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def probe() -> dict[str, object]:
    marker = Path("/etc/.cve-netns-probe")
    if os.environ.get("CVE_NETNS_PROBE") != "1" or not marker.is_file() or marker.read_text().strip() != "ok":
        return {"error": "refusing to load nft rules outside the test netns"}
    work = Path(tempfile.mkdtemp(prefix="cve-probe-"))
    state = work / "state"
    state.mkdir()
    (state / "nftables-enabled").write_text("1", encoding="utf-8")
    _write_exe(work / "systemctl", SYSTEMCTL_STUB)
    _write_exe(
        work / "ufw",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CVE_UFW_LOG\"\nexit 99\n",
    )
    _write_exe(
        work / "apt-get",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CVE_APT_LOG\"\nexit 99\n",
    )
    for name in ("reboot", "shutdown", "poweroff"):
        _write_exe(
            work / name,
            "#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$CVE_REBOOT_LOG\"\nexit 99\n",
        )
    os.environ["CVE_TEST_LOG"] = str(work / "systemctl.log")
    os.environ["CVE_TEST_STATE"] = str(state)
    os.environ["CVE_UFW_LOG"] = str(work / "ufw.log")
    os.environ["CVE_APT_LOG"] = str(work / "apt.log")
    os.environ["CVE_REBOOT_LOG"] = str(work / "reboot.log")
    os.environ["CVE_SYSTEMCTL"] = str(work / "systemctl")
    os.environ["CVE_APT_GET"] = str(work / "apt-get")
    os.environ["PATH"] = f"{work}{os.pathsep}{os.environ.get('PATH', '')}"
    for name in ("systemctl.log", "ufw.log", "apt.log", "reboot.log"):
        (work / name).write_text("", encoding="utf-8")

    distro = ""
    for base in ("/usr/lib/systemd/system/nftables.service", "/lib/systemd/system/nftables.service"):
        path = Path(base)
        if path.is_file():
            distro = path.read_text(encoding="utf-8")
            break
    subprocess.check_call(["nft", "add", "table", "inet", "ufw_sentinel"])
    stop = threading.Event()
    threads = [
        threading.Thread(target=_listen, args=(stop, port, socket.AF_INET, "127.0.0.1"), daemon=True)
        for port in _ports()
    ]
    threads.append(
        threading.Thread(target=_listen, args=(stop, 80, socket.AF_INET6, "::1"), daemon=True)
    )
    for thread in threads:
        thread.start()
    time.sleep(0.05)
    try:
        first = _apply(ROOT)
        second = _apply(ROOT)
        if first.returncode != 0 or second.returncode != 0:
            return {
                "error": "apply failed",
                "first_rc": first.returncode,
                "second_rc": second.returncode,
                "first_err": first.stderr,
                "second_err": second.stderr,
                "first_out": first.stdout,
                "second_out": second.stdout,
            }
        before = _counter()
        plain = {str(port): _connect(port) for port in _ports()}
        after_plain = _counter()
        v6 = _connect(80, socket.AF_INET6, "::1")
        after_v6 = _counter()
        options = {str(port): _connect(port, options=True) for port in _ports()}
        after_options = _counter()
        plain_again = {str(port): _connect(port) for port in _ports()}
        tables = _tables()
        systemctl_after = (work / "systemctl.log").read_text(encoding="utf-8").splitlines()
        (work / "systemctl.log").write_text("", encoding="utf-8")

        stop_proc = subprocess.run(
            [os.environ["CVE_SYSTEMCTL"], "stop", "cve-2026-98432.service"],
            check=False,
        )
        if stop_proc.returncode != 0:
            return {"error": f"unit stop failed rc={stop_proc.returncode}"}
        tables_after_stop = _tables()
        options_after_stop = _connect(80, options=True)
        plain_after_stop = _connect(80)

        (state / "active-cve-2026-98432.service").unlink(missing_ok=True)
        start_proc = subprocess.run(
            [os.environ["CVE_SYSTEMCTL"], "start", "cve-2026-98432.service"],
            check=False,
        )
        if start_proc.returncode != 0:
            return {"error": f"unit start failed rc={start_proc.returncode}"}
        tables_after_start = _tables()
        options_after_start = _connect(80, options=True)
        plain_after_start = _connect(80)

        subprocess.check_call(["nft", "flush", "ruleset"])
        (state / "active-cve-2026-98432.service").unlink(missing_ok=True)
        boot = subprocess.run(
            [os.environ["CVE_SYSTEMCTL"], "start", "cve-2026-98432.service"],
            check=False,
        )
        if boot.returncode != 0:
            return {"error": f"boot start failed rc={boot.returncode}"}
        tables_after_reboot = _tables()
        options_after_reboot = _connect(80, options=True)
        plain_after_reboot = _connect(80)

        bad_app = Path(tempfile.mkdtemp(prefix="cve-bad-"))
        shutil.copytree(ROOT / "deploy", bad_app / "deploy")
        (bad_app / "deploy" / "cve-2026-98432.nft").write_text(
            "table inet { not a ruleset\n",
            encoding="utf-8",
        )
        bad = _apply(bad_app)
        return {
            "plain": plain,
            "plain_again": plain_again,
            "v6": v6,
            "options": options,
            "counter_before": before,
            "counter_after_plain": after_plain,
            "counter_after_v6": after_v6,
            "counter_after_options": after_options,
            "tables": tables,
            "systemctl_after_apply": systemctl_after,
            "distro_flushes": "flush ruleset" in distro,
            "ufw": (work / "ufw.log").read_text(encoding="utf-8"),
            "apt": (work / "apt.log").read_text(encoding="utf-8"),
            "reboot": (work / "reboot.log").read_text(encoding="utf-8"),
            "unit_wanted_by": "WantedBy=" in Path(
                "/etc/systemd/system/cve-2026-98432.service"
            ).read_text(encoding="utf-8"),
            "tables_after_stop": tables_after_stop,
            "options_after_stop": options_after_stop,
            "plain_after_stop": plain_after_stop,
            "tables_after_start": tables_after_start,
            "options_after_start": options_after_start,
            "plain_after_start": plain_after_start,
            "tables_after_reboot": tables_after_reboot,
            "options_after_reboot": options_after_reboot,
            "plain_after_reboot": plain_after_reboot,
            "bad_rc": bad.returncode,
            "tables_after_bad": _tables(),
            "options_after_bad": _connect(80, options=True),
        }
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)


if __name__ == "__main__":
    json.dump(probe(), sys.stdout)
    sys.stdout.write("\n")

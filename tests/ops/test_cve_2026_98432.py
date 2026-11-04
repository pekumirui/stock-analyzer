"""CVE-2026-98432 の緩和と apt のセキュリティ更新。

ルールの文言（優先度・マッチ式・テーブル名）は決め打ちしない。
netns に読み込んだ結果として、普通の TCP が通り、IP オプション付きの
IPv4 TCP が落ち、先にあったテーブルが apply と当該 unit の停止で
消えないことを見る。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "deploy" / "provision.sh"
HELPER = ROOT / "deploy" / "cve-2026-98432.sh"
NGINX_SITE = ROOT / "deploy" / "nginx-site.sh"
SERVICE_PORTS = (22, 80, 443)


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _mcp_port() -> int:
    text = NGINX_SITE.read_text(encoding="utf-8")
    match = re.search(
        r'MCP_UPSTREAM="\$\{MCP_UPSTREAM:-127\.0\.0\.1:(\d+)\}"',
        text,
    )
    if not match:
        _fail("nginx-site.sh does not define an MCP upstream port")
    return int(match.group(1))


def _ports() -> tuple[int, ...]:
    return (*SERVICE_PORTS, _mcp_port())


def test_provision_runs_mitigation_on_full_path_only() -> None:
    text = PROVISION.read_text(encoding="utf-8")
    pre, sep, post = text.partition("--sudoers-only")
    if not sep:
        _fail("provision.sh lost the --sudoers-only early exit")
    if "apt_security_upgrade" in pre or "cve_2026_98432_apply" in pre:
        _fail("sudoers-only path must not upgrade apt or load nft rules")
    if "source \"$APP_DIR/deploy/cve-2026-98432.sh\"" not in post:
        _fail("provision.sh must source deploy/cve-2026-98432.sh")
    if post.index("apt_security_upgrade") > post.index("apt_retry \\"):
        _fail("apt must be upgraded before the general apt_retry install")
    if post.index("ufw allow") > post.index("cve_2026_98432_apply"):
        _fail("ufw allows must be installed before the mitigation is loaded")
    if "nginx_reload_if_pcre_deleted" not in post:
        _fail("provision.sh must reload nginx when libpcre2 was replaced")
    chk = subprocess.run(
        ["bash", "-n", str(PROVISION)],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n provision.sh failed: {chk.stderr or chk.stdout}")
    chk = subprocess.run(
        ["bash", "-n", str(HELPER)],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n cve-2026-98432.sh failed: {chk.stderr or chk.stdout}")
    sc = shutil.which("shellcheck")
    if sc:
        proc = subprocess.run(
            [sc, "-x", str(HELPER)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _fail(f"shellcheck failed:\n{proc.stdout}{proc.stderr}")


def _bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_apt_upgrade_does_not_touch_kernel_or_reboot(tmp_path: Path) -> None:
    log = tmp_path / "apt.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(
        bin_dir / "apt-get",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CVE_APT_LOG\"\n"
        "for arg in \"$@\"; do\n"
        "  case \"$arg\" in\n"
        "    linux|linux-*|dist-upgrade|full-upgrade|upgrade) exit 99 ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
    )
    for name in ("reboot", "shutdown", "poweroff", "systemctl"):
        _write_exe(
            bin_dir / name,
            "#!/bin/sh\n"
            "printf '%s %s\\n' \"$0\" \"$*\" >> \"$CVE_APT_LOG\"\n"
            "exit 99\n",
        )
    env = {
        "APP_DIR": str(ROOT),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CVE_APT_GET": str(bin_dir / "apt-get"),
        "CVE_APT_LOG": str(log),
        "CVE_SYSTEMCTL": str(bin_dir / "systemctl"),
    }
    script = (
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/cve-2026-98432.sh\"\n"
        "apt_security_upgrade\n"
        "apt_security_upgrade\n"
    )
    proc = _bash(script, env)
    if proc.returncode != 0:
        _fail(f"apt_security_upgrade failed:\n{proc.stdout}\n{proc.stderr}\n{log.read_text()}")
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    installs = [ln for ln in lines if "--only-upgrade" in ln]
    if len(installs) != 2:
        _fail(f"expected two only-upgrade installs, got {lines!r}")
    for ln in installs:
        if "Retries=0" not in ln:
            _fail(f"apt self-upgrade must not retry fetches: {ln}")
        if " apt " not in f" {ln} " and not ln.rstrip().endswith(" apt") and " apt " not in ln:
            _fail(f"install did not name apt: {ln}")
        if "apt-utils" not in ln:
            _fail(f"install did not name apt-utils: {ln}")
    joined = "\n".join(lines)
    for banned in ("reboot", "shutdown", "poweroff", "systemctl", "dist-upgrade", "full-upgrade"):
        if banned in joined:
            _fail(f"apt upgrade invoked {banned}: {joined}")


def test_nginx_reloads_only_for_deleted_pcre(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    log = tmp_path / "sc.log"
    active = tmp_path / "nginx-active"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CVE_SC_LOG\"\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"is-active\" ]; then\n"
        "    test -f \"$CVE_NGINX_ACTIVE\"\n"
        "    exit $?\n"
        "  fi\n"
        "done\n"
        "exit 0\n",
    )

    def run() -> list[str]:
        log.write_text("", encoding="utf-8")
        script = (
            "set -euo pipefail\n"
            "source \"$APP_DIR/deploy/cve-2026-98432.sh\"\n"
            "nginx_reload_if_pcre_deleted\n"
        )
        proc = _bash(
            script,
            {
                "APP_DIR": str(ROOT),
                "PROC_ROOT": str(proc_root),
                "CVE_SYSTEMCTL": str(bin_dir / "systemctl"),
                "CVE_SC_LOG": str(log),
                "CVE_NGINX_ACTIVE": str(active),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            },
        )
        if proc.returncode != 0:
            _fail(f"nginx reload check failed:\n{proc.stdout}\n{proc.stderr}")
        return log.read_text(encoding="utf-8").splitlines()

    def plant(pid: str, cmd: str, maps: str) -> None:
        d = proc_root / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "cmdline").write_bytes(cmd.encode() + b"\0")
        (d / "maps").write_text(maps, encoding="utf-8")

    plant(
        "10",
        "nginx: worker",
        "7f r-xp /usr/lib/libpcre2-8.so.0 (deleted)\n",
    )
    active.write_text("1", encoding="utf-8")
    lines = run()
    if not any(ln.startswith("reload") and "nginx" in ln for ln in lines):
        _fail(f"expected nginx reload, got {lines!r}")

    plant("10", "nginx: worker", "7f r-xp /usr/lib/libpcre2-8.so.0\n")
    lines = run()
    if any("reload" in ln for ln in lines):
        _fail(f"fresh libpcre2 must not reload nginx: {lines!r}")

    plant("10", "nginx: worker", "7f r-xp /usr/lib/libssl.so.3 (deleted)\n")
    lines = run()
    if any("reload" in ln for ln in lines):
        _fail(f"a deleted library other than libpcre2 must not reload nginx: {lines!r}")

    plant("11", "python server", "7f r-xp /usr/lib/libpcre2-8.so.0 (deleted)\n")
    plant("10", "nginx: worker", "7f r-xp /usr/lib/libpcre2-8.so.0\n")
    lines = run()
    if any("reload" in ln for ln in lines):
        _fail(f"non-nginx deleted libpcre2 must not reload nginx: {lines!r}")

    active.unlink()
    plant("10", "nginx: worker", "7f r-xp /usr/lib/libpcre2-8.so.0 (deleted)\n")
    lines = run()
    if any("reload" in ln for ln in lines):
        _fail(f"inactive nginx must not be reloaded: {lines!r}")


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


def _run_netns() -> subprocess.CompletedProcess[str]:
    for tool in ("unshare", "nft", "ip", "mount"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the netns mitigation probe")
    wrapped = "\n".join((
        "set -euo pipefail",
        "mount -t tmpfs tmpfs /etc",
        "mkdir -p /etc/systemd/system",
        "printf ok > /etc/.cve-netns-probe",
        "ip link set lo up",
        "export CVE_NETNS_PROBE=1",
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__)))}",
    ))
    cmd = ["unshare", "--user", "--map-root-user", "--mount", "--net", "bash", "-c", wrapped]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
    err = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode != 0 and (
        "Operation not permitted" in err or "unshare:" in err or "Invalid argument" in err
    ):
        proc = subprocess.run(
            ["sudo", "-n", "unshare", "--mount", "--net", "bash", "-c", wrapped],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    return proc


def test_loaded_rules_keep_service_ports_and_foreign_tables() -> None:
    proc = _run_netns()
    if proc.returncode != 0:
        _fail(f"netns probe failed rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        _fail(f"probe did not return JSON ({exc}): {proc.stdout}\n{proc.stderr}")
    if report.get("error"):
        _fail(str(report["error"]))
    ports = [str(p) for p in _ports()]
    for phase in ("plain", "plain_again"):
        for port in ports:
            if report[phase][port] != "ok":
                _fail(f"{phase} tcp/{port} failed: {report[phase][port]}")
    if report["v6"] != "ok":
        _fail(f"IPv6 TCP was blocked: {report['v6']}")
    if report["counter_after_plain"] != report["counter_before"]:
        _fail("plain TCP incremented the drop counter")
    if report["counter_after_v6"] != report["counter_after_plain"]:
        _fail("IPv6 TCP incremented the drop counter")
    if report["counter_after_options"] <= report["counter_after_v6"]:
        _fail("IP-option TCP did not hit a drop counter")
    for port in ports:
        if report["options"][port] == "ok":
            _fail(f"IP-option TCP to {port} was accepted")
    tables = set(report["tables"])
    if "ufw_sentinel" not in tables:
        _fail(f"pre-existing table disappeared: {tables}")
    extra = tables - {"ufw_sentinel"}
    if len(extra) != 1:
        _fail(f"expected one mitigation table besides the sentinel, got {tables}")
    mitigation = extra.pop()
    log = report["systemctl_after_apply"]
    if log.count("enable cve-2026-98432.service") != 2:
        _fail(f"enable was not idempotent: {log}")
    if "start nftables.service" in log or "enable nftables.service" in log:
        _fail(f"distro nftables.service was started: {log}")
    if "reload nftables.service" in log or "restart nftables.service" in log:
        _fail(f"distro nftables.service was reloaded: {log}")
    if sum(1 for ln in log if ln == "daemon-reload") != 1:
        _fail(f"unit install should daemon-reload once: {log}")
    if log.count("start cve-2026-98432.service") != 1:
        _fail(f"first apply should start the unit once: {log}")
    if log.count("reload cve-2026-98432.service") != 1:
        _fail(f"second apply should reload the unit: {log}")
    if report["distro_flushes"] and log.count("disable nftables.service") != 1:
        _fail(f"flushing nftables.service stayed enabled: {log}")
    if report["ufw"] or report["apt"] or report["reboot"]:
        _fail(
            "apply touched ufw/apt/reboot: "
            f"ufw={report['ufw']!r} apt={report['apt']!r} reboot={report['reboot']!r}"
        )
    if not report["unit_wanted_by"]:
        _fail("persistence unit has no WantedBy=, so enable will not survive reboot")
    stop_tables = set(report["tables_after_stop"])
    if "ufw_sentinel" not in stop_tables or mitigation in stop_tables:
        _fail(f"stop removed the wrong tables: {stop_tables}")
    if report["options_after_stop"] != "ok" or report["plain_after_stop"] != "ok":
        _fail("service port did not recover after the unit stop")
    start_tables = set(report["tables_after_start"])
    if start_tables != {"ufw_sentinel", mitigation}:
        _fail(f"start disturbed foreign tables: {start_tables}")
    if report["options_after_start"] == "ok" or report["plain_after_start"] != "ok":
        _fail("start did not restore the drop without blocking plain TCP")
    reboot_tables = set(report["tables_after_reboot"])
    if reboot_tables != {mitigation}:
        _fail(f"boot ExecStart did not restore only the mitigation table: {reboot_tables}")
    if report["options_after_reboot"] == "ok" or report["plain_after_reboot"] != "ok":
        _fail("boot path did not restore the drop")
    if report["bad_rc"] == 0:
        _fail("a broken rules file was accepted")
    if set(report["tables_after_bad"]) != reboot_tables:
        _fail(f"rejected rules file changed live tables: {report['tables_after_bad']}")
    if report["options_after_bad"] == "ok":
        _fail("rejected rules file cleared the live drop")


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

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
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "deploy" / "provision.sh"
HELPER = ROOT / "deploy" / "cve-2026-98432.sh"
NGINX_SITE = ROOT / "deploy" / "nginx-site.sh"
PROBE = Path(__file__).resolve().parent / "cve_2026_98432_netns.py"
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



def _run_netns() -> subprocess.CompletedProcess[str]:
    for tool in ("unshare", "nft", "ip", "mount"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the netns mitigation probe")
    # sudo に落ちた Python は pytest を持たない。プローブは stdlib だけのファイルを起動する。
    wrapped = "\n".join((
        "set -euo pipefail",
        "mount -t tmpfs tmpfs /etc",
        "mkdir -p /etc/systemd/system",
        "printf ok > /etc/.cve-netns-probe",
        "ip link set lo up",
        "export CVE_NETNS_PROBE=1",
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(PROBE))}",
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
    ports = [str(port) for port in _ports()]
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

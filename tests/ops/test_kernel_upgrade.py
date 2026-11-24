"""linux-image-generic / linux-headers-generic の更新と、条件付きの再起動。

apt と shutdown をスタブにして、指名したパッケージ・再起動の有無・
nft ファイルが残ることを実行結果で見る。provision.sh の文言は見ない。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy" / "kernel-upgrade.sh"
PROVISION = ROOT / "deploy" / "provision.sh"
UPDATE = ROOT / "deploy" / "update.sh"
PACKAGES = ("linux-image-generic", "linux-headers-generic")


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _write_exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _World:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.log = root / "cmd.log"
        self.nft = root / "cve-2026-98432.nft"
        self.nft.write_text(
            "table inet cve_2026_98432 {\n\tchain input {\n\t\ttype filter hook input priority -310; policy accept;\n\t}\n}\n",
            encoding="utf-8",
        )
        self.jobs = root / "jobs"
        self.jobs.mkdir()
        self.proc = root / "proc"
        self.proc.mkdir()
        self.required = root / "reboot-required"
        self.state = root / "scheduled"
        self.cve_unit = root / "no-cve-unit.service"
        self.nft_hash = _sha(self.nft)
        self._apt(
            "#!/bin/sh\n"
            "printf 'apt %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
            "only=0\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    --only-upgrade) only=1 ;;\n"
            "    -*|install|Acquire::ForceIPv4=true) ;;\n"
            "    linux-image-generic|linux-headers-generic) ;;\n"
            "    *) echo \"rejected apt arg: $arg\" >&2; exit 99 ;;\n"
            "  esac\n"
            "done\n"
            "if [ \"$only\" -ne 1 ]; then\n"
            "  echo 'missing --only-upgrade' >&2\n"
            "  exit 99\n"
            "fi\n"
            "exit \"${KERNEL_TEST_APT_RC:-0}\"\n"
        )
        self._tool(
            "shutdown",
            "#!/bin/sh\n"
            "printf 'shutdown %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
            "if [ \"$*\" != \"-r +1\" ]; then\n"
            "  echo \"rejected shutdown: $*\" >&2\n"
            "  exit 99\n"
            "fi\n"
            "exit 0\n",
        )
        self._tool(
            "systemctl",
            "#!/bin/sh\n"
            "printf 'systemctl %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
            "case \"$1\" in\n"
            "  is-enabled) exit 0 ;;\n"
            "  enable) exit 0 ;;\n"
            "  *) echo \"rejected systemctl: $*\" >&2; exit 99 ;;\n"
            "esac\n",
        )
        for name in ("nft", "rm", "reboot", "poweroff"):
            self._tool(
                name,
                "#!/bin/sh\n"
                "printf 'unexpected %s %s\\n' \"$0\" \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
                "exit 99\n",
            )

    def _tool(self, name: str, body: str) -> None:
        _write_exe(self.bin_dir / name, body)

    def _apt(self, body: str) -> None:
        self._tool("apt-get", body)

    def env(self, **extra: str) -> dict[str, str]:
        merged = os.environ.copy()
        merged.update(
            {
                "APP_DIR": str(ROOT),
                "PATH": f"{self.bin_dir}{os.pathsep}{merged.get('PATH', '')}",
                "KERNEL_TEST_LOG": str(self.log),
                "KERNEL_APT_GET": str(self.bin_dir / "apt-get"),
                "KERNEL_SHUTDOWN": str(self.bin_dir / "shutdown"),
                "KERNEL_SYSTEMCTL": str(self.bin_dir / "systemctl"),
                "KERNEL_JOBS_DIR": str(self.jobs),
                "KERNEL_PROC_ROOT": str(self.proc),
                "KERNEL_REBOOT_REQUIRED": str(self.required),
                "KERNEL_SHUTDOWN_STATE": str(self.state),
                "KERNEL_CVE_UNIT": str(self.cve_unit),
                "DEBIAN_FRONTEND": "noninteractive",
                "KERNEL_TEST_APT_RC": "0",
            }
        )
        merged.update(extra)
        return merged

    def run(self, script: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=self.env(**extra),
            check=False,
        )

    def lines(self) -> list[str]:
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def assert_nft(self) -> None:
        if _sha(self.nft) != self.nft_hash:
            _fail("nft rules file changed")
        unexpected = [ln for ln in self.lines() if ln.startswith("unexpected ")]
        if unexpected:
            _fail(f"nft/rm/reboot was invoked: {unexpected}")


def _apt_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.startswith("apt ")]


def _assert_only_kernel_packages(lines: list[str]) -> None:
    installs = _apt_lines(lines)
    if not installs:
        _fail(f"apt was not invoked: {lines}")
    for ln in installs:
        tokens = ln.split()[1:]
        if "--only-upgrade" not in tokens or "install" not in tokens:
            _fail(f"install is not --only-upgrade: {ln}")
        if "upgrade" in tokens or "dist-upgrade" in tokens or "full-upgrade" in tokens:
            _fail(f"apt upgraded more than the kernel metapackages: {ln}")
        packages = [
            tok
            for tok in tokens
            if tok not in {"-o", "-y", "install", "--only-upgrade"}
            and not tok.startswith("-")
            and tok != "Acquire::ForceIPv4=true"
        ]
        if tuple(packages) != PACKAGES:
            _fail(f"package list is not the two kernel metapackages: {packages} from {ln}")


UPGRADE_AND_REBOOT = (
    "set -euo pipefail\n"
    "source \"$APP_DIR/deploy/kernel-upgrade.sh\"\n"
    "kernel_packages_upgrade\n"
    "kernel_maybe_reboot\n"
)


def test_upgrade_is_idempotent_and_does_not_reboot_without_flag(tmp_path: Path) -> None:
    world = _World(tmp_path)
    script = (
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/kernel-upgrade.sh\"\n"
        "kernel_packages_upgrade\n"
        "kernel_maybe_reboot\n"
        "kernel_packages_upgrade\n"
        "kernel_maybe_reboot\n"
    )
    proc = world.run(script)
    if proc.returncode != 0:
        _fail(f"upgrade failed:\n{proc.stdout}\n{proc.stderr}\n{world.lines()}")
    _assert_only_kernel_packages(world.lines())
    if len(_apt_lines(world.lines())) != 2:
        _fail(f"second deploy did not repeat only-upgrade: {world.lines()}")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"reboot ran without reboot-required: {world.lines()}")
    if proc.stdout.count("reboot not required") != 2:
        _fail(f"missing reboot not required:\n{proc.stdout}")
    world.assert_nft()


def test_reboot_required_schedules_shutdown_once(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0:
        _fail(f"reboot path failed:\n{proc.stdout}\n{proc.stderr}\n{world.lines()}")
    _assert_only_kernel_packages(world.lines())
    shutdowns = [ln for ln in world.lines() if ln.startswith("shutdown ")]
    if shutdowns != ["shutdown -r +1"]:
        _fail(f"expected one delayed reboot: {world.lines()}")
    if "reboot scheduled: shutdown -r +1" not in proc.stdout.splitlines():
        _fail(f"missing schedule log:\n{proc.stdout}")
    world.assert_nft()

    world.state.write_text("scheduled\n", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0:
        _fail(f"second reboot path failed:\n{proc.stdout}\n{proc.stderr}")
    shutdowns = [ln for ln in world.lines() if ln.startswith("shutdown ")]
    if shutdowns != ["shutdown -r +1"]:
        _fail(f"second deploy scheduled another reboot: {world.lines()}")
    if "reboot already scheduled" not in proc.stdout.splitlines():
        _fail(f"missing already-scheduled log:\n{proc.stdout}")
    world.assert_nft()


def test_apt_failure_does_not_reboot(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT, KERNEL_TEST_APT_RC="1")
    if proc.returncode == 0:
        _fail("failed kernel upgrade must not look successful")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"reboot followed a failed upgrade: {world.lines()}")
    world.assert_nft()


def test_live_job_pid_and_cron_block_reboot(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    job = world.jobs / "backfill-20261107-0840"
    job.mkdir()
    (job / "pid").write_text("4242\n", encoding="utf-8")
    (world.proc / "4242").mkdir()
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0:
        _fail(f"pending path failed:\n{proc.stdout}\n{proc.stderr}")
    if "reboot pending" not in proc.stdout.splitlines():
        _fail(f"live job pid must log reboot pending:\n{proc.stdout}")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"live job was rebooted: {world.lines()}")
    _assert_only_kernel_packages(world.lines())
    world.assert_nft()

    (job / "pid").write_text("not-a-pid\n", encoding="utf-8")
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or "shutdown -r +1" not in world.lines():
        _fail(f"garbage pid must not block reboot: {proc.stdout!r} {world.lines()}")

    (job / "pid").write_text("1\n", encoding="utf-8")
    (world.proc / "1").mkdir()
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or world.lines().count("shutdown -r +1") != 1:
        _fail(f"pid 1 must not block reboot: {proc.stdout!r} {world.lines()}")

    (job / "pid").unlink()
    cron = world.proc / "77"
    cron.mkdir()
    (cron / "cmdline").write_bytes(
        b"bash\0/home/stock/stock-analyzer/deploy/cron-run.sh\0jobs/market/run_batch.py\0"
    )
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or "reboot pending" not in proc.stdout.splitlines():
        _fail(f"cron-run.sh must block reboot:\n{proc.stdout}\n{world.lines()}")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"cron job was rebooted: {world.lines()}")

    (cron / "cmdline").write_bytes(b"grep\0-F\0cron-run.sh\0")
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or "shutdown -r +1" not in world.lines():
        _fail(f"a search for the wrapper name must not block reboot: {proc.stdout!r} {world.lines()}")

    migrate = world.proc / "88"
    migrate.mkdir()
    (migrate / "cmdline").write_bytes(b"python\0jobs/migrate.py\0")
    (cron / "cmdline").write_bytes(b"python\0-c\0print('cron-run.sh')\0")
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or world.lines().count("shutdown -r +1") != 1:
        _fail(f"migrate must not be treated as a cron job: {proc.stdout!r} {world.lines()}")
    world.assert_nft()


def test_dead_pid_file_does_not_block(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    job = world.jobs / "backfill-done"
    job.mkdir()
    (job / "pid").write_text("5150\n", encoding="utf-8")
    (job / "exit").write_text("0\n", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0:
        _fail(f"dead pid path failed:\n{proc.stdout}\n{proc.stderr}")
    if "reboot pending" in proc.stdout.splitlines():
        _fail("a dead pid file blocked reboot")
    if "shutdown -r +1" not in world.lines():
        _fail(f"dead pid file must still reboot: {world.lines()}")
    world.assert_nft()


def test_process_cwd_under_jobs_blocks_reboot(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    worker = world.proc / "90"
    worker.mkdir()
    (worker / "cmdline").write_bytes(b"python\0backfill.py\0")
    (worker / "cwd").write_text(str(world.jobs / "backfill-20261107-0840") + "\n", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or "reboot pending" not in proc.stdout.splitlines():
        _fail(f"cwd under ~/jobs must block reboot:\n{proc.stdout}")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"job cwd was rebooted: {world.lines()}")

    (worker / "cwd").write_text("/tmp\n", encoding="utf-8")
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT)
    if proc.returncode != 0 or "shutdown -r +1" not in world.lines():
        _fail(f"unrelated cwd must reboot: {proc.stdout!r} {world.lines()}")
    world.assert_nft()


def test_real_job_pid_blocks_until_the_process_exits(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    job = world.jobs / "manual-20261107-0900"
    job.mkdir()
    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        (job / "pid").write_text(f"{sleeper.pid}\n", encoding="utf-8")
        proc = world.run(UPGRADE_AND_REBOOT, KERNEL_PROC_ROOT="")
        if proc.returncode != 0:
            _fail(f"live pid probe failed:\n{proc.stdout}\n{proc.stderr}")
        if "reboot pending" not in proc.stdout.splitlines():
            _fail(f"a live ~/jobs pid must block reboot:\n{proc.stdout}")
        if any(ln.startswith("shutdown ") for ln in world.lines()):
            _fail(f"live process was rebooted: {world.lines()}")
    finally:
        sleeper.kill()
        sleeper.wait(timeout=5)
    world.log.write_text("", encoding="utf-8")
    proc = world.run(UPGRADE_AND_REBOOT, KERNEL_PROC_ROOT="")
    if proc.returncode != 0 or "shutdown -r +1" not in world.lines():
        _fail(f"exited job must allow reboot: rc={proc.returncode} {proc.stdout!r} {world.lines()}")
    world.assert_nft()


def test_services_are_enabled_before_shutdown(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    world._tool(
        "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
        "case \"$1\" in\n"
        "  is-enabled) exit 1 ;;\n"
        "  enable) exit 0 ;;\n"
        "  *) echo \"rejected systemctl: $*\" >&2; exit 99 ;;\n"
        "esac\n",
    )
    proc = world.run(
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/kernel-upgrade.sh\"\n"
        "kernel_maybe_reboot\n"
    )
    if proc.returncode != 0:
        _fail(f"enable path failed:\n{proc.stdout}\n{proc.stderr}\n{world.lines()}")
    lines = world.lines()
    for unit in ("stock-analyzer", "stock-analyzer-mcp"):
        enable = f"systemctl enable {unit}"
        if enable not in lines:
            _fail(f"missing {enable}: {lines}")
        if lines.index(enable) > lines.index("shutdown -r +1"):
            _fail(f"{unit} was enabled after shutdown: {lines}")
    for banned in ("stop", "restart", "reload", "daemon-reload"):
        if any(banned in ln for ln in lines):
            _fail(f"systemctl {banned} during reboot: {lines}")
    world.assert_nft()


def test_enable_failure_does_not_reboot(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    world._tool(
        "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
        "exit 1\n",
    )
    proc = world.run(
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/kernel-upgrade.sh\"\n"
        "kernel_maybe_reboot\n"
    )
    if proc.returncode == 0:
        _fail("disabled units that cannot be enabled must not reboot")
    if any(ln.startswith("shutdown ") for ln in world.lines()):
        _fail(f"shutdown ran without enabled units: {world.lines()}")
    world.assert_nft()


def test_cve_unit_is_enabled_without_touching_nft(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    world.cve_unit.write_text("[Unit]\nDescription=probe\n", encoding="utf-8")
    world._tool(
        "systemctl",
        "#!/bin/sh\n"
        "printf 'systemctl %s\\n' \"$*\" >> \"$KERNEL_TEST_LOG\"\n"
        "last=\n"
        "for arg in \"$@\"; do last=\"$arg\"; done\n"
        "case \"$1\" in\n"
        "  is-enabled)\n"
        "    if [ \"$last\" = cve-2026-98432.service ]; then exit 1; fi\n"
        "    exit 0 ;;\n"
        "  enable) exit 0 ;;\n"
        "  *) echo \"rejected systemctl: $*\" >&2; exit 99 ;;\n"
        "esac\n",
    )
    proc = world.run(
        "set -euo pipefail\n"
        "source \"$APP_DIR/deploy/kernel-upgrade.sh\"\n"
        "kernel_maybe_reboot\n"
    )
    if proc.returncode != 0:
        _fail(f"cve enable failed:\n{proc.stdout}\n{proc.stderr}\n{world.lines()}")
    if "systemctl enable cve-2026-98432.service" not in world.lines():
        _fail(f"cve unit was not enabled: {world.lines()}")
    enabled_apps = [
        ln
        for ln in world.lines()
        if ln.startswith("systemctl enable ") and "stock-analyzer" in ln
    ]
    if enabled_apps:
        _fail(f"already-enabled app unit was enabled again: {world.lines()}")
    world.assert_nft()


def test_reboot_only_refuses_non_root(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(PROVISION), "--kernel-reboot-only"],
        capture_output=True,
        text=True,
        env=world.env(),
        check=False,
    )
    if proc.returncode == 0:
        _fail("non-root provision must fail")
    if any(ln.startswith(("shutdown ", "apt ")) for ln in world.lines()):
        _fail(f"non-root path touched apt or shutdown: {world.lines()}")
    world.assert_nft()


def test_provision_reboot_only_schedules_shutdown_without_apt(tmp_path: Path) -> None:
    if shutil.which("unshare") is None:
        pytest.skip("unshare is required")
    world = _World(tmp_path)
    world.required.write_text("*** System restart required ***\n", encoding="utf-8")
    proc = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "bash",
            str(PROVISION),
            "--kernel-reboot-only",
        ],
        capture_output=True,
        text=True,
        env=world.env(),
        check=False,
    )
    err = (proc.stderr or "") + (proc.stdout or "")
    if proc.returncode != 0 and (
        "Operation not permitted" in err or "Invalid argument" in err or "unshare:" in err
    ):
        pytest.skip("user namespace unavailable")
    if proc.returncode != 0:
        _fail(f"reboot-only entry failed:\n{proc.stdout}\n{proc.stderr}\n{world.lines()}")
    if "reboot scheduled: shutdown -r +1" not in proc.stdout.splitlines():
        _fail(f"reboot-only did not schedule shutdown:\n{proc.stdout}")
    if any(ln.startswith("apt ") for ln in world.lines()):
        _fail(f"reboot-only upgraded packages: {world.lines()}")
    if world.lines().count("shutdown -r +1") != 1:
        _fail(f"reboot-only shutdown count: {world.lines()}")
    world.assert_nft()

    world.state.write_text("scheduled\n", encoding="utf-8")
    proc = subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "bash",
            str(PROVISION),
            "--kernel-reboot-only",
        ],
        capture_output=True,
        text=True,
        env=world.env(),
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"second reboot-only failed:\n{proc.stdout}\n{proc.stderr}")
    if "reboot already scheduled" not in proc.stdout.splitlines():
        _fail(f"second reboot-only was not a no-op:\n{proc.stdout}")
    if world.lines().count("shutdown -r +1") != 1:
        _fail(f"second reboot-only called shutdown again: {world.lines()}")
    world.assert_nft()


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
        _fail(f"git {' '.join(args)} failed: {proc.stderr!r} {proc.stdout!r}")


def _prepare_update(tmp_path: Path, *, health_ok: bool) -> tuple[dict[str, str], Path]:
    app = tmp_path / "app"
    home = tmp_path / "home"
    origin = tmp_path / "origin.git"
    bin_dir = tmp_path / "bin"
    trace = tmp_path / "trace.log"
    app.mkdir()
    home.mkdir()
    bin_dir.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["APP_DIR"] = str(app)
    env["DEPLOY_LOCK"] = str(tmp_path / "deploy.lock")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["TRACE"] = str(trace)
    env["HEALTH_FAIL"] = "0" if health_ok else "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    _write_exe(
        bin_dir / "sudo",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --preserve-env=*|--no-pager|--full) shift ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        "printf '%s\\n' \"$*\" >> \"$TRACE\"\n"
        "if [[ \"${1:-}\" == systemctl ]]; then\n"
        "  if [[ \"${2:-}\" == is-active ]]; then echo active; fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ -n \"${1:-}\" && -x \"$1\" ]]; then\n"
        "  exec \"$1\" \"${@:2}\"\n"
        "fi\n"
        "exit 0\n",
    )
    _write_exe(
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
        "if [[ \"${HEALTH_FAIL:-}\" == 1 && \"$url\" == *127.0.0.1:8000* ]]; then\n"
        "  exit 1\n"
        "fi\n"
        "code=200\n"
        "case \"$url\" in\n"
        "  *8010/mcp*) code=401 ;;\n"
        "esac\n"
        "if [[ \"$out\" == *http_code* ]]; then printf '%s' \"$code\"; fi\n"
        "exit 0\n",
    )
    _write_exe(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    (app / "jobs").mkdir(parents=True)
    (app / "deploy").mkdir()
    (app / "requirements.txt").write_text("# test\n", encoding="utf-8")
    (app / "jobs" / "migrate.py").write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['TRACE']).open('a').write('migrate\\n')\n",
        encoding="utf-8",
    )
    (app / "deploy" / "provision.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    (app / "deploy" / "provision.sh").chmod(0o755)
    shutil.copy2(ROOT / "deploy" / "write_rclone_conf.py", app / "deploy" / "write_rclone_conf.py")
    _git(app, env, "init", "-b", "main")
    _git(app, env, "config", "user.email", "deploy-test@example.com")
    _git(app, env, "config", "user.name", "deploy-test")
    _git(app, env, "add", "requirements.txt", "jobs/migrate.py", "deploy")
    _git(app, env, "commit", "-m", "seed")
    origin.mkdir()
    _git(origin, env, "init", "--bare", "-b", "main")
    _git(app, env, "remote", "add", "origin", str(origin))
    _git(app, env, "push", "-u", "origin", "main")
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700)
    key = ssh / "id_github"
    key.write_text("dummy-private\n", encoding="utf-8")
    (ssh / "id_github.pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIdummy stock@test\n",
        encoding="utf-8",
    )
    key.chmod(0o600)
    (ssh / "id_github.pub").chmod(0o644)
    return env, trace


def _run_update(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["bash", str(UPDATE)],
            cwd=env["APP_DIR"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired as exc:
        _fail(f"update.sh timed out: stdout={exc.stdout!r} stderr={exc.stderr!r}")


def _trace_indexes(trace: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for index, line in enumerate(trace.read_text(encoding="utf-8").splitlines()):
        if line.endswith(("provision.sh --sudoers-only", "provision.sh --sudoers-only ")):
            found.setdefault("sudoers", index)
        if line.endswith("--defer-reboot"):
            found.setdefault("defer", index)
        if line == "migrate":
            found.setdefault("migrate", index)
        if line == "systemctl restart stock-analyzer":
            found.setdefault("restart", index)
        if line.endswith("--kernel-reboot-only"):
            found.setdefault("reboot", index)
    return found


def test_update_reboots_only_after_health(tmp_path: Path) -> None:
    env, trace = _prepare_update(tmp_path, health_ok=True)
    proc = _run_update(env)
    if proc.returncode != 0:
        _fail(f"update.sh failed: rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    found = _trace_indexes(trace)
    for key in ("sudoers", "defer", "migrate", "restart", "reboot"):
        if key not in found:
            _fail(f"trace missing {key}: {trace.read_text()!r}")
    if not (found["sudoers"] < found["defer"] < found["migrate"] < found["restart"] < found["reboot"]):
        _fail(f"reboot was not after health: {found} {trace.read_text()!r}")
    text = trace.read_text(encoding="utf-8")
    if "--kernel-reboot-only" in text.split("--defer-reboot", 1)[0]:
        _fail(f"reboot-only ran before the deferred provision:\n{text}")


def test_update_skips_reboot_when_health_fails(tmp_path: Path) -> None:
    env, trace = _prepare_update(tmp_path, health_ok=False)
    proc = _run_update(env)
    if proc.returncode == 0:
        _fail("failed health check must fail the deploy")
    text = trace.read_text(encoding="utf-8")
    if "--defer-reboot" not in text:
        _fail(f"provision was not deferred:\n{text}")
    if "--kernel-reboot-only" in text:
        _fail(f"failed health still scheduled a reboot:\n{text}")
    if "migrate" not in text.splitlines():
        _fail(f"health failed before migrate, so this did not reach the reboot decision:\n{text}")

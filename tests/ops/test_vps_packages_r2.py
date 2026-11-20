"""vps_packages_r2 の出力形。ネットワークと本番 mount には出ない。"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.share_paths import share_path
from jobs.ops.vps_packages_r2 import (
    DEFAULT_DEST,
    LATEST_NAME,
    NOT_INSTALLED,
    REQUIRED_DPKG,
    SECTION_DPKG,
    SECTION_OS_RELEASE,
    SECTION_PIP,
    SECTION_RCLONE,
    SECTION_UNAME,
    build_report,
    collect_report,
    history_name,
    main,
    run,
    sanitize_output,
    with_required_packages,
    write_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
WHEN = datetime(2026, 10, 8, 6, 40, 0, tzinfo=JST)
CANARY = "super-secret-canary-value-do-not-leak"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _dpkg_all() -> str:
    return "\n".join(f"{name}\t1.0-1" for name in REQUIRED_DPKG)


def _os_release(path: Path) -> Path:
    path.write_text(
        'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
        'NAME="Debian GNU/Linux"\n'
        "VERSION_ID=\"12\"\n"
        "ID=debian\n",
        encoding="utf-8",
    )
    return path


def _fake_run(argv: list[str]) -> str:
    joined = " ".join(argv)
    if "-m pip freeze" in joined:
        return "fastapi==0.115.0\nsqlalchemy==2.0.36"
    if argv[-1] == "-W" or "dpkg-query" in joined:
        return _dpkg_all()
    if argv[-1] == "-r" or joined.endswith("uname -r"):
        return "6.8.0-31-generic"
    if "rclone" in joined:
        return "rclone v1.68.2\n- os/version: debian 12 (64 bit)"
    _fail(f"unexpected command {argv!r}")
    return NOT_INSTALLED


def _which(name: str) -> str | None:
    return {
        "dpkg-query": "/usr/bin/dpkg-query",
        "uname": "/usr/bin/uname",
        "rclone": "/usr/bin/rclone",
    }.get(name)


def test_default_dest() -> None:
    if DEFAULT_DEST != str(share_path("inventory")):
        _fail(f"DEFAULT_DEST {DEFAULT_DEST!r}")
    if "data/inventory" not in DEFAULT_DEST:
        _fail(f"inventory dest not under data: {DEFAULT_DEST!r}")
    if LATEST_NAME != "vps-packages.txt":
        _fail(f"LATEST_NAME {LATEST_NAME!r}")


def test_report_sections() -> None:
    text = build_report(
        generated_at="2026-10-08 06:40:00 JST",
        hostname="vps.example",
        pip_freeze="fastapi==0.115.0",
        dpkg=_dpkg_all(),
        uname="6.8.0-31-generic",
        os_release='NAME="Debian GNU/Linux"',
        rclone="rclone v1.68.2",
    )
    for section in (SECTION_PIP, SECTION_DPKG, SECTION_UNAME, SECTION_OS_RELEASE, SECTION_RCLONE):
        if f"=== {section} ===" not in text:
            _fail(f"missing section {section!r}\n{text}")
    if not text.startswith("generated-at: 2026-10-08 06:40:00 JST\nhostname: vps.example\n"):
        _fail(f"header {text[:80]!r}")
    for name in REQUIRED_DPKG:
        if name not in text:
            _fail(f"required package {name} missing")


def test_missing_command_is_not_installed(tmp_path: Path) -> None:
    def which(name: str) -> str | None:
        if name == "rclone":
            return None
        return _which(name)

    os_rel = _os_release(tmp_path / "os-release")
    venv_py = tmp_path / ".venv" / "bin" / "python3"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    text = collect_report(
        WHEN,
        "host1",
        repo_root=tmp_path,
        os_release_path=os_rel,
        run=_fake_run,
        which=which,
    )
    rclone_block = text.split(f"=== {SECTION_RCLONE} ===", 1)[1]
    first = rclone_block.strip().splitlines()[0]
    if first != NOT_INSTALLED:
        _fail(f"rclone missing should be not installed, got {first!r}")
    if CANARY in text:
        _fail("canary leaked into missing-command report")


def test_required_packages_appended_when_absent() -> None:
    got = with_required_packages("bash\t5.2\ncoreutils\t9.1\n")
    for name in REQUIRED_DPKG:
        if f"{name}\t{NOT_INSTALLED}" not in got:
            _fail(f"{name} should be marked not installed\n{got}")
    if "bash\t5.2" not in got:
        _fail("existing packages must stay")


def test_sanitize_redacts_secrets_keeps_packages() -> None:
    raw = (
        "SecretStorage==3.3.3\n"
        "-e git+https://user:hunter2@github.com/org/repo.git@abc#egg=pkg\n"
        "JQUANTS_API_KEY=should-not-appear\n"
        "fastapi==0.115.0\n"
    )
    out = sanitize_output(raw)
    if "hunter2" in out or "should-not-appear" in out or "JQUANTS_API_KEY=" in out:
        _fail(f"secret leaked: {out!r}")
    if "SecretStorage==3.3.3" not in out or "fastapi==0.115.0" not in out:
        _fail(f"packages dropped: {out!r}")
    if "://<redacted>@" not in out:
        _fail(f"credential URL not redacted: {out!r}")


def test_no_env_leak(tmp_path: Path) -> None:
    keys = ("JQUANTS_API_KEY", "R2_SECRET_ACCESS_KEY", "MCP_TOKEN")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ[key] = CANARY
        os_rel = _os_release(tmp_path / "os-release")
        venv_py = tmp_path / ".venv" / "bin" / "python3"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("", encoding="utf-8")
        text = collect_report(
            WHEN,
            "host1",
            repo_root=tmp_path,
            os_release_path=os_rel,
            run=_fake_run,
            which=_which,
        )
    finally:
        for key, old in previous.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    if CANARY in text:
        _fail("environment canary leaked into inventory")
    if "JQUANTS_API_KEY" in text or "R2_SECRET" in text or "MCP_TOKEN" in text:
        _fail(f"secret name leaked: {text}")
    if "VERSION_ID=" not in text:
        _fail("os-release VERSION_ID should remain")


def test_write_latest_and_history(tmp_path: Path) -> None:
    dest = tmp_path / "inventory"
    os_rel = _os_release(tmp_path / "os-release")
    venv_py = tmp_path / ".venv" / "bin" / "python3"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    rc = run(
        dest,
        now=WHEN,
        hostname="vps.example",
        repo_root=tmp_path,
        os_release_path=os_rel,
        run_cmd_fn=_fake_run,
        which=_which,
    )
    if rc != 0:
        _fail(f"run exit {rc}")
    latest = dest / LATEST_NAME
    history = dest / "history" / history_name(WHEN)
    if not latest.is_file() or not history.is_file():
        _fail(f"missing files: {list(dest.rglob('*'))}")
    body = latest.read_text(encoding="utf-8")
    if body != history.read_text(encoding="utf-8"):
        _fail("latest and history must match")
    if "generated-at: 2026-10-08 06:40:00 JST" not in body:
        _fail(body[:200])
    if "hostname: vps.example" not in body:
        _fail(body[:200])
    for name in REQUIRED_DPKG:
        if name not in body:
            _fail(f"{name} missing from written file")
    if list(dest.rglob("*.part")):
        _fail("leftover .part files")
    rc2 = run(
        dest,
        now=WHEN.replace(minute=41),
        hostname="vps.example",
        repo_root=tmp_path,
        os_release_path=os_rel,
        run_cmd_fn=_fake_run,
        which=_which,
    )
    if rc2 != 0:
        _fail(f"second run exit {rc2}")
    body2 = latest.read_text(encoding="utf-8")
    if "06:41:00 JST" not in body2:
        _fail("latest was not overwritten")
    dated = list((dest / "history").glob("vps-packages-20261008.txt"))
    if len(dated) != 1:
        _fail(f"same-day history should stay one file: {dated}")


def test_write_inventory_bytes() -> None:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        dest = Path(d)
        latest, history = write_inventory(dest, "hello\n", WHEN)
        if latest.read_bytes() != b"hello\n":
            _fail(latest.read_bytes())
        if history.name != "vps-packages-20261008.txt":
            _fail(history.name)


def test_main_write_failure_is_nonzero(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    rc = main(["--dest", str(blocker / "inventory")])
    if rc == 0:
        _fail("write failure must be non-zero")


def main_tests() -> None:
    from tempfile import TemporaryDirectory

    test_default_dest()
    test_report_sections()
    test_required_packages_appended_when_absent()
    test_sanitize_redacts_secrets_keeps_packages()
    with TemporaryDirectory() as d:
        test_missing_command_is_not_installed(Path(d))
    with TemporaryDirectory() as d:
        test_no_env_leak(Path(d))
    with TemporaryDirectory() as d:
        test_write_latest_and_history(Path(d))
    test_write_inventory_bytes()
    with TemporaryDirectory() as d:
        test_main_write_failure_is_nonzero(Path(d))
    print("ok: vps packages r2")


if __name__ == "__main__":
    main_tests()

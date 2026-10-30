"""uvicorn ワーカー数は nproc と MemAvailable。決め打ちせず UVICORN_WORKERS で上書き。"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))

from uvicorn_workers import (
    PER_WORKER_MIB,
    choose_workers,
    mem_available_kib,
    parse_env_workers,
    workers_from_host,
)


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_choose_workers_formula() -> None:
    if choose_workers(env_value="4", nproc=2, mem_available_kib=256 * 1024) != 4:
        _fail("UVICORN_WORKERS must win over nproc and memory")
    if choose_workers(env_value=" 2 ", nproc=8, mem_available_kib=64 * 1024) != 2:
        _fail("UVICORN_WORKERS may have surrounding space")
    if choose_workers(env_value=None, nproc=4, mem_available_kib=4 * 384 * 1024) != 4:
        _fail("N must follow nproc when memory is enough")
    if choose_workers(env_value=None, nproc=8, mem_available_kib=384 * 1024) != 1:
        _fail("N must follow memory when RAM is tighter than cores")
    if choose_workers(env_value=None, nproc=3, mem_available_kib=None) != 3:
        _fail("unknown memory must not invent a cap; use nproc")
    if choose_workers(env_value="", nproc=2, mem_available_kib=10 * 1024 * 1024) != 2:
        _fail("empty UVICORN_WORKERS is unset")
    got = choose_workers(
        env_value=None, nproc=16, mem_available_kib=2 * PER_WORKER_MIB * 1024,
    )
    if got != 2:
        _fail(f"expected 2 from 2*PER_WORKER_MIB available, got {got}")


def test_parse_env_workers_rejects_junk() -> None:
    if parse_env_workers(None) is not None or parse_env_workers("  ") is not None:
        _fail("blank env must be unset")
    try:
        parse_env_workers("0")
    except ValueError:
        pass
    else:
        _fail("UVICORN_WORKERS=0 must raise")
    try:
        parse_env_workers("abc")
    except ValueError:
        pass
    else:
        _fail("UVICORN_WORKERS=abc must raise")


def test_mem_available_kib_prefers_available() -> None:
    text = "MemTotal: 2048000 kB\nMemFree: 1000 kB\nMemAvailable: 400000 kB\n"
    if mem_available_kib(text) != 400000:
        _fail("MemAvailable must win")
    if mem_available_kib("MemFree: 9 kB\n") != 9:
        _fail("MemFree is fallback")
    if mem_available_kib("") is not None:
        _fail("empty meminfo has no value")


def test_workers_from_host_reads_env_and_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 384000 kB\n", encoding="utf-8")
    got = workers_from_host({"UVICORN_WORKERS": "5"}, meminfo_path=meminfo)
    if got != 5:
        _fail(f"env override, got {got}")
    got = workers_from_host({}, meminfo_path=meminfo)
    if got < 1:
        _fail(f"host formula must be >= 1, got {got}")


def test_service_and_update_contracts() -> None:
    service = (ROOT / "deploy" / "stock-analyzer.service").read_text(encoding="utf-8")
    start = (ROOT / "deploy" / "uvicorn-start.sh").read_text(encoding="utf-8")
    workers_py = (ROOT / "deploy" / "uvicorn_workers.py").read_text(encoding="utf-8")
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    migrate = (ROOT / "jobs" / "migrate.py").read_text(encoding="utf-8")
    if "--workers 1" in service:
        _fail("stock-analyzer.service must not hardcode --workers 1")
    if "uvicorn-start.sh" not in service:
        _fail("stock-analyzer.service must start via uvicorn-start.sh")
    def _active(text: str) -> str:
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    if "--preload" in _active(service) or "--preload" in _active(start) or re.search(
        r"(^|[^\s#])--preload(\s|$)", update, flags=re.M,
    ):
        _fail("must not use uvicorn --preload")
    if "stock-analyzer-mcp.service" in start:
        _fail("MCP service is out of scope")
    if "UVICORN_WORKERS" not in workers_py:
        _fail("uvicorn_workers.py must honor UVICORN_WORKERS")
    if "sched_getaffinity" not in workers_py:
        _fail("uvicorn_workers.py must read host CPU (sched_getaffinity / nproc)")
    if "MemAvailable" not in workers_py:
        _fail("uvicorn_workers.py must look at MemAvailable")
    if "lru_cache" not in start:
        _fail("uvicorn-start.sh must note process-local lru_cache")
    if "init_db(strict=True)" not in migrate:
        _fail("jobs/migrate.py must finish with init_db(strict=True) before workers start")
    mig_at = update.find('"$venv_py" jobs/migrate.py')
    restart_at = update.find("systemctl restart stock-analyzer")
    if mig_at < 0 or restart_at < 0 or mig_at > restart_at:
        _fail("update.sh must run jobs/migrate.py before restarting stock-analyzer")
    monthly = (ROOT / "app" / "api" / "monthly" / "_common.py").read_text(encoding="utf-8")
    chain = (ROOT / "app" / "supply" / "chain.py").read_text(encoding="utf-8")
    if "lru_cache" not in monthly or "lru_cache" not in chain:
        _fail("expected process-local functools.lru_cache")
    mcp = (ROOT / "deploy" / "stock-analyzer-mcp.service").read_text(encoding="utf-8")
    if "uvicorn-start.sh" in mcp or "--workers" in mcp:
        _fail("MCP service must stay a single process")


def test_uvicorn_start_passes_workers(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / "deploy").mkdir()
    shutil.copy2(ROOT / "deploy" / "uvicorn_workers.py", app / "deploy" / "uvicorn_workers.py")
    shutil.copy2(ROOT / "deploy" / "uvicorn-start.sh", app / "deploy" / "uvicorn-start.sh")
    real_py = shutil.which("python3")
    if not real_py:
        _fail("python3 not on PATH")
    (app / ".venv" / "bin" / "python3").symlink_to(real_py)
    argv_file = tmp_path / "argv.txt"
    _write_exec(
        app / ".venv" / "bin" / "uvicorn",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {argv_file}\n",
    )
    env = os.environ.copy()
    env["APP_DIR"] = str(app)
    env["UVICORN_WORKERS"] = "3"
    proc = subprocess.run(
        ["bash", str(app / "deploy" / "uvicorn-start.sh")],
        cwd=str(app),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        _fail(
            "uvicorn-start.sh failed: "
            f"rc={proc.returncode} stderr={proc.stderr!r} stdout={proc.stdout!r}"
        )
    if "uvicorn workers=3" not in proc.stdout:
        _fail(f"expected workers log, stdout={proc.stdout!r}")
    argv = argv_file.read_text(encoding="utf-8").split()
    try:
        n = argv[argv.index("--workers") + 1]
    except (ValueError, IndexError):
        _fail(f"uvicorn argv missing --workers: {argv!r}")
    if n != "3":
        _fail(f"expected --workers 3, got {argv!r}")
    if "--preload" in argv:
        _fail(f"must not pass --preload: {argv!r}")


def main() -> None:
    import tempfile

    test_choose_workers_formula()
    test_parse_env_workers_rejects_junk()
    test_mem_available_kib_prefers_available()
    with tempfile.TemporaryDirectory(prefix="uvicorn-workers-") as raw:
        test_workers_from_host_reads_env_and_meminfo(Path(raw))
        test_uvicorn_start_passes_workers(Path(raw))
    test_service_and_update_contracts()
    print("ok: uvicorn workers")


if __name__ == "__main__":
    main()

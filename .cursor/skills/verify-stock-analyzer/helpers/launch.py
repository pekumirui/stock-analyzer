"""Start or adopt uvicorn on VERIFY_PORT (default 8000)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SKILL = Path(__file__).resolve().parents[1]
RUN = SKILL / ".run"
DEFAULT_PORT = 8000


def _venv_python() -> Path:
    unix = ROOT / ".venv" / "bin" / "python"
    win = ROOT / ".venv" / "Scripts" / "python.exe"
    if sys.platform == "win32":
        if win.is_file():
            return win
        if unix.is_file():
            return unix
    else:
        if unix.is_file():
            return unix
        if win.is_file():
            return win
    raise SystemExit("missing .venv; run .\\setup.ps1 (Windows) or ./setup.sh (Linux/mac)")


def _port() -> int:
    return int(os.environ.get("VERIFY_PORT", str(DEFAULT_PORT)))


def _listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError:
        return 0, b""


def _spa_ready(url: str) -> bool:
    status, body = _get(url + "/app")
    return status == 200 and "株式分析ツール".encode("utf-8") in body


def _write_state(pid: int | None, port: int, url: str, adopted: bool) -> dict:
    RUN.mkdir(parents=True, exist_ok=True)
    state = {
        "pid": pid,
        "port": port,
        "url": url,
        "adopted": adopted,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "db": str(ROOT / "stock.db"),
    }
    (RUN / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def main() -> None:
    port = _port()
    url = f"http://127.0.0.1:{port}"

    if _listening(port):
        if not _spa_ready(url):
            raise SystemExit(
                f"127.0.0.1:{port} is in use but GET /app is not the SPA; "
                "stop that process or set VERIFY_PORT"
            )
        state = _write_state(pid=None, port=port, url=url, adopted=True)
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return

    python = _venv_python()
    RUN.mkdir(parents=True, exist_ok=True)
    log = RUN / "uvicorn.log"
    popen_kw: dict = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    logf = log.open("w", encoding="utf-8")
    popen_kw["stdout"] = logf
    if sys.platform == "win32":
        popen_kw["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | 0x00000008  # DETACHED_PROCESS
            | 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
            | 0x08000000  # CREATE_NO_WINDOW
        )
    else:
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(
        [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        **popen_kw,
    )
    deadline = time.time() + 30
    status = 0
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"uvicorn exited {proc.returncode}; see {log}")
        if _spa_ready(url):
            break
        time.sleep(0.25)
    else:
        proc.kill()
        raise SystemExit(f"GET {url}/app never became ready (last status={status})")

    state = _write_state(pid=proc.pid, port=port, url=url, adopted=False)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    if os.environ.get("VERIFY_WAIT", "1") not in ("0", "false", "no"):
        proc.wait()


if __name__ == "__main__":
    main()

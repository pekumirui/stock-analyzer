"""Stop only the uvicorn pid recorded by launch.py. Keep artifacts/."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
RUN = SKILL / ".run"
STATE = RUN / "state.json"


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            check=False,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def main() -> None:
    if not STATE.is_file():
        print("nothing to clean (no .run/state.json)")
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    pid = int(state["pid"])
    _kill(pid)
    shutil.rmtree(RUN, ignore_errors=True)
    print(json.dumps({"killed_pid": pid, "run_dir_removed": True, "artifacts_kept": True}))


if __name__ == "__main__":
    main()

"""Read-only: is the verify instance worth driving?"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
STATE = SKILL / ".run" / "state.json"


def _get(url: str) -> tuple[int, object | str]:
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            raw = r.read()
            ctype = r.headers.get("content-type", "")
            if "json" in ctype:
                return r.status, json.loads(raw.decode("utf-8"))
            return r.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:400]
    except OSError as e:
        return 0, str(e)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main() -> None:
    if not STATE.is_file():
        raise SystemExit("no .run/state.json — run launch.py first")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    url = state["url"].rstrip("/")
    adopted = bool(state.get("adopted"))
    pid_raw = state.get("pid")
    pid = int(pid_raw) if pid_raw is not None else None
    out: dict = {"url": url, "pid": pid, "adopted": adopted}
    if adopted:
        out["pid_alive"] = None
    else:
        if pid is None:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            raise SystemExit("state.json missing pid for a non-adopted launch")
        out["pid_alive"] = _alive(pid)
        if not out["pid_alive"]:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            raise SystemExit("pid from state.json is not running")

    st, body = _get(url + "/app")
    out["home_status"] = st
    out["home_ok"] = st == 200 and isinstance(body, str) and "<title>株式分析ツール</title>" in body
    if not out["home_ok"]:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit("GET /app is not the SPA")

    st, body = _get(url + "/api/screener?stage=2&rs_min=70")
    out["screener_status"] = st
    results = body.get("results") if isinstance(body, dict) else None
    out["screener_date"] = body.get("date") if isinstance(body, dict) else None
    out["screener_n"] = len(results) if isinstance(results, list) else 0
    if st != 200 or not results:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit("screener empty — stock.db has no indicators; this instance is not worth driving")

    st, body = _get(url + "/api/stocks/search?" + urllib.parse.urlencode({"q": "トヨタ"}))
    out["search_status"] = st
    hits = body if isinstance(body, list) else []
    out["search_n"] = len(hits)
    demo = [h for h in hits if str(h.get("code", "")).startswith("0")]
    out["demo_codes"] = demo
    if st != 200 or not hits:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit("search トヨタ returned no rows")
    if demo:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        raise SystemExit("demo 0-prefix codes in search; refuse to drive a mixed DB")

    out["ok"] = True
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

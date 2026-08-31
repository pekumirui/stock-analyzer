"""Hit the SPA's /api/* and write artifacts/<feature>/."""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
STATE = SKILL / ".run" / "state.json"
ART = SKILL / "artifacts"


def _url() -> str:
    if not STATE.is_file():
        raise SystemExit("no .run/state.json — run launch.py first")
    return json.loads(STATE.read_text(encoding="utf-8"))["url"].rstrip("/")


def _get(url: str) -> tuple[int, object]:
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read()
        return r.status, json.loads(raw.decode("utf-8"))


def _write(feature: str, name: str, method: str, url: str, status: int, body: object) -> Path:
    dest = ART / feature
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "request.txt").write_text(f"{method} {url}\nstatus {status}\n", encoding="utf-8")
    trimmed = body
    if isinstance(body, dict) and isinstance(body.get("results"), list) and len(body["results"]) > 20:
        trimmed = {**body, "results": body["results"][:20], "results_truncated": len(body["results"])}
    if isinstance(body, list) and len(body) > 20:
        trimmed = body[:20]
    path = dest / name
    path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _pick_earnings_date(base: str, month: str) -> str | None:
    status, counts = _get(f"{base}/api/earnings/calendar?month={month}")
    if status != 200 or not isinstance(counts, dict) or not counts:
        return None
    return max(counts, key=lambda d: counts[d])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("feature", choices=["screener", "earnings", "chart", "monthly", "census", "search"])
    p.add_argument("--date", default="")
    p.add_argument("--code", default="72030")
    p.add_argument("--month", default="")
    args = p.parse_args()
    base = _url()
    from datetime import date

    month = args.month or date.today().strftime("%Y-%m")

    if args.feature == "screener":
        url = f"{base}/api/screener?stage=2&rs_min=70"
        status, body = _get(url)
        path = _write("screener", "body.json", "GET", url, status, body)
        n = len(body.get("results") or []) if isinstance(body, dict) else 0
        if status != 200 or n < 1:
            raise SystemExit(f"screener failed status={status} n={n}")
        print(path)
        return

    if args.feature == "search":
        url = f"{base}/api/stocks/search?" + urllib.parse.urlencode({"q": "トヨタ"})
        status, body = _get(url)
        path = _write("search", "body.json", "GET", url, status, body)
        if status != 200 or not body:
            raise SystemExit(f"search failed status={status}")
        print(path)
        return

    if args.feature == "earnings":
        cal_url = f"{base}/api/earnings/calendar?month={month}"
        st, counts = _get(cal_url)
        _write("earnings", "calendar.json", "GET", cal_url, st, counts)
        day = args.date or _pick_earnings_date(base, month)
        if not day:
            raise SystemExit(f"no earnings days in {month}")
        url = f"{base}/api/earnings?date={day}"
        status, body = _get(url)
        path = _write("earnings", "body.json", "GET", url, status, body)
        if status != 200 or not body:
            raise SystemExit(f"earnings {day} empty")
        print(path)
        return

    if args.feature == "chart":
        url = f"{base}/api/chart/{args.code}"
        status, body = _get(url)
        path = _write("chart", "body.json", "GET", url, status, body)
        candles = body.get("candles") if isinstance(body, dict) else None
        if status != 200 or not candles:
            raise SystemExit(f"chart {args.code} has no candles")
        perf_url = f"{base}/api/earnings-performance/{args.code}"
        pst, pbody = _get(perf_url)
        _write("chart", "performance.json", "GET", perf_url, pst, pbody)
        print(path)
        return

    if args.feature == "monthly":
        cal_url = f"{base}/api/monthly/calendar?month={month}"
        st, counts = _get(cal_url)
        _write("monthly", "calendar.json", "GET", cal_url, st, counts)
        if st != 200 or not counts:
            raise SystemExit(f"no monthly days in {month}")
        day = args.date or max(counts, key=lambda d: counts[d])
        url = f"{base}/api/monthly?date={day}"
        status, body = _get(url)
        path = _write("monthly", "body.json", "GET", url, status, body)
        if status != 200 or not body:
            raise SystemExit(f"monthly {day} empty")
        print(path)
        return

    if args.feature == "census":
        url = f"{base}/api/monthly/census"
        status, body = _get(url)
        path = _write("census", "body.json", "GET", url, status, body)
        rows = body.get("rows") if isinstance(body, dict) else None
        if status != 200 or not rows:
            raise SystemExit("census empty")
        print(path)
        return


if __name__ == "__main__":
    main()

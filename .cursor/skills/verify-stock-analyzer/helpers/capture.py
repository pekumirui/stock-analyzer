"""Hit the SPA's /api/* and write artifacts/<feature>/."""
from __future__ import annotations

import argparse
import json
import urllib.error
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


def _cell_n(cell: object) -> int:
    if isinstance(cell, dict):
        return int(cell.get("n") or 0)
    return int(cell or 0)


def _pick_earnings_date(base: str, month: str, today: str) -> str | None:
    status, counts = _get(f"{base}/api/earnings/calendar?month={month}&today={today}")
    if status != 200 or not isinstance(counts, dict) or not counts:
        return None
    return max(counts, key=lambda d: _cell_n(counts[d]))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("feature", choices=["screener", "earnings", "chart", "stock", "monthly", "search", "supply", "pts", "ranking"])
    p.add_argument("--date", default="")
    p.add_argument("--code", default="")
    p.add_argument("--month", default="")
    p.add_argument("--id", default="")
    p.add_argument("--ranking", default="night_up", choices=["night_up", "night_down", "night_volume"])
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
        today = date.today().isoformat()
        cal_url = f"{base}/api/earnings/calendar?month={month}&today={today}"
        st, counts = _get(cal_url)
        _write("earnings", "calendar.json", "GET", cal_url, st, counts)
        day = args.date or _pick_earnings_date(base, month, today)
        if not day:
            raise SystemExit(f"no earnings days in {month}")
        url = f"{base}/api/earnings?date={day}&today={today}"
        status, body = _get(url)
        path = _write("earnings", "body.json", "GET", url, status, body)
        rows = body.get("actuals") if isinstance(body, dict) else None
        scheduled = body.get("scheduled") if isinstance(body, dict) else None
        if status != 200 or not (rows or scheduled):
            raise SystemExit(f"earnings {day} empty")
        print(path)
        return

    if args.feature == "chart":
        code = args.code or "72030"
        url = f"{base}/api/chart/{code}?tf=D&ma=50,150,200&rel=false"
        status, body = _get(url)
        path = _write("chart", "body.json", "GET", url, status, body)
        candles = body.get("candles") if isinstance(body, dict) else None
        if status != 200 or not candles:
            raise SystemExit(f"chart {code} has no candles")
        perf_url = f"{base}/api/earnings-performance/{code}"
        pst, pbody = _get(perf_url)
        _write("chart", "performance.json", "GET", perf_url, pst, pbody)
        print(path)
        return

    if args.feature == "stock":
        code = args.code or "72030"
        # 銘柄の見出しと概要（SPA が最初に呼ぶ）。404 は銘柄がない
        ov_url = f"{base}/api/stock/{code}/overview"
        try:
            ost, obody = _get(ov_url)
        except urllib.error.HTTPError as e:
            ost, obody = e.code, json.loads(e.read().decode("utf-8") or "null")
        _write("stock", "overview.json", "GET", ov_url, ost, obody)
        if ost != 200:
            raise SystemExit(f"stock {code} overview status={ost}")
        url = f"{base}/api/stock/{code}/filings"
        status, body = _get(url)
        path = _write("stock", "filings.json", "GET", url, status, body)
        filings = body.get("filings") if isinstance(body, dict) else None
        if status != 200 or filings is None:
            raise SystemExit(f"stock {code} failed status={status}")
        docs = [d for f in filings for d in (f.get("documents") or [])]
        (ART / "stock" / "document.html").unlink(missing_ok=True)
        req = f"GET {ov_url}\nstatus {ost}\nGET {url}\nstatus {status}\n"
        if docs:
            doc_url = base + docs[0]["url"]
            with urllib.request.urlopen(doc_url, timeout=30) as r:
                html = r.read()
                (ART / "stock" / "document.html").write_bytes(html)
                req += f"GET {doc_url}\nstatus {r.status}\n"
        (ART / "stock" / "request.txt").write_text(req, encoding="utf-8")
        # xbrl_documents が空の DB でも 200。件数を出して判断を任せる
        print(path, f"filings={len(filings)} documents={len(docs)}")
        return

    if args.feature == "monthly":
        list_url = f"{base}/api/v1/tables"
        status, body = _get(list_url)
        path = _write("monthly", "body.json", "GET", list_url, status, body)
        if status != 200 or not isinstance(body, list) or not body:
            raise SystemExit(f"monthly list empty status={status}")
        if args.code:
            code = args.code
            years_url = f"{base}/api/v1/tables/{code}"
            yst, ybody = _get(years_url)
            _write("monthly", "years.json", "GET", years_url, yst, ybody)
            years = ybody.get("years") if isinstance(ybody, dict) else None
            if yst != 200 or not years:
                raise SystemExit(f"monthly {code} has no years")
            year = max(int(y) for y in years)
            year_url = f"{base}/api/v1/tables/{code}/{year}"
            dst, dbody = _get(year_url)
            _write("monthly", "year.json", "GET", year_url, dst, dbody)
            if dst != 200:
                raise SystemExit(f"monthly {code}/{year} failed status={dst}")
        print(path)
        return

    if args.feature == "ranking":
        q = {}
        if args.month:
            q["month"] = args.month
        url = f"{base}/api/ranking/earnings-perf"
        if q:
            url += "?" + urllib.parse.urlencode(q)
        status, body = _get(url)
        path = _write("ranking", "body.json", "GET", url, status, body)
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"ranking failed status={status}")
        print(
            path,
            f"month={body.get('month')} months={len(body.get('months') or [])} rows={len(body.get('rows') or [])}",
        )
        return

    if args.feature == "pts":
        q = {"ranking": args.ranking}
        if args.date:
            q["date"] = args.date
        url = f"{base}/api/pts?" + urllib.parse.urlencode(q)
        status, body = _get(url)
        path = _write("pts", "body.json", "GET", url, status, body)
        if status != 200 or not isinstance(body, dict):
            raise SystemExit(f"pts failed status={status}")
        # 空の DB（pts_snapshots なし）でも 200。行数は表示して判断を任せる
        print(path, f"dates={len(body.get('dates') or [])} rows={len(body.get('rows') or [])}")
        return

    if args.feature == "supply":
        mat = args.id or "ethylene"
        reqs = []

        def grab(name: str, path: str) -> object:
            url = base + path
            st, body = _get(url)
            _write("supply", f"{name}.json", "GET", url, st, body)
            reqs.append(f"GET {url}\nstatus {st}\n")
            (ART / "supply" / "request.txt").write_text("".join(reqs), encoding="utf-8")
            return body

        graph = grab("graph", "/api/supply/graph")
        ids = {m["id"] for m in graph.get("materials") or []} if isinstance(graph, dict) else set()
        if mat not in ids:
            raise SystemExit(f"supply graph has no material {mat!r} (n={len(ids)})")
        grab("companies", "/api/supply/companies")
        grab("monthly", f"/api/supply/monthly/{mat}")
        grab("countries", f"/api/supply/countries/{mat}")
        print(ART / "supply" / "graph.json")
        return


if __name__ == "__main__":
    main()

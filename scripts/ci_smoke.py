"""CI smoke: 空DBで起動し、主要 API が応答すること。データ投入は CI 用の最小フィクスチャのみ。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# app.config が DATABASE_URL を読む前に一時DBへ向ける (手元の stock.db を汚さない)
_tmp = tempfile.mkdtemp(prefix="stock-analyzer-ci-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        raise SystemExit(f"{label}: {resp.status_code} {resp.text[:400]}")


def _fixture() -> str:
    from app.db import SessionLocal, init_db
    from app.ingest import upsert
    from app.models import DailyPrice, Indicator, Stock, Topix

    init_db()
    db = SessionLocal()
    code = "72030"
    day = date(2026, 8, 21)
    try:
        upsert(db, Stock, [{
            "code": code, "name": "トヨタ自動車", "sector17": "輸送用機器",
            "sector33": "輸送用機器", "market": "プライム",
            "scale_category": "TOPIX Large70", "margin_type": "貸借",
        }])
        upsert(db, DailyPrice, [{
            "code": code, "date": day,
            "open": 3000, "high": 3010, "low": 2990, "close": 3005,
            "volume": 1000, "turnover": 3_000_000, "adj_factor": 1.0,
            "adj_open": 3000, "adj_high": 3010, "adj_low": 2990, "adj_close": 3005,
            "adj_volume": 1000, "market_cap": 50_000,
        }])
        upsert(db, Topix, [{"date": day, "open": 2800, "high": 2810, "low": 2790, "close": 2805}])
        upsert(db, Indicator, [{
            "code": code, "date": day, "close": 3005,
            "ma50": 2900, "ma150": 2800, "ma200": 2700, "ma200_slope_1m": 0.01,
            "high_52w": 3200, "low_52w": 2000, "rs_raw": 20, "rs_rank": 85,
            "stage": 2, "avg_volume_50": 1000,
        }])
    finally:
        db.close()
    return code


def _check_demo_exit() -> int:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_demo", ROOT / "scripts" / "check_demo.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def main() -> None:
    from app.config import apply_dotenv

    got: dict[str, str] = {"FOO": "keep"}
    apply_dotenv(
        'JQUANTS_API_KEY=secret\n# c\nexport BAR="quoted"\nFOO=new\n=bad\nNOT KEY=x\n',
        got,
    )
    if got.get("JQUANTS_API_KEY") != "secret" or got.get("BAR") != "quoted" or got.get("FOO") != "keep":
        raise SystemExit(f"apply_dotenv: {got}")
    if "NOT KEY" in got or "" in got:
        raise SystemExit(f"apply_dotenv accepted bad keys: {got}")

    if (ROOT / "scripts" / "seed_demo.py").exists():
        raise SystemExit("scripts/seed_demo.py should have been removed")

    client = TestClient(app)

    r = client.get("/")
    _expect(r, 200, "GET /")
    if len(r.content) < 200:
        raise SystemExit("GET / returned too little content")

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (empty)")

    r = client.get("/api/stocks/search", params={"q": "トヨタ"})
    _expect(r, 200, "GET /api/stocks/search (empty)")
    if r.json() != []:
        raise SystemExit(f"search should be empty before fixture, got {r.json()!r}")

    r = client.get("/api/earnings/calendar", params={"month": "2026-08"})
    _expect(r, 200, "GET /api/earnings/calendar")
    if r.json() != {}:
        raise SystemExit(f"calendar should be empty, got {r.json()!r}")

    r = client.get("/api/chart/72030")
    _expect(r, 404, "GET /api/chart/72030 (missing)")

    if _check_demo_exit() != 0:
        raise SystemExit("check_demo should pass on empty/real-only DB")

    code = _fixture()

    r = client.get("/api/stocks/search", params={"q": "トヨタ"})
    _expect(r, 200, "GET /api/stocks/search")
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise SystemExit("search for トヨタ returned no rows after fixture")
    if rows[0].get("code") != code:
        raise SystemExit(f"unexpected search hit: {rows[0]!r}")

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (fixture)")
    body = r.json()
    results = body.get("results") if isinstance(body, dict) else body
    if not results:
        raise SystemExit("screener empty after fixture")

    r = client.get(f"/api/chart/{code}")
    _expect(r, 200, f"GET /api/chart/{code}")
    candles = r.json().get("candles") or []
    if not candles:
        raise SystemExit("chart had no candles")

    from app.db import SessionLocal
    from app.ingest import upsert
    from app.models import Stock

    db = SessionLocal()
    try:
        upsert(db, Stock, [{
            "code": "00010", "name": "leftover", "sector17": "", "sector33": "",
            "market": "", "scale_category": "", "margin_type": "",
        }])
    finally:
        db.close()
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        leftover = _check_demo_exit()
    if leftover != 1:
        raise SystemExit("check_demo should fail when a 0-prefix leftover code remains")

    print(
        f"ok: {len(rows)} search hits, "
        f"screener {len(results)}, chart {len(candles)} candles"
    )


if __name__ == "__main__":
    main()

"""CI smoke: 空DBで起動し、主要 API が応答すること。データ投入は CI 用の最小フィクスチャのみ。"""
from __future__ import annotations

import os
import subprocess
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
            "product_category": "011",
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

    spec = importlib.util.spec_from_file_location("check_demo", ROOT / "jobs" / "check_demo.py")
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

    layout = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_layout.py")],
        cwd=ROOT,
    )
    if layout.returncode != 0:
        raise SystemExit("layout tests failed")

    client = TestClient(app)

    r = client.get("/static/monthly_row_order.json")
    _expect(r, 200, "GET /static/monthly_row_order.json")
    if "76830" not in r.text:
        raise SystemExit("row-order JSON missing 76830")

    r = client.get("/")
    _expect(r, 200, "GET /")
    if len(r.content) < 200:
        raise SystemExit("GET / returned too little content")
    if "週足" not in r.text or 'value="W"' not in r.text:
        raise SystemExit("chart timeframe missing 週足")

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

    # 週足は日足を ISO 週で集約。2週にまたがる日足を足して本数と OHLC を確認する
    from app.analytics import compute_indicators, rs_universe_codes
    from app.db import SessionLocal
    from app.ingest import upsert
    from app.models import DailyPrice, Indicator, Statement, Stock

    db = SessionLocal()
    try:
        upsert(db, DailyPrice, [
            {
                "code": code, "date": date(2026, 8, 17),
                "open": 2980, "high": 2990, "low": 2970, "close": 2985,
                "volume": 800, "turnover": 2_388_000, "adj_factor": 1.0,
                "adj_open": 2980, "adj_high": 2990, "adj_low": 2970, "adj_close": 2985,
                "adj_volume": 800, "market_cap": 50_000,
            },
            {
                "code": code, "date": date(2026, 8, 24),
                "open": 3010, "high": 3050, "low": 3000, "close": 3040,
                "volume": 1200, "turnover": 3_648_000, "adj_factor": 1.0,
                "adj_open": 3010, "adj_high": 3050, "adj_low": 3000, "adj_close": 3040,
                "adj_volume": 1200, "market_cap": 50_000,
            },
        ])
    finally:
        db.close()

    r = client.get(f"/api/chart/{code}", params={"tf": "D"})
    _expect(r, 200, f"GET /api/chart/{code}?tf=D")
    daily_n = len(r.json().get("candles") or [])
    r = client.get(f"/api/chart/{code}", params={"tf": "W"})
    _expect(r, 200, f"GET /api/chart/{code}?tf=W")
    weekly = r.json().get("candles") or []
    r = client.get(f"/api/chart/{code}", params={"tf": "M"})
    _expect(r, 200, f"GET /api/chart/{code}?tf=M")
    monthly = r.json().get("candles") or []
    if daily_n != 3:
        raise SystemExit(f"daily chart expected 3 candles, got {daily_n}")
    if len(weekly) != 2:
        raise SystemExit(f"weekly chart expected 2 candles, got {weekly!r}")
    if len(monthly) != 1:
        raise SystemExit(f"monthly chart expected 1 candle, got {monthly!r}")
    w0, w1 = weekly
    if (w0.get("time") != "2026-08-21" or w0.get("open") != 2980
            or w0.get("high") != 3010 or w0.get("low") != 2970
            or w0.get("close") != 3005 or w0.get("volume") != 1800):
        raise SystemExit(f"weekly first candle: {w0!r}")
    if w1.get("time") != "2026-08-24" or w1.get("open") != 3010 or w1.get("close") != 3040:
        raise SystemExit(f"weekly second candle: {w1!r}")

    db = SessionLocal()
    try:
        u = rs_universe_codes(db)
        if u != {code}:
            raise SystemExit(f"rs_universe_codes expected {{{code!r}}}, got {u!r}")
        upsert(db, Stock, [{
            "code": "89510", "name": "日本ビルファンド投資法人",
            "sector17": "不動産", "sector33": "不動産業", "market": "プライム",
            "scale_category": "", "margin_type": "貸借", "product_category": "013",
        }])
        upsert(db, Indicator, [{
            "code": "89510", "date": date(2026, 8, 21), "close": 140000,
            "ma50": 139000, "ma150": 138000, "ma200": 137000, "ma200_slope_1m": 0.01,
            "high_52w": 150000, "low_52w": 120000, "rs_raw": 30, "rs_rank": 90,
            "stage": 2, "avg_volume_50": 1000,
        }])
        upsert(db, DailyPrice, [{
            "code": "89510", "date": date(2026, 8, 21),
            "open": 140000, "high": 141000, "low": 139000, "close": 140000,
            "volume": 100, "turnover": 14_000_000, "adj_factor": 1.0,
            "adj_open": 140000, "adj_high": 141000, "adj_low": 139000,
            "adj_close": 140000, "adj_volume": 100, "market_cap": 2_000_000,
        }])
        common_stmt = {
            "disclosed_date": date(2026, 8, 21), "disclosed_time": "15:00",
            "type_of_period": "FY", "is_revision": False,
            "net_sales": 1e8, "operating_profit": 1e7, "ordinary_profit": 1e7,
            "profit": 8e6, "eps": 10,
        }
        upsert(db, Statement, [
            {**common_stmt, "disclosure_no": "reit-1", "code": "89510",
             "type_of_document": "FYFinancialStatements_Consolidated_JP"},
            {**common_stmt, "disclosure_no": "stock-1", "code": code,
             "type_of_document": "FYFinancialStatements_Consolidated_JP",
             "disclosed_time": "15:30"},
        ])
    finally:
        db.close()

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (with REIT)")
    sc_codes = {x["code"] for x in r.json().get("results") or []}
    if "89510" in sc_codes:
        raise SystemExit("screener should exclude REIT")
    if code not in sc_codes:
        raise SystemExit("screener dropped common stock after REIT fixture")

    r = client.get("/api/earnings/calendar", params={"month": "2026-08"})
    _expect(r, 200, "GET /api/earnings/calendar (with REIT)")
    cal = r.json()
    if cal.get("2026-08-21") != 1:
        raise SystemExit(f"calendar should count only the stock, got {cal!r}")

    r = client.get("/api/earnings", params={"date": "2026-08-21"})
    _expect(r, 200, "GET /api/earnings (with REIT)")
    earn_codes = {x["code"] for x in r.json()}
    if "89510" in earn_codes:
        raise SystemExit("earnings list should exclude REIT")
    if code not in earn_codes:
        raise SystemExit("earnings list dropped common stock")

    r = client.get("/api/stocks/search", params={"q": "ビルファンド"})
    _expect(r, 200, "GET /api/stocks/search (REIT)")
    if not any(x.get("code") == "89510" for x in r.json()):
        raise SystemExit("search should still find REIT for charts")

    db = SessionLocal()
    try:
        compute_indicators(db)
        ind_codes = {c for (c,) in db.query(Indicator.code).distinct().all()}
        if "89510" in ind_codes:
            raise SystemExit("compute_indicators should drop REIT from indicators")
        if code not in ind_codes:
            raise SystemExit("compute_indicators dropped common stock")
    finally:
        db.close()

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

    monthly = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_monthly_tables.py")],
        cwd=ROOT,
    )
    if monthly.returncode != 0:
        raise SystemExit("monthly tables tests failed")
    ingest = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_monthly_ingest.py")],
        cwd=ROOT,
    )
    if ingest.returncode != 0:
        raise SystemExit("monthly ingest tests failed")
    master = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_monthly_stock_master.py")],
        cwd=ROOT,
    )
    if master.returncode != 0:
        raise SystemExit("monthly stock master tests failed")
    issuers = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_monthly_issuers.py")],
        cwd=ROOT,
    )
    if issuers.returncode != 0:
        raise SystemExit("monthly issuers tests failed")
    monthly_sync = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_sync_monthly_from_vps.py")],
        cwd=ROOT,
    )
    if monthly_sync.returncode != 0:
        raise SystemExit("monthly vps sync tests failed")
    tdnet = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_tdnet_monthly_r2.py")],
        cwd=ROOT,
    )
    if tdnet.returncode != 0:
        raise SystemExit("tdnet monthly r2 tests failed")
    ir_monthly = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_ir_monthly_r2.py")],
        cwd=ROOT,
    )
    if ir_monthly.returncode != 0:
        raise SystemExit("ir monthly r2 tests failed")
    ir_html = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_ir_monthly_html_r2.py")],
        cwd=ROOT,
    )
    if ir_html.returncode != 0:
        raise SystemExit("ir monthly html r2 tests failed")
    ir_html_parity = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_html_issue248_parity.py")],
        cwd=ROOT,
    )
    if ir_html_parity.returncode != 0:
        raise SystemExit("ir monthly html issue248 parity tests failed")
    ir_html_ingest = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_ir_monthly_html_ingest.py")],
        cwd=ROOT,
    )
    if ir_html_ingest.returncode != 0:
        raise SystemExit("ir monthly html ingest tests failed")
    ir_xls_ingest = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_ir_monthly_xls_ingest.py")],
        cwd=ROOT,
    )
    if ir_xls_ingest.returncode != 0:
        raise SystemExit("ir monthly xls ingest tests failed")
    xbrl = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_xbrl_parse.py")],
        cwd=ROOT,
    )
    if xbrl.returncode != 0:
        raise SystemExit("xbrl parse tests failed")
    xbrl_match = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_xbrl_match_rate.py")],
        cwd=ROOT,
    )
    if xbrl_match.returncode != 0:
        raise SystemExit("xbrl match rate tests failed")
    alerts = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_alert_patterns.py")],
        cwd=ROOT,
    )
    if alerts.returncode != 0:
        raise SystemExit("alert pattern tests failed")


if __name__ == "__main__":
    main()

"""CI smoke: 空DBで起動し、主要 API が応答すること。データ投入は CI 用の最小フィクスチャのみ。"""
from __future__ import annotations

import json
import os
import re
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


# API スモークの後に subprocess で走らせる。test_layout.py は API より前に走らせるので別枠。
SUITES = (
    "test_monthly_tables.py",
    "test_public_monthly.py",
    "test_nginx_public.py",
    "test_github_ssh.py",
    "test_sqlite_deploy.py",
    "test_monthly_ingest.py",
    "test_monthly_stock_master.py",
    "test_monthly_issuers.py",
    "test_sync_monthly_from_vps.py",
    "test_tdnet_monthly_r2.py",
    "test_tdnet_monthly_pending.py",
    "test_tdnet_monthly_pdf_sidecar.py",
    "test_r2_mount.py",
    "test_ir_monthly_r2.py",
    "test_ir_monthly_html_r2.py",
    "test_html_recipe.py",
    "test_html_issue248_parity.py",
    "test_html_3082_scope.py",
    "test_ir_monthly_html_ingest.py",
    "test_ir_monthly_xls_ingest.py",
    "test_xbrl_parse.py",
    "test_xbrl_match_rate.py",
    "test_xbrl_units.py",
    "test_tdnet_xbrl_r2.py",
    "test_vps_packages_r2.py",
    "test_backup_db.py",
    "test_backfill.py",
    "test_alert_patterns.py",
    "test_html_predicates.py",
    "test_analytics.py",
    "test_jquants.py",
    "test_earnings_schedule.py",
    "test_fx.py",
    "test_supply_chain.py",
    "test_material_stats.py",
)


def unregistered_tests(tests_dir: Path, workflow: str) -> tuple[list[str], list[str]]:
    """(どこからも走らない test_*.py, 実在しない登録名)。workflow は cicd.yml の本文。"""
    direct = set(re.findall(r"python tests/(test_\w+\.py)", workflow))
    registered = {"test_layout.py", *SUITES} | direct
    present = {p.name for p in tests_dir.glob("test_*.py")}
    return sorted(present - registered), sorted(set(SUITES) - present)


def main() -> None:
    from app.config import apply_dotenv

    workflow = (ROOT / ".github" / "workflows" / "cicd.yml").read_text(encoding="utf-8")
    missing, stale = unregistered_tests(ROOT / "tests", workflow)
    if missing or stale:
        raise SystemExit(f"register tests in ci_smoke.SUITES: missing={missing} stale={stale}")

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

    r = client.get("/static/ir_monthly_codes.json")
    _expect(r, 200, "GET /static/ir_monthly_codes.json")
    codes = r.json().get("codes")
    if not isinstance(codes, list) or "1878" not in codes or "141A" not in codes or "9989" not in codes or "2792" not in codes:
        raise SystemExit(f"IR codes JSON missing watchlist codes: {codes!r}"[:400])
    if len(codes) != len(set(codes)):
        raise SystemExit("IR codes JSON has duplicates")
    if codes.index("1878") > codes.index("141A"):
        raise SystemExit("IR codes must list issuers before html")
    if "index_url" in r.text or "http" in r.text.lower():
        raise SystemExit("IR codes JSON must not include crawl URLs")
    from app.ir_monthly_codes import load_ir_monthly_codes

    sample = Path(_tmp) / "ir.json"
    sample.write_text(json.dumps({
        "issuers": [{"code": "1878"}, {"code": "18780"}, {"code": "nope"}],
        "html": [{"code": "141a", "parse": False}, {"code": "1878"}],
    }), encoding="utf-8")
    if load_ir_monthly_codes(sample) != ["1878", "141A"]:
        raise SystemExit(f"load_ir_monthly_codes: {load_ir_monthly_codes(sample)!r}")

    r = client.get("/", follow_redirects=False)
    _expect(r, 301, "GET /")
    loc = r.headers.get("location") or ""
    if not loc.endswith("/monthly"):
        raise SystemExit(f"GET / should redirect to /monthly, got {loc!r}")

    r = client.get("/app")
    _expect(r, 200, "GET /app")
    if len(r.content) < 200:
        raise SystemExit("GET /app returned too little content")
    if "週足" not in r.text or 'value="W"' not in r.text:
        raise SystemExit("chart timeframe missing 週足")

    r = client.get("/monthly")
    _expect(r, 200, "GET /monthly")
    if "株KPI" not in r.text:
        raise SystemExit("GET /monthly missing site name")

    r = client.get("/robots.txt")
    _expect(r, 200, "GET /robots.txt")
    if "Sitemap:" not in r.text:
        raise SystemExit("robots.txt missing Sitemap")

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (empty)")

    r = client.get("/api/stocks/search", params={"q": "トヨタ"})
    _expect(r, 200, "GET /api/stocks/search (empty)")
    if r.json() != []:
        raise SystemExit(f"search should be empty before fixture, got {r.json()!r}")

    r = client.get("/api/earnings/calendar", params={"month": "2026-08", "today": "2026-10-02"})
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

    r = client.get("/api/earnings/calendar", params={"month": "2026-08", "today": "2026-10-02"})
    _expect(r, 200, "GET /api/earnings/calendar (with REIT)")
    cal = r.json()
    if cal.get("2026-08-21") != {"kind": "actual", "n": 1}:
        raise SystemExit(f"calendar should count only the stock, got {cal!r}")

    r = client.get("/api/earnings", params={"date": "2026-08-21", "today": "2026-10-02"})
    _expect(r, 200, "GET /api/earnings (with REIT)")
    listed = r.json()
    if not isinstance(listed, dict) or not isinstance(listed.get("actuals"), list):
        raise SystemExit(f"earnings list should be {{kind, actuals, scheduled}}, got {listed!r}")
    earn_codes = {x["code"] for x in listed["actuals"]}
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

    for name in SUITES:
        proc = subprocess.run([sys.executable, str(ROOT / "tests" / name)], cwd=ROOT)
        if proc.returncode != 0:
            raise SystemExit(f"{name} failed")


if __name__ == "__main__":
    main()

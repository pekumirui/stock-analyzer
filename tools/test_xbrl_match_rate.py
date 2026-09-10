"""Offline XBRL match-rate tests. Network-free, tempfile sqlite."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))

import copy_stocks_for_verify as copy_mod  # noqa: E402
import fetch_xbrl_golden as fetch_mod  # noqa: E402
import xbrl_match_rate as mr  # noqa: E402

FIXTURE_DIR = ROOT / "tools" / "fixtures" / "xbrl_match"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _seed_db(path: Path, sql: str | None = None) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(sql if sql is not None else (FIXTURE_DIR / "seed.sql").read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


MIN_SCHEMA = """
CREATE TABLE xbrl_std_items (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    statement TEXT NOT NULL,
    section TEXT,
    label_ja TEXT NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    sign TEXT NOT NULL DEFAULT 'natural',
    is_required INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE xbrl_filings (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tdnet',
    doc_id TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'other',
    period_type TEXT,
    fiscal_year_end DATE,
    submitted_at TEXT NOT NULL,
    title TEXT,
    accounting_standard TEXT,
    is_amendment INTEGER NOT NULL DEFAULT 0,
    raw_path TEXT NOT NULL,
    raw_sha256 TEXT,
    parse_status TEXT NOT NULL DEFAULT 'pending',
    parse_error TEXT,
    parsed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE xbrl_financials (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    filing_id INTEGER NOT NULL,
    std_item_id INTEGER NOT NULL,
    is_consolidated INTEGER NOT NULL,
    period_type TEXT NOT NULL,
    period_start DATE,
    period_end DATE NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL DEFAULT 'JPY',
    source_fact_id INTEGER,
    source_element_id INTEGER,
    generated_at TEXT NOT NULL
);
"""


def _write_golden(
    dir_path: Path,
    metrics: dict,
    code: str = "72030",
    period_end: str = "2025-03-31",
    *,
    period_basis: str = "cumulative",
    extra: dict | None = None,
) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    rec = {
        "code": code,
        "code4": code[:4],
        "period_end": period_end,
        "period_basis": period_basis,
        "accounting_standard": None,
        "is_consolidated": True,
        "metrics": metrics,
        "unit": "million_jpy",
        "source": "irbank",
        "fetched_at": "2026-09-07T00:00:00+09:00",
    }
    if extra:
        rec.update(extra)
    path = dir_path / f"{code[:4]}_{period_end}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _insert_std(conn: sqlite3.Connection, rows: list[tuple[int, str, str]]) -> None:
    conn.executemany(
        "INSERT INTO xbrl_std_items (id, code, statement, label_ja) VALUES (?, ?, ?, ?)",
        [(i, code, stmt, code) for i, code, stmt in rows],
    )


def _insert_fin(
    conn: sqlite3.Connection,
    *,
    std_item_id: int,
    value: float,
    unit: str = "JPY",
    consol: int = 1,
    code: str = "72030",
    period_end: str = "2025-03-31",
    filing_id: int = 1,
) -> None:
    if conn.execute("SELECT 1 FROM xbrl_filings WHERE id=?", (filing_id,)).fetchone() is None:
        conn.execute(
            """INSERT INTO xbrl_filings (
                id, code, source, doc_id, doc_type, period_type, submitted_at,
                raw_path, parse_status, created_at
            ) VALUES (?, ?, 'tdnet', ?, 'annual', 'FY', '2025-04-25 15:00:00',
                      'x.zip', 'parsed', '2026-09-07T00:00:00')""",
            (filing_id, code, f"doc{filing_id}"),
        )
    conn.execute(
        """INSERT INTO xbrl_financials (
            code, filing_id, std_item_id, is_consolidated, period_type,
            period_end, value, unit, generated_at
        ) VALUES (?, ?, ?, ?, 'FY', ?, ?, ?, '2026-09-07T00:00:00')""",
        (code, filing_id, std_item_id, consol, period_end, value, unit),
    )


def test_scale_yen_to_million(tmp: Path) -> None:
    db = tmp / "scale.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [(1, "net_sales", "PL")])
        _insert_fin(conn, std_item_id=1, value=100_000_000, unit="JPY")
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_scale"
    _write_golden(gdir, {"revenue": 100})
    report = mr.run(db, gdir)
    if report.matched != 1 or report.compared != 1 or report.missing != 0:
        _fail(f"scale: {report.matched}/{report.compared} missing={report.missing}")
    million = mr.to_million_jpy(100_000_000, "JPY")
    if million != Decimal("100"):
        _fail(f"to_million_jpy yen: {million}")
    million2 = mr.to_million_jpy(100, "million_jpy")
    if million2 != Decimal("100"):
        _fail(f"to_million_jpy already million: {million2}")
    iso = mr.to_million_jpy(200_000_000, "iso4217:JPY")
    if iso != Decimal("200"):
        _fail(f"iso4217:JPY: {iso}")


def test_tolerance(tmp: Path) -> None:
    db = tmp / "tol.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [
            (1, "operating_income", "PL"),
            (2, "net_income", "PL"),
            (3, "ordinary_income", "PL"),
        ])
        # abs: 100.5 vs 100 → 0.5 ≤ 1
        _insert_fin(conn, std_item_id=1, value=100_500_000, unit="JPY")
        # rel: 10010 vs 10000 → 10/10010 ≤ 0.001
        _insert_fin(conn, std_item_id=2, value=10_010_000_000, unit="JPY")
        # fail: 105 vs 100 → abs 5 > 1 and rel 0.05 > 0.001
        _insert_fin(conn, std_item_id=3, value=105_000_000, unit="JPY")
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_tol"
    _write_golden(gdir, {"operating_income": 100, "net_income": 10000, "ordinary_income": 100})
    report = mr.run(db, gdir)
    by = {r.std_item: r for r in report.rows}
    if by["operating_income"].status != "match":
        _fail(f"abs tol should match: {by['operating_income']}")
    if by["net_income"].status != "match":
        _fail(f"rel tol should match: {by['net_income']}")
    if by["ordinary_income"].status != "mismatch":
        _fail(f"outside tol should mismatch: {by['ordinary_income']}")
    if report.matched != 2 or report.compared != 3:
        _fail(f"tolerance counts {report.matched}/{report.compared}")


def test_missing_excluded(tmp: Path) -> None:
    db = tmp / "miss.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [
            (1, "net_sales", "PL"),
            (2, "total_assets", "BS"),
        ])
        _insert_fin(conn, std_item_id=1, value=100_000_000, unit="JPY")
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_miss"
    _write_golden(gdir, {"revenue": 100, "total_assets": 500})
    report = mr.run(db, gdir)
    if report.missing != 1:
        _fail(f"expected 1 missing, got {report.missing}")
    if report.compared != 1 or report.matched != 1:
        _fail(f"missing should not fail: {report.matched}/{report.compared}")
    if report.mismatches != 0:
        _fail(f"missing counted as mismatch: {report.mismatches}")
    ta = [r for r in report.rows if r.std_item == "total_assets"]
    if not ta or ta[0].status != "missing":
        _fail(f"total_assets status: {ta}")


def test_fixture_overall(tmp: Path) -> None:
    db = tmp / "fixture.db"
    _seed_db(db)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mr.main(["--db", str(db), "--golden-dir", str(FIXTURE_DIR), "--out", str(tmp / "out.json")])
    if rc != 0:
        _fail(f"match_rate exit {rc}")
    text = buf.getvalue()
    if "overall: 7/8 = 87.50%" not in text:
        _fail(f"fixture stdout: {text}")
    if "missing_excluded=1" not in text:
        _fail(f"missing not in stdout: {text}")
    if "skipped_non_cumulative=2" not in text:
        _fail(f"expected 2 skipped non-cumulative goldens: {text}")
    if "investing_cf=0/1" not in text:
        _fail(f"per_metric missing mismatch: {text}")
    out = json.loads((tmp / "out.json").read_text(encoding="utf-8"))
    if out["overall"]["matched"] != 7 or out["overall"]["compared"] != 8:
        _fail(f"json overall {out['overall']}")
    if out["overall"]["missing"] != 1:
        _fail(f"json missing {out['overall']}")


def test_prefer_consolidated(tmp: Path) -> None:
    db = tmp / "consol.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [(1, "net_sales", "PL")])
        _insert_fin(conn, std_item_id=1, value=1, unit="JPY", consol=0)
        _insert_fin(conn, std_item_id=1, value=100_000_000, unit="JPY", consol=1)
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_consol"
    _write_golden(gdir, {"revenue": 100})
    report = mr.run(db, gdir)
    if report.matched != 1:
        _fail(f"should pick consol 100M yen: {report.rows}")


def test_copy_stocks(tmp: Path) -> None:
    src = tmp / "src.db"
    dst = tmp / "dst.db"
    conn = sqlite3.connect(src)
    try:
        conn.execute(
            "CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, sector33 TEXT)"
        )
        conn.execute("INSERT INTO stocks VALUES ('72030', 'トヨタ自動車', '輸送用機器')")
        conn.execute("INSERT INTO stocks VALUES ('99840', 'ソフトバンクG', '情報・通信業')")
        conn.commit()
    finally:
        conn.close()
    n = copy_mod.copy_stocks(src, dst)
    if n != 2:
        _fail(f"copied {n}")
    got = sqlite3.connect(dst).execute("SELECT code FROM stocks ORDER BY code").fetchall()
    if [r[0] for r in got] != ["72030", "99840"]:
        _fail(f"dest stocks {got}")
    try:
        copy_mod.copy_stocks(src, src)
    except SystemExit as exc:
        if "equals source" not in str(exc):
            _fail(f"same-path message: {exc}")
    else:
        _fail("same-path copy should refuse")
    live = Path("/workspace/verify-live/stock.db")
    if live.exists():
        try:
            copy_mod.copy_stocks(src, live)
        except SystemExit as exc:
            if "prod" not in str(exc).lower() and "refusing" not in str(exc).lower():
                _fail(f"live dest message: {exc}")
        else:
            _fail("verify-live/stock.db should be refused")
    repo_stock = ROOT / "stock.db"
    if repo_stock.exists() or repo_stock.is_symlink():
        try:
            copy_mod.copy_stocks(src, repo_stock)
        except SystemExit:
            pass
        else:
            _fail("repo stock.db should be refused")


def test_fetch_golden(tmp: Path) -> None:
    out = tmp / "golden_out"
    src = FIXTURE_DIR / "mcp_irbank_sample.json"
    rc = fetch_mod.main(["--input", str(src), "--out-dir", str(out)])
    if rc != 0:
        _fail(f"fetch exit {rc}")
    dest = out / "7203_2025-03-31.json"
    if not dest.is_file():
        _fail(f"missing {dest}")
    rec = json.loads(dest.read_text(encoding="utf-8"))
    if rec["code"] != "72030" or rec["code4"] != "7203":
        _fail(f"codes {rec}")
    if rec["period_end"] != "2025-03-31":
        _fail(f"period {rec['period_end']}")
    if rec["unit"] != "million_jpy":
        _fail(f"unit {rec['unit']}")
    if rec["metrics"].get("revenue") != 100 or rec["metrics"].get("equity") != 200:
        _fail(f"metrics {rec['metrics']}")
    if rec.get("period_basis") != "cumulative":
        _fail(f"period_basis {rec.get('period_basis')}")
    nested_dir = tmp / "irbank_map"
    nested_dir.mkdir()
    (nested_dir / "pl.json").write_text(
        json.dumps({"7203": {"2025/03": {"売上高": 9, "営業利益": 8}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    out2 = tmp / "golden_map"
    fetch_mod.main(["--input", str(nested_dir), "--out-dir", str(out2)])
    rec2 = json.loads((out2 / "7203_2025-03-31.json").read_text(encoding="utf-8"))
    if rec2["metrics"].get("revenue") != 9:
        _fail(f"map metrics {rec2['metrics']}")


def test_skip_single_quarter_golden(tmp: Path) -> None:
    db = tmp / "skip_sq.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [(1, "net_sales", "PL")])
        _insert_fin(conn, std_item_id=1, value=4_646_000_000, unit="JPY",
                    code="17240", period_end="2026-06-30")
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_sq"
    _write_golden(
        gdir,
        {"revenue": 2348},
        code="17240",
        period_end="2026-06-30",
        period_basis="single_quarter",
    )
    report = mr.run(db, gdir)
    if report.skipped != 1 or report.compared != 0:
        _fail(f"single_quarter should skip: skipped={report.skipped} compared={report.compared}")
    if not report.skipped_rows or report.skipped_rows[0].reason != "single_quarter_not_comparable":
        _fail(f"skip reason: {report.skipped_rows}")


def test_skip_missing_period_basis(tmp: Path) -> None:
    db = tmp / "skip_miss.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(MIN_SCHEMA)
        _insert_std(conn, [(1, "net_sales", "PL")])
        _insert_fin(conn, std_item_id=1, value=100_000_000, unit="JPY")
        conn.commit()
    finally:
        conn.close()
    gdir = tmp / "g_miss_basis"
    gdir.mkdir(parents=True, exist_ok=True)
    rec = {
        "code": "72030",
        "code4": "7203",
        "period_end": "2025-03-31",
        "metrics": {"revenue": 100},
        "unit": "million_jpy",
        "source": "irbank",
        "fetched_at": "2026-09-07T00:00:00+09:00",
    }
    (gdir / "7203_2025-03-31.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = mr.run(db, gdir)
    if report.skipped != 1 or report.compared != 0:
        _fail(f"missing basis should skip: {report.skipped}/{report.compared}")
    if report.skipped_rows[0].reason != "missing_period_basis":
        _fail(f"reason {report.skipped_rows}")


def test_fetch_builds_cumulative_from_standalone(tmp: Path) -> None:
    out = tmp / "cum_out"
    src = FIXTURE_DIR / "irbank_quarterly_standalone.json"
    rc = fetch_mod.main(["--input", str(src), "--out-dir", str(out)])
    if rc != 0:
        _fail(f"fetch exit {rc}")
    q1 = json.loads((out / "7203_2025-06-30.json").read_text(encoding="utf-8"))
    q2 = json.loads((out / "7203_2025-09-30.json").read_text(encoding="utf-8"))
    q3 = json.loads((out / "7203_2025-12-31.json").read_text(encoding="utf-8"))
    fy = json.loads((out / "7203_2026-03-31.json").read_text(encoding="utf-8"))
    for rec, label in ((q1, "Q1"), (q2, "Q2"), (q3, "Q3"), (fy, "FY")):
        if rec.get("period_basis") != "cumulative":
            _fail(f"{label} basis {rec.get('period_basis')}")
    if q1["metrics"]["revenue"] != 100:
        _fail(f"Q1 revenue {q1['metrics']}")
    if q2["metrics"]["revenue"] != 300:  # 100+200
        _fail(f"Q2 H1 revenue {q2['metrics']}")
    if q3["metrics"]["revenue"] != 600:  # 100+200+300
        _fail(f"Q3 9M revenue {q3['metrics']}")
    if fy["metrics"]["revenue"] != 1000:
        _fail(f"FY revenue {fy['metrics']}")
    if q2.get("cumulative_build") != "sum_q1_q2_standalone":
        _fail(f"Q2 build {q2.get('cumulative_build')}")
    if q3.get("cumulative_build") != "sum_q1_q2_q3_standalone":
        _fail(f"Q3 build {q3.get('cumulative_build')}")
    for p in out.glob("*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("period_basis") == "single_quarter":
            _fail(f"wrote single_quarter {p}")


def test_fetch_rejects_orphan_q2_standalone(tmp: Path) -> None:
    """Q2 standalone without Q1 cannot build H1 — nothing matchable written."""
    src = tmp / "orphan_q2.json"
    src.write_text(
        json.dumps(
            {
                "code": "17240",
                "fiscal_year": "2026/12",
                "quarter": "Q2",
                "period_basis": "single_quarter",
                "metrics": {"revenue": 2348},
                "source": "irbank",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp / "orphan_out"
    try:
        fetch_mod.main(["--input", str(src), "--out-dir", str(out)])
    except SystemExit as exc:
        if "no cumulative" not in str(exc).lower() and "no cumulative" not in str(exc):
            if out.exists() and any(out.glob("*.json")):
                _fail(f"should not write goldens: {list(out.glob('*.json'))}")
    else:
        files = list(out.glob("*.json")) if out.exists() else []
        if files:
            _fail(f"orphan Q2 should not write: {files}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="xbrl-match-") as d:
        tmp = Path(d)
        test_scale_yen_to_million(tmp)
        test_tolerance(tmp)
        test_missing_excluded(tmp)
        test_fixture_overall(tmp)
        test_prefer_consolidated(tmp)
        test_copy_stocks(tmp)
        test_fetch_golden(tmp)
        test_skip_single_quarter_golden(tmp)
        test_skip_missing_period_basis(tmp)
        test_fetch_builds_cumulative_from_standalone(tmp)
        test_fetch_rejects_orphan_q2_standalone(tmp)
    print("ok: xbrl match rate")


if __name__ == "__main__":
    main()

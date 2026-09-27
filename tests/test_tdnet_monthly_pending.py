"""tdnet_monthly_pending の純関数。ネットワークには出ない。一時 sqlite + 偽 PDF 名。"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT / "tools"))

import tdnet_monthly_pending as pending  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _touch_pdf(dest: Path, code: str, name: str) -> Path:
    folder = dest / code
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"%PDF-fake")
    return path


def _seed_db(path: Path, values: list[tuple[str, int, int]]) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE monthly_values ("
            "code TEXT, year INTEGER, element TEXT, month INTEGER, value REAL)"
        )
        conn.executemany(
            "INSERT INTO monthly_values (code, year, element, month, value) "
            "VALUES (?, ?, '売上高', ?, 1.0)",
            values,
        )
        conn.commit()
    finally:
        conn.close()
    return path


def test_pdf_date_from_name() -> None:
    got = pending.pdf_date_from_name(
        "20260805_9279_ギフトHD_2026年10月期7月度_直営店売上速報についてのお知らせ.pdf"
    )
    if got != date(2026, 8, 5):
        _fail(f"gift pdf date {got}")
    if pending.pdf_date_from_name("20260901_262A_テスト_月次.PDF") != date(2026, 9, 1):
        _fail("case-insensitive .PDF")
    if pending.pdf_date_from_name("20261301_9279_x.pdf") is not None:
        _fail("invalid calendar date must be None")
    if pending.pdf_date_from_name("9279_no_date.pdf") is not None:
        _fail("missing prefix must be None")
    if pending.pdf_date_from_name("20260901_9279_x.html") is not None:
        _fail("html must be None")
    if pending.pdf_date_from_name("20260901_9279_x.pdf.part") is not None:
        _fail(".part must be None")


def test_expected_covered_month() -> None:
    if pending.expected_covered_month(date(2026, 8, 5)) != (2026, 7):
        _fail("8/5 → 7月")
    if pending.expected_covered_month(date(2026, 1, 8)) != (2025, 12):
        _fail("1/8 → previous year December")
    if pending.expected_covered_month(date(2026, 12, 31)) != (2026, 11):
        _fail("12/31 → 11月")


def test_heuristic_matches_ingest_anchor() -> None:
    os.environ.setdefault(
        "DATABASE_URL", f"sqlite:///{ROOT / 'stock.db'}"
    )
    sys.path.insert(0, str(ROOT))
    from app.monthly_ingest import _anchor_year

    for as_of in (
        date(2026, 8, 5),
        date(2026, 1, 8),
        date(2026, 3, 1),
        date(2025, 12, 15),
    ):
        year, month = pending.expected_covered_month(as_of)
        anchored = _anchor_year(month, as_of)
        if anchored != year:
            _fail(f"_anchor_year({month}, {as_of})={anchored} != {year}")


def _fixture(tmp: Path) -> tuple[Path, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / "monthly"
    db = tmp / "stock.db"
    # ok: PDF 2026-08-05 → expected 2026-07, DB has 2026-07
    _touch_pdf(
        dest,
        "9279",
        "20260805_9279_ギフトHD_2026年10月期7月度_直営店売上速報についてのお知らせ.pdf",
    )
    _touch_pdf(dest, "9279", "20260705_9279_ギフトHD_古い.pdf")
    # stale: PDF 2026-09-10 → expected 2026-08, DB latest 2026-06 (gap 2)
    _touch_pdf(dest, "7203", "20260910_7203_トヨタ_月次.pdf")
    # missing: PDF 2026-09-01 → expected 2026-08, no monthly_values
    _touch_pdf(dest, "262A", "20260901_262A_テスト_月次.pdf")
    # year wrap ok: PDF 2026-01-08 → expected 2025-12
    _touch_pdf(dest, "9983", "20260108_9983_ファーストリテイリング_月次.pdf")
    # DB ahead of PDF (HTML ingest newer) → ok, not stale
    _touch_pdf(dest, "3382", "20260805_3382_セブン_月次.pdf")
    # ignored: html, no date, non-issuer dir
    html_dir = dest / "1234"
    html_dir.mkdir(parents=True, exist_ok=True)
    (html_dir / "20260915_1234_x_月次.html").write_text("html", encoding="utf-8")
    (html_dir / "notes.txt").write_text("no", encoding="utf-8")
    (dest / "_cache").mkdir(exist_ok=True)
    (dest / "_cache" / "20260901_xxxx_y.pdf").write_bytes(b"%PDF")
    _seed_db(
        db,
        [
            ("92790", 2026, 6),
            ("92790", 2026, 7),
            ("72030", 2026, 5),
            ("72030", 2026, 6),
            ("99830", 2025, 12),
            ("33820", 2026, 8),
        ],
    )
    return dest, db


def test_scan_picks_latest_and_skips_non_pdf(tmp: Path) -> None:
    dest, _db = _fixture(tmp)
    pdfs = pending.scan_latest_pdfs(dest)
    if set(pdfs) != {"9279", "7203", "262A", "9983", "3382"}:
        _fail(f"codes {sorted(pdfs)}")
    if pdfs["9279"].pdf_date != date(2026, 8, 5):
        _fail(f"9279 latest {pdfs['9279']}")
    if "1234" in pdfs:
        _fail("html-only issuer must be skipped")
    if "_cache" in pdfs:
        _fail("non-issuer dir must be skipped")


def test_report_statuses(tmp: Path) -> None:
    dest, db = _fixture(tmp)
    text = pending.run_report(dest=dest, db_path=db)
    if "STALE" not in text or "MISSING" not in text:
        _fail(f"status labels missing:\n{text}")
    if "*7203" not in text:
        _fail(f"7203 not highlighted:\n{text}")
    if "*262A" not in text:
        _fail(f"262A not highlighted:\n{text}")
    if "2026-08" not in text or "2026-06" not in text:
        _fail(f"7203 expected/db months missing:\n{text}")
    payload = json.loads(pending.run_report(dest=dest, db_path=db, as_json=True))
    if payload["summary"] != {"scanned": 5, "ok": 3, "stale": 1, "missing": 1}:
        _fail(f"summary {payload['summary']}")
    by_code = {r["code"]: r for r in payload["rows"]}
    if by_code["7203"]["status"] != "stale" or by_code["7203"]["gap_months"] != 2:
        _fail(f"7203 {by_code['7203']}")
    if by_code["262A"]["status"] != "missing" or by_code["262A"]["db_year"] is not None:
        _fail(f"262A {by_code['262A']}")
    if by_code["9279"]["status"] != "ok" or by_code["9279"]["gap_months"] != 0:
        _fail(f"9279 {by_code['9279']}")
    if by_code["9983"]["status"] != "ok":
        _fail(f"year wrap {by_code['9983']}")
    if by_code["3382"]["status"] != "ok" or by_code["3382"]["gap_months"] != -1:
        _fail(f"DB ahead must be ok {by_code['3382']}")
    # stale/missing first
    if payload["rows"][0]["status"] != "missing":
        _fail(f"sort { [r['status'] for r in payload['rows']] }")


def test_only_stale_and_code_filter(tmp: Path) -> None:
    dest, db = _fixture(tmp)
    payload = json.loads(
        pending.run_report(dest=dest, db_path=db, only_stale=True, as_json=True)
    )
    codes = [r["code"] for r in payload["rows"]]
    if codes != ["262A", "7203"]:
        _fail(f"--only-stale codes {codes}")
    one = json.loads(
        pending.run_report(
            dest=dest, db_path=db, only_codes={"7203"}, as_json=True
        )
    )
    if [r["code"] for r in one["rows"]] != ["7203"]:
        _fail(f"--code {one['rows']}")
    padded = pending.parse_codes(["72030", "262A0"])
    if padded != {"7203", "262A"}:
        _fail(f"parse_codes 5-digit {padded}")


def test_cli_json_and_filters(tmp: Path) -> None:
    dest, db = _fixture(tmp)
    buf = io.StringIO()
    rc = pending.main(
        ["--dest", str(dest), "--db", str(db), "--json", "--only-stale"],
        out=buf,
    )
    if rc != 0:
        _fail(f"cli rc {rc}")
    payload = json.loads(buf.getvalue())
    if payload["summary"]["stale"] != 1 or payload["summary"]["missing"] != 1:
        _fail(payload["summary"])
    if payload["readonly"] is not True:
        _fail("readonly flag")
    buf2 = io.StringIO()
    rc = pending.main(
        ["--dest", str(dest), "--db", str(db), "--code", "9279"],
        out=buf2,
    )
    if rc != 0:
        _fail(f"cli --code rc {rc}")
    text = buf2.getvalue()
    if "9279" not in text or "7203" in text:
        _fail(f"--code leaked other issuers:\n{text}")


def test_sqlite_is_read_only(tmp: Path) -> None:
    dest, db = _fixture(tmp)
    before = db.read_bytes()
    conn = pending.connect_sqlite_ro(db)
    try:
        try:
            conn.execute("INSERT INTO monthly_values VALUES ('x', 1, 'a', 1, 1)")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "readonly" not in str(e).lower() and "read-only" not in str(e).lower():
                _fail(f"expected readonly error, got {e}")
        else:
            _fail("mode=ro must reject INSERT")
    finally:
        conn.close()
    pending.run_report(dest=dest, db_path=db)
    after = db.read_bytes()
    if after != before:
        _fail("report must not write the sqlite file")
    pdf = dest / "7203" / "20260910_7203_トヨタ_月次.pdf"
    if not pdf.is_file() or pdf.read_bytes() != b"%PDF-fake":
        _fail("report must not rewrite PDFs")


def test_missing_paths(tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    err = io.StringIO()
    old_err = sys.stderr
    sys.stderr = err
    try:
        rc = pending.main(
            ["--dest", str(tmp / "no-dest"), "--db", str(tmp / "no.db")],
            out=buf,
        )
        if rc != 2:
            _fail(f"missing dest rc {rc}")
        db = tmp / "empty.db"
        _seed_db(db, [])
        rc = pending.main(
            ["--dest", str(tmp / "no-dest"), "--db", str(db)],
            out=buf,
        )
        if rc != 2:
            _fail(f"missing dest with db rc {rc}")
        dest = tmp / "monthly"
        dest.mkdir()
        rc = pending.main(
            ["--dest", str(dest), "--db", str(tmp / "absent.db")],
            out=buf,
        )
        if rc != 2:
            _fail(f"missing db rc {rc}")
    finally:
        sys.stderr = old_err


def test_default_dest_reuses_tdnet_job() -> None:
    from tdnet_monthly_r2 import DEFAULT_DEST

    if pending.DEFAULT_DEST != DEFAULT_DEST:
        _fail("must reuse tdnet_monthly_r2.DEFAULT_DEST")
    if pending.DEFAULT_DEST != "/home/stock/r2-share/monthly":
        _fail(f"unexpected dest {pending.DEFAULT_DEST}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_pdf_date_from_name()
    test_expected_covered_month()
    test_heuristic_matches_ingest_anchor()
    test_default_dest_reuses_tdnet_job()
    with TemporaryDirectory(prefix="tdnet-pending-") as d:
        tmp = Path(d)
        test_scan_picks_latest_and_skips_non_pdf(tmp / "scan")
        test_report_statuses(tmp / "status")
        test_only_stale_and_code_filter(tmp / "filter")
        test_cli_json_and_filters(tmp / "cli")
        test_sqlite_is_read_only(tmp / "ro")
        test_missing_paths(tmp / "missing")
    print("ok: tdnet monthly pending report")


if __name__ == "__main__":
    main()

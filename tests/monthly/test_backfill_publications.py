from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from jobs.monthly.publication_claim import estimate_month, report_month
from tools.monthly.backfill_publications import main


def test_title_month_and_45_day_window():
    assert report_month("２０２６年７月度 売上高", date(2026, 8, 5)) == (2026, 7)
    assert report_month("3月期12月度", date(2026, 1, 8)) == (2025, 12)
    assert report_month("2026年7月度および2025年7月度", date(2026, 8, 5)) == (2026, 7)
    assert report_month("2026年3月期7月度", date(2026, 8, 5)) == (2026, 7)
    assert report_month("売上速報", date(2026, 8, 5)) is None
    assert estimate_month(date(2026, 8, 5)) == (2026, 7)
    assert estimate_month(date(2026, 9, 14)) == (2026, 8)
    july_end = date(2026, 7, 31)
    assert estimate_month(july_end) == (2026, 6)
    assert (date(2026, 9, 14) - date(2026, 7, 31)).days == 45
    assert estimate_month(date(2026, 9, 15)) == (2026, 8)


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE monthly_values (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          element TEXT NOT NULL,
          month INTEGER NOT NULL,
          value REAL NOT NULL,
          PRIMARY KEY (code, year, element, month)
        );
        CREATE TABLE monthly_element_sources (
          code TEXT, year INTEGER, element TEXT, first_seen_at TEXT
        );
        CREATE TABLE statements (
          code TEXT, disclosed_date TEXT, disclosed_time TEXT,
          type_of_document TEXT, disclosure_no TEXT
        );
        CREATE TABLE tdnet_pdf_documents (
          code TEXT, disclosed_date TEXT, title TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO monthly_values VALUES (?,?,?,?,?)",
        [
            ("72030", 2026, "売上高", 7, 1.0),
            ("72030", 2026, "売上高", 6, 1.0),
            ("72030", 2026, "売上高", 4, 1.0),
            ("72030", 2026, "売上高", 3, 1.0),
            ("72030", 2026, "売上高", 2, 1.0),
            ("72030", 2026, "売上高", 9, 1.0),
            ("72030", 2025, "売上高", 12, 1.0),
        ],
    )
    con.execute(
        "INSERT INTO monthly_element_sources VALUES (?,?,?,?)",
        ("72030", 2026, "売上高", "2020-01-01T00:00:00"),
    )
    con.execute(
        "INSERT INTO statements VALUES (?,?,?,?,?)",
        ("72030", "2026-05-08", "11:00:00", "2026年4月度 月次売上高", "disc-april"),
    )
    con.execute(
        "INSERT INTO statements VALUES (?,?,?,?,?)",
        ("72030", "2026-03-10", "15:00:00", "決算短信", "disc-earnings"),
    )
    con.execute(
        "INSERT INTO tdnet_pdf_documents VALUES (?,?,?)",
        ("72030", "2026-02-20", "2026年1月度 決算短信"),
    )
    con.commit()
    con.close()


def _pdf(folder: Path, name: str, pubdate: str | None = None) -> None:
    path = folder / name
    path.write_bytes(b"%PDF")
    if pubdate is not None:
        (path.with_name(path.name + ".meta.json")).write_text(
            json.dumps({"pubdate": pubdate}), encoding="utf-8",
        )


def _files(dest: Path) -> None:
    folder = dest / "7203"
    folder.mkdir(parents=True)
    _pdf(folder, "20260803_7203_会社_売上速報.pdf")
    _pdf(folder, "20260805_7203_会社_２０２６年７月度売上.pdf", "2026-08-05 15:30:00")
    _pdf(folder, "20260920_7203_会社_2026年7月度訂正.pdf", "2026-09-01 09:00:00")
    _pdf(folder, "20260708_7203_会社_3月期6月度.pdf")
    (folder / "20260708_7203_会社_3月期6月度.pdf-ingest.json").write_text(
        json.dumps({"columns": ["2026-05"]}), encoding="utf-8",
    )
    _pdf(folder, "20260410_7203_会社_売上速報.pdf")
    (folder / "20260720_7203_old.ingest.json").write_text(
        json.dumps({"as_of": "2026-06-30", "months": [4, 5, 6]}), encoding="utf-8",
    )
    (folder / "20261005_7203_page.ingest.json").write_text(
        json.dumps({"as_of": "2026-09-30"}), encoding="utf-8",
    )


def _stored(path: Path) -> dict[tuple[int, int], tuple]:
    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT year, month, basis, published_on, published_time, unknown_reason, confidence "
        "FROM monthly_publications WHERE code='72030'"
    ).fetchall()
    con.close()
    return {
        (year, month): (basis, on, clock, reason, confidence)
        for year, month, basis, on, clock, reason, confidence in rows
    }


def test_dry_run_writes_nothing_and_write_keeps_exact_apart_from_estimated(tmp_path: Path):
    db = tmp_path / "stock.db"
    dest = tmp_path / "monthly"
    _db(db)
    _files(dest)
    assert main(["--db", str(db), "--dest", str(dest)]) == 0
    con = sqlite3.connect(db)
    names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "monthly_publications" not in names

    assert main(["--db", str(db), "--dest", str(dest), "--write"]) == 0
    got = _stored(db)
    assert got[2026, 7] == ("tdnet", "2026-08-05", "15:30:00", None, "exact")
    assert got[2026, 6] == ("tdnet", "2026-07-08", None, None, "exact")
    assert got[2026, 4] == ("tdnet", "2026-05-08", "11:00:00", None, "exact")
    assert got[2026, 3] == ("tdnet", "2026-04-10", None, None, "estimated")
    assert got[2026, 9] == ("first_seen", "2026-10-05", None, None, "estimated")
    assert got[2026, 2] == ("unknown", None, None, "no_source", "unknown")
    assert got[2025, 12] == ("unknown", None, None, "no_source", "unknown")
    assert all(row[1] != "2020-01-01" for row in got.values())

    assert main(["--db", str(db), "--dest", str(dest), "--write"]) == 0
    assert _stored(db) == got


def test_an_existing_exact_date_is_not_replaced_by_no_source(tmp_path: Path, capsys):
    db = tmp_path / "stock.db"
    _db(db)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE monthly_publications ("
        "code TEXT, year INTEGER, month INTEGER, published_on TEXT, published_time TEXT, "
        "basis TEXT, unknown_reason TEXT, source_ref TEXT, recorded_at TEXT, "
        "confidence TEXT, PRIMARY KEY (code, year, month))"
    )
    con.execute(
        "INSERT INTO monthly_publications VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "72030", 2026, 2, "2026-03-05", "15:00:00", "tdnet", None, "kept.pdf",
            "2026-03-05", "exact",
        ),
    )
    con.commit()
    con.close()
    assert main(["--db", str(db), "--dest", str(tmp_path / "empty"), "--write"]) == 0
    got = _stored(db)
    assert got[2026, 2] == ("tdnet", "2026-03-05", "15:00:00", None, "exact")
    text = capsys.readouterr().out
    assert "fraction_exact=" in text
    assert "dry-run" not in text

"""Cloud Agent monthly slice dump/replace. Isolated temp SQLite, no VPS."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".cursor"))
from sync_monthly_from_vps import dump_monthly_slice, replace_monthly


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _make_src(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE stocks (
          code TEXT PRIMARY KEY,
          name TEXT,
          sector17 TEXT,
          sector33 TEXT,
          market TEXT,
          scale_category TEXT,
          margin_type TEXT,
          product_category TEXT
        );
        CREATE TABLE daily_prices (
          code TEXT,
          date TEXT,
          adj_close REAL,
          PRIMARY KEY (code, date)
        );
        CREATE TABLE monthly_issuers (
          code TEXT PRIMARY KEY,
          industry TEXT NOT NULL,
          last_ingested_at TEXT
        );
        CREATE TABLE monthly_tables (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          PRIMARY KEY (code, year)
        );
        CREATE TABLE monthly_elements (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          element TEXT NOT NULL,
          sort_order INTEGER NOT NULL,
          unit TEXT NOT NULL,
          PRIMARY KEY (code, year, element)
        );
        CREATE TABLE monthly_values (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          element TEXT NOT NULL,
          month INTEGER NOT NULL,
          value REAL NOT NULL,
          PRIMARY KEY (code, year, element, month)
        );
        """
    )
    con.execute(
        "INSERT INTO stocks VALUES (?,?,?,?,?,?,?,?)",
        ("262A0", "Zoff", "", "小売業", "プライム", "", "", ""),
    )
    con.execute(
        "INSERT INTO daily_prices VALUES (?,?,?)",
        ("262A0", "2026-09-16", 1234.0),
    )
    con.execute(
        "INSERT INTO monthly_issuers VALUES (?,?,?)",
        ("262A0", "専門店", "2026-09-04T19:14:31"),
    )
    con.execute("INSERT INTO monthly_tables VALUES (?,?)", ("262A0", 2026))
    con.execute(
        "INSERT INTO monthly_elements VALUES (?,?,?,?,?)",
        ("262A0", 2026, "Zoff 全店 売上高(前年比)", 0, "yoy_index"),
    )
    con.execute(
        "INSERT INTO monthly_values VALUES (?,?,?,?,?)",
        ("262A0", 2026, "Zoff 全店 売上高(前年比)", 7, 92.9),
    )
    con.commit()
    con.close()


def _make_dest(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE stocks (
          code TEXT PRIMARY KEY,
          name TEXT,
          sector17 TEXT,
          sector33 TEXT,
          market TEXT,
          scale_category TEXT,
          margin_type TEXT,
          product_category TEXT
        );
        CREATE TABLE daily_prices (
          code TEXT,
          date TEXT,
          adj_close REAL,
          PRIMARY KEY (code, date)
        );
        CREATE TABLE monthly_issuers (
          code TEXT PRIMARY KEY,
          industry TEXT NOT NULL,
          last_ingested_at TEXT
        );
        CREATE TABLE monthly_tables (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          PRIMARY KEY (code, year)
        );
        CREATE TABLE monthly_elements (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          element TEXT NOT NULL,
          sort_order INTEGER NOT NULL,
          unit TEXT NOT NULL,
          PRIMARY KEY (code, year, element)
        );
        CREATE TABLE monthly_values (
          code TEXT NOT NULL,
          year INTEGER NOT NULL,
          element TEXT NOT NULL,
          month INTEGER NOT NULL,
          value REAL NOT NULL,
          PRIMARY KEY (code, year, element, month)
        );
        """
    )
    con.execute(
        "INSERT INTO stocks VALUES (?,?,?,?,?,?,?,?)",
        ("72030", "トヨタ自動車", "", "輸送用機器", "プライム", "", "", ""),
    )
    con.execute(
        "INSERT INTO daily_prices VALUES (?,?,?)",
        ("72030", "2026-09-16", 3000.0),
    )
    con.execute(
        "INSERT INTO monthly_issuers VALUES (?,?,?)",
        ("99990", "その他", None),
    )
    con.execute("INSERT INTO monthly_tables VALUES (?,?)", ("99990", 2020))
    con.commit()
    con.close()


def test_sync_monthly_from_vps() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        src = tmp_p / "prod.db"
        dest = tmp_p / "local.db"
        slice_path = tmp_p / "slice.sqlite"
        _make_src(src)
        _make_dest(dest)
        dumped = dump_monthly_slice(src, slice_path)
        if dumped["monthly_values"] != 1 or dumped["distinct_codes"] != 1:
            _fail(f"dump counts: {dumped!r}")
        slice_con = sqlite3.connect(slice_path)
        if "daily_prices" in {
            r[0] for r in slice_con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }:
            _fail("slice must not include daily_prices")
        slice_con.close()
        replaced = replace_monthly(dest, slice_path)
        if replaced["monthly_values"] != 1:
            _fail(f"replace counts: {replaced!r}")
        dest_con = sqlite3.connect(dest)
        if dest_con.execute("SELECT COUNT(*) FROM monthly_issuers").fetchone()[0] != 1:
            _fail("stale monthly_issuers must be replaced")
        if dest_con.execute(
            "SELECT value FROM monthly_values WHERE month=7"
        ).fetchone()[0] != 92.9:
            _fail("yoy cell")
        if dest_con.execute(
            "SELECT COUNT(*) FROM daily_prices WHERE code='72030'"
        ).fetchone()[0] != 1:
            _fail("must not touch local prices")
        if dest_con.execute(
            "SELECT name FROM stocks WHERE code='72030'"
        ).fetchone()[0] != "トヨタ自動車":
            _fail("must not overwrite existing stocks")
        if dest_con.execute(
            "SELECT name FROM stocks WHERE code='262A0'"
        ).fetchone()[0] != "Zoff":
            _fail("missing monthly stock row")
        dest_con.close()
        again = replace_monthly(dest, slice_path)
        if again["monthly_values"] != 1:
            _fail(f"second replace: {again!r}")
    print("ok: sync_monthly_from_vps")


if __name__ == "__main__":
    test_sync_monthly_from_vps()

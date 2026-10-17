#!/usr/bin/env python3
"""Copy production monthly_* into a Cloud Agent stock.db. Read-only on src."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

MONTHLY_TABLES = (
    "monthly_issuers",
    "monthly_tables",
    "monthly_elements",
    "monthly_values",
)
# 本番に無いこともある（作られる前のデプロイ）。両方にあるときだけ写す
OPTIONAL_TABLES = ("monthly_element_sources",)
DELETE_ORDER = (
    "monthly_element_sources",
    "monthly_values",
    "monthly_elements",
    "monthly_tables",
    "monthly_issuers",
)


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _has(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone() is not None


def _table_sql(con: sqlite3.Connection, table: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row or not row[0]:
        raise SystemExit(f"missing table {table}")
    return row[0]


def dump_monthly_slice(src_path: Path, slice_path: Path) -> dict[str, int]:
    slice_path.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(slice_path)
    counts: dict[str, int] = {}
    try:
        src.execute("BEGIN")
        for table in MONTHLY_TABLES + tuple(t for t in OPTIONAL_TABLES if _has(src, t)):
            dst.execute(_table_sql(src, table))
            cols = _cols(src, table)
            colsql = ",".join(cols)
            rows = list(src.execute(f"SELECT {colsql} FROM {table}"))
            if rows:
                q = ",".join("?" * len(cols))
                dst.executemany(
                    f"INSERT INTO {table} ({colsql}) VALUES ({q})", rows,
                )
            counts[table] = len(rows)
        codes = [
            r[0] for r in src.execute("SELECT DISTINCT code FROM monthly_tables")
        ]
        counts["distinct_codes"] = len(codes)
        dst.execute(_table_sql(src, "stocks"))
        if codes:
            stock_cols = _cols(src, "stocks")
            colsql = ",".join(stock_cols)
            q_row = ",".join("?" * len(stock_cols))
            ph = ",".join("?" * len(codes))
            rows = list(src.execute(
                f"SELECT {colsql} FROM stocks WHERE code IN ({ph})", codes,
            ))
            dst.executemany(
                f"INSERT INTO stocks ({colsql}) VALUES ({q_row})", rows,
            )
            counts["stocks_for_monthly"] = len(rows)
        else:
            counts["stocks_for_monthly"] = 0
        src.rollback()
        dst.commit()
    finally:
        dst.close()
        src.close()
    return counts


def replace_monthly(dest_path: Path, slice_path: Path) -> dict[str, int]:
    src = sqlite3.connect(f"file:{slice_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_path, timeout=30)
    dst.execute("PRAGMA busy_timeout=30000")
    counts: dict[str, int] = {}
    tables = MONTHLY_TABLES + tuple(t for t in OPTIONAL_TABLES if _has(src, t) and _has(dst, t))
    try:
        for table in tables:
            if _cols(src, table) != _cols(dst, table):
                raise SystemExit(
                    f"schema mismatch {table}: "
                    f"slice={_cols(src, table)} dest={_cols(dst, table)}"
                )
        stock_common = [c for c in _cols(dst, "stocks") if c in _cols(src, "stocks")]
        dst.execute("BEGIN IMMEDIATE")
        for table in DELETE_ORDER:
            # 本番に無い表でも手元の古い分は消す（行と食い違わないように）
            if table in tables or (table in OPTIONAL_TABLES and _has(dst, table)):
                dst.execute(f"DELETE FROM {table}")
        if stock_common:
            colsql = ",".join(stock_common)
            q = ",".join("?" * len(stock_common))
            rows = list(src.execute(f"SELECT {colsql} FROM stocks"))
            dst.executemany(
                f"INSERT OR IGNORE INTO stocks ({colsql}) VALUES ({q})", rows,
            )
            counts["stocks_inserted"] = dst.execute("SELECT changes()").fetchone()[0]
        for table in tables:
            cols = _cols(src, table)
            colsql = ",".join(cols)
            q = ",".join("?" * len(cols))
            rows = list(src.execute(f"SELECT {colsql} FROM {table}"))
            if rows:
                dst.executemany(
                    f"INSERT INTO {table} ({colsql}) VALUES ({q})", rows,
                )
            counts[table] = len(rows)
        dst.commit()
    except Exception:
        dst.rollback()
        raise
    finally:
        src.close()
        dst.close()
    return counts


def _print_counts(counts: dict[str, int]) -> None:
    for key, n in counts.items():
        print(f"{key}\t{n}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] not in ("dump", "replace"):
        print(
            "usage: sync_monthly_from_vps.py dump SRC SLICE\n"
            "       sync_monthly_from_vps.py replace DEST SLICE",
            file=sys.stderr,
        )
        return 2
    cmd, left, right = args
    if cmd == "dump":
        _print_counts(dump_monthly_slice(Path(left), Path(right)))
        return 0
    _print_counts(replace_monthly(Path(left), Path(right)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

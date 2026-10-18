#!/usr/bin/env python3
"""Copy stocks (and optionally statements) into a verify SQLite DB.

Reads the source. Never writes prod stock.db (repo ./stock.db or
/workspace/verify-live/stock.db). Target must be a different path.

  python tools/xbrl/copy_stocks_for_verify.py --from PATH --to PATH
  python tools/xbrl/copy_stocks_for_verify.py --from PATH --to PATH --tables stocks,statements
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_DEST = (
    ROOT / "stock.db",
    Path("/workspace/verify-live/stock.db"),
)


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def refuse_dest(source: Path, dest: Path) -> str | None:
    src = _resolve(source)
    dst = _resolve(dest)
    if not src.is_file():
        return f"source is not a file: {src}"
    if dst == src:
        return f"dest equals source: {dst}"
    forbidden = {_resolve(p) for p in FORBIDDEN_DEST}
    if dst in forbidden:
        return f"refusing to write prod stock.db: {dst}"
    return None


def copy_table(source: Path, dest: Path, table: str) -> int:
    src = _resolve(source)
    dst = _resolve(dest)
    err = refuse_dest(src, dst)
    if err:
        raise SystemExit(err)
    if table not in {"stocks", "statements"}:
        raise SystemExit(f"refusing table {table!r} (stocks/statements only)")
    dst.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(dst.as_uri(), uri=True)
    try:
        src_uri = src.as_uri() + "?mode=ro"
        conn.execute("ATTACH DATABASE ? AS src", (src_uri,))
        row = conn.execute(
            "SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None or not row[0]:
            raise SystemExit(f"source has no {table} table: {src}")
        src_cols = [r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")]
        if not src_cols:
            raise SystemExit(f"source {table} table has no columns: {src}")
        dest_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if dest_exists is None:
            conn.execute(row[0])
        dest_cols = {r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")}
        cols = [c for c in src_cols if c in dest_cols]
        if not cols:
            raise SystemExit(f"no overlapping {table} columns to copy")
        col_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
        conn.execute(f"DELETE FROM main.{table}")
        conn.execute(
            f"INSERT INTO main.{table} ({col_sql}) SELECT {col_sql} FROM src.{table}"
        )
        n = conn.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        conn.commit()
        return int(n)
    finally:
        conn.close()


def copy_stocks(source: Path, dest: Path) -> int:
    return copy_table(source, dest, "stocks")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", "--source", dest="source", required=True, help="source stock.db (read only)")
    ap.add_argument("--to", "--dest-db", dest="dest", required=True, help="verify DB to write")
    ap.add_argument(
        "--tables",
        default="stocks",
        help="comma-separated: stocks (default) and/or statements",
    )
    args = ap.parse_args(argv)
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    bits = []
    for table in tables:
        n = copy_table(Path(args.source), Path(args.dest), table)
        bits.append(f"{n} {table}")
    print(
        f"copied {', '.join(bits)} from {Path(args.source).resolve()} "
        f"to {Path(args.dest).resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

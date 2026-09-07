#!/usr/bin/env python3
"""Copy ONLY the stocks table into a verify SQLite DB.

Reads the source. Never writes prod stock.db (repo ./stock.db or
/workspace/verify-live/stock.db). Target must be a different path.

  python scripts/copy_stocks_for_verify.py --from PATH --to PATH
  python scripts/copy_stocks_for_verify.py --source PATH --dest-db PATH
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def copy_stocks(source: Path, dest: Path) -> int:
    src = _resolve(source)
    dst = _resolve(dest)
    err = refuse_dest(src, dst)
    if err:
        raise SystemExit(err)
    dst.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(dst.as_uri(), uri=True)
    try:
        src_uri = src.as_uri() + "?mode=ro"
        conn.execute("ATTACH DATABASE ? AS src", (src_uri,))
        row = conn.execute(
            "SELECT sql FROM src.sqlite_master WHERE type='table' AND name='stocks'"
        ).fetchone()
        if row is None or not row[0]:
            raise SystemExit(f"source has no stocks table: {src}")
        src_cols = [r[1] for r in conn.execute("PRAGMA src.table_info(stocks)")]
        if not src_cols:
            raise SystemExit(f"source stocks table has no columns: {src}")
        dest_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stocks'"
        ).fetchone()
        if dest_exists is None:
            conn.execute(row[0])
        dest_cols = {r[1] for r in conn.execute("PRAGMA main.table_info(stocks)")}
        cols = [c for c in src_cols if c in dest_cols]
        if not cols:
            raise SystemExit("no overlapping stocks columns to copy")
        col_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
        conn.execute("DELETE FROM main.stocks")
        conn.execute(
            f"INSERT INTO main.stocks ({col_sql}) SELECT {col_sql} FROM src.stocks"
        )
        n = conn.execute("SELECT COUNT(*) FROM main.stocks").fetchone()[0]
        conn.commit()
        return int(n)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", "--source", dest="source", required=True, help="source stock.db (read only)")
    ap.add_argument("--to", "--dest-db", dest="dest", required=True, help="verify DB to write (stocks only)")
    args = ap.parse_args(argv)
    n = copy_stocks(Path(args.source), Path(args.dest))
    print(f"copied {n} stocks from {Path(args.source).resolve()} to {Path(args.dest).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

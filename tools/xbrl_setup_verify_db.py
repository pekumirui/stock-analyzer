#!/usr/bin/env python3
"""Create stock_verify.db (xbrl_* schema + stocks copy). Never writes to --source-db."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def copy_stocks(source: Path, dest: Path) -> int:
    src = _connect_ro(source)
    try:
        names = {
            r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "stocks" not in names:
            raise SystemExit(f"source DB has no stocks table: {source}")
        cols_src = [r[1] for r in src.execute("PRAGMA table_info(stocks)")]
        rows = src.execute(
            f"SELECT {', '.join(cols_src)} FROM stocks"
        ).fetchall()
        if not rows:
            raise SystemExit(f"source DB stocks is empty: {source}")
    finally:
        src.close()

    dst = sqlite3.connect(str(dest))
    try:
        cols_dst = [r[1] for r in dst.execute("PRAGMA table_info(stocks)")]
        use = [c for c in cols_src if c in cols_dst]
        if "code" not in use:
            raise SystemExit("verify DB stocks table has no code column")
        idx = [cols_src.index(c) for c in use]
        payload = [tuple(row[i] for i in idx) for row in rows]
        placeholders = ",".join("?" * len(use))
        colsql = ",".join(use)
        dst.execute("DELETE FROM stocks")
        dst.executemany(
            f"INSERT INTO stocks ({colsql}) VALUES ({placeholders})",
            payload,
        )
        dst.commit()
        return len(payload)
    finally:
        dst.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=str(ROOT / "stock_verify.db"),
        help="verify DB path (default: repo root stock_verify.db)",
    )
    ap.add_argument(
        "--source-db",
        default=str(ROOT / "stock.db"),
        help="read-only source DB for stocks master (default: stock.db)",
    )
    args = ap.parse_args()

    dest = Path(args.db).expanduser()
    if not dest.is_absolute():
        dest = (Path.cwd() / dest).resolve()
    else:
        dest = dest.resolve()
    source = Path(args.source_db).expanduser()
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    else:
        source = source.resolve()

    forbidden = {(ROOT / "stock.db").resolve()}
    live = Path("/workspace/verify-live/stock.db")
    try:
        forbidden.add(live.resolve())
    except OSError:
        pass
    if dest in forbidden:
        print("refusing to write to production stock.db", file=sys.stderr)
        return 1
    if dest == source:
        print("refusing to use source DB as verify DB", file=sys.stderr)
        return 1
    if not source.is_file():
        print(f"source DB not found: {source}", file=sys.stderr)
        return 1

    dest.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite:///{dest}"
    sys.path.insert(0, str(ROOT))
    from app.db import engine, init_db  # noqa: E402

    init_db()
    engine.dispose()
    n = copy_stocks(source, dest)
    print(f"verify db: {dest}")
    print(f"source (read-only): {source}")
    print(f"copied stocks: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

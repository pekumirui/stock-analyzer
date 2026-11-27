from __future__ import annotations

import argparse
import gzip
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.prodslice.plan import PLAN, PROD_USER_VERSION, Skip, parse_codes

RO_HOST = "stock-vps-ro"
FULL_HOST = "stock-vps"
REMOTE_COMMAND = "/home/stock/stock-analyzer/deploy/prod_ro_command.sh"
FIXED_DB_VERSIONS = (1, PROD_USER_VERSION)
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
MUX_OPTS = ("-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/cm-prod-ro-%C", "-o", "ControlPersist=300")


def _configured(host: str) -> bool:
    out = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, check=False)
    return out.returncode == 0 and f"hostname {host}\n" not in out.stdout


def ssh_command(words: list[str], *, mux: bool = False) -> list[str]:
    host = os.environ.get("PROD_RO_SSH")
    if host:
        remote = words
    elif _configured(RO_HOST):
        host, remote = RO_HOST, words
    elif _configured(FULL_HOST):
        host, remote = FULL_HOST, [shlex.quote(w) for w in (REMOTE_COMMAND, *words)]
    else:
        raise SystemExit(f"本番につなぐ ssh の設定が無い（{RO_HOST} か {FULL_HOST}）。deploy/README.md「本番の読み取り」")
    return ["ssh", *SSH_OPTS, *(MUX_OPTS if mux else ()), host, *remote]


def fetch_slice(codes: list[str], out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        packed = Path(tmp) / "slice.sqlite.gz"
        with packed.open("wb") as f:
            subprocess.run(ssh_command(["slice", ",".join(codes)]), stdout=f, check=True)
        with gzip.open(packed, "rb") as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def _db_path() -> Path:
    from app.db import engine
    return Path(engine.url.database).resolve()


def _columns(con: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")]


def guard(db: Path, force: bool) -> None:
    if force or not db.exists():
        return
    con = sqlite3.connect(db)
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        has_prices = bool(_columns(con, "main", "daily_prices")) and \
            con.execute("SELECT 1 FROM daily_prices LIMIT 1").fetchone() is not None
    finally:
        con.close()
    if version not in FIXED_DB_VERSIONS and has_prices:
        raise SystemExit(f"{db} は backfill した DB に見えるので上書きしない（--force で上書き）")


def load(slice_db: Path, db: Path) -> dict[str, int]:
    from app.db import init_db
    init_db()
    con = sqlite3.connect(db, isolation_level=None)
    counts: dict[str, int] = {}
    try:
        con.execute("ATTACH DATABASE ? AS s", (str(slice_db),))
        con.execute("BEGIN")
        for table, rule in PLAN:
            mine = _columns(con, "main", table)
            if not mine or isinstance(rule, Skip):
                continue
            con.execute(f"DELETE FROM main.{table}")
            theirs = set(_columns(con, "s", table))
            cols = ",".join(c for c in mine if c in theirs)
            if not cols:
                continue
            con.execute(f"INSERT OR REPLACE INTO main.{table} ({cols}) SELECT {cols} FROM s.{table}")
            counts[table] = con.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        con.execute(f"PRAGMA user_version = {PROD_USER_VERSION}")
        con.execute("COMMIT")
    finally:
        con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="./run.sh prod", description="本番の DB から銘柄を切り出して stock.db を作り直す")
    p.add_argument("codes", nargs="+", help="銘柄コード（4 桁か 5 桁。カンマ区切りも可）")
    p.add_argument("--force", action="store_true", help="backfill した DB でも上書きする")
    args = p.parse_args(argv)
    codes = parse_codes(",".join(args.codes))
    db = _db_path()
    guard(db, args.force)
    with tempfile.TemporaryDirectory() as tmp:
        slice_db = Path(tmp) / "slice.sqlite"
        fetch_slice(codes, slice_db)
        counts = load(slice_db, db)
    shown = ", ".join(f"{t} {n}" for t, n in counts.items() if n)
    print(f"本番から {', '.join(codes)} を切り出して {db} に入れた: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

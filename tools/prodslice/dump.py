from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from tools.prodslice.plan import PLAN, ByCode, ChildOf, CopyAll, Rule, UsedBy, parse_codes


def _columns(con: sqlite3.Connection, schema: str, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")}


def _where(con: sqlite3.Connection, table: str, rule: Rule, codes: list[str]) -> tuple[str, list[str]] | None:
    have = _columns(con, "src", table)
    if isinstance(rule, CopyAll):
        return "", []
    if isinstance(rule, ByCode):
        cols = [c for c in rule.columns if c in have]
        if not cols:
            return None
        marks = ",".join("?" * len(codes))
        return " WHERE " + " OR ".join(f"{c} IN ({marks})" for c in cols), codes * len(cols)
    if isinstance(rule, ChildOf):
        if rule.column not in have or rule.parent_column not in _columns(con, "main", rule.parent):
            return None
        return f" WHERE {rule.column} IN (SELECT {rule.parent_column} FROM main.{rule.parent})", []
    if isinstance(rule, UsedBy):
        parts = [f"SELECT {c} FROM main.{t}" for t, c in rule.sources if c in _columns(con, "main", t)]
        if rule.column not in have or not parts:
            return None
        return f" WHERE {rule.column} IN ({' UNION '.join(parts)})", []
    return None


def dump(src: Path, out: Path, codes: list[str]) -> dict[str, int]:
    out.unlink(missing_ok=True)
    con = sqlite3.connect(f"file:{out}", uri=True, isolation_level=None)
    counts: dict[str, int] = {}
    try:
        con.execute("ATTACH DATABASE ? AS src", (f"file:{src}?mode=ro",))
        con.execute("BEGIN")
        for table, rule in PLAN:
            if not _columns(con, "src", table):
                continue
            where = _where(con, table, rule, codes)
            if where is None:
                continue
            con.execute(f"CREATE TABLE main.{table} AS SELECT * FROM src.{table}{where[0]}", where[1])
            counts[table] = con.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        con.execute("COMMIT")
    finally:
        con.close()
    return counts


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m tools.prodslice.dump SRC_DB OUT_DB CODES", file=sys.stderr)
        return 2
    counts = dump(Path(argv[0]), Path(argv[1]), parse_codes(argv[2]))
    print(json.dumps(counts, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

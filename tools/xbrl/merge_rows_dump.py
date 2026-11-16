"""行をまとめる変更（KAB-476）の新旧の表を、目で見るために TSV で出す（DB は読み取り専用。マージしない）。

  PYTHONPATH=. python /tmp/merge_rows_dump.py --new /tmp/new_ts.py --exclusions /tmp/ex.json --db /path/to/stock.db --code 16050 --code 76850
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.config as app_config
from app.xbrl import three_statements as old


def dump(tag: str, code: str, statement: str, table: dict) -> None:
    periods = table["periods"]
    print(f"### {tag}\t{code}\t{statement}")
    print("\t".join(["section", "label", "key", "merged_keys", *(p["label"] for p in periods)]))
    print("\t".join(["", "会計基準", "", "", *(str(p["accounting_standard"]) for p in periods)]))
    for row in table["rows"]:
        cells = ["" if c is None else f"{c['value'] / 1e6:.0f}" for c in row["cells"]]
        section = f"{row['group'] or '-'}>{row['section'] or '-'}"
        print("\t".join([section, row["label"], row["key"], ",".join(row.get("merged_keys") or []), *cells]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", required=True)
    parser.add_argument("--exclusions", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--code", action="append", required=True)
    args = parser.parse_args(argv)
    app_config.XBRL_ROW_MERGE_EXCLUSIONS_PATH = Path(args.exclusions)
    spec = importlib.util.spec_from_file_location("ts_new", args.new)
    new = importlib.util.module_from_spec(spec)
    sys.modules["ts_new"] = new
    spec.loader.exec_module(new)
    db = Session(create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true"))
    for code in args.code:
        for statement in old.STATEMENTS:
            dump("old", code, statement, old.financial_table(db, code, statement=statement, periods=old.MAX_PERIODS))
            dump("new", code, statement, new.financial_table(db, code, statement=statement, periods=new.MAX_PERIODS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

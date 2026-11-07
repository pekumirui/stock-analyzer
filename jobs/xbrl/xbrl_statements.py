"""短信 XBRL から statements の行を作る（KAB-383。app/xbrl/statements.py）。

使い方:
  python jobs/xbrl/xbrl_statements.py
  python jobs/xbrl/xbrl_statements.py --since 2026-08-01
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.xbrl.statements import sync_statements  # noqa: E402
from jobs._r2common import jst_today  # noqa: E402

DEFAULT_DAYS = 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=date.fromisoformat,
                    help=f"YYYY-MM-DD 以降に提出された短信。省略時は直近 {DEFAULT_DAYS} 日")
    args = ap.parse_args(argv)
    since = args.since or jst_today() - timedelta(days=DEFAULT_DAYS - 1)
    db = SessionLocal()
    try:
        stats = sync_statements(db, since)
        print(f"xbrl_statements since={since.isoformat()} {stats}", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

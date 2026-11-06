"""Normalize parsed XBRL facts into xbrl_financials.

使い方:
  python jobs/xbrl/xbrl_normalize.py
  python jobs/xbrl/xbrl_normalize.py --rebuild
  python jobs/xbrl/xbrl_normalize.py --rebuild --code 72030
  python jobs/xbrl/xbrl_normalize.py --since 2026-08-01 --source tdnet
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="xbrl_financials / xbrl_segments / 大株主を消して parsed filing を全部やり直す")
    ap.add_argument("--code", help="5桁コードに限定（--rebuild と併用可）")
    ap.add_argument("--since", type=date.fromisoformat,
                    help="YYYY-MM-DD 以降に提出された parsed filing を、消さずに 1 件ずつやり直す")
    ap.add_argument("--source", choices=["tdnet", "tdnet_pdf", "edinet"], help="filing の source に限定")
    args = ap.parse_args()
    if args.rebuild and args.since:
        ap.error("--rebuild と --since は一緒に使えない")
    db = SessionLocal()
    try:
        apply_mappings(db)
        stats = run_normalize(db, rebuild=args.rebuild, code=args.code, since=args.since, source=args.source)
        print(f"normalize {stats}")
        return 0 if stats.get("errors", 0) == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

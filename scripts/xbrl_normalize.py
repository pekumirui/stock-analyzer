"""Normalize parsed XBRL facts into xbrl_financials.

使い方:
  python scripts/xbrl_normalize.py
  python scripts/xbrl_normalize.py --rebuild
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        stats = run_normalize(db, rebuild=args.rebuild)
        print(f"normalize {stats}")
        return 0 if stats.get("errors", 0) == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

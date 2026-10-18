"""Normalize parsed XBRL facts into xbrl_financials.

使い方:
  python jobs/xbrl/xbrl_normalize.py
  python jobs/xbrl/xbrl_normalize.py --rebuild
  python jobs/xbrl/xbrl_normalize.py --rebuild --code 72030
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="xbrl_financials を消して parsed filing を全部やり直す")
    ap.add_argument("--code", help="5桁コードに限定（--rebuild と併用可）")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        apply_mappings(db)
        stats = run_normalize(db, rebuild=args.rebuild, code=args.code)
        print(f"normalize {stats}")
        return 0 if stats.get("errors", 0) == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

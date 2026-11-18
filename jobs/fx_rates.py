"""Frankfurter ECB 日次レートを fx_rates へ UPSERT。

使い方:
  python jobs/fx_rates.py                # 最終保存日以降の差分（空なら 1999-01-04〜）
  python jobs/fx_rates.py --backfill     # 1999-01-04 から今日まで（冪等）
  python jobs/fx_rates.py --from 2024-01-01 --to 2024-12-31
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.fx import FX_EARLIEST, ingest_range, resolve_start


def _d(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--backfill",
        action="store_true",
        help="1999-01-04 から再取得（UPSERT・冪等）",
    )
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    args = ap.parse_args()

    today = date.today()
    start_arg = _d(args.date_from)
    end = _d(args.date_to) or today
    if end > today:
        end = today

    db = SessionLocal()
    try:
        start = resolve_start(
            db, backfill=args.backfill, start=start_arg, today=today,
        )
        if end < start:
            print(f"fx skip (end {end.isoformat()} < start {start.isoformat()})")
            return 0
        print(
            f"fx ingest {start.isoformat()}..{end.isoformat()} "
            f"(earliest={FX_EARLIEST.isoformat()})"
        )
        stats = ingest_range(db, start, end)
        print(
            f"fx done days={stats['days']} rows={stats['rows']} "
            f"chunks={stats['chunks']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

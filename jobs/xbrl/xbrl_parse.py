"""Parse TDnet XBRL ZIPs from R2 dest into xbrl_* tables.

使い方:
  python jobs/xbrl/xbrl_parse.py
  python jobs/xbrl/xbrl_parse.py --from 2026-01-01 --to 2026-09-05
  python jobs/xbrl/xbrl_parse.py --doc-id 081220260805509698
  python jobs/xbrl/xbrl_parse.py --retry-failed
  python jobs/xbrl/xbrl_parse.py --reparse
  python jobs/xbrl/xbrl_parse.py load-mappings
  python jobs/xbrl/xbrl_parse.py report-unmapped --limit 50
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs._r2common import fail_if_mount_down  # noqa: E402
from app.config import XBRL_DEST  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.xbrl.jobs import report_unmapped, run_parse  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402


def _d(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="parse", choices=["parse", "load-mappings", "report-unmapped"])
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    ap.add_argument("--doc-id")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument(
        "--reparse",
        action="store_true",
        help="parsed/skipped で sha が同じ ZIP も再パースする（KAB-48 の Summary 取りこぼし直し）",
    )
    ap.add_argument("--dest", default=XBRL_DEST)
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.command == "load-mappings":
            stats = apply_mappings(db)
            print(f"load-mappings std={stats['std_items']} mappings={stats['mappings']} created={stats['created']} deleted={stats['deleted']}")
            return 0
        if args.command == "report-unmapped":
            rows = report_unmapped(db, limit=args.limit)
            for row in rows:
                print(
                    f"{row['fact_count']:6d}  {row['namespace_key']}:{row['local_name']}  {row['label_ja'] or ''}",
                    flush=True,
                )
            print(f"unmapped monetary elements: {len(rows)}")
            return 0
        dest = Path(args.dest)
        rc = fail_if_mount_down("xbrl_parse", dest)
        if rc is not None:
            return rc
        stats = run_parse(
            db,
            dest=dest,
            start=_d(args.date_from),
            end=_d(args.date_to),
            doc_id=args.doc_id,
            retry_failed=args.retry_failed,
            reparse=args.reparse,
        )
        print(f"parse processed={stats['processed']} errors={stats['errors']} skipped={stats['skipped']}")
        return 0 if stats["errors"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

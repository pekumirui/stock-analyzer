"""上場会社のホームページ URL を homepages へ（KAB-319）。

使い方:
  python jobs/market/homepages.py --code 4543 --code 9501 --dry-run
  python jobs/market/homepages.py --code 4543 --code 9501
  python jobs/market/homepages.py --all
  python jobs/market/homepages.py --code 4543 --refresh

表 homepages はデプロイの jobs/migrate.py が作る。ここでは作らない。
本番の一括投入は KPI 承認後。この PR では --all を本番に走らせない。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.market.homepages import ordinary_code, sync_homepages  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Save each listed company's homepage URL")
    ap.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    ap.add_argument("--all", action="store_true", help="every ordinary stocks.code")
    ap.add_argument("--dry-run", action="store_true", help="print plan, do not write")
    ap.add_argument("--refresh", action="store_true", help="allow a weaker source to replace a stronger one")
    args = ap.parse_args(argv)
    if bool(args.all) == bool(args.code):
        ap.error("specify --code or --all")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    codes = None if args.all else [ordinary_code(c) for c in args.code]
    db = SessionLocal()
    try:
        report = sync_homepages(
            db,
            codes,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    finally:
        db.close()
    for line in report.lines:
        print(line, flush=True)
    print(report.summary(dry_run=args.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

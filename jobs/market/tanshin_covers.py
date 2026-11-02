"""決算短信の表紙を tanshin_covers に書く（KAB-325）。

使い方:
  python jobs/market/tanshin_covers.py --code 7203 --dry-run
  python jobs/market/tanshin_covers.py --code 7203
  python jobs/market/tanshin_covers.py --limit 20

表はデプロイの jobs/migrate.py が作る。ここでは作らない。
各社の最新の決算短信を1行書く。disclosure_no が主キーなので、次の短信が来ても前の行は残る。
--all は無い。本番への一括投入はしない。
説明資料 PDF の保存と、ホームページからの探索は別 issue。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.market.homepages import ordinary_code  # noqa: E402
from app.market.tanshin_cover import sync_covers  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Record cover flags from the latest earnings flash")
    ap.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    ap.add_argument("--limit", type=int, help="how many companies, newest filings first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if bool(args.code) == bool(args.limit):
        ap.error("specify --code or --limit")
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be >= 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    try:
        codes = [ordinary_code(code) for code in args.code] if args.code else None
        stored = sync_covers(db, codes, limit=args.limit, dry_run=args.dry_run)
    finally:
        db.close()
    for line in stored.lines:
        print(line, flush=True)
    print(stored.summary(dry_run=args.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

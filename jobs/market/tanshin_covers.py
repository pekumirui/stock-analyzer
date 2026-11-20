"""決算短信の表紙を tanshin_covers に書く（KAB-325）。

使い方:
  python jobs/market/tanshin_covers.py --code 7203 --dry-run
  python jobs/market/tanshin_covers.py --code 7203
  python jobs/market/tanshin_covers.py --limit 20
  python jobs/market/tanshin_covers.py --days 3

表はデプロイの jobs/migrate.py が作る。ここでは作らない。
各社の最新の決算短信を1行書く。disclosure_no が主キーなので、次の短信が来ても前の行は残る。
--days N は今日(JST)から遡る日数（当日を含む）に決算短信を出した会社。毎時の短信の取り込みのあとに 1、日次 cron は 3。
初回は --days を 1 四半期より長くして手で一度（どの会社も最新の短信が入る）。--all は無い。
説明資料 PDF の保存と、ホームページからの探索は別 issue。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.market.homepages import now_jst, ordinary_code
from app.market.tanshin_cover import sync_covers


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Record cover flags from the latest earnings flash")
    ap.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    ap.add_argument("--limit", type=int, help="how many companies, newest filings first")
    ap.add_argument("--days", type=int, help="companies that filed in the last N days JST (inclusive)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if sum((bool(args.code), args.limit is not None, args.days is not None)) != 1:
        ap.error("specify one of --code, --limit, --days")
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be >= 1")
    if args.days is not None and args.days < 1:
        ap.error("--days must be >= 1")
    return args


def since_from_days(days: int, now: datetime | None = None) -> datetime:
    today = (now or now_jst()).date()
    return datetime.combine(today - timedelta(days=days - 1), time.min)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    try:
        codes = [ordinary_code(code) for code in args.code] if args.code else None
        since = since_from_days(args.days) if args.days is not None else None
        stored = sync_covers(db, codes, limit=args.limit, since=since, dry_run=args.dry_run)
    finally:
        db.close()
    for line in stored.lines:
        print(line, flush=True)
    print(stored.summary(dry_run=args.dry_run), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

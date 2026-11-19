"""yfinance の 1 分足（直近 7 日）を data/intraday/1m/ の日ごとの Parquet へ（KAB-208）。

使い方:
  python jobs/market/intraday_1m.py                    # 内国株全銘柄
  python jobs/market/intraday_1m.py --codes 72030 67580 --sleep 0
  python jobs/market/intraday_1m.py --limit 300        # 先頭から 300 銘柄（試し取り）

cron は平日 20:30（deploy/crontab）。19:30 の日次バッチの後に回し、当日の AdjFactor が DB にある状態で
過去の日を埋める。設計は docs/jobs/market.md「分足」と app/market/intraday.py。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.market.intraday import collect, daily_context, fetch, store
from jobs.share_paths import share_path

DOMESTIC = "011"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--codes", nargs="*", help="J-Quants 5 桁コード。省略で内国株全銘柄")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.5, help="1 銘柄ごとの待ち（秒）")
    ap.add_argument("--dest", type=Path, default=None, help="既定は data/intraday/1m")
    args = ap.parse_args(argv)
    root = args.dest or share_path("intraday") / "1m"

    db = SessionLocal()
    try:
        codes = args.codes or [c for (c,) in db.execute(
            text("SELECT code FROM stocks WHERE product_category = :p ORDER BY code"), {"p": DOMESTIC})]
        if args.limit:
            codes = codes[:args.limit]
        print(f"開始 {len(codes)} 銘柄 → {root}")
        got, stats = collect(codes, fetch, sleep=args.sleep)
        asof, daily = daily_context(db, date.today() - timedelta(days=20))
    finally:
        db.close()
    res = store(root, got, asof, daily)
    print(f"完了 ok={stats['ok']} empty={stats['empty']} failed={stats['failed']} "
          f"rate_limited={stats['rate_limited']} days={res['days']} added={res['added']} "
          f"skipped={res['skipped']} asof={asof}")
    return 1 if stats["ok"] == 0 or stats["failed"] > len(codes) // 10 else 0


if __name__ == "__main__":
    raise SystemExit(main())

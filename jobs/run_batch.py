"""日次バッチ / 初回バックフィル。

使い方:
  python jobs/run_batch.py                # 当日取込+指標再計算
  python jobs/run_batch.py --date 2026-08-14
  python jobs/run_batch.py --backfill 5   # 初回: 5年分。失敗したら非ゼロ終了
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db
from app import ingest, analytics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="取得日 YYYY-MM-DD (省略時は今日)")
    ap.add_argument("--backfill", type=int, help="年数を指定して初回バックフィル")
    ap.add_argument("--no-recompute", action="store_true", help="指標の再計算をスキップ")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.backfill:
            ingest.run_backfill(db, years=args.backfill)
        else:
            ingest.run_daily(db, day=args.date)
        if not args.no_recompute:
            print("指標を再計算中 (MA/RS/ステージ)...")
            analytics.compute_indicators(db)
            print("決算後パフォーマンスを計算中...")
            analytics.compute_earnings_events(db)
        print("完了")
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""日次バッチ / 初回バックフィル。

使い方:
  python jobs/market/run_batch.py                # 当日取込+指標再計算
  python jobs/market/run_batch.py --date 2026-08-14
  python jobs/market/run_batch.py --backfill 5   # 初回: 5年分。失敗したら非ゼロ終了
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import SessionLocal, init_db
from app.market import ingest, analytics
from app.market.earnings_schedule import sync


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="取得日 YYYY-MM-DD (省略時は今日)")
    ap.add_argument("--backfill", type=int, help="年数を指定して初回バックフィル")
    ap.add_argument("--no-recompute", action="store_true", help="指標の再計算をスキップ")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        n_new = None
        if args.backfill:
            ingest.run_backfill(db, years=args.backfill)
        else:
            n_new = ingest.run_daily(db, day=args.date)
        if args.no_recompute:
            pass
        elif n_new == 0 and analytics.indicators_current(db):
            # 早朝の回など、取り込むものが無く前回の再計算も済んでいるとき
            print("新しい株価・決算が無いので再計算をスキップ")
        else:
            px = analytics.price_frame(db)
            db.commit()
            print("指標を再計算中 (MA/RS/ステージ)...")
            analytics.compute_indicators(db, px=px)
            print("決算後パフォーマンスを計算中...")
            analytics.compute_earnings_events(db, px=px)
        as_of = date.fromisoformat(args.date) if args.date else date.today()
        try:
            n_ed = sync(db, as_of)
        except Exception:
            print("決算発表予定の更新に失敗")
            raise
        print(f"決算発表予定 {n_ed}")
        print("完了")
    finally:
        db.close()


if __name__ == "__main__":
    main()

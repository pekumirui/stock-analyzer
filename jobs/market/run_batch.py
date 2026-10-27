"""日次バッチ / 初回バックフィル。

使い方:
  python jobs/market/run_batch.py                # 当日取込+指標再計算+発表予定（全部）
  python jobs/market/run_batch.py --only prices  # 株価・TOPIX → 指標・決算後パフォーマンス（cron 16:35）
  python jobs/market/run_batch.py --only fins    # 銘柄一覧・決算 → 決算後パフォーマンス（cron 18:05）
  python jobs/market/run_batch.py --only schedule  # 決算発表予定だけ（cron 10:10）
  python jobs/market/run_batch.py --date 2026-08-14
  python jobs/market/run_batch.py --backfill 5   # 初回: 5年分。失敗したら非ゼロ終了
  python jobs/market/run_batch.py --refetch-prices  # DB にある日足を全部取り直す（再開可）
  python jobs/market/run_batch.py --readjust-prices # 調整後株価を DB の中で全銘柄計算し直す
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db import SessionLocal, init_db
from app.market import ingest, analytics
from app.market.adjust import readjust
from app.market.earnings_schedule import sync


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="取得日 YYYY-MM-DD (省略時は今日)")
    ap.add_argument("--backfill", type=int, help="年数を指定して初回バックフィル")
    ap.add_argument("--refetch-prices", action="store_true",
                    help="DB にある日足を全部取り直して指標を計算し直す")
    ap.add_argument("--readjust-prices", action="store_true",
                    help="調整後株価を DB の調整前の値と調整係数から全銘柄計算し直して指標も計算し直す")
    ap.add_argument("--no-recompute", action="store_true", help="指標の再計算をスキップ")
    ap.add_argument("--only", choices=("prices", "fins", "schedule"),
                    help="日次の一部だけ（J-Quants の配信時刻ごとに cron を分ける）。省略で全部")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        n_new = None
        refetch_failed = 0
        if args.only == "schedule":
            pass
        elif args.only == "prices":
            n_new = ingest.run_prices(db, day=args.date)
        elif args.only == "fins":
            n_new = ingest.run_fins(db, day=args.date)
        elif args.refetch_prices:
            refetch_failed = ingest.run_refetch_prices(db)
        elif args.readjust_prices:
            print(f"調整後株価を計算し直し {readjust(db)} 行")
        elif args.backfill:
            ingest.run_backfill(db, years=args.backfill)
        else:
            n_new = ingest.run_daily(db, day=args.date)
        if args.no_recompute or args.only == "schedule":
            pass
        elif n_new == 0 and analytics.indicators_current(db):
            print("新しい株価・決算が無いので再計算をスキップ")
        else:
            px = analytics.price_frame(db)
            db.commit()
            # 決算だけの回は、株価が増えていなければ指標（株価だけで決まる）を飛ばす
            if args.only != "fins" or not analytics.indicators_current(db):
                print("指標を再計算中 (MA/RS/ステージ)...")
                analytics.compute_indicators(db, px=px)
            print("決算後パフォーマンスを計算中...")
            analytics.compute_earnings_events(db, px=px)
        if args.only in ("prices", "fins"):
            print("完了")
            return
        as_of = date.fromisoformat(args.date) if args.date else date.today()
        try:
            n_ed = sync(db, as_of)
        except Exception:
            print("決算発表予定の更新に失敗")
            raise
        print(f"決算発表予定 {n_ed}")
        print("完了")
        if refetch_failed:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

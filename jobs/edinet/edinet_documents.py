"""EDINET 書類一覧（type=2）→ edinet_documents。EDINET_API_KEY が要る。

使い方:
  python jobs/edinet/edinet_documents.py                         # 直近 7 日の未確定の日（cron）
  python jobs/edinet/edinet_documents.py --from 2025-06-01 --to 2025-06-30
  python jobs/edinet/edinet_documents.py --backfill-years 5      # 5 年前〜今日。確定済みの日は飛ばす
  python jobs/edinet/edinet_documents.py --from 2025-06-27 --to 2025-06-27 --force

1 日 1 リクエスト（ラッパーの既定で 1 秒間隔）。確定済みの日は取り直さないので、途中で止めても続きから。
設計は docs/jobs/edinet.md §3。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.edinet.client import skip_on_maintenance
from app.edinet.documents import days_to_fetch, ingest_days, now_jst


def _d(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="YYYY-MM-DD（省略時は今日）")
    ap.add_argument("--backfill-years", type=int, help="N 年前から（最大 10）")
    ap.add_argument("--force", action="store_true", help="確定済みの日も取り直す")
    args = ap.parse_args()

    if not os.environ.get("EDINET_API_KEY"):
        print("edinet skip: EDINET_API_KEY が無い")
        return 0

    from app.edinet_wrapper import EdinetClient

    today = now_jst().date()
    start = _d(args.date_from)
    if args.backfill_years:
        start = today - timedelta(days=365 * min(args.backfill_years, 10))
    db = SessionLocal()
    try:
        days = days_to_fetch(db, today, start, _d(args.date_to), force=args.force)
        if not days:
            print("edinet skip: 取る日が無い")
            return 0
        print(f"edinet list {days[0].isoformat()}..{days[-1].isoformat()} days={len(days)}")
        try:
            with EdinetClient() as client:
                st = ingest_days(db, client, days)
        except Exception as exc:
            code = skip_on_maintenance("edinet_documents", exc)
            if code is not None:
                return code
            raise
        print(
            f"edinet done days={st['days']} rows={st['rows']} "
            f"linked={st['linked']} events={st['events']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

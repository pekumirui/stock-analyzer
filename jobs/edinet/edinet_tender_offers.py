"""EDINET 公開買付けの書類（240〜340）→ edinet_tender_offer_docs / edinet_tender_offers。

EDINET_API_KEY が要る。入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。

使い方:
  python jobs/edinet/edinet_tender_offers.py --limit 100               # 新しい書類から 100 件（cron）
  python jobs/edinet/edinet_tender_offers.py --from 2026-10-01 --to 2026-10-15
  python jobs/edinet/edinet_tender_offers.py --retry-failed --limit 100
  python jobs/edinet/edinet_tender_offers.py --refresh-only            # 案件の表の作り直しだけ

届出書・報告書・意見表明は XBRL を 1 回ダウンロード（1〜3 秒）。撤回届出書・対質問回答報告書などは
一覧のメタデータだけ。ZIP は残す（docs/jobs/edinet.md「書類の ZIP」）。設計は docs/jobs/edinet.md「公開買付け」。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.edinet.client import skip_on_maintenance
from app.edinet.tender_offers import (
    exit_status,
    mark_outside_window,
    pending_documents,
    refresh_offers,
    run,
)


def _d(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", help="file_date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="file_date YYYY-MM-DD")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--reparse", action="store_true", help="取り込み済みもダウンロードし直す")
    ap.add_argument("--limit", type=int, help="最大件数（0 以下は無制限）")
    ap.add_argument("--refresh-only", action="store_true", help="ダウンロードせず、案件の表だけ作り直す")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.refresh_only:
            print(f"edinet tob offers={refresh_offers(db)}")
            return 0
        if not os.environ.get("EDINET_API_KEY"):
            print("edinet tob skip: EDINET_API_KEY が無い")
            return 0

        from app.edinet_wrapper import EdinetClient

        expired = mark_outside_window(db)
        if expired:
            print(f"edinet tob outside-window n={expired}", flush=True)
        docs = pending_documents(
            db, start=_d(args.date_from), end=_d(args.date_to),
            retry_failed=args.retry_failed, reparse=args.reparse,
            limit=args.limit if args.limit and args.limit > 0 else None,
        )
        print(f"edinet tob docs={len(docs)}", flush=True)
        try:
            with EdinetClient() as client:
                st = run(db, client, docs)
        except Exception as exc:
            code = skip_on_maintenance("edinet_tender_offers", exc)
            if code is not None:
                return code
            raise
        st["missing"] += expired
        print(
            f"edinet tob done parsed={st['parsed']} meta={st['meta']} failed={st['failed']} "
            f"missing={st['missing']} errors={st['errors']} offers={st['offers']}"
        )
        return exit_status(st)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

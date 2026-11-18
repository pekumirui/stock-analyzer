"""EDINET 大量保有報告書・変更報告書・訂正（350 / 360）→ large_holding_reports / large_holdings。

EDINET_API_KEY が要る。入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。

使い方:
  python jobs/edinet/edinet_large_holdings.py --limit 1000                # 新しい書類から 1000 件（cron）
  python jobs/edinet/edinet_large_holdings.py --from 2026-10-01 --to 2026-10-15
  python jobs/edinet/edinet_large_holdings.py --retry-failed --limit 100
  python jobs/edinet/edinet_large_holdings.py --refresh-only              # 訂正・取下げ・最新の付け直しだけ
  python jobs/edinet/edinet_large_holdings.py --reparse-fixes             # 義務発生日の誤記補正（KAB-269）を既存行へ反映

1 書類 約 1 秒（CSV を 1 回ダウンロード。ラッパーの既定で 1 秒間隔）。ZIP は残す（docs/DESIGN_edinet.md §10）。
設計は docs/DESIGN_edinet.md §5。
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
from app.edinet.large_holdings import (
    exit_status,
    load_obligation_date_fixes,
    mark_outside_window,
    pending_documents,
    refresh_versions,
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
    ap.add_argument("--doc-id", action="append", dest="doc_ids", metavar="DOC_ID",
                    help="指定した docID だけ（繰り返し可）")
    ap.add_argument("--reparse-fixes", action="store_true",
                    help="config/large_holding_date_fixes.json の docID を取り直す")
    ap.add_argument("--limit", type=int, help="最大件数（0 以下は無制限）")
    ap.add_argument("--refresh-only", action="store_true", help="ダウンロードせず、訂正・取下げ・最新だけ付け直す")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.refresh_only:
            print(f"edinet lvh refreshed={refresh_versions(db)}")
            return 0
        if not os.environ.get("EDINET_API_KEY"):
            print("edinet lvh skip: EDINET_API_KEY が無い")
            return 0

        from app.edinet_wrapper import EdinetClient

        expired = mark_outside_window(db)
        if expired:
            print(f"edinet lvh outside-window n={expired}", flush=True)
        doc_ids = list(args.doc_ids or [])
        reparse = args.reparse
        if args.reparse_fixes:
            reparse = True
            doc_ids = list(load_obligation_date_fixes())
            print(f"edinet lvh reparse-fixes n={len(doc_ids)}", flush=True)
        docs = pending_documents(
            db, start=_d(args.date_from), end=_d(args.date_to),
            retry_failed=args.retry_failed, reparse=reparse,
            limit=args.limit if args.limit and args.limit > 0 else None,
            doc_ids=doc_ids or None,
        )
        print(f"edinet lvh docs={len(docs)}", flush=True)
        try:
            with EdinetClient() as client:
                st = run(db, client, docs)
        except Exception as exc:
            code = skip_on_maintenance("edinet_large_holdings", exc)
            if code is not None:
                return code
            raise
        st["missing"] += expired
        print(
            f"edinet lvh done parsed={st['parsed']} failed={st['failed']} "
            f"missing={st['missing']} errors={st['errors']} refreshed={st['refreshed']}"
        )
        return exit_status(st)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

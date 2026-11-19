"""EDINET 有報・半期報・四半期報の XBRL → xbrl_filings(source='edinet') / xbrl_facts / xbrl_financials。

EDINET_API_KEY が要る。入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。

使い方:
  python jobs/edinet/edinet_xbrl.py --limit 300                      # 新しい書類から 300 件（cron）
  python jobs/edinet/edinet_xbrl.py --from 2025-06-01 --to 2025-06-30
  python jobs/edinet/edinet_xbrl.py --code 72030 --reparse
  python jobs/edinet/edinet_xbrl.py --retry-failed --limit 100

1 書類 2〜3 秒（ダウンロード 1 秒間隔 + パース）。ZIP は残す（§10）。設計は docs/DESIGN_edinet.md §4。
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
from app.edinet.xbrl import DEFAULT_DOC_TYPES, QUARTERLY, pending_documents, run
from app.xbrl.jobs import finish_run, start_run


def _d(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", help="file_date YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", help="file_date YYYY-MM-DD")
    ap.add_argument("--code", help="stocks.code 5 桁")
    ap.add_argument("--no-quarterly", action="store_true", help="四半期報告書（140/150）を除く")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--reparse", action="store_true", help="取り込み済みもダウンロードし直す")
    ap.add_argument("--limit", type=int, help="最大件数（0 以下は無制限）")
    args = ap.parse_args()

    if not os.environ.get("EDINET_API_KEY"):
        print("edinet xbrl skip: EDINET_API_KEY が無い")
        return 0

    from app.edinet_wrapper import EdinetClient

    doc_types = tuple(t for t in DEFAULT_DOC_TYPES if not (args.no_quarterly and t in QUARTERLY))
    db = SessionLocal()
    try:
        docs = pending_documents(
            db, doc_types=doc_types, start=_d(args.date_from), end=_d(args.date_to),
            code=args.code, retry_failed=args.retry_failed, reparse=args.reparse,
            limit=args.limit,
        )
        print(f"edinet xbrl docs={len(docs)}", flush=True)
        job = start_run(db, "edinet_xbrl")
        try:
            with EdinetClient() as client:
                st = run(db, client, docs)
        except Exception as exc:
            code = skip_on_maintenance("edinet_xbrl", exc)
            if code is not None:
                finish_run(db, job, "skipped", 0, 0, "maintenance skip")
                return code
            raise
        errors = st["errors"] + st["failed"]
        finish_run(db, job, "success" if errors == 0 else "failed",
                   st["parsed"], errors,
                   f"skipped={st['skipped']} missing={st['missing']} retracted={st['retracted']}")
        # missing（type=1 が 404）は failed で残したので exit=1 にしない（KAB-388）
        print(
            f"edinet xbrl done parsed={st['parsed']} skipped={st['skipped']} "
            f"failed={st['failed']} missing={st['missing']} errors={st['errors']} "
            f"retracted={st['retracted']}"
        )
        return 0 if errors == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""決算説明資料を Claude の Message Batches API で読み、briefing_pages / briefing_facts に書く（KAB-349）。

使い方:
  python jobs/market/briefing_extract.py submit --code 6857 --dry-run   # 出す依頼の数だけ見る
  python jobs/market/briefing_extract.py submit --code 6857
  python jobs/market/briefing_extract.py submit --limit 50              # 新しい開示から 50 冊
  python jobs/market/briefing_extract.py collect                       # 終わった Batch を書く（何度でも）
  python jobs/market/briefing_extract.py status

ANTHROPIC_API_KEY が要る（従量課金）。Batch は通常の半額で、終わるまで最長 24 時間（多くは 1 時間以内）。
submit と collect は別に回す（cron はまだ置かない）。表はデプロイの jobs/migrate.py が作る。
PDF は open_briefing で開く。手元は data/briefing（SHARE_ROOT があればその下）。
storage=onedrive は rclone で一時ファイルにして読み、data/briefing には戻さない。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import func, select

from app.db import SessionLocal
from app.market import briefing_batch as bb
from app.market.homepages import ordinary_code
from app.models import BriefingExtract
from app.share_paths import share_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Read earnings presentation PDFs with the Claude Batch API")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="send unread presentations to a batch")
    s.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    s.add_argument("--limit", type=int, help="how many presentations, newest first")
    s.add_argument("--redo", action="store_true", help="read again presentations already done / failed")
    s.add_argument("--no-thinking", action="store_true", help="thinking を止める（出力が減る。精度は未検証）")
    s.add_argument("--dry-run", action="store_true", help="PDF を読んで依頼の数を数えるだけ。API に出さない")
    sub.add_parser("collect", help="write results of ended batches")
    sub.add_parser("status", help="count presentations by status")
    args = ap.parse_args(argv)
    if args.cmd == "submit":
        if not args.code and not args.limit:
            ap.error("specify --code or --limit")
        if args.limit is not None and args.limit < 1:
            ap.error("--limit must be >= 1")
    return args


def make_client():
    import anthropic  # 本番の依存。テストは偽のクライアントを渡すので import しない

    return anthropic.Anthropic()


def main(argv: list[str] | None = None, client=None) -> int:
    args = parse_args(argv)
    root = share_path("briefing")
    db = SessionLocal()
    try:
        if args.cmd == "status":
            for st, n in db.execute(select(BriefingExtract.status, func.count()).group_by(BriefingExtract.status)):
                print(f"{st}: {n}")
            return 0
        if args.cmd == "submit":
            codes = [ordinary_code(c) for c in args.code] or None
            docs = bb.pending_documents(db, codes, limit=args.limit, redo=args.redo)
            if client is None and not args.dry_run:
                client = make_client()
            res = bb.submit(db, client, root, docs, thinking=not args.no_thinking, dry_run=args.dry_run)
            print(res.summary(args.dry_run), flush=True)
            return 0
        res = bb.collect(db, client or make_client(), root)
        print(res.summary(), flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

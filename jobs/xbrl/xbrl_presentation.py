"""取り込み済みの有報・半期報・四半期報・短信 XBRL の表示リンクを ZIP から埋める（KAB-463）。

cron には載せない（過去分を 1 回流すだけ。新しい書類は取り込みが書く）。設計は docs/DESIGN_xbrl.md §4.5。
新しい提出から 1 件ずつ commit するので、止めても続きから進む。
EDINET の ZIP は app/edinet/archive.py download。手元に無く退避の記録があれば OneDrive、記録が無ければ EDINET。
EDINET から取った ZIP は data/edinet に残り、翌朝 backup_data が OneDrive へ移す。

使い方:
  python jobs/xbrl/xbrl_presentation.py --dry-run
  python jobs/xbrl/xbrl_presentation.py --source tdnet --batch 500
  python jobs/xbrl/xbrl_presentation.py --source edinet --limit 3000 --batch 200 --sleep 1
  python jobs/xbrl/xbrl_presentation.py --source edinet --local-only --batch 500
  python jobs/xbrl/xbrl_presentation.py --archived-only --limit 50 --batch 10
  python jobs/xbrl/xbrl_presentation.py --code 7203 --code 6758
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import XBRL_DEST  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.client import skip_on_maintenance  # noqa: E402
from app.xbrl.presentation_backfill import SOURCES, line, origins, run, targets  # noqa: E402
from app.xbrl.presentation_backfill import edinet_fetcher, tdnet_fetcher  # noqa: E402
from jobs._r2common import fail_if_mount_down  # noqa: E402
from jobs.xbrl.tdnet_xbrl_r2 import jq_lookup_code  # noqa: E402

JOB = "xbrl_presentation"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=SOURCES, action="append", help="繰り返し可。無ければ両方")
    ap.add_argument("--code", action="append", default=[], help="4 桁または 5 桁。繰り返し可")
    ap.add_argument("--limit", type=int, default=0, help="最大件数（0 以下は無制限）")
    ap.add_argument("--batch", type=int, default=None, help="N 件ごとに進み具合を 1 行出す")
    ap.add_argument("--sleep", type=float, default=0.0, help="EDINET・OneDrive から取ったあと待つ秒数")
    ap.add_argument("--local-only", action="store_true",
                    help="EDINET・OneDrive に行かない。手元（data/edinet）に無い書類は印を付けずに飛ばす")
    ap.add_argument("--archived-only", action="store_true",
                    help="退避の記録（edinet_archives）がある EDINET の書類だけ。OneDrive からの読み出しを測るとき")
    ap.add_argument("--dest", default=XBRL_DEST, help="短信 XBRL の ZIP の根（raw_path の起点）")
    ap.add_argument("--dry-run", action="store_true", help="対象の件数だけ")
    args = ap.parse_args(argv)
    if args.batch is not None and args.batch < 1:
        ap.error("--batch は 1 以上")
    sources = ("edinet",) if args.archived_only else tuple(args.source or SOURCES)
    codes = [jq_lookup_code(c) for c in args.code]

    init_db()
    db = SessionLocal()
    try:
        ids = targets(db, sources=sources, codes=codes, archived_only=args.archived_only)
        if args.dry_run:
            print(f"{JOB} dry-run remaining={len(ids)} sources={','.join(sources)}", flush=True)
            return 0
        batch_ids = ids if args.limit <= 0 else ids[:args.limit]
        print(f"{JOB} docs={len(batch_ids)} remaining={len(ids)}", flush=True)

        fetchers = {}
        if "tdnet" in sources:
            dest = Path(args.dest)
            rc = fail_if_mount_down(JOB, dest)
            if rc is not None:
                return rc
            fetchers["tdnet"] = tdnet_fetcher(dest)
        with ExitStack() as stack:
            if "edinet" in sources:
                client = None
                if not args.local_only and os.environ.get("EDINET_API_KEY"):
                    from app.edinet_wrapper import EdinetClient

                    client = stack.enter_context(EdinetClient())
                elif not args.local_only:
                    print(f"{JOB} EDINET_API_KEY が無いので手元の ZIP だけ読む", flush=True)
                fetchers["edinet"] = edinet_fetcher(db, client)
            try:
                counts = run(db, batch_ids, fetchers, batch=args.batch, sleep=args.sleep)
            except Exception as exc:
                code = skip_on_maintenance(JOB, exc)
                if code is not None:
                    return code
                raise
        left = len(targets(db, sources=sources, codes=codes, archived_only=args.archived_only))
        print(f"{JOB} {line(counts)} skipped={counts['skipped']} remaining={left} {origins(counts)}", flush=True)
        return 0 if counts["errors"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

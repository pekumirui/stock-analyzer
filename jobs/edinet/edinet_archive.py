"""取込済みで ZIP を残していない EDINET の書類を取り直して data/edinet に残す（KAB-281）。

EDINET_API_KEY が要る。閲覧期間の終わりが近い順（自己株買い 1 年 → 大量保有・公開買付け 5 年 →
有報系 10 年。同じ期間なら古い提出から）。404 は gone として記録し、次の回は飛ばす。
新しく取り込む書類は各取込（app/edinet/archive.py download）が残すので、ここは過去分だけ。
OneDrive へは毎朝 jobs/ops/backup_data.py が move する。設計は docs/DESIGN_edinet.md §10。

使い方:
  python jobs/edinet/edinet_archive.py --limit 1000     # cron（夜間）
  python jobs/edinet/edinet_archive.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.edinet.archive import backfill, backfill_targets
from app.edinet.client import skip_on_maintenance


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000, help="最大件数（0 以下は無制限）")
    ap.add_argument("--dry-run", action="store_true", help="対象の件数と先頭 10 件だけ")
    args = ap.parse_args(argv)

    db = SessionLocal()
    try:
        targets = backfill_targets(db)
        if args.dry_run:
            print(f"edinet archive dry-run remaining={len(targets)}")
            for t in targets[:10]:
                print(f"  {t.doc.doc_id} type={t.doc.doc_type_code} file={t.file_type} "
                      f"file_date={t.doc.file_date} expires={t.expires}")
            return 0
        if not os.environ.get("EDINET_API_KEY"):
            print("edinet archive skip: EDINET_API_KEY が無い")
            return 0
        batch = targets if args.limit <= 0 else targets[:args.limit]
        print(f"edinet archive docs={len(batch)} remaining={len(targets)}", flush=True)

        from app.edinet_wrapper import EdinetClient

        try:
            with EdinetClient() as client:
                st = backfill(db, client, batch)
        except Exception as exc:
            code = skip_on_maintenance("edinet_archive", exc)
            if code is not None:
                return code
            raise
        left = len(targets) - st["saved"] - st["gone"]
        print(f"edinet archive done saved={st['saved']} gone={st['gone']} "
              f"errors={st['errors']} remaining={left}")
        return 0 if st["errors"] == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

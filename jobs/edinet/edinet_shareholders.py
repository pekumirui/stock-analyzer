"""有報の大株主の状況・所有者別状況を xbrl_facts から正規化する（KAB-266）。

新着は jobs/edinet/edinet_xbrl.py が keep_fact を広げて取り込み、normalize_filing が表を作る。
このジョブは既存 fact からの作り直しと、訂正の最新フラグの付け直し。

使い方:
  python jobs/edinet/edinet_shareholders.py --rebuild
  python jobs/edinet/edinet_shareholders.py --rebuild --code 72030
  python jobs/edinet/edinet_shareholders.py --refresh-only

本番の過去分は fact がまだ無い（次元付きを keep_fact が落としていた）ので、
先に `python jobs/edinet/edinet_xbrl.py --reparse` が要る。設計は docs/DESIGN_edinet.md §9。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.xbrl.shareholders import rebuild_shareholders, refresh_versions


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="parsed の EDINET filing から大株主・所有者別を作り直す")
    ap.add_argument("--refresh-only", action="store_true",
                    help="ダウンロードせず、訂正・取下げ・最新だけ付け直す")
    ap.add_argument("--code", help="stocks.code 5 桁")
    args = ap.parse_args()

    if not args.rebuild and not args.refresh_only:
        ap.error("--rebuild か --refresh-only を指定する")

    db = SessionLocal()
    try:
        if args.refresh_only and not args.rebuild:
            print(f"edinet shareholders refreshed={refresh_versions(db)}")
            return 0
        stats = rebuild_shareholders(db, code=args.code)
        print(f"edinet shareholders {stats}")
        return 0 if stats.get("errors", 0) == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

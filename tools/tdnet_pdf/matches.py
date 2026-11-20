"""PDF だけの期の短信 PDF の行に XBRL の要素を付ける（xbrl_pdf_matches。docs/jobs/xbrl.md §18）。

cron では動かさない。銘柄ごとに入れ替えるので何度流しても同じ。

  python tools/tdnet_pdf/matches.py --batch 200
  python tools/tdnet_pdf/matches.py --code 9880 --code 6857
  python tools/tdnet_pdf/matches.py --unmatched 50   # 付かなかった行名の多い順（付け直さない）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, init_db
from app.xbrl.pdf_matches import rebuild_matches, summary, unmatched_names
from tools.tdnet_pdf.financials import code5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", action="append", default=[], help="4 桁または 5 桁。繰り返し可。無ければ全銘柄")
    parser.add_argument(
        "--batch", type=int, default=None,
        help="N 銘柄ごとに累計を 1 行出す。database is locked の銘柄は区切りで待って 1 回やり直す",
    )
    parser.add_argument("--unmatched", type=int, default=None, metavar="N", help="付かなかった行名の上位 N を出すだけ")
    args = parser.parse_args(argv)
    if args.batch is not None and args.batch < 1:
        parser.error("--batch は 1 以上")
    init_db()
    db = SessionLocal()
    try:
        if args.unmatched is not None:
            for statement, name, n in unmatched_names(db, args.unmatched):
                print(f"{statement}\t{name}\t{n}", flush=True)
            return 0
        codes = [code5(code) for code in args.code] or None
        counts = rebuild_matches(db, codes=codes, batch=args.batch)
        print(summary(counts), flush=True)
        return 1 if counts["failed"] else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

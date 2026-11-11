"""短信 PDF の HTML（tdnet_pdf_documents）→ xbrl_filings(source='tdnet_pdf') → xbrl_financials（KAB-173）。

cron では動かさない。tools/tdnet_pdf/ingest.py で過去の短信 PDF を入れたあとに手で流す（新しい短信は
TDnet の ZIP があり、ingest は PDF を取らない）。取り込み済みの回は飛ばすので何度流してもよい。

  python tools/tdnet_pdf/financials.py --limit 100000
  python tools/tdnet_pdf/financials.py --limit 100000 --batch 500   # 500 件ごとに進み具合を 1 行出す
  python tools/tdnet_pdf/financials.py --code 7203
  python tools/tdnet_pdf/financials.py --reparse
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402
from app.xbrl.pdf import SyncOutcome, sync_pdf_filings  # noqa: E402

_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")


def code5(raw: str) -> str:
    text = raw.strip().upper()
    if not _CODE_RE.fullmatch(text):
        raise ValueError(f"code は 4〜5 桁の英数字です: {raw}")
    return text + "0" if len(text) == 4 else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", action="append", default=[], help="4 桁または 5 桁。繰り返し可")
    parser.add_argument("--reparse", action="store_true", help="指紋を無視して HTML を読み直す")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument(
        "--batch", type=int, default=None,
        help="N 件ごとに区切って累計を 1 行出す。database is locked の回は区切りで待って 1 回やり直す",
    )
    args = parser.parse_args(argv)
    if args.batch is not None and args.batch < 1:
        parser.error("--batch は 1 以上")
    codes = [code5(code) for code in args.code] or None
    db = SessionLocal()
    try:
        apply_mappings(db)
        counts = sync_pdf_filings(
            db, codes=codes, reparse=args.reparse, limit=args.limit, batch=args.batch,
        )
        parts = [f"{outcome.value}={counts[outcome]}" for outcome in SyncOutcome]
        parts.append(f"garbled={counts['garbled']}")
        parts.append(f"textless={counts['textless']}")
        parts.append(f"remaining={counts['remaining']}")
        print("tdnet_pdf_financials " + " ".join(parts), flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

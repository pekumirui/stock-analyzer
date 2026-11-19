"""EDINET コードリスト → edinet_filers、stocks.edinet_code を付け直す。API キー不要。

使い方:
  python jobs/edinet/edinet_codes.py              # ダウンロードして取り込む
  python jobs/edinet/edinet_codes.py --zip Edinetcode.zip   # 手元の ZIP から

設計は docs/DESIGN_edinet.md §2。取り込んだあと edinet_documents.code の空きも付け直す。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal
from app.edinet.codes import ingest_code_list, link_stocks
from app.edinet.documents import now_jst, relink_codes

UNMATCHED_SHOW = 20


def download() -> bytes:
    import httpx

    from app.edinet_wrapper.config import EDINET_CODE_LIST_JA

    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        res = client.get(EDINET_CODE_LIST_JA)
        res.raise_for_status()
        return res.content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="Edinetcode.zip のパス（省略時はダウンロード）")
    args = ap.parse_args()

    payload = Path(args.zip).read_bytes() if args.zip else download()
    db = SessionLocal()
    try:
        n = ingest_code_list(db, payload, now_jst())
        st = link_stocks(db)
        relinked = relink_codes(db)
    finally:
        db.close()
    miss = st["unmatched_equity"]
    print(
        f"edinet codes filers={n} stocks={st['stocks']} linked={st['linked']} "
        f"changed={st['changed']} unmatched_equity={len(miss)} "
        f"duplicate_sec={len(st['duplicate_sec_codes'])} relinked_docs={relinked}"
    )
    if miss:
        more = " …" if len(miss) > UNMATCHED_SHOW else ""
        print("  unmatched 内国株: " + " ".join(miss[:UNMATCHED_SHOW]) + more)
    if st["duplicate_sec_codes"]:
        print("  証券コードが重複: " + " ".join(st["duplicate_sec_codes"][:UNMATCHED_SHOW]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

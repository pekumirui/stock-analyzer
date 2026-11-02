"""decks.json の説明資料 PDF を IR Bank から <root>/pdf/<id>.pdf に取得する。既にあれば取らない。

doc_id が無い資料（TDnet に出ていないもの）は note に従って手で置く。

  .venv/bin/python analyzer/deck_reader/fetch.py [--root /workspace/deck-reader] [id ...]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_ROOT = Path("/workspace/deck-reader")
URL = "https://api.irbank.net/api/market-files/v1/tdnet/{doc_id}.pdf"


def load_decks(ids: list[str] | None = None) -> list[dict]:
    decks = json.loads((HERE / "decks.json").read_text())
    if ids:
        known = {d["id"] for d in decks}
        missing = set(ids) - known
        if missing:
            raise SystemExit(f"decks.json に無い: {', '.join(sorted(missing))}")
        decks = [d for d in decks if d["id"] in ids]
    return decks


def pdf_path(root: Path, deck_id: str) -> Path:
    return root / "pdf" / f"{deck_id}.pdf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("ids", nargs="*")
    args = ap.parse_args()
    for d in load_decks(args.ids):
        path = pdf_path(args.root, d["id"])
        if path.exists() and path.stat().st_size > 0:
            print(f"{path.name} あり")
            continue
        if not d.get("doc_id"):
            print(f"{path.name} 無し: {d.get('note', '手で置く')}")
            continue
        with urllib.request.urlopen(URL.format(doc_id=d["doc_id"]), timeout=60) as r:  # nosec B310
            body = r.read()
        if not body.startswith(b"%PDF"):
            raise RuntimeError(f"not a pdf: {d['doc_id']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        print(f"{path.name} {len(body):,}")
        time.sleep(1)


if __name__ == "__main__":
    main()

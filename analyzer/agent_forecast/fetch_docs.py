"""設定の月次・説明資料 PDF を IR Bank から <root>/raw/ に取得する。既にあれば取らない。

  .venv/bin/python analyzer/agent_forecast/fetch_docs.py --target targets/6036_FY2026Q4.json
"""

from __future__ import annotations

import argparse
import time
import urllib.request
from pathlib import Path

from common import load_target, root_of, target_args

URL = "https://api.irbank.net/api/market-files/v1/tdnet/{doc_id}.pdf"


def fetch(items: list[dict], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for it in items:
        path = dest / f"{it['date'].replace('-', '')}_{it['title']}.pdf"
        if path.exists() and path.stat().st_size > 0:
            continue
        with urllib.request.urlopen(URL.format(doc_id=it["doc_id"]), timeout=60) as r:  # nosec B310
            body = r.read()
        if not body.startswith(b"%PDF"):
            raise RuntimeError(f"not a pdf: {it['doc_id']}")
        path.write_bytes(body)
        print(f"{path.name} {len(body):,}")
        time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    args = ap.parse_args()
    t = load_target(args.target)
    raw = root_of(t, args.root) / "raw"
    fetch(t.get("monthly", []), raw / "monthly_pdf")
    fetch(t.get("briefing", []), raw / "briefing_pdf")


if __name__ == "__main__":
    main()

"""設定の月次・説明資料 PDF を <root>/raw/ に取得する。既にあれば取らない。

月次は IR Bank。説明資料は索引（briefing_documents）にあれば
app.market.briefing_files から読む。OneDrive へ移した PDF もここから取れる。
索引が無い、または DB を読めないときはこれまでどおり IR Bank。

  .venv/bin/python analyzer/agent_forecast/fetch_docs.py --target targets/6036_FY2026Q4.json
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

from common import load_target, root_of, target_args

URL = "https://api.irbank.net/api/market-files/v1/tdnet/{doc_id}.pdf"


def indexed_briefing(doc_id: str) -> bytes | None:
    """索引の PDF。行が無ければ None。OneDrive の取得失敗はそのまま上げる。"""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from sqlalchemy.exc import OperationalError

        from app.config import DATABASE_URL
        from app.market.briefing_files import read_indexed
    except ImportError:
        return None
    if DATABASE_URL.startswith("sqlite///"):
        raw = DATABASE_URL[len("sqlite///"):].split("?", 1)[0]
        if raw and raw != ":memory:" and not Path(raw).is_file():
            return None
    try:
        return read_indexed(doc_id)
    except OperationalError:
        return None


def fetch(items: list[dict], dest: Path, *, indexed=None, pause: float = 1.0) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for it in items:
        path = dest / f"{it['date'].replace('-', '')}_{it['title']}.pdf"
        if path.exists() and path.stat().st_size > 0:
            continue
        body = indexed(it["doc_id"]) if indexed is not None else None
        downloaded = body is None
        if downloaded:
            with urllib.request.urlopen(URL.format(doc_id=it["doc_id"]), timeout=60) as r:  # nosec B310
                body = r.read()
        if not body.startswith(b"%PDF"):
            raise RuntimeError(f"not a pdf: {it['doc_id']}")
        path.write_bytes(body)
        print(f"{path.name} {len(body):,}")
        if downloaded and pause:
            time.sleep(pause)


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    args = ap.parse_args()
    t = load_target(args.target)
    raw = root_of(t, args.root) / "raw"
    fetch(t.get("monthly", []), raw / "monthly_pdf")
    fetch(t.get("briefing", []), raw / "briefing_pdf", indexed=indexed_briefing)


if __name__ == "__main__":
    main()

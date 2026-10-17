"""株探の PTS ナイトタイムセッション ランキング（値上がり・値下がり・出来高）を pts_snapshots へ。

使い方:
  python jobs/market/pts_snapshot.py

cron は平日 15:30〜24:00 の 30 分ごと（deploy/crontab）。設計は DESIGN.md「PTS」。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.market.pts import client, snapshot_all  # noqa: E402


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        with client() as http:
            stats = snapshot_all(db, http)
    finally:
        db.close()
    # 3 本とも取れなければ非ゼロ（cron-run.sh が fail を残す）
    return 1 if stats and all(n < 0 for n in stats.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

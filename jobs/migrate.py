"""既存DBに不足している列を追加する (データを消さずにスキーマだけ追随)。

追加された列の値はNULLのままなので、株価・財務は再取得が必要。
作り直してよいなら .\\run.ps1 reset の方が確実。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.exc import OperationalError

from app.db import init_db, is_sqlite_lock, migrate_add_columns, missing_columns

LOCK_ATTEMPTS = 10
LOCK_SLEEP_S = 5.0
LOCK_SLEEP_CAP_S = 60.0


def migrate_once() -> None:
    init_db()
    lacks = missing_columns()
    if not lacks:
        print("スキーマは最新です。追加する列はありません。")
        return
    print("不足している列:")
    for t, cols in lacks.items():
        print(f"  {t}: {', '.join(cols)}")
    n = migrate_add_columns()
    print(f"\n{n}列を追加しました。")
    print("追加列の値は空のため、株価・財務の再取得を推奨します:")
    print("  .\\run.ps1 backfill 2")


def main() -> None:
    delay = LOCK_SLEEP_S
    for attempt in range(1, LOCK_ATTEMPTS + 1):
        try:
            migrate_once()
            return
        except OperationalError as exc:
            if not is_sqlite_lock(exc) or attempt >= LOCK_ATTEMPTS:
                raise
            print(
                f"migrate locked (attempt {attempt}/{LOCK_ATTEMPTS}); "
                f"retry in {int(delay)}s",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, LOCK_SLEEP_CAP_S)


if __name__ == "__main__":
    main()

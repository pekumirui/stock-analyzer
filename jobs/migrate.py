"""既存DBに不足している列を追加する (データを消さずにスキーマだけ追随)。デプロイごとに update.sh が呼ぶ。

最後に月次の後始末 (app.monthly.maintenance.run_deploy_maintenance) を走らせる。

追加された列の値はNULLのままなので、株価・財務は再取得が必要。
作り直してよいなら .\\run.ps1 reset の方が確実。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import (
    engine,
    ensure_indexes,
    init_db,
    is_sqlite_lock,
    migrate_add_columns,
    migrate_xbrl_elements_is_custom,
    migrate_xbrl_financials_source_layer,
    migrate_xbrl_financials_unique,
    missing_columns,
)
from app.monthly.maintenance import run_deploy_maintenance

LOCK_ATTEMPTS = 10
LOCK_SLEEP_S = 5.0
LOCK_SLEEP_CAP_S = 60.0


def migrate_once() -> None:
    init_db()
    # KAB-319 初稿の ir_sites。本番には載っていない。homepages に差し替える
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ir_sites"))
    ensure_indexes()
    migrate_columns()
    if migrate_xbrl_financials_unique():
        print("xbrl_financials の UNIQUE を value_kind/range_side/target_period 込みに付け替えた。")
    n_layer = migrate_xbrl_financials_source_layer()
    if n_layer:
        print(f"xbrl_financials.source_layer を {n_layer} 行、要素の namespace から埋めた。")
    flips = migrate_xbrl_elements_is_custom()
    if any(flips.values()):
        print(f"xbrl_elements.is_custom を付け直した: 独自へ {flips['to_custom']} 行、"
              f"標準へ {flips['to_standard']} 行（KAB-81）。")
    run_deploy_maintenance()
    # uvicorn --workers N が同時に init_db(strict=True) しても CREATE INDEX しないよう、
    # 起動前に 1 回だけ strict まで通す（--preload は使わない）。
    init_db(strict=True)


def migrate_columns() -> None:
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

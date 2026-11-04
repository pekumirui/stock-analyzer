"""daily_prices を WITHOUT ROWID に作り直す（KAB-289）。一度だけ手で流す。

rowid 表は日ごとに全銘柄を追記するので、1 銘柄の行が表全体に散る。WITHOUT ROWID は
主キー (code, date) の順に行を置くので、チャートなど銘柄ごとの読み出しが続いたページで済む。

使い方:
  python jobs/ops/rebuild_daily_prices.py            # 見積もりだけ（書かない）
  python jobs/ops/rebuild_daily_prices.py --yes      # 作り直す

作り直しは 1 トランザクション。コピーの間は書き込みロックを握るので、他のライター
（cron のバッチ・月次 PUT）は busy_timeout のあと失敗しうる。日次バッチ（19:30）と
重ならない時間に流す。読み取り（API）は WAL なので止まらない。
ディスクは表の大きさの約 3 倍（新しい表と WAL）を一時的に使う。足りなければ止まる。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import DailyPrice  # noqa: E402

TABLE = "daily_prices"
OLD = "daily_prices__old"
DISK_FACTOR = 3


def is_without_rowid(conn) -> bool:
    sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :n"), {"n": TABLE},
    ).scalar()
    return bool(sql) and "WITHOUT ROWID" in sql.upper()


def table_bytes(conn) -> int:
    """表と索引の大きさ。dbstat が無い SQLite では行数からの概算。"""
    try:
        n = conn.execute(text(
            "SELECT SUM(pgsize) FROM dbstat WHERE name = :t "
            "OR name IN (SELECT name FROM sqlite_master WHERE tbl_name = :t AND type = 'index')"
        ), {"t": TABLE}).scalar()
        if n:
            return int(n)
    except Exception:  # noqa: BLE001 dbstat の無いビルドがある
        pass
    rows = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}")).scalar() or 0
    return int(rows) * 200


def db_path() -> Path:
    db = engine.url.database
    if not db:
        raise SystemExit("DATABASE_URL が SQLite のファイルではありません")
    return Path(db).resolve()


def rebuild(conn) -> int:
    """rowid 表を WITHOUT ROWID にコピーし直す。コピーした行数を返す。"""
    old_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({TABLE})")).fetchall()]
    conn.execute(text(f"ALTER TABLE {TABLE} RENAME TO {OLD}"))
    # RENAME 後も名前付き INDEX は残る。新しい CREATE と衝突するので先に外す
    for row in conn.execute(text(f"PRAGMA index_list('{OLD}')")).fetchall():
        if row[3] == "c":
            conn.execute(text(f'DROP INDEX IF EXISTS "{row[1]}"'))
    DailyPrice.__table__.create(bind=conn)
    new_cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({TABLE})")).fetchall()]
    cols = ", ".join(f'"{c}"' for c in new_cols if c in old_cols)
    n = conn.execute(text(
        f"INSERT INTO {TABLE} ({cols}) SELECT {cols} FROM {OLD} ORDER BY code, date"
    )).rowcount
    conn.execute(text(f"DROP TABLE {OLD}"))
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", help="作り直す（無ければ見積もりだけ）")
    args = ap.parse_args(argv)

    path = db_path()
    with engine.connect() as conn:
        if is_without_rowid(conn):
            print(f"{TABLE} は既に WITHOUT ROWID です。何もしません。")
            return 0
        size = table_bytes(conn)
    need = size * DISK_FACTOR
    free = shutil.disk_usage(path.parent).free
    mib = 1024 * 1024
    print(f"{TABLE}: 約 {size // mib} MiB。必要な空き 約 {need // mib} MiB / 空き {free // mib} MiB")
    if free < need:
        print("空きが足りないので止めます。")
        return 1
    if not args.yes:
        print("見積もりだけです。作り直すときは --yes を付けてください。")
        return 0

    t0 = time.monotonic()
    with engine.begin() as conn:
        n = rebuild(conn)
    t1 = time.monotonic()
    with engine.connect() as conn:
        conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
    print(f"{n} 行を作り直しました（{t1 - t0:.0f} 秒）。WAL を切り詰めました"
          f"（{time.monotonic() - t1:.0f} 秒）。")
    print(f"DB ファイルは縮みません（空いたページは再利用）。縮めるなら VACUUM: {os.fspath(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ゴールデンデータで stock.db を作る（./run.sh golden）。tests/golden/db/*.csv を空の DB に入れ、指標を計算し直す。

作り直すのは tools/golden/build_golden_db.py。中身と期間は tests/golden/db/manifest.json。
既に行がある DB には入れない（./run.sh reset してから）。作った DB は PRAGMA user_version が
GOLDEN_USER_VERSION になる。install.sh はこれを見て、ゴールデンデータの DB には日次 batch を回さない。
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, text

from app.db import SessionLocal, engine, init_db
from app.market.analytics import compute_earnings_events, compute_indicators
from app.models import Base
from app.monthly.maintenance import backfill_legacy_element_sources

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden" / "db"
GOLDEN_USER_VERSION = 1
CHUNK = 5000


def _converter(col):
    t = col.type
    if isinstance(t, Boolean):
        return lambda s: s in ("1", "True", "true")
    if isinstance(t, DateTime):
        return datetime.fromisoformat
    if isinstance(t, Date):
        return lambda s: date.fromisoformat(s[:10])
    if isinstance(t, Integer):
        return int
    if isinstance(t, Float):
        return float
    return str


def load_table(table, path: Path) -> int:
    conv = {c.name: _converter(c) for c in table.columns}
    n = 0
    with path.open(encoding="utf-8", newline="") as f, engine.begin() as conn:
        reader = csv.DictReader(f)
        unknown = set(reader.fieldnames or []) - set(conv)
        if unknown:
            raise SystemExit(f"{path.name}: モデルに無い列 {sorted(unknown)}（build_golden_db.py で作り直す）")
        batch: list[dict] = []
        for row in reader:
            batch.append({k: (None if v == "" else conv[k](v)) for k, v in row.items()})
            if len(batch) >= CHUNK:
                conn.execute(table.insert(), batch)
                n += len(batch)
                batch = []
        if batch:
            conn.execute(table.insert(), batch)
            n += len(batch)
    return n


def is_golden_db() -> bool:
    with engine.connect() as conn:
        return conn.execute(text("PRAGMA user_version")).scalar() == GOLDEN_USER_VERSION


def main(golden_dir: Path = GOLDEN_DIR) -> int:
    started = time.monotonic()
    init_db()
    with engine.connect() as conn:
        if conn.execute(text("SELECT COUNT(*) FROM stocks")).scalar():
            print("この DB には既に行があります。./run.sh reset してから golden してください", file=sys.stderr)
            return 1
    for table in Base.metadata.sorted_tables:
        path = golden_dir / f"{table.name}.csv"
        if path.is_file():
            print(f"{table.name}\t{load_table(table, path)}")
    db = SessionLocal()
    try:
        compute_indicators(db)
        n = db.execute(text("SELECT COUNT(*) FROM indicators")).scalar()
        print(f"indicators\t{n}（計算し直し）")
        compute_earnings_events(db)
        n = db.execute(text("SELECT COUNT(*) FROM earnings_events")).scalar()
        print(f"earnings_events\t{n}（計算し直し）")
    finally:
        db.close()
    backfill_legacy_element_sources(verbose=False)
    with engine.begin() as conn:
        conn.execute(text(f"PRAGMA user_version = {GOLDEN_USER_VERSION}"))
    print(f"ゴールデンデータで DB を作りました（{time.monotonic() - started:.1f} 秒）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

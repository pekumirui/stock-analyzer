"""indicators の差分書き込みが全置換と同じ中身になるか。"""
from datetime import date

import numpy as np
import pandas as pd

from app.db import SessionLocal, init_db, upsert
from app.market.analytics import sync_indicators
from app.models import Indicator


def _frame() -> pd.DataFrame:
    rows = []
    for code in ("11110", "22220"):
        for day in (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)):
            rows.append({
                "code": code, "date": day, "close": 100.0 + day.day, "ma50": np.nan,
                "ma150": 1.5, "ma200": 2.5, "ma200_slope_1m": 0.1, "high_52w": 120.0,
                "low_52w": 80.0, "rs_raw": 3.25, "rs_rank": 50.0 if code == "11110" else np.nan,
                "stage": 2.0, "avg_volume_50": 1000.0,
            })
    return pd.DataFrame(rows)


def _table(db) -> list[tuple]:
    return sorted(
        (r.code, r.date, r.close, r.ma50, r.ma150, r.ma200, r.ma200_slope_1m, r.high_52w,
         r.low_52w, r.rs_raw, r.rs_rank, r.stage, r.avg_volume_50)
        for r in db.query(Indicator).all()
    )


def _expected(out: pd.DataFrame) -> list[tuple]:
    def val(v):
        return None if pd.isna(v) else v
    return sorted(
        (r.code, r.date, r.close, val(r.ma50), r.ma150, r.ma200, r.ma200_slope_1m, r.high_52w,
         r.low_52w, r.rs_raw, None if pd.isna(r.rs_rank) else int(r.rs_rank),
         int(r.stage), r.avg_volume_50)
        for r in out.itertuples()
    )


def test_sync_matches_frame_after_drift():
    init_db()
    db = SessionLocal()
    try:
        out = _frame()
        sync_indicators(db, out, chunk_size=2)
        assert _table(db) == _expected(out)

        # 値の違う行・余分な行・欠けた行・NULL になった行を作ってから同期し直す
        upsert(db, Indicator, [
            {"code": "11110", "date": date(2026, 1, 5), "close": 1.0},
            {"code": "33330", "date": date(2026, 1, 5), "close": 9.0},
            {"code": "22220", "date": date(2026, 1, 6), "ma50": 7.0},
        ])
        db.query(Indicator).filter(Indicator.code == "22220",
                                   Indicator.date == date(2026, 1, 7)).delete()
        db.commit()
        sync_indicators(db, out, chunk_size=2)
        assert _table(db) == _expected(out)

        # 古い日が抜け、新しい日が入る
        nxt = out[out["date"] != date(2026, 1, 5)].copy()
        add = nxt[nxt["date"] == date(2026, 1, 7)].assign(date=date(2026, 1, 8))
        nxt = pd.concat([nxt, add], ignore_index=True)
        sync_indicators(db, nxt, chunk_size=2)
        assert _table(db) == _expected(nxt)
    finally:
        db.close()

"""調整後株価の自前計算（KAB-193）。値は J-Quants の配信値。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-adjust-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.market.adjust import readjust  # noqa: E402
from app.models import DailyPrice  # noqa: E402

D = date.fromisoformat


def _row(code, d, c, vo, factor=1.0, ex_rt=None, adj_c=None):
    return {"code": code, "date": D(d), "open": c, "high": c, "low": c, "close": c, "volume": vo,
            "adj_factor": factor, "ex_rt": ex_rt, "adj_open": adj_c or c, "adj_high": adj_c or c,
            "adj_low": adj_c or c, "adj_close": adj_c or c, "adj_volume": vo}


def test_readjust_matches_jquants() -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, DailyPrice, [
            # キオクシア 2026-09-29 に 1:3。J-Quants の AdjC は 9/25 18593.3、9/28 17780.0
            _row("285A0", "2026-09-25", 55780, 21757800),
            _row("285A0", "2026-09-28", 53340, 18399200),
            _row("285A0", "2026-09-29", 17880, 41572200, factor=1 / 3, ex_rt="1"),
            # 2 回の分割（1:2 のあと 1:5）。古い日は両方を掛ける
            _row("11110", "2026-01-05", 1000, 100),
            _row("11110", "2026-02-02", 600, 100, factor=0.5, ex_rt="1"),
            _row("11110", "2026-03-02", 130, 100, factor=0.2, ex_rt="1"),
            # ライツイシューは出来高を調整しない
            _row("22220", "2026-01-05", 1000, 100),
            _row("22220", "2026-02-02", 900, 100, factor=0.9, ex_rt="3"),
            # 権利落ちの無い銘柄は書き換えない
            _row("33330", "2026-01-05", 1000, 100),
        ])
        assert readjust(db) == 5  # キオクシア 2 行、2 回分割 2 行、ライツイシュー 1 行
        db.expire_all()

        def adj(code, d):
            r = db.get(DailyPrice, (code, D(d)))
            return r.adj_close, r.adj_volume

        assert adj("285A0", "2026-09-25") == (18593.3, 65273400)
        assert adj("285A0", "2026-09-28") == (17780.0, 55197600)
        assert adj("285A0", "2026-09-29") == (17880, 41572200)
        assert adj("11110", "2026-01-05") == (100.0, 1000)
        assert adj("11110", "2026-02-02") == (120.0, 500)
        assert adj("22220", "2026-01-05") == (900.0, 100)
        assert readjust(db) == 0  # 2 回目は変わる行が無い
    finally:
        db.close()

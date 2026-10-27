"""権利落ちでの調整後株価の計算し直し（KAB-193）と、日足の全期間の取り直し（KAB-188）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-refetch-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.market.ingest import run_daily, run_refetch_prices  # noqa: E402
from app.models import DailyPrice  # noqa: E402

# キオクシアの 2026-09-29 の 1:3 分割（J-Quants の値）
API = {
    "2026-09-25": {"C": 55780, "AdjFactor": 1.0, "AdjC": 18593.3},
    "2026-09-28": {"C": 53340, "AdjFactor": 1.0, "AdjC": 17780.0},
    "2026-09-29": {"C": 17880, "AdjFactor": 1 / 3, "AdjC": 17880.0},
}


def _bar(day: str, code: str = "285A0") -> dict:
    v = API[day]
    return {"Code": code, "Date": day, "O": v["C"], "H": v["C"], "L": v["C"], "C": v["C"],
            "UL": "0", "LL": "0", "ExRT": None, "Vo": 1, "Va": 1, "AdjFactor": v["AdjFactor"],
            "AdjO": v["AdjC"], "AdjH": v["AdjC"], "AdjL": v["AdjC"], "AdjC": v["AdjC"], "AdjVo": 1}


class JQ:
    calls: ClassVar[list[str]] = []
    broken: ClassVar[set[str]] = set()

    def __enter__(self) -> "JQ":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def equities_master(self, date: str | None = None) -> list[dict]:
        return []

    def bars_daily(self, date: str | None = None, code: str | None = None, **kwargs) -> list[dict]:
        JQ.calls.append(f"bars:{date or code}")
        if date in JQ.broken:
            raise RuntimeError("503")
        if code:
            return [_bar(d, code) for d in API]
        return [_bar(date)] if date in API else []

    def topix(self, **kwargs) -> list[dict]:
        return []

    def fins_summary(self, **kwargs) -> list[dict]:
        return []


def _stale(db) -> None:
    """分割前に取り込んだままの行（調整後が古く、UL / LL が無い）。"""
    upsert(db, DailyPrice, [
        {"code": "285A0", "date": date.fromisoformat(d), "close": API[d]["C"],
         "adj_close": API[d]["C"], "adj_factor": 1.0}
        for d in ("2026-09-25", "2026-09-28")])


def _adj(db, d: str) -> float:
    return db.get(DailyPrice, ("285A0", date.fromisoformat(d))).adj_close


def test_split_day_readjusts_in_db() -> None:
    init_db()
    db = SessionLocal()
    try:
        _stale(db)
        JQ.calls = []
        with patch("app.market.ingest.JQuantsClient", JQ):
            run_daily(db, "2026-09-29")
        assert "bars:285A0" not in JQ.calls  # API で取り直さない（プランの期間より前が取れないため）
        db.expire_all()
        assert _adj(db, "2026-09-28") == 17780.0
        assert _adj(db, "2026-09-25") == 18593.3
    finally:
        db.close()


def test_refetch_fills_flags_and_resumes() -> None:
    init_db()
    db = SessionLocal()
    try:
        db.query(DailyPrice).delete()
        db.commit()
        _stale(db)
        JQ.calls, JQ.broken = [], {"2026-09-28"}
        with patch("app.market.ingest.JQuantsClient", JQ):
            assert run_refetch_prices(db) == 1
        db.expire_all()
        assert _adj(db, "2026-09-25") == 18593.3
        assert db.get(DailyPrice, ("285A0", date(2026, 9, 25))).upper_limit == 0
        assert _adj(db, "2026-09-28") == 53340  # 失敗した日は元のまま
        # 再実行は残った日だけ
        JQ.calls, JQ.broken = [], set()
        with patch("app.market.ingest.JQuantsClient", JQ):
            assert run_refetch_prices(db) == 0
        assert JQ.calls == ["bars:2026-09-28"]
        db.expire_all()
        assert _adj(db, "2026-09-28") == 17780.0
    finally:
        db.close()

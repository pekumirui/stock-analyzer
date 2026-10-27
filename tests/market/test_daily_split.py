"""日次バッチを J-Quants の配信時刻ごとに分ける（KAB-209）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-daily-split-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.market import ingest  # noqa: E402
from app.models import DailyPrice  # noqa: E402
from jobs.market import run_batch  # noqa: E402

calls: list[str] = []


class JQ:
    def __enter__(self) -> "JQ":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def equities_master(self, date: str | None = None) -> list[dict]:
        calls.append("master")
        return []

    def bars_daily(self, date: str | None = None, **kwargs) -> list[dict]:
        calls.append(f"bars:{date}")
        return [{"Code": "72030", "Date": date, "O": 1, "H": 1, "L": 1, "C": 1, "Vo": 1, "AdjFactor": 1}]

    def topix(self, from_: str | None = None, to: str | None = None) -> list[dict]:
        calls.append(f"topix:{from_}")
        return []

    def fins_summary(self, date: str | None = None, **kwargs) -> list[dict]:
        calls.append(f"fins:{date}")
        return []


def test_run_prices_takes_only_prices() -> None:
    init_db()
    db = SessionLocal()
    calls.clear()
    try:
        with patch("app.market.ingest.JQuantsClient", JQ):
            assert ingest.run_prices(db, "2026-10-26") == 1
        assert calls == ["bars:2026-10-26", "topix:2026-10-26"]
    finally:
        db.close()


def test_run_fins_skips_prices_already_in_db() -> None:
    init_db()
    db = SessionLocal()
    calls.clear()
    try:
        upsert(db, DailyPrice, [{"code": "72030", "date": date(2026, 10, 27), "close": 1.0}])
        with patch("app.market.ingest.JQuantsClient", JQ):
            assert ingest.run_fins(db, "2026-10-27") == 0
        # 前営業日は DB の株価で当日より前の最終日（上の run_prices の 10/26）
        assert calls == ["master", "fins:2026-10-27", "fins:2026-10-26"]
    finally:
        db.close()


def test_run_fins_fetches_prices_when_missing() -> None:
    init_db()
    db = SessionLocal()
    calls.clear()
    try:
        with patch("app.market.ingest.JQuantsClient", JQ):
            assert ingest.run_fins(db, "2026-10-28") == 1
        assert calls == ["master", "bars:2026-10-28", "topix:2026-10-28",
                         "fins:2026-10-28", "fins:2026-10-27"]
    finally:
        db.close()


def _main(argv: list[str], n_new: int = 1, current: bool = True) -> list[str]:
    done: list[str] = []
    with patch.object(sys, "argv", ["run_batch.py", *argv]), \
            patch.object(run_batch.ingest, "run_prices", lambda db, day: done.append("prices") or n_new), \
            patch.object(run_batch.ingest, "run_fins", lambda db, day: done.append("fins") or n_new), \
            patch.object(run_batch.analytics, "indicators_current", lambda db: current), \
            patch.object(run_batch.analytics, "price_frame", lambda db: None), \
            patch.object(run_batch.analytics, "compute_indicators", lambda db, px: done.append("indicators")), \
            patch.object(run_batch.analytics, "compute_earnings_events",
                         lambda db, px: done.append("events")), \
            patch.object(run_batch, "sync", lambda db, as_of: done.append("schedule") or 2):
        run_batch.main()
    return done


def test_main_only() -> None:
    assert _main(["--only", "schedule"]) == ["schedule"]
    assert _main(["--only", "prices"]) == ["prices", "indicators", "events"]
    # 決算だけの回は、指標が株価の最終日まで済んでいれば飛ばす
    assert _main(["--only", "fins"]) == ["fins", "events"]
    assert _main(["--only", "fins"], current=False) == ["fins", "indicators", "events"]
    # 配信前で何も増えていなければ再計算しない
    assert _main(["--only", "prices"], n_new=0) == ["prices"]

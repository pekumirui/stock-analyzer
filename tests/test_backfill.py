"""カレンダー空とバックフィル1日の rollback。J-Quants には繋がない。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-backfill-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.market.ingest import BackfillError, run_backfill  # noqa: E402
from app.market.jquants import JQuantsClient  # noqa: E402
from app.models import DailyPrice  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


class _FakeJQ:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "_FakeJQ":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def equities_master(self, date: str | None = None) -> list[dict]:
        return []

    def topix(self, from_: str | None = None, to: str | None = None) -> list[dict]:
        return []

    def trading_days(self, from_: str, to: str) -> list[str]:
        return ["2026-09-01"]

    def bars_daily(self, date: str | None = None, **kwargs) -> list[dict]:
        return [{
            "Code": "72030", "Date": date,
            "O": 1, "H": 1, "L": 1, "C": 1, "Vo": 1, "Va": 1,
            "AdjFactor": 1, "AdjO": 1, "AdjH": 1, "AdjL": 1, "AdjC": 1, "AdjVo": 1,
            "MktCap": 1,
        }]

    def fins_summary(self, date: str | None = None, **kwargs) -> list[dict]:
        raise RuntimeError("fins down")


class _EmptyCalJQ(_FakeJQ):
    def trading_days(self, from_: str, to: str) -> list[str]:
        raise RuntimeError("CALENDAR_EMPTY: no HolDiv=='1' trading days")


def test_trading_days_empty_raises() -> None:
    jq = JQuantsClient(api_key="x")
    try:
        jq.calendar = lambda from_, to: []  # type: ignore[method-assign]
        try:
            jq.trading_days("2026-01-01", "2026-01-05")
        except RuntimeError as exc:
            if "CALENDAR_EMPTY" not in str(exc):
                _fail(f"empty HolDiv: {exc}")
        else:
            _fail("empty HolDiv should raise")

        def boom(from_: str, to: str) -> list[dict]:
            raise RuntimeError("network")

        jq.calendar = boom  # type: ignore[method-assign]
        try:
            jq.trading_days("2026-01-01", "2026-01-05")
        except RuntimeError as exc:
            if "CALENDAR_EMPTY" not in str(exc):
                _fail(f"calendar throw: {exc}")
        else:
            _fail("calendar throw should raise")
    finally:
        jq.close()


def test_backfill_calendar_empty() -> None:
    init_db()
    db = SessionLocal()
    try:
        with patch("app.market.ingest.JQuantsClient", _EmptyCalJQ):
            try:
                run_backfill(db, years=1)
            except BackfillError as exc:
                if exc.code != "CALENDAR_EMPTY":
                    _fail(f"calendar empty code: {exc.code} {exc}")
                if "CALENDAR_EMPTY" not in str(exc):
                    _fail(f"calendar empty message: {exc}")
            else:
                _fail("empty calendar should raise BackfillError")
    finally:
        db.close()


def test_backfill_day_rollback() -> None:
    init_db()
    db = SessionLocal()
    try:
        with patch("app.market.ingest.JQuantsClient", _FakeJQ):
            try:
                run_backfill(db, years=1)
            except BackfillError as exc:
                if exc.code != "BACKFILL_INCOMPLETE":
                    _fail(f"day fail code: {exc.code} {exc}")
                if exc.failed_day != "2026-09-01":
                    _fail(f"failed_day: {exc.failed_day!r}")
            else:
                _fail("statements throw should raise BackfillError")
        leftover = db.query(DailyPrice).all()
        dates = {row.date.isoformat() for row in leftover if row.date is not None}
        if "2026-09-01" in dates:
            _fail(f"price-only day must not stay: {dates}")
    finally:
        db.close()


def main() -> None:
    test_trading_days_empty_raises()
    test_backfill_calendar_empty()
    test_backfill_day_rollback()
    print("ok: backfill")


if __name__ == "__main__":
    main()

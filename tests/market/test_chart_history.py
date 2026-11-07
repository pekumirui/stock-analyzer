"""チャート API は日足を切らずに返す。初期表示が直近 1 年でも、それより前へスクロールできる。"""
from __future__ import annotations

from datetime import date, timedelta

from app.db import SessionLocal, upsert
from app.models import DailyPrice


def _weekdays(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_chart_api_returns_bars_older_than_one_year(c) -> None:
    days = _weekdays(date(2023, 11, 1), date(2026, 11, 6))
    code = "88000"
    db = SessionLocal()
    try:
        upsert(db, DailyPrice, [
            {"code": code, "date": day, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
             "volume": 10.0, "turnover": 1000.0, "adj_factor": 1.0,
             "adj_open": 100.0, "adj_high": 110.0, "adj_low": 90.0, "adj_close": 105.0,
             "adj_volume": 10.0}
            for day in days
        ])
    finally:
        db.close()
    r = c.get(f"/api/chart/{code}")
    assert r.status_code == 200
    got = r.json()["t"]
    assert got == [day.isoformat() for day in days]
    assert got[0] < (date.fromisoformat(got[-1]).replace(year=date.fromisoformat(got[-1]).year - 1)).isoformat()
    assert len(got) > 400

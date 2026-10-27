"""ランキングタブの決算発表後パフォーマンス（KAB-192 / KAB-194）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-ranking-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.main import app  # noqa: E402
from app.market.analytics import compute_earnings_events, first_disclosures  # noqa: E402
from app.models import DailyPrice, EarningsEvent, Statement, Stock, Topix  # noqa: E402

D = date.fromisoformat
FY = D("2027-03-31")


def test_first_disclosures() -> None:
    got = first_disclosures([
        ("11110", D("2026-10-14"), "15:30:00", FY, "2Q", False),
        ("11110", D("2026-11-04"), "15:30:00", FY, "2Q", False),   # 訂正。起点にしない
        ("11110", D("2026-10-14"), "13:00:00", FY, "FY", True),    # 予想修正だけ
        ("22220", D("2026-10-30"), "16:00:00", FY, "2Q", False),
        ("22220", D("2026-10-30"), "13:00:00", FY, "1Q", False),   # 同じ日の別の期。早い時刻
    ])
    assert got == {("11110", D("2026-10-14")): "15:30:00", ("22220", D("2026-10-30")): "13:00:00"}


def _stmt(no, code, d, t, period="2Q", revision=False):
    return {"disclosure_no": no, "code": code, "disclosed_date": D(d), "disclosed_time": t,
            "type_of_period": period, "fiscal_year_end": FY, "is_revision": revision}


def test_batch_marks_first_disclosure() -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, Statement, [
            _stmt("a1", "11110", "2026-10-14", "15:30:00"),
            _stmt("a2", "11110", "2026-10-21", "15:30:00"),            # 訂正
            _stmt("a3", "11110", "2026-10-14", "13:00:00", revision=True),
        ])
        days = [D("2026-10-13") + timedelta(days=i) for i in range(12)]
        upsert(db, DailyPrice, [{"code": "11110", "date": d, "open": 100, "high": 110, "low": 90,
                                 "close": 105, "adj_open": 100, "adj_close": 105} for d in days])
        upsert(db, Topix, [{"date": d, "open": 2000, "close": 2000} for d in days])
        compute_earnings_events(db)
        e = db.get(EarningsEvent, ("11110", D("2026-10-14")))
        # 予想修正の 13:00 ではなく決算短信の 15:30。引け後なので翌日の始値から
        assert (e.is_first, e.disclosed_time, e.base_date) == (True, "15:30:00", D("2026-10-15"))
        assert db.get(EarningsEvent, ("11110", D("2026-10-21"))).is_first is False
    finally:
        db.close()


def _ev(code, d, r, a, t="15:30:00", first=True):
    return {"code": code, "disclosed_date": D(d), "base_date": D(d) + timedelta(days=1),
            "base_price": 1000, "return_to_date": r, "alpha_to_date": a,
            "disclosed_time": t, "is_first": first}


def test_earnings_perf_ranking() -> None:
    db = SessionLocal()
    try:
        db.query(EarningsEvent).delete()
        db.query(DailyPrice).delete()
        db.commit()
        upsert(db, Stock, [{"code": c, "name": n} for c, n in
                           (("11110", "Ａ社"), ("22220", "B社"), ("44440", "D社"))])
        upsert(db, EarningsEvent, [
            _ev("11110", "2026-10-14", 5.0, 3.0), _ev("11110", "2026-11-04", 50.0, 50.0, first=False),
            _ev("22220", "2026-10-30", 12.5, 10.0, t="13:00:00"), _ev("33330", "2026-10-20", 99.0, 99.0, first=False),
            _ev("44440", "2026-09-30", 1.0, 1.0, t="15:00:00"), _ev("44440", "2026-10-01", None, None, t="16:00:00"),
        ])
        upsert(db, DailyPrice, [{"code": "11110", "date": D("2026-11-05"), "close": 1}])
    finally:
        db.close()
    c = TestClient(app)
    d = c.get("/api/ranking/earnings-perf").json()
    assert d["months"] == ["2026-10", "2026-09"]
    assert d["month"] == "2026-10" and d["as_of"] == "2026-11-05"
    got = [(r["rank"], r["code"], r["session"], r["return_to_date"]) for r in d["rows"]]
    # 最初の決算短信でない行は出ない。値の無い D 社は最後
    assert got == [(1, "22220", "intraday", 12.5), (2, "11110", "after", 5.0), (3, "44440", "after", None)]
    top = d["rows"][0]
    assert top["name"] == "B社" and top["alpha_to_date"] == 10.0 and top["disclosed_time"] == "13:00"
    assert top["days"] == (date.today() - D("2026-10-30")).days
    sep = c.get("/api/ranking/earnings-perf", params={"month": "2026-09"}).json()
    assert [(r["code"], r["session"]) for r in sep["rows"]] == [("44440", "intraday")]
    assert c.get("/api/ranking/earnings-perf", params={"month": "2026-08"}).json()["rows"] == []
    for bad in ("2026-13", "2026/10", "26-10"):
        assert c.get("/api/ranking/earnings-perf", params={"month": bad}).status_code == 400

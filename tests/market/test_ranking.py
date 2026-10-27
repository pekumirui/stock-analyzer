"""ランキングタブの決算発表後パフォーマンス（KAB-192）。"""
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
from app.models import DailyPrice, EarningsEvent, Statement, Stock  # noqa: E402

D = date.fromisoformat
FY = D("2027-03-31")


def _stmt(no, code, d, t, period="2Q", revision=False):
    return {"disclosure_no": no, "code": code, "disclosed_date": D(d), "disclosed_time": t,
            "type_of_period": period, "fiscal_year_end": FY, "is_revision": revision,
            "type_of_document": "EarnForecastRevision" if revision else "2QFinancialStatements_Consolidated_JP"}


def _ev(code, d, r, a):
    return {"code": code, "disclosed_date": D(d), "base_date": D(d) + timedelta(days=1),
            "base_price": 1000, "return_to_date": r, "alpha_to_date": a}


def _seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, Stock, [{"code": c, "name": n} for c, n in
                           (("11110", "Ａ社"), ("22220", "B社"), ("33330", "C社"), ("44440", "D社"))])
        upsert(db, Statement, [
            _stmt("a1", "11110", "2026-10-14", "15:30:00"),
            _stmt("a2", "11110", "2026-11-04", "15:30:00"),          # 訂正。起点にしない
            _stmt("b1", "22220", "2026-10-30", "13:00:00"),
            _stmt("c1", "33330", "2026-10-20", "16:00:00", revision=True),  # 予想修正だけ
            _stmt("d1", "44440", "2026-09-30", "15:00:00", period="1Q"),
            _stmt("d2", "44440", "2026-10-01", "16:00:00"),
        ])
        upsert(db, EarningsEvent, [
            _ev("11110", "2026-10-14", 5.0, 3.0), _ev("11110", "2026-11-04", 50.0, 50.0),
            _ev("22220", "2026-10-30", 12.5, 10.0), _ev("33330", "2026-10-20", 99.0, 99.0),
            _ev("44440", "2026-09-30", 1.0, 1.0), _ev("44440", "2026-10-01", None, None),
        ])
        upsert(db, DailyPrice, [{"code": "11110", "date": D("2026-11-05"), "close": 1}])
    finally:
        db.close()


def test_earnings_perf_ranking() -> None:
    _seed()
    c = TestClient(app)
    d = c.get("/api/ranking/earnings-perf").json()
    assert d["months"] == ["2026-10", "2026-09"]
    assert d["month"] == "2026-10" and d["as_of"] == "2026-11-05"
    got = [(r["rank"], r["code"], r["session"], r["return_to_date"]) for r in d["rows"]]
    # 訂正（11/4）と予想修正だけの C 社は出ない。値の無い D 社は最後
    assert got == [(1, "22220", "intraday", 12.5), (2, "11110", "after", 5.0), (3, "44440", "after", None)]
    top = d["rows"][0]
    assert top["name"] == "B社" and top["alpha_to_date"] == 10.0 and top["disclosed_time"] == "13:00"
    assert top["days"] == (date.today() - D("2026-10-30")).days
    sep = c.get("/api/ranking/earnings-perf", params={"month": "2026-09"}).json()
    assert [(r["code"], r["session"]) for r in sep["rows"]] == [("44440", "intraday")]
    assert c.get("/api/ranking/earnings-perf", params={"month": "2026-08"}).json()["rows"] == []
    for bad in ("2026-13", "2026/10", "26-10"):
        assert c.get("/api/ranking/earnings-perf", params={"month": bad}).status_code == 400

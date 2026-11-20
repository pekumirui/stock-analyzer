"""ランキングタブの決算発表後パフォーマンス（KAB-192 / KAB-194 / KAB-195 / KAB-198 / KAB-201）。"""
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

from fastapi.testclient import TestClient

from app.db import SessionLocal, init_db, upsert
from app.main import app
from app.market.analytics import compute_earnings_events, first_disclosures
from app.models import DailyPrice, EarningsEvent, Statement, Stock, Topix

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
            _stmt("a4", "11110", "2026-10-22", "15:30:00", period="3Q"),  # 次の期
        ])
        days = [D("2026-10-13") + timedelta(days=i) for i in range(12)]
        upsert(db, DailyPrice, [{"code": "11110", "date": d, "open": 100, "high": 110, "low": 90,
                                 "close": 105, "adj_open": 100, "adj_close": 105} for d in days])
        upsert(db, Topix, [{"date": d, "open": 2000, "close": 2000} for d in days])
        compute_earnings_events(db)
        e = db.get(EarningsEvent, ("11110", D("2026-10-14")))
        assert (e.is_first, e.disclosed_time, e.base_date) == (True, "15:30:00", D("2026-10-15"))
        assert db.get(EarningsEvent, ("11110", D("2026-10-21"))).is_first is False
        assert (e.next_disclosed_date, e.end_date, e.return_to_next, e.alpha_to_next) == \
            (D("2026-10-22"), D("2026-10-21"), 5.0, 5.0)
        e = db.get(EarningsEvent, ("11110", D("2026-10-22")))
        assert (e.next_disclosed_date, e.end_date, e.return_to_next) == (None, D("2026-10-24"), 5.0)
    finally:
        db.close()


def _ev(code, d, r, a, t="15:30:00", first=True, nxt=None):
    return {"code": code, "disclosed_date": D(d), "base_date": D(d) + timedelta(days=1),
            "base_price": 1000, "return_to_next": r, "alpha_to_next": a, "return_to_date": -1,
            "disclosed_time": t, "is_first": first, "next_disclosed_date": D(nxt) if nxt else None,
            "end_date": D(nxt) - timedelta(days=1) if nxt else D("2026-11-05")}


def test_earnings_perf_ranking() -> None:
    db = SessionLocal()
    try:
        db.query(EarningsEvent).delete()
        db.query(DailyPrice).delete()
        db.commit()
        db.query(Topix).delete()
        upsert(db, Stock, [{"code": c, "name": n, "market": mk, "sector33": sec} for c, n, mk, sec in
                           (("11110", "Ａ社", "プライム", "小売業"), ("22220", "B社", "グロース", "情報・通信業"),
                            ("44440", "D社", None, None))])
        upsert(db, Topix, [{"date": D(x), "close": 1} for x in
                           ("2026-09-30", "2026-10-01", "2026-10-30", "2026-11-02", "2026-11-04", "2026-11-05")])
        upsert(db, EarningsEvent, [
            _ev("11110", "2026-10-14", 5.0, 3.0), _ev("11110", "2026-11-04", 50.0, 50.0, first=False),
            _ev("22220", "2026-10-30", 12.5, 10.0, t="13:00:00"), _ev("33330", "2026-10-20", 99.0, 99.0, first=False),
            _ev("44440", "2026-09-30", 1.0, 1.0, t="15:00:00", nxt="2026-10-01"),
            _ev("44440", "2026-10-01", None, None, t="16:00:00"),
        ])
        upsert(db, DailyPrice, [{"code": "11110", "date": D("2026-11-05"), "close": 1}])
    finally:
        db.close()
    c = TestClient(app)
    d = c.get("/api/ranking/earnings-perf").json()
    assert d["months"] == ["2026-10", "2026-09"]
    assert d["month"] == "2026-10" and d["as_of"] == "2026-11-05"
    got = [(r["rank"], r["code"], r["session"], r["return_to_next"]) for r in d["rows"]]
    assert got == [(1, "22220", "intraday", 12.5), (2, "11110", "after", 5.0), (3, "44440", "after", None)]
    top = d["rows"][0]
    assert top["name"] == "B社" and top["alpha_to_next"] == 10.0 and top["disclosed_time"] == "13:00"
    assert (top["market"], top["sector33"]) == ("グロース", "情報・通信業")
    assert (top["next_disclosed_date"], top["end_date"], top["bdays"]) == (None, "2026-11-05", 3)
    sep = c.get("/api/ranking/earnings-perf", params={"month": "2026-09"}).json()
    assert [(r["code"], r["session"], r["end_date"], r["bdays"]) for r in sep["rows"]] == \
        [("44440", "intraday", "2026-09-30", 0)]
    assert c.get("/api/ranking/earnings-perf", params={"month": "2026-08"}).json()["rows"] == []
    for bad in ("2026-13", "2026/10", "26-10"):
        assert c.get("/api/ranking/earnings-perf", params={"month": bad}).status_code == 400


def test_pts_tab_moved_into_ranking() -> None:
    """PTS はランキングの中のタブになった（KAB-265）。古い #pts は #ranking/pts へ読み替える。"""
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    tabs = (ROOT / "static/js/tabs.js").read_text(encoding="utf-8")
    rk = (ROOT / "static/js/ranking.js").read_text(encoding="utf-8")
    pts = (ROOT / "static/js/pts.js").read_text(encoding="utf-8")
    nav = html[html.index("<nav id=\"app-nav\">"):html.index("</nav>")]
    ranking = html[html.index('id="tab-ranking"'):html.index('id="tab-monthly"')]
    assert 'data-tab="pts"' not in html and 'id="tab-pts"' not in html
    assert 'data-tab="pts"' not in nav
    assert 'id="rk-subs"' in ranking and 'data-rksub="perf"' in ranking and 'data-rksub="pts"' in ranking
    assert ">決算後パフォーマンス</button>" in ranking and ">PTS</button>" in ranking
    assert 'id="rk-sub-perf"' in ranking and 'id="rk-sub-pts"' in ranking
    for el in ('id="pts-date"', 'id="pts-ranking"', 'id="pts-at"', 'id="pts-info"',
               'id="pts-summary"', 'id="pts-tbl"', 'data-ranking="night_up"',
               'data-ranking="night_down"', 'data-ranking="night_volume"'):
        assert el in ranking
    assert 'id="rk-kind"' in ranking and 'id="rk-tbl"' in ranking
    tabs_lit = tabs.split("const TABS")[1].split(";")[0]
    assert '"pts"' not in tabs_lit
    assert 'const RANKING_SUBS = ["pts"]' in tabs
    assert 'if(tab === "pts")' in tabs and 'code: "pts"' in tabs
    assert "showRankingSub" in tabs and "showRankingSub" in rk
    assert 'rkSub === "pts"' in rk
    assert "$(\"#pts-date\")" in pts and "$(\"#pts-tbl\")" in pts
    assert 'id="pts-tbl" class="sheet"' in html
    assert "shortName(raw)" in pts
    assert "<thead>" in pts and "</tbody>" in pts

"""決算発表後パフォーマンスの基準日と基準価格（KAB-187 / KAB-191）。

株価はトーメンデバイス・キオクシアの J-Quants の日足そのまま。残りは合成。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-earnings-perf-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.market.analytics import compute_earnings_events, no_open_days  # noqa: E402
from app.market.ingest import _price_row  # noqa: E402
from app.market.price_limit import at_limit, price_limit  # noqa: E402
from app.models import DailyPrice, EarningsEvent, Statement, Topix  # noqa: E402

D = date.fromisoformat

# (日付, O, H, L, C, UL, LL)。UL / LL が None の行はフラグ取込前の過去データ
BARS = {
    # トーメンデバイス: 7/30 13:00 発表。当日は寄ってストップ高引け、7/31 は寄らずのストップ高
    "27370": [
        ("2026-07-29", 14910, 15090, 13540, 14150, "0", "0"),
        ("2026-07-30", 14330, 17150, 14080, 17150, "1", "0"),
        ("2026-07-31", 21150, 21150, 21150, 21150, "1", "0"),
        ("2026-08-03", 25150, 26150, 25110, 26150, "1", "0"),
        ("2026-08-04", 28140, 31150, 28040, 31150, "1", "0"),
    ],
    # キオクシア: 5/15 15:30 発表。5/18 は寄らずのストップ高
    "285A0": [
        ("2026-05-14", 50070, 53490, 45110, 48460, "0", "0"),
        ("2026-05-15", 48460, 48470, 42100, 44450, "0", "0"),
        ("2026-05-18", 51450, 51450, 51450, 51450, "1", "0"),
        ("2026-05-19", 52010, 52590, 48000, 49770, "0", "0"),
        ("2026-05-20", 51000, 51450, 48270, 51290, "0", "0"),
    ],
    # 2 日続けて寄らずのストップ高。フラグ無しなので値幅表で判定する
    "11110": [
        ("2026-05-14", 1000, 1000, 990, 1000, None, None),
        ("2026-05-15", 1000, 1010, 1000, 1000, None, None),
        ("2026-05-18", 1300, 1300, 1300, 1300, None, None),
        ("2026-05-19", 1600, 1600, 1600, 1600, None, None),
        ("2026-05-20", 1800, 1900, 1750, 1850, None, None),
    ],
    # 寄らずのストップ安
    "22220": [
        ("2026-05-15", 1000, 1010, 990, 1000, "0", "0"),
        ("2026-05-18", 850, 850, 850, 850, "0", "1"),
        ("2026-05-19", 800, 820, 780, 790, "0", "0"),
        ("2026-05-20", 795, 800, 790, 800, "0", "0"),
    ],
    # 1 回しか約定しない薄商いの日（四本値は同じだが値幅いっぱいではない）
    "33330": [
        ("2026-05-15", 1000, 1000, 1000, 1000, None, None),
        ("2026-05-18", 1010, 1010, 1010, 1010, None, None),
        ("2026-05-19", 1020, 1030, 1020, 1030, None, None),
    ],
    # 場中の発表。当日の終値で買える
    "55550": [
        ("2026-05-14", 1000, 1000, 990, 1000, "0", "0"),
        ("2026-05-15", 1000, 1050, 990, 1020, "0", "0"),
        ("2026-05-18", 1030, 1060, 1020, 1040, "0", "0"),
    ],
    # 場中の発表で当日はストップ高引け（フラグ無し）。引けでは買えないので翌営業日の始値
    "66660": [
        ("2026-05-14", 1000, 1000, 990, 1000, None, None),
        ("2026-05-15", 1010, 1300, 1000, 1300, None, None),
        ("2026-05-18", 1350, 1400, 1320, 1380, None, None),
    ],
    # 大引けが 15:00 だったころの 15:00 発表は引け後
    "44440": [
        ("2024-05-14", 1000, 1000, 990, 1000, "0", "0"),
        ("2024-05-15", 1010, 1020, 1000, 1010, "0", "0"),
        ("2024-05-16", 1050, 1080, 1040, 1070, "0", "0"),
    ],
}
STMTS = [
    ("27370", "2026-07-30", "13:00:00"),
    ("285A0", "2026-05-15", "15:30:00"),
    ("11110", "2026-05-15", "15:30:00"),
    ("22220", "2026-05-15", "16:00:00"),
    ("33330", "2026-05-15", "15:30:00"),
    ("44440", "2024-05-15", "15:00:00"),
    ("55550", "2026-05-15", "10:00:00"),
    ("66660", "2026-05-15", "11:30:00"),
]


def _seed(db) -> None:
    px, days = [], set()
    for code, bars in BARS.items():
        for d, o, h, lo, c, ul, ll in bars:
            q = {"Code": code, "Date": d, "O": o, "H": h, "L": lo, "C": c, "Vo": 1, "Va": 1,
                 "AdjFactor": 1, "AdjO": o, "AdjH": h, "AdjL": lo, "AdjC": c, "AdjVo": 1}
            if ul is not None:
                q.update(UL=ul, LL=ll)
            px.append(_price_row(q))
            days.add(D(d))
    upsert(db, DailyPrice, px)
    upsert(db, Topix, [{"date": d, "open": 2000, "high": 2000, "low": 2000, "close": 2020}
                       for d in sorted(days)])
    upsert(db, Statement, [{"disclosure_no": f"{code}-{d}", "code": code, "disclosed_date": D(d),
                            "disclosed_time": t} for code, d, t in STMTS])


def test_price_limit() -> None:
    assert price_limit(99) == 30
    assert price_limit(100) == 50
    assert price_limit(14150) == 3000
    assert price_limit(44450) == 7000
    assert at_limit(1000, 1300) and at_limit(1000, 700) and at_limit(999, 1149)
    assert not at_limit(1000, 1200) and not at_limit(None, 1300)


def test_ingest_flags() -> None:
    row = _price_row({"Code": "1", "Date": "2026-05-18", "C": 1, "UL": "1", "LL": "0"})
    assert (row["upper_limit"], row["lower_limit"]) == (1, 0)
    row = _price_row({"Code": "1", "Date": "2026-05-18", "C": 1})
    assert (row["upper_limit"], row["lower_limit"]) == (None, None)


def test_base_is_first_open_after_earnings() -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed(db)
        assert no_open_days(db) == {
            ("27370", D("2026-07-31")), ("285A0", D("2026-05-18")),
            ("11110", D("2026-05-18")), ("11110", D("2026-05-19")), ("22220", D("2026-05-18")),
        }
        compute_earnings_events(db)

        def ev(code, d):
            return db.get(EarningsEvent, (code, D(d)))

        e = ev("27370", "2026-07-30")
        assert (e.base_date, e.base_price) == (D("2026-08-03"), 25150)
        assert e.return_1d == round((26150 / 25150 - 1) * 100, 2)
        assert e.alpha_1d == round((26150 / 25150 - 2020 / 2000) * 100, 2)
        assert e.return_to_date == round((31150 / 25150 - 1) * 100, 2)
        e = ev("55550", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-15"), 1020)
        assert e.return_1d == e.alpha_1d == round((1040 / 1020 - 1) * 100, 2)
        e = ev("66660", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-18"), 1350)
        e = ev("285A0", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-19"), 52010)
        assert e.return_1d == round((49770 / 52010 - 1) * 100, 2)
        assert e.return_5d is None
        e = ev("11110", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-20"), 1800)
        e = ev("22220", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-19"), 800)
        e = ev("33330", "2026-05-15")
        assert (e.base_date, e.base_price) == (D("2026-05-18"), 1010)
        e = ev("44440", "2024-05-15")
        assert (e.base_date, e.base_price) == (D("2024-05-16"), 1050)
    finally:
        db.close()

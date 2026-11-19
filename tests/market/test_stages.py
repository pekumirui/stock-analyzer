"""ミネルヴィニのステージ判定（docs/OVERVIEW.md 4.2）。作った株価の並びで最終日のステージを見る。"""
from datetime import date, timedelta

from app.db import SessionLocal, upsert
from app.market.analytics import compute_indicators
from app.models import DailyPrice, Indicator

CODE = "11110"


def _stages(closes: list[float], volumes: list[float] | None = None) -> list:
    """closes を 1 銘柄の日足として入れ、日ごとのステージを返す。銘柄が 1 つなので RS は 99。"""
    db = SessionLocal()
    try:
        db.query(DailyPrice).delete()
        db.query(Indicator).delete()
        db.commit()
        day = date(2024, 1, 1)
        rows = []
        for i, c in enumerate(closes):
            v = volumes[i] if volumes else 1000.0
            rows.append({"code": CODE, "date": day + timedelta(days=i), "open": c, "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": v, "adj_factor": 1.0,
                         "adj_open": c, "adj_high": c * 1.01, "adj_low": c * 0.99, "adj_close": c,
                         "adj_volume": v})
        upsert(db, DailyPrice, rows)
        compute_indicators(db, keep_days=len(closes))
        return [r.stage for r in db.query(Indicator).order_by(Indicator.date).all()]
    finally:
        db.close()


def _uptrend(n: int, start: float = 100.0, rate: float = 0.003) -> list[float]:
    return [start * (1 + rate) ** i for i in range(n)]


def test_stage2_uptrend():
    assert _stages(_uptrend(300))[-1] == 2


def test_stage2_needs_ma200_rising_every_day():
    closes = _uptrend(300)
    k = len(closes) - 1 - 205
    closes[k] = closes[k] * 2.2
    st = _stages(closes)
    assert st[-1] != 2
    assert st[-6] != 2
    assert st[-30] == 2


def test_stage1_base_far_above_low():
    closes = [100.0 + (1 if i % 2 else -1) for i in range(300)]
    closes[-100] = 70.0
    assert _stages(closes)[-1] == 1


def _topping(signal: str) -> tuple[list[float], list[float]]:
    """上昇のあと高値圏で 100 日もみ合い、200 日線の傾きが最大の半分を割る。

    signal が wide なら最後の 20 日だけ値幅を広げ、heavy なら最後の 20 日の下げに出来高 3 倍、
    none ならどちらも無い。最終日は下げの日（50 日線の下）。
    """
    up = _uptrend(260)
    top = up[-1]
    n = 100
    side = []
    for i in range(n):
        lo, hi = (0.88, 0.99) if signal == "wide" and i >= n - 20 else (0.93, 0.97)
        side.append(top * (lo if i % 2 else hi))
    closes = up + side
    volumes = [1000.0] * len(closes)
    if signal == "heavy":
        for i in range(len(closes) - 20, len(closes)):
            if closes[i] < closes[i - 1]:
                volumes[i] = 3000.0
    return closes, volumes


def test_stage3_topping_with_heavy_volume():
    closes, volumes = _topping("heavy")
    st = _stages(closes, volumes)
    assert 2 in st[-120:]
    assert st[-1] == 3


def test_stage3_without_volume_by_volatility():
    closes, volumes = _topping("wide")
    assert _stages(closes, volumes)[-1] == 3


def test_stage3_needs_a_warning_sign():
    closes, volumes = _topping("none")
    assert _stages(closes, volumes)[-1] is None


def test_stage3_includes_ma200_still_rising():
    closes, volumes = _topping("heavy")
    _stages(closes, volumes)
    db = SessionLocal()
    try:
        last = db.query(Indicator).order_by(Indicator.date.desc()).first()
    finally:
        db.close()
    assert last.ma200_slope_1m > 0.005 and last.stage == 3


def test_stage3_needs_recent_stage2():
    closes = [100.0 + (8 if i % 2 else -8) * (1 if i > 260 else 0.1) for i in range(300)]
    assert _stages(closes)[-1] != 3


def test_stage4_decline_near_low():
    closes = list(reversed(_uptrend(300)))
    assert _stages(closes)[-1] == 4

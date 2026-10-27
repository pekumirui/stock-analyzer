"""yfinance 1 分足の保存と分割調整（KAB-208）。ネットワークは使わない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
from yfinance.exceptions import YFRateLimitError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-intraday-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, upsert  # noqa: E402
from app.market import intraday  # noqa: E402
from app.models import DailyPrice  # noqa: E402

D = date.fromisoformat


def _bars(code: str, day: str, price: float, n: int = 3, volume: int = 100) -> pd.DataFrame:
    ts = pd.date_range(f"{day} 09:05", periods=n, freq="1min", tz="Asia/Tokyo")
    return pd.DataFrame({"code": code, "ts": ts, "open": price, "high": price, "low": price,
                         "close": price, "volume": volume})


def _daily(code, d, high, factor=1.0, ex_rt=None):
    return {"code": code, "date": D(d), "open": high, "high": high, "low": high, "close": high,
            "volume": 1, "adj_factor": factor, "ex_rt": ex_rt}


def test_yahoo_symbol() -> None:
    assert intraday.yahoo_symbol("72030") == "7203.T"
    assert intraday.yahoo_symbol("130A0") == "130A.T"


def test_normalize() -> None:
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10.0]},
        index=pd.DatetimeIndex(pd.to_datetime(["2026-10-27 09:05"]).tz_localize("Asia/Tokyo"),
                               name="Datetime"))
    out = intraday.normalize("72030", raw)
    assert list(out.columns) == intraday.COLUMNS
    assert out["volume"].dtype == "int64"
    assert intraday.normalize("72030", raw.iloc[:0]).empty


def test_write_day_keeps_existing(tmp_path: Path) -> None:
    d = D("2026-10-27")
    assert intraday.write_day(tmp_path, d, _bars("72030", "2026-10-27", 3000)) == 1
    # 一度書いた銘柄は上書きしない。無い銘柄だけ足す
    again = pd.concat([_bars("72030", "2026-10-27", 1000), _bars("67580", "2026-10-27", 4000)])
    assert intraday.write_day(tmp_path, d, again) == 1
    got = intraday.read_day(tmp_path, d)
    assert sorted(got["code"].unique()) == ["67580", "72030"]
    assert (got[got["code"] == "72030"]["close"] == 3000).all()
    assert intraday.day_path(tmp_path, d) == tmp_path / "2026" / "2026-10-27.parquet"


def test_store_skips_days_before_split(tmp_path: Path) -> None:
    daily = pd.DataFrame([
        {"code": "11110", "date": D("2026-10-26"), "high": 3000, "adj_factor": 1.0},
        {"code": "11110", "date": D("2026-10-27"), "high": 1000, "adj_factor": 1 / 3},
        {"code": "22220", "date": D("2026-10-26"), "high": 500, "adj_factor": 1.0},
        {"code": "22220", "date": D("2026-10-27"), "high": 500, "adj_factor": 1.0},
        # Yahoo だけが調整した（DB に権利落ちが無い）日は高値の比で弾く
        {"code": "33330", "date": D("2026-10-26"), "high": 900, "adj_factor": 1.0},
        {"code": "33330", "date": D("2026-10-27"), "high": 300, "adj_factor": 1.0},
    ])
    got = {
        # 10/27 に 1:3。Yahoo は 10/26 の足を 1000 に直して返す
        "11110": pd.concat([_bars("11110", "2026-10-26", 1000), _bars("11110", "2026-10-27", 1000)]),
        "22220": pd.concat([_bars("22220", "2026-10-26", 500), _bars("22220", "2026-10-27", 500)]),
        "33330": pd.concat([_bars("33330", "2026-10-26", 300), _bars("33330", "2026-10-27", 300)]),
    }
    res = intraday.store(tmp_path, got, D("2026-10-27"), daily)
    assert res == {"days": 2, "added": 4, "skipped": 2}
    assert sorted(intraday.read_day(tmp_path, D("2026-10-26"))["code"].unique()) == ["22220"]
    assert len(intraday.read_day(tmp_path, D("2026-10-27"))["code"].unique()) == 3


def test_store_only_latest_when_db_is_behind(tmp_path: Path) -> None:
    daily = pd.DataFrame([{"code": "22220", "date": D("2026-10-26"), "high": 500, "adj_factor": 1.0}])
    got = {"22220": pd.concat([_bars("22220", "2026-10-26", 500), _bars("22220", "2026-10-27", 500)])}
    # 10/27 の日足がまだ DB に無い。10/27 に権利落ちがあるかもしれないので 10/26 は書かない
    res = intraday.store(tmp_path, got, D("2026-10-26"), daily)
    assert res == {"days": 1, "added": 1, "skipped": 1}


def test_load_adjusts_with_daily_factor(tmp_path: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, DailyPrice, [
            _daily("11110", "2026-10-26", 3000),
            _daily("11110", "2026-10-27", 1000, factor=1 / 3, ex_rt="1"),
            _daily("22220", "2026-10-26", 1000),
            _daily("22220", "2026-10-27", 900, factor=0.9, ex_rt="3"),
        ])
        for code, p26, p27 in (("11110", 3000, 1000), ("22220", 1000, 900)):
            intraday.write_day(tmp_path, D("2026-10-26"), _bars(code, "2026-10-26", p26, volume=300))
            intraday.write_day(tmp_path, D("2026-10-27"), _bars(code, "2026-10-27", p27, volume=300))

        raw = intraday.load(tmp_path, "11110", D("2026-10-26"), D("2026-10-27"))
        assert raw["close"].tolist() == [3000] * 3 + [1000] * 3

        adj = intraday.load(tmp_path, "11110", D("2026-10-26"), D("2026-10-27"), db)
        assert adj["close"].round(6).tolist() == [1000] * 6
        assert adj["volume"].round(6).tolist() == [900] * 3 + [300] * 3

        # ライツイシューは出来高を調整しない
        adj = intraday.load(tmp_path, "22220", D("2026-10-26"), D("2026-10-26"), db)
        assert adj["close"].round(6).tolist() == [900] * 3
        assert adj["volume"].tolist() == [300] * 3
        assert intraday.load(tmp_path, "99990", D("2026-10-26"), D("2026-10-27"), db).empty
    finally:
        db.close()


def test_collect_waits_on_rate_limit() -> None:
    calls: list[str] = []

    def fetcher(code: str) -> pd.DataFrame:
        calls.append(code)
        if code == "22220" and calls.count(code) == 1:
            raise YFRateLimitError()
        if code == "33330":
            raise KeyError("tradingPeriods")
        if code == "44440":
            return pd.DataFrame(columns=intraday.COLUMNS)
        return _bars(code, "2026-10-27", 100)

    got, stats = intraday.collect(["11110", "22220", "33330", "44440"], fetcher, sleep=0,
                                  rate_limit_waits=(0,), log=lambda _: None)
    assert sorted(got) == ["11110", "22220"]
    assert stats == {"ok": 2, "empty": 1, "failed": 1, "rate_limited": 1}


def test_collect_gives_up_after_waits() -> None:
    def fetcher(code: str) -> pd.DataFrame:
        if code == "22220":
            raise YFRateLimitError()
        return _bars(code, "2026-10-27", 100)

    got, stats = intraday.collect(["11110", "22220", "33330"], fetcher, sleep=0,
                                  rate_limit_waits=(0, 0), log=lambda _: None)
    assert sorted(got) == ["11110"]
    assert stats == {"ok": 1, "empty": 0, "failed": 2, "rate_limited": 3}


def test_fetch_treats_missing_minute_bars_as_empty() -> None:
    class NoMinute:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def history(self, **kw):
            raise KeyError("tradingPeriods")

    assert intraday.fetch("158A0", ticker=NoMinute).empty

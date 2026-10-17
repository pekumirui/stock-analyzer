"""KAB-34: momentum backtest のヘルパ切り出しが数値結果を変えないこと。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKTEST_PATH = ROOT / "analyzer" / "momentum" / "backtest.py"

PRE_STALE_LIMIT = 5
PRE_HOLD_MONTHS = 3
PRE_NW_LAGS = PRE_HOLD_MONTHS - 1
PRE_TURNOVER_WINDOW = 20


def load_backtest():
    spec = importlib.util.spec_from_file_location("momentum_backtest", BACKTEST_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


bt = load_backtest()


def pre_q1_strong_quintiles(form_ex: pd.Series):
    """改名前の inline qcut（labels=False + 1、強い順）。"""
    return pd.qcut(form_ex.rank(method="first", ascending=False), 5, labels=False) + 1


def pre_newey_west_t(values: np.ndarray) -> float:
    """改名前の stats() 内 HAC ループ。"""
    n = len(values)
    mean = values.mean()
    x = values - mean
    s = (x @ x) / n
    for k in range(1, PRE_NW_LAGS + 1):
        s += 2 * (1 - k / (PRE_NW_LAGS + 1)) * (x[k:] @ x[:-k]) / n
    return mean / np.sqrt(s / n)


def pre_month_end_decision_dates(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    me = list(pd.Series(dates, index=dates).groupby(dates.to_period("M")).max())
    if (me[-1] + pd.offsets.BDay(1)).month == me[-1].month:
        me = me[:-1]
    return me


def pre_build_panel(
    adj: pd.DataFrame,
    turnover: pd.DataFrame,
    topix: pd.Series,
    min_turnover: float,
    form_months: int = PRE_HOLD_MONTHS,
    skip_months: int = 0,
) -> pd.DataFrame:
    dates = adj.index
    me = list(pd.Series(dates, index=dates).groupby(dates.to_period("M")).max())
    if (me[-1] + pd.offsets.BDay(1)).month == me[-1].month:
        me = me[:-1]

    fresh = adj.ffill(limit=PRE_STALE_LIMIT)
    last_px = adj.ffill()
    liq = turnover.rolling(PRE_TURNOVER_WINDOW, min_periods=PRE_TURNOVER_WINDOW).mean()

    rows = []
    for i in range(form_months, len(me) - PRE_HOLD_MONTHS):
        d0, de, d, d1 = me[i - form_months], me[i - skip_months], me[i], me[i + PRE_HOLD_MONTHS]
        p0, p, p1 = fresh.loc[d0], adj.loc[d], last_px.loc[d1]
        pe = p if skip_months == 0 else fresh.loc[de]
        ok = p0.notna() & pe.notna() & p.notna() & (liq.loc[d] >= min_turnover)
        codes = ok[ok].index
        tx_form = topix.loc[de] / topix.loc[d0] - 1
        tx_hold = topix.loc[d1] / topix.loc[d] - 1
        delisted = adj.loc[d1:, codes].isna().all()
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "code": codes,
                    "form_ex": pe[codes] / p0[codes] - 1 - tx_form,
                    "hold_ex": p1[codes] / p[codes] - 1 - tx_hold,
                    "turnover20": liq.loc[d, codes],
                    "delisted_in_hold": delisted.to_numpy(),
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True)
    panel["q"] = panel.groupby("date")["form_ex"].transform(pre_q1_strong_quintiles)
    return panel


def pre_stats(monthly: pd.DataFrame) -> dict[str, float]:
    spread = monthly["spread"].to_numpy()
    n = len(spread)
    mean = spread.mean()
    sd = spread.std(ddof=1)
    x = spread - mean
    s = (x @ x) / n
    for k in range(1, PRE_NW_LAGS + 1):
        s += 2 * (1 - k / (PRE_NW_LAGS + 1)) * (x[k:] @ x[:-k]) / n
    return {
        "months": n,
        "mean": mean,
        "t": mean / (sd / np.sqrt(n)),
        "t_nw": mean / np.sqrt(s / n),
        "win": (spread > 0).mean(),
        "from": monthly.index.min().strftime("%Y-%m"),
        "to": monthly.index.max().strftime("%Y-%m"),
    }


def synthetic_market() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """改名前に数値を固定した合成パネル（seed=0）。"""
    idx = pd.bdate_range("2022-01-03", "2023-10-12")
    codes = [f"{i:04d}0" for i in range(1, 11)]
    rng = np.random.default_rng(0)
    drift = np.linspace(0.002, -0.002, len(codes))
    adj = pd.DataFrame(index=idx, columns=codes, dtype=float)
    for j, c in enumerate(codes):
        rets = 0.0005 + drift[j] + rng.normal(0, 0.005, len(idx))
        adj[c] = 1000 * np.exp(np.cumsum(rets))
    hole = pd.bdate_range("2022-03-28", "2022-03-30")
    adj.loc[hole, "00100"] = np.nan
    adj.loc["2023-07-01":, "01000"] = np.nan
    turnover = pd.DataFrame(2e8, index=idx, columns=codes)
    topix = pd.Series(2000 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, len(idx)))), index=idx)
    return adj, turnover, topix


def test_stale_limit_renamed() -> None:
    assert not hasattr(bt, "STALE_LIMIT")
    assert not hasattr(bt, "NW_LAGS")
    assert bt.FORM_START_FFILL_BDAYS == PRE_STALE_LIMIT


def test_workaround_comments_removed() -> None:
    src = BACKTEST_PATH.read_text(encoding="utf-8")
    assert "STALE_LIMIT" not in src
    assert "NW_LAGS" not in src
    gone = (
        "形成開始日の価格をこの営業日数まで前方埋めする",
        "保有期間が 3 か月重なるので Newey-West のラグは 2",
        "最終月は月末まで揃っていなければ判定日に使わない",
        "5 分位。Q1 = 形成期間が最も強い",
        "月次判定で 3 か月保有なので系列は重なり",
    )
    for fragment in gone:
        assert fragment not in src, fragment


def test_quintiles_match_pre_refactor() -> None:
    rng = np.random.default_rng(1)
    s = pd.Series(rng.normal(size=50))
    got = np.asarray(bt.q1_strong_quintiles(s))
    want = np.asarray(pre_q1_strong_quintiles(s))
    np.testing.assert_array_equal(got, want)
    s10 = pd.Series([0.10, 0.05, 0.00, -0.05, -0.10, 0.08, 0.02, -0.02, -0.08, 0.15])
    np.testing.assert_array_equal(
        np.asarray(bt.q1_strong_quintiles(s10)),
        [1, 2, 3, 4, 5, 2, 3, 4, 5, 1],
    )


def test_newey_west_t_match_pre_refactor() -> None:
    rng = np.random.default_rng(2)
    values = rng.normal(size=40)
    np.testing.assert_allclose(bt.newey_west_t(values), pre_newey_west_t(values), rtol=0, atol=0)
    spread = np.array([0.01, -0.02, 0.03, 0.00, 0.015, -0.005, 0.02, -0.01], dtype=float)
    np.testing.assert_allclose(bt.newey_west_t(spread), 1.568929081105472, rtol=0, atol=1e-15)


def test_month_end_decision_dates_match_pre_refactor() -> None:
    dates = pd.bdate_range("2022-01-03", "2023-10-12")
    got = bt.month_end_decision_dates(dates)
    want = pre_month_end_decision_dates(dates)
    assert got == want
    assert got[-1] == pd.Timestamp("2023-09-29")
    assert pd.Timestamp("2023-10-12") not in got


def test_build_panel_and_stats_match_pre_refactor() -> None:
    adj, turnover, topix = synthetic_market()
    got_panel = bt.build_panel(adj, turnover, topix, min_turnover=1e8)
    want_panel = pre_build_panel(adj, turnover, topix, min_turnover=1e8)
    pd.testing.assert_frame_equal(got_panel, want_panel, check_exact=True)

    split = pd.Timestamp("2023-01-01")
    got_monthly, got_lines = bt.summarize(got_panel, split)
    want_monthly, want_lines = bt.summarize(want_panel, split)
    pd.testing.assert_frame_equal(got_monthly, want_monthly, check_exact=True)
    assert got_lines == want_lines

    got_stats = bt.stats(got_monthly)
    want_stats = pre_stats(want_monthly)
    assert got_stats["months"] == want_stats["months"] == 15
    assert got_stats["from"] == want_stats["from"] == "2022-04"
    assert got_stats["to"] == want_stats["to"] == "2023-06"
    np.testing.assert_allclose(got_stats["mean"], want_stats["mean"], rtol=0, atol=0)
    np.testing.assert_allclose(got_stats["t"], want_stats["t"], rtol=0, atol=0)
    np.testing.assert_allclose(got_stats["t_nw"], want_stats["t_nw"], rtol=0, atol=0)
    np.testing.assert_allclose(got_stats["win"], want_stats["win"], rtol=0, atol=0)

    np.testing.assert_allclose(got_stats["mean"], 0.18397115788869584, rtol=0, atol=1e-15)
    np.testing.assert_allclose(got_stats["t"], 16.267884264670876, rtol=0, atol=1e-12)
    np.testing.assert_allclose(got_stats["t_nw"], 11.400266471788596, rtol=0, atol=1e-12)
    assert got_stats["win"] == 1.0


def test_form_12_1_panel_matches_pre_refactor() -> None:
    adj, turnover, topix = synthetic_market()
    got = bt.build_panel(adj, turnover, topix, min_turnover=1e8, form_months=12, skip_months=1)
    want = pre_build_panel(adj, turnover, topix, min_turnover=1e8, form_months=12, skip_months=1)
    pd.testing.assert_frame_equal(got, want, check_exact=True)

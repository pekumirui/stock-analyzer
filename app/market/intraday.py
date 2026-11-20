"""yfinance の 1 分足を data/intraday/1m/ に日ごとの Parquet で置き、読むときに分割で調整する（KAB-208）。

ファイルは `{root}/YYYY/YYYY-MM-DD.parquet`。1 ファイルに全銘柄、列は code / ts / open / high / low / close / volume。
値は調整前。Yahoo は分割があると過去の分足をさかのぼって直すので、一度書いた (銘柄, 日) は上書きしない。
取り直しで埋めるのは、その日より後に権利落ちが無いと DB で確かめられた銘柄だけ（`writable`）。
調整は `daily_prices.adj_factor` を `app/market/adjust.py` と同じ向きに掛ける（`adjust`）。
Yahoo の東証 1 分足は 15:30 のクロージング・オークションの足が無く、寄りの数分も無いことが多い。日足の始値・終値とは合わない。
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .adjust import RIGHTS_ISSUE

COLUMNS = ["code", "ts", "open", "high", "low", "close", "volume"]
PERIOD = "7d"            # 1 分足は 1 回 7 日まで（さかのぼれるのは約 30 日）
SPLIT_RATIO = 1.2        # 分足の高値と日足の高値がこの比を超えてずれたら、調整済みとみなして書かない


def yahoo_symbol(code: str) -> str:
    """J-Quants の 5 桁コード → Yahoo のシンボル（72030 → 7203.T、130A0 → 130A.T）。"""
    return f"{code[:4]}.T"


def day_path(root: Path, d: date) -> Path:
    return Path(root) / f"{d:%Y}" / f"{d.isoformat()}.parquet"


def fetch(code: str, ticker: Callable | None = None) -> pd.DataFrame:
    """1 銘柄の直近 7 日の 1 分足（調整前）。値が無い銘柄は空。レート制限などの例外はそのまま上げる。"""
    import yfinance as yf
    from yfinance.exceptions import YFPricesMissingError

    ticker = ticker or yf.Ticker
    try:
        df = ticker(yahoo_symbol(code)).history(
            period=PERIOD, interval="1m", auto_adjust=False, actions=False, raise_errors=True)
    except YFPricesMissingError:
        return pd.DataFrame(columns=COLUMNS)
    except KeyError as e:
        # 日足はあるのに 1 分足を配っていない銘柄（158A など）は応答に tradingPeriods が無い
        if e.args == ("tradingPeriods",):
            return pd.DataFrame(columns=COLUMNS)
        raise
    return normalize(code, df)


def normalize(code: str, df: pd.DataFrame) -> pd.DataFrame:
    """yfinance の history() の表を COLUMNS の形にする。"""
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df.reset_index().rename(columns=str.lower).rename(columns={"datetime": "ts"})
    out.insert(0, "code", code)
    out["ts"] = pd.to_datetime(out["ts"]).dt.tz_convert("Asia/Tokyo")
    out["volume"] = out["volume"].fillna(0).astype("int64")
    return out[COLUMNS]


def split_days(bars: pd.DataFrame) -> dict[date, pd.DataFrame]:
    if bars.empty:
        return {}
    return {d: g for d, g in bars.groupby(bars["ts"].dt.date)}


def read_day(root: Path, d: date, codes: Iterable[str] | None = None) -> pd.DataFrame:
    path = day_path(root, d)
    if not path.is_file():
        return pd.DataFrame(columns=COLUMNS)
    filters = [("code", "in", list(codes))] if codes is not None else None
    return pd.read_parquet(path, filters=filters)


def write_day(root: Path, d: date, new: pd.DataFrame) -> int:
    """その日のファイルに、まだ無い銘柄の足だけ足す。返り値は足した銘柄数。書き込みは一時ファイル → rename。"""
    path = day_path(root, d)
    old = read_day(root, d)
    add = new[~new["code"].isin(set(old["code"]))]
    if add.empty:
        return 0
    out = pd.concat([old, add], ignore_index=True) if not old.empty else add
    out = out.sort_values(["code", "ts"]).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    out.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(path)
    return int(add["code"].nunique())


def daily_context(db: Session, since: date) -> tuple[date | None, pd.DataFrame]:
    """DB の日足の最終日と、since 以降の日足（code / date / high / adj_factor）。"""
    asof = db.execute(text("SELECT MAX(date) FROM daily_prices")).scalar()
    rows = db.execute(
        text("SELECT code, date, high, adj_factor FROM daily_prices WHERE date >= :d"),
        {"d": since.isoformat()}).all()
    daily = pd.DataFrame(rows, columns=["code", "date", "high", "adj_factor"])
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    return (date.fromisoformat(str(asof)) if asof else None), daily


def writable(code: str, d: date, latest: date, asof: date | None, bars: pd.DataFrame,
             daily: pd.DataFrame) -> bool:
    """(code, d) の足が調整前のままだと言えるか。

    latest（今回どの銘柄かで取れた最新の日）は、まだ後ろに権利落ちが無いので常に調整前。それより前の日は、
    latest まで DB に日足があり（asof >= latest）、(d, latest] に権利落ちが無く、日足の高値と比が合うときだけ。
    """
    if d == latest:
        return True
    if asof is None or asof < latest:
        return False
    mine = daily[daily["code"] == code]
    after = mine[(mine["date"] > d) & (mine["date"] <= latest)]
    if (after["adj_factor"].fillna(1.0) != 1.0).any():
        return False
    day = mine[mine["date"] == d]
    if day.empty or not day["high"].iloc[0]:
        return False
    ratio = float(bars["high"].max()) / float(day["high"].iloc[0])
    return 1 / SPLIT_RATIO <= ratio <= SPLIT_RATIO


def collect(codes: list[str], fetcher: Callable[[str], pd.DataFrame], *, sleep: float = 0.5,
            rate_limit_waits: tuple[float, ...] = (60.0, 300.0),
            log: Callable[[str], None] = print) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """銘柄を 1 つずつ間隔を空けて取る。レート制限は待って取り直し、それでも駄目なら残りを諦める。"""
    from yfinance.exceptions import YFRateLimitError

    got: dict[str, pd.DataFrame] = {}
    stats = {"ok": 0, "empty": 0, "failed": 0, "rate_limited": 0}
    for i, code in enumerate(codes):
        for wait in (*rate_limit_waits, None):
            try:
                df = fetcher(code)
                break
            except YFRateLimitError:
                stats["rate_limited"] += 1
                if wait is None:
                    log(f"rate limit で打ち切り {i}/{len(codes)}（残りは次の回）")
                    stats["failed"] += len(codes) - i
                    return got, stats
                log(f"rate limit {code} {wait:.0f}s 待つ")
                time.sleep(wait)
            except Exception as e:
                log(f"fail {code}: {type(e).__name__}: {str(e)[:120]}")
                stats["failed"] += 1
                df = None
                break
        if df is not None:
            if df.empty:
                stats["empty"] += 1
            else:
                got[code] = df
                stats["ok"] += 1
        if (i + 1) % 500 == 0:
            log(f"[{i + 1}/{len(codes)}] ok={stats['ok']} empty={stats['empty']} failed={stats['failed']}")
        if sleep:
            time.sleep(sleep)
    return got, stats


def store(root: Path, got: dict[str, pd.DataFrame], asof: date | None,
          daily: pd.DataFrame) -> dict[str, int]:
    """取れた足を日ごとのファイルへ。書けない (銘柄, 日) は数えるだけ。"""
    per_day: dict[date, list[pd.DataFrame]] = {}
    skipped = 0
    by_code = {code: split_days(bars) for code, bars in got.items()}
    if not by_code:
        return {"days": 0, "added": 0, "skipped": 0}
    latest = max(max(days) for days in by_code.values())
    for code, days in by_code.items():
        for d, g in days.items():
            if writable(code, d, latest, asof, g, daily):
                per_day.setdefault(d, []).append(g)
            else:
                skipped += 1
    added = 0
    for d in sorted(per_day):
        added += write_day(root, d, pd.concat(per_day[d], ignore_index=True))
    return {"days": len(per_day), "added": added, "skipped": skipped}


def adjust(db: Session, bars: pd.DataFrame) -> pd.DataFrame:
    """調整前の分足に、その日より後の adj_factor を掛ける。出来高は割る（ライツイシューの係数は使わない）。"""
    if bars.empty:
        return bars
    rows = db.execute(
        text("SELECT code, date, adj_factor, ex_rt FROM daily_prices "
             "WHERE code IN :codes AND adj_factor IS NOT NULL AND adj_factor != 1")
        .bindparams(bindparam("codes", expanding=True)),
        {"codes": sorted(set(bars["code"]))}).all()
    out = bars.copy()
    day = out["ts"].dt.date
    price = pd.Series(1.0, index=out.index)
    vol = pd.Series(1.0, index=out.index)
    for code, d, factor, ex_rt in rows:
        d = date.fromisoformat(str(d))
        hit = (out["code"] == code) & (day < d)
        price[hit] *= factor
        if ex_rt != RIGHTS_ISSUE:
            vol[hit] *= factor
    for c in ("open", "high", "low", "close"):
        out[c] = out[c] * price
    out["volume"] = out["volume"] / vol
    return out


def load(root: Path, code: str, start: date, end: date, db: Session | None = None) -> pd.DataFrame:
    """1 銘柄の start〜end の 1 分足。db を渡すと分割で調整した値、渡さなければ調整前。"""
    days = sorted(p for p in Path(root).glob("*/*.parquet")
                  if start.isoformat() <= p.stem <= end.isoformat())
    parts = [pd.read_parquet(p, filters=[("code", "==", code)]) for p in days]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    bars = pd.concat(parts, ignore_index=True).sort_values("ts").reset_index(drop=True)
    return adjust(db, bars) if db is not None else bars

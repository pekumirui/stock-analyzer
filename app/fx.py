"""Frankfurter ECB 日次レート（EUR ベース）とクロス換算。

保存は quote 通貨の対 EUR だけ。任意ペアは 1 EUR あたりのレートの比で出す。
休日・ECB 非公表日は指定日以前の直近営業日（as_of）を使う。
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import FxRate

FX_EARLIEST = date(1999, 1, 4)
HOLIDAY_NOTE = (
    "休日・ECB非公表日は指定日以前の直近営業日（as_of）のレートを使う。"
    "requested_date と as_of が違うときは holiday_fallback=true。"
)
CCY_RE = re.compile(r"^[A-Za-z]{3}$")
PAIR_RE = re.compile(r"^([A-Za-z]{3})[/_\-]([A-Za-z]{3})$")
RETRY_BACKOFF_SEC = (5, 10, 20, 40, 60, 60)


class FxLookupError(ValueError):
    """通貨・日付・欠損など、呼び出し側が直せる参照エラー。"""


def parse_ccy(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not CCY_RE.fullmatch(s):
        raise FxLookupError("currency must be a 3-letter ISO code (e.g. USD, JPY, EUR)")
    return s


def parse_pair(
    pair: Any = None, base: Any = None, quote: Any = None,
) -> tuple[str, str]:
    """USD/JPY 形式、または base と quote。返すのは大文字。"""
    if pair is not None and str(pair).strip():
        text = str(pair).strip().upper()
        m = PAIR_RE.fullmatch(text)
        if m is None:
            raise FxLookupError("pair must look like USD/JPY")
        return m.group(1), m.group(2)
    if base is None or quote is None or not str(base).strip() or not str(quote).strip():
        raise FxLookupError("need pair (USD/JPY) or both base and quote")
    return parse_ccy(base), parse_ccy(quote)


def needed_quotes(base: str, quote: str) -> tuple[str, ...]:
    """対EUR保存からクロスに必要な quote。EUR と同一通貨は不要。"""
    out: list[str] = []
    for ccy in (base, quote):
        if ccy != "EUR" and ccy not in out:
            out.append(ccy)
    return tuple(out)


def cross_rate(eur_rates: Mapping[str, float], base: str, quote: str) -> float:
    """1 base = 戻り値 quote。eur_rates は 1 EUR あたりの各通貨。EUR は省略可。"""
    base_ccy = parse_ccy(base)
    quote_ccy = parse_ccy(quote)
    if base_ccy == quote_ccy:
        return 1.0

    def per_eur(ccy: str) -> float:
        if ccy == "EUR":
            return 1.0
        if ccy not in eur_rates:
            raise FxLookupError(f"no EUR/{ccy} rate on this date")
        rate = float(eur_rates[ccy])
        if rate == 0:
            raise FxLookupError(f"EUR/{ccy} rate is zero")
        return rate

    return per_eur(quote_ccy) / per_eur(base_ccy)


def iter_date_chunks(start: date, end: date, chunk_days: int) -> Iterator[tuple[date, date]]:
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    if end < start:
        return
    cur = start
    delta = timedelta(days=chunk_days - 1)
    one = timedelta(days=1)
    while cur <= end:
        chunk_end = cur + delta
        if chunk_end > end:
            chunk_end = end
        yield cur, chunk_end
        cur = chunk_end + one


def parse_frankfurter_payload(payload: Mapping[str, Any]) -> dict[date, dict[str, float]]:
    """単日・時系列のどちらも {date: {quote: rate}}。基準は EUR のみ。"""
    base = str(payload.get("base") or "").upper()
    if base and base != "EUR":
        raise RuntimeError(f"Frankfurter base must be EUR, got {base}")
    raw_rates = payload.get("rates")
    if not isinstance(raw_rates, dict) or not raw_rates:
        return {}
    sample_key = next(iter(raw_rates))
    sample_val = raw_rates[sample_key]
    out: dict[date, dict[str, float]] = {}
    if isinstance(sample_val, dict):
        for day_s, quotes in raw_rates.items():
            day = date.fromisoformat(str(day_s))
            out[day] = _quotes_map(quotes)
    else:
        day_s = payload.get("date")
        if not day_s:
            raise RuntimeError("Frankfurter single-day payload missing date")
        out[date.fromisoformat(str(day_s))] = _quotes_map(raw_rates)
    return {day: quotes for day, quotes in out.items() if quotes}


def _quotes_map(quotes: Any) -> dict[str, float]:
    if not isinstance(quotes, dict):
        return {}
    out: dict[str, float] = {}
    for ccy, val in quotes.items():
        code = str(ccy).strip().upper()
        if not CCY_RE.fullmatch(code) or code == "EUR":
            continue
        if val is None:
            continue
        out[code] = float(val)
    return out


def rows_from_series(series: Mapping[date, Mapping[str, float]]) -> list[dict]:
    rows: list[dict] = []
    for day in sorted(series):
        for quote, rate in series[day].items():
            rows.append({"date": day, "quote": quote, "rate": float(rate)})
    return rows


class FrankfurterClient:
    """api.frankfurter.app の時系列。キー不要。429 は指数バックオフ。"""

    def __init__(self, base_url: str | None = None, timeout: float = 60.0):
        if base_url is None:
            from . import config as _config  # noqa: PLC0415
            base_url = _config.FRANKFURTER_BASE
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"User-Agent": "stock-analyzer-fx/1.0"},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> FrankfurterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def timeseries(self, start: date, end: date) -> dict[date, dict[str, float]]:
        if start == end:
            path = f"/{start.isoformat()}"
        else:
            path = f"/{start.isoformat()}..{end.isoformat()}"
        payload = self._get_json(path)
        return parse_frankfurter_payload(payload)

    def _get_json(self, path: str) -> dict[str, Any]:
        data: dict[str, Any] | None = None
        for attempt, wait in enumerate(RETRY_BACKOFF_SEC):
            r = self._client.get(path)
            if r.status_code == 429:
                print(f"  429 Too Many Requests → {wait}秒待機して再試行 ({path})")
                time.sleep(wait)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                raise RuntimeError(f"Frankfurter response is not an object: {path}")
            data = body
            break
        if data is None:
            raise RuntimeError(f"リトライ上限に達しました: {path}")
        return data


def last_fx_date(db: Session) -> date | None:
    return db.execute(select(func.max(FxRate.date))).scalar_one()


def ingest_range(
    db: Session,
    start: date,
    end: date,
    *,
    client: FrankfurterClient | None = None,
    chunk_days: int | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """期間を chunk して UPSERT。冪等。EUR 自身は書かない。"""
    from .market.ingest import upsert

    if end < start:
        return {"days": 0, "rows": 0, "chunks": 0}
    own = client is None
    http = client or FrankfurterClient()
    days = 0
    rows_n = 0
    chunks = 0
    try:
        if chunk_days is None:
            from . import config as _config  # noqa: PLC0415
            chunk_days = _config.FX_CHUNK_DAYS
        for chunk_start, chunk_end in iter_date_chunks(start, end, chunk_days):
            chunks += 1
            series = http.timeseries(chunk_start, chunk_end)
            rows = rows_from_series(series)
            upsert(db, FxRate, rows, commit=commit)
            days += len(series)
            rows_n += len(rows)
            print(
                f"  fx {chunk_start.isoformat()}..{chunk_end.isoformat()} "
                f"days={len(series)} rows={len(rows)}"
            )
    finally:
        if own:
            http.close()
    return {"days": days, "rows": rows_n, "chunks": chunks}


def resolve_start(
    db: Session,
    *,
    backfill: bool = False,
    start: date | None = None,
    today: date | None = None,
) -> date:
    """バックフィルは 1999-01-04。差分は最終保存日（再 UPSERT のためその日を含む）。"""
    today = today or date.today()
    floor = FX_EARLIEST
    if start is not None:
        return max(start, floor)
    if backfill:
        return floor
    last = last_fx_date(db)
    if last is None:
        return floor
    return min(last, today)


def eur_rates_on(db: Session, as_of: date, quotes: Iterable[str] | None = None) -> dict[str, float]:
    q = db.query(FxRate).filter(FxRate.date == as_of)
    wanted = tuple(quotes) if quotes is not None else ()
    if wanted:
        q = q.filter(FxRate.quote.in_(wanted))
    return {row.quote: float(row.rate) for row in q.all()}


def as_of_date(db: Session, requested: date, quotes: tuple[str, ...]) -> date | None:
    """requested 以前で、必要な quote が揃う最終日。quotes が空なら最終日。"""
    if not quotes:
        return db.execute(
            select(func.max(FxRate.date)).where(FxRate.date <= requested)
        ).scalar_one()
    stmt = (
        select(FxRate.date)
        .where(FxRate.date <= requested, FxRate.quote.in_(quotes))
        .group_by(FxRate.date)
        .having(func.count(func.distinct(FxRate.quote)) >= len(quotes))
        .order_by(FxRate.date.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def pair_rate(db: Session, requested: date, base: str, quote: str) -> dict[str, Any]:
    base_ccy, quote_ccy = parse_ccy(base), parse_ccy(quote)
    need = needed_quotes(base_ccy, quote_ccy)
    as_of = as_of_date(db, requested, need)
    if as_of is None:
        raise FxLookupError(
            f"no FX rate on or before {requested.isoformat()} for {base_ccy}/{quote_ccy}"
        )
    rates = {"EUR": 1.0}
    if need:
        rates.update(eur_rates_on(db, as_of, need))
    return {
        "pair": f"{base_ccy}/{quote_ccy}",
        "base": base_ccy,
        "quote": quote_ccy,
        "requested_date": requested.isoformat(),
        "as_of": as_of.isoformat(),
        "holiday_fallback": as_of != requested,
        "rate": cross_rate(rates, base_ccy, quote_ccy),
        "holiday_policy": HOLIDAY_NOTE,
    }


def pair_series(
    db: Session,
    start: date,
    end: date,
    base: str,
    quote: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """期間内の公表日。非営業日は含めない。各点はその日のクロス。"""
    if end < start:
        raise FxLookupError("to must be on or after from")
    base_ccy, quote_ccy = parse_ccy(base), parse_ccy(quote)
    need = needed_quotes(base_ccy, quote_ccy)
    observations: list[dict[str, Any]] = []
    truncated = False
    if not need:
        days = [
            row[0]
            for row in db.query(FxRate.date)
            .filter(FxRate.date >= start, FxRate.date <= end)
            .distinct()
            .order_by(FxRate.date)
            .limit(limit + 1)
            .all()
        ]
        truncated = len(days) > limit
        for day in days[:limit]:
            observations.append({
                "date": day.isoformat(),
                "as_of": day.isoformat(),
                "holiday_fallback": False,
                "rate": 1.0,
            })
    else:
        q = (
            db.query(FxRate)
            .filter(FxRate.date >= start, FxRate.date <= end, FxRate.quote.in_(need))
            .order_by(FxRate.date)
        )
        by_day: dict[date, dict[str, float]] = {}
        for row in q.all():
            by_day.setdefault(row.date, {})[row.quote] = float(row.rate)
        for day in sorted(by_day):
            rates = by_day[day]
            if any(c not in rates for c in need):
                continue
            rates = dict(rates)
            rates["EUR"] = 1.0
            observations.append({
                "date": day.isoformat(),
                "as_of": day.isoformat(),
                "holiday_fallback": False,
                "rate": cross_rate(rates, base_ccy, quote_ccy),
            })
            if len(observations) >= limit:
                truncated = True
                break
    return {
        "pair": f"{base_ccy}/{quote_ccy}",
        "base": base_ccy,
        "quote": quote_ccy,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "count": len(observations),
        "truncated": truncated,
        "holiday_policy": (
            HOLIDAY_NOTE
            + " 期間指定では公表日だけを返す。休日を直近営業日で埋めたいときは fx_rate にその日を渡す。"
        ),
        "observations": observations,
    }

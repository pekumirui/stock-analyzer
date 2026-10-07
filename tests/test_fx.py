"""EUR ベースのクロス換算・Frankfurter 取込・休日フォールバック。HTTP はフェイク。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-fx-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.fx import (  # noqa: E402
    FX_EARLIEST,
    FrankfurterClient,
    FxLookupError,
    as_of_date,
    cross_rate,
    ingest_range,
    iter_date_chunks,
    needed_quotes,
    pair_rate,
    pair_series,
    parse_ccy,
    parse_frankfurter_payload,
    parse_pair,
    resolve_start,
    rows_from_series,
)
from app.ingest import upsert  # noqa: E402
from app.models import FxRate  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


class _Resp:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHTTP:
    def __init__(self, replies: list[_Resp]) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []
        self.closed = False

    def get(self, path: str, params: dict | None = None) -> _Resp:  # noqa: ARG002
        self.calls.append(path)
        if not self.replies:
            _fail(f"unexpected GET {path}")
        return self.replies.pop(0)

    def close(self) -> None:
        self.closed = True


class _FakeSeries:
    def __init__(self, series: dict[date, dict[str, float]]) -> None:
        self.series = series
        self.calls: list[tuple[date, date]] = []

    def timeseries(self, start: date, end: date) -> dict[date, dict[str, float]]:
        self.calls.append((start, end))
        return {
            day: dict(quotes)
            for day, quotes in self.series.items()
            if start <= day <= end
        }

    def close(self) -> None:
        return None


def test_cross_rate_pairs() -> None:
    eur = {"USD": 1.10, "JPY": 165.0}
    usd_jpy = cross_rate(eur, "USD", "JPY")
    if abs(usd_jpy - (165.0 / 1.10)) > 1e-12:
        _fail(f"USD/JPY {usd_jpy}")
    if cross_rate(eur, "EUR", "USD") != 1.10:
        _fail("EUR/USD")
    if abs(cross_rate(eur, "USD", "EUR") - (1 / 1.10)) > 1e-12:
        _fail("USD/EUR")
    if cross_rate(eur, "jpy", "JPY") != 1.0:
        _fail("JPY/JPY")
    try:
        cross_rate(eur, "USD", "GBP")
    except FxLookupError as exc:
        if "GBP" not in str(exc):
            _fail(exc)
    else:
        _fail("missing quote must raise")


def test_parse_pair_and_needed() -> None:
    if parse_pair("usd/jpy") != ("USD", "JPY"):
        _fail("slash pair")
    if parse_pair("USD-JPY") != ("USD", "JPY"):
        _fail("dash pair")
    if parse_pair(base="eur", quote="usd") != ("EUR", "USD"):
        _fail("base+quote")
    if needed_quotes("USD", "JPY") != ("USD", "JPY"):
        _fail(needed_quotes("USD", "JPY"))
    if needed_quotes("EUR", "USD") != ("USD",):
        _fail(needed_quotes("EUR", "USD"))
    if needed_quotes("EUR", "EUR") != ():
        _fail("EUR/EUR needs no quotes")
    try:
        parse_pair("USDJPY")
    except FxLookupError:
        pass
    else:
        _fail("USDJPY without separator must raise")
    try:
        parse_ccy("USDT")
    except FxLookupError:
        pass
    else:
        _fail("4-letter ccy must raise")


def test_iter_date_chunks() -> None:
    chunks = list(iter_date_chunks(date(1999, 1, 4), date(2001, 1, 10), 366))
    if chunks[0] != (date(1999, 1, 4), date(2000, 1, 4)):
        _fail(chunks[0])
    if chunks[-1][1] != date(2001, 1, 10):
        _fail(chunks[-1])
    if any(a > b for a, b in chunks):
        _fail(chunks)
    cur = date(1999, 1, 4)
    for start, end in chunks:
        if start != cur:
            _fail(f"gap {cur} vs {start}")
        cur = date.fromordinal(end.toordinal() + 1)
    if list(iter_date_chunks(date(2024, 1, 2), date(2024, 1, 1), 10)):
        _fail("empty inverted range")


def test_parse_frankfurter_payload() -> None:
    single = {
        "amount": 1.0,
        "base": "EUR",
        "date": "1999-01-04",
        "rates": {"USD": 1.1789, "JPY": 133.73, "EUR": 1.0},
    }
    got = parse_frankfurter_payload(single)
    if set(got) != {date(1999, 1, 4)}:
        _fail(got)
    if "EUR" in got[date(1999, 1, 4)]:
        _fail("must not store EUR")
    if got[date(1999, 1, 4)]["JPY"] != 133.73:
        _fail(got)
    series = {
        "base": "EUR",
        "start_date": "1999-01-04",
        "end_date": "1999-01-05",
        "rates": {
            "1999-01-04": {"USD": 1.1789, "JPY": 133.73},
            "1999-01-05": {"USD": 1.179, "JPY": 130.96},
        },
    }
    days = parse_frankfurter_payload(series)
    if list(days) != [date(1999, 1, 4), date(1999, 1, 5)]:
        _fail(days)
    rows = rows_from_series(days)
    if len(rows) != 4:
        _fail(rows)
    try:
        parse_frankfurter_payload({"base": "USD", "date": "2024-01-04", "rates": {"JPY": 150}})
    except RuntimeError as exc:
        if "EUR" not in str(exc):
            _fail(exc)
    else:
        _fail("non-EUR base must raise")


def test_client_timeseries_and_429() -> None:
    client = FrankfurterClient(base_url="https://api.frankfurter.dev/v1")
    fake = _FakeHTTP([
        _Resp(429),
        _Resp(200, {
            "base": "EUR",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "rates": {
                "2024-01-04": {"USD": 1.1, "JPY": 160.0},
                "2024-01-05": {"USD": 1.09, "JPY": 159.0},
            },
        }),
    ])
    client._client = fake  # type: ignore[assignment]
    slept: list[float] = []
    with patch("app.fx.time.sleep", slept.append):
        series = client.timeseries(date(2024, 1, 4), date(2024, 1, 5))
    if slept != [5]:
        _fail(slept)
    if fake.calls != ["/2024-01-04..2024-01-05", "/2024-01-04..2024-01-05"]:
        _fail(fake.calls)
    if series[date(2024, 1, 4)]["USD"] != 1.1:
        _fail(series)
    single = _FakeHTTP([_Resp(200, {
        "base": "EUR", "date": "2024-01-04", "rates": {"USD": 1.1},
    })])
    client._client = single  # type: ignore[assignment]
    one = client.timeseries(date(2024, 1, 4), date(2024, 1, 4))
    if single.calls != ["/2024-01-04"]:
        _fail(single.calls)
    if date(2024, 1, 4) not in one:
        _fail(one)
    missing = _FakeHTTP([_Resp(404)])
    client._client = missing  # type: ignore[assignment]
    if client.timeseries(date(1999, 1, 1), date(1999, 1, 1)) != {}:
        _fail("404 should be empty")
    client.close()
    if not missing.closed:
        _fail("close")


def test_ingest_upsert_and_holiday_fallback() -> None:
    init_db()
    db = SessionLocal()
    try:
        fake = _FakeSeries({
            date(2024, 1, 4): {"USD": 1.10, "JPY": 165.0},
            date(2024, 1, 5): {"USD": 1.09, "JPY": 159.14},
        })
        stats = ingest_range(
            db, date(2024, 1, 4), date(2024, 1, 7), client=fake, chunk_days=10,
        )
        if stats["days"] != 2 or stats["rows"] != 4 or stats["chunks"] != 1:
            _fail(stats)
        if fake.calls != [(date(2024, 1, 4), date(2024, 1, 7))]:
            _fail(fake.calls)
        n = db.query(FxRate).count()
        if n != 4:
            _fail(n)
        ingest_range(db, date(2024, 1, 4), date(2024, 1, 7), client=fake)
        if db.query(FxRate).count() != 4:
            _fail("re-ingest must stay idempotent")
        friday = pair_rate(db, date(2024, 1, 5), "USD", "JPY")
        expect = 159.14 / 1.09
        if abs(friday["rate"] - expect) > 1e-12:
            _fail(friday)
        if friday["holiday_fallback"]:
            _fail("Friday is an ECB day")
        sat = pair_rate(db, date(2024, 1, 6), "USD", "JPY")
        if sat["as_of"] != "2024-01-05" or not sat["holiday_fallback"]:
            _fail(sat)
        if abs(sat["rate"] - expect) > 1e-12:
            _fail(sat)
        if as_of_date(db, date(2024, 1, 6), ("USD", "JPY")) != date(2024, 1, 5):
            _fail("as_of weekend")
        eur_usd = pair_rate(db, date(2024, 1, 6), "EUR", "USD")
        if eur_usd["rate"] != 1.09:
            _fail(eur_usd)
        series = pair_series(db, date(2024, 1, 4), date(2024, 1, 7), "USD", "JPY")
        if series["count"] != 2:
            _fail(series)
        if [o["date"] for o in series["observations"]] != ["2024-01-04", "2024-01-05"]:
            _fail(series["observations"])
        if any(o["holiday_fallback"] for o in series["observations"]):
            _fail("series is published days only")
        try:
            pair_rate(db, date(2023, 12, 31), "USD", "JPY")
        except FxLookupError:
            pass
        else:
            _fail("before first observation must raise")
    finally:
        db.close()


def test_resolve_start_and_chunks_ingest() -> None:
    init_db()
    db = SessionLocal()
    try:
        db.query(FxRate).delete()
        db.commit()
        today = date(2024, 1, 10)
        if resolve_start(db, backfill=True, today=today) != FX_EARLIEST:
            _fail("backfill start")
        if resolve_start(db, today=today) != FX_EARLIEST:
            _fail("empty table starts at earliest")
        upsert(db, FxRate, [
            {"date": date(2024, 1, 5), "quote": "USD", "rate": 1.1},
        ])
        if resolve_start(db, today=today) != date(2024, 1, 5):
            _fail("incremental includes last saved day")
        if resolve_start(db, backfill=True, today=today) != FX_EARLIEST:
            _fail("backfill ignores last date")
        fake = _FakeSeries({
            date(2023, 12, 29): {"USD": 1.1},
            date(2024, 1, 4): {"USD": 1.2},
        })
        ingest_range(
            db, date(2023, 12, 20), date(2024, 1, 4),
            client=fake, chunk_days=10,
        )
        if len(fake.calls) != 2:
            _fail(fake.calls)
        if fake.calls[0][0] != date(2023, 12, 20):
            _fail(fake.calls[0])
        if fake.calls[0][1] < fake.calls[0][0]:
            _fail(fake.calls[0])
        if fake.calls[-1][1] != date(2024, 1, 4):
            _fail(fake.calls[-1])
    finally:
        db.close()


def main() -> None:
    test_cross_rate_pairs()
    test_parse_pair_and_needed()
    test_iter_date_chunks()
    test_parse_frankfurter_payload()
    test_client_timeseries_and_429()
    test_ingest_upsert_and_holiday_fallback()
    test_resolve_start_and_chunks_ingest()
    print("ok: fx")


if __name__ == "__main__":
    main()

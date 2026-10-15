"""J-Quants V2 クライアントと日次取込。HTTP はフェイク。sleep は注入する。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-jquants-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"
os.environ.setdefault("JQUANTS_API_KEY", "test-key")

from app.config import load_dotenv  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.market.ingest import (  # noqa: E402
    ingest_master,
    ingest_prices_for_code,
    ingest_prices_for_date,
    ingest_statements_for_code,
    ingest_topix,
    run_daily,
)
from app.market.jquants import JQuantsClient, RateLimiter  # noqa: E402
from app.models import DailyPrice, Statement, Stock, Topix  # noqa: E402


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
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def get(self, path: str, params: dict | None = None) -> _Resp:
        self.calls.append((path, dict(params or {})))
        if not self.replies:
            _fail(f"unexpected GET {path} {params}")
        return self.replies.pop(0)

    def close(self) -> None:
        self.closed = True


def test_rate_limiter_sleeps_when_full() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    def mono() -> float:
        return clock["t"]

    def sleep(sec: float) -> None:
        slept.append(sec)
        clock["t"] += sec

    lim = RateLimiter(2)
    with patch("app.market.jquants.time.monotonic", mono), patch("app.market.jquants.time.sleep", sleep):
        lim.acquire()
        lim.acquire()
        if slept:
            _fail(f"first two must not sleep: {slept}")
        lim.acquire()
        if not slept or slept[0] < 60:
            _fail(f"third acquire must wait a minute: {slept}")


def test_get_all_pages_and_429() -> None:
    jq = JQuantsClient(api_key="x")
    if jq._client.headers.get("x-api-key") != "x":
        _fail("V2 auth is the x-api-key header")
    fake = _FakeHTTP([
        _Resp(429),
        _Resp(200, {"data": [{"Code": "72030"}], "pagination_key": "p2"}),
        _Resp(200, {"data": [{"Code": "99840"}]}),
    ])
    jq._client = fake  # type: ignore[assignment]
    slept: list[float] = []
    with patch("app.market.jquants.time.sleep", slept.append), patch.object(jq._limiters["default"], "acquire", lambda: None):
        rows = jq.get_all("/equities/master", {"date": "2026-09-01"})
    if rows != [{"Code": "72030"}, {"Code": "99840"}]:
        _fail(f"pages {rows}")
    if slept != [5]:
        _fail(f"first 429 waits 5s: {slept}")
    if [c[1] for c in fake.calls] != [
        {"date": "2026-09-01"},
        {"date": "2026-09-01"},
        {"date": "2026-09-01", "pagination_key": "p2"},
    ]:
        _fail(f"pagination {fake.calls}")
    jq.close()
    if not fake.closed:
        _fail("close")


def test_429_backoff_gives_up_after_six() -> None:
    jq = JQuantsClient(api_key="x")
    fake = _FakeHTTP([_Resp(429)] * 6)
    jq._client = fake  # type: ignore[assignment]
    slept: list[float] = []
    with patch("app.market.jquants.time.sleep", slept.append), patch.object(jq._limiters["fins"], "acquire", lambda: None):
        try:
            jq.get_all("/fins/summary", {"date": "2026-09-01"})
        except RuntimeError as exc:
            if "リトライ上限" not in str(exc):
                _fail(exc)
        else:
            _fail("six 429s must raise")
    if slept != [5, 10, 20, 40, 60, 60] or len(fake.calls) != 6:
        _fail(f"backoff {slept} calls={len(fake.calls)}")


def test_fins_uses_its_own_bucket() -> None:
    jq = JQuantsClient(api_key="x")
    jq._client = _FakeHTTP([_Resp(200, {"data": []}), _Resp(200, {"data": []})])  # type: ignore[assignment]
    used: list[str] = []
    with patch.object(jq._limiters["default"], "acquire", lambda: used.append("default")), \
            patch.object(jq._limiters["fins"], "acquire", lambda: used.append("fins")):
        jq.get_all("/fins/summary")
        jq.get_all("/equities/bars/daily")
    if used != ["fins", "default"]:
        _fail(f"rate limit buckets {used}")


def test_endpoints_and_params() -> None:
    fake = _FakeHTTP([_Resp(200, {"data": [{"Date": "2026-09-01", "HolDiv": "1"}]})] * 6)
    with JQuantsClient(api_key="x") as jq:
        jq._client = fake  # type: ignore[assignment]
        for lim in jq._limiters.values():
            lim.acquire = lambda: None  # type: ignore[method-assign]
        jq.equities_master("2026-09-01")
        jq.bars_daily(code="72030", from_="2026-08-01", to="2026-09-01")
        jq.topix(from_="2026-01-01", to="2026-01-31")
        jq.fins_summary(date="2026-09-01")
        if jq.calendar("2026-09-01", "2026-09-01") != [{"Date": "2026-09-01", "HolDiv": "1"}]:
            _fail("calendar rows")
        if jq.fins_earnings_date("2026-10-02") != [{"Date": "2026-09-01", "HolDiv": "1"}]:
            _fail("earnings-date rows")
    if not fake.closed:
        _fail("context exit must close the HTTP client")
    if fake.calls != [
        ("/equities/master", {"date": "2026-09-01"}),
        ("/equities/bars/daily", {"code": "72030", "from": "2026-08-01", "to": "2026-09-01"}),
        ("/indices/bars/daily/topix", {"from": "2026-01-01", "to": "2026-01-31"}),
        ("/fins/summary", {"date": "2026-09-01"}),
        ("/markets/calendar", {"from": "2026-09-01", "to": "2026-09-01"}),
        ("/fins/earnings-date", {"date": "2026-10-02"}),
    ]:
        _fail(fake.calls)


def test_ingest_writes_price_and_statement_rows() -> None:
    init_db()
    db = SessionLocal()

    class JQ:
        def equities_master(self, date: str | None = None) -> list[dict]:
            return [{"Code": "72030", "CoName": "トヨタ", "S17Nm": "輸送", "S33Nm": "輸送用機器",
                     "MktNm": "プライム", "ScaleCat": "TOPIX Large70", "MrgnNm": "貸借", "ProdCat": "011"}]

        def bars_daily(self, date: str | None = None, code: str | None = None,
                       from_: str | None = None, to: str | None = None) -> list[dict]:
            halt = {"Code": "99990", "Date": date or "2026-09-01", "C": None, "AdjC": None}
            live = {
                "Code": code or "72030", "Date": (date or from_ or "2026-09-01").replace("-", ""),
                "O": "－", "H": "110", "L": "90", "C": "105",
                "Vo": "10", "Va": "1000", "AdjFactor": "1",
                "AdjO": "100", "AdjH": "110", "AdjL": "90", "AdjC": "210", "AdjVo": "10",
                "MktCap": "50",
            }
            return [live, halt]

        def topix(self, from_: str | None = None, to: str | None = None) -> list[dict]:
            return [{"Date": from_ or "2026-09-01", "O": "2800", "H": "2810", "L": "2790", "C": "2805"}]

        def fins_summary(self, date: str | None = None, code: str | None = None) -> list[dict]:
            return [{
                "DiscNo": "D1", "Code": "72030", "DiscDate": "2026-09-01",
                "DocType": "FYFinancialStatements_Consolidated_JP",
                "CurPerType": "1Q", "Sales": "1000", "OP": "100", "OdP": "90", "NP": "80", "EPS": "10",
                "CurFYEn": "2026-03-31",
            }, {
                "DiscNo": 2, "Code": "72030", "DiscDate": "2026-09-01",
                "DocType": "EarnForecastRevision", "CurPerType": "FY", "Sales": "",
            }]

    try:
        n = ingest_master(db, JQ())  # type: ignore[arg-type]
        if n != 1:
            _fail(f"master {n}")
        st = db.get(Stock, "72030")
        if st is None or st.name != "トヨタ" or st.product_category != "011":
            _fail(st)
        n = ingest_prices_for_date(db, JQ(), "2026-09-01")  # type: ignore[arg-type]
        if n != 1:
            _fail(f"prices {n}")
        px = db.get(DailyPrice, ("72030", date(2026, 9, 1)))
        if px is None or px.adj_close != 210.0 or px.close != 105.0:
            _fail(f"price row {px}")
        if px.open is not None or px.adj_factor != 1.0:
            _fail(f"missing-value mark must be NULL: open={px.open} adj_factor={px.adj_factor}")
        if db.get(DailyPrice, ("99990", date(2026, 9, 1))) is not None:
            _fail("halt day inserted")
        n = ingest_prices_for_code(db, JQ(), "72030", from_="2026-09-01", to="2026-09-01")  # type: ignore[arg-type]
        if n != 1:
            _fail(f"code prices {n}")
        n = ingest_topix(db, JQ(), from_="2026-09-01", to="2026-09-01")  # type: ignore[arg-type]
        if n != 1:
            _fail(f"topix {n}")
        tx = db.get(Topix, date(2026, 9, 1))
        if tx is None or tx.close != 2805.0:
            _fail(tx)
        n = ingest_statements_for_code(db, JQ(), "72030")  # type: ignore[arg-type]
        if n != 2:
            _fail(f"stmt {n}")
        stmt = db.get(Statement, "D1")
        if stmt is None or stmt.net_sales != 1000.0 or stmt.type_of_period != "1Q":
            _fail(stmt)
        if stmt.is_revision or stmt.fiscal_year_end != date(2026, 3, 31):
            _fail(f"D1 is_revision={stmt.is_revision} fy_end={stmt.fiscal_year_end}")
        if json.loads(stmt.raw_json)["OdP"] != "90":
            _fail(f"raw_json must keep the source fields: {stmt.raw_json}")
        rev = db.get(Statement, "2")
        if rev is None or rev.is_revision is not True or rev.net_sales is not None:
            _fail(f"forecast revision row {rev}")
    finally:
        db.close()


def test_run_daily_uses_injected_client() -> None:
    init_db()
    db = SessionLocal()
    calls: list[str] = []

    class JQ:
        def __enter__(self) -> "JQ":
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def equities_master(self, date: str | None = None) -> list[dict]:
            calls.append("master")
            return []

        def bars_daily(self, date: str | None = None, **kwargs) -> list[dict]:
            calls.append(f"bars:{date}")
            return []

        def topix(self, from_: str | None = None, to: str | None = None) -> list[dict]:
            calls.append(f"topix:{from_}")
            return []

        def fins_summary(self, date: str | None = None, **kwargs) -> list[dict]:
            calls.append(f"fins:{date}")
            if date != "2026-09-01":
                return []
            # 前回の取込後 (19:30 より後) に出た開示
            return [{"DiscNo": "L1", "Code": "72030", "DiscDate": "2026-09-01",
                     "DiscTime": "20:00", "DocType": "EarnForecastRevision", "CurPerType": "FY"}]

    try:
        with patch("app.market.ingest.JQuantsClient", JQ):
            # 2026-09-01 より前の株価は無いので前営業日の取り直しは無い
            db.query(Statement).filter(Statement.disclosure_no == "L1").delete()
            db.commit()
            run_daily(db, "2026-09-01")
            if calls != ["master", "bars:2026-09-01", "topix:2026-09-01", "fins:2026-09-01"]:
                _fail(calls)
            # 09-01 の株価がある (前のテスト) ので、09-03 の回は 09-01 の決算も取り直す
            db.query(Statement).filter(Statement.disclosure_no == "L1").delete()
            db.commit()
            calls.clear()
            n = run_daily(db, "2026-09-03")
            if calls[-2:] != ["fins:2026-09-03", "fins:2026-09-01"]:
                _fail(calls)
            if n != 1 or db.get(Statement, "L1") is None:
                _fail(f"late disclosure must count as new: n={n}")
            # 2 回目は増えないので 0 (再計算のスキップを妨げない)
            if run_daily(db, "2026-09-03") != 0:
                _fail("re-fetching the same late disclosure must not count")
    finally:
        db.close()


def test_load_dotenv_keeps_existing_env() -> None:
    env: dict[str, str] = {"KEEP": "1"}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / ".env"
        path.write_text("FOO=bar\nKEEP=nope\n", encoding="utf-8")
        load_dotenv(path, env)
        if env != {"KEEP": "1", "FOO": "bar"}:
            _fail(env)
        load_dotenv(Path(d) / "missing.env", env)
        if env != {"KEEP": "1", "FOO": "bar"}:
            _fail(f"missing .env must be a no-op: {env}")


def main() -> None:
    test_rate_limiter_sleeps_when_full()
    test_get_all_pages_and_429()
    test_429_backoff_gives_up_after_six()
    test_fins_uses_its_own_bucket()
    test_endpoints_and_params()
    test_ingest_writes_price_and_statement_rows()
    test_run_daily_uses_injected_client()
    test_load_dotenv_keeps_existing_env()
    print("ok: jquants ingest")


if __name__ == "__main__":
    main()

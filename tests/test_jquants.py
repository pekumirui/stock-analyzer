"""J-Quants V2 クライアントと日次取込。HTTP はフェイク。sleep は注入する。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-jquants-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"
os.environ.setdefault("JQUANTS_API_KEY", "test-key")

from app.config import load_dotenv  # noqa: E402
from app.db import (  # noqa: E402
    SessionLocal,
    _server_default_sql,
    engine,
    init_db,
    migrate_add_columns,
    missing_columns,
)
from app.ingest import (  # noqa: E402
    _d,
    _f,
    _price_row,
    _statement_row,
    ingest_master,
    ingest_prices_for_code,
    ingest_prices_for_date,
    ingest_statements_for_code,
    ingest_topix,
    run_daily,
)
from app.jquants import JQuantsClient, RateLimiter  # noqa: E402
from app.models import DailyPrice, MonthlyElement, Statement, Stock, Topix  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


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


def test_parse_helpers() -> None:
    if _d("2026-09-01") != date(2026, 9, 1) or _d("20260901") != date(2026, 9, 1):
        _fail("date parse")
    if _d("") is not None or _d(None) is not None or _d("nope") is not None:
        _fail("bad date")
    if _f("1.5") != 1.5 or _f(2) != 2.0:
        _fail("float")
    if _f("") is not None or _f("-") is not None or _f("－") is not None or _f(None) is not None:
        _fail("empty float")
    if _f("x") is not None:
        _fail("nan float")
    halt = _price_row({"Code": "72030", "Date": "2026-09-01", "C": None, "AdjC": None})
    if halt is not None:
        _fail("halt day")
    row = _price_row({
        "Code": "72030", "Date": "2026-09-01",
        "O": "100", "H": "110", "L": "90", "C": "105",
        "Vo": "10", "Va": "1000", "AdjFactor": "1",
        "AdjO": "100", "AdjH": "110", "AdjL": "90", "AdjC": "105", "AdjVo": "10",
        "MktCap": "50",
    })
    if row is None or row["code"] != "72030" or row["adj_close"] != 105.0 or row["date"] != date(2026, 9, 1):
        _fail(f"price row {row}")
    stmt = _statement_row({
        "DiscNo": 1, "Code": "72030", "DiscDate": "2026-09-01",
        "DocType": "FYFinancialStatements_Consolidated_JP",
        "CurPerType": "2Q", "Sales": "100", "OP": "10", "OdP": "8", "NP": "5", "EPS": "1",
        "CurFYEn": "2026-03-31",
    })
    if stmt["disclosure_no"] != "1" or stmt["net_sales"] != 100.0 or stmt["is_revision"] is not False:
        _fail(stmt)
    rev = _statement_row({
        "DiscNo": 2, "Code": "72030", "DiscDate": "2026-09-02",
        "DocType": "EarnForecastRevision", "CurPerType": "FY",
    })
    if rev["is_revision"] is not True:
        _fail(rev)


def test_rate_limiter_sleeps_when_full() -> None:
    clock = {"t": 0.0}
    slept: list[float] = []

    def mono() -> float:
        return clock["t"]

    def sleep(sec: float) -> None:
        slept.append(sec)
        clock["t"] += sec

    lim = RateLimiter(2)
    with patch("app.jquants.time.monotonic", mono), patch("app.jquants.time.sleep", sleep):
        lim.acquire()
        lim.acquire()
        if slept:
            _fail(f"first two must not sleep: {slept}")
        lim.acquire()
        if not slept or slept[0] < 60:
            _fail(f"third acquire must wait a minute: {slept}")


def test_get_all_pages_and_429() -> None:
    jq = JQuantsClient(api_key="x")
    fake = _FakeHTTP([
        _Resp(429),
        _Resp(200, {"data": [{"Code": "72030"}], "pagination_key": "p2"}),
        _Resp(200, {"data": [{"Code": "99840"}]}),
    ])
    jq._client = fake  # type: ignore[method-assign]
    with patch("app.jquants.time.sleep", lambda s: None), patch.object(jq._limiters["default"], "acquire", lambda: None):
        try:
            rows = jq.get_all("/equities/master", {"date": "2026-09-01"})
        except Exception as exc:
            _fail(f"429 should retry: {exc}")
    if rows != [{"Code": "72030"}, {"Code": "99840"}]:
        _fail(f"pages {rows}")
    if fake.calls[0][0] != "/equities/master":
        _fail(fake.calls)
    if fake.calls[2][1].get("pagination_key") != "p2":
        _fail(f"pagination {fake.calls}")
    if jq._bucket("/fins/summary") != "fins" or jq._bucket("/equities/master") != "default":
        _fail("bucket")
    jq.close()
    if not fake.closed:
        _fail("close")
    fake2 = _FakeHTTP([_Resp(200, {"data": [{"Date": "2026-09-01"}]})])
    with JQuantsClient(api_key="x") as jq2:
        if jq2 is None:
            _fail("context manager must yield the client")
        jq2._client = fake2  # type: ignore[method-assign]
        with patch.object(jq2._limiters["default"], "acquire", lambda: None):
            if jq2.calendar("2026-09-01", "2026-09-01") != [{"Date": "2026-09-01"}]:
                _fail("calendar")
    if not fake2.closed:
        _fail("context close")
    seen: list[tuple[str, dict | None]] = []

    def record(path: str, params: dict | None = None) -> list:
        seen.append((path, params))
        return [{"ok": path}]

    jq3 = JQuantsClient(api_key="x")
    jq3.get_all = record  # type: ignore[method-assign]
    if jq3.equities_master("2026-09-01") != [{"ok": "/equities/master"}]:
        _fail("master wrapper")
    if jq3.bars_daily(date="2026-09-01") != [{"ok": "/equities/bars/daily"}]:
        _fail("bars wrapper")
    if jq3.fins_summary(date="2026-09-01") != [{"ok": "/fins/summary"}]:
        _fail("fins wrapper")
    if jq3.topix(from_="2026-01-01", to="2026-01-31") != [{"ok": "/indices/bars/daily/topix"}]:
        _fail("topix wrapper")
    paths = [p for p, _ in seen]
    if paths != [
        "/equities/master",
        "/equities/bars/daily",
        "/fins/summary",
        "/indices/bars/daily/topix",
    ]:
        _fail(paths)


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
                "Code": code or "72030", "Date": date or from_ or "2026-09-01",
                "O": "100", "H": "110", "L": "90", "C": "105",
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
        if n != 1:
            _fail(f"stmt {n}")
        stmt = db.get(Statement, "D1")
        if stmt is None or stmt.net_sales != 1000.0 or stmt.type_of_period != "1Q":
            _fail(stmt)
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
            return []

    try:
        with patch("app.ingest.JQuantsClient", JQ):
            run_daily(db, "2026-09-01")
        if calls != ["master", "bars:2026-09-01", "topix:2026-09-01", "fins:2026-09-01"]:
            _fail(calls)
    finally:
        db.close()


def test_load_dotenv_and_migrate() -> None:
    env: dict[str, str] = {"KEEP": "1"}
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / ".env"
        path.write_text("FOO=bar\nKEEP=nope\n", encoding="utf-8")
        load_dotenv(path, env)
        if env.get("FOO") != "bar" or env.get("KEEP") != "1":
            _fail(env)
        load_dotenv(Path(d) / "missing.env", env)
    sql = _server_default_sql(MonthlyElement.__table__.c.unit)
    if sql is None or "number" not in sql:
        _fail(f"server default {sql}")
    init_db()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE stocks DROP COLUMN product_category"))
    if "product_category" not in missing_columns().get("stocks", []):
        _fail(missing_columns())
    n = migrate_add_columns(verbose=False)
    if n < 1:
        _fail(f"migrate added {n}")
    if missing_columns().get("stocks"):
        _fail(missing_columns())


def main() -> None:
    test_parse_helpers()
    test_rate_limiter_sleeps_when_full()
    test_get_all_pages_and_429()
    test_ingest_writes_price_and_statement_rows()
    test_run_daily_uses_injected_client()
    test_load_dotenv_and_migrate()
    print("ok: jquants ingest")


if __name__ == "__main__":
    main()

"""決算発表予定。発表ログの畳み込みと、カレンダーに出す日。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-earn-date-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")
os.environ.setdefault("JQUANTS_API_KEY", "test-key")

from app.config import EARNINGS_DATE_LOOKBACK_DAYS  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.market.earnings_schedule import (  # noqa: E402
    ActualMonth,
    Cell,
    FQName,
    FiscalMonthDay,
    Schedule,
    board_for_day,
    cells_for_month,
    day_payload,
    days_to_fetch,
    fiscal_year_end,
    fold_current,
    ingest_pub_date,
    kind_of,
    load_current,
    month_payload,
    parse_pub,
    sync,
)
from app.db import upsert  # noqa: E402
from app.models import EarningsDateFetch, EarningsDatePub, Statement, Stock  # noqa: E402

TODAY = date(2026, 10, 2)


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _wire(**over: object) -> dict:
    base = {
        "PubDate": "2026-09-20",
        "SchDate": "2026-11-06",
        "FQName": "2Q",
        "FYE": "0331",
        "Code": "72030",
        "CoName": "トヨタ自動車",
        "CoNameEn": "TOYOTA MOTOR CORPORATION",
    }
    base.update(over)
    return base


def _pub(**over: object):
    pub = parse_pub(_wire(**over))
    if pub is None:
        _fail(f"wire must parse: {over}")
    return pub


class _JQ:
    def __init__(self, rows: list[dict] | None = None, boom: bool = False) -> None:
        self.rows = rows or []
        self.boom = boom
        self.calls: list[str] = []

    def fins_earnings_date(self, pub_date: str) -> list[dict]:
        self.calls.append(pub_date)
        if self.boom:
            raise RuntimeError("http 500")
        return self.rows


def _db():
    init_db()
    db = SessionLocal()
    db.query(EarningsDatePub).delete()
    db.query(EarningsDateFetch).delete()
    db.query(Statement).delete()
    db.query(Stock).delete()
    db.commit()
    return db


def test_fye_labels() -> None:
    feb = FiscalMonthDay.parse("0299")
    mar = FiscalMonthDay.parse("0331")
    odd = FiscalMonthDay.parse("0220")
    if feb is None or feb.label != "2月末" or feb.raw != "0299":
        _fail(feb)
    if mar is None or mar.label != "3月期":
        _fail(mar)
    if odd is None or odd.label != "2/20":
        _fail(odd)
    if FiscalMonthDay.parse("0430") is None or FiscalMonthDay.parse("0430").label != "4月期":
        _fail("April month-end")
    if FiscalMonthDay.parse("1331") is not None or FiscalMonthDay.parse("0232") is not None:
        _fail("bad MMDD must be None")
    if FiscalMonthDay.parse("331") is not None or FiscalMonthDay.parse("") is not None:
        _fail("short FYE")


def test_fiscal_year_end() -> None:
    def fy(fq: FQName, raw: str, sch: date) -> date:
        fye = FiscalMonthDay.parse(raw)
        if fye is None:
            _fail(raw)
        return fiscal_year_end(fq, fye, sch)
    cases = [
        (FQName.Q2, "0331", date(2026, 11, 6), date(2027, 3, 31)),
        (FQName.FY, "0331", date(2026, 5, 12), date(2026, 3, 31)),
        (FQName.Q1, "0331", date(2026, 8, 5), date(2027, 3, 31)),
        (FQName.Q3, "0331", date(2027, 2, 10), date(2027, 3, 31)),
        (FQName.FY, "1231", date(2027, 2, 12), date(2026, 12, 31)),
        (FQName.Q3, "1231", date(2026, 11, 10), date(2026, 12, 31)),
        (FQName.Q2, "0299", date(2026, 10, 9), date(2027, 2, 28)),
        (FQName.FY, "0299", date(2026, 4, 10), date(2026, 2, 28)),
        (FQName.Q1, "0220", date(2026, 6, 30), date(2027, 2, 20)),
        (FQName.Q3, "0630", date(2026, 5, 13), date(2026, 6, 30)),
    ]
    for fq, raw, sch, want in cases:
        if fy(fq, raw, sch) != want:
            _fail((fq, raw, sch, fy(fq, raw, sch), want))


def test_parse_rejects_unknown_and_keeps_tombstone() -> None:
    ok = parse_pub(_wire())
    if ok is None or ok.sch_date != date(2026, 11, 6) or ok.co_name != "トヨタ自動車":
        _fail(ok)
    if ok.fq is not FQName.Q2:
        _fail(ok.fq)
    blank = parse_pub(_wire(SchDate=""))
    if blank is None or blank.sch_date is not None:
        _fail(blank)
    for bad in (
        {"SchDate": "not-a-date"},
        {"FQName": "4Q"},
        {"FQName": "5Q"},
        {"Code": "7203"},
        {"Code": "7203a"},
        {"FYE": "1331"},
        {"PubDate": "20260920"},
    ):
        if parse_pub(_wire(**bad)) is not None:
            _fail(bad)
    if parse_pub({k: v for k, v in _wire().items() if k != "SchDate"}) is not None:
        _fail("missing SchDate")


def test_date_move_and_tombstone() -> None:
    early = _pub(PubDate="2026-08-07", SchDate="2026-11-05")
    later = _pub(PubDate="2026-09-20", SchDate="2026-11-06")
    moved = fold_current([early, later])
    if len(moved) != 1 or moved[0].sch_date != date(2026, 11, 6):
        _fail(moved)
    if fold_current([later, early]) != moved:
        _fail("older pub after newer must not win")
    cleared = _pub(PubDate="2026-09-21", SchDate="")
    if fold_current([early, later, cleared]) != ():
        _fail("empty SchDate tombstone")
    other = parse_pub(_wire(FQName="FY", FYE="0299"))
    both = fold_current([later, other])
    if [s.fq for s in both] != [FQName.Q2, FQName.FY]:
        _fail(both)
    if both[1].fye.label != "2月末":
        _fail(both[1])


def test_same_pub_date_last_in_sequence_wins() -> None:
    a = _pub(SchDate="2026-11-05")
    b = _pub(SchDate="2026-11-06", CoName="訂正後")
    got = fold_current([a, b])
    if len(got) != 1 or got[0].sch_date != date(2026, 11, 6) or got[0].name != "訂正後":
        _fail(got)


def test_cells_and_kinds() -> None:
    today = TODAY
    nov5 = date(2026, 11, 5)
    nov6 = date(2026, 11, 6)
    sched = (
        _sched("72030", "2Q", nov6, "0331"),
        _sched("72030", "FY", nov6, "0331"),
        _sched("67580", "2Q", today, "0331"),
        _sched("99840", "1Q", today, "0331"),
    )
    actuals = ActualMonth(
        counts={date(2026, 10, 1): 4, today: 2},
        codes_on_today=frozenset({"67580"}),
    )
    cells = cells_for_month(date(2026, 10, 1), today, actuals, sched)
    if cells[date(2026, 10, 1)] != _cell("actual", 4, 0):
        _fail(cells[date(2026, 10, 1)])
    mixed = cells[today]
    if mixed.kind != "mixed" or mixed.n != 2 or mixed.pending != 1:
        _fail(mixed)
    if nov5 in cells:
        _fail("moved-away day must be absent")
    future = cells_for_month(
        date(2026, 11, 1), today, ActualMonth({}, frozenset()), sched,
    )
    if future[nov6].kind != "scheduled" or future[nov6].n != 1 or future[nov6].pending != 0:
        _fail(future)
    quiet = cells_for_month(date(2026, 10, 1), today, ActualMonth({}, frozenset()), ())
    if today in quiet:
        _fail("quiet today is omitted from the calendar")
    if kind_of(today, today, False, False) != "scheduled":
        _fail("quiet today is scheduled so the copy is 予定なし")
    past_board = board_for_day(date(2026, 10, 1), today, [{"code": "72030"}], sched)
    if past_board.kind != "actual" or past_board.scheduled:
        _fail(past_board)
    future_board = board_for_day(nov6, today, [{"code": "72030"}], sched)
    if future_board.actuals or [i.code for i in future_board.scheduled] != ["72030"]:
        _fail(future_board)
    if len(future_board.scheduled[0].periods) != 2:
        _fail(future_board.scheduled[0])


def _sched(code: str, fq: str, sch: date, fye: str) -> Schedule:
    parsed = FiscalMonthDay.parse(fye)
    if parsed is None:
        _fail(fye)
    return Schedule(code, FQName(fq), parsed, sch, code)


def _cell(kind: str, n: int, pending: int) -> Cell:
    return Cell(kind, n, pending)  # type: ignore[arg-type]


def test_ingest_ledger_overwrite_and_reit_filter() -> None:
    db = _db()
    try:
        day = date(2026, 9, 20)
        jq = _JQ([
            _wire(SchDate="2026-11-05"),
            _wire(SchDate="2026-11-06", CoName="後勝ち"),
            _wire(Code="89510", CoName="日本ビルファンド"),
            _wire(Code="99990", CoName="未登録"),
            _wire(PubDate="2026-09-21"),
            _wire(FQName="4Q"),
        ])
        n = ingest_pub_date(db, jq, day)  # type: ignore[arg-type]
        if n != 3:
            _fail(f"written {n}")
        row = db.get(EarningsDatePub, ("72030", "2Q", day))
        if row is None or row.sch_date != date(2026, 11, 6) or row.co_name != "後勝ち":
            _fail(row)
        if "TOYOTA" in (row.co_name or ""):
            _fail("english name must not be stored")
        fetch = db.get(EarningsDateFetch, day)
        if fetch is None or fetch.n != 3:
            _fail(fetch)
        if db.get(EarningsDatePub, ("89510", "2Q", day)) is None:
            _fail("REIT row is stored")
        upsert(db, Stock, [
            {"code": "72030", "name": "トヨタ自動車", "product_category": "011"},
            {"code": "89510", "name": "日本ビルファンド投資法人", "product_category": "013"},
        ])
        current = load_current(db, date(2026, 11, 6), date(2026, 11, 6))
        codes = {s.code for s in current}
        if codes != {"72030", "99990"}:
            _fail(codes)
        toyota = next(s for s in current if s.code == "72030")
        if toyota.name != "トヨタ自動車":
            _fail(toyota)
        missing = next(s for s in current if s.code == "99990")
        if missing.name != "未登録":
            _fail(missing)

        again = _JQ([_wire(SchDate="")])
        ingest_pub_date(db, again, day)  # type: ignore[arg-type]
        cleared = db.get(EarningsDatePub, ("72030", "2Q", day))
        if cleared is None or cleared.sch_date is not None:
            _fail(cleared)
        left = {s.code for s in load_current(db, date(2026, 11, 5), date(2026, 11, 6))}
        if "72030" in left:
            _fail(left)

        empty = _JQ([])
        if ingest_pub_date(db, empty, date(2026, 9, 19)) != 0:  # type: ignore[arg-type]
            _fail("empty payload writes 0")
        led = db.get(EarningsDateFetch, date(2026, 9, 19))
        if led is None or led.n != 0:
            _fail(led)

        boom = _JQ(boom=True)
        try:
            ingest_pub_date(db, boom, date(2026, 9, 18))  # type: ignore[arg-type]
        except RuntimeError:
            pass
        else:
            _fail("http error must raise")
        if db.get(EarningsDateFetch, date(2026, 9, 18)) is not None:
            _fail("http error must not ledger")
    finally:
        db.close()


def test_older_ingest_does_not_resurrect() -> None:
    db = _db()
    try:
        ingest_pub_date(db, _JQ([_wire(PubDate="2026-09-20", SchDate="2026-11-06")]), date(2026, 9, 20))  # type: ignore[arg-type]
        ingest_pub_date(db, _JQ([_wire(PubDate="2026-08-07", SchDate="2026-11-05")]), date(2026, 8, 7))  # type: ignore[arg-type]
        if load_current(db, date(2026, 11, 5), date(2026, 11, 5)):
            _fail("older publication must not put the issuer back on Nov 5")
        got = load_current(db, date(2026, 11, 6), date(2026, 11, 6))
        if [s.code for s in got] != ["72030"] or got[0].sch_date != date(2026, 11, 6):
            _fail(got)
    finally:
        db.close()


def test_payloads_mixed_past_and_unfetched() -> None:
    db = _db()
    try:
        ingest_pub_date(db, _JQ([  # type: ignore[arg-type]
            _wire(Code="72030", SchDate="2026-10-02", CoName="トヨタ"),
            _wire(Code="67580", SchDate="2026-10-02", CoName="ソニー"),
            _wire(Code="72030", FQName="FY", SchDate="2026-10-01"),
        ]), date(2026, 9, 20))
        upsert(db, Stock, [{"code": "72030", "name": "トヨタ自動車", "product_category": "011",
                            "sector33": "輸送用機器"}])
        mixed = day_payload(db, TODAY, TODAY, [{"code": "67580", "disclosure_no": "D1"}])
        if mixed["kind"] != "mixed":
            _fail(mixed)
        if [row["code"] for row in mixed["actuals"]] != ["67580"]:
            _fail(mixed["actuals"])
        if [row["code"] for row in mixed["scheduled"]] != ["72030"]:
            _fail(mixed["scheduled"])
        if mixed["scheduled"][0]["name"] != "トヨタ自動車":
            _fail(mixed["scheduled"][0])
        if mixed["scheduled"][0]["sector33"] != "輸送用機器" or mixed["actuals"][0]["sector33"] is not None:
            _fail(mixed)
        if mixed["scheduled"][0]["periods"] != [{"fq": "2Q", "fye": "0331", "label": "FY27.3"}]:
            _fail(mixed["scheduled"][0]["periods"])
        past = day_payload(db, date(2026, 10, 1), TODAY, [{"code": "72030"}])
        if past["kind"] != "actual" or past["scheduled"] != []:
            _fail(past)
        if "ready" in past:
            _fail("past payload has no ready flag")
        future = day_payload(db, date(2026, 11, 6), TODAY, [{"code": "72030"}])
        if future["kind"] != "scheduled" or future["actuals"] or future["scheduled"]:
            _fail(future)
        if future.get("ready") is not False:
            _fail("ledger-incomplete future empty is 予定未取得")
        quiet = day_payload(db, TODAY, TODAY, [])
        # 67580 and 72030 are scheduled today, so this is not quiet. Use a clean day.
        db.query(EarningsDatePub).delete()
        db.commit()
        quiet = day_payload(db, TODAY, TODAY, [])
        if quiet["kind"] != "scheduled" or quiet["scheduled"] or quiet["actuals"]:
            _fail(quiet)
        if "ready" in quiet:
            _fail("quiet today stays 予定なし even before the ledger is full")
        start = TODAY - timedelta(days=EARNINGS_DATE_LOOKBACK_DAYS)
        span = (TODAY - start).days + 1
        upsert(db, EarningsDateFetch, [
            {"pub_date": start + timedelta(days=i), "n": 0} for i in range(span)
        ])
        ready_future = day_payload(db, date(2026, 11, 6), TODAY, [])
        if ready_future["scheduled"] or "ready" in ready_future or ready_future["kind"] != "scheduled":
            _fail(ready_future)
        cal = month_payload(
            db, "2026-10", TODAY,
            ActualMonth({TODAY: 1}, frozenset({"67580"})),
        )
        if cal != {"2026-10-02": {"kind": "actual", "n": 1}}:
            _fail(cal)
    finally:
        db.close()


def test_sync_idempotent_with_tiny_lookback() -> None:
    db = _db()
    calls: list[str] = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def fins_earnings_date(self, pub_date: str) -> list[dict]:
            calls.append(pub_date)
            if pub_date == "2026-09-30":
                return []
            return [_wire(PubDate=pub_date, SchDate="2026-11-06")]

    try:
        with patch("app.market.earnings_schedule.JQuantsClient", Client):
            first = sync(db, TODAY, lookback_days=2)
            after_first = list(calls)
            second = sync(db, TODAY, lookback_days=2)
        if first != 3 or after_first != ["2026-09-30", "2026-10-01", "2026-10-02"]:
            _fail((first, after_first))
        if second != 2 or calls[len(after_first):] != ["2026-10-01", "2026-10-02"]:
            _fail((second, calls))
        led = db.get(EarningsDateFetch, date(2026, 9, 30))
        if led is None or led.n != 0:
            _fail(led)
        pubs = db.query(EarningsDatePub).count()
        if pubs != 2:
            _fail(pubs)
        window = days_to_fetch(TODAY, set(), EARNINGS_DATE_LOOKBACK_DAYS)
        if len(window) != EARNINGS_DATE_LOOKBACK_DAYS + 1:
            _fail(len(window))
        if window[0] != TODAY - timedelta(days=EARNINGS_DATE_LOOKBACK_DAYS) or window[-1] != TODAY:
            _fail((window[0], window[-1]))
        saturday = days_to_fetch(date(2026, 10, 3), set(), 0)
        if saturday != [date(2026, 10, 2), date(2026, 10, 3)]:
            _fail(saturday)
    finally:
        db.close()


def test_batch_syncs_after_indicators() -> None:
    order: list[str] = []

    def run_daily(db, day=None):
        order.append(f"daily:{day}")
        return 0

    def run_backfill(db, years=5):
        order.append(f"backfill:{years}")

    def indicators_current(db):
        order.append("current")
        return True

    def price_frame(db):
        order.append("px")

    def compute_indicators(db, px=None):
        order.append("ind")

    def compute_earnings_events(db, px=None):
        order.append("evt")

    def sync_fn(db, today):
        order.append(f"sync:{today.isoformat()}")
        return 2

    from jobs.market.run_batch import main as batch_main

    buf = io.StringIO()
    with patch("jobs.market.run_batch.ingest.run_daily", run_daily), \
            patch("jobs.market.run_batch.ingest.run_backfill", run_backfill), \
            patch("jobs.market.run_batch.analytics.indicators_current", indicators_current), \
            patch("jobs.market.run_batch.analytics.price_frame", price_frame), \
            patch("jobs.market.run_batch.analytics.compute_indicators", compute_indicators), \
            patch("jobs.market.run_batch.analytics.compute_earnings_events", compute_earnings_events), \
            patch("jobs.market.run_batch.sync", sync_fn), \
            patch("sys.argv", ["run_batch.py", "--date", "2026-10-02"]), \
            redirect_stdout(buf):
        batch_main()
    if order != ["daily:2026-10-02", "current", "sync:2026-10-02"]:
        _fail(order)
    if "決算発表予定 2" not in buf.getvalue() or "完了" not in buf.getvalue():
        _fail(buf.getvalue())

    order.clear()
    buf = io.StringIO()

    def sync_boom(db, today):
        order.append("sync")
        raise RuntimeError("boom")

    with patch("jobs.market.run_batch.ingest.run_daily", lambda db, day=None: order.append("daily") or 1), \
            patch("jobs.market.run_batch.analytics.price_frame", price_frame), \
            patch("jobs.market.run_batch.analytics.compute_indicators", compute_indicators), \
            patch("jobs.market.run_batch.analytics.compute_earnings_events", compute_earnings_events), \
            patch("jobs.market.run_batch.sync", sync_boom), \
            patch("sys.argv", ["run_batch.py", "--date", "2026-10-02"]), \
            redirect_stdout(buf):
        try:
            batch_main()
        except RuntimeError as exc:
            if str(exc) != "boom":
                _fail(exc)
        else:
            _fail("schedule failure must fail the process")
    if order != ["daily", "px", "ind", "evt", "sync"]:
        _fail(order)
    text = buf.getvalue()
    if "決算発表予定の更新に失敗" not in text or "完了" in text:
        _fail(text)

    order.clear()
    with patch("jobs.market.run_batch.ingest.run_backfill", run_backfill), \
            patch("jobs.market.run_batch.analytics.price_frame", price_frame), \
            patch("jobs.market.run_batch.analytics.compute_indicators", compute_indicators), \
            patch("jobs.market.run_batch.analytics.compute_earnings_events", compute_earnings_events), \
            patch("jobs.market.run_batch.sync", sync_fn), \
            patch("sys.argv", ["run_batch.py", "--backfill", "1", "--date", "2026-10-03"]), \
            redirect_stdout(io.StringIO()):
        batch_main()
    if order != ["backfill:1", "px", "ind", "evt", "sync:2026-10-03"]:
        _fail(order)


def _add_stmt(db, no: str, code: str, period: str, fy_end: date, disclosed: date,
             sales: float, **over: object) -> None:
    row = {
        "disclosure_no": no,
        "code": code,
        "disclosed_date": disclosed,
        "disclosed_time": "15:00",
        "type_of_period": period,
        "type_of_document": f"{period}FinancialStatements_Consolidated_JP",
        "fiscal_year_end": fy_end,
        "net_sales": sales,
        "operating_profit": sales / 10,
        "ordinary_profit": sales / 12,
        "profit": sales / 20,
        "eps": sales / 1_000_000,
        "is_revision": False,
    }
    row.update(over)
    db.add(Statement(**row))  # type: ignore[arg-type]


def test_scheduled_carries_latest_actual() -> None:
    """未発表は直近の実績を latest に載せ、発表済みの actuals は形を変えない。"""
    db = _db()
    try:
        ingest_pub_date(db, _JQ([  # type: ignore[arg-type]
            _wire(Code="40620", SchDate="2026-10-29", CoName="イビデン", FQName="2Q"),
            _wire(Code="65040", SchDate="2026-10-29", CoName="富士電機", FQName="2Q"),
            _wire(Code="72030", SchDate="2026-10-29", CoName="トヨタ", FQName="2Q"),
        ]), date(2026, 9, 20))
        upsert(db, Stock, [
            {"code": "40620", "name": "イビデン", "product_category": "011",
             "sector33": "電気機器"},
            {"code": "65040", "name": "富士電機", "product_category": "011",
             "sector33": "電気機器"},
            {"code": "72030", "name": "トヨタ自動車", "product_category": "011",
             "sector33": "輸送用機器"},
        ])
        _add_stmt(db, "ibiden-1q-ly", "40620", "1Q", date(2026, 3, 31),
                  date(2025, 7, 31), 80_000_000)
        _add_stmt(db, "ibiden-1q", "40620", "1Q", date(2027, 3, 31),
                  date(2026, 7, 31), 100_000_000,
                  fy_net_sales=400_000_000, fy_operating_profit=40_000_000)
        _add_stmt(db, "ibiden-1q-dup", "40620", "1Q", date(2027, 3, 31),
                  date(2026, 8, 1), 100_000_000,
                  fy_net_sales=400_000_000, fy_operating_profit=40_000_000)
        _add_stmt(db, "toyota-2q", "72030", "2Q", date(2027, 3, 31),
                  date(2026, 10, 29), 200_000_000)
        db.commit()
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    mixed = client.get("/api/earnings", params={"date": "2026-10-29", "today": "2026-10-29"}).json()
    if mixed.get("kind") != "mixed":
        _fail(mixed)
    actuals = mixed.get("actuals") or []
    if [r["code"] for r in actuals] != ["72030"]:
        _fail(actuals)
    if "latest" in actuals[0]:
        _fail("発表済みの行に latest を足さない")
    if actuals[0]["period"] != "2Q" or (actuals[0].get("cumulative") or {}).get("net_sales") != 200_000_000:
        _fail(actuals[0])

    by = {r["code"]: r for r in mixed.get("scheduled") or []}
    if set(by) != {"40620", "65040"}:
        _fail(by)
    latest = by["40620"].get("latest")
    if latest is None or latest.get("disclosure_no") != "ibiden-1q" or latest.get("period") != "1Q":
        _fail(latest)
    if latest.get("fy_label") != "FY27.3":
        _fail(latest)
    if (latest.get("cumulative") or {}).get("net_sales") != 100_000_000:
        _fail(latest)
    yoy = ((latest.get("yoy") or {}).get("net_sales") or {})
    if yoy.get("pct") != 25.0:
        _fail(yoy)
    prog = ((latest.get("progress") or {}).get("cur") or {})
    if prog.get("net_sales") != 25.0:
        _fail(latest.get("progress"))
    if by["65040"].get("latest") is not None:
        _fail(by["65040"])

    past = client.get("/api/earnings", params={"date": "2026-07-31", "today": "2026-10-29"}).json()
    if past.get("kind") != "actual" or past.get("scheduled") != []:
        _fail(past)
    if [r["code"] for r in past.get("actuals") or []] != ["40620"]:
        _fail(past.get("actuals"))
    if "latest" in past["actuals"][0] or past["actuals"][0]["period"] != "1Q":
        _fail(past["actuals"][0])

    detail = client.get("/api/earnings/detail/40620", params={"disclosure_no": "ibiden-1q"})
    if detail.status_code != 200 or detail.json().get("period") != "1Q":
        _fail(detail.text)


def test_scheduled_latest_markup() -> None:
    js = (ROOT / "static" / "js" / "earnings.js").read_text(encoding="utf-8")
    if "latestPeriodLabel" not in js or 'q+"実績"' not in js:
        _fail("scheduled rows must label the prior period as 1Q実績")
    if "latestBadge" not in js or "qb-act" not in js:
        _fail("1Q実績 must use qb-act")
    if "r.latest" not in js or "metricCells(latest" not in js:
        _fail("scheduled metrics come from latest")
    if "metricCells(r, r.period)" not in js:
        _fail("published rows keep metricCells from the filing")
    if "loadDetail({" not in js.split("async function loadSched")[1].split("async function loadDetail")[0]:
        _fail("selecting a scheduled row with latest loads that filing's detail")
    if "まだ開示されていません" not in js:
        _fail("no prior filing still reads まだ開示されていません")
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    if "#earn-tbl td.fy, #earn-tbl th.fy { width:1%; white-space:nowrap; }" not in css:
        _fail("決算期 1Q実績 must stay on one line")


def test_routes() -> None:
    db = _db()
    try:
        ingest_pub_date(db, _JQ([_wire(SchDate="2026-11-06")]), date(2026, 9, 20))  # type: ignore[arg-type]
        upsert(db, Stock, [{"code": "72030", "name": "トヨタ自動車", "product_category": "011"}])
    finally:
        db.close()
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    cal = client.get("/api/earnings/calendar", params={"month": "2026-11", "today": "2026-10-02"})
    if cal.status_code != 200 or cal.json().get("2026-11-06") != {"kind": "scheduled", "n": 1}:
        _fail(cal.text)
    day = client.get("/api/earnings", params={"date": "2026-11-06", "today": "2026-10-02"})
    body = day.json()
    if body.get("kind") != "scheduled" or body.get("scheduled") != [{
        "code": "72030",
        "name": "トヨタ自動車",
        "periods": [{"fq": "2Q", "fye": "0331", "label": "FY27.3"}],
        "sector33": None,
        "latest": None,
    }]:
        _fail(body)
    if body.get("ready") is not False or body.get("actuals") != []:
        _fail(body)


def main() -> None:
    test_fye_labels()
    test_fiscal_year_end()
    test_parse_rejects_unknown_and_keeps_tombstone()
    test_date_move_and_tombstone()
    test_same_pub_date_last_in_sequence_wins()
    test_cells_and_kinds()
    test_ingest_ledger_overwrite_and_reit_filter()
    test_older_ingest_does_not_resurrect()
    test_payloads_mixed_past_and_unfetched()
    test_sync_idempotent_with_tiny_lookback()
    test_batch_syncs_after_indicators()
    test_scheduled_carries_latest_actual()
    test_scheduled_latest_markup()
    test_routes()
    print("ok: earnings schedule")


if __name__ == "__main__":
    main()

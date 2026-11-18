"""EDINET コードリストと書類一覧の取込（KAB-72）。HTTP はフェイク。

testdata/entry_*.json は edinet-api（ラッパー）の tests/fixtures の写し。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.codes import ingest_code_list, link_stocks, parse_list_date  # noqa: E402
from app.edinet.documents import (  # noqa: E402
    CodeLookup,
    days_to_fetch,
    ingest_days,
    is_finalized,
    relink_codes,
    store_listing,
)
from app.edinet_wrapper._core import parse_document_list  # noqa: E402
from app.models import (  # noqa: E402
    EdinetDocument,
    EdinetFiler,
    EdinetListDay,
    Stock,
)

DATA = Path(__file__).resolve().parent / "testdata"
JST = ZoneInfo("Asia/Tokyo")


def _jst(*args) -> datetime:
    """DB と同じ JST naive。"""
    return datetime(*args, tzinfo=JST).replace(tzinfo=None)


FETCHED = _jst(2023, 4, 4, 9, 0)

CODE_CSV = (
    "ダウンロード実行日,2023年04月03日現在,件数,4件\n"
    "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,"
    "提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号\n"
    '"E10001","内国法人・組合","上場","有","100","3月31日","エディネット株式会社",'
    '"EDINET","エディネット","東京","電気機器","72030","6000012010023"\n'
    '"E10002","内国法人・組合","上場","有","50","3月31日","優先株親株式会社",'
    '"PREF","ユウセン","東京","空運業","92020",""\n'
    '"E10003","内国法人・組合","非上場","無","","","非上場株式会社",'
    '"","","大阪","","",""\n'
    '"E10004","内国法人・組合","非上場","有","10","12月31日","旧エディネット株式会社",'
    '"","","東京","電気機器","72030",""\n'
)


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EdinetcodeDlInfo.csv", text.encode("cp932"))
    return buf.getvalue()


def _entry(name: str, **over) -> dict:
    d = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    d.update(over)
    return d


def _listing(day: str, results: list[dict], process: str | None = None):
    return parse_document_list({
        "metadata": {
            "title": "提出された書類を把握するためのAPI",
            "parameter": {"date": day, "type": "2"},
            "resultset": {"count": len(results)},
            "processDateTime": process or f"{day} 13:01",
            "status": "200",
            "message": "OK",
        },
        "results": results,
    })


class FakeClient:
    def __init__(self, listings: dict[date, object]):
        self.listings = listings
        self.calls: list[date] = []

    def list_documents(self, day):
        self.calls.append(day)
        return self.listings[day]


def _reset(db) -> None:
    for model in (EdinetDocument, EdinetListDay, EdinetFiler, Stock):
        db.execute(delete(model))
    db.add_all([
        Stock(code="72030", name="エディネット", product_category="011", market="プライム"),
        Stock(code="92020", name="優先株親", product_category="011", market="プライム"),
        Stock(code="92025", name="優先株親（優先株式）", product_category="011", market="プライム"),
        Stock(code="131A0", name="プロ", product_category="011", market="TOKYO PRO MARKET"),
        Stock(code="99990", name="新規上場", product_category="011", market="グロース"),
    ])
    db.commit()


def _doc(db, file_date: str, seq: int) -> EdinetDocument:
    return db.get(EdinetDocument, (date.fromisoformat(file_date), seq))


def test_code_list_links_stocks() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        payload = _zip(CODE_CSV)
        assert parse_list_date(payload) == date(2023, 4, 3)
        assert ingest_code_list(db, payload, FETCHED) == 4
        f = db.get(EdinetFiler, "E10001")
        assert (f.sec_code, f.listed, f.industry, f.list_date) == (
            "72030", "上場", "電気機器", date(2023, 4, 3))
        st = link_stocks(db)
        codes = dict(db.execute(select(Stock.code, Stock.edinet_code)).all())
        assert codes["72030"] == "E10001"
        assert st["duplicate_sec_codes"] == ["72030"]
        assert codes["92025"] == "E10002"
        assert codes["131A0"] is None
        assert st["unmatched_equity"] == ["99990"]
        assert st["linked"] == 3
        assert link_stocks(db)["changed"] == 0
    finally:
        db.close()


def test_store_listing_idempotent_and_links_code() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        ingest_code_list(db, _zip(CODE_CSV), FETCHED)
        lookup = CodeLookup.load(db)
        orig = _entry("entry_withdrawn_child")
        no_sec = _entry("entry_withdrawn_child", seqNumber=9, docID="S100NOSE",
                        secCode=None, edinetCode="E10002")
        other = _entry("entry_withdrawn_child", seqNumber=10, docID="S100UNLS",
                       secCode=None, edinetCode="E10003")
        listing = _listing("2023-03-01", [orig, no_sec, other])
        st = store_listing(db, date(2023, 3, 1), listing, lookup, FETCHED)
        assert st == {"rows": 3, "events": 0, "linked": 2}
        store_listing(db, date(2023, 3, 1), listing, lookup, FETCHED)
        assert db.query(EdinetDocument).count() == 3
        r = _doc(db, "2023-03-01", 8)
        assert (r.doc_id, r.code, r.doc_type_code, r.xbrl_flag) == ("S100ORIG", "72030", "120", True)
        assert r.submit_datetime == _jst(2023, 3, 1, 9, 0)
        assert _doc(db, "2023-03-01", 9).code == "92020"
        assert _doc(db, "2023-03-01", 10).code is None
        day = db.get(EdinetListDay, date(2023, 3, 1))
        assert (day.count, day.max_seq, day.finalized) == (3, 10, False)
    finally:
        db.close()


def test_events_update_original_rows() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        lookup = CodeLookup.load(db)
        nd_orig = _entry("entry_withdrawn_child", seqNumber=5, docID="S100NDSC")
        store_listing(db, date(2023, 3, 1),
                      _listing("2023-03-01", [_entry("entry_withdrawn_child"), nd_orig]),
                      lookup, FETCHED)
        # 取下げ: EDINET は元の行のメタデータを空にして返す。前の値は残す
        blank = _entry("entry_withdrawn_child", withdrawalStatus="2", filerName=None,
                       secCode=None, edinetCode=None, docTypeCode=None)
        notice = _entry("entry_withdrawal_notice")
        edit = _entry("entry_edit_notice", docID="S100ORIG")
        nd_start = _entry("entry_nondisclosure_start")
        st = store_listing(db, date(2023, 4, 3),
                           _listing("2023-04-03", [notice, edit, nd_start]), lookup, FETCHED)
        assert st["events"] == 2
        r = _doc(db, "2023-03-01", 8)
        assert (r.withdrawal_status, r.doc_info_edit_status) == ("2", "2")
        assert _doc(db, "2023-03-01", 5).disclosure_status == "2"
        store_listing(db, date(2023, 3, 1),
                      _listing("2023-03-01", [blank, nd_orig]), lookup, FETCHED)
        r = _doc(db, "2023-03-01", 8)
        db.refresh(r)
        assert (r.filer_name, r.code, r.doc_type_code, r.withdrawal_status) == (
            "エディネット株式会社", "72030", "120", "2")
        assert _doc(db, "2023-03-01", 5).disclosure_status == "2"
        store_listing(db, date(2023, 4, 4),
                      _listing("2023-04-04", [_entry("entry_nondisclosure_end")]), lookup, FETCHED)
        row = _doc(db, "2023-03-01", 5)
        db.refresh(row)
        assert row.disclosure_status == "0"
    finally:
        db.close()


def test_relink_codes_after_code_list() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        lookup = CodeLookup.load(db)
        e = _entry("entry_withdrawn_child", secCode=None, edinetCode="E10002")
        store_listing(db, date(2023, 3, 1), _listing("2023-03-01", [e]), lookup, FETCHED)
        assert _doc(db, "2023-03-01", 8).code is None
        ingest_code_list(db, _zip(CODE_CSV), FETCHED)
        assert relink_codes(db) == 1
        r = _doc(db, "2023-03-01", 8)
        db.refresh(r)
        assert r.code == "92020"
    finally:
        db.close()


def test_finalized_and_days_to_fetch() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        assert not is_finalized(date(2023, 4, 3), _jst(2023, 4, 3, 23, 59), FETCHED)
        assert is_finalized(date(2023, 4, 3), _jst(2023, 4, 4, 0, 1), FETCHED)
        assert is_finalized(date(2023, 4, 3), None, FETCHED)
        today = date(2023, 4, 10)
        assert days_to_fetch(db, today)[0] == date(2023, 4, 3)
        assert days_to_fetch(db, today)[-1] == today
        listings = {
            date(2023, 4, 8): _listing("2023-04-08", [], "2023-04-09 00:05"),
            date(2023, 4, 9): _listing("2023-04-09", [], "2023-04-09 18:00"),
        }
        client = FakeClient(listings)
        ingest_days(db, client, sorted(listings), log=lambda *_: None)
        days = days_to_fetch(db, today)
        assert date(2023, 4, 8) not in days
        assert date(2023, 4, 9) in days
        db.add(EdinetListDay(file_date=date(2020, 1, 6), count=0, finalized=True, fetched_at=FETCHED))
        db.add(EdinetListDay(file_date=date(2023, 3, 1), count=0, finalized=False, fetched_at=FETCHED))
        db.commit()
        days = days_to_fetch(db, today)
        assert days[0] == date(2023, 3, 1)
        assert date(2020, 1, 7) not in days
        assert days_to_fetch(db, today, date(2023, 4, 8), date(2023, 4, 8)) == []
        assert days_to_fetch(db, today, date(2023, 4, 8), date(2023, 4, 8), force=True) == [date(2023, 4, 8)]
        assert days_to_fetch(db, today, date(2023, 4, 12)) == []
    finally:
        db.close()

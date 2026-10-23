"""EDINET 自己株券買付状況報告書（KAB-79）。ダウンロードはフェイク、XBRL は実物の表の形に合わせた合成。"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-buyback-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.buybacks import (  # noqa: E402
    exit_status,
    keep_after,
    mark_outside_window,
    parse_buyback,
    pending_documents,
    refresh_versions,
    run,
)
from app.edinet.textblocks import integer, is_dash, jp_dates, read_instance  # noqa: E402
from app.edinet_wrapper.errors import EdinetNotFoundError  # noqa: E402
from app.models import (  # noqa: E402
    EdinetBuyback,
    EdinetBuybackResolution,
    EdinetDocument,
    Stock,
)
from tests.edinet.test_edinet_large_holdings import _ErrClient  # noqa: E402
from tests.edinet.xbrl_fixture import para, table, xbrl_zip  # noqa: E402

FETCHED = datetime(2026, 10, 15, 18, 0)  # noqa: DTZ001
SBR = "jpcrp-sbr_cor"


def _holding(issued="165,356,289", treasury="2,119,557") -> str:
    return para("３【保有状況】", "2026年9月30日現在") + table(
        ["報告月末日における保有状況", "株式数（株）"], ["発行済株式総数", issued], ["保有自己株式数", treasury])


def _board_two() -> str:
    """決議 2 件（終わりかけの決議と始まった決議）。取得日の行・計・累計・進捗。"""
    return para("（２）【取締役会決議による取得の状況】") + table(
        ["2026年９月30日現在"],
        ["区分", "株式数(株)", "価額の総額(円)"],
        ["取締役会(2025年８月１日)での決議状況 (取得期間 2025年11月１日～2026年10月31日)", "", "15,000,000",
         "30,000,000,000"],
        ["報告月における取得自己株式(取得日)", "９月１日", "224,000", "566,903,300"],
        ["", "９月２日", "273,400", "1,331,204,005"],
        ["計", "―", "497,400", "1,898,107,305"],
        ["報告月末現在の累計取得自己株式", "", "7,683,700", "27,134,681,260"],
        ["自己株式取得の進捗状況(％)", "", "51.22", "90.45"],
        ["取締役会(2026年７月30日)での決議状況 (取得期間 2026年11月１日～2027年10月31日)", "", "14,000,000",
         "40,000,000,000"],
        ["報告月における取得自己株式(取得日)", "―", "―", "―"],
        ["計", "―", "―", "―"],
        ["報告月末現在の累計取得自己株式", "", "―", "―"],
        ["自己株式取得の進捗状況(％)", "", "―", "―"],
    )


def _board_one(month_shares="189,200", cum="1,520,000") -> str:
    """期間が決議の次の行・枠に（上限）・累積の表記・末日。"""
    return para("（２）【取締役会決議による取得の状況】", "2026年9月30日現在") + table(
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["取締役会（2026年６月30日）での決議状況", "1,520,000（上限）", "7,000,000,000（上限）"],
        ["（取得期間 2026年７月１日～2026年12月末日）"],
        ["報告月における取得自己株式（取得日）", "9月1日", month_shares, "731,179,500"],
        ["計", "－", month_shares, "731,179,500"],
        ["報告月末現在の累積取得自己株式", cum, "6,067,616,000"],
        ["自己株式取得の進捗状況（％）", "100.00", "86.68"],
    )


def _report(board: str, *, amends: str = "－", shareholders: str | None = None) -> bytes:
    return xbrl_zip([
        (f"{SBR}:DocumentTitleCoverPage", "自己株券買付状況報告書"),
        (f"{SBR}:ReportingPeriodCoverPage", "自　2026年9月1日　至　2026年9月30日"),
        ("jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI", amends),
        (f"{SBR}:AcquisitionsByResolutionOfShareholdersMeetingTextBlock",
         shareholders or para("（１）【株主総会決議による取得の状況】", "該当事項はありません。")),
        (f"{SBR}:AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock", board),
        (f"{SBR}:DisposalsOfTreasurySharesTextBlock", para("２【処理状況】", "該当事項はありません。")),
        (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding()),
    ])


def _doc(seq: int, doc_id: str, **over) -> EdinetDocument:
    row = {
        "file_date": date(2026, 10, 14), "seq_number": seq, "doc_id": doc_id, "code": "43850",
        "edinet_code": "E34064", "sec_code": "43850", "filer_name": "株式会社メルカリ",
        "doc_type_code": "220", "form_code": "170000", "ordinance_code": "010",
        "doc_description": "自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
        "submit_datetime": datetime(2026, 10, 14, 15, seq),  # noqa: DTZ001
        "withdrawal_status": "0", "doc_info_edit_status": "0", "disclosure_status": "0",
        "xbrl_flag": True, "pdf_flag": True, "attach_doc_flag": False, "english_doc_flag": False,
        "csv_flag": True, "legal_status": "1", "fetched_at": FETCHED,
    }
    row.update(over)
    return EdinetDocument(**row)


def _reset(db) -> None:
    for model in (EdinetBuybackResolution, EdinetBuyback, EdinetDocument, Stock):
        db.execute(delete(model))
    db.add(Stock(code="43850", name="メルカリ", product_category="011", market="プライム"))
    db.commit()


def _run(db, client, docs, *, today: date | None = None):
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        st = run(db, client, docs, today=today)
    return st, buf.getvalue()


TODAY = date(2026, 10, 22)


def test_helpers() -> None:
    assert jp_dates("2026年９月末日") == [date(2026, 9, 30)]
    assert jp_dates("2026年12月末日") == [date(2026, 12, 31)]
    assert jp_dates("令和８年２月末日まで、令和元年5月1日") == [date(2026, 2, 28), date(2019, 5, 1)]
    assert jp_dates("取得期間 2026年８月６日～2026年10月30日") == [date(2026, 8, 6), date(2026, 10, 30)]
    assert is_dash("―") and is_dash("－（株）") and is_dash("ー") and not is_dash("") and not is_dash("1")
    assert integer("1,520,000（上限）") == 1520000
    assert integer("―（株）") is None
    assert keep_after(TODAY) == date(2025, 10, 22)


def test_parse_two_resolutions() -> None:
    rep = parse_buyback(read_instance(_report(_board_two())))
    assert (rep.period_start, rep.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (rep.issued_shares, rep.treasury_shares) == (165356289, 2119557)
    a, b = rep.resolutions
    assert (a.kind, a.body, a.resolution_date) == ("board", "取締役会", date(2025, 8, 1))
    assert (a.acquire_from, a.acquire_to) == (date(2025, 11, 1), date(2026, 10, 31))
    assert (a.limit_shares, a.limit_amount) == (15000000, 30000000000)
    assert (a.month_shares, a.month_amount) == (497400, 1898107305)
    assert (a.cumulative_shares, a.cumulative_amount) == (7683700, 27134681260)
    assert (a.progress_shares, a.progress_amount) == (0.5122, 0.9045)
    # 始まったばかりの決議: 「―」は 0、進捗は NULL
    assert (b.resolution_date, b.limit_shares) == (date(2026, 7, 30), 14000000)
    assert (b.month_shares, b.cumulative_shares, b.progress_shares) == (0, 0, None)


def test_parse_period_on_next_row_and_shareholders() -> None:
    sh = para("（１）【株主総会決議による取得の状況】") + table(
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["定時株主総会（2026年６月25日）での決議状況 （取得日 2026年９月15日）", "100,000", "300,000,000"],
        ["報告月における取得自己株式", "100,000", "290,000,000"],
        ["計", "100,000", "290,000,000"],
        ["報告月末現在の累計取得自己株式", "100,000", "290,000,000"],
        ["自己株式取得の進捗状況（％）", "100.0", "96.7"],
    )
    rep = parse_buyback(read_instance(_report(_board_one(), shareholders=sh)))
    s, b = rep.resolutions
    assert (s.kind, s.body, s.resolution_date) == ("shareholders", "定時株主総会", date(2026, 6, 25))
    assert (s.acquire_from, s.acquire_to) == (date(2026, 9, 15), date(2026, 9, 15))
    assert (s.month_shares, s.progress_amount) == (100000, 0.967)
    assert (b.acquire_from, b.acquire_to) == (date(2026, 7, 1), date(2026, 12, 31))
    assert (b.limit_shares, b.limit_amount) == (1520000, 7000000000)
    assert (b.cumulative_shares, b.progress_shares) == (1520000, 1.0)


def test_run_versions_and_rerun() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100ORIG"),
            _doc(2, "S100BRKN"),
            _doc(3, "S100NOXB", xbrl_flag=False),
            _doc(4, "S100LVH1", doc_type_code="350"),
        ])
        db.commit()
        client = _ErrClient({"S100ORIG": _report(_board_two()), "S100BRKN": b"not a zip"})
        docs = pending_documents(db, today=TODAY)
        assert {d.doc_id for d in docs} == {"S100ORIG", "S100BRKN"}
        st, out = _run(db, client, docs, today=TODAY)
        assert (st["parsed"], st["failed"], st["missing"], st["errors"]) == (1, 1, 0, 0)
        assert exit_status(st) == 1
        assert "edinet buyback parse-error n=1 sample=S100BRKN" in out

        orig = db.get(EdinetBuyback, "S100ORIG")
        assert (orig.code, orig.report_month, orig.resolution_count) == ("43850", "2026-09", 2)
        assert (orig.month_shares, orig.month_amount) == (497400, 1898107305)
        assert (orig.is_latest, orig.original_doc_id) == (True, "S100ORIG")
        res = db.scalars(select(EdinetBuybackResolution).where(EdinetBuybackResolution.doc_id == "S100ORIG")
                         .order_by(EdinetBuybackResolution.resolution_no)).all()
        assert [r.resolution_date for r in res] == [date(2025, 8, 1), date(2026, 7, 30)]

        # 再実行しても取り直さない・行は増えない
        assert pending_documents(db, today=TODAY) == []
        _run(db, client, pending_documents(db, reparse=True, today=TODAY), today=TODAY)
        assert db.query(EdinetBuybackResolution).count() == 2
        assert db.query(EdinetBuyback).count() == 2

        # 訂正（230）: 大元に束ね、最新は訂正
        db.add(_doc(5, "S100AMD1", doc_type_code="230", form_code="170001", parent_doc_id="S100ORIG",
                    doc_description="訂正自己株券買付状況報告書（法２４条の６第１項に基づくもの）"))
        db.commit()
        client.payloads["S100AMD1"] = _report(_board_one(), amends="S100ORIG")
        _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        db.expire_all()
        amd = db.get(EdinetBuyback, "S100AMD1")
        assert (amd.is_amendment, amd.amends_doc_id, amd.original_doc_id) == (True, "S100ORIG", "S100ORIG")
        assert (db.get(EdinetBuyback, "S100ORIG").is_latest, amd.is_latest) == (False, True)
        assert amd.month_shares == 189200

        # 訂正の取下げ: 元が最新に戻る
        db.query(EdinetDocument).filter_by(doc_id="S100AMD1").update({"withdrawal_status": "2"})
        db.commit()
        assert refresh_versions(db) == 2
        db.expire_all()
        assert (db.get(EdinetBuyback, "S100AMD1").withdrawn, db.get(EdinetBuyback, "S100AMD1").is_latest) == (True, False)
        assert db.get(EdinetBuyback, "S100ORIG").is_latest is True
        assert refresh_versions(db) == 0
    finally:
        db.close()


def test_missing_and_outside_window() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        old_day = keep_after(TODAY) - timedelta(days=1)
        db.add_all([_doc(1, "S100GONE"), _doc(2, "S100OLD1", file_date=old_day)])
        db.commit()
        assert [d.doc_id for d in pending_documents(db, today=TODAY)] == ["S100GONE"]
        assert mark_outside_window(db, today=TODAY) == 1
        old = db.get(EdinetBuyback, "S100OLD1")
        assert old.parse_status == "failed" and "OutsideEdinetWindow" in old.parse_error
        client = _ErrClient({"S100GONE": EdinetNotFoundError("Not Found", status=404, endpoint="/documents/S100GONE")})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["missing"], exit_status(st)) == (1, 0)
        assert "edinet buyback missing n=1 sample=S100GONE" in out
        assert pending_documents(db, today=TODAY) == []
        assert client.calls == ["S100GONE"]
    finally:
        db.close()


def main() -> None:
    init_db()
    test_helpers()
    test_parse_two_resolutions()
    test_parse_period_on_next_row_and_shareholders()
    test_run_versions_and_rerun()
    test_missing_and_outside_window()
    print("ok")


if __name__ == "__main__":
    main()

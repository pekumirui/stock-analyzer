"""EDINET 有報・半期報 XBRL → xbrl_financials（KAB-73）。ダウンロードはフェイク、ZIP は合成。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-xbrl-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.edinet.xbrl import keep_fact, pending_documents, run, title_meta
from app.models import (
    EdinetDocument,
    Stock,
    XbrlContext,
    XbrlFact,
    XbrlFiling,
    XbrlFinancial,
    XbrlMajorShareholder,
    XbrlSegment,
    XbrlShareholderFiling,
    XbrlShareholdingByCategory,
    XbrlStdItem,
)
from app.xbrl.context import ParsedContext
from app.xbrl.mappings import apply_mappings
from app.xbrl.parser import ParsedFact
from app.xbrl.revisions import xbrl_points
from tests.xbrl.test_xbrl_parse import IFRS_NS, MANUFACTURING, _ixbrl_html

FETCHED = datetime(2025, 6, 28, 9, 0)


def _zip(standard: str = "Japan GAAP", facts=MANUFACTURING, **kw) -> bytes:
    html = _ixbrl_html(facts, filing_instant="2025-06-27", dei_period_end="2026-03-31", **kw).replace(
        "</ix:hidden>",
        '<ix:nonNumeric name="jpdei_cor:AccountingStandardsDEI" contextRef="FilingDateInstant">'
        f"{standard}</ix:nonNumeric>\n    </ix:hidden>",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/0101010_honbun_jpcrp030000-asr-001_ixbrl.htm", html)
        zf.writestr("XBRL/AuditDoc/jpaud-aar-cn-001_ixbrl.htm", html)
    return buf.getvalue()


class _Got:
    def __init__(self, content: bytes):
        self.content = content


class NotFound(Exception):
    """vendor の 404（status 属性。app.edinet.client.is_not_found が見る）。"""
    status = 404


class FakeClient:
    def __init__(self, payloads: dict[str, bytes], gone: tuple[str, ...] = ()):
        self.payloads = payloads
        self.gone = gone
        self.calls: list[str] = []

    def download(self, doc_id, file_type):
        self.calls.append(doc_id)
        if doc_id in self.gone:
            raise NotFound("404 Not Found")
        if doc_id not in self.payloads:
            raise RuntimeError("404")
        return _Got(self.payloads[doc_id])


def _doc(seq: int, doc_id: str, **over) -> EdinetDocument:
    row = {
        "file_date": date(2025, 6, 27), "seq_number": seq, "doc_id": doc_id, "code": "72030",
        "edinet_code": "E02144", "sec_code": "72030", "doc_type_code": "120",
        "doc_description": "有価証券報告書－第121期(2025/04/01－2026/03/31)",
        "submit_datetime": datetime(2025, 6, 27, 15, 30),
        "withdrawal_status": "0", "doc_info_edit_status": "0", "disclosure_status": "0",
        "xbrl_flag": True, "pdf_flag": True, "attach_doc_flag": False, "english_doc_flag": False,
        "csv_flag": True, "legal_status": "1", "fetched_at": FETCHED,
    }
    row.update(over)
    return EdinetDocument(**row)


def _reset(db) -> None:
    for model in (XbrlMajorShareholder, XbrlShareholdingByCategory, XbrlShareholderFiling,
                  XbrlSegment, XbrlFinancial, XbrlFact, XbrlContext, XbrlFiling, EdinetDocument, Stock):
        db.execute(delete(model))
    db.add(Stock(code="72030", name="トヨタ", product_category="011", market="プライム"))
    db.commit()
    apply_mappings(db)


def _value(db, filing_id: int, std: str) -> Decimal | None:
    row = (db.query(XbrlFinancial).join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
           .filter(XbrlFinancial.filing_id == filing_id, XbrlStdItem.code == std,
                   XbrlFinancial.value_kind == "actual").one_or_none())
    return None if row is None else Decimal(str(row.value))


def test_title_meta() -> None:
    m = title_meta("120", "有価証券報告書－第121期(2024/04/01－2025/03/31)")
    assert (m["doc_type"], m["period_type"], m["is_amendment"], m["accounting_standard"]) == (
        "annual", "FY", False, None)
    m = title_meta("130", "訂正有価証券報告書－第40期(2024/04/01－2025/03/31)")
    assert (m["doc_type"], m["period_type"], m["is_amendment"]) == ("annual", "FY", True)
    m = title_meta("160", "半期報告書－第78期(2025/04/01－2026/03/31)")
    assert (m["doc_type"], m["period_type"], m["is_amendment"]) == ("semiannual", "Q2", False)
    m = title_meta("140", "四半期報告書－第70期第3四半期(2023/10/01－2023/12/31)")
    assert (m["doc_type"], m["period_type"]) == ("quarterly", "Q3")
    assert title_meta("140", "四半期報告書－第138期第2四半期(2019/07/01－2019/09/30)")["period_type"] == "Q2"
    assert title_meta("150", "訂正四半期報告書－第１１期第１四半期")["period_type"] == "Q1"


def test_keep_fact() -> None:
    def ctx(rel, dims=None):
        return ParsedContext(context_ref="c", period_type="duration", start_date=None,
                             end_date=date(2025, 3, 31), is_consolidated=1, relative_period=rel,
                             dimensions=dims or {})

    def fact(key="jppfs_cor"):
        return ParsedFact(namespace_uri="u", namespace_key=key, local_name="NetSales",
                          context_ref="c", unit="JPY", scale=6, value_num=Decimal(1),
                          value_text=None, is_nil=False, is_numeric=True)

    assert keep_fact(fact(), ctx("current"))
    assert keep_fact(fact(), ctx("current", {"jppfs_cor:ConsolidatedOrNonConsolidatedAxis":
                                             "jppfs_cor:NonConsolidatedMember"}))
    assert not keep_fact(fact(), ctx("prior1"))
    assert keep_fact(fact(), ctx("current", {"jpcrp_cor:OperatingSegmentsAxis": "x:AutoMember"}))
    assert not keep_fact(fact(), ctx("current", {"jppfs_cor:ComponentsOfEquityAxis": "x:CapitalStockMember"}))
    name = ParsedFact(namespace_uri="u", namespace_key="jpcrp_cor", local_name="NameMajorShareholders",
                      context_ref="c", unit=None, scale=None, value_num=None, value_text="A",
                      is_nil=False, is_numeric=False)
    assert keep_fact(name, ctx("current", {"jpcrp_cor:MajorShareholdersAxis":
                                           "jpcrp_cor:No1MajorShareholdersMember"}))
    vr = ParsedFact(namespace_uri="u", namespace_key="jpcrp_cor",
                    local_name="NameMajorShareholdersVotingRights",
                    context_ref="c", unit=None, scale=None, value_num=None, value_text="A",
                    is_nil=False, is_numeric=False)
    assert keep_fact(vr, ctx("current", {"jpcrp_cor:MajorShareholdersVotingRightsAxis":
                                         "jpcrp_cor:No1MajorShareholdersVotingRightsMember"}))
    assert not keep_fact(fact(), ctx("current", {"jpcrp_cor:MajorShareholdersAxis":
                                                "jpcrp_cor:No1MajorShareholdersMember"}))
    cat = ParsedFact(namespace_uri="u", namespace_key="jpcrp_cor",
                     local_name="NumberOfShareholdersFinancialInstitutions",
                     context_ref="c", unit=None, scale=None, value_num=Decimal(1),
                     value_text=None, is_nil=False, is_numeric=True)
    assert keep_fact(cat, ctx("current", {"jpcrp_cor:ClassesOfSharesAxis":
                                         "jpcrp_cor:OrdinaryShareMember"}))
    assert not keep_fact(cat, ctx("prior1", {"jpcrp_cor:ClassesOfSharesAxis":
                                            "jpcrp_cor:OrdinaryShareMember"}))
    assert keep_fact(fact("jpdei_cor"), ctx(None))
    assert not keep_fact(fact(), None)


def test_ingest_normalize_and_skip() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100AAAA"),
            _doc(2, "S100WDRN", withdrawal_status="2"),
            _doc(3, "S100NOCD", code=None),
            _doc(4, "S100ONEX", doc_type_code="350"),
            _doc(5, "S100NOXB", xbrl_flag=False),
            _doc(6, "S100FAIL"),
        ])
        db.commit()
        docs = pending_documents(db)
        assert [d.doc_id for d in docs] == ["S100AAAA", "S100FAIL"]
        client = FakeClient({"S100AAAA": _zip()})
        st = run(db, client, docs)
        assert st["parsed"] == 1 and st["errors"] == 1
        f = db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100AAAA").one()
        assert (f.code, f.doc_type, f.period_type, f.accounting_standard, f.raw_path) == (
            "72030", "annual", "FY", "JPGAAP", "edinet:S100AAAA")
        assert f.submitted_at == datetime(2025, 6, 27, 15, 30)
        assert _value(db, f.id, "net_sales") == Decimal("200000000")
        assert _value(db, f.id, "total_assets") == Decimal("1000000000")
        refs = {c.context_ref for c in db.query(XbrlContext).filter_by(filing_id=f.id)}
        assert "PriorYearDuration" not in refs and "FilingDateInstant" in refs
        assert [d.doc_id for d in pending_documents(db)] == ["S100FAIL"]
        assert [d.doc_id for d in pending_documents(db, reparse=True)] == ["S100AAAA", "S100FAIL"]
        assert xbrl_points(db, "72030", date(2026, 1, 1)) == []
    finally:
        db.close()


def test_ifrs_and_withdrawal_retracts() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100IFRS"))
        db.commit()
        ifrs = [("RevenueIFRS", "CurrentYearDuration", "JPY", "6", "300")]
        payload = _zip("IFRS", facts=ifrs, ns_prefix="jpigp_cor", ns_uri=IFRS_NS)
        run(db, FakeClient({"S100IFRS": payload}), pending_documents(db))
        f = db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100IFRS").one()
        assert f.accounting_standard == "IFRS"
        assert _value(db, f.id, "net_sales") == Decimal("300000000")
        row = db.get(EdinetDocument, (date(2025, 6, 27), 1))
        row.withdrawal_status = "2"
        db.commit()
        st = run(db, FakeClient({}), [])
        assert st["retracted"] == 1
        db.refresh(f)
        assert (f.parse_status, f.parse_error) == ("skipped", "withdrawn")
        assert db.query(XbrlFinancial).filter_by(filing_id=f.id).count() == 0
        assert db.query(XbrlFact).filter_by(filing_id=f.id).count() == 0
        assert run(db, FakeClient({}), [])["retracted"] == 0
    finally:
        db.close()


def test_not_found_is_stored_failed_and_not_retried() -> None:
    """type=1 が 404 の書類は failed で残し、次の回は取らない。errors に数えない（KAB-388）。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([_doc(1, "S100GY6A", doc_type_code="130",
                         doc_description="訂正有価証券報告書－第59期(2018/04/01－2019/03/31)"),
                    _doc(2, "S100TEMP")])
        db.commit()
        client = FakeClient({}, gone=("S100GY6A",))
        st = run(db, client, pending_documents(db))
        assert (st["missing"], st["errors"], st["failed"]) == (1, 1, 0)
        f = db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100GY6A").one()
        assert (f.code, f.parse_status, f.doc_type, f.is_amendment) == ("72030", "failed", "annual", True)
        assert "404" in f.parse_error
        assert db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100TEMP").count() == 0
        assert [d.doc_id for d in pending_documents(db)] == ["S100TEMP"]
        retry = pending_documents(db, retry_failed=True)
        assert [d.doc_id for d in retry] == ["S100GY6A", "S100TEMP"]
        st = run(db, FakeClient({"S100TEMP": _zip()}, gone=("S100GY6A",)), retry)
        assert (st["parsed"], st["missing"], st["errors"]) == (1, 1, 0)
        assert db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100GY6A").count() == 1
    finally:
        db.close()


def test_job_exit_zero_on_not_found(monkeypatch, capsys) -> None:
    """404 だけなら cron は exit=0。404 以外の失敗は exit=1 のまま（KAB-388）。"""
    import importlib

    import app.edinet_wrapper as wrapper

    job = importlib.import_module("jobs.edinet.edinet_xbrl")
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100GY6A"))
        db.commit()
    finally:
        db.close()

    class Client(FakeClient):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setenv("EDINET_API_KEY", "dummy")
    monkeypatch.setattr(sys, "argv", ["edinet_xbrl.py", "--limit", "300"])
    monkeypatch.setattr(wrapper, "EdinetClient", lambda: Client({}, gone=("S100GY6A",)))
    assert job.main() == 0
    assert "missing=1 errors=0" in capsys.readouterr().out
    calls: list[Client] = []
    monkeypatch.setattr(wrapper, "EdinetClient", lambda: calls.append(Client({})) or calls[-1])
    assert job.main() == 0
    assert calls[-1].calls == []

    db = SessionLocal()
    try:
        db.add(_doc(2, "S100TEMP"))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(wrapper, "EdinetClient", lambda: Client({}))
    assert job.main() == 1

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, XbrlFiling
from app.xbrl.pdf import SyncOutcome, sync_pdf_filing
from tests.xbrl.test_tdnet_pdf_kab212_220 import _cell, _pages, _seed, _stock, _text
from tools.tdnet_pdf import ingest

HTML = _pages([_text(40, 40, "連結貸借対照表", 120), _cell(200, 80, "1,000", 40)])


def _statement(no: str, code: str) -> SimpleNamespace:
    return SimpleNamespace(
        disclosure_no=no, code=code, disclosed_date=date(2024, 5, 9), disclosed_time="15:00",
        type_of_period="FY", type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
        fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
    )


def _filings(db, no: str) -> int:
    return db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).count()


def test_filing_is_kept_once_the_code_leaves_stocks() -> None:
    no = "20240509526001"
    db = SessionLocal()
    try:
        _seed(db, no, HTML, _statement(no, "52610"))
        db.commit()
        assert sync_pdf_filing(db, no) is not SyncOutcome.ABSENT
        assert _filings(db, no) == 1
        db.query(Stock).filter_by(code="52610").delete()
        db.commit()
        first = db.query(XbrlFiling.id).filter_by(source="tdnet_pdf", doc_id=no).scalar()
        assert sync_pdf_filing(db, no) is SyncOutcome.UNCHANGED
        assert sync_pdf_filing(db, no, reparse=True) is not SyncOutcome.ABSENT
        assert db.query(XbrlFiling.id).filter_by(source="tdnet_pdf", doc_id=no).scalar() == first
    finally:
        db.close()


def test_no_filing_is_made_for_codes_not_in_stocks() -> None:
    no = "20240509526004"
    db = SessionLocal()
    try:
        _seed(db, no, HTML, _statement(no, "52640"))
        db.query(Stock).filter_by(code="52640").delete()
        db.commit()
        assert sync_pdf_filing(db, no) is SyncOutcome.ABSENT
        assert _filings(db, no) == 0
    finally:
        db.close()


def test_targets_skip_codes_not_in_stocks() -> None:
    db = SessionLocal()
    try:
        _stock(db, "52620")
        for no, code in (("20240509526002", "52620"), ("20240509526003", "52630")):
            db.add(Statement(disclosure_no=no, code=code, disclosed_date=date(2024, 5, 9),
                             type_of_document="FYFinancialStatements_Consolidated_JP"))
        db.commit()
        assert [s.disclosure_no for s in ingest.targets(db, None)] == ["20240509526002"]
        assert ingest.targets(db, ["52630"]) == []
    finally:
        db.close()

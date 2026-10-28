"""KAB-247: 業績予想の修正の連結/単体。IR BANK も本番 DB も使わない。

EarnForecastRevision の書類種別には Consolidated / NonConsolidated が無い。見出しに
「個別」「単体」も「連結」も無いときの既定は、同じ銘柄の通常の短信のうち日付が近い 1 件
（会計基準の引き継ぎと同じ）。近い短信が非連結なら単体。連結なら連結のまま。
見出しに「個別」だけあれば単体、「連結」だけあれば連結（従来どおり）。
"""
from __future__ import annotations

import gzip
import hashlib
import html
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlContext, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import (
    SyncOutcome,
    inherit_consolidated_from_statements,
    sync_pdf_filing,
)

MILLION = Decimal(10) ** 6


def _fail(msg: object) -> None:
    raise AssertionError(msg)


def _dt(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


def _text(x: float, y: float, text: str, width: float) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 50) -> str:
    return _text(x1 - width, y, text, width)


def _pages(*pages: list[str]) -> str:
    svgs = "".join(f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>' for parts in pages)
    return f"<html><body>{svgs}</body></html>"


def _revision_html(heading: str) -> str:
    """7865 20240522503651 型。表の見出しに連結/個別が無い。"""
    return _pages([
        _text(40, 40, heading, 420),
        _cell(300, 56, "売上高", 40),
        _cell(300, 68, "百万円", 30),
        _text(40, 82, "前回発表予想（A）", 90),
        _cell(300, 82, "336", 50),
        _text(40, 96, "今回発表予想（B）", 90),
        _cell(300, 96, "380", 50),
    ])


def _stock(db, code: str) -> None:
    if db.get(Stock, code) is None:
        db.add(Stock(
            code=code, name=code, sector17="x", sector33="x",
            market="プライム", scale_category="", margin_type="", product_category="011",
        ))


def _earnings(code: str, no: str, disclosed: date, *, consolidated: bool) -> Statement:
    mid = "Consolidated" if consolidated else "NonConsolidated"
    return Statement(
        disclosure_no=no, code=code, disclosed_date=disclosed, disclosed_time="15:00",
        type_of_period="FY", type_of_document=f"FYFinancialStatements_{mid}_JP",
        period_start=date(disclosed.year - 1, 1, 21), period_end=date(disclosed.year, 1, 20),
        fiscal_year_start=date(disclosed.year - 1, 1, 21), fiscal_year_end=date(disclosed.year, 1, 20),
    )


def _revision_statement(code: str, no: str, disclosed: date) -> SimpleNamespace:
    return SimpleNamespace(
        disclosure_no=no, code=code, disclosed_date=disclosed, disclosed_time="17:00",
        type_of_period="1Q", type_of_document="EarnForecastRevision",
        period_start=date(disclosed.year, 1, 21), period_end=date(disclosed.year, 4, 20),
        fiscal_year_start=date(disclosed.year, 1, 21), fiscal_year_end=date(disclosed.year + 1, 1, 20),
    )


def _seed(db, no: str, html_text: str, statement: SimpleNamespace) -> None:
    _stock(db, statement.code)
    db.merge(Statement(
        disclosure_no=no, code=statement.code, disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time, type_of_period=statement.type_of_period,
        type_of_document=statement.type_of_document, period_start=statement.period_start,
        period_end=statement.period_end, fiscal_year_start=statement.fiscal_year_start,
        fiscal_year_end=statement.fiscal_year_end,
    ))
    raw = html_text.encode("utf-8")
    db.merge(TdnetPdfDocument(
        disclosure_no=no, code=statement.code, disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time, title=statement.type_of_document,
        status="ok", html_gz=gzip.compress(raw), bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), created_at=_dt(2024, 8, 1, 15, 0),
    ))
    db.commit()


def _cons(db, no: str) -> set[int]:
    filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
    ctx = {row.is_consolidated for row in db.query(XbrlContext).filter_by(filing_id=filing.id)}
    fin = {int(row.is_consolidated) for row in db.query(XbrlFinancial).filter_by(filing_id=filing.id)}
    return ctx | fin


def _sales(db, no: str) -> Decimal:
    filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
    item = db.query(XbrlStdItem).filter_by(code="net_sales").one()
    row = (
        db.query(XbrlFinancial)
        .filter_by(filing_id=filing.id, std_item_id=item.id, value_kind="forecast", range_side="point")
        .one()
    )
    return Decimal(str(row.value))


def test_inherit_consolidated_uses_nearest_earnings() -> None:
    """日付が近い 1 件。遠い非連結より近い連結が勝つ（連結会社を単体にしない）。"""
    db = SessionLocal()
    try:
        _stock(db, "28090")
        db.merge(_earnings("28090", "20210101500001", date(2021, 1, 11), consolidated=False))
        db.merge(_earnings("28090", "20220111500002", date(2022, 1, 11), consolidated=True))
        db.commit()
        got = inherit_consolidated_from_statements(db, code="28090", disclosed_date=date(2021, 12, 27))
        if got is not True:
            _fail(got)
        none = inherit_consolidated_from_statements(db, code="99990", disclosed_date=date(2021, 12, 27))
        if none is not None:
            _fail(none)
    finally:
        db.close()


def test_kab247_nonconsolidated_earnings_make_silent_revision_standalone() -> None:
    """7865 20240522503651: 見出しに個別が無く、近い短信が非連結なら単体。"""
    no = "20240522503651"
    db = SessionLocal()
    try:
        db.merge(_earnings("78650", "20240229545849", date(2024, 3, 1), consolidated=False))
        _seed(db, no, _revision_html("1．2025年1月期 第１四半期累計期間業績予想の修正等（2024年1月21日～2024年4月20日）"),
              _revision_statement("78650", no, date(2024, 5, 27)))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        if _cons(db, no) != {0}:
            _fail(_cons(db, no))
        if _sales(db, no) != MILLION * 380:
            _fail(_sales(db, no))
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        refs = [row.context_ref for row in db.query(XbrlContext).filter_by(filing_id=filing.id)]
        if not refs or any(not ref.endswith("_NonConsolidated") for ref in refs):
            _fail(refs)
    finally:
        db.close()


def test_kab247_consolidated_earnings_keep_silent_revision_consolidated() -> None:
    """近い短信が連結なら、見出しに連結が無くても連結のまま。"""
    no = "20211210450418"
    db = SessionLocal()
    try:
        db.merge(_earnings("28090", "20220111500002", date(2022, 1, 11), consolidated=True))
        _seed(db, no, _revision_html("１．業績予想の修正（2021年1月21日～2021年4月20日）"),
              _revision_statement("28090", no, date(2021, 12, 27)))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        if _cons(db, no) != {1}:
            _fail(_cons(db, no))
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        refs = [row.context_ref for row in db.query(XbrlContext).filter_by(filing_id=filing.id)]
        if any("_NonConsolidated" in ref for ref in refs):
            _fail(refs)
    finally:
        db.close()


def test_kab247_heading_kobetsu_wins_over_consolidated_earnings() -> None:
    """見出しに「個別」があれば、近い短信が連結でも単体。"""
    no = "20231120592700"
    db = SessionLocal()
    try:
        db.merge(_earnings("78650", "20231001500001", date(2023, 10, 1), consolidated=True))
        heading = "第３四半期累計期間（累計）個別業績予想数値の修正（2023年1月21日～2023年10月20日）"
        _seed(db, no, _revision_html(heading), _revision_statement("78650", no, date(2023, 11, 28)))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        if _cons(db, no) != {0}:
            _fail(_cons(db, no))
    finally:
        db.close()


def test_kab247_heading_renketsu_wins_over_nonconsolidated_earnings() -> None:
    """見出しに「連結」があれば、近い短信が非連結でも連結。"""
    no = "20250601512347"
    db = SessionLocal()
    try:
        db.merge(_earnings("78650", "20250301500001", date(2025, 3, 1), consolidated=False))
        _seed(db, no, _revision_html("連結業績予想の修正（2025年1月21日～2025年4月20日）"),
              _revision_statement("78650", no, date(2025, 6, 1)))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        if _cons(db, no) != {1}:
            _fail(_cons(db, no))
    finally:
        db.close()


def test_kab247_no_nearby_earnings_stays_consolidated() -> None:
    """通常の短信が無い修正は、従来どおり連結。"""
    no = "20250601512348"
    db = SessionLocal()
    try:
        _seed(db, no, _revision_html("１．業績予想の修正（2025年1月21日～2025年4月20日）"),
              _revision_statement("99990", no, date(2025, 6, 1)))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        if _cons(db, no) != {1}:
            _fail(_cons(db, no))
    finally:
        db.close()

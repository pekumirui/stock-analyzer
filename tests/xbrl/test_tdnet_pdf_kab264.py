"""KAB-264: IFRS 本表 PL の純利益（「…の帰属」見出し / 「親会社の所有者に帰属する当期純利益」）。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import gzip
import hashlib
import html
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import PdfDisclosure, SyncOutcome, read_tdnet_pdf, sync_pdf_filing

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
    svgs = "".join(
        f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>' for parts in pages
    )
    return f"<html><body>{svgs}</body></html>"


def _disclosure(**kwargs) -> PdfDisclosure:
    base = {
        "disclosure_no": "20241030506170",
        "code": "81330",
        "disclosed_date": date(2024, 10, 30),
        "disclosed_time": "15:00",
        "type_of_period": "2Q",
        "type_of_document": "2QFinancialStatements_Consolidated_IFRS",
        "period_start": date(2024, 4, 1),
        "period_end": date(2024, 9, 30),
        "fiscal_year_start": date(2024, 4, 1),
        "fiscal_year_end": date(2025, 3, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _stock(db, code: str) -> None:
    if db.get(Stock, code) is None:
        db.add(Stock(
            code=code, name=code, sector17="x", sector33="x",
            market="プライム", scale_category="", margin_type="", product_category="011",
        ))


def _statement(**kwargs) -> SimpleNamespace:
    base = {
        "disclosure_no": "20241030506170",
        "code": "81330",
        "disclosed_date": date(2024, 10, 30),
        "disclosed_time": "15:00",
        "type_of_period": "2Q",
        "type_of_document": "2QFinancialStatements_Consolidated_IFRS",
        "period_start": date(2024, 4, 1),
        "period_end": date(2024, 9, 30),
        "fiscal_year_start": date(2024, 4, 1),
        "fiscal_year_end": date(2025, 3, 31),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _seed(db, no: str, html_text: str, statement: SimpleNamespace) -> None:
    _stock(db, statement.code)
    db.merge(Statement(
        disclosure_no=no,
        code=statement.code,
        disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time,
        type_of_period=statement.type_of_period,
        type_of_document=statement.type_of_document,
        period_start=statement.period_start,
        period_end=statement.period_end,
        fiscal_year_start=statement.fiscal_year_start,
        fiscal_year_end=statement.fiscal_year_end,
    ))
    raw = html_text.encode("utf-8")
    db.merge(TdnetPdfDocument(
        disclosure_no=no,
        code=statement.code,
        disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time,
        title=statement.type_of_document,
        status="ok",
        html_gz=gzip.compress(raw),
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        created_at=_dt(2024, 10, 30, 15, 0),
    ))
    db.commit()


def _rows(db, no: str) -> dict[tuple, Decimal]:
    filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one_or_none()
    if filing is None:
        return {}
    found = {}
    query = (
        db.query(XbrlFinancial, XbrlStdItem)
        .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
        .filter(XbrlFinancial.filing_id == filing.id)
    )
    for row, item in query:
        found[(item.code, row.value_kind, row.range_side, row.target_period)] = Decimal(str(row.value))
    return found


def test_kab264_attr_heading_does_not_glue_to_owners_np() -> None:
    """81330 20241030506170: 「中間純利益の帰属」を次の行名に付けず、7,837 を採る。"""
    parts = [
        _text(40, 40, "（２）要約中間連結包括利益計算書", 280),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前中間連結会計期間", 120),
        _cell(560, 76, "当中間連結会計期間", 120),
        _text(40, 112, "売上収益", 60),
        _cell(400, 112, "100,000", 50),
        _cell(560, 112, "110,000", 50),
        _text(40, 130, "中間純利益", 80),
        _cell(400, 130, "10,418", 50),
        _cell(560, 130, "9,239", 50),
        _text(40, 148, "中間純利益の帰属", 120),
        _text(50, 166, "当社株主に帰属する中間純利益", 200),
        _cell(400, 166, "8,917", 50),
        _cell(560, 166, "7,837", 50),
        _text(50, 184, "非支配持分に帰属する中間純利益", 210),
        _cell(400, 184, "1,501", 50),
        _cell(560, 184, "1,402", 50),
        _text(40, 202, "中間包括利益の帰属", 140),
        _text(50, 220, "当社株主に帰属する中間包括利益", 210),
        _cell(400, 220, "9,000", 50),
        _cell(560, 220, "8,000", 50),
    ]
    no = "20241030506170"
    html_text = _pages(parts)
    facts = _facts(read_tdnet_pdf(html_text, _disclosure()))
    ctx = "CurrentAccumulatedQ2Duration"
    if facts.get(("当社株主に帰属する当期純利益", ctx)) != MILLION * 7837:
        _fail(facts)
    if facts.get(("当期純利益", ctx)) != MILLION * 9239:
        _fail(facts)
    if any("の帰属当社" in name or "の帰属親会社" in name for name, _ref in facts):
        _fail(facts)
    if facts.get(("当社株主に帰属する中間包括利益", ctx)) != MILLION * 8000:
        _fail(facts)
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement())
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        rows = _rows(db, no)
        if rows.get(("net_income", "actual", "point", "Q2")) != MILLION * 7837:
            _fail(rows)
    finally:
        db.close()


def test_kab264_owners_only_row_still_concatenates() -> None:
    """KAB-240: 次が「親会社の所有者」だけのときは見出しを残して連結する。"""
    parts = [
        _text(40, 40, "要約四半期連結損益計算書", 200),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前第1四半期", 80),
        _cell(560, 76, "当第1四半期", 80),
        _text(40, 112, "四半期利益又は損失(△)", 180),
        _cell(400, 112, "81,115", 50),
        _cell(560, 112, "△3,912", 50),
        _text(40, 130, "四半期利益又は損失(△)の帰属", 220),
        _text(50, 148, "親会社の所有者", 100),
        _cell(400, 148, "80,058", 50),
        _cell(560, 148, "△4,388", 50),
        _text(50, 166, "非支配持分", 70),
        _cell(400, 166, "1,057", 50),
        _cell(560, 166, "476", 40),
    ]
    d = _disclosure(
        disclosure_no="20220428530699",
        code="64740",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS",
        period_start=date(2022, 1, 1), period_end=date(2022, 3, 31),
        fiscal_year_start=date(2022, 1, 1), fiscal_year_end=date(2022, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    ctx = "CurrentAccumulatedQ1Duration"
    if facts.get(("親会社の所有者に帰属する当期利益", ctx)) != MILLION * Decimal("-4388"):
        _fail(facts)


def test_kab264_owners_net_income_with_jun_maps() -> None:
    """52010 20230512568007: 「親会社の所有者に帰属する当期純利益」22,072 を採る。"""
    parts = [
        _text(40, 40, "（2）要約四半期連結純損益計算書", 260),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前第1四半期", 80),
        _cell(560, 76, "当第1四半期", 80),
        _text(40, 112, "売上収益", 60),
        _cell(400, 112, "50,000", 50),
        _cell(560, 112, "55,000", 50),
        _text(40, 130, "当期純利益", 80),
        _cell(400, 130, "25,000", 50),
        _cell(560, 130, "28,144", 50),
        _text(40, 148, "親会社の所有者に帰属する当期純利益", 240),
        _cell(400, 148, "20,000", 50),
        _cell(560, 148, "22,072", 50),
        _text(40, 166, "非支配持分に帰属する当期純利益", 210),
        _cell(400, 166, "5,000", 50),
        _cell(560, 166, "6,072", 50),
    ]
    no = "20230512568007"
    html_text = _pages(parts)
    d = _disclosure(
        disclosure_no=no,
        code="52010",
        disclosed_date=date(2023, 5, 12),
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS",
        period_start=date(2023, 1, 1), period_end=date(2023, 3, 31),
        fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(html_text, d))
    ctx = "CurrentAccumulatedQ1Duration"
    if facts.get(("親会社の所有者に帰属する当期純利益", ctx)) != MILLION * 22072:
        _fail(facts)
    if facts.get(("当期純利益", ctx)) != MILLION * 28144:
        _fail(facts)
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement(
            disclosure_no=no, code="52010",
            disclosed_date=date(2023, 5, 12),
            type_of_period="1Q",
            type_of_document="1QFinancialStatements_Consolidated_IFRS",
            period_start=date(2023, 1, 1), period_end=date(2023, 3, 31),
            fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        rows = _rows(db, no)
        if rows.get(("net_income", "actual", "point", "Q1")) != MILLION * 22072:
            _fail(rows)
    finally:
        db.close()

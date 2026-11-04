"""KAB-259〜263: 短信 PDF の本表 PL 見出し・行名折り返し・売上収益・textless・cash_end。

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
from app.xbrl.pdf import (
    PdfDisclosure,
    SyncOutcome,
    format_garbled_pages,
    format_textless_pages,
    garbled_page_indexes,
    label_key,
    outlined_page,
    read_pages,
    read_tdnet_pdf,
    sync_pdf_filing,
    sync_pdf_filings,
    textless_page_indexes,
)

MILLION = Decimal(10) ** 6
_OUTLINE_PATHS = 100


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


def _paths(n: int = _OUTLINE_PATHS) -> list[str]:
    return [f'<path d="M{i} {i} L{i + 1} {i + 1}"/>' for i in range(n)]


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


def _pl_cols() -> list[str]:
    return [
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前中間連結会計期間", 120),
        _cell(560, 76, "当中間連結会計期間", 120),
    ]


def test_kab259_combined_ci_is_pl() -> None:
    """81330 20241030506170: 「要約中間連結包括利益計算書」を PL として読む。"""
    parts = [
        _text(40, 40, "（２）要約中間連結包括利益計算書", 280),
        *_pl_cols(),
        _text(40, 112, "売上収益", 60),
        _cell(400, 112, "100,000", 50),
        _cell(560, 112, "110,000", 50),
        _text(40, 130, "営業利益", 60),
        _cell(400, 130, "10,000", 50),
        _cell(560, 130, "11,000", 50),
        _text(40, 148, "税引前利益", 70),
        _cell(400, 148, "9,000", 50),
        _cell(560, 148, "10,000", 50),
        _text(40, 166, "親会社の所有者に帰属する当期利益", 220),
        _cell(400, 166, "6,000", 50),
        _cell(560, 166, "7,000", 50),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure()))
    ctx = "CurrentAccumulatedQ2Duration"
    if facts.get(("売上収益", ctx)) != MILLION * 110000:
        _fail(facts)
    if facts.get(("営業利益", ctx)) != MILLION * 11000:
        _fail(facts)
    if facts.get(("税引前利益", ctx)) != MILLION * 10000:
        _fail(facts)
    if facts.get(("親会社の所有者に帰属する当期利益", ctx)) != MILLION * 7000:
        _fail(facts)


def test_kab259_net_income_statement_is_pl() -> None:
    """52010 20230512568007: 「要約四半期連結純損益計算書」を PL として読む。"""
    parts = [
        _text(40, 40, "（2）要約四半期連結純損益計算書", 260),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前第1四半期", 80),
        _cell(560, 76, "当第1四半期", 80),
        _text(40, 112, "売上収益", 60),
        _cell(400, 112, "50,000", 50),
        _cell(560, 112, "55,000", 50),
        _text(40, 130, "営業利益", 60),
        _cell(400, 130, "4,000", 50),
        _cell(560, 130, "5,000", 50),
    ]
    d = _disclosure(
        disclosure_no="20230512568007",
        code="52010",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS",
        period_start=date(2023, 1, 1), period_end=date(2023, 3, 31),
        fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    ctx = "CurrentAccumulatedQ1Duration"
    if facts.get(("売上収益", ctx)) != MILLION * 55000:
        _fail(facts)
    if facts.get(("営業利益", ctx)) != MILLION * 5000:
        _fail(facts)


def test_kab259_separate_ci_is_not_pl_even_if_first() -> None:
    """損益計算書がある書類では、先に出る包括利益計算書を PL にしない。"""
    ci = [
        _text(40, 40, "連結包括利益計算書", 140),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前期", 40),
        _cell(560, 76, "当期", 40),
        _text(40, 112, "売上高", 50),
        _cell(400, 112, "9,000", 40),
        _cell(560, 112, "9,900", 40),
    ]
    pl = [
        _text(40, 40, "連結損益計算書", 120),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前期", 40),
        _cell(560, 76, "当期", 40),
        _text(40, 112, "売上高", 50),
        _cell(400, 112, "1,000", 40),
        _cell(560, 112, "1,100", 40),
    ]
    d = _disclosure(
        type_of_document="2QFinancialStatements_Consolidated_JP",
        type_of_period="2Q",
    )
    facts = _facts(read_tdnet_pdf(_pages(ci, pl), d))
    if facts.get(("売上高", "CurrentAccumulatedQ2Duration")) != MILLION * 1100:
        _fail(facts)


def test_kab260_wrapped_parent_np_beats_total_np() -> None:
    """78440 20250508534523: 折り返した親会社株主に帰属する純利益 818 を採る。"""
    if label_key("（内訳）親会社株主に帰属する当期純利益") != "親会社株主に帰属する当期純利益":
        _fail(label_key("（内訳）親会社株主に帰属する当期純利益"))
    parts = [
        _text(40, 40, "連結損益計算書", 120),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 120),
        _cell(560, 76, "当連結会計年度", 120),
        _text(40, 112, "当期純利益又は当期純損失（△）", 220),
        _cell(400, 112, "△515", 40),
        _cell(560, 112, "820", 40),
        _text(40, 130, "（内訳）", 50),
        _text(40, 148, "親会社株主に帰属する当期純利益又は親会社株主に", 300),
        _cell(400, 148, "△517", 40),
        _cell(560, 148, "818", 40),
        _text(40, 166, "帰属する当期純損失（△）", 180),
    ]
    no = "20250508534523"
    html_text = _pages(parts)
    facts = _facts(read_tdnet_pdf(html_text, _disclosure(
        disclosure_no=no, code="78440", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
        fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
    )))
    if facts.get(("親会社株主に帰属する当期純利益", "CurrentYearDuration")) != MILLION * 818:
        _fail(facts)
    if any("内訳" in name for name, _ref in facts):
        _fail(facts)
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement(
            disclosure_no=no, code="78440", type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            disclosed_date=date(2025, 5, 8),
            period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
            fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        rows = _rows(db, no)
        if rows.get(("net_income", "actual", "point", "FY")) != MILLION * 818:
            _fail(rows)
    finally:
        db.close()


def test_kab261_sales_revenue_beats_total_revenue() -> None:
    """61410 20230510563374: 「売上収益」を採り、「収益合計」は採らない。"""
    parts = [
        _text(40, 40, "要約四半期連結損益計算書", 200),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前第1四半期", 80),
        _cell(560, 76, "当第1四半期", 80),
        _text(40, 112, "収益", 30),
        _text(50, 130, "売上収益", 60),
        _cell(400, 130, "107,262", 50),
        _cell(560, 130, "123,225", 50),
        _text(50, 148, "その他の収益", 80),
        _cell(400, 148, "2,072", 40),
        _cell(560, 148, "2,332", 40),
        _text(40, 166, "収益合計", 60),
        _cell(400, 166, "109,334", 50),
        _cell(560, 166, "125,557", 50),
    ]
    no = "20230510563374"
    html_text = _pages(parts)
    d = _disclosure(
        disclosure_no=no, code="61410", type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS",
        period_start=date(2023, 1, 1), period_end=date(2023, 3, 31),
        fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(html_text, d))
    ctx = "CurrentAccumulatedQ1Duration"
    if facts.get(("売上収益", ctx)) != MILLION * 123225:
        _fail(facts)
    if any(name.startswith("収益売上") for name, _ref in facts):
        _fail(facts)
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement(
            disclosure_no=no, code="61410", type_of_period="1Q",
            type_of_document="1QFinancialStatements_Consolidated_IFRS",
            disclosed_date=date(2023, 5, 10),
            period_start=date(2023, 1, 1), period_end=date(2023, 3, 31),
            fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        rows = _rows(db, no)
        if rows.get(("net_sales", "actual", "point", "Q1")) != MILLION * 123225:
            _fail(rows)
    finally:
        db.close()


def test_kab261_revenue_only_stays_net_sales() -> None:
    """「収益」だけの本表（三菱商事型）は今までどおり売上高にする。"""
    parts = [
        _text(40, 40, "要約四半期連結損益計算書", 200),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前第1四半期", 80),
        _cell(560, 76, "当第1四半期", 80),
        _text(40, 112, "収益", 30),
        _cell(400, 112, "4,000,000", 60),
        _cell(560, 112, "4,500,000", 60),
    ]
    d = _disclosure(
        disclosure_no="20230804534066", code="80020", type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS",
        period_start=date(2023, 4, 1), period_end=date(2023, 6, 30),
        fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    if facts.get(("収益", "CurrentAccumulatedQ1Duration")) != MILLION * 4500000:
        _fail(facts)


def _header(page_no: str, code: str = "3358") -> list[str]:
    return [
        _text(40, 40, f"会社（{code}）2025年3月期第2四半期決算短信", 280),
        _text(280, 800, f"- {page_no} -", 20),
    ]


def test_kab262_textless_pages_are_marked() -> None:
    """33580: 2 ページ目以降の文字無しを textless とし、サマリーの売上は残す。"""
    summary = _header("1") + [
        _text(40, 80, "（1）経営成績", 80),
        _text(40, 98, "（単位：百万円）", 100),
        _cell(400, 116, "売上高", 50),
        _text(40, 140, "当第2四半期", 100),
        _cell(400, 140, "1,234", 40),
    ]
    html_text = _pages(summary, _header("2"), _header("3"), _header("4"))
    pages = read_pages(html_text)
    garbled = garbled_page_indexes(pages)
    if garbled:
        _fail(garbled)
    indexes = textless_page_indexes(pages, exclude=garbled)
    if indexes != [1, 2, 3]:
        _fail(indexes)
    if format_textless_pages(indexes) != "textless pages=2-4":
        _fail(format_textless_pages(indexes))
    no = "20241008595029"
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement(
            disclosure_no=no, code="33580",
            type_of_document="2QFinancialStatements_NonConsolidated_JP",
        ))
        apply_mappings(db)
        counts = sync_pdf_filings(db, codes=["33580"], limit=10)
        if counts[SyncOutcome.PARSED] != 1:
            _fail(counts)
        if counts["textless"] != 1:
            _fail(counts)
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        if filing.parse_status != "parsed" or filing.parse_error != "textless pages=2-4":
            _fail((filing.parse_status, filing.parse_error))
        rows = _rows(db, no)
        if rows.get(("net_sales", "actual", "point", "Q2")) != MILLION * 1234:
            _fail(rows)
    finally:
        db.close()


def test_kab262_first_page_empty_is_not_textless() -> None:
    """1 ページ目だけの空は印を付けない（2 ページ目以降が対象）。"""
    html_text = _pages(
        [],
        _header("2") + [
            _text(40, 80, "（1）経営成績", 80),
            _text(40, 98, "（単位：百万円）", 100),
            _cell(400, 116, "売上高", 50),
            _text(40, 140, "当第2四半期", 100),
            _cell(400, 140, "100", 30),
        ],
    )
    pages = read_pages(html_text)
    if textless_page_indexes(pages):
        _fail(textless_page_indexes(pages))


def test_kab262_outlined_stays_garbled_not_textless() -> None:
    """アウトライン化（KAB-227）は garbled のまま。textless と二重にしない。"""
    html_text = _pages(
        [*_header("1"), *_paths()],
        [*_header("2"), *_paths()],
        _header("3") + [
            _text(40, 80, "損益計算書", 80),
            _text(40, 98, "（単位：百万円）", 100),
            _cell(400, 116, "当第2四半期累計期間", 120),
            _text(40, 140, "売上高", 50),
            _cell(400, 140, "2,355", 50),
        ],
    )
    pages = read_pages(html_text)
    if not outlined_page(pages[0]) or not outlined_page(pages[1]):
        _fail([outlined_page(p) for p in pages])
    garbled = garbled_page_indexes(pages)
    if garbled != [0, 1]:
        _fail(garbled)
    if format_garbled_pages(garbled) != "garbled pages=1-2":
        _fail(format_garbled_pages(garbled))
    if textless_page_indexes(pages, exclude=garbled):
        _fail(textless_page_indexes(pages, exclude=garbled))


def test_kab263_cash_end_is_instant() -> None:
    """75510: CF の期末残高・期首残高は時点。営業 CF は期間のまま。"""
    parts = [
        _text(40, 40, "連結キャッシュ・フロー計算書", 180),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 120),
        _cell(560, 76, "当連結会計年度", 120),
        _text(40, 112, "営業活動によるキャッシュ・フロー", 220),
        _cell(400, 112, "1,000", 40),
        _cell(560, 112, "1,200", 40),
        _text(40, 148, "現金及び現金同等物の期首残高", 180),
        _cell(400, 148, "8,000", 40),
        _cell(560, 148, "8,200", 40),
        _text(40, 184, "現金及び現金同等物の期末残高", 180),
        _cell(400, 184, "8,200", 40),
        _cell(560, 184, "8,516", 40),
    ]
    no = "20260512526693"
    html_text = _pages(parts)
    pkg = read_tdnet_pdf(html_text, _disclosure(
        disclosure_no=no, code="75510", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
        fiscal_year_start=date(2025, 4, 1), fiscal_year_end=date(2026, 3, 31),
    ))
    facts = _facts(pkg)
    if facts.get(("現金及び現金同等物の期末残高", "CurrentYearInstant")) != MILLION * 8516:
        _fail(facts)
    if facts.get(("現金及び現金同等物の期首残高", "CurrentYearInstant")) != MILLION * 8200:
        _fail(facts)
    if facts.get(("営業活動によるキャッシュ・フロー", "CurrentYearDuration")) != MILLION * 1200:
        _fail(facts)
    db = SessionLocal()
    try:
        _seed(db, no, html_text, _statement(
            disclosure_no=no, code="75510", type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            disclosed_date=date(2026, 5, 12),
            period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
            fiscal_year_start=date(2025, 4, 1), fiscal_year_end=date(2026, 3, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        cash = (
            db.query(XbrlFinancial)
            .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
            .filter(XbrlFinancial.filing_id == filing.id, XbrlStdItem.code == "cash_end")
            .one()
        )
        if cash.period_start is not None:
            _fail(cash.period_start)
        if cash.period_end != date(2026, 3, 31):
            _fail(cash.period_end)
        if Decimal(str(cash.value)) != MILLION * 8516:
            _fail(cash.value)
        cfo = (
            db.query(XbrlFinancial)
            .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
            .filter(XbrlFinancial.filing_id == filing.id, XbrlStdItem.code == "operating_cf")
            .one()
        )
        if cfo.period_start != date(2025, 4, 1):
            _fail(cfo.period_start)
    finally:
        db.close()

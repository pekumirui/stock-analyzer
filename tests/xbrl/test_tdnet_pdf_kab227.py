"""KAB-227: アウトライン化されたページを garbled と判定する。

文字化け（KAB-218）は今の扱いのまま（印を付け、取れた値は残す）。
テキストが無い・柱だけ残るページは、図形が多ければ同じ印を付ける。
IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import gzip
import hashlib
import html
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import (
    PdfDisclosure,
    PdfLayoutError,
    SyncOutcome,
    format_garbled_pages,
    garbled_page_indexes,
    outlined_page,
    read_pages,
    read_tdnet_pdf,
    sync_pdf_filing,
)

MILLION = Decimal(10) ** 6
GOLDEN_HTML = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "html"
# 7865 20210818487561: サマリー 0–1 ページがアウトライン。合成 HTML で再現する。
KAB227 = "20210818487561"
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


def _paths(n: int = _OUTLINE_PATHS) -> list[str]:
    return [f'<path d="M{i} {i} L{i + 1} {i + 1}"/>' for i in range(n)]


def _pages(*pages: list[str]) -> str:
    svgs = "".join(
        f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>' for parts in pages
    )
    return f"<html><body>{svgs}</body></html>"


def _header(page_no: str) -> list[str]:
    return [
        _text(40, 40, "ピープル㈱（7865）2022年1月期第2四半期決算短信", 280),
        _text(280, 800, page_no, 10),
    ]


def _pl_page() -> list[str]:
    return [
        *_header("3"),
        _text(40, 80, "損益計算書", 80),
        _text(40, 98, "（単位：百万円）", 100),
        _cell(400, 116, "前第2四半期累計期間", 120),
        _cell(560, 116, "当第2四半期累計期間", 120),
        _text(40, 140, "売上高", 50),
        _cell(400, 140, "2,800", 50),
        _cell(560, 140, "2,355", 50),
    ]


def _disclosure(**kwargs) -> PdfDisclosure:
    base = {
        "disclosure_no": KAB227,
        "code": "78650",
        "disclosed_date": date(2021, 9, 2),
        "disclosed_time": "15:00",
        "type_of_period": "2Q",
        "type_of_document": "2QFinancialStatements_NonConsolidated_JP",
        "period_start": date(2021, 1, 21),
        "period_end": date(2021, 7, 20),
        "fiscal_year_start": date(2021, 1, 21),
        "fiscal_year_end": date(2022, 1, 20),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _stock(db, code: str) -> None:
    if db.get(Stock, code) is None:
        db.add(Stock(
            code=code, name=code, sector17="x", sector33="x",
            market="プライム", scale_category="", margin_type="", product_category="011",
        ))


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
        created_at=_dt(2021, 9, 2, 15, 0),
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


def _statement(**kwargs) -> SimpleNamespace:
    base = {
        "disclosure_no": KAB227,
        "code": "78650",
        "disclosed_date": date(2021, 9, 2),
        "disclosed_time": "15:00",
        "type_of_period": "2Q",
        "type_of_document": "2QFinancialStatements_NonConsolidated_JP",
        "period_start": date(2021, 1, 21),
        "period_end": date(2021, 7, 20),
        "fiscal_year_start": date(2021, 1, 21),
        "fiscal_year_end": date(2022, 1, 20),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_kab227_outlined_summary_pages_are_garbled() -> None:
    """20210818487561: サマリー 0–1 が path だけ。garbled の印。本表の売上は残る。"""
    html_text = _pages(
        [*_header("1"), *_paths()],
        [*_header("2"), *_paths()],
        _pl_page(),
    )
    pages = read_pages(html_text)
    if [page.drawings for page in pages] != [_OUTLINE_PATHS, _OUTLINE_PATHS, 0]:
        _fail([page.drawings for page in pages])
    if not outlined_page(pages[0]) or not outlined_page(pages[1]) or outlined_page(pages[2]):
        _fail([(outlined_page(page), _page_preview(page)) for page in pages])
    indexes = garbled_page_indexes(pages)
    if indexes != [0, 1]:
        _fail(f"indexes {indexes}")
    if format_garbled_pages(indexes) != "garbled pages=1-2":
        _fail(format_garbled_pages(indexes))
    db = SessionLocal()
    try:
        _seed(db, KAB227, html_text, _statement())
        apply_mappings(db)
        if sync_pdf_filing(db, KAB227) is not SyncOutcome.PARSED:
            _fail("outlined summary should stay parsed")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=KAB227).one()
        if filing.parse_error != "garbled pages=1-2":
            _fail(filing.parse_error)
        rows = _rows(db, KAB227)
        if rows.get(("net_sales", "actual", "point", "Q2")) != MILLION * 2355:
            _fail(f"sales {rows}")
    finally:
        db.close()


def test_kab227_all_outlined_pages_stay_failed() -> None:
    """7865 2021–2022 / 20260310578954: 表が 1 つも無い。failed のまま。"""
    html_text = _pages(_paths(), _paths())
    pages = read_pages(html_text)
    if garbled_page_indexes(pages) != [0, 1]:
        _fail(garbled_page_indexes(pages))
    try:
        read_tdnet_pdf(html_text, _disclosure())
    except PdfLayoutError as exc:
        if str(exc) != "no in-scope table":
            _fail(exc)
    else:
        _fail("expected no in-scope table")
    db = SessionLocal()
    try:
        _seed(db, "20260310578954", html_text, _statement(
            disclosure_no="20260310578954",
            disclosed_date=date(2026, 3, 10),
            type_of_period="FY",
            type_of_document="FYFinancialStatements_NonConsolidated_JP",
            period_start=date(2025, 1, 21), period_end=date(2026, 1, 20),
            fiscal_year_start=date(2025, 1, 21), fiscal_year_end=date(2026, 1, 20),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, "20260310578954") is not SyncOutcome.FAILED:
            _fail("all-outline should fail")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id="20260310578954").one()
        if filing.parse_status != "failed" or filing.parse_error != "no in-scope table":
            _fail((filing.parse_status, filing.parse_error))
        if _rows(db, "20260310578954"):
            _fail(_rows(db, "20260310578954"))
    finally:
        db.close()


def test_kab227_blank_or_sparse_text_is_not_garbled() -> None:
    """図形の無い空ページ・字の少ない説明ページは garbled にしない（prev5 の 8 件を増やさない）。"""
    sparse = [_text(40, 200, "該当事項はありません。", 140)]
    html_text = _pages([], sparse, _pl_page())
    pages = read_pages(html_text)
    if any(page.drawings for page in pages):
        _fail([page.drawings for page in pages])
    if garbled_page_indexes(pages):
        _fail(garbled_page_indexes(pages))
    pkg = read_tdnet_pdf(html_text, _disclosure())
    sales = {
        fact.value_num for fact in pkg.facts
        if fact.local_name == "売上高" and "Current" in fact.context_ref
    }
    if MILLION * 2355 not in sales:
        _fail(pkg.facts)


def test_kab227_garbled_text_pages_unchanged() -> None:
    """KAB-218 の文字化けページは今までどおり garbled。アウトライン判定は足さない。"""
    garbled = "\u3e26\u3e27\u3e30" * 40
    html_text = _pages(
        [
            _text(40, 40, "（1）経営成績", 100),
            _text(40, 58, "（単位：百万円）", 100),
            _cell(400, 76, "売上高", 50),
            _text(40, 112, "当第2四半期", 100),
            _cell(400, 112, "1,234,000", 60),
        ],
        [_text(40, 40, garbled, 400), _text(40, 80, garbled, 400)],
    )
    pages = read_pages(html_text)
    if outlined_page(pages[1]):
        _fail(pages[1].drawings)
    if garbled_page_indexes(pages) != [1]:
        _fail(garbled_page_indexes(pages))
    if format_garbled_pages([1]) != "garbled pages=2":
        _fail(format_garbled_pages([1]))


def test_kab227_mini_html_is_not_outlined() -> None:
    """原因ごとの小さな組に、アウトライン判定の誤検出が無い。"""
    nos = sorted(path.name.split(".")[0] for path in GOLDEN_HTML.glob("*.html.gz"))
    if len(nos) < 25:
        _fail(f"mini set {len(nos)}")
    for no in nos:
        html_text = gzip.decompress((GOLDEN_HTML / f"{no}.html.gz").read_bytes()).decode("utf-8")
        pages = read_pages(html_text)
        outlined = [page.index for page in pages if outlined_page(page)]
        if outlined:
            _fail(f"{no} outlined {outlined}")


def _page_preview(page) -> tuple:
    text = "".join(line.text for line in page.lines)
    return page.index, page.drawings, len(text), text[:40]

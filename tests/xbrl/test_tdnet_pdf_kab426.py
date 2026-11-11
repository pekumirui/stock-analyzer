"""KAB-426: BS の「1年内返済予定の長期借入金」など、先頭が「1年内」「1年以内」の行名を読む。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6


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


def _disclosure() -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no="20260514534137",
        code="98800",
        disclosed_date=date(2026, 5, 14),
        disclosed_time="15:30",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        fiscal_year_start=date(2025, 4, 1),
        fiscal_year_end=date(2026, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _bs(rows: list[tuple[str, str, str]]) -> str:
    parts = [
        _text(40, 40, "（１）連結貸借対照表", 160),
        _text(480, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 100),
        _cell(560, 76, "当連結会計年度", 100),
        _text(40, 94, "流動負債", 60),
    ]
    for n, (label, prior, current) in enumerate(rows):
        y = 112 + 18 * n
        parts += [_text(50, y, label, 14 * len(label)), _cell(400, y, prior), _cell(560, y, current)]
    return _pages(parts)


def test_kab426_one_year_rows_are_read() -> None:
    """9880 20260514534137: 短期借入金と未払法人税等の間の「1年内返済予定の長期借入金 600」。"""
    page = _bs([
        ("短期借入金", "3,000", "3,123"),
        ("１年内返済予定の長期借入金", "400", "600"),
        ("１年内償還予定の社債", "100", "50"),
        ("１年以内に期限到来の固定負債", "70", "80"),
        ("未払法人税等", "1,800", "1,956"),
    ])
    facts = {(f.local_name, f.context_ref): f.value_num for f in read_tdnet_pdf(page, _disclosure()).facts}
    expected = {
        ("短期借入金", "CurrentYearInstant"): MILLION * 3123,
        ("1年内返済予定の長期借入金", "CurrentYearInstant"): MILLION * 600,
        ("1年内返済予定の長期借入金", "Prior1YearInstant"): MILLION * 400,
        ("1年内償還予定の社債", "CurrentYearInstant"): MILLION * 50,
        ("1年以内に期限到来の固定負債", "CurrentYearInstant"): MILLION * 80,
        ("未払法人税等", "CurrentYearInstant"): MILLION * 1956,
    }
    for key, value in expected.items():
        assert facts.get(key) == value, (key, facts)


def test_kab426_other_digits_in_label_still_dropped() -> None:
    """「1年内」以外の数字（注記番号）が残る行名は今までどおり入れない。"""
    page = _bs([
        ("短期借入金", "3,000", "3,123"),
        ("その他5,7", "10", "20"),
    ])
    names = {f.local_name for f in read_tdnet_pdf(page, _disclosure()).facts}
    assert "短期借入金" in names, names
    assert not any(name.startswith("その他") for name in names), names

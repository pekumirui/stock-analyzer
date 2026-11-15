"""KAB-464: 本表の後ろの「…明細書」の行を本表に入れない。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import html
from datetime import date
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf


def _text(x: float, y: float, text: str, width: float) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 50) -> str:
    return _text(x1 - width, y, text, width)


def _disclosure() -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no="20240426578794",
        code="92060",
        disclosed_date=date(2024, 4, 30),
        disclosed_time="15:00",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_NonConsolidated_JP",
        period_start=date(2023, 4, 1),
        period_end=date(2024, 3, 31),
        fiscal_year_start=date(2023, 4, 1),
        fiscal_year_end=date(2024, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _rows(y0: float, rows: list[tuple[str, str, str]]) -> list[str]:
    parts: list[str] = []
    for n, (label, prior, current) in enumerate(rows):
        y = y0 + 18 * n
        parts += [_text(50, y, label, 14 * len(label)), _cell(400, y, prior), _cell(560, y, current)]
    return parts


def _page(title: str) -> str:
    statement = [
        ("営業収入合計", "32,275", "40,019"),
        ("事業費合計", "31,055", "36,766"),
        ("営業総利益", "1,220", "3,253"),
        ("当期純利益", "73", "912"),
    ]
    detail = [
        ("燃油費及び燃料税", "8,678", "10,850"),
        ("空港使用料", "1,969", "2,954"),
        ("事業費計", "31,055", "36,766"),
    ]
    parts = [
        _text(40, 40, "（２）損益計算書", 120),
        _text(480, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前事業年度", 80),
        _cell(560, 76, "当事業年度", 80),
        *_rows(94, statement),
        _text(250, 190, title, 14 * len(title)),
        _cell(400, 208, "前事業年度", 80),
        _cell(560, 208, "当事業年度", 80),
        *_rows(226, detail),
    ]
    return f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg></body></html>'


def _pl_names(title: str) -> list[str]:
    facts = read_tdnet_pdf(_page(title), _disclosure()).facts
    return [f.local_name for f in facts if f.namespace_key == "pdf-pl" and f.context_ref.startswith("Current")]


def test_kab464_schedule_after_income_statement_is_not_read() -> None:
    """9206 20240426578794: 損益計算書の次の「事業費明細書」の行が PL に入っていた。"""
    statement = ["営業収入合計", "事業費合計", "営業総利益", "当期純利益"]
    assert _pl_names("事業費明細書") == statement
    assert _pl_names("製造原価明細書") == statement

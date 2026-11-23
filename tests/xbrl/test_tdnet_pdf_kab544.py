from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6


def _text(x: float, y: float, text: str, width: float, size: float = 9.0) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 27) -> str:
    return _text(x1 - width, y, text, width)


def _disclosure() -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no="20220427529420",
        code="97280",
        disclosed_date=date(2022, 4, 27),
        disclosed_time="15:00",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2021, 4, 1),
        period_end=date(2022, 3, 31),
        fiscal_year_start=date(2021, 4, 1),
        fiscal_year_end=date(2022, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _bs(rows: list[tuple[float, str, str | None, str | None, str | None]]) -> dict[str, dict[str, Decimal]]:
    parts = [
        _text(60, 40, "（１）連結貸借対照表", 120),
        _text(470, 58, "（単位：百万円）", 70),
        _cell(397.7, 76, "前連結会計年度", 63),
        _cell(522.9, 76, "当連結会計年度", 63),
        _cell(397.7, 88, "(2021年３月31日)", 72),
        _cell(522.9, 88, "(2022年３月31日)", 72),
    ]
    y = 104.0
    for x, label, mark, prior, current in rows:
        parts.append(_text(x, y, label, 9 * len(label)))
        if mark:
            parts.append(_text(338.2, y - 1.1, mark, 21, size=6.0))
            parts.append(_text(463.4, y - 1.1, mark, 21, size=6.0))
        if prior:
            parts.append(_cell(397.7, y, prior))
        if current:
            parts.append(_cell(522.9, y, current))
        y += 13.5
    page = f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg></body></html>'
    out: dict[str, dict[str, Decimal]] = {}
    for fact in read_tdnet_pdf(page, _disclosure()).facts:
        if fact.namespace_key == "pdf-bs":
            out.setdefault(fact.local_name, {})[fact.context_ref] = fact.value_num
    return out


def test_kab544_heading_and_note_mark_list() -> None:
    facts = _bs([
        (76, "固定資産", None, None, None),
        (89, "有形固定資産", None, None, None),
        (102, "建物及び構築物", None, "6,812", "7,768"),
        (102, "有形固定資産合計", None, "5,411", "5,988"),
        (89, "投資その他の資産", None, None, None),
        (102, "投資有価証券", "※1,※2", "14,034", "15,653"),
        (102, "長期貸付金", None, "800", "833"),
        (102, "投資その他の資産合計", None, "20,360", "21,946"),
        (76, "その他の包括利益累計額", None, None, None),
        (89, "その他有価証券評価差額金", None, "2,050", "2,282"),
    ])
    assert facts["投資有価証券"] == {
        "Prior1YearInstant": MILLION * 14034,
        "CurrentYearInstant": MILLION * 15653,
    }, sorted(facts)
    assert facts["建物及び構築物"]["CurrentYearInstant"] == MILLION * 7768
    assert facts["その他有価証券評価差額金"]["CurrentYearInstant"] == MILLION * 2282
    assert facts["投資その他の資産合計"]["CurrentYearInstant"] == MILLION * 21946
    assert not [name for name in facts if name.startswith(("有形固定資産建物", "投資その他の資産投資", "その他の包括利益累計額その他"))]


def test_kab544_note_mark_list_before_current_only_value() -> None:
    facts = _bs([
        (76, "流動負債合計", None, "16,868", "13,626"),
        (76, "長期借入金", "※１,※２", "－", "32,000"),
        (76, "固定負債合計", None, "8,641", "38,987"),
    ])
    assert facts["長期借入金"] == {"CurrentYearInstant": MILLION * 32000}, sorted(facts)


def test_kab544_heading_wrapped_into_total_keeps_name() -> None:
    facts = _bs([
        (76, "その他の包括利益累計額", None, None, None),
        (89, "合計", None, "591", "1,280"),
        (76, "純資産合計", None, "57,000", "61,652"),
    ])
    assert facts["その他の包括利益累計額合計"]["CurrentYearInstant"] == MILLION * 1280, sorted(facts)

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

THOUSAND = Decimal(1000)


def _text(x: float, y: float, text: str, width: float | None = None) -> str:
    body = html.escape(text, quote=False)
    width = 9 * len(text) if width is None else width
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="9.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str) -> str:
    return _text(x1 - 37.1, y, text, 37.1)


def _page(parts: list[str]) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'


def _disclosure(no: str, code: str, period: str, start: date, end: date, fy_start: date, fy_end: date) -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        code=code,
        disclosed_date=date(2025, 10, 31),
        disclosed_time="18:30",
        type_of_period=period,
        type_of_document=f"{period}FinancialStatements_Consolidated_JP",
        period_start=start,
        period_end=end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _bs_facts(pages: list[str], d: PdfDisclosure) -> dict[str, dict[str, Decimal]]:
    out: dict[str, dict[str, Decimal]] = {}
    for fact in read_tdnet_pdf(f"<html><body>{''.join(pages)}</body></html>", d).facts:
        if fact.namespace_key == "pdf-bs":
            out.setdefault(fact.local_name, {})[fact.context_ref] = fact.value_num
    return out


def test_kab545_shifted_column_headers_fall_back_to_position() -> None:
    d = _disclosure("20230530587868", "66180", "FY", date(2022, 4, 1), date(2023, 3, 31), date(2022, 4, 1), date(2023, 3, 31))
    parts = [
        _text(90.3, 470, "（１）連結貸借対照表"),
        _text(90.3, 489.7, "（訂正後）"),
        _text(416.0, 503.7, "（単位：千円）", 58.0),
        _text(290.9, 519.9, "前連結会計年度", 58.0),
        _text(416.0, 519.9, "当連結会計年度", 58.0),
        _text(290.9, 533.5, "(2022年３月31日)", 65.7),
        _text(416.0, 533.5, "(2023年３月31日)", 65.7),
        _text(70.9, 548.2, "資産の部"),
        _text(70.9, 561.7, "流動資産"),
    ]
    rows = [
        ("現金及び預金", "1,738,010", "2,262,561"),
        ("流動資産合計", "7,689,108", "8,437,564"),
        ("資産合計", "11,349,408", "12,232,006"),
    ]
    for n, (label, prior, current) in enumerate(rows):
        y = 575.3 + 13.5 * n
        parts += [_text(70.9, y, label), _cell(416.4, y, prior), _cell(541.4, y, current)]
    facts = _bs_facts([_page(parts)], d)
    assert facts["資産合計"] == {
        "Prior1YearInstant": THOUSAND * 11349408,
        "CurrentYearInstant": THOUSAND * 12232006,
    }, facts.get("資産合計")


def test_kab545_running_head_on_next_page_keeps_one_statement() -> None:
    d = _disclosure("20251030583722", "94440", "1Q", date(2024, 5, 1), date(2024, 7, 31), date(2024, 5, 1), date(2025, 4, 30))
    head = "（１）四半期連結貸借対照表"
    columns = [
        _text(484.0, 97.8, "（単位：千円）", 58.0),
        _text(320.0, 113.9, "前連結会計年度", 58.0),
        _text(430.0, 113.9, "当第１四半期連結会計期間", 100.0),
        _text(320.0, 123.8, "(2024年４月30日)", 65.7),
        _text(445.0, 123.8, "(2024年７月31日)", 65.7),
    ]
    assets = [_text(22.7, 27.4, head), _text(63.7, 85.1, head), *columns, _text(62.2, 140, "資産の部")]
    for n, (label, prior, current) in enumerate([
        ("現金及び預金", "2,081,825", "1,794,663"),
        ("資産合計", "24,185,866", "23,632,013"),
    ]):
        y = 162.3 + 12 * n
        assets += [_text(75.1, y, label), _cell(395.9, y, prior), _cell(520.4, y, current)]
    liabilities = [_text(22.7, 27.4, head), *columns, _text(62.2, 140, "負債の部")]
    for n, (label, prior, current) in enumerate([
        ("流動負債合計", "10,447,122", "10,158,813"),
        ("負債合計", "21,563,593", "21,234,719"),
        ("純資産合計", "2,622,273", "2,397,294"),
        ("負債純資産合計", "24,185,866", "23,632,013"),
    ]):
        y = 162.3 + 12 * n
        liabilities += [_text(75.1, y, label), _cell(395.9, y, prior), _cell(520.4, y, current)]
    facts = _bs_facts([_page(assets), _page(liabilities)], d)
    assert facts["資産合計"]["CurrentYearInstant"] == THOUSAND * 23632013
    assert facts["負債純資産合計"] == {
        "Prior1YearInstant": THOUSAND * 24185866,
        "CurrentYearInstant": THOUSAND * 23632013,
    }, sorted(facts)

"""KAB-229 / KAB-231 / KAB-233: 業績予想修正 PDF の列寄せ・同時配当・円銭。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6
OKU = Decimal(10) ** 8


def _fail(msg: object) -> None:
    raise AssertionError(msg)


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


def _disclosure(**kwargs) -> PdfDisclosure:
    base = {
        "disclosure_no": "20250227584167",
        "code": "78650",
        "disclosed_date": date(2025, 2, 28),
        "disclosed_time": "17:00",
        "type_of_period": "FY",
        "type_of_document": "EarnForecastRevision",
        "period_start": date(2024, 1, 21),
        "period_end": date(2025, 1, 20),
        "fiscal_year_start": date(2024, 1, 21),
        "fiscal_year_end": date(2025, 1, 20),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def test_kab229_header_range_keeps_shifted_op_and_np() -> None:
    """7865 20250227584167: 営業利益・当期純利益の x1 が見出し内でずれても今回予想が残る。"""
    parts = [
        _text(40, 40, "通期業績予想の修正", 160),
        _text(400, 55, "（単位：百万円）", 80),
        _text(226.7, 70, "売上高", 35.0),
        _text(285.4, 70, "営業利益", 45.0),
        _text(349.2, 70, "経常利益", 44.9),
        _text(416.0, 70, "当期純利益", 40.1),
        _text(479.0, 70, "1株当たり当期純利益", 52.0),
        _text(40, 88, "前回発表予想（A）", 100),
        _cell(259.2, 88, "1,856", 30),
        _cell(323.0, 88, "△125", 30),
        _cell(386.8, 88, "△123", 30),
        _cell(446.3, 88, "△132", 25),
        _cell(530.5, 88, "△30.16", 40),
        _text(40, 106, "今回修正予想（B）", 100),
        _cell(254.2, 106, "1,916", 25),
        _cell(320.4, 106, "△50", 25),
        _cell(379.1, 106, "△45", 20),
        _cell(448.7, 106, "△72", 25),
        _cell(530.3, 106, "△16.46", 40),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure()))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上高", ctx)) != MILLION * 1916:
        _fail(facts)
    if facts.get(("営業利益", ctx)) != MILLION * Decimal("-50"):
        _fail(facts)
    if facts.get(("経常利益", ctx)) != MILLION * Decimal("-45"):
        _fail(facts)
    if facts.get(("当期純利益", ctx)) != MILLION * Decimal("-72"):
        _fail(facts)
    if facts.get(("1株当たり当期純利益", ctx)) != Decimal("-16.46"):
        _fail(facts)


def test_kab229_narrow_ordinary_income_stays_in_column() -> None:
    """7865 20230519577395: 経常利益の「5」が列から落ちない。"""
    parts = [
        _text(40, 40, "第１四半期累計期間業績予想の修正", 220),
        _text(400, 55, "（単位：百万円）", 80),
        _text(226.7, 70, "売上高", 35.0),
        _text(285.4, 70, "営業利益", 45.0),
        _text(349.2, 70, "経常利益", 44.9),
        _text(412.1, 70, "四半期純利益", 48.1),
        _text(40, 88, "前回発表予想（A）", 100),
        _cell(254.1, 88, "829", 20),
        _cell(320.4, 88, "△50", 25),
        _cell(384.2, 88, "△50", 25),
        _cell(437.6, 88, "△35", 20),
        _text(40, 106, "今回修正予想（B）", 100),
        _cell(254.3, 106, "1,011", 25),
        _cell(313.1, 106, "1", 5),
        _cell(376.8, 106, "5", 5),
        _cell(442.8, 106, "4", 10),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20230519577395",
        disclosed_date=date(2023, 5, 23),
        type_of_period="1Q",
        period_start=date(2023, 1, 21), period_end=date(2023, 4, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )))
    ctx = "CurrentAccumulatedQ1Duration_CurrentMember_ForecastMember"
    if facts.get(("売上高", ctx)) != MILLION * 1011:
        _fail(facts)
    if facts.get(("営業利益", ctx)) != MILLION * 1:
        _fail(facts)
    if facts.get(("経常利益", ctx)) != MILLION * 5:
        _fail(facts)
    if facts.get(("当期純利益", ctx)) != MILLION * 4:
        _fail(facts)


def test_kab231_forecast_revision_also_reads_dividend() -> None:
    """7865 20240214537449: 業績予想修正 doc の 2 ページ目の配当予想 48 円を読む。"""
    page1 = [
        _text(40, 40, "業績予想及び配当予想の修正に関するお知らせ", 280),
        _text(40, 70, "● 業績予想の修正について", 180),
        _cell(220, 90, "売上高", 40),
        _cell(220, 108, "百万円", 30),
        _text(40, 126, "今回修正予想", 80),
        _cell(220, 126, "5,353", 40),
    ]
    page2 = [
        _text(40, 40, "●配当予想の修正について", 180),
        _text(200, 70, "1株当たり配当金", 90),
        _cell(300, 90, "期末", 30),
        _cell(420, 90, "年間", 30),
        _text(40, 120, "今回予想", 60),
        _cell(280, 120, "48", 20),
        _cell(400, 120, "48", 20),
    ]
    facts = _facts(read_tdnet_pdf(_pages(page1, page2), _disclosure(
        disclosure_no="20240214537449",
        disclosed_date=date(2024, 2, 22),
        period_start=date(2023, 1, 21), period_end=date(2024, 1, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )))
    sales = "CurrentYearDuration_CurrentMember_ForecastMember"
    year_end = "CurrentYearDuration_YearEndMember_CurrentMember_ForecastMember_NonConsolidated"
    annual = "CurrentYearDuration_AnnualMember_CurrentMember_ForecastMember_NonConsolidated"
    if facts.get(("売上高", sales)) != MILLION * 5353:
        _fail(facts)
    dps_end = facts.get(("1株当たり配当金", year_end))
    dps_ann = facts.get(("1株当たり配当金", annual))
    if dps_end is None:
        dps_end = next((v for (name, ref), v in facts.items() if "YearEndMember" in ref), None)
    if dps_ann is None:
        dps_ann = next((v for (name, ref), v in facts.items() if "AnnualMember" in ref), None)
    if dps_end != Decimal("48") or dps_ann != Decimal("48"):
        _fail(facts)


def test_kab233_combined_yen_sen_token() -> None:
    """8630 20231206599881: 1 語の「801円98銭」を 801.98 として読む。"""
    parts = [
        _text(40, 40, "通期業績予想（連結）の修正", 220),
        _text(400, 58, "（単位：億円）", 80),
        _cell(220, 80, "経常利益", 50),
        _cell(400, 80, "当期純利益", 60),
        _cell(580, 80, "1株当たり当期純利益", 120),
        _text(40, 110, "今回修正予想", 80),
        _cell(220, 110, "4,510", 40),
        _cell(400, 110, "3,240", 40),
        _text(500, 110, "801円98銭", 80),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20231206599881", code="86300",
        disclosed_date=date(2023, 12, 6),
        period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
        fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
    )))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("経常利益", ctx)) != OKU * 4510:
        _fail(facts)
    if facts.get(("当期純利益", ctx)) != OKU * 3240:
        _fail(facts)
    if facts.get(("1株当たり当期純利益", ctx)) != Decimal("801.98"):
        _fail(facts)

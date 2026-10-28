"""KAB-228 / KAB-239 / KAB-241: 短信 PDF の期の判定。<text> の座標で再現する。IR BANK も本番 DB も使わない。

- 業績予想の修正の表は、見出しの期（日付の範囲、無ければ「第2四半期（累計）」「通期」）で期を決める。
  累計の表を通期にしない（7513・7865）。見出しにも題名にも決め手が無く、累計と通期の両方が書いてあれば入れない
- 年をずらしても月末は月末のまま（6506 の 2023年2月期の翌期末は 2024-02-29）
- 「令和」の無い 1〜2 桁の年は、書類の年度に近い方（6268「22年12月期」= 2022、「8年6月期」= 令和8年）
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.xbrl.pdf import PdfDisclosure, PdfLayoutError, read_tdnet_pdf


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
        "disclosure_no": "20220406518123", "code": "75130", "disclosed_date": date(2022, 4, 6),
        "disclosed_time": "15:00", "type_of_period": "2Q", "type_of_document": "EarnForecastRevision",
        "period_start": date(2021, 9, 1), "period_end": date(2022, 2, 28),
        "fiscal_year_start": date(2021, 9, 1), "fiscal_year_end": date(2022, 8, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _ends(pkg) -> dict[str, date]:
    return {ctx.context_ref: ctx.end_date for ctx in pkg.contexts}


def _revision_table(y: float, heading: str, values: tuple[str, str]) -> list[str]:
    """業績予想の修正の小さな表（売上高だけ）。前回・今回の 2 行。"""
    return [
        _text(40, y, heading, 420),
        _cell(300, y + 16, "売上高", 40),
        _cell(300, y + 28, "百万円", 30),
        _text(40, y + 42, "前回発表予想（A）", 90),
        _cell(300, y + 42, values[0], 50),
        _text(40, y + 56, "今回発表予想（B）", 90),
        _cell(300, y + 56, values[1], 50),
    ]


def test_kab228_cumulative_revision_table_is_not_full_year() -> None:
    """7513 20220406518123: 第2四半期（累計）の表と通期の表。累計の値を通期に入れない。"""
    parts = [
        _text(40, 40, "第２四半期累計期間の業績予想及び通期業績予想の修正に関するお知らせ", 380),
        *_revision_table(80, "（1）2022年8月期第2四半期（累計）業績予想数値の修正（2021年9月1日～2022年2月28日）",
                         ("131,400", "138,500")),
        *_revision_table(200, "（2）2022年8月期通期業績予想数値の修正（2021年9月1日～2022年8月31日）",
                         ("271,400", "280,700")),
    ]
    pkg = read_tdnet_pdf(_pages(parts), _disclosure())
    facts = _facts(pkg)
    million = Decimal(10) ** 6
    want = {
        ("売上高", "CurrentAccumulatedQ2Duration_CurrentMember_ForecastMember"): million * 138500,
        ("売上高", "CurrentAccumulatedQ2Duration_ForecastMember_PreviousMember"): million * 131400,
        ("売上高", "CurrentYearDuration_CurrentMember_ForecastMember"): million * 280700,
        ("売上高", "CurrentYearDuration_ForecastMember_PreviousMember"): million * 271400,
    }
    if facts != want:
        _fail(facts)
    ends = _ends(pkg)
    if ends["CurrentAccumulatedQ2Duration_CurrentMember_ForecastMember"] != date(2022, 2, 28):
        _fail(ends)


def test_kab228_twentieth_fiscal_year_q1_revision() -> None:
    """7865 20230519577395: 1 月 20 日決算の第1四半期累計の修正（1月21日～4月20日）は AccumulatedQ1。"""
    parts = [
        _text(40, 40, "2024年１月期第１四半期累計期間 業績予想の修正に関するお知らせ", 320),
        *_revision_table(80, "1．2024年1月期 第１四半期累計期間 業績予想の修正等（2023年1月21日～2023年4月20日）",
                         ("1,000", "1,011")),
    ]
    d = _disclosure(
        disclosure_no="20230519577395", code="78650", type_of_period="1Q",
        disclosed_date=date(2023, 5, 19), period_start=date(2023, 1, 21), period_end=date(2023, 4, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    refs = {ref for _name, ref in _facts(pkg)}
    if refs != {"CurrentAccumulatedQ1Duration_CurrentMember_ForecastMember",
                "CurrentAccumulatedQ1Duration_ForecastMember_PreviousMember"}:
        _fail(refs)
    if _ends(pkg)["CurrentAccumulatedQ1Duration_CurrentMember_ForecastMember"] != date(2023, 4, 20):
        _fail(_ends(pkg))


def test_kab228_heading_without_period_uses_title() -> None:
    """表の見出しに期が無ければ題名の期。題名が「第２四半期累計期間 業績予想の修正」なら累計。"""
    parts = [
        _text(40, 40, "2024年１月期第２四半期累計期間 業績予想の修正に関するお知らせ", 320),
        *_revision_table(80, "１．修正の内容", ("2,361", "2,394")),
    ]
    d = _disclosure(
        disclosure_no="20230825546825", code="78650", type_of_period="2Q",
        disclosed_date=date(2023, 8, 25), period_start=date(2023, 1, 21), period_end=date(2023, 7, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )
    refs = {ref for _name, ref in _facts(read_tdnet_pdf(_pages(parts), d))}
    if not refs or any("AccumulatedQ2" not in ref for ref in refs):
        _fail(refs)


def test_kab228_ambiguous_heading_is_not_saved() -> None:
    """累計と通期の両方を書いた題名で、表の見出しに期が無ければ入れない（通期と決めつけない）。"""
    parts = [
        _text(40, 40, "第２四半期累計期間及び通期業績予想の修正に関するお知らせ", 320),
        *_revision_table(80, "１．修正の内容", ("131,400", "138,500")),
    ]
    with pytest.raises(PdfLayoutError):
        read_tdnet_pdf(_pages(parts), _disclosure())


def test_kab228_full_year_revision_without_period_stays_full_year() -> None:
    """期の書いていない「業績予想の修正」は今までどおり通期。"""
    parts = [
        _text(40, 40, "業績予想の修正に関するお知らせ", 200),
        *_revision_table(80, "１．修正の内容", ("271,400", "280,700")),
    ]
    d = _disclosure(type_of_period="FY", period_start=date(2021, 9, 1), period_end=date(2022, 8, 31))
    refs = {ref for _name, ref in _facts(read_tdnet_pdf(_pages(parts), d))}
    if refs != {"CurrentYearDuration_CurrentMember_ForecastMember", "CurrentYearDuration_ForecastMember_PreviousMember"}:
        _fail(refs)


def _forecast_summary(fy_start: date, fy_end: date, period_end: date, title: str) -> tuple[list[str], PdfDisclosure]:
    parts = [
        _text(40, 40, title, 300),
        _text(40, 58, "（単位：百万円）", 80),
        _cell(300, 76, "売上収益", 60),
        _text(40, 94, "通期", 30),
        _cell(300, 94, "580,000", 60),
    ]
    d = _disclosure(
        disclosure_no="20230407543997", code="65060", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_IFRS", disclosed_date=date(2023, 4, 7),
        period_start=fy_start, period_end=period_end, fiscal_year_start=fy_start, fiscal_year_end=fy_end,
    )
    return parts, d


def test_kab239_next_year_of_february_end_is_leap_day() -> None:
    """6506 20230407543997: 2023年2月期の翌期（2024年2月期）の予想は 2024-02-29 まで。"""
    parts, d = _forecast_summary(
        date(2022, 3, 1), date(2023, 2, 28), date(2023, 2, 28), "３．2024年2月期の連結業績予想",
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    ends = _ends(pkg)
    if ends.get("NextYearDuration_ForecastMember") != date(2024, 2, 29):
        _fail(ends)
    starts = {ctx.context_ref: ctx.start_date for ctx in pkg.contexts}
    if starts["NextYearDuration_ForecastMember"] != date(2023, 3, 1):
        _fail(starts)


def test_kab239_day_that_is_not_month_end_is_kept() -> None:
    """月末でない決算日（20 日）は日をそのまま移す。"""
    parts, _ = _forecast_summary(
        date(2023, 1, 21), date(2024, 1, 20), date(2024, 1, 20), "３．2025年1月期の業績予想",
    )
    d = _disclosure(
        disclosure_no="20240229545849", code="78650", type_of_period="FY",
        type_of_document="FYFinancialStatements_NonConsolidated_JP", disclosed_date=date(2024, 2, 29),
        period_start=date(2023, 1, 21), period_end=date(2024, 1, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )
    ends = _ends(read_tdnet_pdf(_pages(parts), d))
    if ends.get("NextYearDuration_ForecastMember_NonConsolidated") != date(2025, 1, 20):
        _fail(ends)


def _q1_summary(rows: list[tuple[str, str]], fy_end: date, period_end: date) -> tuple[list[str], PdfDisclosure]:
    parts = [
        _text(40, 40, "（1）連結経営成績（累計）", 160),
        _cell(300, 58, "基本的1株当たり四半期利益", 140),
        _cell(300, 70, "円 銭", 30),
    ]
    for n, (label, value) in enumerate(rows):
        parts += [_text(40, 88 + n * 14, label, 140), _cell(300, 88 + n * 14, value, 50)]
    d = _disclosure(
        disclosure_no="20220428530699", code="62680", type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_IFRS", disclosed_date=date(2022, 4, 28),
        period_start=date(period_end.year, 1, 1), period_end=period_end,
        fiscal_year_start=date(fy_end.year, 1, 1), fiscal_year_end=fy_end,
    )
    return parts, d


def test_kab241_two_digit_year_is_western_year() -> None:
    """6268 20220428530699: 「22年12月期第１四半期」は 2022 年（令和22年 = 2040 年ではない）。前期と分かれる。"""
    parts, d = _q1_summary(
        [("22年12月期第１四半期", "△36.57"), ("21年12月期第１四半期", "647.79")],
        date(2022, 12, 31), date(2022, 3, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    got = {ref: value for (_name, ref), value in facts.items()}
    if got != {"CurrentAccumulatedQ1Duration_ResultMember": Decimal("-36.57"),
               "Prior1AccumulatedQ1Duration_ResultMember": Decimal("647.79")}:
        _fail(facts)


def test_kab241_reiwa_years_still_read_as_reiwa() -> None:
    """「令和4年3月期」と「令和」を省いた「4年3月期」は、2022 年度の書類では令和4年 = 2022。"""
    for current, prior in (("令和4年12月期第１四半期", "令和3年12月期第１四半期"),
                           ("4年12月期第１四半期", "3年12月期第１四半期")):
        parts, d = _q1_summary([(current, "10.00"), (prior, "9.00")], date(2022, 12, 31), date(2022, 3, 31))
        got = {ref for _name, ref in _facts(read_tdnet_pdf(_pages(parts), d))}
        if got != {"CurrentAccumulatedQ1Duration_ResultMember", "Prior1AccumulatedQ1Duration_ResultMember"}:
            _fail((current, got))

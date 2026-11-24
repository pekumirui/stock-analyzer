from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

THOUSAND = Decimal(1000)
MILLION = Decimal(1000000)
FY_DATES = ("(2022年３月31日)", "(2023年３月31日)")
Q2_DATES = ("(2021年３月31日)", "(2021年９月30日)")


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


def _disclosure(
    no: str, code: str, period: str, start: date, end: date, fy_end: date, standard: str = "JP",
) -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        code=code,
        disclosed_date=end,
        disclosed_time="15:00",
        type_of_period=period,
        type_of_document=f"{period}FinancialStatements_Consolidated_{standard}",
        period_start=start,
        period_end=end,
        fiscal_year_start=start,
        fiscal_year_end=fy_end,
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _bs_rows(pages: list[str], d: PdfDisclosure, context: str) -> list[tuple[str, Decimal]]:
    return [
        (fact.local_name, fact.value_num)
        for fact in read_tdnet_pdf(f"<html><body>{''.join(pages)}</body></html>", d).facts
        if fact.namespace_key == "pdf-bs" and fact.context_ref == context
    ]


def _columns(head: str, prior: str, current: str, dates: tuple[str, str], unit: str = "（単位：千円）") -> list[str]:
    return [
        _text(90.3, 30, head),
        _text(416.0, 42, unit, 58.0),
        _text(290.9, 54, prior, 58.0),
        _text(416.0, 54, current, 58.0),
        _text(290.9, 64, dates[0], 65.7),
        _text(416.0, 64, dates[1], 65.7),
    ]


def test_kab548_label_printed_below_its_amounts_on_a_new_page() -> None:
    d = _disclosure("20230530587868", "66180", "FY", date(2022, 4, 1), date(2023, 3, 31), date(2023, 3, 31))
    first = [*_columns("（１）連結貸借対照表", "前連結会計年度", "当連結会計年度", FY_DATES), _text(70.9, 78, "資産の部")]
    for n, (label, prior, current) in enumerate([
        ("現金及び預金", "1,738,010", "2,262,561"),
        ("流動資産合計", "7,689,108", "8,437,564"),
        ("機械装置及び運搬具（純額）", "745,663", "830,651"),
    ]):
        y = 92 + 13.5 * n
        first += [_text(70.9, y, label), _cell(416.4, y, prior), _cell(541.4, y, current)]
    second = [
        _cell(416.4, 60.1, "360,284"), _cell(541.4, 60.1, "357,620"),
        _text(70.9, 66.8, "土地"),
    ]
    for n, (label, prior, current) in enumerate([
        ("リース資産", "1,247,875", "1,323,583"),
        ("減価償却累計額", "△278,518", "△442,850"),
        ("リース資産（純額）", "969,356", "880,732"),
        ("資産合計", "11,349,408", "12,232,006"),
    ]):
        y = 80.4 + 13.5 * n
        second += [_text(70.9, y, label), _cell(416.4, y, prior), _cell(541.4, y, current)]
    rows = _bs_rows([_page(first), _page(second)], d, "CurrentYearInstant")
    assert ("土地", THOUSAND * 357620) in rows, rows
    assert ("リース資産", THOUSAND * 1323583) in rows, rows
    assert not any(name.startswith("土地リース") for name, _ in rows), rows


def test_kab548_label_between_rows_still_joins_the_next_row() -> None:
    d = _disclosure("20230530587869", "66180", "FY", date(2022, 4, 1), date(2023, 3, 31), date(2023, 3, 31))
    parts = [*_columns("（１）連結貸借対照表", "前連結会計年度", "当連結会計年度", FY_DATES), _text(70.9, 78, "資産の部")]
    parts += [_cell(416.4, 92, "100"), _cell(541.4, 92, "200"), _text(70.9, 99, "建物")]
    parts += [_text(70.9, 106, "及び構築物"), _cell(416.4, 106, "300"), _cell(541.4, 106, "400")]
    rows = _bs_rows([_page(parts)], d, "CurrentYearInstant")
    assert rows == [("建物及び構築物", THOUSAND * 400)], rows


def test_kab548_term_in_label_is_not_a_note_number() -> None:
    d = _disclosure("20211028419428", "62010", "2Q", date(2021, 4, 1), date(2021, 9, 30), date(2022, 3, 31), "IFRS")
    parts = [*_columns("（１）要約四半期連結財政状態計算書", "前連結会計年度", "当第２四半期連結会計期間", Q2_DATES, "（単位：百万円）"), _text(70.9, 78, "資産")]
    for n, (label, prior, current) in enumerate([
        ("流動資産", None, None),
        ("現金及び現金同等物", "238,248", "153,831"),
        ("預入期間が３ヶ月超の定期預金", "353,864", "404,636"),
        ("その他の金融資産5,7", "5,947", "4,854"),
        ("流動資産合計", "598,059", "563,321"),
    ]):
        y = 92 + 13.5 * n
        parts.append(_text(70.9, y, label))
        if prior is not None:
            parts += [_cell(416.4, y, prior), _cell(541.4, y, current)]
    rows = _bs_rows([_page(parts)], d, "CurrentYearInstant")
    names = [name for name, _ in rows]
    assert ("預入期間が3ヶ月超の定期預金", MILLION * 404636) in rows, rows
    assert not any(name.startswith("その他の金融資産") for name in names), names


def test_kab548_ifrs_headings_do_not_glue_to_the_first_row() -> None:
    d = _disclosure("20211028419430", "62010", "2Q", date(2021, 4, 1), date(2021, 9, 30), date(2022, 3, 31), "IFRS")
    parts = _columns("（１）要約四半期連結財政状態計算書", "前連結会計年度", "当第２四半期連結会計期間", Q2_DATES, "（単位：百万円）")
    for n, (label, prior, current) in enumerate([
        ("負債及び資本", None, None),
        ("負債", None, None),
        ("流動負債", None, None),
        ("営業債務及びその他の債務", "613,579", "597,362"),
        ("負債合計", "3,181,436", "3,257,937"),
        ("資本", None, None),
        ("親会社の所有者に帰属する持分", None, None),
        ("資本金", "80,462", "80,462"),
        ("親会社の所有者に帰属する持分合計", "3,236,038", "3,572,251"),
        ("負債及び資本合計", "6,503,986", "6,917,261"),
    ]):
        y = 78 + 13.5 * n
        parts.append(_text(70.9, y, label))
        if prior is not None:
            parts += [_cell(416.4, y, prior), _cell(541.4, y, current)]
    rows = _bs_rows([_page(parts)], d, "CurrentYearInstant")
    assert [name for name, _ in rows] == [
        "営業債務及びその他の債務", "負債合計", "資本金", "親会社の所有者に帰属する持分合計", "負債及び資本合計",
    ], rows


def _table(rows: list[tuple[str, str | None, str | None]], *, standard: str = "JP") -> list[tuple[str, Decimal]]:
    d = _disclosure("20240809569790", "72170", "1Q", date(2024, 4, 1), date(2024, 6, 30), date(2025, 3, 31), standard)
    parts = _columns("（１）四半期連結貸借対照表", "前連結会計年度", "当第１四半期連結会計期間", ("(2024年３月31日)", "(2024年６月30日)"))
    for n, (label, prior, current) in enumerate(rows):
        y = 78 + 13.5 * n
        parts.append(_text(89.0, y, label))
        if prior is not None:
            parts.append(_cell(416.4, y, prior))
        if current is not None:
            parts.append(_cell(541.4, y, current))
    return _bs_rows([_page(parts)], d, "CurrentYearInstant")


def test_kab548_box_drawing_and_hyphen_dashes_are_empty_cells() -> None:
    rows = _table([
        ("買掛金", "196,968", "260,592"),
        ("未払法人税等", "164,629", "─"),
        ("製品保証引当金", "5,532", "5,668"),
        ("ソフトウエア仮勘定", "112,500", "‐"),
        ("その他", "409", "409"),
        ("流動負債合計", "479,038", "266,669"),
    ])
    assert rows == [
        ("買掛金", THOUSAND * 260592),
        ("製品保証引当金", THOUSAND * 5668),
        ("その他", THOUSAND * 409),
        ("流動負債合計", THOUSAND * 266669),
    ], rows


def test_kab548_note_prefix_and_bracketed_class_heads() -> None:
    rows = _table([
        ("(資産の部)", None, None),
        ("流動資産", "3,625,236", "3,655,119"),
        ("(負債の部)", None, None),
        ("流動負債", "1,839,187", "1,796,862"),
        ("注1.社債、借入金及びリース負債", "151,698", "149,102"),
        ("保険業法第113条繰延資産", "311,714", "376,646"),
    ], standard="IFRS")
    assert [name for name, _ in rows] == [
        "流動資産", "流動負債", "社債、借入金及びリース負債", "保険業法第113条繰延資産",
    ], rows


def test_kab548_rows_after_the_grand_total_are_not_bs() -> None:
    rows = _table([
        ("資本計", "3,866,418", "3,941,819"),
        ("負債・資本計", "6,167,340", "6,205,062"),
        ("注1.社債、借入金及びリース負債残高", "394,636", "393,255"),
        ("うちリース負債除く残高", "241,290", "236,262"),
    ], standard="IFRS")
    assert [name for name, _ in rows] == ["資本計", "負債・資本計"], rows


def test_kab548_equity_sub_headings_without_amounts() -> None:
    rows = _table([
        ("資本金", "840,687", "840,687"),
        ("資本剰余金", None, None),
        ("資本準備金", "687,087", "687,087"),
        ("資本剰余金合計", "687,108", "687,108"),
        ("利益剰余金", None, None),
        ("利益準備金", "77,935", "77,935"),
        ("その他利益剰余金", None, None),
        ("別途積立金", "2,371,262", "2,371,262"),
        ("利益剰余金合計", "1,566,920", "1,566,920"),
    ])
    assert [name for name, _ in rows] == [
        "資本金", "資本準備金", "資本剰余金合計", "利益準備金", "別途積立金", "利益剰余金合計",
    ], rows

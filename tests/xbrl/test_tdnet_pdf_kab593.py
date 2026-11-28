from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(1000000)
Q1_DATES = ("（2024年３月31日）", "（2024年６月30日）")


def _text(x: float, y: float, text: str, width: float | None = None) -> str:
    body = html.escape(text, quote=False)
    width = 9 * len(text) if width is None else width
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="9.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 37.1) -> str:
    return _text(x1 - width, y, text, width)


def _page(parts: list[str]) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>'


def _disclosure(no: str, code: str, standard: str = "JP") -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        code=code,
        disclosed_date=date(2024, 8, 7),
        disclosed_time="15:00",
        type_of_period="1Q",
        type_of_document=f"1QFinancialStatements_Consolidated_{standard}",
        period_start=date(2024, 4, 1),
        period_end=date(2024, 6, 30),
        fiscal_year_start=date(2024, 4, 1),
        fiscal_year_end=date(2025, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pages: list[str], d: PdfDisclosure, namespace: str = "pdf-bs") -> list[tuple[str, Decimal]]:
    return [
        (fact.local_name, fact.value_num)
        for fact in read_tdnet_pdf(f"<html><body>{''.join(pages)}</body></html>", d).facts
        if fact.namespace_key == namespace and fact.context_ref == "CurrentYearInstant"
    ]


def _columns(head: str, *, note: bool = False) -> list[str]:
    parts = [
        _text(90.3, 30, head),
        _text(466.9, 42, "（単位：百万円）", 71.7),
        _text(332.5, 54, "前連結会計年度", 62.7),
        _text(453.4, 54, "当第１四半期連結会計期間", 107.5),
        _text(323.5, 64, Q1_DATES[0], 80.6),
        _text(440.0, 64, Q1_DATES[1], 80.6),
    ]
    if note:
        parts.append(_text(283.2, 59, "注記", 17.9))
    return parts


def _rows(rows: list[tuple[str, str | None, str | None, str | None]], y0: float = 80.0) -> list[str]:
    parts: list[str] = []
    for n, (label, note, prior, current) in enumerate(rows):
        y = y0 + 17.9 * n
        parts.append(_text(77.1, y, label))
        if note is not None:
            parts.append(_cell(301.1, y, note, 4.5 * len(note)))
        if prior is not None:
            parts.append(_cell(417.6, y, prior))
        if current is not None:
            parts.append(_cell(534.1, y, current))
    return parts


def test_kab593_note_numbers_in_the_note_column_stay_off_the_label() -> None:
    d = _disclosure("20240807564835", "77330", "IFRS")
    parts = [*_columns("（１）要約四半期連結財政状態計算書", note=True), _text(59.2, 72, "資産")]
    parts += _rows([
        ("現金及び現金同等物", "7,15", "340,933", "274,408"),
        ("営業債権及びその他の債権", "15", "197,599", "184,708"),
        ("その他の金融負債", "7、13", "28,058", "12,693"),
        ("流動資産合計", None, "800,340", "736,623"),
    ])
    assert _facts([_page(parts)], d) == [
        ("現金及び現金同等物", MILLION * 274408),
        ("営業債権及びその他の債権", MILLION * 184708),
        ("その他の金融負債", MILLION * 12693),
        ("流動資産合計", MILLION * 736623),
    ]


def test_kab593_note_numbers_without_a_note_column_still_drop_the_row() -> None:
    d = _disclosure("20240807564836", "77330", "IFRS")
    parts = [*_columns("（１）要約四半期連結財政状態計算書"), _text(59.2, 72, "資産")]
    parts += _rows([
        ("現金及び現金同等物", None, "340,933", "274,408"),
        ("その他の金融資産 5,7", None, "9,862", "10,017"),
        ("流動資産合計", None, "800,340", "736,623"),
    ])
    names = [name for name, _ in _facts([_page(parts)], d)]
    assert names == ["現金及び現金同等物", "流動資産合計"], names


def test_kab593_marked_amount_in_the_amount_column_is_an_amount() -> None:
    d = _disclosure("20240809568862", "98540")
    parts = [*_columns("（１）四半期連結貸借対照表"), _text(62.2, 72, "資産の部")]
    parts += _rows([
        ("現金及び預金", None, "4,360", "3,947"),
        ("受取手形", None, "※ 8", "※ 10"),
        ("売掛金", None, "820", "863"),
        ("繰延ヘッジ損益", None, "※ △44", "※ 6"),
        ("流動資産合計", None, "7,390", "7,435"),
    ])
    assert _facts([_page(parts)], d) == [
        ("現金及び預金", MILLION * 3947),
        ("受取手形", MILLION * 10),
        ("売掛金", MILLION * 863),
        ("繰延ヘッジ損益", MILLION * 6),
        ("流動資産合計", MILLION * 7435),
    ]


def test_kab593_mark_printed_apart_from_its_amount() -> None:
    d = _disclosure("20260508520073", "33860")
    parts = [*_columns("（１）四半期連結貸借対照表"), _text(62.2, 72, "資産の部")]
    for n, (label, prior, current) in enumerate([
        ("現金及び預金", "2,747", "2,643"),
        ("受取手形", "7", "1"),
        ("売掛金", "3,078", "3,368"),
        ("流動資産合計", "9,000", "9,500"),
    ]):
        y = 80.0 + 17.9 * n
        parts.append(_text(77.1, y, label))
        if label == "受取手形":
            parts += [_text(404.0, y, "※", 9.0), _cell(417.6, y, prior, 4.5)]
            parts += [_text(520.6, y, "※", 9.0), _cell(534.1, y, current, 4.5)]
        else:
            parts += [_cell(417.6, y, prior), _cell(534.1, y, current)]
    assert _facts([_page(parts)], d) == [
        ("現金及び預金", MILLION * 2643),
        ("受取手形", MILLION * 1),
        ("売掛金", MILLION * 3368),
        ("流動資産合計", MILLION * 9500),
    ]


def test_kab593_note_mark_beside_the_label_is_not_an_amount() -> None:
    d = _disclosure("20240809568863", "98540")
    parts = [*_columns("（１）四半期連結貸借対照表"), _text(62.2, 72, "資産の部")]
    parts += _rows([
        ("現金及び預金", None, "4,360", "3,947"),
        ("受取手形", None, "820", "863"),
        ("流動資産合計", None, "7,390", "7,435"),
    ])
    parts.append(_text(120.0, 80.0 + 17.9, "※1", 13.5))
    assert _facts([_page(parts)], d) == [
        ("現金及び預金", MILLION * 3947),
        ("受取手形", MILLION * 863),
        ("流動資産合計", MILLION * 7435),
    ]


def test_kab593_angle_bracket_correction_drops_the_old_bs_and_the_omission_marks() -> None:
    d = _disclosure("20250910555890", "61370")
    parts = [_text(72.7, 20, "（１）四半期連結貸借対照表"), _text(72.7, 26, "〈訂正前〉")]
    parts += [*_columns("")[1:], _text(62.2, 72, "資産の部")]
    parts += _rows([
        ("受取手形及び売掛金", None, "10,524", "8,661"),
        ("流動資産合計", None, "41,686", "38,145"),
        ("（中略）", None, None, None),
        ("資産合計", None, "74,735", "71,977"),
    ])
    old = _page(parts)
    parts = [_text(72.7, 20, "〈訂正後〉")]
    parts += [*_columns("（１）四半期連結貸借対照表")[1:], _text(72.7, 30, "（１）四半期連結貸借対照表")]
    parts.append(_text(62.2, 72, "資産の部"))
    parts += _rows([
        ("受取手形及び売掛金", None, "10,524", "8,772"),
        ("流動資産合計", None, "41,686", "38,256"),
        ("（中略）", None, None, None),
        ("資産合計", None, "74,735", "72,087"),
    ])
    assert _facts([old, _page(parts)], d) == [
        ("受取手形及び売掛金", MILLION * 8772),
        ("流動資産合計", MILLION * 38256),
        ("資産合計", MILLION * 72087),
    ]

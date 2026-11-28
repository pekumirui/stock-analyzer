from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6
PRIOR_X1 = 397.7
CURRENT_X1 = 522.9
CURRENT = "CurrentYearDuration"
PRIOR = "Prior1YearDuration"


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


def _dated_header() -> list[str]:
    return [
        _text(470, 58, "（単位：百万円）", 70),
        _cell(PRIOR_X1, 76, "前連結会計年度", 63),
        _cell(CURRENT_X1, 76, "当連結会計年度", 63),
        _cell(PRIOR_X1, 88, "(自 2020年４月１日", 72),
        _cell(CURRENT_X1, 88, "(自 2021年４月１日", 72),
        _cell(PRIOR_X1, 100, "至 2021年３月31日)", 72),
        _cell(CURRENT_X1, 100, "至 2022年３月31日)", 72),
    ]


def _cf(lines: list[list[tuple[float, str] | tuple[float, str, float]]], header: list[str] | None = None) -> dict[str, dict[str, Decimal]]:
    parts = [_text(60, 40, "（３）連結キャッシュ・フロー計算書", 160), *(header or _dated_header())]
    y = 116.0
    for words in lines:
        for word in words:
            x, text = word[0], word[1]
            if len(word) == 3:
                parts.append(_cell(x, y, text, word[2]))
            else:
                parts.append(_text(x, y, text, 9 * len(text)))
        y += 12.0
    page = f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg></body></html>'
    out: dict[str, dict[str, Decimal]] = {}
    for fact in read_tdnet_pdf(page, _disclosure()).facts:
        if fact.namespace_key == "pdf-cf":
            out.setdefault(fact.local_name, {})[fact.context_ref] = fact.value_num
    return out


def _row(label: str, prior: str | None, current: str | None, x: float = 76) -> list:
    words: list = [(x, label)]
    if prior is not None:
        words.append((PRIOR_X1, prior, 27))
    if current is not None:
        words.append((CURRENT_X1, current, 27))
    return words


def test_kab612_wrapped_label_with_note_mark_between_amounts_and_tail() -> None:
    facts = _cf([
        _row("法人税等", "187,371", "140,611"),
        [(76, "営業債権及びその他の債権の増減（△は増")],
        [(PRIOR_X1, "377,886", 27), (CURRENT_X1, "426,040", 27)],
        [(103, "※1", 8)],
        [(76, "加額）")],
        _row("棚卸資産の増減（△は増加額）", "△44,427", "△18,180"),
        [(76, "有形固定資産・無形資産及び投資不動産の")],
        [(PRIOR_X1, "20,878", 27), (CURRENT_X1, "13,269", 27)],
        [(139, "※3", 8)],
        [(76, "売却による収入")],
        _row("政府補助金による収入", "1,048", "1,462"),
        [(95, "※2", 8)],
        _row("その他", "△128,138", "△1,888"),
    ])
    assert facts["営業債権及びその他の債権の増減"][CURRENT] == MILLION * 426040, sorted(facts)
    assert facts["棚卸資産の増減"][CURRENT] == MILLION * -18180, sorted(facts)
    assert facts["有形固定資産・無形資産及び投資不動産の売却による収入"][CURRENT] == MILLION * 13269, sorted(facts)
    assert facts["政府補助金による収入"][CURRENT] == MILLION * 1462, sorted(facts)
    assert facts["その他"][CURRENT] == MILLION * -1888, sorted(facts)


def test_kab612_change_tail_without_gaku_stays_on_the_row() -> None:
    facts = _cf([
        [(76, "営業債務及びその他の債務・未払人件費の")],
        [(PRIOR_X1, "△246,004", 27), (CURRENT_X1, "△425,879", 27)],
        [(76, "増減（△は減少額）")],
        _row("その他の流動負債の増減（△は減少額）", "14,260", "△101,736"),
    ])
    assert facts["営業債務及びその他の債務・未払人件費の増減"][CURRENT] == MILLION * -425879, sorted(facts)
    assert facts["その他の流動負債の増減"][CURRENT] == MILLION * -101736, sorted(facts)


def test_kab612_numbered_section_headings_do_not_prefix_first_item() -> None:
    facts = _cf([
        [(59, "Ⅰ"), (72, "営業活動によるキャッシュ・フロー")],
        _row("１.当期純利益", "206,500", "76,239", x=64),
        [(64, "２.営業活動によるキャッシュ・フローへの調整")],
        _row("(１)減価償却費及び償却費", "96,414", "102,503", x=78),
        _row("計", "226,619", "228,000", x=169),
        [(59, "Ⅱ"), (72, "投資活動によるキャッシュ・フロー")],
        _row("１.有形固定資産の取得", "△114,859", "△197,453", x=64),
    ])
    assert facts["当期純利益"][CURRENT] == MILLION * 76239, sorted(facts)
    assert facts["減価償却費及び償却費"][CURRENT] == MILLION * 102503, sorted(facts)
    assert facts["有形固定資産の取得"][CURRENT] == MILLION * -197453, sorted(facts)


def test_kab612_comparison_column_and_table_after_closing_cash() -> None:
    header = [
        _text(470, 58, "（単位：百万円）", 70),
        _cell(330, 76, "2020年度", 40),
        _cell(420, 76, "2021年度", 40),
        _cell(510, 76, "比較", 20),
    ]
    lines = []
    for label, prior, current, diff in (
        ("営業活動によるキャッシュ・フロー", "89", "218", "129"),
        ("現金及び現金同等物の期末残高", "428", "524", "96"),
    ):
        lines.append([(76, label), (330, prior, 20), (420, current, 20), (510, diff, 20)])
    lines.append([(470, "（単位：百万円）", 70)])
    lines.append([(310, "2021年度見通し", 60), (400, "2021年度見通し", 60), (500, "比較", 20)])
    lines.append([(76, "合計"), (330, "5,800", 30), (420, "6,200", 30), (510, "400", 20)])
    facts = _cf(lines, header)
    assert facts["営業活動によるキャッシュ・フロー"] == {
        PRIOR: MILLION * 89,
        CURRENT: MILLION * 218,
    }, facts
    assert "合計" not in facts, sorted(facts)


def test_kab612_term_and_note_ref_in_cf_labels() -> None:
    facts = _cf([
        _row("短期借入金（３ヶ月以内）の純増減額（△は減少）", "△17,167", "16,555"),
        _row("短期借入金(3ヵ月超)による収入", "414", "185"),
        _row("営業活動による現金生成額 (5)(j)", "△11,978", "△12,835"),
    ])
    assert facts["短期借入金(3ヶ月以内)の純増減額"][CURRENT] == MILLION * 16555, sorted(facts)
    assert facts["短期借入金(3ヵ月超)による収入"][CURRENT] == MILLION * 185, sorted(facts)
    assert facts["営業活動による現金生成額"][CURRENT] == MILLION * -12835, sorted(facts)


def test_kab612_note_numbers_in_cf_labels_and_cells() -> None:
    facts = _cf([
        _row("金融収益 2,3", "△9,396", "△39,251"),
        _row("減損損失 8,14", "42,805", "2,493"),
        [(76, "連結の範囲の変更を伴う子会社株式の取得による支出"), (PRIOR_X1, "-", 4), (CURRENT_X1, "（注）△8,442", 58)],
        _row("その他", "△21", "△10"),
    ])
    assert facts["金融収益"][CURRENT] == MILLION * -39251, sorted(facts)
    assert facts["減損損失"][CURRENT] == MILLION * 2493, sorted(facts)
    assert facts["連結の範囲の変更を伴う子会社株式の取得による支出"] == {CURRENT: MILLION * -8442}, sorted(facts)
    assert facts["その他"][CURRENT] == MILLION * -10, sorted(facts)


def test_kab612_hyphen_and_box_dash_cells() -> None:
    facts = _cf([
        _row("減損損失", "‐", "139"),
        _row("投資有価証券売却損益（△は益）", "△54", "‐"),
        _row("持分法による投資損益（△は益）", "287", "236"),
        _row("関係会社株式の取得による支出", "△17,217", "─"),
        _row("関係会社貸付による支出", "△45,510", "△109,920"),
    ])
    assert facts["減損損失"] == {CURRENT: MILLION * 139}, sorted(facts)
    assert facts["投資有価証券売却損益"] == {PRIOR: MILLION * -54}, sorted(facts)
    assert facts["持分法による投資損益"][CURRENT] == MILLION * 236, sorted(facts)
    assert facts["関係会社株式の取得による支出"] == {PRIOR: MILLION * -17217}, sorted(facts)
    assert facts["関係会社貸付による支出"][CURRENT] == MILLION * -109920, sorted(facts)

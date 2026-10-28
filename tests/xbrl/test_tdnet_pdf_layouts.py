"""短信 PDF の HTML の読み取りで、実データで間違えた並び（KAB-173）。空 DB も IR BANK も使わない。

どれも ZIP / J-Quants と突き合わせて値が違った（または取れなかった）回の形を、<text> の座標だけで作り直したもの。
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, label_key, read_tdnet_pdf

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


def _disclosure(**kwargs) -> PdfDisclosure:
    base = {
        "disclosure_no": "20260806512198",
        "code": "80010",
        "disclosed_date": date(2026, 8, 6),
        "disclosed_time": "15:30",
        "type_of_period": "1Q",
        "type_of_document": "1QFinancialStatements_Consolidated_IFRS",
        "period_start": date(2026, 4, 1),
        "period_end": date(2026, 6, 30),
        "fiscal_year_start": date(2026, 4, 1),
        "fiscal_year_end": date(2027, 3, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _values(pkg) -> set[Decimal]:
    return {fact.value_num for fact in pkg.facts}


def test_current_first_bs_with_change_column() -> None:
    """伊藤忠: 当期が左、前期末が中、増減額が右。見出しは数値の中央にあり、右端に重ならない。"""
    parts = [
        _text(55, 69, "（２）要約四半期連結財政状態計算書", 180),
        _text(55, 96, "（単位：百万円）", 80),
        _text(282, 115, "2 0 2 6 年", 40), _text(372, 114, "2 0 2 6 年", 40),
        _text(144, 120, "科　　目", 40), _text(466, 120, "増 減 額", 40),
        _text(282, 125, "６　月　末", 40), _text(372, 124, "３　月　末", 40),
        _text(68, 204, "現金及び現金同等物", 90),
        _cell(354, 204, "673,419", 31), _cell(444, 204, "593,766", 31), _cell(534, 204, "79,653", 27),
        _text(68, 768, "資産合計", 40),
        _cell(354, 768, "17,179,751", 45), _cell(444, 768, "16,732,815", 45), _cell(534, 768, "446,936", 32),
    ]
    pkg = read_tdnet_pdf(_pages(parts), _disclosure())
    facts = _facts(pkg)
    assert facts[("資産合計", "CurrentYearInstant")] == MILLION * 17179751
    assert facts[("資産合計", "Prior1YearInstant")] == MILLION * 16732815
    assert MILLION * 446936 not in _values(pkg)


def test_two_line_running_head_and_page_number_are_not_headers() -> None:
    """香陵住販: 2 行の柱（銘柄名(コード) / 期 題名）とページ番号が、続きのページの列見出しに入る。"""
    def head(page: int) -> list[str]:
        return [
            _text(424, 25, "香陵住販株式会社(3495)", 99),
            _text(384, 34, "2026年９月期", 54), _text(442, 34, "第３四半期決算短信", 81),
            _text(280, 800, f"- {page} -", 20),
        ]

    # 見出しは列の中央（前期 353–398、当期 478–523）。
    cols = [
        _text(341, 104, "前連結会計年度", 70), _text(450, 104, "当第３四半期連結会計期間", 100),
        _text(335, 116, "(2025年９月30日)", 80), _text(460, 116, "(2026年６月30日)", 80),
    ]
    first = head(5) + [
        _text(56, 80, "（１）四半期連結貸借対照表", 150),
        _text(470, 92, "(単位：千円)", 50),
        *cols,
        _text(76, 150, "資産合計", 40), _cell(398, 150, "17,837,116", 45), _cell(523, 150, "16,435,718", 45),
    ]
    second = head(6) + [
        _text(470, 70, "(単位：千円)", 50),
        *cols,
        _text(76, 150, "負債合計", 40), _cell(398, 150, "11,638,376", 45), _cell(523, 150, "9,530,128", 40),
    ]
    third = head(7) + [_text(56, 100, "（３）四半期連結財務諸表に関する注記事項", 200)]
    d = _disclosure(
        disclosure_no="20260805510579", code="34950", type_of_period="3Q",
        type_of_document="3QFinancialStatements_Consolidated_JP",
        period_start=date(2025, 10, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2025, 10, 1), fiscal_year_end=date(2026, 9, 30),
    )
    facts = _facts(read_tdnet_pdf(_pages(first, second, third), d))
    assert facts[("負債合計", "CurrentYearInstant")] == Decimal(1000) * 9530128
    assert facts[("負債合計", "Prior1YearInstant")] == Decimal(1000) * 11638376


def test_segment_pl_is_not_the_pl() -> None:
    """トヨタ: 「自動車等セグメントと金融セグメントを区分した連結損益計算書」を本表と取り違えない。"""
    cols = [_text(330, 110, "前連結会計年度", 70), _text(440, 110, "当連結会計年度", 70)]
    pl = [
        _text(60, 66, "２．連結損益計算書及び連結包括利益計算書", 220),
        _text(82, 83, "連結損益計算書", 80),
        _text(450, 95, "（単位：百万円）", 70),
        *cols,
        _text(60, 140, "営業収益", 40),
        _text(70, 152, "商品・製品売上収益", 90), _cell(382, 152, "43,598,877", 45), _cell(489, 152, "45,865,949", 45),
        _text(60, 200, "営業収益合計", 60), _cell(382, 200, "48,036,704", 45), _cell(489, 200, "50,684,952", 45),
    ]
    segment = [
        _text(107, 66, "自動車等セグメントと金融セグメントを区分した連結損益計算書", 300),
        _text(450, 78, "（単位：百万円）", 70),
        *cols,
        _text(60, 143, "営業収益", 40), _cell(382, 143, "43,787,709", 45), _cell(489, 143, "46,079,610", 45),
    ]
    d = _disclosure(
        disclosure_no="20260506517369", code="72030", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_IFRS",
        period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
        fiscal_year_start=date(2025, 4, 1), fiscal_year_end=date(2026, 3, 31),
    )
    pkg = read_tdnet_pdf(_pages(pl, segment), d)
    facts = _facts(pkg)
    assert facts[("営業収益合計", "CurrentYearDuration")] == MILLION * 50684952
    assert MILLION * 46079610 not in _values(pkg)


def test_forecast_and_quarter_only_columns_are_not_statement_values() -> None:
    """東京海上・トレンドマイクロ: 本表の後ろの「業績予想の修正」表と、3 か月だけの表を本表の値にしない。"""
    parts = [
        _text(56, 66, "（２）中間連結損益計算書", 120),
        _text(450, 78, "（単位：百万円）", 70),
        _text(300, 90, "前中間連結会計期間", 90), _text(430, 90, "当中間連結会計期間", 90),
        _text(60, 140, "経常利益", 40), _cell(382, 140, "1,219,135", 45), _cell(506, 140, "1,202,445", 45),
        _text(450, 300, "（単位：百万円）", 70),
        _text(280, 312, "前第2四半期連結会計期間", 100), _text(410, 312, "当第2四半期連結会計期間", 100),
        _text(60, 340, "経常利益", 40), _cell(382, 340, "400,001", 40), _cell(506, 340, "300,003", 40),
        _text(450, 500, "（単位：百万円）", 70),
        _text(200, 512, "前回発表予想(A)", 70), _text(290, 512, "今回修正予想(B)", 70), _text(390, 512, "増減額", 40),
        _text(60, 540, "経常利益", 40),
        _cell(250, 540, "1,230,000", 45), _cell(340, 540, "1,380,000", 45), _cell(430, 540, "150,000", 40),
    ]
    d = _disclosure(
        disclosure_no="20260812518692", code="36590", type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_JP",
        period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2026, 1, 1), fiscal_year_end=date(2026, 12, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    assert facts[("経常利益", "CurrentAccumulatedQ2Duration")] == MILLION * 1202445
    values = _values(pkg)
    for wrong in ("300003", "1230000", "1380000", "150000"):
        assert MILLION * Decimal(wrong) not in values, wrong


def test_or_and_triangle_labels() -> None:
    assert label_key("営業利益又は営業損失（△）") == "営業利益"
    assert label_key("営業利益(△損失)") == "営業利益"
    assert label_key("親会社株主に帰属する四半期純利益又は親会社株主に帰属する四半期純損失（△）") == (
        "親会社株主に帰属する当期純利益"
    )
    assert label_key("中間利益の帰属：親会社の所有者") == "親会社の所有者に帰属する当期利益"
    assert label_key("営業活動に関するキャッシュ・フロー") == "営業活動によるキャッシュ・フロー"
    assert label_key("営業活動から得た現金及び現金同等物(純額)") == "営業活動によるキャッシュ・フロー"
    assert label_key("現金及び現金同等物の四半期末残高") == "現金及び現金同等物の期末残高"
    assert label_key("基本的１株当たり親会社の所有者に帰属する当期利益") == "基本的1株当たり当期利益"
    assert label_key("連結売上高(A)") == "連結売上高"
    assert label_key("売上高（Ｂ）") == "売上高"


def _forecast_page(rows: list[str]) -> list[str]:
    return [
        _text(55, 644, "３．2027年３月期の連結業績予想（2026年４月１日～2027年３月31日）", 300),
        _text(150, 678, "売上高", 30), _text(230, 678, "営業利益", 40),
        _text(140, 696, "百万円", 30), _text(220, 696, "百万円", 30),
        *rows,
    ]


def test_range_forecast_three_line_layouts() -> None:
    """レンジ予想は 3 行。「下限 / ～ / 上限」「下限～ / ダッシュ / 上限」「下限 / 行名 / ～上限」の 3 通り。"""
    layouts = {
        "tilde-line": [
            _cell(170, 707, "57,823", 30), _cell(260, 707, "11,500", 30),
            _text(88, 717, "通期", 20), _text(162, 717, "～", 8), _text(252, 717, "～", 8),
            _cell(170, 728, "59,000", 30), _cell(260, 728, "12,500", 30),
        ],
        "open-lower": [
            _cell(170, 707, "57,823～", 36), _cell(260, 707, "11,500～", 36),
            _text(88, 717, "通期", 20), _cell(170, 717, "－", 8), _cell(260, 717, "－", 8),
            _cell(170, 728, "59,000", 30), _cell(260, 728, "12,500", 30),
        ],
        "tilde-upper": [
            _cell(170, 707, "57,823", 30), _cell(260, 707, "11,500", 30),
            _text(88, 717, "通期", 20),
            _cell(170, 728, "～59,000", 36), _cell(260, 728, "～12,500", 36),
        ],
    }
    d = _disclosure(type_of_document="1QFinancialStatements_Consolidated_JP")
    for name, rows in layouts.items():
        facts = _facts(read_tdnet_pdf(_pages(_forecast_page(rows)), d))
        low = facts.get(("売上高", "NextYearDuration_ForecastMember_LowerMember")) or facts.get(
            ("売上高", "CurrentYearDuration_ForecastMember_LowerMember")
        )
        high = facts.get(("売上高", "NextYearDuration_ForecastMember_UpperMember")) or facts.get(
            ("売上高", "CurrentYearDuration_ForecastMember_UpperMember")
        )
        assert (low, high) == (MILLION * 57823, MILLION * 59000), (name, facts)
        assert not any(member.endswith("_ForecastMember") and local == "売上高" for local, member in facts), name


def test_kab245_dividend_yen_paren_year_end_is_per_share() -> None:
    """KAB-245: 親見出し「年間配当金（円）」が期末列だけに付き、単位の注記が無い。

    期末 46 / 前回期末 40 は 1 株当たり。隣の「配当金総額（百万円）」は百万円のまま。
    """
    parts = [
        _text(184, 40, "配当予想の修正（増配）に関するお知らせ", 228),
        _text(71, 70, "１．修正の内容", 77),
        _text(321, 88, "年間配当金（円）", 88),
        _text(545, 88, "配当金総額（百万円）", 100),
        _text(225, 106, "第", 11),
        _text(239, 106, "2", 6),
        _text(248, 106, "四半期末", 44),
        _text(354, 106, "期末", 22),
        _text(460, 106, "合計", 22),
        _text(77, 130, "前回予想", 66),
        _cell(282, 130, "40", 13), _text(285, 130, "円", 11),
        _cell(388, 130, "40", 13), _text(391, 130, "円", 11),
        _cell(495, 130, "80", 13), _text(498, 130, "円", 11),
        _cell(640, 130, "100", 30),
        _text(77, 160, "今回修正予想", 66),
        _cell(388, 160, "46", 13), _text(391, 160, "円", 11),
        _cell(495, 160, "86", 13), _text(498, 160, "円", 11),
        _cell(640, 160, "120", 30),
    ]
    d = _disclosure(
        disclosure_no="20260212556179", code="85110",
        type_of_period="FY", type_of_document="DividendForecastRevision",
        disclosed_date=date(2026, 2, 12),
        period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
        fiscal_year_start=date(2025, 4, 1), fiscal_year_end=date(2026, 3, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    year_end = [
        fact for fact in pkg.facts
        if fact.local_name == "年間配当金" and "YearEndMember" in fact.context_ref
    ]
    by_prev = {
        "PreviousMember" in fact.context_ref: (fact.value_num, fact.unit, fact.scale)
        for fact in year_end
    }
    assert by_prev.get(False) == (Decimal("46"), "JPYPerShares", 0), pkg.facts
    assert by_prev.get(True) == (Decimal("40"), "JPYPerShares", 0), pkg.facts
    totals = [
        fact for fact in pkg.facts
        if "配当金総額" in fact.local_name
    ]
    assert totals, pkg.facts
    assert all(fact.unit == "JPY" and fact.scale == 6 for fact in totals), totals
    assert {fact.value_num for fact in totals} >= {MILLION * 100, MILLION * 120}


def test_dividends_are_solo_and_keep_their_slots() -> None:
    """配当は単体の値（ZIP と同じ）。ダッシュだけの「第１四半期末」の列も残し、見出しが隣へ寄らない。
    単体の配当の context が、連結の本表の context を奪わない。"""
    parts = [
        _text(55, 518, "２．配当の状況", 60),
        _text(323, 531, "年間配当金", 50),
        _text(162, 543, "第１四半期末", 50), _text(241, 543, "第２四半期末", 50),
        _text(413, 543, "期末", 20), _text(492, 543, "合計", 20),
        _text(201, 556, "円 銭", 20), _text(280, 556, "円 銭", 20), _text(437, 556, "円 銭", 20),
        _text(516, 556, "円 銭", 20),
        _text(59, 567, "2026年３月期", 60),
        _cell(220, 567, "－", 8), _cell(300, 567, "32.00", 25), _cell(457, 567, "40.00", 25), _cell(536, 567, "72.00", 25),
    ]
    pl = [
        _text(56, 66, "（２）四半期連結損益計算書", 120),
        _text(450, 78, "（単位：百万円）", 70),
        _text(300, 90, "前第１四半期連結累計期間", 110), _text(430, 90, "当第１四半期連結累計期間", 110),
        _text(60, 140, "売上高", 30), _cell(398, 140, "549,586", 40), _cell(523, 140, "732,388", 40),
    ]
    d = _disclosure(type_of_document="1QFinancialStatements_Consolidated_JP")
    pkg = read_tdnet_pdf(_pages(parts, pl), d)
    by_ref = {ctx.context_ref: ctx for ctx in pkg.contexts}
    dps = {fact.context_ref: fact.value_num for fact in pkg.facts if fact.local_name == "年間配当金"}
    assert dps, pkg.facts
    for ref in dps:
        assert by_ref[ref].is_consolidated == 0, ref
    q2 = next(value for ref, value in dps.items() if "SecondQuarterMember" in ref)
    assert q2 == Decimal("32.00")
    assert not any("FirstQuarterMember" in ref for ref in dps)
    sales = next(fact for fact in pkg.facts if fact.local_name == "売上高" and fact.context_ref.startswith("Current"))
    assert by_ref[sales.context_ref].is_consolidated == 1


def test_toc_page_numbers_are_not_summary_values() -> None:
    """業績予想の表の下（次のページ）の目次のページ番号を、予想の列の値にしない。"""
    page1 = _forecast_page([
        _text(88, 707, "通期", 20), _cell(170, 707, "4,500", 30), _cell(260, 707, "450", 30),
    ])
    page2 = [
        _text(64, 100, "当四半期の経営成績の概況……………………………", 200), _cell(260, 100, "2", 5),
        _text(64, 115, "四半期貸借対照表……………………………………", 200), _cell(260, 115, "3", 5),
    ]
    d = _disclosure(type_of_document="1QFinancialStatements_Consolidated_JP")
    values = _values(read_tdnet_pdf(_pages(page1, page2), d))
    assert MILLION * 4500 in values
    assert MILLION * 2 not in values and MILLION * 3 not in values


def test_negative_numbers_shifted_right_stay_in_one_column() -> None:
    """JT: △ の数値だけ右端が 4pt ずれる。1 列を 2 列に割ると前期・当期が 2 つずつになり、表ごと捨てていた。"""
    parts = [
        _text(56, 66, "（２）要約中間連結損益計算書", 140),
        _text(450, 78, "（単位：百万円）", 70),
        _text(340, 90, "前中間会計期間", 70), _text(460, 90, "当中間会計期間", 70),
        _text(330, 100, "(自 2025年１月１日", 80), _text(450, 100, "(自 2026年１月１日", 80),
        _text(330, 110, "至 2025年６月30日)", 80), _text(450, 110, "至 2026年６月30日)", 80),
        _text(60, 140, "売上収益", 40), _cell(418, 140, "1,686,789", 45), _cell(534, 140, "1,986,070", 45),
        _text(60, 152, "売上原価", 40), _cell(422, 152, "△715,439", 45), _cell(538, 152, "△816,081", 45),
        _text(60, 164, "売上総利益", 50), _cell(418, 164, "971,349", 40), _cell(534, 164, "1,169,988", 45),
    ]
    d = _disclosure(
        disclosure_no="20260730502851", code="29140", type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_IFRS",
        period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2026, 1, 1), fiscal_year_end=date(2026, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    assert facts[("売上収益", "CurrentAccumulatedQ2Duration")] == MILLION * 1986070
    # IFRS の費用の △ は ZIP に合わせて正
    assert facts[("売上原価", "CurrentAccumulatedQ2Duration")] == MILLION * 816081
    assert facts[("売上総利益", "CurrentAccumulatedQ2Duration")] == MILLION * 1169988

"""KAB-251: 1 株当たりの円/銭 2 列・「1株当り」・見出しが「円」だけ。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6


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
        "disclosure_no": "20230412545908",
        "code": "36080",
        "disclosed_date": date(2023, 4, 12),
        "disclosed_time": "18:10",
        "type_of_period": "FY",
        "type_of_document": "FYFinancialStatements_Consolidated_JP",
        "period_start": date(2022, 3, 1),
        "period_end": date(2023, 2, 28),
        "fiscal_year_start": date(2022, 3, 1),
        "fiscal_year_end": date(2023, 2, 28),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _per_share(pkg, name: str) -> list[tuple[str, str, Decimal]]:
    return [
        (fact.local_name, fact.context_ref, fact.value_num)
        for fact in pkg.facts
        if name in fact.local_name
    ]


def test_kab251_yen_sen_columns_eps_bps_dividend() -> None:
    """3608 20230412545908: EPS 35円21銭、BPS 1168円69銭、配当 10 円。百万円にしない。"""
    parts = [
        _text(40, 40, "(1)連結経営成績", 140),
        _text(400, 52, "（単位：百万円）", 80),
        _text(180, 70, "１株当たり", 70),
        _text(180, 82, "当期純利益", 70),
        _text(250, 94, "円", 12),
        _text(300, 94, "銭", 12),
        _text(40, 112, "2023年2月期", 80),
        _cell(250, 112, "35", 20),
        _cell(300, 112, "21", 16),
        _text(40, 140, "(2)連結財政状態", 140),
        _text(400, 152, "百万円 円銭", 70),
        _text(180, 168, "１株当たり純資産", 90),
        _text(250, 180, "円", 12),
        _text(300, 180, "銭", 12),
        _text(40, 198, "2023年2月期", 80),
        _cell(250, 198, "1,168", 30),
        _cell(300, 198, "69", 16),
        _text(40, 230, "1.配当の状況", 100),
        _text(400, 242, "円銭 百万円", 70),
        _text(200, 258, "年間配当金", 70),
        _text(220, 272, "期末", 30),
        _text(250, 284, "円", 12),
        _text(300, 284, "銭", 12),
        _text(370, 272, "合計", 30),
        _text(400, 284, "円", 12),
        _text(450, 284, "銭", 12),
        _text(520, 272, "配当金総額（合計）", 90),
        _text(520, 284, "百万円", 40),
        _text(40, 302, "2023年2月期", 80),
        _cell(250, 302, "10", 16),
        _cell(300, 302, "00", 16),
        _cell(400, 302, "10", 16),
        _cell(450, 302, "00", 16),
        _cell(540, 302, "851", 24),
        _text(40, 320, "2024年2月期(予想)", 110),
        _cell(250, 320, "15", 16),
        _cell(300, 320, "00", 16),
        _cell(400, 320, "15", 16),
        _cell(450, 320, "00", 16),
        _cell(540, 320, "―", 16),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure()))
    current = "CurrentYearDuration_ResultMember"
    bps_ctx = "CurrentYearInstant_ResultMember"
    dps_end = "CurrentYearDuration_YearEndMember_NonConsolidated"
    dps_ann = "CurrentYearDuration_AnnualMember_NonConsolidated"
    dps_end_fc = "NextYearDuration_ForecastMember_YearEndMember_NonConsolidated"
    dps_ann_fc = "NextYearDuration_AnnualMember_ForecastMember_NonConsolidated"
    if facts.get(("1株当たり当期純利益", current)) != Decimal("35.21"):
        _fail(facts)
    if facts.get(("1株当たり純資産", bps_ctx)) != Decimal("1168.69"):
        _fail(facts)
    dps_end_v = facts.get(("年間配当金", dps_end), facts.get(("1株当たり配当金", dps_end)))
    dps_ann_v = facts.get(("年間配当金", dps_ann), facts.get(("1株当たり配当金", dps_ann)))
    if dps_end_v != Decimal("10") or dps_ann_v != Decimal("10"):
        _fail(facts)
    if MILLION * 10 in {dps_end_v, dps_ann_v}:
        _fail("dividend scaled as millions")
    fc_end = facts.get(("年間配当金", dps_end_fc), facts.get(("1株当たり配当金", dps_end_fc)))
    fc_ann = facts.get(("年間配当金", dps_ann_fc), facts.get(("1株当たり配当金", dps_ann_fc)))
    if fc_end != Decimal("15") or fc_ann != Decimal("15"):
        _fail(facts)
    if facts.get(("配当金総額(合計)", "CurrentYearDuration_NonConsolidated")) != MILLION * 851:
        _fail(facts)


def test_kab251_atari_yen_header_not_millions() -> None:
    """3447 20250213572510: 「基本的1株当り当期利益」「円」。71.96 を百万円にしない。"""
    parts = [
        _text(40, 40, "業績予想の修正について", 180),
        _text(400, 55, "（単位：百万円）", 80),
        _cell(220, 76, "売上収益", 50),
        _cell(220, 90, "百万円", 40),
        _text(300, 76, "基本的", 40),
        _text(345, 76, "1", 8),
        _text(355, 76, "株当り", 40),
        _text(300, 90, "当期利益", 50),
        _text(360, 90, "円", 12),
        _text(40, 110, "今回修正予想（B）", 100),
        _cell(220, 110, "16,500", 40),
        _cell(380, 110, "71.96", 40),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20250213572510", code="34470",
        disclosed_date=date(2025, 2, 14), disclosed_time="13:00",
        type_of_document="EarnForecastRevision",
        period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
        fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
    )))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上収益", ctx)) != MILLION * 16500:
        _fail(facts)
    eps = facts.get(("基本的1株当たり当期利益", ctx))
    if eps != Decimal("71.96"):
        _fail(facts)
    if any(value >= MILLION for (name, _ref), value in facts.items() if "株" in name):
        _fail(facts)


def test_kab251_yen_sen_columns_do_not_conflict_drop() -> None:
    """3944 20250131559503: 円列 222 と銭列 81 を 222.81 にし、両方捨てない。"""
    parts = [
        _text(40, 40, "連結業績予想の修正に関するお知らせ", 220),
        _text(40, 58, "2024年12月期通期連結業績予想の修正（2024年1月1日～2024年12月31日）", 360),
        _text(400, 72, "（単位：百万円）", 80),
        _cell(220, 90, "売上高", 40),
        _cell(220, 104, "百万円", 40),
        _text(300, 90, "１株当たり", 70),
        _text(300, 104, "当期純利益", 70),
        _text(360, 118, "円", 12),
        _text(410, 118, "銭", 12),
        _text(40, 140, "今回修正予想（B）", 100),
        _cell(220, 140, "18,102", 40),
        _cell(360, 140, "222", 24),
        _cell(410, 140, "81", 16),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20250131559503", code="39440",
        disclosed_date=date(2025, 1, 31), disclosed_time="16:00",
        type_of_document="EarnForecastRevision",
        period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
        fiscal_year_start=date(2024, 1, 1), fiscal_year_end=date(2024, 12, 31),
    )))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上高", ctx)) != MILLION * 18102:
        _fail(facts)
    if facts.get(("1株当たり当期純利益", ctx)) != Decimal("222.81"):
        _fail(facts)


def test_kab251_split_yen_sen_paren_headers() -> None:
    """7590 20240223542040: 「（円」「銭）」に分かれた FEPS を -4.45 として読む。"""
    parts = [
        _text(40, 40, "業績予想の修正について", 180),
        _text(40, 58, "2024年1月期通期連結業績予想数値の修正（2023年1月21日～2024年1月20日）", 380),
        _cell(220, 80, "売上高", 40),
        _cell(220, 94, "（百万円）", 50),
        _text(300, 80, "１株当たり", 70),
        _text(300, 94, "当期純利益", 70),
        _text(360, 108, "（円", 20),
        _text(410, 108, "銭）", 20),
        _text(40, 130, "今回修正予想 (B)", 100),
        _cell(220, 130, "19,411", 40),
        _cell(360, 130, "△4", 20),
        _cell(410, 130, "45", 16),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20240223542040", code="75900",
        disclosed_date=date(2024, 2, 23), disclosed_time="15:00",
        type_of_document="EarnForecastRevision",
        period_start=date(2023, 2, 1), period_end=date(2024, 1, 31),
        fiscal_year_start=date(2023, 2, 1), fiscal_year_end=date(2024, 1, 31),
    )))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上高", ctx)) != MILLION * 19411:
        _fail(facts)
    if facts.get(("1株当たり当期純利益", ctx)) != Decimal("-4.45"):
        _fail(facts)


def test_kab251_combined_yen_sen_aligns_with_undecided() -> None:
    """9507 20230529585548: 「15円00銭」の右端を円の数字に合わせ、未定の列と揃えて配当 15 / 15 / 30。"""
    parts = [
        _text(40, 40, "業績予想および配当予想の修正に関するお知らせ", 280),
        _text(40, 70, "●配当予想の修正", 120),
        _text(336, 90, "年間配当金", 55),
        _text(208, 104, "第", 10),
        _text(221, 104, "2", 8),
        _text(228, 104, "四半期末", 45),
        _text(346, 104, "期", 15),
        _text(366, 104, "末", 12),
        _text(469, 104, "合", 15),
        _text(489, 104, "計", 12),
        _text(55, 130, "前回予想", 50),
        _text(225, 130, "未定", 25),
        _text(349, 130, "未定", 25),
        _text(473, 130, "未定", 25),
        _text(55, 160, "今回修正予想", 70),
        _text(240, 160, "15", 10),
        _text(252, 160, "円", 10),
        _text(265, 160, "00", 10),
        _text(277, 160, "銭", 15),
        _text(364, 160, "15", 10),
        _text(377, 160, "円", 10),
        _text(389, 160, "00", 10),
        _text(402, 160, "銭", 15),
        _text(490, 160, "30", 10),
        _text(503, 160, "円", 10),
        _text(515, 160, "00", 10),
        _text(528, 160, "銭", 15),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20230529585548", code="95070",
        disclosed_date=date(2023, 5, 30), disclosed_time="15:00",
        type_of_document="EarnForecastRevision",
        period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
        fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
    )))
    q2 = "CurrentYearDuration_CurrentMember_ForecastMember_SecondQuarterMember_NonConsolidated"
    end = "CurrentYearDuration_CurrentMember_ForecastMember_YearEndMember_NonConsolidated"
    ann = "CurrentYearDuration_AnnualMember_CurrentMember_ForecastMember_NonConsolidated"
    got = {
        "q2": facts.get(("年間配当金", q2), facts.get(("1株当たり配当金", q2))),
        "end": facts.get(("年間配当金", end), facts.get(("1株当たり配当金", end))),
        "ann": facts.get(("年間配当金", ann), facts.get(("1株当たり配当金", ann))),
    }
    if got != {"q2": Decimal("15"), "end": Decimal("15"), "ann": Decimal("30")}:
        _fail((got, facts))


def test_kab251_split_ichi_kabu_atari_keeps_q3_feps() -> None:
    """7865 20241126529281: 「1」「株当たり」が上段に分かれ、Q3 の EPS 予想と前回予想が残る。

    前期実績 65.53 の右端が △41.80 より短いと 2 列になり、未定の無い束ねが「1」を末尾へ回す。
    """
    parts = [
        _text(40, 40, "2025年1月期第3四半期累計期間業績予想の修正等（2024年1月21日～2024年10月20日）", 400),
        _text(484.1, 70, "1", 4.0),
        _text(490.2, 70, "株当たり", 35.9),
        _text(226.7, 82, "売上高", 35.0),
        _text(285.4, 82, "営業利益", 45.0),
        _text(349.2, 82, "経常利益", 44.9),
        _text(412.1, 82, "四半期純利益", 48.1),
        _text(479.0, 88, "四半期純利益", 52.0),
        _text(236.3, 100, "百万円", 29.9),
        _text(300.1, 100, "百万円", 29.9),
        _text(368.8, 100, "百万円", 29.9),
        _text(432.7, 100, "百万円", 29.9),
        _text(509.9, 100, "円銭", 19.9),
        _text(40, 118, "前回予想（A）", 90),
        _cell(259.2, 118, "1,249", 30),
        _cell(323.0, 118, "△179", 30),
        _cell(386.8, 118, "△176", 30),
        _cell(446.3, 118, "△183", 25),
        _cell(530.5, 118, "△41.80", 40.1),
        _text(40, 136, "今回修正（B）", 90),
        _cell(254.2, 136, "1,244", 25),
        _cell(318.0, 136, "△114", 25),
        _cell(381.8, 136, "△112", 25),
        _cell(451.2, 136, "△120", 30),
        _cell(530.3, 136, "△27.49", 39.8),
        _text(40, 154, "（ご参考）前期第3四半期実績", 160),
        _cell(259.2, 154, "4,392", 30),
        _cell(317.9, 154, "393", 20),
        _cell(381.7, 154, "411", 20),
        _cell(446.2, 154, "287", 20),
        _cell(520.6, 154, "65.53", 30),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20241126529281", code="78650",
        disclosed_date=date(2024, 11, 26), disclosed_time="16:30",
        type_of_period="3Q", type_of_document="EarnForecastRevision",
        period_start=date(2024, 1, 21), period_end=date(2024, 10, 20),
        fiscal_year_start=date(2024, 1, 21), fiscal_year_end=date(2025, 1, 20),
    )))
    current = next(
        ((name, ref) for name, ref in facts if name == "1株当たり当期純利益" and "CurrentMember" in ref and "ForecastMember" in ref),
        None,
    )
    previous = next(
        ((name, ref) for name, ref in facts if name == "1株当たり当期純利益" and "PreviousMember" in ref),
        None,
    )
    if current is None or facts[current] != Decimal("-27.49"):
        _fail(facts)
    if previous is None or facts[previous] != Decimal("-41.80"):
        _fail(facts)
    if any(name.endswith("1") and "株" in name for name, _ref in facts):
        _fail(facts)


def test_kab251_drops_implausible_per_share() -> None:
    """1 株当たり 1e5 以上は入れない。"""
    parts = [
        _text(40, 40, "(1)連結経営成績", 140),
        _text(180, 70, "１株当たり当期純利益", 120),
        _text(250, 84, "円", 12),
        _text(40, 110, "2023年2月期", 80),
        _cell(250, 110, "200000", 40),
        _text(40, 140, "(2)連結財政状態", 140),
        _text(400, 152, "（単位：百万円）", 80),
        _text(180, 160, "連結純資産", 60),
        _text(180, 174, "百万円", 40),
        _text(40, 196, "2023年2月期", 80),
        _cell(250, 196, "98,878", 40),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure()))
    if any("株" in name for name, _ref in facts):
        _fail(facts)
    if facts.get(("純資産", "CurrentYearInstant_ResultMember")) != MILLION * 98878:
        _fail(facts)

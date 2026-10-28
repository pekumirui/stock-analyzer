"""KAB-224 / KAB-234 / KAB-237: 短信 PDF の見出し判定。<text> の座標と golden HTML で再現する。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import gzip
import html
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6
GOLDEN_HTML = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "html"
EXPECTED = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "expected.json"


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
        "disclosure_no": "20240508584856",
        "code": "45020",
        "disclosed_date": date(2024, 5, 9),
        "disclosed_time": "15:00",
        "type_of_period": "FY",
        "type_of_document": "FYFinancialStatements_Consolidated_IFRS",
        "period_start": date(2023, 4, 1),
        "period_end": date(2024, 3, 31),
        "fiscal_year_start": date(2023, 4, 1),
        "fiscal_year_end": date(2024, 3, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _golden(no: str) -> tuple[str, PdfDisclosure]:
    html_text = gzip.decompress((GOLDEN_HTML / f"{no}.html.gz").read_bytes()).decode("utf-8")
    doc = json.loads(EXPECTED.read_text(encoding="utf-8"))["disclosures"][no]
    st = doc["statement"]
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        **{k: (date.fromisoformat(v) if k.endswith(("_date", "_start", "_end")) else v) for k, v in st.items()},
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return html_text, parsed


def test_kab224_long_eps_header_line_is_not_prose() -> None:
    """4502 20240508584856: EPS 見出し行が短い語の並びで 40 字を超えても列見出しのまま。"""
    # compact は 42 字。語はどれも 8 字以下（KAB-216 の 1 語の注記は 40 字超のまま文章）。
    parts = [
        _text(40, 40, "（1）連結経営成績", 120),
        _cell(200, 60, "売上収益", 50),
        _text(40, 78, "百万円", 40),
        _text(40, 96, "2024年３月期", 80),
        _cell(200, 96, "4,263,762", 50),
        _text(40, 120, "円銭", 30),
        _text(140, 138, "基本的", 40),
        _text(230, 138, "希薄化後", 50),
        _text(130, 156, "１株当たり", 50),
        _text(220, 156, "１株当たり", 50),
        _text(360, 156, "帰属持分", 40),
        _text(420, 156, "税引前", 30),
        _text(470, 156, "営業", 20),
        _text(510, 156, "Core売上収益", 70),
        _text(590, 156, "Core営業利益", 70),
        _text(670, 156, "Core", 30),
        _text(710, 156, "EPS", 24),
        _text(130, 174, "当期利益", 50),
        _text(220, 174, "当期利益", 50),
        _text(40, 200, "2024年３月期", 80),
        _cell(180, 200, "92.09", 40),
        _cell(280, 200, "91.16", 40),
    ]
    pkg = read_tdnet_pdf(_pages(parts), _disclosure())
    facts = _facts(pkg)
    basic = ("基本的1株当たり当期利益", "CurrentYearDuration_ResultMember")
    diluted = ("希薄化後1株当たり当期利益", "CurrentYearDuration_ResultMember")
    if facts.get(basic) != Decimal("92.09"):
        _fail(facts)
    if facts.get(diluted) != Decimal("91.16"):
        _fail(facts)


def test_kab234_circled_bs_pl_headings() -> None:
    """8628 20240722552090: 「①四半期貸借対照表」「②四半期損益計算書」を本表として読む。"""
    parts = [
        _text(40, 16, "1.経営成績等の概況", 120),
        _text(40, 40, "①四半期貸借対照表", 120),
        _text(40, 58, "(単位：百万円)", 80),
        _text(200, 76, "前事業年度", 60),
        _text(320, 76, "当第1四半期会計期間", 110),
        _text(200, 94, "(2024年3月31日)", 90),
        _text(320, 94, "(2024年6月30日)", 90),
        _text(40, 120, "資産合計", 50),
        _cell(260, 120, "1,172,667", 50),
        _cell(400, 120, "1,179,182", 50),
        _text(40, 140, "純資産合計", 60),
        _cell(260, 140, "76,326", 40),
        _cell(400, 140, "74,181", 40),
        _text(40, 200, "②四半期損益計算書", 120),
        _text(40, 218, "(単位：百万円)", 80),
        _text(200, 236, "前第1四半期累計期間", 110),
        _text(350, 236, "当第1四半期累計期間", 110),
        _text(40, 260, "営業利益", 50),
        _cell(260, 260, "3,596", 40),
        _cell(420, 260, "4,235", 40),
        _text(40, 280, "経常利益", 50),
        _cell(260, 280, "3,594", 40),
        _cell(420, 280, "4,225", 40),
        _text(40, 300, "四半期純利益", 70),
        _cell(260, 300, "2,360", 40),
        _cell(420, 300, "2,894", 40),
    ]
    d = _disclosure(
        disclosure_no="20240722552090", code="86280",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_NonConsolidated_JP",
        disclosed_date=date(2024, 7, 29),
        period_start=date(2024, 4, 1), period_end=date(2024, 6, 30),
        fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    if facts.get(("資産合計", "CurrentYearInstant_NonConsolidated")) != MILLION * 1179182:
        _fail(facts)
    if facts.get(("純資産合計", "CurrentYearInstant_NonConsolidated")) != MILLION * 74181:
        _fail(facts)
    if facts.get(("営業利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 4235:
        _fail(facts)
    if facts.get(("経常利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 4225:
        _fail(facts)
    if facts.get(("当期純利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 2894:
        _fail(facts)


def test_kab237_interim_dividend_notice() -> None:
    """8628 20220825523972: 「中間配当（予定）について」。行名は数値の下、未定は欠け。"""
    parts = [
        _text(40, 40, "2023年3月期中間配当（予定）について", 260),
        _text(200, 70, "1株当たり配当金", 90),
        _cell(300, 90, "中間期末", 50),
        _cell(420, 90, "期末", 30),
        _cell(540, 90, "年間", 30),
        _text(40, 112, "2023年3月期", 80),
        _cell(280, 120, "未定", 30),
        _cell(400, 120, "未定", 30),
        _cell(520, 120, "未定", 30),
        _text(40, 128, "前回予定", 60),
        _text(40, 148, "2023年3月期", 80),
        _cell(280, 155, "20", 20),
        _text(282, 155, "円", 12),
        _text(296, 155, "00", 16),
        _text(314, 155, "銭", 12),
        _cell(400, 155, "未定", 30),
        _cell(520, 155, "未定", 30),
        _text(40, 165, "今回予定", 60),
        _text(40, 185, "2022年3月期", 80),
        _cell(280, 192, "20", 20),
        _text(282, 192, "円", 12),
        _text(296, 192, "00", 16),
        _text(314, 192, "銭", 12),
        _cell(400, 192, "20", 20),
        _text(402, 192, "円", 12),
        _text(416, 192, "00", 16),
        _text(434, 192, "銭", 12),
        _cell(520, 192, "40", 20),
        _text(522, 192, "円", 12),
        _text(536, 192, "00", 16),
        _text(554, 192, "銭", 12),
        _text(40, 202, "実績", 40),
    ]
    d = _disclosure(
        disclosure_no="20220825523972", code="86280",
        type_of_period="FY", type_of_document="DividendForecastRevision",
        disclosed_date=date(2022, 8, 25),
        period_start=date(2022, 4, 1), period_end=date(2023, 3, 31),
        fiscal_year_start=date(2022, 4, 1), fiscal_year_end=date(2023, 3, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    q2 = [
        fact for fact in pkg.facts
        if "SecondQuarterMember" in fact.context_ref and "ForecastMember" in fact.context_ref
    ]
    if len(q2) != 1 or q2[0].value_num != Decimal("20"):
        _fail([(f.local_name, f.context_ref, f.value_num) for f in pkg.facts])
    if any("ResultMember" in fact.context_ref for fact in pkg.facts):
        _fail("実績を入れた")


def test_kab237_planned_dividend_amount_heading() -> None:
    """予定配当額について（短信の配当の状況ではない）。"""
    parts = [
        _text(40, 40, "予定配当額について", 140),
        _cell(300, 70, "中間期末", 50),
        _text(40, 100, "今回予定", 60),
        _cell(280, 100, "18", 20),
        _text(282, 100, "円", 12),
        _text(296, 100, "00", 16),
        _text(314, 100, "銭", 12),
    ]
    d = _disclosure(
        disclosure_no="20210826490476", code="86280",
        type_of_period="FY", type_of_document="DividendForecastRevision",
        disclosed_date=date(2021, 8, 26),
        period_start=date(2021, 4, 1), period_end=date(2022, 3, 31),
        fiscal_year_start=date(2021, 4, 1), fiscal_year_end=date(2022, 3, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    q2 = [
        fact for fact in pkg.facts
        if "SecondQuarterMember" in fact.context_ref and "ForecastMember" in fact.context_ref
    ]
    if len(q2) != 1 or q2[0].value_num != Decimal("18"):
        _fail([(f.local_name, f.context_ref, f.value_num) for f in pkg.facts])


def test_kab224_takeda_fy_summary_eps_from_golden() -> None:
    """20220510538507（golden）の FY EPS/DEPS が残る。"""
    html_text, d = _golden("20220510538507")
    facts = _facts(read_tdnet_pdf(html_text, d))
    if facts.get(("基本的1株当たり当期利益", "CurrentYearDuration_ResultMember")) != Decimal("147.14"):
        _fail(facts)
    if facts.get(("希薄化後1株当たり当期利益", "CurrentYearDuration_ResultMember")) != Decimal("145.87"):
        _fail(facts)


def test_kab234_matsui_bs_pl_from_golden() -> None:
    """20240722552090（golden）: ① 付き本表から Eq/TA/OP/OdP/NP。"""
    html_text, d = _golden("20240722552090")
    facts = _facts(read_tdnet_pdf(html_text, d))
    if facts.get(("資産合計", "CurrentYearInstant_NonConsolidated")) != MILLION * 1179182:
        _fail(facts)
    if facts.get(("純資産合計", "CurrentYearInstant_NonConsolidated")) != MILLION * 74181:
        _fail(facts)
    if facts.get(("営業利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 4235:
        _fail(facts)
    if facts.get(("経常利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 4225:
        _fail(facts)
    if facts.get(("当期純利益", "CurrentAccumulatedQ1Duration_NonConsolidated")) != MILLION * 2894:
        _fail(facts)


def test_kab237_matsui_dividend_from_golden() -> None:
    """20220825523972（golden）: 中間配当（予定）の今回予定 20 円。"""
    html_text, d = _golden("20220825523972")
    pkg = read_tdnet_pdf(html_text, d)
    q2 = [
        fact for fact in pkg.facts
        if "SecondQuarterMember" in fact.context_ref and "ForecastMember" in fact.context_ref
    ]
    if not q2 or q2[0].value_num != Decimal("20"):
        _fail([(f.local_name, f.context_ref, f.value_num) for f in pkg.facts])
    if any("ResultMember" in fact.context_ref for fact in pkg.facts):
        _fail("実績を入れた")

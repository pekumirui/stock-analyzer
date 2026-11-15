"""KAB-226 / KAB-235: 訂正短信の判定と、訂正理由書のあとのサマリー。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。対象開示は golden HTML。
"""
from __future__ import annotations

import gzip
import html
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import XbrlFiling
from app.xbrl.pdf import (
    PdfDisclosure,
    is_amendment_notice,
    read_pages,
    read_tdnet_pdf,
)
from tools.tdnet_pdf.golden_check import MINI_HTML, compare, dump_in_process, load_expected

MILLION = Decimal(10) ** 6
OKU = Decimal(10) ** 8
GOLDEN_HTML = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "html"
EXPECTED = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "expected.json"

KAB226 = "20260605563772"  # 4502 「…決算短信…」の修正について
KAB235_NABTESCO = "20260814521255"  # 6268 訂正・数値データ訂正
KAB235_TEPCO = "20260212557842"  # 9501 開示事項の変更
TARGETS = (KAB226, KAB235_NABTESCO, KAB235_TEPCO)


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
        "disclosure_no": "20260605563772",
        "code": "45020",
        "disclosed_date": date(2026, 6, 5),
        "disclosed_time": "15:30",
        "type_of_period": "FY",
        "type_of_document": "FYFinancialStatements_Consolidated_JP",
        "period_start": date(2025, 4, 1),
        "period_end": date(2026, 3, 31),
        "fiscal_year_start": date(2025, 4, 1),
        "fiscal_year_end": date(2026, 3, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _empty_pages():
    return read_pages(_pages([_text(40, 40, "各位", 40)]))


def _summary_sales(label: str, amount: str) -> list[str]:
    return [
        _text(40, 40, label, 280),
        _text(40, 80, "(1)連結経営成績", 120),
        _text(40, 100, "（単位：百万円）", 100),
        _cell(400, 120, "売上高", 50),
        _text(40, 140, "2026年3月期", 80),
        _cell(400, 140, amount, 60),
    ]


def _golden(no: str) -> tuple[str, PdfDisclosure, str]:
    html_text = gzip.decompress((GOLDEN_HTML / f"{no}.html.gz").read_bytes()).decode("utf-8")
    doc = json.loads(EXPECTED.read_text(encoding="utf-8"))["disclosures"][no]
    st = doc["statement"]
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        **{k: (date.fromisoformat(v) if k.endswith(("_date", "_start", "_end")) else v) for k, v in st.items()},
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return html_text, parsed, doc["title"]


def _facts(pkg) -> dict[tuple[str, str], object]:
    return {(fact.local_name, fact.context_ref): fact for fact in pkg.facts}


def test_kab226_tanshin_revision_title_is_amendment() -> None:
    """「…決算短信…の修正について」は訂正。業績予想・配当の修正や通常の短信は違う。"""
    pages = _empty_pages()
    if not is_amendment_notice("「2026年３月期決算短信〔IFRS〕（連結）」の修正について", pages):
        _fail("4502 title")
    if not is_amendment_notice("2026年3月期決算短信の一部修正", pages):
        _fail("一部修正")
    if is_amendment_notice("2026年３月期決算短信〔IFRS〕（連結）", pages):
        _fail("通常の短信")
    if is_amendment_notice("2025年12月期通期連結業績予想の修正に関するお知らせ", pages):
        _fail("業績予想の修正")
    if is_amendment_notice("業績予想の修正について", pages):
        _fail("業績予想の修正について")
    if is_amendment_notice("配当予想の修正について", pages):
        _fail("配当予想の修正について")
    if not is_amendment_notice("「2026年３月期 第３四半期決算短信〔ＩＦＲＳ〕（非連結）」の一部追加のお知らせ", pages):
        _fail("一部追加")
    if not is_amendment_notice("(追加・数値データ追加)「2026年４月期 第３四半期決算短信〔ＩＦＲＳ〕（非連結）」", pages):
        _fail("数値データ追加")
    if is_amendment_notice("追加型投資信託の決算のお知らせ", pages):
        _fail("決算短信でない追加")


def test_kab235_change_and_teiseiban_markers_are_amendment() -> None:
    """題名に「訂正」が無くても、<変更前>/<変更後> や（訂正版）なら訂正。"""
    henkou = read_pages(_pages(
        [_text(40, 40, "2026年３月期第３四半期決算短信〔日本基準〕（連結）", 280), _text(40, 80, "＜変更前＞", 60)],
        [_text(40, 40, "＜変更後＞", 60)],
    ))
    if not is_amendment_notice("2026年３月期第３四半期決算短信〔日本基準〕（連結）", henkou):
        _fail("変更前/後")
    teiseiban = read_pages(_pages([_text(40, 40, "（訂正版）", 60), _text(40, 60, "2026年12月期第２四半期決算短信〔ＩＦＲＳ〕（連結）", 280)]))
    if not is_amendment_notice("2026年12月期第２四半期決算短信〔ＩＦＲＳ〕（連結）", teiseiban):
        _fail("訂正版")


def test_kab226_summary_after_two_cover_pages() -> None:
    """訂正理由の 2 ページのあと、3 ページ目のサマリー（百万円）を読む。"""
    html_text = _pages(
        [_text(40, 40, "「2026年3月期決算短信〔日本基準〕（連結）」の修正について", 320)],
        [_text(40, 40, "以上", 40)],
        _summary_sales("2026年3月期決算短信〔日本基準〕（連結）", "4,612,176"),
        [_text(40, 40, "注記", 40)],
        _summary_sales("目次のあと", "9,999,999"),
    )
    d = _disclosure(type_of_document="FYFinancialStatements_Consolidated_JP")
    pkg = read_tdnet_pdf(html_text, d)
    sales = [
        fact for fact in pkg.facts
        if fact.local_name == "売上高" and fact.namespace_key == "pdf-sum" and "Prior" not in fact.context_ref
    ]
    if len(sales) != 1 or sales[0].value_num != MILLION * Decimal("4612176"):
        _fail([(f.local_name, f.value_num, f.scale, f.unit) for f in pkg.facts])
    if sales[0].unit != "JPY" or sales[0].scale != 6:
        _fail(("unit", sales[0].unit, sales[0].scale))
    if any(fact.value_num == MILLION * Decimal("9999999") for fact in pkg.facts):
        _fail("page after summary window")
    if not is_amendment_notice("「2026年3月期決算短信〔日本基準〕（連結）」の修正について", read_pages(html_text)):
        _fail("cover title")


def test_regular_tanshin_still_drops_summary_on_page_three() -> None:
    """通常の短信は 1〜2 ページ目だけ。3 ページ目のサマリー見出しは読まない。"""
    html_text = _pages(
        _summary_sales("2026年3月期決算短信〔日本基準〕（連結）", "1,000"),
        [_text(40, 40, "注記", 40)],
        _summary_sales("本文", "9,999,999"),
    )
    d = _disclosure(
        disclosure_no="20220428530699",
        type_of_document="FYFinancialStatements_Consolidated_JP",
    )
    pkg = read_tdnet_pdf(html_text, d)
    if is_amendment_notice("2026年3月期決算短信〔日本基準〕（連結）", read_pages(html_text)):
        _fail("regular title")
    values = {fact.value_num for fact in pkg.facts if fact.local_name == "売上高"}
    if MILLION * Decimal("1000") not in values:
        _fail(values)
    if MILLION * Decimal("9999999") in values:
        _fail("late summary kept")


def test_kab226_235_target_html_is_amendment_and_reads_summary() -> None:
    """対象 3 開示: 訂正扱い。サマリーの百万円・円銭・% が J-Quants と一致する。"""
    cases = (
        (
            KAB226,
            ("基本的1株当たり当期利益", "CurrentYearDuration_ResultMember", Decimal("-96.75"), "JPYPerShares", 0),
            ("売上収益", "CurrentYearDuration_ResultMember", MILLION * Decimal("4505720"), "JPY", 6),
            ("親会社所有者帰属持分比率", "CurrentYearInstant_ResultMember", Decimal("0.479"), "pure", -2),
            ("Core売上収益", "CurrentYearDuration_ResultMember", OKU * Decimal("45057"), "JPY", 8),
        ),
        (
            KAB235_NABTESCO,
            ("基本的1株当たり当期利益", "CurrentAccumulatedQ2Duration_ResultMember", Decimal("106.78"), "JPYPerShares", 0),
            ("売上高", "CurrentAccumulatedQ2Duration_ResultMember", MILLION * Decimal("167401"), "JPY", 6),
            ("親会社所有者帰属持分比率", "CurrentYearInstant_ResultMember", Decimal("0.624"), "pure", -2),
        ),
        (
            KAB235_TEPCO,
            ("1株当たり当期純利益", "CurrentAccumulatedQ3Duration_ResultMember", Decimal("-413.61"), "JPYPerShares", 0),
            ("売上高", "CurrentAccumulatedQ3Duration_ResultMember", MILLION * Decimal("4612176"), "JPY", 6),
            ("自己資本比率", "CurrentYearInstant_ResultMember", Decimal("0.206"), "pure", -2),
        ),
    )
    for no, *want in cases:
        html_text, d, title = _golden(no)
        pages = read_pages(html_text)
        if not is_amendment_notice(title, pages):
            _fail(f"amendment {no} {title}")
        facts = _facts(read_tdnet_pdf(html_text, d))
        for name, ref, value, unit, scale in want:
            fact = facts.get((name, ref))
            if fact is None or fact.value_num != value or fact.unit != unit or fact.scale != scale:
                _fail(f"{no} {name} {fact}")


def test_kab226_235_jquants_gaps_and_is_amendment() -> None:
    """golden HTML を sync し、J-Quants の欠けが埋まり、単位付きで一致する。訂正フラグ。"""
    data = load_expected()
    result = dump_in_process(data, [MINI_HTML], list(TARGETS))
    if any(result["outcomes"].get(no) != "parsed" for no in TARGETS):
        _fail(result["outcomes"])
    subset = {"disclosures": {no: data["disclosures"][no] for no in TARGETS}}
    report = compare(subset, result)
    if report.lost or report.changed or report.gaps_wrong:
        _fail(f"lost {report.lost[:3]} changed {report.changed[:3]} wrong {report.gaps_wrong[:3]}")
    if len(report.fixed) < 30:
        _fail(f"fixed {len(report.fixed)} {[g['jq_key'] for g in report.fixed]}")
    by_no: dict[str, list[dict]] = {}
    for row in result["rows"]:
        by_no.setdefault(row["no"], []).append(row)

    def find(no: str, std: str, kind: str, period: str, *, consolidated: bool = True) -> dict:
        hits = [
            row for row in by_no[no]
            if row["std"] == std and row["value_kind"] == kind and row["target_period"] == period
            and row["range_side"] == "point" and row["is_consolidated"] is consolidated
        ]
        if not hits:
            _fail(f"missing {no} {std} {kind} {period} cons={consolidated}")
        return hits[0]

    sales = find(KAB226, "net_sales", "actual", "FY")
    if sales["unit"] != "JPY" or Decimal(sales["value"]) != MILLION * Decimal("4505720"):
        _fail(sales)
    eps = find(KAB226, "eps_basic", "actual", "FY")
    if eps["unit"] != "JPYPerShares" or Decimal(eps["value"]) != Decimal("-96.75"):
        _fail(eps)
    eqar = find(KAB226, "equity_ratio", "actual", "FY")
    if eqar["unit"] != "ratio" or Decimal(eqar["value"]) != Decimal("0.479"):
        _fail(eqar)
    nab_sales = find(KAB235_NABTESCO, "net_sales", "actual", "Q2")
    if nab_sales["unit"] != "JPY" or Decimal(nab_sales["value"]) != MILLION * Decimal("167401"):
        _fail(nab_sales)
    tepco_eps = find(KAB235_TEPCO, "eps_basic", "actual", "Q3")
    if tepco_eps["unit"] != "JPYPerShares" or Decimal(tepco_eps["value"]) != Decimal("-413.61"):
        _fail(tepco_eps)

    db = SessionLocal()
    try:
        for no in TARGETS:
            filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
            if filing.is_amendment is not True:
                _fail(f"is_amendment {no}")
    finally:
        db.close()


def test_kab212_correction_markers_still_amendment() -> None:
    """KAB-212 の（訂正前）（訂正後）はこれまでどおり訂正。"""
    pages = read_pages(_pages([
        _text(40, 40, "（訂正前）", 60),
        _text(40, 80, "（訂正後）", 60),
    ]))
    if not is_amendment_notice("2024年8月期決算短信の一部訂正について", _empty_pages()):
        _fail("訂正 in title")
    if not is_amendment_notice("お知らせ", pages):
        _fail("訂正前/後")

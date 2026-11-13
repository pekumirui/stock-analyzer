"""KAB-452: BS・PL・CF の行名の「金銭」を 1 株当たりの単位にしない。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。
"""
from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf

MILLION = Decimal(10) ** 6


def _text(x: float, y: float, text: str, width: float) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 50) -> str:
    return _text(x1 - width, y, text, width)


def _disclosure() -> PdfDisclosure:
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no="20260730502846",
        code="83040",
        disclosed_date=date(2026, 7, 30),
        disclosed_time="15:30",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_JP",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 6, 30),
        fiscal_year_start=date(2026, 4, 1),
        fiscal_year_end=date(2027, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def test_kab452_money_trust_rows_keep_table_unit() -> None:
    """8304 20260730502846: 「買入金銭債権 51,312」「金銭の信託 10,262」は表の単位（百万円）。"""
    rows = [
        ("現金預け金", "900,000", "912,345"),
        ("買入金銭債権", "50,000", "51,312"),
        ("金銭の信託", "10,000", "10,262"),
        ("有価証券", "700,000", "712,000"),
    ]
    parts = [
        _text(40, 40, "（１）四半期連結貸借対照表", 180),
        _text(480, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 100),
        _cell(560, 76, "当第１四半期連結会計期間", 140),
        _text(40, 94, "資産の部", 60),
    ]
    for n, (label, prior, current) in enumerate(rows):
        y = 112 + 18 * n
        parts += [_text(50, y, label, 14 * len(label)), _cell(400, y, prior), _cell(560, y, current)]
    page = f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg></body></html>'
    facts = {f.local_name: f for f in read_tdnet_pdf(page, _disclosure()).facts if f.context_ref.startswith("Current")}
    for name, value in (("現金預け金", 912345), ("買入金銭債権", 51312), ("金銭の信託", 10262), ("有価証券", 712000)):
        assert name in facts, (name, sorted(facts))
        assert facts[name].unit == "JPY", (name, facts[name].unit)
        assert facts[name].value_num == MILLION * value, (name, facts[name].value_num)


"""KAB-432: PL の 1 株当たりの見出しの下に続く「希薄化後」などを 1 株当たりとして読む。

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
        disclosure_no="20251028579861",
        code="68570",
        disclosed_date=date(2025, 10, 28),
        disclosed_time="15:30",
        type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_IFRS",
        period_start=date(2025, 4, 1),
        period_end=date(2025, 9, 30),
        fiscal_year_start=date(2025, 4, 1),
        fiscal_year_end=date(2026, 3, 31),
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _pl(rows: list[tuple[str, str | None, str | None]]) -> str:
    parts = [
        _text(40, 40, "（２）要約中間連結損益計算書", 180),
        _text(480, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前中間連結会計期間", 100),
        _cell(560, 76, "当中間連結会計期間", 100),
    ]
    for n, (label, prior, current) in enumerate(rows):
        y = 94 + 18 * n
        parts.append(_text(50, y, label, 14 * len(label)))
        if prior is not None:
            parts += [_cell(400, y, prior), _cell(560, y, current or "")]
    return f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg></body></html>'


def _facts(page: str) -> set[tuple[str, str, str, Decimal]]:
    return {
        (f.local_name, f.context_ref, f.unit, f.value_num)
        for f in read_tdnet_pdf(page, _disclosure()).facts
        if f.namespace_key == "pdf-pl"
    }


def test_kab432_diluted_under_per_share_heading() -> None:
    """6857 20251028579861: 見出し「1株当たり中間利益」の下の「基本的」「希薄化後」。"""
    facts = _facts(_pl([
        ("売上収益", "389,425", "481,299"),
        ("中間利益", "69,105", "170,519"),
        ("1株当たり中間利益", None, None),
        ("基本的", "93.92円", "232.19円"),
        ("希薄化後", "93.64円", "231.49円"),
    ]))
    ctx = "CurrentAccumulatedQ2Duration"
    assert facts >= {
        ("売上収益", ctx, "JPY", MILLION * 481299),
        ("1株当たり当期利益基本的", ctx, "JPYPerShares", Decimal("232.19")),
        ("希薄化後", ctx, "JPYPerShares", Decimal("231.49")),
        ("希薄化後", "Prior1AccumulatedQ2Duration", "JPYPerShares", Decimal("93.64")),
    }, facts
    assert not any(name == "希薄化後" and unit == "JPY" for name, _, unit, _ in facts), facts


def test_kab432_continuing_rows_under_per_share() -> None:
    """継続事業・非継続事業・合計は 1 株当たりの行の下だけ 1 株当たり。帰属の内訳の下は金額のまま。"""
    facts = _facts(_pl([
        ("中間利益の帰属", None, None),
        ("親会社の所有者継続事業", "11,495", "41,656"),
        ("非継続事業", "235", "8,291"),
        ("合計", "11,731", "49,947"),
        ("1株当たり中間利益", None, None),
        ("基本的1株当たり中間利益（円）継続事業", "45.06", "167.91"),
        ("非継続事業", "0.92", "33.42"),
        ("合計", "45.98", "201.34"),
        ("希薄化後1株当たり中間利益（円）継続事業", "44.95", "167.52"),
        ("非継続事業", "0.92", "33.34"),
        ("合計", "45.87", "200.87"),
    ]))
    ctx = "CurrentAccumulatedQ2Duration"
    assert facts >= {
        ("非継続事業", ctx, "JPY", MILLION * 8291),
        ("合計", ctx, "JPY", MILLION * 49947),
        ("非継続事業", ctx, "JPYPerShares", Decimal("33.42")),
        ("合計", ctx, "JPYPerShares", Decimal("201.34")),
        ("非継続事業#2", ctx, "JPYPerShares", Decimal("33.34")),
        ("合計#2", ctx, "JPYPerShares", Decimal("200.87")),
    }, facts


def test_kab432_sibling_without_per_share_heading_stays_money() -> None:
    """1 株当たりの行が上に無い「希薄化後」は今までどおり表の単位。"""
    facts = _facts(_pl([
        ("親会社株主に帰属する当期利益", None, None),
        ("基本", "589,896", "615,724"),
        ("希薄化後", "589,896", "615,724"),
    ]))
    assert ("希薄化後", "CurrentAccumulatedQ2Duration", "JPY", MILLION * 615724) in facts, facts

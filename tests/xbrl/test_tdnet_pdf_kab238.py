"""KAB-238: 2 列にまたがる 1 語の見出しを金額列ごとに分ける。

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

from app.xbrl.pdf import PdfDisclosure, read_tdnet_pdf
from tools.tdnet_pdf.golden_check import MINI_HTML, compare, dump_in_process, load_expected

MILLION = Decimal(10) ** 6
GOLDEN_HTML = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "html"
EXPECTED = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "expected.json"
KAB238 = "20260430514688"


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
        "disclosure_no": KAB238,
        "code": "62680",
        "disclosed_date": date(2026, 4, 30),
        "disclosed_time": "16:00",
        "type_of_period": "1Q",
        "type_of_document": "1QFinancialStatements_Consolidated_IFRS",
        "period_start": date(2026, 1, 1),
        "period_end": date(2026, 3, 31),
        "fiscal_year_start": date(2026, 1, 1),
        "fiscal_year_end": date(2026, 12, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], object]:
    return {(fact.local_name, fact.context_ref): fact for fact in pkg.facts}


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


def _spanning_forecast_parts() -> list[str]:
    """6268 20260430514688 の通期・中間予想。上段見出しが NP と EPS の 1 語。"""
    return [
        _text(55.7, 40.0, "3. 連結業績予想", 120.0),
        _text(379.7, 76.0, "親会社の所有者に基本的１株当たり", 144.0),
        _text(186.2, 80.8, "売上高", 27.0),
        _text(253.7, 80.8, "営業利益", 36.0),
        _text(321.2, 80.8, "税引前利益", 45.0),
        _text(379.7, 85.6, "帰属する当期利益", 72.0),
        _text(469.7, 85.6, "当期利益", 36.0),
        _text(176.4, 95.3, "百万円", 22.5),
        _text(226.0, 95.3, "％", 7.5),
        _text(248.4, 95.3, "百万円", 22.5),
        _text(298.0, 95.3, "％", 7.5),
        _text(320.4, 95.3, "百万円", 22.5),
        _text(370.0, 95.3, "％", 7.5),
        _text(392.4, 95.3, "百万円", 22.5),
        _text(442.0, 95.3, "％", 7.5),
        _text(502.7, 95.3, "円", 7.5),
        _text(514.0, 95.3, "銭", 7.5),
        _text(73.7, 106.1, "第２四半期(累計)", 72.0),
        _cell(199.1, 106.1, "157,400", 31.5),
        _cell(233.7, 106.1, "9.9", 13.5),
        _cell(271.1, 106.1, "11,200", 27.0),
        _cell(305.7, 106.1, "21.8", 18.0),
        _cell(343.1, 106.1, "12,000", 27.0),
        _cell(377.7, 106.1, "40.4", 18.0),
        _cell(415.1, 106.1, "6,300", 22.5),
        _cell(449.7, 106.1, "△3.3", 22.5),
        _cell(521.7, 106.1, "53.76", 22.5),
        _text(100.7, 117.1, "通期", 18.0),
        _cell(199.1, 117.1, "327,000", 31.5),
        _cell(233.7, 117.1, "6.2", 13.5),
        _cell(271.1, 117.1, "27,700", 27.0),
        _cell(305.7, 117.1, "33.6", 18.0),
        _cell(343.1, 117.1, "28,600", 27.0),
        _cell(377.7, 117.1, "32.1", 18.0),
        _cell(415.1, 117.1, "18,600", 27.0),
        _cell(449.7, 117.1, "18.5", 18.0),
        _cell(521.7, 117.1, "158.72", 27.0),
    ]


def test_kab238_parent_header_on_adjacent_columns_is_not_split() -> None:
    """隣り合う配当列にまたがる「年間配当金」は分割せず、各列の親見出しのまま。"""
    parts = [
        _text(40.0, 40.0, "1. 配当の状況", 120.0),
        _text(160.0, 58.0, "年間配当金", 280.0),
        _cell(300.0, 76.0, "第3四半期末", 90.0),
        _cell(430.0, 76.0, "期末", 40.0),
        _text(250.0, 94.0, "円 銭", 50.0),
        _text(400.0, 94.0, "円 銭", 50.0),
        _text(40.0, 112.0, "2026年8月期", 110.0),
        _cell(300.0, 112.0, "40", 30.0),
        _cell(430.0, 112.0, "22", 30.0),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        period_start=date(2025, 9, 1), period_end=date(2026, 8, 31),
        fiscal_year_start=date(2025, 9, 1), fiscal_year_end=date(2026, 8, 31),
    )))
    names = {name for name, _ref in facts}
    if names != {"年間配当金"}:
        _fail(f"{facts}")
    q3 = facts.get(("年間配当金", "CurrentYearDuration_ThirdQuarterMember_NonConsolidated"))
    year_end = facts.get(("年間配当金", "CurrentYearDuration_YearEndMember_NonConsolidated"))
    if q3 is None or q3.value_num != Decimal("40"):
        _fail(f"q3 {facts}")
    if year_end is None or year_end.value_num != Decimal("22"):
        _fail(f"year_end {facts}")


def test_kab238_spanning_np_eps_header_splits_to_columns() -> None:
    """％ 列を挟んだ 1 語の「親会社の所有者に基本的１株当たり」を NP 列と EPS 列に分ける。"""
    facts = _facts(read_tdnet_pdf(_pages(_spanning_forecast_parts()), _disclosure()))
    names = {name for name, _ref in facts}
    if "親会社の所有者に帰属する当期利益" not in names:
        _fail(f"np {facts}")
    if "基本的1株当たり当期利益" not in names:
        _fail(f"eps {facts}")
    if any("所有者" in name and "基本的" in name for name in names):
        _fail(f"glued {names}")
    np_fy = facts.get(("親会社の所有者に帰属する当期利益", "CurrentYearDuration_ForecastMember"))
    np_h1 = facts.get(("親会社の所有者に帰属する当期利益", "CurrentAccumulatedQ2Duration_ForecastMember"))
    eps_fy = facts.get(("基本的1株当たり当期利益", "CurrentYearDuration_ForecastMember"))
    eps_h1 = facts.get(("基本的1株当たり当期利益", "CurrentAccumulatedQ2Duration_ForecastMember"))
    if np_fy is None or np_fy.value_num != MILLION * 18600:
        _fail(f"fnp {np_fy}")
    if np_h1 is None or np_h1.value_num != MILLION * 6300:
        _fail(f"fnp2q {np_h1}")
    if eps_fy is None or eps_fy.value_num != Decimal("158.72") or eps_fy.unit != "JPYPerShares":
        _fail(f"feps {eps_fy}")
    if eps_h1 is None or eps_h1.value_num != Decimal("53.76") or eps_h1.unit != "JPYPerShares":
        _fail(f"feps2q {eps_h1}")
    if any(fact.value_num in {Decimal("9.9"), Decimal("0.099"), Decimal("-3.3"), Decimal("-0.033")} for fact in facts.values()):
        _fail(f"pct leaked {facts}")


def test_kab238_nabtesco_1q_forecast_from_golden() -> None:
    """20260430514688: 通期・中間の FNP / FEPS が J-Quants と一致する。"""
    html_text, d = _golden(KAB238)
    facts = _facts(read_tdnet_pdf(html_text, d))
    want = (
        ("親会社の所有者に帰属する当期利益", "CurrentYearDuration_ForecastMember", MILLION * 18600, "JPY"),
        ("親会社の所有者に帰属する当期利益", "CurrentAccumulatedQ2Duration_ForecastMember", MILLION * 6300, "JPY"),
        ("基本的1株当たり当期利益", "CurrentYearDuration_ForecastMember", Decimal("158.72"), "JPYPerShares"),
        ("基本的1株当たり当期利益", "CurrentAccumulatedQ2Duration_ForecastMember", Decimal("53.76"), "JPYPerShares"),
    )
    for name, ref, value, unit in want:
        fact = facts.get((name, ref))
        if fact is None or fact.value_num != value or fact.unit != unit:
            _fail(f"{name} {ref} {fact}")
    if any("所有者" in name and "基本的" in name for name, _ref in facts):
        _fail(f"glued {[name for name, _ref in facts]}")


def test_kab238_jquants_fnp_feps_match() -> None:
    """golden HTML を sync し、FNP / FEPS / FNP2Q / FEPS2Q が J-Quants と一致する。"""
    data = load_expected()
    result = dump_in_process(data, [MINI_HTML], [KAB238])
    if result["outcomes"].get(KAB238) != "parsed":
        _fail(result["outcomes"])
    subset = {"disclosures": {KAB238: data["disclosures"][KAB238]}}
    report = compare(subset, result)
    if report.lost or report.changed or report.gaps_wrong:
        _fail(f"lost {report.lost[:3]} changed {report.changed[:3]} wrong {report.gaps_wrong[:3]}")
    by_key = {
        (row["std"], row["value_kind"], row["target_period"]): row
        for row in result["rows"]
        if row["no"] == KAB238 and row["range_side"] == "point" and row["is_consolidated"]
    }
    want = {
        ("net_income", "forecast", "FY"): ("18600000000", "JPY"),
        ("net_income", "forecast", "H1"): ("6300000000", "JPY"),
        ("eps_basic", "forecast", "FY"): ("158.72", "JPYPerShares"),
        ("eps_basic", "forecast", "H1"): ("53.76", "JPYPerShares"),
    }
    for key, (value, unit) in want.items():
        row = by_key.get(key)
        if row is None or row["value"] != value or row["unit"] != unit:
            _fail(f"{key} {row}")

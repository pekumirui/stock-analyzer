"""短信サマリーの当期実績（KAB-58 の売上補完、KAB-375 のサマリー優先）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-sf-')}/stock.db")

from app.db import SessionLocal, init_db
from app.models import XbrlContext, XbrlElement
from app.xbrl.jobs import run_normalize
from app.xbrl.normalize import (
    SOURCE_LAYER_PRIMARY,
    SOURCE_LAYER_SUMMARY,
    source_layer_of,
)
from tests.xbrl.test_xbrl_forecast import (
    AXIS_C,
    AXIS_RF,
    CONS,
    FORECAST,
    NCONS,
    RESULT,
    _context,
    _fail,
    _one,
    build_ixbrl,
    parse_and_normalize,
)


CUSTOM_NS = "http://www.xbrl.tdnet.info/jp/tse/ed/custom/71980"
CUSTOM_PFX = "tse-qcediffr-71980"


def _ytd_result_forecast_contexts() -> str:
    ytd = [(AXIS_C, CONS)]
    result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
    result_nc = [(AXIS_C, NCONS), (AXIS_RF, RESULT)]
    fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
    inst = [(AXIS_C, CONS)]
    return "".join([
        _context("CurrentYTDDuration", start="2025-04-01", end="2025-09-30", members=ytd),
        _context("CurrentYTDResult", start="2025-04-01", end="2025-09-30", members=result),
        _context("CurrentYTDResultNC", start="2025-04-01", end="2025-09-30", members=result_nc),
        _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=fc),
        _context("CurrentYearInstant", start=None, end="2025-09-30", members=inst),
    ])


def _custom_xmlns() -> str:
    return f'xmlns:{CUSTOM_PFX}="{CUSTOM_NS}"'


def test_summary_fills_actual_sales_when_primary_is_custom_tag(tmp: Path) -> None:
    """71980 型: 本表は拡張タグだけ。サマリーの当期実績で補い、予想は変えない。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            (CUSTOM_PFX, "OperatingRevenueIFRS", "CurrentYTDDuration", "JPY", "6", "111", "-6"),
            ("jpigp_cor", "OperatingProfitLossIFRS", "CurrentYTDDuration", "JPY", "6", "40", "-6"),
            ("tse-ed-t", "OperatingRevenuesIFRS", "CurrentYTDResult", "JPY", "6", "5949", "-6"),
            ("tse-ed-t", "OperatingRevenuesIFRS", "CurrentYearDuration", "JPY", "6", "28000", "-6"),
            ("jpigp_cor", "AssetsIFRS", "CurrentYearInstant", "JPY", "6", "800", "-6"),
        ]
        html = build_ixbrl(
            facts=facts,
            contexts=_ytd_result_forecast_contexts(),
            extra_xmlns=_custom_xmlns(),
        )
        filing = parse_and_normalize(
            db, tmp, code="71980", doc_id="kab58-7198",
            title="2026年3月期 第2四半期決算短信〔IFRS〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("5949000000"):
            _fail(f"summary fallback sales {sales.value if sales else None}")
        if sales.source_layer != SOURCE_LAYER_SUMMARY:
            _fail(f"fallback source_layer {sales.source_layer}")
        el = db.get(XbrlElement, sales.source_element_id)
        if el is None or el.local_name != "OperatingRevenuesIFRS" or el.namespace_key != "tse-ed-t":
            _fail(f"must not map company extension: {el}")
        custom = db.query(XbrlElement).filter_by(local_name="OperatingRevenueIFRS").one_or_none()
        if custom is None or not custom.is_custom:
            _fail(f"custom extension missing: {custom}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("28000000000"):
            _fail(f"forecast changed {fc.value if fc else None}")
        stats = run_normalize(db, rebuild=True, code="71980")
        if stats["processed"] != 1:
            _fail(f"rebuild {stats}")
        sales2 = _one(db, filing.id, "net_sales", value_kind="actual")
        if Decimal(str(sales2.value)) != Decimal("5949000000"):
            _fail("rebuild changed fallback net_sales")
    finally:
        db.close()


def test_summary_actual_wins_over_primary(tmp: Path) -> None:
    """8267 型: 本表の売上高より、サマリーの営業収益（J-Quants と同じ値）を使う。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jppfs_cor", "NetSales", "CurrentYTDDuration", "JPY", "6", "200", "-6"),
            ("jppfs_cor", "OperatingRevenue1", "CurrentYTDDuration", "JPY", "6", "230", "-6"),
            ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "50", "-6"),
            ("jppfs_cor", "OrdinaryIncome", "CurrentYTDDuration", "JPY", "6", "45", "-6"),
            ("tse-ed-t", "OperatingRevenues", "CurrentYTDResult", "JPY", "6", "230", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYTDResult", "JPY", "6", "200", "-6"),
            ("tse-ed-t", "OperatingIncome", "CurrentYTDResult", "JPY", "6", "51", "-6"),
            ("tse-ed-t", "OperatingIncome", "CurrentYTDResultNC", "JPY", "6", "7", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "500", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ytd_result_forecast_contexts())
        filing = parse_and_normalize(
            db, tmp, code="82670", doc_id="kab375-aeon",
            title="2027年2月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("230000000"):
            _fail(f"summary sales {sales.value if sales else None}")
        if db.get(XbrlElement, sales.source_element_id).local_name != "OperatingRevenues":
            _fail("summary OperatingRevenues must beat summary NetSales")
        if sales.source_layer != SOURCE_LAYER_SUMMARY:
            _fail(f"sales source_layer {sales.source_layer}")
        op = _one(db, filing.id, "operating_income", value_kind="actual")
        if op is None or Decimal(str(op.value)) != Decimal("51000000") or not op.is_consolidated:
            _fail(f"consolidated summary op {op.value if op else None}")
        ordinary = _one(db, filing.id, "ordinary_income", value_kind="actual")
        if ordinary is None or Decimal(str(ordinary.value)) != Decimal("45000000"):
            _fail(f"primary kept when summary missing {ordinary.value if ordinary else None}")
        if ordinary.source_layer != SOURCE_LAYER_PRIMARY:
            _fail(f"ordinary source_layer {ordinary.source_layer}")
        if filing.fiscal_year_end != date(2025, 9, 30):
            _fail(f"fy_end changed {filing.fiscal_year_end}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("500000000"):
            _fail(f"forecast {fc.value if fc else None}")
    finally:
        db.close()


def test_summary_us_gaap_profits(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("tse-ed-t", "NetSalesUS", "CurrentYTDResult", "JPY", "6", "900", "-6"),
            ("tse-ed-t", "OperatingIncomeUS", "CurrentYTDResult", "JPY", "6", "90", "-6"),
            ("tse-ed-t", "NetIncomeUS", "CurrentYTDResult", "JPY", "6", "60", "-6"),
            ("tse-ed-t", "NetIncomePerShareUS", "CurrentYTDResult", "JPYPershare", "0", "287.24", "2"),
        ]
        contexts = _context(
            "CurrentYTDResult", start="2025-04-01", end="2025-09-30",
            members=[(AXIS_C, CONS), (AXIS_RF, RESULT)],
        )
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="77510", doc_id="kab375-us",
            title="2026年3月期 第2四半期決算短信〔米国基準〕（連結）",
            html=html,
        )
        for code, want in (
            ("net_sales", "900000000"), ("operating_income", "90000000"),
            ("net_income", "60000000"), ("eps_basic", "287.24"),
        ):
            row = _one(db, filing.id, code, value_kind="actual")
            if row is None or Decimal(str(row.value)) != Decimal(want):
                _fail(f"US {code} {row.value if row else None}")
    finally:
        db.close()


REIT_NS = "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/re/t/2014-01-12"


def test_reit_summary_actuals(tmp: Path) -> None:
    """89840 型: REIT のサマリーは tse-re-t。営業収益・利益・1口当たり利益を取る。"""
    init_db()
    db = SessionLocal()
    try:
        members = [
            ("tse-re-t:ConsolidatedNonconsolidatedAxis", "tse-re-t:NonConsolidatedMember"),
            ("tse-re-t:ResultForecastAxis", "tse-re-t:ResultMember"),
        ]
        contexts = _context(
            "CurrentYearDuration_NonConsolidatedMember_ResultMember",
            start="2026-03-01", end="2026-08-31", members=members,
        )
        facts = [
            ("tse-re-t", "OperatingRevenuesREIT", "CurrentYearDuration_NonConsolidatedMember_ResultMember", "JPY", "6", "31249", "-6"),
            ("tse-re-t", "OperatingIncome", "CurrentYearDuration_NonConsolidatedMember_ResultMember", "JPY", "6", "15000", "-6"),
            ("tse-re-t", "OrdinaryIncome", "CurrentYearDuration_NonConsolidatedMember_ResultMember", "JPY", "6", "13000", "-6"),
            ("tse-re-t", "NetIncome", "CurrentYearDuration_NonConsolidatedMember_ResultMember", "JPY", "6", "12999", "-6"),
            ("tse-re-t", "NetIncomePerUnitREIT", "CurrentYearDuration_NonConsolidatedMember_ResultMember", "JPYPershare", "0", "2673", "0"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts, extra_xmlns=f'xmlns:tse-re-t="{REIT_NS}"')
        filing = parse_and_normalize(
            db, tmp, code="89840", doc_id="kab375-reit",
            title="2026年8月期 決算短信（REIT）",
            html=html,
        )
        want = {
            "net_sales": "31249000000",
            "operating_income": "15000000000",
            "ordinary_income": "13000000000",
            "net_income": "12999000000",
            "eps_basic": "2673",
        }
        for code, value in want.items():
            row = _one(db, filing.id, code, value_kind="actual")
            if row is None or Decimal(str(row.value)) != Decimal(value):
                _fail(f"REIT {code} {row.value if row else None}")
            if row.source_layer != SOURCE_LAYER_SUMMARY:
                _fail(f"REIT {code} source_layer {row.source_layer}")
    finally:
        db.close()


def test_revenue2_summary_fills_when_primary_missing(tmp: Path) -> None:
    """21560 型: サマリーの Revenue2 当期実績で補う。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            (CUSTOM_PFX, "WeirdSales", "CurrentYTDDuration", "JPY", "6", "1", "-6"),
            ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "12", "-6"),
            ("tse-ed-t", "Revenue2", "CurrentYTDResult", "JPY", "6", "321", "-6"),
            ("tse-ed-t", "Revenue2", "CurrentYearDuration", "JPY", "6", "800", "-6"),
        ]
        html = build_ixbrl(
            facts=facts,
            contexts=_ytd_result_forecast_contexts(),
            extra_xmlns=_custom_xmlns(),
        )
        filing = parse_and_normalize(
            db, tmp, code="21560", doc_id="kab58-2156",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("321000000"):
            _fail(f"Revenue2 fallback {sales.value if sales else None}")
        el = db.get(XbrlElement, sales.source_element_id)
        if el is None or el.local_name != "Revenue2":
            _fail(f"expected Revenue2 source {el}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("800000000"):
            _fail(f"Revenue2 forecast {fc.value if fc else None}")
    finally:
        db.close()


def test_sfrs_summary_sales_without_ifrs_standard(tmp: Path) -> None:
    """92570 型: タイトルに IFRS が無くても tse-ed-t SalesIFRS で IFRS と判定し売上を使う。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "90", "-6"),
            ("tse-ed-t", "SalesIFRS", "CurrentYTDResult", "JPY", "6", "770", "-6"),
            ("tse-ed-t", "SalesIFRS", "CurrentYearDuration", "JPY", "6", "1600", "-6"),
            ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "800", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ytd_result_forecast_contexts())
        filing = parse_and_normalize(
            db, tmp, code="92570", doc_id="kab58-sfrs",
            title="2026年3月期 第2四半期決算短信〔シンガポール基準〕（連結）",
            html=html,
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"SalesIFRS facts should set IFRS: {filing.accounting_standard}")
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("770000000"):
            _fail(f"SFRS fallback {sales.value if sales else None}")
        if sales.source_layer != SOURCE_LAYER_SUMMARY:
            _fail(f"SFRS source_layer {sales.source_layer}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("1600000000"):
            _fail(f"SFRS forecast {fc.value if fc else None}")
    finally:
        db.close()


def test_insurance_without_summary_sales_stays_empty(tmp: Path) -> None:
    """87290 型: サマリーにも売上合計が無ければ実績売上は空のまま。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jppfs_cor", "OrdinaryIncome", "CurrentYTDDuration", "JPY", "6", "33", "-6"),
            ("tse-ed-t", "OrdinaryIncome", "CurrentYTDResult", "JPY", "6", "40", "-6"),
            ("tse-ed-t", "OrdinaryIncome", "CurrentYearDuration", "JPY", "6", "90", "-6"),
            ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "5000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ytd_result_forecast_contexts())
        filing = parse_and_normalize(
            db, tmp, code="87290", doc_id="kab58-ins",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        if _one(db, filing.id, "net_sales", value_kind="actual") is not None:
            _fail("insurance without sales total must stay empty")
        op = _one(db, filing.id, "ordinary_income", value_kind="forecast")
        if op is None:
            _fail("ordinary forecast should still come from summary")
    finally:
        db.close()


def test_fallback_prefers_consolidated_over_higher_priority_standalone(tmp: Path) -> None:
    """65940/60300/62680 型: 単体の JGAAP 売上（priority 小）より連結の IFRS 売上を採る。"""
    init_db()
    db = SessionLocal()
    try:
        result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
        result_nc = [(AXIS_C, NCONS), (AXIS_RF, RESULT)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        ytd = [(AXIS_C, CONS)]
        contexts = "".join([
            _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=ytd),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ResultMember",
                start="2025-04-01", end="2026-03-31", members=result,
            ),
            _context(
                "CurrentYearDuration_NonConsolidatedMember_ResultMember",
                start="2025-04-01", end="2026-03-31", members=result_nc,
            ),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ForecastMember",
                start="2025-04-01", end="2026-03-31", members=fc,
            ),
        ])
        facts = [
            ("jppfs_cor", "OperatingIncome", "CurrentYearDuration", "JPY", "6", "9", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYearDuration_NonConsolidatedMember_ResultMember",
             "JPY", "6", "273452", "-6"),
            ("tse-ed-t", "NetSalesIFRS", "CurrentYearDuration_ConsolidatedMember_ResultMember",
             "JPY", "6", "2708705", "-6"),
            ("tse-ed-t", "NetSalesIFRS", "CurrentYearDuration_ConsolidatedMember_ForecastMember",
             "JPY", "6", "2800000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="65940", doc_id="kab59-6594",
            title="2026年3月期 決算短信〔IFRS〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("2708705000000"):
            _fail(f"consol IFRS sales {sales.value if sales else None}")
        if not sales.is_consolidated:
            _fail("must pick ConsolidatedMember over standalone NetSales")
        el = db.get(XbrlElement, sales.source_element_id)
        if el is None or el.local_name != "NetSalesIFRS":
            _fail(f"expected NetSalesIFRS not standalone NetSales: {el}")
        if sales.source_layer != SOURCE_LAYER_SUMMARY:
            _fail(f"source_layer {sales.source_layer}")
    finally:
        db.close()


def test_fallback_prefers_consolidated_operating_revenues_ifrs(tmp: Path) -> None:
    """60300 型: 単体 OperatingRevenues より連結 RevenueIFRS。"""
    init_db()
    db = SessionLocal()
    try:
        result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
        result_nc = [(AXIS_C, NCONS), (AXIS_RF, RESULT)]
        ytd = [(AXIS_C, CONS)]
        contexts = "".join([
            _context("CurrentYearDuration", start="2025-07-01", end="2026-06-30", members=ytd),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ResultMember",
                start="2025-07-01", end="2026-06-30", members=result,
            ),
            _context(
                "CurrentYearDuration_NonConsolidatedMember_ResultMember",
                start="2025-07-01", end="2026-06-30", members=result_nc,
            ),
        ])
        facts = [
            ("jppfs_cor", "OperatingIncome", "CurrentYearDuration", "JPY", "6", "1", "-6"),
            ("tse-ed-t", "OperatingRevenues",
             "CurrentYearDuration_NonConsolidatedMember_ResultMember",
             "JPY", "6", "14463", "-6"),
            ("tse-ed-t", "RevenueIFRS",
             "CurrentYearDuration_ConsolidatedMember_ResultMember",
             "JPY", "6", "25634", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="60300", doc_id="kab59-6030",
            title="2026年6月期 決算短信〔IFRS〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("25634000000"):
            _fail(f"60300 consol {sales.value if sales else None}")
        el = db.get(XbrlElement, sales.source_element_id)
        if el is None or el.local_name != "RevenueIFRS":
            _fail(f"expected RevenueIFRS: {el}")
    finally:
        db.close()


def _assert_summary_q_sales(
    db,
    filing,
    *,
    want: Decimal,
    want_end: date,
    local_name: str,
) -> None:
    if filing.fiscal_year_end != want_end:
        _fail(f"fy_end {filing.fiscal_year_end} want {want_end} (not forecast year-end)")
    sales = _one(db, filing.id, "net_sales", value_kind="actual")
    if sales is None or Decimal(str(sales.value)) != want:
        _fail(f"summary-only sales {sales.value if sales else None}")
    if sales.source_layer != SOURCE_LAYER_SUMMARY:
        _fail(f"source_layer {sales.source_layer}")
    if sales.period_end != want_end:
        _fail(f"period_end {sales.period_end}")
    el = db.get(XbrlElement, sales.source_element_id)
    if el is None or el.local_name != local_name:
        _fail(f"source {el}")
    result_ctx = (
        db.query(XbrlContext)
        .filter(
            XbrlContext.filing_id == filing.id,
            XbrlContext.context_ref.like("%ResultMember"),
        )
        .one()
    )
    if result_ctx.relative_period != "current":
        _fail(f"ResultMember relative_period {result_ctx.relative_period}")


def test_summary_only_sfrs_q2_uses_result_member_period_end(tmp: Path) -> None:
    """92570 型: 本表も DEI も無く、Q2 実績の ResultMember 期末を当期末にする。"""
    init_db()
    db = SessionLocal()
    try:
        result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        contexts = "".join([
            _context(
                "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember",
                start="2026-01-01", end="2026-06-30", members=result,
            ),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ForecastMember",
                start="2026-01-01", end="2026-12-31", members=fc,
            ),
        ])
        facts = [
            ("tse-ed-t", "SalesIFRS",
             "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember",
             "JPY", "6", "10462", "-6"),
            ("tse-ed-t", "SalesIFRS",
             "CurrentYearDuration_ConsolidatedMember_ForecastMember",
             "JPY", "6", "22000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="92570", doc_id="kab59-9257",
            title="2026年12月期 第2四半期決算短信〔ＳＦＲＳ(Ｉ)及びＩＦＲＳ〕（連結）",
            html=html,
        )
        _assert_summary_q_sales(
            db, filing,
            want=Decimal("10462000000"),
            want_end=date(2026, 6, 30),
            local_name="SalesIFRS",
        )
        fc_row = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc_row is None or Decimal(str(fc_row.value)) != Decimal("22000000000"):
            _fail(f"92570 forecast {fc_row.value if fc_row else None}")
    finally:
        db.close()


def test_summary_only_sfrs_q1_without_ifrs_in_title(tmp: Path) -> None:
    """76990 型: 〔SFRS〕は IFRS。Q1 ResultMember が relative_period=current。"""
    init_db()
    db = SessionLocal()
    try:
        result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        contexts = "".join([
            _context(
                "CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember",
                start="2026-04-01", end="2026-06-30", members=result,
            ),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ForecastMember",
                start="2026-04-01", end="2027-03-31", members=fc,
            ),
        ])
        facts = [
            ("tse-ed-t", "NetSalesIFRS",
             "CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember",
             "JPY", "6", "21469", "-6"),
            ("tse-ed-t", "NetSalesIFRS",
             "CurrentYearDuration_ConsolidatedMember_ForecastMember",
             "JPY", "6", "90000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="76990", doc_id="kab59-7699",
            title="2027年3月期 第1四半期決算短信〔SFRS〕（連結）",
            html=html,
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"SFRS title should be IFRS: {filing.accounting_standard}")
        _assert_summary_q_sales(
            db, filing,
            want=Decimal("21469000000"),
            want_end=date(2026, 6, 30),
            local_name="NetSalesIFRS",
        )
    finally:
        db.close()


def test_amendment_flash_summary_only_keeps_quarter_actual(tmp: Path) -> None:
    """訂正短信 50350/60710/75090 型: 本表無しでも四半期実績売上を残す。"""
    init_db()
    db = SessionLocal()
    try:
        result = [(AXIS_C, CONS), (AXIS_RF, RESULT)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        contexts = "".join([
            _context(
                "CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember",
                start="2025-04-01", end="2025-06-30", members=result,
            ),
            _context(
                "CurrentYearDuration_ConsolidatedMember_ForecastMember",
                start="2025-04-01", end="2026-03-31", members=fc,
            ),
        ])
        facts = [
            ("tse-ed-t", "NetSales",
             "CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember",
             "JPY", "6", "2280", "-6"),
            ("tse-ed-t", "NetSales",
             "CurrentYearDuration_ConsolidatedMember_ForecastMember",
             "JPY", "6", "10000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=contexts)
        filing = parse_and_normalize(
            db, tmp, code="50350", doc_id="kab59-5035",
            title="（訂正）2026年3月期 第1四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        if not filing.is_amendment:
            _fail("expected amendment")
        _assert_summary_q_sales(
            db, filing,
            want=Decimal("2280000000"),
            want_end=date(2025, 6, 30),
            local_name="NetSales",
        )
    finally:
        db.close()


def test_fallback_prefers_consolidated_summary(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "9", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYTDResultNC", "JPY", "6", "10", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYTDResult", "JPY", "6", "80", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "200", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ytd_result_forecast_contexts())
        filing = parse_and_normalize(
            db, tmp, code="45910", doc_id="kab58-cons",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("80000000"):
            _fail(f"consol preferred {sales.value if sales else None}")
        if not sales.is_consolidated:
            _fail("fallback should pick ConsolidatedMember")
    finally:
        db.close()


def test_source_layer_helper() -> None:
    if source_layer_of(XbrlElement(namespace_key="tse-ed-t", local_name="NetSales")) != SOURCE_LAYER_SUMMARY:
        _fail("tse-ed-t is summary")
    if source_layer_of(XbrlElement(namespace_key="jppfs_cor", local_name="NetSales")) != SOURCE_LAYER_PRIMARY:
        _fail("jppfs is primary")
    if source_layer_of(XbrlElement(namespace_key="jpigp_cor", local_name="RevenueIFRS")) != SOURCE_LAYER_PRIMARY:
        _fail("jpigp is primary")


def test_migrate_source_layer_from_element() -> None:
    from sqlalchemy import text

    from app.db import engine, migrate_add_columns, migrate_xbrl_financials_source_layer

    init_db()
    migrate_add_columns(verbose=False)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO xbrl_elements "
            "(namespace_uri, namespace_key, local_name, is_custom, first_seen_at) "
            "VALUES "
            "('http://tse/kab58-sum', 'tse-ed-t', 'Kab58SumSales', 0, '2026-01-01'), "
            "('http://jppfs/kab58-pri', 'jppfs_cor', 'Kab58PriSales', 0, '2026-01-01')"
        ))
        rows = conn.execute(
            text(
                "SELECT id, namespace_key FROM xbrl_elements "
                "WHERE local_name IN ('Kab58SumSales', 'Kab58PriSales')"
            )
        ).fetchall()
        by_ns = {r[1]: r[0] for r in rows}
        conn.execute(
            text(
                "INSERT INTO xbrl_financials ("
                " code, filing_id, std_item_id, is_consolidated, period_type,"
                " period_end, value, unit, generated_at, value_kind, range_side,"
                " target_period, source_element_id"
                ") VALUES "
                "('71980', 91001, 1, 1, 'Q2', '2025-09-30', 1, 'JPY', '2026-01-01',"
                " 'actual', 'point', 'Q2', :sum_id),"
                "('80350', 91002, 1, 1, 'Q2', '2025-09-30', 2, 'JPY', '2026-01-01',"
                " 'actual', 'point', 'Q2', :pri_id)"
            ),
            {"sum_id": by_ns["tse-ed-t"], "pri_id": by_ns["jppfs_cor"]},
        )
    n = migrate_xbrl_financials_source_layer()
    if n < 2:
        _fail(f"expected to fill source_layer, got {n}")
    with engine.begin() as conn:
        layers = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "SELECT code, source_layer FROM xbrl_financials "
                    "WHERE filing_id IN (91001, 91002)"
                )
            ).fetchall()
        }
    if layers.get("71980") != "summary" or layers.get("80350") != "primary":
        _fail(f"backfill layers {layers}")
    n2 = migrate_xbrl_financials_source_layer()
    if n2 != 0:
        _fail(f"second backfill should be 0, got {n2}")


def main() -> None:
    test_source_layer_helper()
    test_migrate_source_layer_from_element()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_summary_fills_actual_sales_when_primary_is_custom_tag(tmp)
        test_summary_actual_wins_over_primary(tmp)
        test_summary_us_gaap_profits(tmp)
        test_reit_summary_actuals(tmp)
        test_revenue2_summary_fills_when_primary_missing(tmp)
        test_sfrs_summary_sales_without_ifrs_standard(tmp)
        test_insurance_without_summary_sales_stays_empty(tmp)
        test_fallback_prefers_consolidated_summary(tmp)
        test_fallback_prefers_consolidated_over_higher_priority_standalone(tmp)
        test_fallback_prefers_consolidated_operating_revenues_ifrs(tmp)
        test_summary_only_sfrs_q2_uses_result_member_period_end(tmp)
        test_summary_only_sfrs_q1_without_ifrs_in_title(tmp)
        test_amendment_flash_summary_only_keeps_quarter_actual(tmp)
    print("ok: xbrl sales fallback")


if __name__ == "__main__":
    main()

"""KAB-58: 本表に標準売上タグが無いときだけ短信サマリーの実績売上で補う。"""
from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-sf-')}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import XbrlElement  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.normalize import (  # noqa: E402
    SOURCE_LAYER_PRIMARY,
    SOURCE_LAYER_SUMMARY,
    source_layer_of,
)
from tests.xbrl.test_xbrl_forecast import (  # noqa: E402
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


def test_primary_sales_not_overwritten_by_summary(tmp: Path) -> None:
    """本表で売上が取れている値はサマリー実績があっても変えない。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jppfs_cor", "NetSales", "CurrentYTDDuration", "JPY", "6", "200", "-6"),
            ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "50", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYTDResult", "JPY", "6", "999", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "500", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ytd_result_forecast_contexts())
        filing = parse_and_normalize(
            db, tmp, code="80350", doc_id="kab58-keep",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("200000000"):
            _fail(f"primary overwritten {sales.value if sales else None}")
        if sales.source_layer != SOURCE_LAYER_PRIMARY:
            _fail(f"primary source_layer {sales.source_layer}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("500000000"):
            _fail(f"forecast {fc.value if fc else None}")
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
    """92570/76990 型: タイトルが SFRS で accounting_standard が空でも tse-ed-t SalesIFRS を使う。"""
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
        if filing.accounting_standard is not None:
            _fail(f"SFRS title must leave standard empty: {filing.accounting_standard}")
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
        test_primary_sales_not_overwritten_by_summary(tmp)
        test_revenue2_summary_fills_when_primary_missing(tmp)
        test_sfrs_summary_sales_without_ifrs_standard(tmp)
        test_insurance_without_summary_sales_stays_empty(tmp)
        test_fallback_prefers_consolidated_summary(tmp)
    print("ok: xbrl sales fallback")


if __name__ == "__main__":
    main()

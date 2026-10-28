"""KAB-52〜56: IFRS 単体の jppfs 除外、業種別売上、会計基準、営業総収入。"""
from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-kab52-')}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import XbrlElement, XbrlFinancial  # noqa: E402
from app.xbrl.normalize import (  # noqa: E402
    mapping_applies,
    skip_jppfs_on_ifrs_filing,
)
from tests.xbrl.test_xbrl_forecast import (  # noqa: E402
    AXIS_C,
    AXIS_RF,
    AXIS_REV,
    CONS,
    CURRENT,
    FORECAST,
    NCONS,
    PREVIOUS,
    _context,
    _fail,
    _one,
    build_ixbrl,
    ifrs_contexts,
    ifrs_facts,
    jpgaap_facts,
    jpgaap_q2_contexts,
    parse_and_normalize,
)


def _ncons_ytd() -> str:
    ytd = [(AXIS_C, NCONS)]
    inst = [(AXIS_C, NCONS)]
    return "".join([
        _context("CurrentYTDDuration_NonConsolidatedMember", start="2026-05-01", end="2026-07-31", members=ytd),
        _context("CurrentYearInstant_NonConsolidatedMember", start=None, end="2026-07-31", members=inst),
    ])


def _source_keys(db, filing_id: int) -> set[str]:
    keys: set[str] = set()
    for row in db.query(XbrlFinancial).filter_by(filing_id=filing_id).all():
        el = db.get(XbrlElement, row.source_element_id)
        if el is not None:
            keys.add(el.namespace_key or "")
    return keys


def test_mapping_applies_ifrs_local_name() -> None:
    mapping = SimpleNamespace(accounting_standard="IFRS")
    jpgaap = SimpleNamespace(accounting_standard="JPGAAP")
    empty = SimpleNamespace(accounting_standard=None)
    sales_ifrs = SimpleNamespace(local_name="SalesIFRS", namespace_key="tse-ed-t", namespace_uri="")
    net_sales = SimpleNamespace(local_name="NetSales", namespace_key="tse-ed-t", namespace_uri="")
    if not mapping_applies(mapping, jpgaap, sales_ifrs):
        _fail("JPGAAP filing should accept *IFRS elements")
    if mapping_applies(mapping, jpgaap, net_sales):
        _fail("JPGAAP filing must not use IFRS mapping for non-IFRS names")
    if not mapping_applies(mapping, empty, sales_ifrs):
        _fail("NULL filing should accept *IFRS elements")
    ifrs_filing = SimpleNamespace(accounting_standard="IFRS")
    jppfs = SimpleNamespace(local_name="ProfitLoss", namespace_key="jppfs_cor", namespace_uri="")
    if not skip_jppfs_on_ifrs_filing(ifrs_filing, jppfs):
        _fail("IFRS filing must skip jppfs")
    if skip_jppfs_on_ifrs_filing(jpgaap, jppfs):
        _fail("JPGAAP filing must keep jppfs")
    jpigp = SimpleNamespace(local_name="ProfitLossIFRS", namespace_key="jpigp_cor", namespace_uri="")
    if skip_jppfs_on_ifrs_filing(ifrs_filing, jpigp):
        _fail("must not skip jpigp")
    jpgaap_map = SimpleNamespace(accounting_standard="JPGAAP")
    pdf_sum = SimpleNamespace(local_name="経常利益", namespace_key="pdf-sum", namespace_uri="")
    nc = SimpleNamespace(is_consolidated=0)
    cons = SimpleNamespace(is_consolidated=1)
    if not mapping_applies(jpgaap_map, ifrs_filing, pdf_sum, nc):
        _fail("IFRS filing #nc pdf should accept JPGAAP mapping")
    if mapping_applies(jpgaap_map, ifrs_filing, pdf_sum, cons):
        _fail("IFRS filing consolidated must not use JPGAAP 経常利益")
    if mapping_applies(jpgaap_map, ifrs_filing, net_sales, nc):
        _fail("JPGAAP mapping on non-pdf must stay filing-standard")


def test_436a0_ifrs_standalone_skips_jppfs(tmp: Path) -> None:
    """436A0: ProfitLossIFRS を採り、jppfs 由来の行を残さない。"""
    init_db()
    db = SessionLocal()
    try:
        facts = [
            ("jpigp_cor", "ProfitLossIFRS", "CurrentYTDDuration_NonConsolidatedMember", "JPY", "3", "277228", "-3"),
            ("jppfs_cor", "ProfitLoss", "CurrentYTDDuration_NonConsolidatedMember", "JPY", "3", "272744", "-3"),
            ("jpigp_cor", "RevenueIFRS", "CurrentYTDDuration_NonConsolidatedMember", "JPY", "3", "800000", "-3"),
            ("jppfs_cor", "NetSales", "CurrentYTDDuration_NonConsolidatedMember", "JPY", "3", "790000", "-3"),
            ("jppfs_cor", "CostOfSales", "CurrentYTDDuration_NonConsolidatedMember", "JPY", "3", "100", "-3"),
            ("jppfs_cor", "CurrentAssets", "CurrentYearInstant_NonConsolidatedMember", "JPY", "3", "10", "-3"),
            ("jpigp_cor", "AssetsIFRS", "CurrentYearInstant_NonConsolidatedMember", "JPY", "3", "5000", "-3"),
            ("jppfs_cor", "Assets", "CurrentYearInstant_NonConsolidatedMember", "JPY", "3", "4000", "-3"),
        ]
        html = build_ixbrl(facts=facts, contexts=_ncons_ytd())
        filing = parse_and_normalize(
            db, tmp, code="436A0", doc_id="081220260828527917",
            title="2027年4月期 第1四半期決算短信〔IFRS〕（単体）",
            html=html,
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"436A0 standard {filing.accounting_standard}")
        ni = _one(db, filing.id, "net_income", value_kind="actual")
        if ni is None or Decimal(str(ni.value)) != Decimal("277228000"):
            _fail(f"436A0 net_income {ni.value if ni else None}")
        el = db.get(XbrlElement, ni.source_element_id)
        if el is None or el.local_name != "ProfitLossIFRS":
            _fail(f"expected ProfitLossIFRS: {el}")
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("800000000"):
            _fail(f"436A0 sales {sales.value if sales else None}")
        assets = _one(db, filing.id, "total_assets", value_kind="actual")
        if assets is None or Decimal(str(assets.value)) != Decimal("5000000"):
            _fail(f"436A0 assets {assets.value if assets else None}")
        if _one(db, filing.id, "current_assets", value_kind="actual") is not None:
            _fail("current_assets is jppfs-only and must stay empty")
        if _one(db, filing.id, "cost_of_sales", value_kind="actual") is not None:
            _fail("cost_of_sales is jppfs-only and must stay empty")
        keys = _source_keys(db, filing.id)
        jppfs = [k for k in keys if k.startswith("jppfs")]
        if jppfs:
            _fail(f"436A0 still has jppfs sources {jppfs} keys={keys}")
    finally:
        db.close()


def test_ifrs_consol_prefers_attributable_over_profitlossifrs(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        facts = list(ifrs_facts())
        facts.append(
            ("jpigp_cor", "ProfitLossIFRS", "CurrentYTDDuration", "JPY", "6", "30", "-6"),
        )
        html = build_ixbrl(facts=facts, contexts=ifrs_contexts())
        filing = parse_and_normalize(
            db, tmp, code="45020", doc_id="ifrs-attr",
            title="2026年3月期 第2四半期決算短信〔IFRS〕（連結）",
            html=html,
        )
        ni = _one(db, filing.id, "net_income", value_kind="actual")
        if ni is None or Decimal(str(ni.value)) != Decimal("22000000"):
            _fail(f"consol net_income should stay attributable 22M: {ni.value if ni else None}")
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("300000000"):
            _fail(f"IFRS sales changed {sales.value if sales else None}")
    finally:
        db.close()


def test_jpgaap_still_uses_jppfs(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        html = build_ixbrl(facts=jpgaap_facts(), contexts=jpgaap_q2_contexts())
        filing = parse_and_normalize(
            db, tmp, code="80350", doc_id="jp-keep",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("200000000"):
            _fail(f"JPGAAP sales {sales.value if sales else None}")
        ni = _one(db, filing.id, "net_income", value_kind="actual")
        if ni is None or Decimal(str(ni.value)) != Decimal("30000000"):
            _fail(f"JPGAAP net_income {ni.value if ni else None}")
        keys = _source_keys(db, filing.id)
        if "jppfs_cor" not in keys:
            _fail(f"JPGAAP should still use jppfs: {keys}")
    finally:
        db.close()


def test_industry_and_ifrs_net_sales_mappings(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        ytd = [(AXIS_C, CONS)]
        inst = [(AXIS_C, CONS)]
        ctx = "".join([
            _context("CurrentYTDDuration", start="2026-04-01", end="2026-06-30", members=ytd),
            _context("CurrentYearInstant", start=None, end="2026-06-30", members=inst),
        ])
        cases = [
            ("83580", "OrdinaryIncomeBNK", "37105", Decimal("37105000000"), "jppfs_cor", "日本基準"),
            ("57130", "NetSalesIFRS", "540110", Decimal("540110000000"), "jpigp_cor", "IFRS"),
            ("59150", "NetSalesOfCompletedConstructionContractsCNS", "8954", Decimal("8954000000"), "jppfs_cor", "日本基準"),
            ("90330", "OperatingRevenueRWY", "10103", Decimal("10103000000"), "jppfs_cor", "日本基準"),
            ("71990", "Revenue2IFRS", "12174", Decimal("12174000000"), "jpigp_cor", "IFRS"),
        ]
        for code, name, raw, want, ns, std in cases:
            facts = [
                (ns, name, "CurrentYTDDuration", "JPY", "6", raw, "-6"),
                ("jpigp_cor" if std == "IFRS" else "jppfs_cor",
                 "AssetsIFRS" if std == "IFRS" else "Assets",
                 "CurrentYearInstant", "JPY", "6", "1", "-6"),
            ]
            html = build_ixbrl(facts=facts, contexts=ctx)
            filing = parse_and_normalize(
                db, tmp, code=code, doc_id=f"ns-{code}",
                title=f"2027年3月期 第1四半期決算短信〔{std}〕（連結）",
                html=html,
            )
            sales = _one(db, filing.id, "net_sales", value_kind="actual")
            if sales is None or Decimal(str(sales.value)) != want:
                _fail(f"{code} {name} {sales.value if sales else None} != {want}")
            el = db.get(XbrlElement, sales.source_element_id)
            if el is None or el.local_name != name:
                _fail(f"{code} source {el}")
    finally:
        db.close()


def test_revenue_ifrs_beats_revenue2ifrs(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        ytd = [(AXIS_C, CONS)]
        inst = [(AXIS_C, CONS)]
        ctx = "".join([
            _context("CurrentYTDDuration", start="2026-04-01", end="2026-06-30", members=ytd),
            _context("CurrentYearInstant", start=None, end="2026-06-30", members=inst),
        ])
        facts = [
            ("jpigp_cor", "RevenueIFRS", "CurrentYTDDuration", "JPY", "6", "122462", "-6"),
            ("jpigp_cor", "Revenue2IFRS", "CurrentYTDDuration", "JPY", "6", "128492", "-6"),
            ("jpigp_cor", "AssetsIFRS", "CurrentYearInstant", "JPY", "6", "1", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="82530", doc_id="rev2",
            title="2027年3月期 第1四半期決算短信〔IFRS〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("122462000000"):
            _fail(f"RevenueIFRS should win: {sales.value if sales else None}")
    finally:
        db.close()


def test_gross_operating_revenue_beats_netsales(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        ytd = [(AXIS_C, CONS)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        inst = [(AXIS_C, CONS)]
        ctx = "".join([
            _context("CurrentYTDDuration", start="2026-03-01", end="2026-08-31", members=ytd),
            _context("CurrentYearDuration", start="2026-03-01", end="2027-02-28", members=fc),
            _context("CurrentYearInstant", start=None, end="2026-08-31", members=inst),
        ])
        facts = [
            ("jppfs_cor", "NetSales", "CurrentYTDDuration", "JPY", "6", "2154", "-6"),
            ("jppfs_cor", "GrossOperatingRevenue", "CurrentYTDDuration", "JPY", "6", "7670", "-6"),
            ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "5000", "-6"),
            ("tse-ed-t", "GrossOperatingRevenues", "CurrentYearDuration", "JPY", "6", "15100", "-6"),
            ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "1", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="75440", doc_id="gor-7544",
            title="2027年2月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("7670000000"):
            _fail(f"GrossOperatingRevenue should win: {sales.value if sales else None}")
        fc_row = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc_row is None or Decimal(str(fc_row.value)) != Decimal("15100000000"):
            _fail(f"GrossOperatingRevenues forecast {fc_row.value if fc_row else None}")
    finally:
        db.close()


def test_gross_operating_revenue_only(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        ytd = [(AXIS_C, CONS)]
        inst = [(AXIS_C, CONS)]
        ctx = "".join([
            _context("CurrentYTDDuration", start="2026-03-01", end="2026-08-31", members=ytd),
            _context("CurrentYearInstant", start=None, end="2026-08-31", members=inst),
        ])
        facts = [
            ("jppfs_cor", "GrossOperatingRevenue", "CurrentYTDDuration", "JPY", "6", "51073", "-6"),
            ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "1", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="99460", doc_id="gor-9946",
            title="2027年2月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("51073000000"):
            _fail(f"99460 sales {sales.value if sales else None}")
    finally:
        db.close()


def test_forecast_revision_salesifrs_sets_ifrs(tmp: Path) -> None:
    """40510 型: タイトルに基準が無く *IFRS 予想があれば IFRS。"""
    init_db()
    db = SessionLocal()
    try:
        cur = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, CURRENT)]
        prev = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, PREVIOUS)]
        ctx = "".join([
            _context("CurrentYearDuration", start="2025-10-01", end="2026-09-30", members=cur),
            _context("PreviousForecastDuration", start="2025-10-01", end="2026-09-30", members=prev),
        ])
        facts = [
            ("tse-ed-t", "SalesIFRS", "CurrentYearDuration", "JPY", "6", "22000", "-6"),
            ("tse-ed-t", "SalesIFRS", "PreviousForecastDuration", "JPY", "6", "19730", "-6"),
            ("tse-ed-t", "ProfitAttributableToOwnersOfParentIFRS", "CurrentYearDuration", "JPY", "6", "1903", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="40510", doc_id="rev-4051",
            title="2026年9月期通期連結業績予想の修正及び配当予想の修正（増配）",
            html=html,
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"40510 should be IFRS from *IFRS facts: {filing.accounting_standard}")
        cur_row = _one(db, filing.id, "net_sales", value_kind="forecast")
        prev_row = _one(db, filing.id, "net_sales", value_kind="forecast_previous")
        ni = _one(db, filing.id, "net_income", value_kind="forecast")
        if cur_row is None or Decimal(str(cur_row.value)) != Decimal("22000000000"):
            _fail(f"40510 fy sales {cur_row.value if cur_row else None}")
        if prev_row is None or Decimal(str(prev_row.value)) != Decimal("19730000000"):
            _fail(f"40510 prev sales {prev_row.value if prev_row else None}")
        if ni is None or Decimal(str(ni.value)) != Decimal("1903000000"):
            _fail(f"40510 profit {ni.value if ni else None}")
    finally:
        db.close()


def test_jpgaap_flash_with_ifrs_forecast_keeps_jppfs(tmp: Path) -> None:
    """40530 型: 短信は JPGAAP のまま、*IFRS 予想だけ採る。"""
    init_db()
    db = SessionLocal()
    try:
        ytd = [(AXIS_C, CONS)]
        fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
        inst = [(AXIS_C, CONS)]
        ctx = "".join([
            _context("CurrentYTDDuration", start="2025-10-01", end="2026-06-30", members=ytd),
            _context("CurrentYearDuration", start="2025-10-01", end="2026-09-30", members=fc),
            _context("CurrentYearInstant", start=None, end="2026-06-30", members=inst),
        ])
        facts = [
            ("jppfs_cor", "NetSales", "CurrentYTDDuration", "JPY", "6", "10000", "-6"),
            ("jppfs_cor", "ProfitLossAttributableToOwnersOfParent", "CurrentYTDDuration", "JPY", "6", "800", "-6"),
            ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "50", "-6"),
            ("tse-ed-t", "SalesIFRS", "CurrentYearDuration", "JPY", "6", "22000", "-6"),
            ("tse-ed-t", "ProfitAttributableToOwnersOfParentIFRS", "CurrentYearDuration", "JPY", "6", "1903", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="40530", doc_id="mix-4053",
            title="2026年9月期 第3四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        if filing.accounting_standard != "JPGAAP":
            _fail(f"40530 title JPGAAP must win: {filing.accounting_standard}")
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("10000000000"):
            _fail(f"40530 actual sales {sales.value if sales else None}")
        fc_row = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc_row is None or Decimal(str(fc_row.value)) != Decimal("22000000000"):
            _fail(f"40530 IFRS forecast {fc_row.value if fc_row else None}")
        ni_fc = _one(db, filing.id, "net_income", value_kind="forecast")
        if ni_fc is None or Decimal(str(ni_fc.value)) != Decimal("1903000000"):
            _fail(f"40530 IFRS profit fc {ni_fc.value if ni_fc else None}")
        keys = _source_keys(db, filing.id)
        if "jppfs_cor" not in keys:
            _fail(f"40530 must keep jppfs actuals: {keys}")
    finally:
        db.close()


def test_inherit_accounting_standard_from_prior_ifrs(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        html_ifrs = build_ixbrl(facts=ifrs_facts(), contexts=ifrs_contexts())
        parse_and_normalize(
            db, tmp, code="61910", doc_id="flash-6191",
            title="2026年3月期 第2四半期決算短信〔IFRS〕（連結）",
            html=html_ifrs,
            submitted_at="2025-10-01 15:00:00",
        )
        cur = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, CURRENT)]
        ctx = _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=cur)
        facts = [
            ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "34000", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="61910", doc_id="rev-6191",
            title="業績予想の修正及び配当予想に関するお知らせ",
            html=html,
            submitted_at="2025-10-31 16:00:00",
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"should inherit IFRS: {filing.accounting_standard}")
    finally:
        db.close()


def test_61910_salesifrs_forecast(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        cur = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, CURRENT)]
        ctx = _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=cur)
        facts = [
            ("tse-ed-t", "SalesIFRS", "CurrentYearDuration", "JPY", "6", "34000", "-6"),
            ("tse-ed-t", "ProfitAttributableToOwnersOfParentIFRS", "CurrentYearDuration", "JPY", "6", "1950", "-6"),
        ]
        html = build_ixbrl(facts=facts, contexts=ctx)
        filing = parse_and_normalize(
            db, tmp, code="61910", doc_id="rev-6191-ifrs",
            title="業績予想の修正及び配当予想に関するお知らせ",
            html=html,
        )
        if filing.accounting_standard != "IFRS":
            _fail(f"61910 *IFRS → IFRS: {filing.accounting_standard}")
        ni = _one(db, filing.id, "net_income", value_kind="forecast")
        if ni is None or Decimal(str(ni.value)) != Decimal("1950000000"):
            _fail(f"61910 profit {ni.value if ni else None}")
    finally:
        db.close()


def main() -> None:
    test_mapping_applies_ifrs_local_name()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_436a0_ifrs_standalone_skips_jppfs(tmp)
        test_ifrs_consol_prefers_attributable_over_profitlossifrs(tmp)
        test_jpgaap_still_uses_jppfs(tmp)
        test_industry_and_ifrs_net_sales_mappings(tmp)
        test_revenue_ifrs_beats_revenue2ifrs(tmp)
        test_gross_operating_revenue_beats_netsales(tmp)
        test_gross_operating_revenue_only(tmp)
        test_forecast_revision_salesifrs_sets_ifrs(tmp)
        test_jpgaap_flash_with_ifrs_forecast_keeps_jppfs(tmp)
        test_inherit_accounting_standard_from_prior_ifrs(tmp)
        test_61910_salesifrs_forecast(tmp)
    print("ok: xbrl kab52-56")


if __name__ == "__main__":
    main()

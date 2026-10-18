"""EPS / dividend / forecast normalize tests (JP-GAAP, IFRS, range, revision)."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "xbrl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-fc-')}/stock.db")

from app.db import SessionLocal, init_db, migrate_xbrl_financials_unique  # noqa: E402
from app.db import upsert  # noqa: E402
from app.models import Stock, XbrlFinancial, XbrlStdItem  # noqa: E402
from app.xbrl.context import classify_title  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402
from app.xbrl.normalize import (  # noqa: E402
    LEGACY_PRIMARY_CODES,
    classify_context,
    infer_target_period,
)
from app.xbrl.store import parse_zip_into_filing  # noqa: E402
from app.models import XbrlContext, XbrlFiling  # noqa: E402
import xbrl_vs_statements as vs  # noqa: E402

JP_NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor"
IFRS_NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2024-11-01/jpigp_cor"
TSE_NS = "http://www.xbrl.tdnet.info/jp/tse/ed/t/2014-01-12"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def seed_stock(db, code: str, name: str) -> None:
    upsert(db, Stock, [{
        "code": code, "name": name, "sector17": "x", "sector33": "x",
        "market": "プライム", "scale_category": "", "margin_type": "",
        "product_category": "011",
    }])


def _member(dim: str, member: str) -> str:
    return (
        f'<xbrldi:explicitMember dimension="{dim}">{member}</xbrldi:explicitMember>'
    )


def _context(
    ctx_id: str,
    *,
    start: str | None,
    end: str,
    members: list[tuple[str, str]],
) -> str:
    period = (
        f"<xbrli:instant>{end}</xbrli:instant>"
        if start is None
        else f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
    )
    scenario = ""
    if members:
        inner = "".join(_member(d, m) for d, m in members)
        scenario = f"<xbrli:scenario>{inner}</xbrli:scenario>"
    return f"""
      <xbrli:context id="{ctx_id}">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>{period}</xbrli:period>
        {scenario}
      </xbrli:context>"""


def _fact(ns: str, name: str, ctx: str, unit: str, scale: str, value: str, decimals: str) -> str:
    return (
        f'<ix:nonFraction name="{ns}:{name}" contextRef="{ctx}" '
        f'unitRef="{unit}" scale="{scale}" decimals="{decimals}">{value}</ix:nonFraction>'
    )


def build_ixbrl(
    *,
    facts: list[tuple[str, str, str, str, str, str, str]],
    contexts: str,
    extra_xmlns: str = "",
) -> str:
    hidden = "\n      ".join(_fact(*f) for f in facts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:jppfs_cor="{JP_NS}"
      xmlns:jpigp_cor="{IFRS_NS}"
      xmlns:tse-ed-t="{TSE_NS}"
      xmlns:jpdei_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      {extra_xmlns}>
<head>
  <ix:header>
    <ix:hidden>
      {hidden}
    </ix:hidden>
    <ix:resources>
      {contexts}
      <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
      <xbrli:unit id="JPYPershare">
        <xbrli:divide>
          <xbrli:unitNumerator><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unitNumerator>
          <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
        </xbrli:divide>
      </xbrli:unit>
    </ix:resources>
  </ix:header>
</head>
<body></body>
</html>
"""


def write_zip(dest: Path, name: str, html: str) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRLData/Summary/summary.htm", html)
        zf.writestr(
            "XBRLData/Summary/manifest.xml",
            '<?xml version="1.0" encoding="UTF-8"?><manifest><ixbrl>summary.htm</ixbrl></manifest>\n',
        )
    path = dest / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())
    return path


AXIS_C = "tse-ed-t:ConsolidatedOrNonConsolidatedAxis"
AXIS_RF = "tse-ed-t:ResultForecastAxis"
AXIS_REV = "tse-ed-t:PreviousCurrentAxis"
AXIS_RG = "tse-ed-t:UpperLowerAxis"
AXIS_DIV = "tse-ed-t:InterimYearEndAxis"
CONS = "tse-ed-t:ConsolidatedMember"
NCONS = "tse-ed-t:NonConsolidatedMember"
RESULT = "tse-ed-t:ResultMember"
FORECAST = "tse-ed-t:ForecastMember"
CURRENT = "tse-ed-t:CurrentMember"
PREVIOUS = "tse-ed-t:PreviousMember"
LOWER = "tse-ed-t:LowerMember"
UPPER = "tse-ed-t:UpperMember"


def jpgaap_q2_contexts() -> str:
    ytd = [(AXIS_C, CONS)]
    fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
    fc_lo = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_RG, LOWER)]
    fc_hi = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_RG, UPPER)]
    dps_q2 = [(AXIS_C, NCONS), (AXIS_RF, RESULT), (AXIS_DIV, "tse-ed-t:SecondQuarterMember")]
    dps_ye_f = [(AXIS_C, NCONS), (AXIS_RF, FORECAST), (AXIS_DIV, "tse-ed-t:YearEndMember")]
    dps_ann_f = [(AXIS_C, NCONS), (AXIS_RF, FORECAST), (AXIS_DIV, "tse-ed-t:AnnualMember")]
    inst = [(AXIS_C, CONS)]
    return "".join([
        _context("CurrentYTDDuration", start="2025-04-01", end="2025-09-30", members=ytd),
        _context("CurrentYearInstant", start=None, end="2025-09-30", members=inst),
        _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=fc),
        _context("CurrentYearDurationLower", start="2025-04-01", end="2026-03-31", members=fc_lo),
        _context("CurrentYearDurationUpper", start="2025-04-01", end="2026-03-31", members=fc_hi),
        _context("DpsQ2", start="2025-04-01", end="2026-03-31", members=dps_q2),
        _context("DpsYearEndF", start="2025-04-01", end="2026-03-31", members=dps_ye_f),
        _context("DpsAnnualF", start="2025-04-01", end="2026-03-31", members=dps_ann_f),
    ])


def jpgaap_facts() -> list[tuple[str, str, str, str, str, str, str]]:
    # ns, name, ctx, unit, scale, value, decimals
    return [
        ("jppfs_cor", "NetSales", "CurrentYTDDuration", "JPY", "6", "200", "-6"),
        ("jppfs_cor", "OperatingIncome", "CurrentYTDDuration", "JPY", "6", "50", "-6"),
        ("jppfs_cor", "OrdinaryIncome", "CurrentYTDDuration", "JPY", "6", "48", "-6"),
        ("jppfs_cor", "ProfitLossAttributableToOwnersOfParent", "CurrentYTDDuration", "JPY", "6", "30", "-6"),
        ("jppfs_cor", "Assets", "CurrentYearInstant", "JPY", "6", "1000", "-6"),
        ("jppfs_cor", "Liabilities", "CurrentYearInstant", "JPY", "6", "600", "-6"),
        ("jppfs_cor", "NetAssets", "CurrentYearInstant", "JPY", "6", "400", "-6"),
        ("tse-ed-t", "NetIncomePerShare", "CurrentYTDDuration", "JPYPershare", "0", "12.5", "2"),
        ("tse-ed-t", "DilutedNetIncomePerShare", "CurrentYTDDuration", "JPYPershare", "0", "12.1", "2"),
        ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "500", "-6"),
        ("tse-ed-t", "OperatingIncome", "CurrentYearDuration", "JPY", "6", "80", "-6"),
        ("tse-ed-t", "OrdinaryIncome", "CurrentYearDuration", "JPY", "6", "75", "-6"),
        ("tse-ed-t", "ProfitAttributableToOwnersOfParent", "CurrentYearDuration", "JPY", "6", "60", "-6"),
        ("tse-ed-t", "NetIncomePerShare", "CurrentYearDuration", "JPYPershare", "0", "25.0", "2"),
        ("tse-ed-t", "NetSales", "CurrentYearDurationLower", "JPY", "6", "480", "-6"),
        ("tse-ed-t", "NetSales", "CurrentYearDurationUpper", "JPY", "6", "520", "-6"),
        ("tse-ed-t", "DividendPerShare", "DpsQ2", "JPYPershare", "0", "10", "2"),
        ("tse-ed-t", "DividendPerShare", "DpsYearEndF", "JPYPershare", "0", "15", "2"),
        ("tse-ed-t", "DividendPerShare", "DpsAnnualF", "JPYPershare", "0", "25", "2"),
    ]


def ifrs_contexts() -> str:
    ytd = [(AXIS_C, CONS)]
    fc = [(AXIS_C, CONS), (AXIS_RF, FORECAST)]
    inst = [(AXIS_C, CONS)]
    return "".join([
        _context("CurrentYTDDuration", start="2025-04-01", end="2025-09-30", members=ytd),
        _context("CurrentYearInstant", start=None, end="2025-09-30", members=inst),
        _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=fc),
    ])


def ifrs_facts() -> list[tuple[str, str, str, str, str, str, str]]:
    return [
        ("jpigp_cor", "RevenueIFRS", "CurrentYTDDuration", "JPY", "6", "300", "-6"),
        ("jpigp_cor", "OperatingProfitLossIFRS", "CurrentYTDDuration", "JPY", "6", "40", "-6"),
        ("jpigp_cor", "ProfitLossAttributableToOwnersOfParentIFRS", "CurrentYTDDuration", "JPY", "6", "22", "-6"),
        ("jpigp_cor", "AssetsIFRS", "CurrentYearInstant", "JPY", "6", "800", "-6"),
        ("tse-ed-t", "BasicEarningsPerShareIFRS", "CurrentYTDDuration", "JPYPershare", "0", "8.2", "2"),
        ("tse-ed-t", "DilutedEarningsPerShareIFRS", "CurrentYTDDuration", "JPYPershare", "0", "8.0", "2"),
        ("tse-ed-t", "SalesIFRS", "CurrentYearDuration", "JPY", "6", "640", "-6"),
        ("tse-ed-t", "OperatingIncomeIFRS", "CurrentYearDuration", "JPY", "6", "90", "-6"),
        ("tse-ed-t", "ProfitAttributableToOwnersOfParentIFRS", "CurrentYearDuration", "JPY", "6", "70", "-6"),
        ("tse-ed-t", "BasicEarningsPerShareIFRS", "CurrentYearDuration", "JPYPershare", "0", "16.5", "2"),
    ]


def revision_contexts() -> str:
    cur = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, CURRENT)]
    prev = [(AXIS_C, CONS), (AXIS_RF, FORECAST), (AXIS_REV, PREVIOUS)]
    return "".join([
        _context("CurrentYearDuration", start="2025-04-01", end="2026-03-31", members=cur),
        _context("PreviousForecastDuration", start="2025-04-01", end="2026-03-31", members=prev),
    ])


def revision_facts() -> list[tuple[str, str, str, str, str, str, str]]:
    return [
        ("tse-ed-t", "NetSales", "CurrentYearDuration", "JPY", "6", "1830", "-6"),
        ("tse-ed-t", "OperatingIncome", "CurrentYearDuration", "JPY", "6", "120", "-6"),
        ("tse-ed-t", "OrdinaryIncome", "CurrentYearDuration", "JPY", "6", "123", "-6"),
        ("tse-ed-t", "ProfitAttributableToOwnersOfParent", "CurrentYearDuration", "JPY", "6", "84", "-6"),
        ("tse-ed-t", "NetIncomePerShare", "CurrentYearDuration", "JPYPershare", "0", "1260", "2"),
        ("tse-ed-t", "NetSales", "PreviousForecastDuration", "JPY", "6", "1700", "-6"),
    ]


def _rows(db, filing_id: int, code: str) -> list[XbrlFinancial]:
    item = db.query(XbrlStdItem).filter_by(code=code).one()
    return db.query(XbrlFinancial).filter_by(filing_id=filing_id, std_item_id=item.id).all()


def _one(db, filing_id: int, code: str, **kwargs) -> XbrlFinancial | None:
    item = db.query(XbrlStdItem).filter_by(code=code).one()
    q = db.query(XbrlFinancial).filter_by(filing_id=filing_id, std_item_id=item.id)
    for k, v in kwargs.items():
        q = q.filter_by(**{k: v})
    rows = q.all()
    if not rows:
        return None
    if len(rows) > 1:
        consol = [r for r in rows if r.is_consolidated]
        rows = consol or rows
    return rows[0]


def parse_and_normalize(db, tmp: Path, *, code: str, doc_id: str, title: str, html: str):
    seed_stock(db, code, code)
    apply_mappings(db)
    zpath = write_zip(tmp, f"{doc_id}.zip", html)
    filing = parse_zip_into_filing(
        db,
        zip_path=zpath,
        code=code,
        doc_id=doc_id,
        title=title,
        raw_path=f"{doc_id}.zip",
        raw_sha256=doc_id,
        submitted_at="2025-10-31 16:00:00",
        cache_dir=tmp / "cache",
    )
    if filing.parse_status != "parsed":
        _fail(f"parse {doc_id}: {filing.parse_status} {filing.parse_error}")
    run_normalize(db)
    return filing


def test_classify_revision_title() -> None:
    a = classify_title("2026年3月期通期業績予想の修正に関するお知らせ")
    if a["doc_type"] != "forecast_revision" or a["period_type"] != "FY":
        _fail(f"forecast revision: {a}")
    b = classify_title("配当予想の修正に関するお知らせ")
    if b["doc_type"] != "dividend_revision":
        _fail(f"div revision: {b}")
    c = classify_title("2026年3月期 第1四半期決算短信〔日本基準〕（連結）")
    if c["doc_type"] != "quarterly":
        _fail(f"q1 still quarterly: {c}")


def test_classify_context_helpers() -> None:
    init_db()
    db = SessionLocal()
    try:
        filing = XbrlFiling(
            code="72030", source="tdnet", doc_id="ctx1", doc_type="quarterly",
            period_type="Q2", submitted_at=__import__("datetime").datetime(2025, 10, 31),
            raw_path="x.zip", parse_status="parsed",
            created_at=__import__("datetime").datetime(2025, 10, 31),
        )
        db.add(filing)
        db.flush()
        ctx = XbrlContext(
            filing_id=filing.id, context_ref="CurrentYearDuration",
            period_type="duration",
            start_date=__import__("datetime").date(2025, 4, 1),
            end_date=__import__("datetime").date(2026, 3, 31),
            is_consolidated=1, relative_period=None,
            dimensions_json=json.dumps({
                AXIS_C: CONS, AXIS_RF: FORECAST, AXIS_RG: LOWER,
            }),
            has_dimensions=True,
        )
        klass = classify_context(ctx)
        if klass is None or klass.value_kind != "forecast" or klass.range_side != "lower":
            _fail(f"range classify {klass}")
        if infer_target_period(ctx, filing, klass, "net_sales") != "FY":
            _fail("fy target")
        h1 = XbrlContext(
            filing_id=filing.id, context_ref="CurrentInterimDuration",
            period_type="duration",
            start_date=__import__("datetime").date(2025, 4, 1),
            end_date=__import__("datetime").date(2025, 9, 30),
            is_consolidated=1, relative_period=None,
            dimensions_json=json.dumps({AXIS_C: CONS, AXIS_RF: FORECAST}),
            has_dimensions=True,
        )
        h1k = classify_context(h1)
        if h1k is None or infer_target_period(h1, filing, h1k, "net_sales") != "H1":
            _fail(f"h1 target {h1k}")
        seg = XbrlContext(
            filing_id=filing.id, context_ref="Seg",
            period_type="duration",
            start_date=__import__("datetime").date(2025, 4, 1),
            end_date=__import__("datetime").date(2025, 9, 30),
            is_consolidated=1, relative_period="current",
            dimensions_json=json.dumps({"jpcrp_cor:OperatingSegmentsAxis": "jpcrp_cor:ReportableSegmentMember"}),
            has_dimensions=True,
        )
        if classify_context(seg) is not None:
            _fail("segment must be skipped")
    finally:
        db.close()


def test_jpgaap_eps_dividend_forecast_range(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        html = build_ixbrl(facts=jpgaap_facts(), contexts=jpgaap_q2_contexts())
        filing = parse_and_normalize(
            db, tmp, code="80350", doc_id="jp1",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("200000000"):
            _fail(f"legacy net_sales changed: {sales.value if sales else None}")
        if sales.target_period != "Q2" or sales.range_side != "point":
            _fail(f"legacy extras {sales.target_period} {sales.range_side}")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast", range_side="point")
        if fc is None or Decimal(str(fc.value)) != Decimal("500000000"):
            _fail(f"fy forecast {fc.value if fc else None}")
        if fc.target_period != "FY":
            _fail(f"forecast target {fc.target_period}")
        lo = _one(db, filing.id, "net_sales", value_kind="forecast", range_side="lower")
        hi = _one(db, filing.id, "net_sales", value_kind="forecast", range_side="upper")
        if lo is None or hi is None:
            _fail("range missing")
        if Decimal(str(lo.value)) != Decimal("480000000") or Decimal(str(hi.value)) != Decimal("520000000"):
            _fail(f"range {lo.value} {hi.value}")
        eps = _one(db, filing.id, "eps_basic", value_kind="actual")
        if eps is None or Decimal(str(eps.value)) != Decimal("12.5"):
            _fail(f"eps {eps.value if eps else None}")
        diluted = _one(db, filing.id, "eps_diluted", value_kind="actual")
        if diluted is None or Decimal(str(diluted.value)) != Decimal("12.1"):
            _fail(f"diluted {diluted.value if diluted else None}")
        dps_q2 = _one(db, filing.id, "dps", value_kind="actual", target_period="Q2")
        if dps_q2 is None or Decimal(str(dps_q2.value)) != Decimal("10"):
            _fail(f"dps q2 {dps_q2.value if dps_q2 else None}")
        if dps_q2.is_consolidated:
            _fail("dps should follow NonConsolidatedMember")
        dps_ye = _one(db, filing.id, "dps", value_kind="forecast", target_period="year_end")
        dps_ann = _one(db, filing.id, "dps", value_kind="forecast", target_period="annual")
        if dps_ye is None or Decimal(str(dps_ye.value)) != Decimal("15"):
            _fail("dps year_end")
        if dps_ann is None or Decimal(str(dps_ann.value)) != Decimal("25"):
            _fail("dps annual")
        # rebuild is idempotent and keeps legacy
        stats = run_normalize(db, rebuild=True, code="80350")
        if stats["processed"] != 1:
            _fail(f"rebuild {stats}")
        sales2 = _one(db, filing.id, "net_sales", value_kind="actual")
        if Decimal(str(sales2.value)) != Decimal("200000000"):
            _fail("rebuild changed legacy net_sales")
    finally:
        db.close()


def test_ifrs_forecast_and_no_ordinary(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        html = build_ixbrl(facts=ifrs_facts(), contexts=ifrs_contexts())
        filing = parse_and_normalize(
            db, tmp, code="45020", doc_id="ifrs1",
            title="2026年3月期 第2四半期決算短信〔IFRS〕（連結）",
            html=html,
        )
        sales = _one(db, filing.id, "net_sales", value_kind="actual")
        if sales is None or Decimal(str(sales.value)) != Decimal("300000000"):
            _fail(f"ifrs sales {sales.value if sales else None}")
        if _one(db, filing.id, "ordinary_income") is not None:
            _fail("ifrs ordinary should stay unmapped")
        fc = _one(db, filing.id, "net_sales", value_kind="forecast")
        if fc is None or Decimal(str(fc.value)) != Decimal("640000000"):
            _fail(f"ifrs fy forecast {fc.value if fc else None}")
        eps = _one(db, filing.id, "eps_basic", value_kind="actual")
        if eps is None or Decimal(str(eps.value)) != Decimal("8.2"):
            _fail(f"ifrs eps {eps.value if eps else None}")
        fc_eps = _one(db, filing.id, "eps_basic", value_kind="forecast")
        if fc_eps is None or Decimal(str(fc_eps.value)) != Decimal("16.5"):
            _fail("ifrs forecast eps")
    finally:
        db.close()


def test_forecast_revision_current_and_previous(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        html = build_ixbrl(facts=revision_facts(), contexts=revision_contexts())
        filing = parse_and_normalize(
            db, tmp, code="18780", doc_id="rev1",
            title="2026年3月期通期業績予想の修正に関するお知らせ",
            html=html,
        )
        if filing.doc_type != "forecast_revision":
            _fail(f"doc_type {filing.doc_type}")
        cur = _one(db, filing.id, "net_sales", value_kind="forecast")
        prev = _one(db, filing.id, "net_sales", value_kind="forecast_previous")
        if cur is None or Decimal(str(cur.value)) != Decimal("1830000000"):
            _fail(f"current forecast {cur.value if cur else None}")
        if prev is None or Decimal(str(prev.value)) != Decimal("1700000000"):
            _fail(f"previous forecast {prev.value if prev else None}")
        if _one(db, filing.id, "net_sales", value_kind="actual") is not None:
            _fail("revision must not invent actuals")
    finally:
        db.close()


def test_legacy_23_unchanged_with_forecast_facts(tmp: Path) -> None:
    """既存23項目の実績は forecast があっても同じ値。"""
    init_db()
    db = SessionLocal()
    try:
        html = build_ixbrl(facts=jpgaap_facts(), contexts=jpgaap_q2_contexts())
        filing = parse_and_normalize(
            db, tmp, code="80350", doc_id="leg1",
            title="2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            html=html,
        )
        expected = {
            "net_sales": Decimal("200000000"),
            "operating_income": Decimal("50000000"),
            "ordinary_income": Decimal("48000000"),
            "net_income": Decimal("30000000"),
            "total_assets": Decimal("1000000000"),
            "total_liabilities": Decimal("600000000"),
            "net_assets": Decimal("400000000"),
        }
        for code, want in expected.items():
            row = _one(db, filing.id, code, value_kind="actual")
            if row is None or Decimal(str(row.value)) != want:
                _fail(f"{code} {row.value if row else None} != {want}")
        present = {
            db.get(XbrlStdItem, r.std_item_id).code
            for r in db.query(XbrlFinancial).filter_by(filing_id=filing.id, value_kind="actual").all()
        }
        leftover = (present & LEGACY_PRIMARY_CODES) - set(expected)
        leftover -= {"profit_before_tax", "cost_of_sales", "gross_profit", "sga"}
        if leftover:
            _fail(f"unexpected legacy actuals {leftover}")
        n_legacy = db.query(XbrlFinancial).filter_by(filing_id=filing.id, value_kind="actual").count()
        if n_legacy < 7:
            _fail(f"too few actuals {n_legacy}")
    finally:
        db.close()


def test_vs_statements_fixture(tmp: Path) -> None:
    db = tmp / "vs.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript("""
        CREATE TABLE statements (
            disclosure_no TEXT PRIMARY KEY,
            code TEXT,
            disclosed_date TEXT,
            type_of_period TEXT,
            type_of_document TEXT,
            period_end TEXT,
            fiscal_year_end TEXT,
            net_sales REAL,
            operating_profit REAL,
            ordinary_profit REAL,
            profit REAL,
            eps REAL,
            fy_net_sales REAL,
            fy_operating_profit REAL,
            fy_ordinary_profit REAL,
            fy_profit REAL,
            fy_eps REAL,
            f2_net_sales REAL,
            f2_operating_profit REAL,
            f2_ordinary_profit REAL,
            f2_profit REAL,
            f2_eps REAL,
            is_revision INTEGER,
            raw_json TEXT
        );
        CREATE TABLE xbrl_std_items (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            statement TEXT NOT NULL,
            label_ja TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE xbrl_filings (
            id INTEGER PRIMARY KEY,
            code TEXT,
            submitted_at TEXT,
            doc_id TEXT,
            doc_type TEXT,
            parse_status TEXT
        );
        CREATE TABLE xbrl_financials (
            id INTEGER PRIMARY KEY,
            code TEXT,
            filing_id INTEGER,
            std_item_id INTEGER,
            is_consolidated INTEGER,
            period_type TEXT,
            period_end TEXT,
            value NUMERIC,
            unit TEXT,
            value_kind TEXT,
            range_side TEXT,
            target_period TEXT
        );
        INSERT INTO xbrl_std_items (id, code, statement, label_ja) VALUES
          (1,'net_sales','PL','売上高'),
          (2,'operating_income','PL','営業利益'),
          (3,'ordinary_income','PL','経常利益'),
          (4,'net_income','PL','純利益'),
          (5,'eps_basic','PL','EPS'),
          (6,'dps','OTHER','配当');
        INSERT INTO xbrl_filings (id, code, submitted_at, doc_id, doc_type, parse_status)
          VALUES (1,'80350','2025-10-31 16:00:00','jp1','quarterly','parsed');
        INSERT INTO statements VALUES (
          'D1','80350','2025-10-31','2Q','2QFinancialStatements_Consolidated_JP',
          '2025-09-30','2026-03-31',
          200000000, 50000000, 48000000, 30000000, 12.5,
          500000000, 80000000, 75000000, 60000000, 25.0,
          NULL,NULL,NULL,NULL,NULL,0,
          '{"Div2Q":"10","FDivFY":"15","FDivAnn":"25","DEPS":"12.1"}'
        );
        INSERT INTO xbrl_financials (
          code, filing_id, std_item_id, is_consolidated, period_type, period_end,
          value, unit, value_kind, range_side, target_period
        ) VALUES
          ('80350',1,1,1,'Q2','2025-09-30',200000000,'JPY','actual','point','Q2'),
          ('80350',1,2,1,'Q2','2025-09-30',50000000,'JPY','actual','point','Q2'),
          ('80350',1,3,1,'Q2','2025-09-30',48000000,'JPY','actual','point','Q2'),
          ('80350',1,4,1,'Q2','2025-09-30',30000000,'JPY','actual','point','Q2'),
          ('80350',1,5,1,'Q2','2025-09-30',12.5,'JPYPershare','actual','point','Q2'),
          ('80350',1,1,1,'Q2','2026-03-31',500000000,'JPY','forecast','point','FY'),
          ('80350',1,2,1,'Q2','2026-03-31',80000000,'JPY','forecast','point','FY'),
          ('80350',1,3,1,'Q2','2026-03-31',75000000,'JPY','forecast','point','FY'),
          ('80350',1,4,1,'Q2','2026-03-31',60000000,'JPY','forecast','point','FY'),
          ('80350',1,5,1,'Q2','2026-03-31',25.0,'JPYPershare','forecast','point','FY'),
          ('80350',1,6,0,'Q2','2026-03-31',10,'JPYPershare','actual','point','Q2'),
          ('80350',1,6,0,'Q2','2026-03-31',15,'JPYPershare','forecast','point','year_end'),
          ('80350',1,6,0,'Q2','2026-03-31',25,'JPYPershare','forecast','point','annual'),
          ('80350',1,1,1,'Q2','2026-03-31',480000000,'JPY','forecast','lower','FY');
        """)
        conn.commit()
    finally:
        conn.close()
    report = vs.compare_db(db)
    if report.compared < 10 or report.matched < 10:
        _fail(f"vs_statements {report.matched}/{report.compared} missing={report.missing} rows={report.rows}")
    if report.mismatches != 0:
        _fail(f"unexpected mismatches { [r for r in report.rows if r.status=='mismatch'] }")
    out = tmp / "vs.json"
    rc = vs.main(["--db", str(db), "--out", str(out)])
    if rc != 0:
        _fail("vs main")
    payload = json.loads(out.read_text(encoding="utf-8"))
    if payload["overall"]["mismatches"] != 0:
        _fail(payload["overall"])


def test_migrate_unique_from_old_schema(tmp: Path) -> None:
    from app.db import engine
    from sqlalchemy import text

    init_db()
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS xbrl_financials"))
        conn.execute(text("""
            CREATE TABLE xbrl_financials (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                filing_id INTEGER NOT NULL,
                std_item_id INTEGER NOT NULL,
                is_consolidated INTEGER NOT NULL,
                period_type TEXT NOT NULL,
                period_start DATE,
                period_end DATE NOT NULL,
                value NUMERIC NOT NULL,
                unit TEXT NOT NULL DEFAULT 'JPY',
                source_fact_id INTEGER,
                source_element_id INTEGER,
                generated_at TEXT NOT NULL,
                UNIQUE (filing_id, std_item_id, is_consolidated, period_end)
            )
        """))
        conn.execute(text("""
            INSERT INTO xbrl_financials (
              code, filing_id, std_item_id, is_consolidated, period_type,
              period_end, value, unit, generated_at
            ) VALUES ('72030', 1, 1, 1, 'FY', '2025-03-31', 100, 'JPY', '2026-01-01')
        """))
    from app.db import migrate_add_columns
    migrate_add_columns(verbose=False)
    migrate_xbrl_financials_unique()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO xbrl_financials (
              code, filing_id, std_item_id, is_consolidated, period_type,
              period_end, value, unit, generated_at, value_kind, range_side, target_period
            ) VALUES (
              '72030', 1, 1, 1, 'FY', '2025-03-31', 200, 'JPY', '2026-01-01',
              'forecast', 'point', 'FY'
            )
        """))
        n = conn.execute(text("SELECT COUNT(*) FROM xbrl_financials")).scalar()
        if n != 2:
            _fail(f"expected actual+forecast rows, got {n}")
        val = conn.execute(
            text("SELECT value FROM xbrl_financials WHERE value_kind='actual'")
        ).scalar()
        if Decimal(str(val)) != Decimal("100"):
            _fail(f"legacy value changed {val}")


def main() -> None:
    test_classify_revision_title()
    test_classify_context_helpers()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_jpgaap_eps_dividend_forecast_range(tmp)
        test_ifrs_forecast_and_no_ordinary(tmp)
        test_forecast_revision_current_and_previous(tmp)
        test_legacy_23_unchanged_with_forecast_facts(tmp)
        test_vs_statements_fixture(tmp)
        test_migrate_unique_from_old_schema(tmp)
    print("ok: xbrl forecast")


if __name__ == "__main__":
    main()

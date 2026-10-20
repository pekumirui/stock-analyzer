"""EDINET のセグメント注記 → xbrl_segments（KAB-77）。ZIP は合成、ダウンロードはフェイク。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-seg-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.xbrl import keep_fact, pending_documents, run  # noqa: E402
from app.models import XbrlFiling, XbrlFinancial, XbrlSegment, XbrlStdItem  # noqa: E402
from app.xbrl.context import ParsedContext  # noqa: E402
from app.xbrl.normalize import rebuild_all  # noqa: E402
from app.xbrl.parser import ParsedFact  # noqa: E402
from app.xbrl.segments import member_kind  # noqa: E402
from tests.edinet.test_edinet_xbrl import FakeClient, _doc, _reset  # noqa: E402
from tests.xbrl.test_xbrl_parse import MANUFACTURING, _ixbrl_html  # noqa: E402

CO = "jpcrp030000-asr_E02144-000"
AXIS = "jpcrp_cor:OperatingSegmentsAxis"
AUTO = f"{CO}:AutomotiveReportableSegmentMember"
FIN = f"{CO}:FinancialServicesReportableSegmentMember"
SUBTOTAL = "jpcrp_cor:ReportableSegmentsMember"
RECON = "jpcrp_cor:ReconcilingItemsMember"


def _seg_ctx(ref: str, member: str, *, instant: bool = False, prior: bool = False) -> str:
    period = ("<xbrli:instant>2025-03-31</xbrli:instant>" if instant else
              "<xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate>")
    if prior:
        period = "<xbrli:startDate>2023-04-01</xbrli:startDate><xbrli:endDate>2024-03-31</xbrli:endDate>"
    return f"""
      <xbrli:context id="{ref}">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E02144</xbrli:identifier></xbrli:entity>
        <xbrli:period>{period}</xbrli:period>
        <xbrli:scenario><xbrldi:explicitMember dimension="{AXIS}">{member}</xbrldi:explicitMember></xbrli:scenario>
      </xbrli:context>"""


CONTEXTS = "".join([
    _seg_ctx("CurrentYearDuration_Auto", AUTO),
    _seg_ctx("CurrentYearDuration_Fin", FIN),
    _seg_ctx("CurrentYearDuration_Sub", SUBTOTAL),
    _seg_ctx("CurrentYearDuration_Recon", RECON),
    _seg_ctx("CurrentYearInstant_Auto", AUTO, instant=True),
    _seg_ctx("PriorYearDuration_Auto", AUTO, prior=True),
])
SEGMENT_FACTS = [
    ("NetSales", "CurrentYearDuration_Auto", "JPY", "6", "150"),
    ("NetSales", "CurrentYearDuration_Fin", "JPY", "6", "60"),
    ("NetSales", "CurrentYearDuration_Sub", "JPY", "6", "210"),
    ("NetSales", "CurrentYearDuration_Recon", "JPY", "6", "-10"),
    ("OperatingIncome", "CurrentYearDuration_Auto", "JPY", "6", "40"),
    ("Assets", "CurrentYearInstant_Auto", "JPY", "6", "700"),
    ("NetSales", "PriorYearDuration_Auto", "JPY", "6", "140"),
]

CO_NS = "http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/2025-03-31/01/2025-06-18"
XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:link="http://www.xbrl.org/2003/linkbase"
    xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:{CO}="{CO_NS}" targetNamespace="{CO_NS}">
  <xsd:annotation><xsd:appinfo>
    <link:linkbaseRef xlink:type="simple" xlink:href="co_lab.xml"
      xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"
      xlink:arcrole="http://www.w3.org/1999/xlink/properties/linkbase"/>
  </xsd:appinfo></xsd:annotation>
  <xsd:element name="AutomotiveReportableSegmentMember" id="{CO}_AutomotiveReportableSegmentMember"/>
  <xsd:element name="FinancialServicesReportableSegmentMember" id="{CO}_FinancialServicesReportableSegmentMember"/>
</xsd:schema>
"""


def _label(href: str, label_id: str, text: str) -> str:
    return f"""
    <link:loc xlink:type="locator" xlink:href="{href}" xlink:label="loc_{label_id}"/>
    <link:label xlink:type="resource" xlink:label="lab_{label_id}" xml:lang="ja"
      xlink:role="http://www.xbrl.org/2003/role/label">{text}</link:label>
    <link:labelArc xlink:type="arc" xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-label"
      xlink:from="loc_{label_id}" xlink:to="lab_{label_id}"/>"""


LAB = f"""<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink"
    xmlns:{CO}="{CO_NS}">
  <link:labelLink xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">
    {_label("co.xsd#" + CO + "_AutomotiveReportableSegmentMember", "auto", "自動車")}
    {_label("co.xsd#" + CO + "_FinancialServicesReportableSegmentMember", "fin", "金融")}
  </link:labelLink>
</link:linkbase>
"""


def _zip() -> bytes:
    html = _ixbrl_html(MANUFACTURING + SEGMENT_FACTS, filing_instant="2025-06-27",
                       dei_period_end="2025-03-31", current_end="2025-03-31",
                       current_start="2024-04-01", extra_context=CONTEXTS, schema_ref="co.xsd")
    html = html.replace(
        "</ix:hidden>",
        '<ix:nonNumeric name="jpdei_cor:AccountingStandardsDEI" contextRef="FilingDateInstant">'
        "Japan GAAP</ix:nonNumeric>\n    </ix:hidden>",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/0101010_honbun_jpcrp030000-asr-001_ixbrl.htm", html)
        zf.writestr("XBRL/PublicDoc/co.xsd", XSD)
        zf.writestr("XBRL/PublicDoc/co_lab.xml", LAB)
    return buf.getvalue()


def _segments(db, filing_id: int) -> dict[tuple[str, str], XbrlSegment]:
    rows = (db.query(XbrlSegment, XbrlStdItem.code)
            .join(XbrlStdItem, XbrlStdItem.id == XbrlSegment.std_item_id)
            .filter(XbrlSegment.filing_id == filing_id).all())
    return {(code, seg.member): seg for seg, code in rows}


def test_keep_fact_segments() -> None:
    def ctx(rel, dims):
        return ParsedContext(context_ref="c", period_type="duration", start_date=None,
                             end_date=date(2025, 3, 31), is_consolidated=1, relative_period=rel,
                             dimensions=dims)

    fact = ParsedFact(namespace_uri="u", namespace_key="jppfs_cor", local_name="NetSales",
                      context_ref="c", unit="JPY", scale=6, value_num=Decimal(1),
                      value_text=None, is_nil=False, is_numeric=True)
    assert keep_fact(fact, ctx("current", {AXIS: AUTO}))
    assert keep_fact(fact, ctx("current", {AXIS: AUTO, "jppfs_cor:ConsolidatedOrNonConsolidatedAxis":
                                           "jppfs_cor:NonConsolidatedMember"}))
    assert not keep_fact(fact, ctx("prior1", {AXIS: AUTO}))
    # セグメント以外の軸（株主資本等変動計算書の内訳など）は今までどおり捨てる
    assert not keep_fact(fact, ctx("current", {"jppfs_cor:ComponentsOfEquityAxis": "x:CapitalStockMember"}))
    assert not keep_fact(fact, ctx("current", {AXIS: AUTO, "jppfs_cor:ComponentsOfEquityAxis": "x:Y"}))


def test_member_kind() -> None:
    assert member_kind(AUTO) == "segment"
    assert member_kind(SUBTOTAL) == "subtotal"
    assert member_kind("jpcrp_cor:TotalOfReportableSegmentsAndOthersMember") == "total"
    assert member_kind(RECON) == "reconciling"
    assert member_kind(f"{CO}:CorporateExpensesAndEliminationMember") == "reconciling"
    assert member_kind(
        "jpcrp_cor:OperatingSegmentsNotIncludedInReportableSegmentsAndOtherRevenueGeneratingBusinessActivitiesMember"
    ) == "other"
    assert member_kind(f"{CO}:SomethingMember") == "unknown"


def test_ingest_segments() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100SEGM"))
        db.commit()
        run(db, FakeClient({"S100SEGM": _zip()}), pending_documents(db))
        f = db.query(XbrlFiling).filter_by(source="edinet", doc_id="S100SEGM").one()
        assert f.parse_status == "parsed"
        segs = _segments(db, f.id)
        auto = segs[("seg_net_sales", AUTO)]
        assert (auto.value, auto.member_label_ja, auto.member_kind, auto.axis) == (
            Decimal("150000000"), "自動車", "segment", AXIS)
        assert (auto.period_start, auto.period_end, auto.is_consolidated) == (
            date(2024, 4, 1), date(2025, 3, 31), True)
        assert segs[("seg_net_sales", FIN)].member_label_ja == "金融"
        assert segs[("seg_net_sales", SUBTOTAL)].member_kind == "subtotal"
        assert segs[("seg_net_sales", RECON)].value == Decimal("-10000000")
        assert segs[("seg_profit", AUTO)].value == Decimal("40000000")
        assert segs[("seg_assets", AUTO)].value == Decimal("700000000")
        assert segs[("seg_assets", AUTO)].period_start is None
        # 前期のセグメントは入れない
        assert len(segs) == 6
        # xbrl_financials は次元なしの値のまま（セグメントの NetSales を拾わない）
        sales = (db.query(XbrlFinancial).join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
                 .filter(XbrlFinancial.filing_id == f.id, XbrlStdItem.code == "net_sales").one())
        assert Decimal(str(sales.value)) == Decimal("200000000")
        assert not db.query(XbrlFinancial).join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id) \
            .filter(XbrlStdItem.statement == "SEG").count()
        # --rebuild で作り直しても同じ
        st = rebuild_all(db)
        assert st["errors"] == 0
        # 消す前と作り直した後の件数（KAB-83）。正規化が同じなので前後が一致する
        assert st["segments_deleted"] == st["segments"] == 6
        assert st["deleted"] == st["financials"] > 0
        assert len(_segments(db, f.id)) == 6
    finally:
        db.close()

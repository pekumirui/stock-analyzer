"""有報の主要な経営指標等・従業員・株式数・研究開発費・設備投資額（KAB-78）。ZIP は合成。

要素名と context は 2025-06 提出の実物（スバル・日立など）に合わせた。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-annual-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.xbrl import pending_documents, run  # noqa: E402
from app.models import (  # noqa: E402
    EdinetDocument,
    Stock,
    XbrlContext,
    XbrlFact,
    XbrlFiling,
    XbrlFinancial,
    XbrlSegment,
    XbrlStdItem,
)
from app.xbrl.mappings import apply_mappings  # noqa: E402
from tests.edinet.test_edinet_xbrl import FakeClient, _doc  # noqa: E402

NS = {
    "jpcrp_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2024-11-01/jpcrp_cor",
    "jppfs_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor",
    "jpigp_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2024-11-01/jpigp_cor",
    "jpdei_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor",
    "co": "http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E00000-000/2025-03-31/01/2025-06-27",
}
CONSOL_AXIS = "jppfs_cor:ConsolidatedOrNonConsolidatedAxis"
NCONS = "jppfs_cor:NonConsolidatedMember"
SEG_AXIS = "jpcrp_cor:OperatingSegmentsAxis"


def _ctx(cid: str, *, instant: str | None = None, start: str | None = None, end: str | None = None,
         members: list[tuple[str, str]] = ()) -> str:
    period = (f"<xbrli:instant>{instant}</xbrli:instant>" if instant else
              f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>")
    scen = "".join(f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>' for a, m in members)
    scen = f"<xbrli:scenario>{scen}</xbrli:scenario>" if scen else ""
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">'
            f"E00000</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period>{scen}</xbrli:context>")


CONTEXTS = "".join([
    _ctx("CurrentYearDuration", start="2024-04-01", end="2025-03-31"),
    _ctx("CurrentYearInstant", instant="2025-03-31"),
    _ctx("Prior1YearDuration", start="2023-04-01", end="2024-03-31"),
    _ctx("Prior1YearInstant", instant="2024-03-31"),
    _ctx("CurrentYearDuration_NonConsolidatedMember", start="2024-04-01", end="2025-03-31",
         members=[(CONSOL_AXIS, NCONS)]),
    _ctx("CurrentYearInstant_NonConsolidatedMember", instant="2025-03-31", members=[(CONSOL_AXIS, NCONS)]),
    _ctx("CurrentYearInstant_AutoMember", instant="2025-03-31", members=[(SEG_AXIS, "co:AutoReportableSegmentMember")]),
    _ctx("CurrentYearInstant_Row1Member", instant="2025-03-31",
         members=[("jpcrp_cor:TreasurySharesEtcAxis", "jpcrp_cor:Row1Member")]),
    _ctx("FilingDateInstant", instant="2025-06-27"),
])

# (prefix:name, contextRef, unitRef, scale, decimals, 表示値, sign)
JPGAAP = [
    ("jppfs_cor:NetSales", "CurrentYearDuration", "JPY", "6", "-6", "200", ""),
    # 主要な経営指標等（連結・単体）。前期は入れない
    ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "CurrentYearInstant", "JPYPerShares", "0", "2", "2,120.08", ""),
    ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "Prior1YearInstant", "JPYPerShares", "0", "2", "1,900.00", ""),
    ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember",
     "JPYPerShares", "0", "2", "1,500.50", ""),
    ("jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults", "CurrentYearDuration", "JPYPerShares", "0", "2",
     "12.34", "-"),
    ("jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults", "CurrentYearDuration_NonConsolidatedMember",
     "JPYPerShares", "0", "2", "440.11", ""),
    ("jpcrp_cor:DilutedEarningsPerShareSummaryOfBusinessResults", "CurrentYearDuration", "JPYPerShares", "0", "2",
     "440.08", ""),
    ("jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults", "CurrentYearInstant", "pure", "-2", "3", "47.1", ""),
    ("jpcrp_cor:RateOfReturnOnEquitySummaryOfBusinessResults", "CurrentYearDuration", "pure", "-2", "3", "22.0", ""),
    ("jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember",
     "shares", "3", "-3", "733,057", ""),
    # 自己株式等の表: 所有者ごとの行（Row1Member）は入れず、計だけ
    ("jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc", "CurrentYearInstant_Row1Member", "shares", "0", "0",
     "1,623,400", ""),
    ("jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc", "CurrentYearInstant", "shares", "0", "0", "2,023,400", ""),
    # 従業員の状況: 連結・単体・セグメント別（セグメント別は入れない）
    ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant", "pure", "0", "0", "37,866", ""),
    ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant_NonConsolidatedMember", "pure", "0", "0", "17,885", ""),
    ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant_AutoMember", "pure", "0", "0", "30,000", ""),
    ("jpcrp_cor:NumberOfEmployees", "Prior1YearInstant", "pure", "0", "0", "36,000", ""),
    ("jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees",
     "CurrentYearInstant_NonConsolidatedMember", "pure", "0", "1", "39.8", ""),
    ("jpcrp_cor:AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees",
     "CurrentYearInstant_NonConsolidatedMember", "pure", "0", "1", "15.9", ""),
    ("jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees",
     "CurrentYearInstant_NonConsolidatedMember", "JPY", "0", "0", "7,307,644", ""),
    # 研究開発費: 研究開発活動の節が PL 注記より先
    ("jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities", "CurrentYearDuration",
     "JPY", "8", "-8", "1,600", ""),
    ("jppfs_cor:ResearchAndDevelopmentExpensesSGA", "CurrentYearDuration", "JPY", "6", "-6", "150,000", ""),
    ("jpcrp_cor:CapitalExpendituresOverviewOfCapitalExpendituresEtc", "CurrentYearDuration", "JPY", "6", "-6",
     "176,144", ""),
]


def _html(facts, standard: str, consolidated: bool = True) -> str:
    attrs = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    body = []
    for name, ctx, unit, scale, decimals, shown, sign in facts:
        s = f' sign="{sign}"' if sign else ""
        body.append(f'<ix:nonFraction name="{name}" contextRef="{ctx}" unitRef="{unit}" scale="{scale}" '
                    f'decimals="{decimals}" format="ixt:num-dot-decimal"{s}>{shown}</ix:nonFraction>')
    units = "".join(f'<xbrli:unit id="{u}"><xbrli:measure>{m}</xbrli:measure></xbrli:unit>' for u, m in (
        ("JPY", "iso4217:JPY"), ("USD", "iso4217:USD"), ("pure", "xbrli:pure"), ("shares", "xbrli:shares")))
    for uid, curr in (("JPYPerShares", "iso4217:JPY"), ("USDPerShares", "iso4217:USD")):
        units += (f'<xbrli:unit id="{uid}"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>{curr}'
                  "</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares"
                  "</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217" {attrs}>
<head><ix:header><ix:hidden>
<ix:nonNumeric name="jpdei_cor:AccountingStandardsDEI" contextRef="FilingDateInstant">{standard}</ix:nonNumeric>
<ix:nonNumeric name="jpdei_cor:CurrentPeriodEndDateDEI" contextRef="FilingDateInstant">2025-03-31</ix:nonNumeric>
<ix:nonNumeric name="jpdei_cor:WhetherConsolidatedFinancialStatementsArePreparedDEI" contextRef="FilingDateInstant">{str(consolidated).lower()}</ix:nonNumeric>
</ix:hidden><ix:resources>{CONTEXTS}{units}</ix:resources></ix:header></head>
<body><p>{"".join(body)}</p></body></html>
"""


def _zip(facts, standard: str = "Japan GAAP", consolidated: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/0101010_honbun_jpcrp030000-asr-001_ixbrl.htm", _html(facts, standard, consolidated))
    return buf.getvalue()


def _ingest(facts, standard: str = "Japan GAAP", consolidated: bool = True) -> tuple:
    init_db()
    db = SessionLocal()
    for model in (XbrlSegment, XbrlFinancial, XbrlFact, XbrlContext, XbrlFiling, EdinetDocument, Stock):
        db.execute(delete(model))
    db.add(Stock(code="72030", name="テスト", product_category="011", market="プライム"))
    db.add(_doc(1, "S100KB78", file_date=date(2025, 6, 27),
                submit_datetime=datetime(2025, 6, 27, 15, 0)))  # noqa: DTZ001
    db.commit()
    apply_mappings(db)
    st = run(db, FakeClient({"S100KB78": _zip(facts, standard, consolidated)}), pending_documents(db))
    assert st["parsed"] == 1, st
    f = db.query(XbrlFiling).filter_by(doc_id="S100KB78").one()
    rows = (db.query(XbrlStdItem.code, XbrlFinancial.is_consolidated, XbrlFinancial.value, XbrlFinancial.unit,
                     XbrlFinancial.period_end)
            .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
            .filter(XbrlFinancial.filing_id == f.id, XbrlFinancial.value_kind == "actual").all())
    out = {(c, bool(cons)): (Decimal(str(v)), u, end) for c, cons, v, u, end in rows}
    db.close()
    return f, out


def test_jpgaap_annual_items() -> None:
    f, got = _ingest(JPGAAP)
    assert f.accounting_standard == "JPGAAP"

    def v(code, cons=True):
        hit = got.get((code, cons))
        return None if hit is None else (hit[0], hit[1])

    assert v("bps") == (Decimal("2120.08"), "JPYPerShares")
    assert v("bps", False) == (Decimal("1500.50"), "JPYPerShares")
    # EPS は本表に数値が無いので主要な経営指標等から。△は負
    assert v("eps_basic") == (Decimal("-12.34"), "JPYPerShares")
    assert v("eps_basic", False) == (Decimal("440.11"), "JPYPerShares")
    assert v("eps_diluted") == (Decimal("440.08"), "JPYPerShares")
    # 比率は小数（47.1% → 0.471）。unit は ratio
    assert v("equity_ratio") == (Decimal("0.471"), "ratio")
    assert v("roe") == (Decimal("0.220"), "ratio")
    assert v("shares_issued", False) == (Decimal("733057000"), "shares")
    assert v("treasury_shares") == (Decimal("2023400"), "shares")
    assert v("employees") == (Decimal("37866"), "persons")
    assert v("employees", False) == (Decimal("17885"), "persons")
    assert v("employee_avg_age", False) == (Decimal("39.8"), "years")
    assert v("employee_avg_tenure", False) == (Decimal("15.9"), "years")
    assert v("employee_avg_salary", False) == (Decimal("7307644"), "JPY")
    assert v("rd_expenses") == (Decimal("160000000000"), "JPY")
    assert v("capex_reported") == (Decimal("176144000000"), "JPY")
    # 既存の本表の科目は今までどおり
    assert v("net_sales") == (Decimal("200000000"), "JPY")
    # 当期末だけ（前期・セグメント別・所有者別の行は入らない）
    assert {end for _v, _u, end in got.values()} == {date(2025, 3, 31)}
    assert ("employees", True) in got and len([k for k in got if k[0] == "employees"]) == 2


IFRS = [
    ("jpigp_cor:RevenueIFRS", "CurrentYearDuration", "JPY", "6", "-6", "300", ""),
    ("jpigp_cor:BasicEarningsLossPerShareIFRS", "CurrentYearDuration", "JPYPerShares", "0", "2", "133.85", ""),
    ("jpcrp_cor:BasicEarningsLossPerShareIFRSSummaryOfBusinessResults", "CurrentYearDuration", "JPYPerShares",
     "0", "2", "133.85", ""),
    # 名前は EquityToAssetRatio だが 1株当たり親会社所有者帰属持分
    ("jpcrp_cor:EquityToAssetRatioIFRSSummaryOfBusinessResults", "CurrentYearInstant", "JPYPerShares", "0", "2",
     "1,277.25", ""),
    ("jpcrp_cor:RatioOfOwnersEquityToGrossAssetsIFRSSummaryOfBusinessResults", "CurrentYearInstant", "pure", "-2",
     "3", "44.0", ""),
    ("jpcrp_cor:RateOfReturnOnEquityIFRSSummaryOfBusinessResults", "CurrentYearDuration", "pure", "-2", "3",
     "10.7", ""),
    # 単体は日本基準の要素。jppfs は IFRS の書類では使わない（PL 注記の研究開発費も）
    ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember",
     "JPYPerShares", "0", "2", "783.48", ""),
    ("jppfs_cor:ResearchAndDevelopmentExpensesSGA", "CurrentYearDuration_NonConsolidatedMember", "JPY", "6", "-6",
     "54,860", ""),
    ("jpigp_cor:ResearchAndDevelopmentExpenditureRecognizedAsExpenseDuringPeriodIFRS", "CurrentYearDuration",
     "JPY", "6", "-6", "142,448", ""),
]


def test_ifrs_annual_items() -> None:
    f, got = _ingest(IFRS, "IFRS")
    assert f.accounting_standard == "IFRS"
    assert got[("eps_basic", True)][:2] == (Decimal("133.85"), "JPYPerShares")
    assert got[("bps", True)][:2] == (Decimal("1277.25"), "JPYPerShares")
    assert got[("bps", False)][:2] == (Decimal("783.48"), "JPYPerShares")
    assert got[("equity_ratio", True)][:2] == (Decimal("0.440"), "ratio")
    assert got[("roe", True)][:2] == (Decimal("0.107"), "ratio")
    # 研究開発活動の節が無い会社は IFRS の注記
    assert got[("rd_expenses", True)][:2] == (Decimal("142448000000"), "JPY")
    assert ("rd_expenses", False) not in got


def test_years_and_months() -> None:
    """「43歳10ヶ月」は年と月が別の要素。年に 月÷12 を足す。月だけ（1 年未満）も入る"""
    age = "jpcrp_cor:AverageAge{}InformationAboutReportingCompanyInformationAboutEmployees"
    tenure = "jpcrp_cor:AverageLengthOfService{}InformationAboutReportingCompanyInformationAboutEmployees"
    ncons = "CurrentYearInstant_NonConsolidatedMember"
    facts = [
        ("jppfs_cor:NetSales", "CurrentYearDuration", "JPY", "6", "-6", "200", ""),
        (age.format("Years"), ncons, "pure", "0", "0", "43", ""),
        (age.format("Months"), ncons, "pure", "0", "0", "10", ""),
        (tenure.format("Months"), ncons, "pure", "0", "0", "6", ""),
    ]
    _f, got = _ingest(facts)
    assert got[("employee_avg_age", False)][:2] == (Decimal("43.83"), "years")
    assert got[("employee_avg_tenure", False)][:2] == (Decimal("0.50"), "years")


def test_single_entity_rows_are_non_consolidated() -> None:
    """連結財務諸表の無い会社（DEI が false）は、連結の軸が無い context の値も単体"""
    facts = [
        ("jppfs_cor:NetSales", "CurrentYearDuration_NonConsolidatedMember", "JPY", "6", "-6", "200", ""),
        ("jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities", "CurrentYearDuration",
         "JPY", "6", "-6", "290", ""),
        ("jpcrp_cor:CapitalExpendituresOverviewOfCapitalExpendituresEtc", "CurrentYearDuration", "JPY", "6", "-6",
         "715", ""),
        ("jpcrp_cor:TotalNumberOfSharesHeldTreasurySharesEtc", "CurrentYearInstant", "shares", "0", "0", "1,000", ""),
        ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant_NonConsolidatedMember", "pure", "0", "0", "500", ""),
    ]
    _f, got = _ingest(facts, consolidated=False)
    assert got[("rd_expenses", False)][:2] == (Decimal("290000000"), "JPY")
    assert got[("capex_reported", False)][:2] == (Decimal("715000000"), "JPY")
    assert got[("treasury_shares", False)][:2] == (Decimal("1000"), "shares")
    assert got[("employees", False)][:2] == (Decimal("500"), "persons")
    assert not any(cons for _code, cons in got)


def test_usd_facts_keep_usd_unit() -> None:
    """62690 型: std.unit が JPY* でも fact が USD なら financials も USD（KAB-147）。

    単体の bps と pure の科目は今までどおり。
    """
    facts = [
        ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "CurrentYearInstant",
         "USDPerShares", "0", "2", "21.26", ""),
        ("jpcrp_cor:NetAssetsPerShareSummaryOfBusinessResults", "CurrentYearInstant_NonConsolidatedMember",
         "JPYPerShares", "0", "2", "1,500.50", ""),
        ("jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities", "CurrentYearDuration",
         "USD", "0", "0", "23,926,000", ""),
        ("jpcrp_cor:CapitalExpendituresOverviewOfCapitalExpendituresEtc", "CurrentYearDuration",
         "USD", "0", "0", "7,350,000", ""),
        ("jpcrp_cor:NumberOfEmployees", "CurrentYearInstant", "pure", "0", "0", "5,000", ""),
        ("jpcrp_cor:EquityToAssetRatioSummaryOfBusinessResults", "CurrentYearInstant", "pure", "-2", "3",
         "47.1", ""),
        ("jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees",
         "CurrentYearInstant_NonConsolidatedMember", "pure", "0", "1", "39.8", ""),
        ("jpcrp_cor:BasicEarningsLossPerShareSummaryOfBusinessResults", "CurrentYearDuration",
         "USDPerShares", "0", "2", "3.45", ""),
    ]
    _f, got = _ingest(facts)

    def v(code, cons=True):
        hit = got.get((code, cons))
        return None if hit is None else (hit[0], hit[1])

    assert v("bps") == (Decimal("21.26"), "USDPerShares")
    assert v("rd_expenses") == (Decimal("23926000"), "USD")
    assert v("capex_reported") == (Decimal("7350000"), "USD")
    assert v("eps_basic") == (Decimal("3.45"), "USDPerShares")
    assert v("bps", False) == (Decimal("1500.50"), "JPYPerShares")
    assert v("employees") == (Decimal("5000"), "persons")
    assert v("equity_ratio") == (Decimal("0.471"), "ratio")
    assert v("employee_avg_age", False) == (Decimal("39.8"), "years")

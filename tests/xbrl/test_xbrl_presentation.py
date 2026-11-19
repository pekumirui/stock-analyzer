"""表示リンク（*pre.xml）の区分と要素の並びの読み取りと保存（xbrl_presentation_roles。docs/jobs/xbrl.md §4.2）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, engine
from app.models import XbrlFact, XbrlFiling
from app.xbrl.mappings import apply_mappings
from app.xbrl.presentation import (
    StatementRole,
    classify_role,
    read_presentation,
    statement_roles,
)
from app.xbrl.store import delete_filing, delete_filing_payload, parse_zip_into_filing
from tests.xbrl.test_xbrl_parse import MANUFACTURING, build_zip, seed_stock, write_zip

PPFS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor_2024-11-01.xsd"
IGP = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2024-11-01/jpigp_cor_2024-11-01.xsd"
COMPANY_XSD = "jpcrp030000-asr-001_E99999-000_2026-03-31_01_2026-06-20.xsd"
COMPANY_PREFIX = "jpcrp030000-asr_E99999-000"
JPPFS_ROLE = "http://disclosure.edinet-fsa.go.jp/role/jppfs/"
JPCRP_ROLE = "http://disclosure.edinet-fsa.go.jp/role/jpcrp/"
CODE = "99990"


def _href(name: str) -> str:
    if name.startswith(COMPANY_PREFIX + "_"):
        return f"{COMPANY_XSD}#{name}"
    if name.startswith("jpigp_cor_"):
        return f"{IGP}#{name}"
    return f"{PPFS}#{name}"


def _link(role_uri: str, arcs: list[tuple]) -> str:
    """arcs: (親, 子, order[, preferredLabel])。loc は名前の逆順に書く（表示の順と違う）。"""
    names = sorted({a[0] for a in arcs} | {a[1] for a in arcs}, reverse=True)
    locs = "\n".join(
        f'<link:loc xlink:type="locator" xlink:href="{_href(n)}" xlink:label="{n}"/>' for n in names
    )
    arc_xml = []
    for arc in reversed(arcs):
        src, dst, order = arc[:3]
        pref = f' preferredLabel="http://www.xbrl.org/2003/role/{arc[3]}"' if len(arc) > 3 else ""
        arc_xml.append(
            f'<link:presentationArc xlink:type="arc" xlink:arcrole="http://www.xbrl.org/2003/arcrole/parent-child" '
            f'xlink:from="{src}" xlink:to="{dst}" order="{order}"{pref}/>'
        )
    return (
        f'<link:presentationLink xlink:type="extended" xlink:role="{role_uri}">\n{locs}\n'
        + "\n".join(arc_xml) + "\n</link:presentationLink>"
    )


def _pre(links: list[str]) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<link:linkbase xmlns:xlink="http://www.w3.org/1999/xlink" '
        'xmlns:link="http://www.xbrl.org/2003/linkbase">\n' + "\n".join(links) + "\n</link:linkbase>"
    )


def _company_xsd(*locals_: str) -> str:
    els = "\n".join(
        f'<xsd:element id="{COMPANY_PREFIX}_{n}" name="{n}" substitutionGroup="xbrli:item"/>' for n in locals_
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'targetNamespace="http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E99999-000/2026-03-31/01/2026-06-20">\n'
        f"{els}\n</xsd:schema>"
    )


def _p(local: str) -> str:
    return f"jppfs_cor_{local}"


def _c(local: str) -> str:
    return f"{COMPANY_PREFIX}_{local}"


def _bs(role: str, consolidated: bool = True) -> str:
    return _link(JPPFS_ROLE + role, [
        (_p("BalanceSheetHeading"), _p("BalanceSheetTable"), 1),
        (_p("BalanceSheetTable"), _p("ConsolidatedOrNonConsolidatedAxis"), 1),
        (_p("ConsolidatedOrNonConsolidatedAxis"), _p("ConsolidatedMember" if consolidated else "NonConsolidatedMember"), 1),
        (_p("BalanceSheetTable"), _p("BalanceSheetLineItems"), 2),
        (_p("BalanceSheetLineItems"), _p("AssetsAbstract"), 1),
        (_p("AssetsAbstract"), _p("CashAndDeposits"), 1),
        (_p("AssetsAbstract"), _c("WidgetsCA"), 2),
        (_p("AssetsAbstract"), _p("Assets"), 3, "totalLabel"),
        (_p("BalanceSheetLineItems"), _p("Liabilities"), 2, "totalLabel"),
        (_p("BalanceSheetLineItems"), _p("NetAssets"), 3, "totalLabel"),
    ])


def _pl(role: str) -> str:
    return _link(JPPFS_ROLE + role, [
        (_p("StatementOfIncomeHeading"), _p("NetSales"), 1),
        (_p("StatementOfIncomeHeading"), _p("CostOfSales"), 2),
        (_p("StatementOfIncomeHeading"), _p("GrossProfit"), 3),
        (_p("StatementOfIncomeHeading"), _p("OperatingIncome"), 10),
        (_p("StatementOfIncomeHeading"), _p("IncomeBeforeIncomeTaxes"), 11),
        (_p("StatementOfIncomeHeading"), _p("ProfitLoss"), 12),
    ])


def _ci(role: str) -> str:
    return _link(JPPFS_ROLE + role, [
        (_p("StatementOfComprehensiveIncomeHeading"), _p("ProfitLoss"), 1),
        (_p("StatementOfComprehensiveIncomeHeading"), _p("ComprehensiveIncome"), 2),
    ])


def _cf(role: str) -> str:
    return _link(JPPFS_ROLE + role, [
        (_p("StatementOfCashFlowsHeading"), _p("IncomeBeforeIncomeTaxes"), 1),
        (_p("StatementOfCashFlowsHeading"), _p("NetCashProvidedByUsedInOperatingActivities"), 2),
        (_p("StatementOfCashFlowsHeading"), _p("NetIncreaseDecreaseInCashAndCashEquivalents"), 3),
        (_p("StatementOfCashFlowsHeading"), _p("CashAndCashEquivalents"), 4, "periodStartLabel"),
        (_p("StatementOfCashFlowsHeading"), _p("CashAndCashEquivalents"), 5, "periodEndLabel"),
    ])


def _ss(role: str) -> str:
    return _link(JPPFS_ROLE + role, [
        (_p("StatementOfChangesInEquityHeading"), _p("ProfitLoss"), 1),
    ])


def _notes(role: str) -> str:
    return _link(JPCRP_ROLE + role, [
        (_p("NotesHeading"), _p("CashAndDeposits"), 1),
    ])


def _annual_pre() -> str:
    return _pre([
        _link(JPCRP_ROLE + "rol_CoverPage", [(_p("CoverPageHeading"), _p("CompanyName"), 1)]),
        _bs("rol_ConsolidatedBalanceSheet"),
        _pl("rol_ConsolidatedStatementOfIncome"),
        _ci("rol_ConsolidatedStatementOfComprehensiveIncome"),
        _ss("rol_ConsolidatedStatementOfChangesInEquity"),
        _cf("rol_ConsolidatedStatementOfCashFlows-indirect"),
        _notes("rol_NotesConsolidatedBalanceSheet"),
        _notes("rol_NotesConsolidatedStatementOfCashFlows"),
        _bs("rol_BalanceSheet", consolidated=False),
        _pl("rol_StatementOfIncome"),
        _ss("rol_StatementOfChangesInEquity"),
    ])


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _keys(items) -> list[str]:
    return [("ext:" if i.is_custom else "") + i.local_name + (f"/{i.preferred_label}" if i.preferred_label else "")
            for i in items]


def test_classify_role() -> None:
    cases = {
        "rol_ConsolidatedBalanceSheet": StatementRole("BS", True, False),
        "rol_BalanceSheet": StatementRole("BS", False, False),
        "rol_ConsolidatedStatementOfIncome": StatementRole("PL", True, False),
        "rol_ConsolidatedStatementOfComprehensiveIncome": StatementRole("PL", True, False),
        "rol_ConsolidatedStatementOfComprehensiveIncomeSingleStatement": StatementRole("PL", True, False),
        "rol_ConsolidatedStatementOfCashFlows-indirect": StatementRole("CF", True, False),
        "rol_StatementOfCashFlows-direct": StatementRole("CF", False, False),
        "rol_ConsolidatedStatementOfFinancialPositionIFRS": StatementRole("BS", True, True),
        "rol_ConsolidatedStatementOfProfitOrLossIFRS": StatementRole("PL", True, True),
        "rol_CondensedSemiAnnualConsolidatedStatementOfCashFlowsIFRS": StatementRole("CF", True, True),
        "rol_Type1SemiAnnualConsolidatedBalanceSheet": StatementRole("BS", True, False),
        "rol_YearToQuarterEndConsolidatedStatementOfIncome": StatementRole("PL", True, False),
        "rol_QuarterlyConsolidatedBalanceSheetTDnet": StatementRole("BS", True, False),
        "rol_QuarterlyBalanceSheetTDnet": StatementRole("BS", False, False),
        "rol_CondensedQuarterlyStatementOfFinancialPositionTDnetIFRS": StatementRole("BS", False, True),
        "rol_CondensedYearToQuarterEndStatementOfProfitOrLossTDnetIFRS": StatementRole("PL", False, True),
    }
    for name, want in cases.items():
        assert classify_role(name) == want, name
    for name in (
        "rol_NotesConsolidatedBalanceSheet",
        "rol_NotesConsolidatedStatementOfCashFlows",
        "rol_ConsolidatedStatementOfChangesInEquity",
        "rol_CondensedSemiAnnualConsolidatedStatementOfChangesInEquityIFRS",
        "rol_StatementOfUnitholdersEquity",
        "rol_CoverPage",
        "rol_BusinessResultsOfGroup",
        "RoleAttachedDocument",
        "ConsolidatedBalanceSheet",
    ):
        assert classify_role(name) is None, name


def test_annual_jpgaap_roles_and_order(tmp: Path) -> None:
    root = _write(tmp / "unpacked", {
        f"XBRL/PublicDoc/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml": _annual_pre(),
        f"XBRL/PublicDoc/{COMPANY_XSD}": _company_xsd("WidgetsCA"),
        "XBRL/AuditDoc/jpaud-aar-cn-001_E99999-000_pre.xml": _pre([_bs("rol_AuditBalanceSheet")]),
    })
    roles = read_presentation(root)
    assert [r.role for r in roles][:3] == ["rol_CoverPage", "rol_ConsolidatedBalanceSheet",
                                           "rol_ConsolidatedStatementOfIncome"]
    assert "rol_AuditBalanceSheet" not in {r.role for r in roles}
    kept = statement_roles(roles)
    assert [(r.role, k.statement, k.is_consolidated, k.is_ifrs) for r, k in kept] == [
        ("rol_ConsolidatedBalanceSheet", "BS", True, False),
        ("rol_ConsolidatedStatementOfIncome", "PL", True, False),
        ("rol_ConsolidatedStatementOfComprehensiveIncome", "PL", True, False),
        ("rol_ConsolidatedStatementOfCashFlows-indirect", "CF", True, False),
        ("rol_BalanceSheet", "BS", False, False),
        ("rol_StatementOfIncome", "PL", False, False),
    ]
    by_role = {r.role: r for r, _ in kept}
    assert _keys(by_role["rol_ConsolidatedBalanceSheet"].items) == [
        "AssetsAbstract", "CashAndDeposits", "ext:WidgetsCA", "Assets/totalLabel",
        "Liabilities/totalLabel", "NetAssets/totalLabel",
    ]
    custom = by_role["rol_ConsolidatedBalanceSheet"].items[2]
    assert (custom.namespace_key, custom.local_name, custom.is_custom) == (COMPANY_PREFIX, "WidgetsCA", True)
    cash = by_role["rol_ConsolidatedBalanceSheet"].items[1]
    assert (cash.namespace_key, cash.is_custom) == ("jppfs_cor", False)
    assert _keys(by_role["rol_ConsolidatedStatementOfIncome"].items) == [
        "NetSales", "CostOfSales", "GrossProfit", "OperatingIncome", "IncomeBeforeIncomeTaxes", "ProfitLoss",
    ]
    assert _keys(by_role["rol_ConsolidatedStatementOfCashFlows-indirect"].items) == [
        "IncomeBeforeIncomeTaxes", "NetCashProvidedByUsedInOperatingActivities",
        "NetIncreaseDecreaseInCashAndCashEquivalents",
        "CashAndCashEquivalents/periodStartLabel", "CashAndCashEquivalents/periodEndLabel",
    ]


def test_depth_counts_kept_ancestors_only(tmp: Path) -> None:
    link = _link(JPPFS_ROLE + "rol_BalanceSheet", [
        (_p("BalanceSheetHeading"), _p("BalanceSheetTable"), 1),
        (_p("BalanceSheetTable"), _p("BalanceSheetLineItems"), 1),
        (_p("BalanceSheetLineItems"), _p("AssetsAbstract"), 1),
        (_p("AssetsAbstract"), _p("PropertyPlantAndEquipmentAbstract"), 1),
        (_p("PropertyPlantAndEquipmentAbstract"), _p("PropertyPlantAndEquipment"), 1, "totalLabel"),
        (_p("PropertyPlantAndEquipmentAbstract"), _p("BuildingsAbstract"), 2),
        (_p("BuildingsAbstract"), _p("Buildings"), 1),
        (_p("Buildings"), _p("AccumulatedDepreciationBuildings"), 1),
        (_p("AssetsAbstract"), _p("LandTable"), 2),
        (_p("LandTable"), _p("LandLineItems"), 1),
        (_p("LandLineItems"), _p("Land"), 1),
        (_p("AssetsAbstract"), _p("Assets"), 3, "totalLabel"),
        (_p("BalanceSheetLineItems"), _p("LiabilitiesAndNetAssets"), 2, "totalLabel"),
    ])
    root = _write(tmp / "unpacked", {"XBRLData/Attachment/x-pre.xml": _pre([link])})
    (role, _), = statement_roles(read_presentation(root))
    assert [(i.local_name, i.depth) for i in role.items] == [
        ("AssetsAbstract", 0), ("PropertyPlantAndEquipmentAbstract", 1), ("PropertyPlantAndEquipment", 2),
        ("BuildingsAbstract", 2), ("Buildings", 3), ("AccumulatedDepreciationBuildings", 4), ("Land", 1),
        ("Assets", 1), ("LiabilitiesAndNetAssets", 0),
    ]
    assert role.items[2].as_json() == ["jppfs_cor", "PropertyPlantAndEquipment", 0, "totalLabel", 2, None]


LAB_ROLE = "http://www.xbrl.org/2003/role/label"
CONSOLIDATED_LABEL = "http://disclosure.edinet-fsa.go.jp/jppfs/Consolidated/role/label"


def _labels_xml(base: str, entries: list[tuple[str, str, str, str]]) -> str:
    """entries: (要素の id, 役割, 言語, ラベル)。"""
    body = []
    for n, (el, role, lang, body_text) in enumerate(entries):
        body.append(f'<link:loc xlink:type="locator" xlink:href="{base}#{el}" xlink:label="c{n}"/>')
        body.append(f'<link:label xlink:type="resource" xlink:label="l{n}" xlink:role="{role}" xml:lang="{lang}">'
                    f"{body_text}</link:label>")
        body.append(f'<link:labelArc xlink:type="arc" xlink:arcrole="http://www.xbrl.org/2003/arcrole/concept-label" '
                    f'xlink:from="c{n}" xlink:to="l{n}"/>')
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<link:linkbase xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:link="http://www.xbrl.org/2003/linkbase">\n'
        '<link:labelLink xlink:type="extended" xlink:role="http://www.xbrl.org/2003/role/link">\n'
        + "\n".join(body) + "\n</link:labelLink></link:linkbase>"
    )


def test_headings_get_company_then_taxonomy_labels(tmp: Path) -> None:
    taxonomy = tmp / "taxonomy"
    remote_lab = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/label/jppfs_2024-11-01_lab.xml"
    lab_path = taxonomy / "disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/label/jppfs_2024-11-01_lab.xml"
    lab_path.parent.mkdir(parents=True)
    xsd = taxonomy / "disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor_2024-11-01.xsd"
    xsd.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        + "".join(f'<xsd:element id="jppfs_cor_{n}" name="{n}"/>' for n in (
            "AssetsAbstract", "CurrentAssetsAbstract", "ValuationAndTranslationAdjustmentsAbstract"))
        + "</xsd:schema>", encoding="utf-8")
    lab_path.write_text(_labels_xml("../jppfs_cor_2024-11-01.xsd", [
        ("jppfs_cor_AssetsAbstract", LAB_ROLE, "ja", "資産の部"),
        ("jppfs_cor_AssetsAbstract", LAB_ROLE, "en", "Assets"),
        ("jppfs_cor_CurrentAssetsAbstract", LAB_ROLE, "ja", "流動資産"),
        ("jppfs_cor_ValuationAndTranslationAdjustmentsAbstract", LAB_ROLE, "ja", "評価・換算差額等"),
        ("jppfs_cor_ValuationAndTranslationAdjustmentsAbstract", CONSOLIDATED_LABEL, "ja", "その他の包括利益累計額"),
    ]), encoding="utf-8")
    company_lab = f"{COMPANY_XSD.removesuffix('.xsd')}_lab.xml"
    company_xsd = _company_xsd("WidgetsAbstract", "WidgetsCA").replace(
        "</xsd:schema>",
        f'<link:linkbaseRef xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xlink:type="simple" xlink:href="{company_lab}" xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>'
        f'<link:linkbaseRef xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xlink:type="simple" xlink:href="{remote_lab}" xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>'
        "</xsd:schema>")
    link = _link(JPPFS_ROLE + "rol_ConsolidatedBalanceSheet", [
        (_p("BalanceSheetHeading"), _p("AssetsAbstract"), 1),
        (_p("AssetsAbstract"), _p("CurrentAssetsAbstract"), 1),
        (_p("CurrentAssetsAbstract"), _c("WidgetsAbstract"), 1),
        (_c("WidgetsAbstract"), _c("WidgetsCA"), 1),
        (_p("AssetsAbstract"), _p("ValuationAndTranslationAdjustmentsAbstract"), 2,
         "CONSOLIDATED"),
        (_p("ValuationAndTranslationAdjustmentsAbstract"), _p("CashAndDeposits"), 1),
    ]).replace("http://www.xbrl.org/2003/role/CONSOLIDATED", CONSOLIDATED_LABEL)
    root = _write(tmp / "unpacked", {
        f"XBRL/PublicDoc/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml": _pre([link]),
        f"XBRL/PublicDoc/{COMPANY_XSD}": company_xsd,
        f"XBRL/PublicDoc/{company_lab}": _labels_xml(COMPANY_XSD, [
            (f"{COMPANY_PREFIX}_WidgetsAbstract", LAB_ROLE, "ja", "ウィジェット"),
            ("jppfs_cor_CurrentAssetsAbstract", LAB_ROLE, "ja", "流動資産の部"),
        ]).replace(f'{COMPANY_XSD}#jppfs_cor_', f"{PPFS}#jppfs_cor_"),
    })
    (role, _), = statement_roles(read_presentation(root, taxonomy))
    assert [(i.local_name, i.label) for i in role.items] == [
        ("AssetsAbstract", "資産の部"), ("CurrentAssetsAbstract", "流動資産の部"), ("WidgetsAbstract", "ウィジェット"),
        ("WidgetsCA", None), ("ValuationAndTranslationAdjustmentsAbstract", "その他の包括利益累計額"),
        ("CashAndDeposits", None),
    ]
    assert role.items[4].preferred_label == "label"
    (role, _), = statement_roles(read_presentation(root, tmp / "missing"))
    assert role.items[0].label is None and role.items[2].label == "ウィジェット"


def test_ifrs_quarterly_tdnet_two_sets(tmp: Path) -> None:
    ifrs_bs = _link(
        "http://disclosure.edinet-fsa.go.jp/role/jpigp/rol_CondensedQuarterlyStatementOfFinancialPositionTDnetIFRS",
        [("jpigp_cor_StatementOfFinancialPositionIFRSHeading", "jpigp_cor_CashAndCashEquivalentsIFRS", 1),
         ("jpigp_cor_StatementOfFinancialPositionIFRSHeading", "jpigp_cor_AssetsIFRS", 2, "totalLabel")],
    )
    ifrs_pl = _link(
        "http://disclosure.edinet-fsa.go.jp/role/jpigp/rol_CondensedYearToQuarterEndStatementOfProfitOrLossTDnetIFRS",
        [("jpigp_cor_StatementOfProfitOrLossIFRSHeading", "jpigp_cor_RevenueIFRS", 1)],
    )
    root = _write(tmp / "unpacked", {
        "XBRLData/Attachment/tse-qcedjpfr-99990-2026-06-30-01-2026-08-10-pre.xml": _pre([
            _bs("rol_QuarterlyBalanceSheetTDnet", consolidated=False),
            _pl("rol_YearToQuarterEndStatementOfIncomeTDnet"),
            ifrs_bs,
            ifrs_pl,
        ]),
    })
    kept = statement_roles(read_presentation(root))
    assert [(r.role, k.statement, k.is_consolidated, k.is_ifrs) for r, k in kept] == [
        ("rol_QuarterlyBalanceSheetTDnet", "BS", False, False),
        ("rol_YearToQuarterEndStatementOfIncomeTDnet", "PL", False, False),
        ("rol_CondensedQuarterlyStatementOfFinancialPositionTDnetIFRS", "BS", False, True),
        ("rol_CondensedYearToQuarterEndStatementOfProfitOrLossTDnetIFRS", "PL", False, True),
    ]
    ifrs_items = kept[2][0].items
    assert [(i.namespace_key, i.local_name, i.is_custom, i.preferred_label) for i in ifrs_items] == [
        ("jpigp_cor", "CashAndCashEquivalentsIFRS", False, None),
        ("jpigp_cor", "AssetsIFRS", False, "totalLabel"),
    ]


def test_same_element_under_two_loc_labels(tmp: Path) -> None:
    loc = '<link:loc xlink:type="locator" xlink:href="{h}" xlink:label="{lab}"/>'
    arc = ('<link:presentationArc xlink:type="arc" xlink:arcrole="http://www.xbrl.org/2003/arcrole/parent-child" '
           'xlink:from="{a}" xlink:to="{b}" order="{o}"/>')
    body = "\n".join([
        loc.format(h=_href(_p("CashAndDeposits")), lab="cash"),
        loc.format(h=_href(_p("AssetsAbstract")), lab="assets_parent"),
        loc.format(h=_href(_p("Assets")), lab="total"),
        loc.format(h=_href(_p("AssetsAbstract")), lab="assets_child"),
        loc.format(h=_href(_p("BalanceSheetLineItems")), lab="items"),
        arc.format(a="assets_parent", b="total", o=2),
        arc.format(a="assets_parent", b="cash", o=1),
        arc.format(a="items", b="assets_child", o=1),
    ])
    link = (f'<link:presentationLink xlink:type="extended" xlink:role="{JPPFS_ROLE}rol_BalanceSheet">\n'
            f"{body}\n</link:presentationLink>")
    root = _write(tmp / "unpacked", {"XBRLData/Attachment/x-pre.xml": _pre([link])})
    (role, _), = statement_roles(read_presentation(root))
    assert _keys(role.items) == ["AssetsAbstract", "CashAndDeposits", "Assets"]


def test_broken_and_missing_files(tmp: Path) -> None:
    root = _write(tmp / "unpacked", {
        "XBRLData/Attachment/a-pre.xml": "<link:linkbase",
        "XBRLData/Attachment/b-pre.xml": _pre([_pl("rol_StatementOfIncome")]),
    })
    assert [r.role for r, _ in statement_roles(read_presentation(root))] == ["rol_StatementOfIncome"]
    assert read_presentation(_write(tmp / "empty", {"x.txt": ""})) == []


def _rows(sql: str, params: dict | None = None) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params or {})]


def _ingest(db, zpath: Path, doc_id: str = "pre1") -> XbrlFiling:
    return parse_zip_into_filing(
        db,
        zip_path=zpath,
        code=CODE,
        doc_id=doc_id,
        title="2026年3月期 決算短信〔日本基準〕（連結）",
        raw_path=f"9999/{zpath.name}",
        raw_sha256="x",
        submitted_at="2026-04-25 15:00:00",
        cache_dir=zpath.parent / "cache",
    )


def _facts(db, filing_id: int) -> list[tuple]:
    return sorted(
        (f.element_id, f.context_id, str(f.value_num), f.unit)
        for f in db.query(XbrlFact).filter_by(filing_id=filing_id)
    )


def test_ingest_stores_replaces_and_deletes(tmp: Path) -> None:
    db = SessionLocal()
    try:
        seed_stock(db, CODE, "テスト")
        apply_mappings(db)
        plain = _ingest(db, write_zip(tmp, "plain.zip", build_zip(MANUFACTURING)), doc_id="plain")
        assert plain.parse_status == "parsed"
        assert _rows("SELECT count(*) FROM xbrl_presentation_roles WHERE filing_id = :f", {"f": plain.id}) == [(0,)]
        plain_facts = len(_facts(db, plain.id))

        files = {
            f"XBRLData/Attachment/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml": _annual_pre(),
            f"XBRLData/Attachment/{COMPANY_XSD}": _company_xsd("WidgetsCA"),
        }
        zpath = write_zip(tmp, "with-pre.zip", build_zip(MANUFACTURING, extra_files=files))
        filing = _ingest(db, zpath)
        assert filing.parse_status == "parsed"
        assert len(_facts(db, filing.id)) == plain_facts
        got = _rows(
            "SELECT role, seq, statement, is_consolidated, is_ifrs, items_json FROM xbrl_presentation_roles "
            "WHERE filing_id = :f ORDER BY seq", {"f": filing.id},
        )
        assert [r[:5] for r in got] == [
            ("rol_ConsolidatedBalanceSheet", 0, "BS", 1, 0),
            ("rol_ConsolidatedStatementOfIncome", 1, "PL", 1, 0),
            ("rol_ConsolidatedStatementOfComprehensiveIncome", 2, "PL", 1, 0),
            ("rol_ConsolidatedStatementOfCashFlows-indirect", 3, "CF", 1, 0),
            ("rol_BalanceSheet", 4, "BS", 0, 0),
            ("rol_StatementOfIncome", 5, "PL", 0, 0),
        ]
        assert json.loads(got[0][5])[:3] == [
            ["jppfs_cor", "AssetsAbstract", 0, None, 0, None],
            ["jppfs_cor", "CashAndDeposits", 0, None, 1, None],
            [COMPANY_PREFIX, "WidgetsCA", 1, None, 1, None],
        ]
        assert json.loads(got[3][5])[-1] == ["jppfs_cor", "CashAndCashEquivalents", 0, "periodEndLabel", 0, None]
        facts_before = _facts(db, filing.id)

        files[f"XBRLData/Attachment/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml"] = _pre(
            [_cf("rol_StatementOfCashFlows-indirect")])
        _ingest(db, write_zip(tmp, "with-pre.zip", build_zip(MANUFACTURING, extra_files=files)))
        assert _rows("SELECT role, statement, is_consolidated FROM xbrl_presentation_roles WHERE filing_id = :f",
                     {"f": filing.id}) == [("rol_StatementOfCashFlows-indirect", "CF", 0)]
        assert len(_facts(db, filing.id)) == len(facts_before)

        delete_filing_payload(db, filing.id)
        db.commit()
        assert _rows("SELECT count(*) FROM xbrl_presentation_roles WHERE filing_id = :f", {"f": filing.id}) == [(0,)]

        filing = _ingest(db, zpath)
        assert _rows("SELECT count(*) FROM xbrl_presentation_roles WHERE filing_id = :f", {"f": filing.id}) == [(1,)]
        delete_filing(db, filing)
        db.commit()
        assert _rows("SELECT count(*) FROM xbrl_presentation_roles") == [(0,)]
    finally:
        db.close()


def test_broken_presentation_keeps_ingest(tmp: Path) -> None:
    db = SessionLocal()
    try:
        seed_stock(db, CODE, "テスト")
        files = {"XBRLData/Attachment/broken-pre.xml": "<link:linkbase><link:presentationLink"}
        filing = _ingest(db, write_zip(tmp, "broken.zip", build_zip(MANUFACTURING, extra_files=files)), "broken")
        assert filing.parse_status == "parsed"
        assert db.query(XbrlFact).filter_by(filing_id=filing.id).count() > 0
        assert _rows("SELECT count(*) FROM xbrl_presentation_roles WHERE filing_id = :f", {"f": filing.id}) == [(0,)]
    finally:
        db.close()


def test_init_db_adds_table_to_existing_db() -> None:
    from app.db import init_db

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE xbrl_presentation_roles"))
    init_db()
    names = {r[0] for r in _rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "xbrl_presentation_roles" in names

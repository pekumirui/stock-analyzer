"""銘柄の業績詳細（docs/screen/stock.md「業績詳細」）。node で static/js/stock-financials.js の表の組み立てを呼ぶ。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_NODE = r"""
global.$ = () => ({innerHTML: "", textContent: "", hidden: false});
global.document = {querySelectorAll(){ return []; }};
global.fmt = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("ja-JP", {maximumFractionDigits: d, minimumFractionDigits: d});
const fs = require(process.argv[1]);
const d = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  html: fs.fsTableHtml(d),
  empty: fs.fsTableHtml({periods: [], rows: [], empty: {reason: "no_material", message: "本表が無い"}}),
  meta: fs.fsMeta(d),
  sectioned: fs.fsTableHtml(JSON.parse(process.argv[3])),
  mixed: fs.fsTableHtml(JSON.parse(process.argv[4])),
  mixedMeta: fs.fsMeta(JSON.parse(process.argv[4])),
  standards: fs.fsTableHtml(JSON.parse(process.argv[5])),
  deep: fs.fsTableHtml(JSON.parse(process.argv[6])),
  deepAll: fs.fsTableHtml(JSON.parse(process.argv[6]), {all: true}),
  sectionedAll: fs.fsTableHtml(JSON.parse(process.argv[3]), {all: true}),
  sectionedToggled: fs.fsTableHtml(JSON.parse(process.argv[3]), {id: "BS:", open: new Map([["BS:|/h:資産:資産/h:流動資産:流動資産", false], ["BS:|/h:負債:負債", true]])}),
  trees: Object.fromEntries(Object.entries(JSON.parse(process.argv[7])).map(([name, t]) => {
    const out = [];
    const walk = (n, d) => { out.push([d, n.row ? n.row.label : "[" + n.name + "]", n.basic, n.hasBasic]); n.children.forEach(c => walk(c, d + 1)); };
    fs.fsTree(t.rows, t.statement).forEach(n => walk(n, 0));
    return [name, out];
  })),
  earlier: fs.fsEarlierHtml({...d, cumulative: true, earlier: [JSON.parse(process.argv[3])]}),
  noEarlier: fs.fsEarlierHtml(d),
  amounts: [fs.fsAmount(1234567890, "money"), fs.fsAmount(-2500000, "money"), fs.fsAmount(-400000, "money"),
            fs.fsAmount(41.666, "per_share"), fs.fsAmount(null, "money")],
}));
"""

PAYLOAD = {
    "statement": "BS", "consolidated": True, "basis": "連結", "accounting_standards": ["JPGAAP"],
    "cumulative": False, "unit": "JPY",
    "periods": [
        {"period_end": "2025-03-31", "label": "25.03", "period_type": "FY", "source": "edinet", "source_label": "有報",
         "doc_id": "S100A", "submitted_at": "2025-06-20 15:00", "accounting_standard": "JPGAAP"},
        {"period_end": "2025-06-30", "label": "25.06", "period_type": "Q1", "source": "tdnet_pdf", "source_label": "PDF",
         "doc_id": "D1", "submitted_at": "2025-08-08 15:00", "accounting_standard": "JPGAAP"},
        {"period_end": "2025-12-31", "label": "25.12", "period_type": "Q3", "source": "edinet", "source_label": "有報",
         "doc_id": "S100B", "submitted_at": "2026-02-12 15:00", "accounting_standard": "JPGAAP"},
    ],
    "rows": [
        {"key": "Goodwill", "label": "のれん", "unit": "money", "unmatched_pdf": False,
         "cells": [{"value": 5_000_000, "source": "edinet"}, None, {"value": 6_400_000, "source": "tdnet"}]},
        {"key": "pdf:前受金", "label": "前受金", "unit": "money", "unmatched_pdf": True,
         "cells": [None, {"value": 4_000_000, "source": "tdnet_pdf"}, None]},
        {"key": "EPS", "label": "1株当たり当期純利益", "unit": "per_share", "unmatched_pdf": False,
         "cells": [{"value": -12.5, "source": "edinet"}, None, None]},
    ],
    "empty": None,
}


def _row(key: str, label: str, *heads: str, indent: int = 0) -> dict:
    return {"key": key, "label": label, "unit": "money", "unmatched_pdf": False,
            "heads": [{"key": f"h:{h}", "name": h} for h in heads], "indent": indent,
            "cells": [{"value": 1_000_000, "source": "edinet"}, None, None]}


SECTIONED = {**PAYLOAD, "rows": [
    _row("CashAndDeposits", "現金及び預金", "資産", "流動資産"),
    _row("CurrentAssets", "流動資産合計", "資産", "流動資産"),
    _row("Land", "土地", "資産", "固定資産"),
    _row("Assets", "資産合計", "資産"),
    _row("ShortTermLoansPayable", "借入金", "負債", "流動負債"),
    _row("LongTermLoansPayable", "借入金", "負債", "固定負債"),
    _row("LiabilitiesAndNetAssets", "負債純資産合計"),
]}


MIXED = {**PAYLOAD, "consolidated": None, "basis": "連結・個別", "periods": [
    {**PAYLOAD["periods"][0], "consolidated": False},
    {**PAYLOAD["periods"][1], "consolidated": True},
    {**PAYLOAD["periods"][2], "consolidated": True},
]}


STANDARDS = {**PAYLOAD, "accounting_standards": ["IFRS", "JPGAAP"], "periods": [
    PAYLOAD["periods"][0], {**PAYLOAD["periods"][1], "accounting_standard": "IFRS"},
    {**PAYLOAD["periods"][2], "accounting_standard": "IFRS"},
]}


DEEP = {**PAYLOAD, "rows": [
    _row("Buildings", "建物", "資産の部", "固定資産", "有形固定資産", "鉄道事業固定資産"),
    _row("AccumulatedDepreciation", "減価償却累計額", "資産の部", "固定資産", "有形固定資産", "鉄道事業固定資産", indent=1),
    _row("Land", "土地", "資産の部", "固定資産", "有形固定資産"),
    _row("PropertyPlantAndEquipment", "有形固定資産", "資産の部"),
    _row("BuildingsNet", "建物", "資産の部", indent=1),
    _row("Software", "ソフトウエア", "資産の部", "固定資産", "無形固定資産", "その他", "x", "y", "z"),
]}


def _tree_rows(statement: str, spec: str) -> dict:
    rows, heads = [], []
    for line in spec.strip().splitlines():
        line = line.strip()
        if line.startswith("#"):
            name = line[1:].strip()
            heads = [] if name == "-" else name.split(" > ")
            continue
        label, key = line.split()
        rows.append(_row(key, label, *heads))
    return {"statement": statement, "rows": rows}


TREES = {
    "pl": _tree_rows("PL", """
        # -
        売上高 NetSales
        売上原価 CostOfSales
        売上総利益 GrossProfit
        販売費及び一般管理費 SellingGeneralAndAdministrativeExpenses
        営業利益 OperatingIncome
        # 営業外収益
        受取利息 InterestIncomeNOI
        営業外収益合計 NonOperatingIncome
        # 営業外費用
        支払利息 InterestExpensesNOE
        営業外費用合計 NonOperatingExpenses
        # -
        経常利益 OrdinaryIncome
        税金等調整前当期純利益 IncomeBeforeIncomeTaxes
        法人税、住民税及び事業税 IncomeTaxesCurrent
        法人税等調整額 IncomeTaxesDeferred
        法人税等合計 IncomeTaxes
        当期純利益 ProfitLoss
        非支配株主に帰属する当期純利益 ProfitLossAttributableToNonControllingInterests
        親会社株主に帰属する当期純利益 ProfitLossAttributableToOwnersOfParent
        基本的1株当たり当期利益 BasicEarningsLossPerShareIFRS
        希薄化後1株当たり当期利益 DilutedEarningsLossPerShareIFRS
        # その他の包括利益
        その他有価証券評価差額金 ValuationDifferenceOnAvailableForSaleSecuritiesNetOfTaxOCI
        その他の包括利益 OtherComprehensiveIncome
        # -
        包括利益 ComprehensiveIncome
    """),
    "cf": _tree_rows("CF", """
        # 営業活動によるキャッシュ・フロー
        税金等調整前当期純利益 IncomeBeforeIncomeTaxes
        減価償却費 DepreciationAndAmortizationOpeCF
        その他 OtherNetOpeCF
        小計 SubtotalOpeCF
        法人税等の支払額 IncomeTaxesPaidOpeCF
        営業活動によるキャッシュ・フロー NetCashProvidedByUsedInOperatingActivities
        # 投資活動によるキャッシュ・フロー
        有形固定資産の取得による支出 PurchaseOfPropertyPlantAndEquipmentInvCF
        投資有価証券の取得による支出 PurchaseOfInvestmentSecuritiesInvCF
        投資活動によるキャッシュ・フロー NetCashProvidedByUsedInInvestmentActivities
        # 財務活動によるキャッシュ・フロー
        配当金の支払額 CashDividendsPaidFinCF
        非支配株主への配当金の支払額 DividendsPaidToNonControllingInterestsFinCFIFRS
        自己株式の取得による支出 PurchaseOfTreasuryStockFinCF
        財務活動によるキャッシュ・フロー NetCashProvidedByUsedInFinancingActivities
        # -
        現金及び現金同等物の増減額 NetIncreaseDecreaseInCashAndCashEquivalents
        現金及び現金同等物の期末残高 CashAndCashEquivalents
    """),
    "ifrs_bs": _tree_rows("BS", """
        # 資産 > 流動資産
        現金及び現金同等物 CashAndCashEquivalentsIFRS
        棚卸資産 InventoriesCAIFRS
        流動資産合計 CurrentAssetsIFRS
        # 資産 > 非流動資産
        持分法で会計処理されている投資 InvestmentsAccountedForUsingEquityMethodIFRS
        # 資産 > 非流動資産 > 有形固定資産
        土地 LandAcquisitionCostIFRS
        有形固定資産合計 PropertyPlantAndEquipmentIFRS
        # 資産 > 非流動資産
        無形資産 IntangibleAssetsIFRS
        非流動資産合計 NonCurrentAssetsIFRS
        # 資産
        資産合計 AssetsIFRS
        # 資本
        資本金 ShareCapitalIFRS
        利益剰余金 RetainedEarningsIFRS
        親会社の所有者に帰属する持分合計 EquityAttributableToOwnersOfParentIFRS
        非支配持分 NonControllingInterestsIFRS
        資本合計 EquityIFRS
        # -
        負債及び資本合計 LiabilitiesAndEquityIFRS
    """),
}


def _run() -> dict:
    out = subprocess.run(["node", "-e", _NODE, str(ROOT / "static/js/stock-financials.js"), json.dumps(PAYLOAD),
                          json.dumps(SECTIONED), json.dumps(MIXED), json.dumps(STANDARDS), json.dumps(DEEP),
                          json.dumps(TREES)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _rows(html: str) -> list[str]:
    body = html.split("<tbody>", 1)[1]
    return body.split("<tr")[1:]


def _cells(row: str) -> list[str]:
    return re.findall(r"<td[^>]*>.*?</td>", row)


def test_amounts_are_million_yen_and_per_share_yen() -> None:
    a = _run()["amounts"]
    assert a[0] == "1,235"
    assert a[1] == '<span class="loss">-3</span>'
    assert a[2] == "0"
    assert a[3] == "41.67"
    assert a[4] == ""


def test_header_shows_label_period_type_and_source() -> None:
    html = _run()["html"]
    head = html.split("<tbody>", 1)[0]
    ths = re.findall(r"<th[^>]*>.*?</th>", head)
    assert len(ths) == 4
    assert "科目" in ths[0]
    assert "25.03" in ths[1] and "通期" in ths[1] and "有報" in ths[1]
    assert "25.06" in ths[2] and "1Q" in ths[2] and "PDF" in ths[2]
    assert "25.12" in ths[3] and "3Q" in ths[3]


def test_filled_cell_is_marked_with_its_source() -> None:
    goodwill = _cells(_rows(_run()["html"])[0])
    assert len(goodwill) == 4
    assert "fs-fill" not in goodwill[1]
    assert goodwill[2] == '<td class="num"></td>'
    assert 'class="num fill"' in goodwill[3]
    assert '<span class="qb fs-fill">XBRL</span>' in goodwill[3]
    assert "有報 に無く、XBRL から補った値" in goodwill[3]
    assert goodwill[3].startswith('<td class="num fill"') and ">6<" in goodwill[3]


def test_unmatched_pdf_row_is_marked() -> None:
    rows = _rows(_run()["html"])
    pdf = _cells(rows[1])
    assert rows[1].startswith(' class="pdf"')
    assert "前受金" in pdf[0] and "fs-pdf" in pdf[0] and "PDFのみ" in pdf[0]
    assert "fs-fill" not in rows[1]
    assert "fs-pdf" not in rows[0] and "fs-pdf" not in rows[2]


def test_per_share_row_and_null_cells() -> None:
    eps = _cells(_rows(_run()["html"])[2])
    assert "（円）" in eps[0]
    assert eps[1] == '<td class="num"><span class="loss">-12.50</span></td>'
    assert eps[2] == eps[3] == '<td class="num"></td>'


def test_meta_and_empty() -> None:
    r = _run()
    assert r["meta"] == "連結 / 日本基準 / 単位: 百万円（1株当たりは円）"
    assert r["empty"] == '<tr><td class="l sub" title="本表が無い">データなし</td></tr>'


def _names(html: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", _cells(r)[0]) for r in _rows(html)]


def test_section_totals_become_parents_and_basic_ones_open_first() -> None:
    r = _run()
    assert _names(r["sectioned"]) == ["▾資産合計", "▾流動資産合計", "現金及び預金", "▸固定資産", "▸負債", "負債純資産合計"]
    rows = _rows(r["sectioned"])
    assert rows[0].startswith(' class="fs-par fs-basic"') and 'class="l fs-tog"' in rows[0]
    assert re.search(r'<td class="([^"]*)"', rows[2]).group(1) == "l d2"
    assert rows[3].startswith(' class="fs-sec fs-sec1"') and 'title="固定資産"' in rows[3]
    assert _cells(rows[4])[1] == '<td colspan="3"></td>'


def test_all_view_and_toggled_rows() -> None:
    r = _run()
    assert _names(r["sectionedAll"]) == ["▾資産合計", "▾流動資産合計", "現金及び預金", "▾固定資産", "土地", "▾負債",
                                         "▾流動負債", "借入金", "▾固定負債", "借入金", "負債純資産合計"]
    assert _names(r["sectionedToggled"]) == ["▾資産合計", "▸流動資産合計", "▸固定資産", "▾負債", "▸流動負債", "▸固定負債",
                                             "負債純資産合計"]
    assert 'data-key="BS:|/h:資産:資産/h:流動資産:流動資産" data-open="0"' in r["sectionedToggled"]


def test_pl_profit_stages_hold_what_feeds_them() -> None:
    tree = [(d, name, basic) for d, name, basic, _ in _run()["trees"]["pl"]]
    assert tree == [
        (0, "売上高", True), (0, "売上総利益", True), (1, "売上原価", False),
        (0, "営業利益", True), (1, "販売費及び一般管理費", False),
        (0, "経常利益", True), (1, "営業外収益合計", False), (2, "受取利息", False),
        (1, "営業外費用合計", False), (2, "支払利息", False),
        (0, "税金等調整前当期純利益", True),
        (0, "当期純利益", True), (1, "法人税等合計", False), (2, "法人税、住民税及び事業税", False), (2, "法人税等調整額", False),
        (0, "親会社株主に帰属する当期純利益", True), (1, "非支配株主に帰属する当期純利益", False),
        (0, "基本的1株当たり当期利益", True), (1, "希薄化後1株当たり当期利益", False),
        (0, "その他の包括利益", False), (1, "その他有価証券評価差額金", False),
        (0, "包括利益", False),
    ]


def test_cf_sections_and_basic_items_inside() -> None:
    tree = _run()["trees"]["cf"]
    assert [(d, name, basic) for d, name, basic, _ in tree] == [
        (0, "営業活動によるキャッシュ・フロー", True), (1, "小計", False), (2, "税金等調整前当期純利益", False),
        (2, "減価償却費", True), (2, "その他", False), (1, "法人税等の支払額", False),
        (0, "投資活動によるキャッシュ・フロー", True), (1, "有形固定資産の取得による支出", True), (1, "投資有価証券の取得による支出", False),
        (0, "財務活動によるキャッシュ・フロー", True), (1, "配当金の支払額", True), (1, "非支配株主への配当金の支払額", False),
        (1, "自己株式の取得による支出", True),
        (0, "現金及び現金同等物の増減額", True), (0, "現金及び現金同等物の期末残高", True),
    ]
    assert [name for _, name, _, has in tree if has] == ["営業活動によるキャッシュ・フロー", "小計",
                                                         "投資活動によるキャッシュ・フロー", "財務活動によるキャッシュ・フロー"]


def test_ifrs_bs_nested_sections_and_equity_subtotal() -> None:
    tree = [(d, name, basic) for d, name, basic, _ in _run()["trees"]["ifrs_bs"]]
    assert tree == [
        (0, "資産合計", True), (1, "流動資産合計", True), (2, "現金及び現金同等物", True), (2, "棚卸資産", False),
        (1, "非流動資産合計", True), (2, "持分法で会計処理されている投資", False), (2, "有形固定資産合計", False),
        (3, "土地", False), (2, "無形資産", False),
        (0, "資本合計", True), (1, "親会社の所有者に帰属する持分合計", True), (2, "資本金", False), (2, "利益剰余金", False),
        (1, "非支配持分", False),
        (0, "負債及び資本合計", False),
    ]


def test_flat_table_without_sections() -> None:
    html = _run()["html"]
    assert "fs-sec" not in html
    assert all(re.search(r'<td class="l"', r) for r in _rows(html))


def test_single_columns_are_marked_and_edge_is_drawn() -> None:
    r = _run()
    assert r["mixedMeta"] == "連結・個別 / 日本基準 / 単位: 百万円（1株当たりは円）"
    ths = re.findall(r"<th[^>]*>.*?</th>", r["mixed"].split("<tbody>", 1)[0])
    assert "fs-basis" in ths[1] and "個別" in ths[1] and 'class="fs-edge"' not in ths[1]
    assert "fs-basis" not in ths[2] and ths[2].startswith('<th class="fs-edge"')
    assert "fs-basis" not in ths[3] and "fs-edge" not in ths[3]
    goodwill = _cells(_rows(r["mixed"])[0])
    assert goodwill[2] == '<td class="num fs-edge"></td>'
    assert "fs-edge" not in goodwill[1] + goodwill[3]
    assert "fs-basis" not in r["html"] and "fs-edge" not in r["html"]


def test_standard_is_shown_in_the_header_only_when_columns_differ() -> None:
    r = _run()
    ths = re.findall(r"<th[^>]*>.*?</th>", r["standards"].split("<tbody>", 1)[0])
    assert '<span class="g fs-src fs-std">日本基準</span></th>' in ths[1]
    assert '<span class="g fs-src fs-std">IFRS</span></th>' in ths[2] and "IFRS</span></th>" in ths[3]
    assert "fs-std" not in r["html"] and "fs-std" not in r["mixed"]


def test_heading_rows_of_any_depth_and_indented_rows() -> None:
    r = _run()
    assert _names(r["deep"]) == ["▸資産の部"]
    rows = _rows(r["deepAll"])
    assert _names(r["deepAll"]) == ["▾資産の部", "▾固定資産", "▾有形固定資産", "▾鉄道事業固定資産", "▾建物", "減価償却累計額",
                                    "土地", "▾無形固定資産", "▾その他", "▾x", "▾y", "▾z", "ソフトウエア", "▾有形固定資産", "建物"]
    secs = [r.split('"')[1] for r in rows if r.startswith(' class="fs-sec')]
    assert secs[:4] == ["fs-sec fs-sec0", "fs-sec fs-sec1", "fs-sec fs-sec2", "fs-sec fs-sec3"]
    assert secs[-1] == "fs-sec fs-sec5"
    body = [r for r in rows if not r.startswith(' class="fs-sec')]
    depth = [re.search(r'<td class="([^"]*)"', r).group(1) for r in body]
    assert depth == ["l d4 fs-tog", "l d5", "l d3", "l d6", "l d1 fs-tog", "l d2"]


def test_tables_of_the_earlier_standard_follow_the_main_table() -> None:
    r = _run()
    assert r["noEarlier"] == ""
    assert r["earlier"].startswith('<p class="sub fs-earlier">日本基準の期 / 25.03〜25.12 / 連結</p>'
                                   '<div class="tbl-wrap"><table class="sheet fin fs"><thead>')
    assert r["earlier"].count("<table") == 1 and "科目（累計）" in r["earlier"] and "fs-sec" in r["earlier"]

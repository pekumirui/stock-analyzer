"""財務3表: 有報・短信 XBRL・短信 PDF を要素でそろえて 1 枚の表にする（docs/jobs/xbrl.md「財務3表」）。"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.xbrl import three_statements as fst
from tools.xbrl.heading_order import adjacent_same_headings, diff_dumps, emitted_heading_names, uchiwake_count

M = 1_000_000
CODE = "12340"
LOOSE = "56780"
SINGLE = "24680"
NAMED = "13570"
QUARTER = "36790"
TWICE = "22820"
OPENING = "46810"
AMENDED = "57910"
NONCONSOL = {"jppfs_cor:ConsolidatedOrNonConsolidatedAxis": "jppfs_cor:NonConsolidatedMember"}
SEGMENT = {"jpcrp_cor:OperatingSegmentsAxis": "jpcrp_cor:ReportableSegmentsMember"}

LABELS = {
    "CashAndDeposits": "現金及び預金", "OtherCA": "その他", "CurrentAssets": "流動資産",
    "Land": "土地", "OtherPPE": "その他", "PropertyPlantAndEquipment": "有形固定資産", "Goodwill": "のれん",
    "OtherIA": "その他", "IntangibleAssets": "無形固定資産", "Assets": "資産",
    "ShortTermLoansPayable": "短期借入金", "OtherCL": "その他", "Liabilities": "負債",
    "NetAssets": "純資産", "LiabilitiesAndNetAssets": "負債純資産",
    "NetSales": "売上高", "CostOfSales": "売上原価", "OperatingIncome": "営業利益",
    "InterestExpensesNOE": "支払利息", "IncomeBeforeIncomeTaxes": "税引前当期純利益又は税引前当期純損失（△）",
    "ProfitLoss": "当期純利益又は当期純損失（△）",
    "DepreciationAndAmortizationOpeCF": "減価償却費",
    "NetCashProvidedByUsedInOperatingActivities": "営業活動によるキャッシュ・フロー",
    "NetIncreaseDecreaseInCashAndCashEquivalents": "現金及び現金同等物の増減額（△は減少）",
    "CashAndCashEquivalents": "現金及び現金同等物の残高",
    "BondsPayable": "社債", "BorrowingsCLIFRS": "借入金", "BorrowingsNCLIFRS": "借入金",
    "ProfitLossAttributableToOwnersOfParent": "親会社株主に帰属する当期純利益",
    "ProfitLossAttributableToNonControllingInterests": "非支配株主に帰属する当期純利益",
    "ComprehensiveIncome": "包括利益", "ComprehensiveIncomeAttributableToOwnersOfTheParent": "親会社株主に係る包括利益",
    "ComprehensiveIncomeAttributableToNonControllingInterests": "非支配株主に係る包括利益",
    "NotesAndAccountsReceivableTrade": "売掛金", "AccountsReceivableTrade": "売掛金", "GuaranteeDeposits": "差入保証金",
    "GoodwillIFRS": "のれん",
    "ImpairmentLossesPL": "減損損失", "FinanceCosts": "金融費用", "ShareOnly": "持分法による投資損失",
    "TotalExpenses": "費用合計", "OtherAdj": "その他調整", "AdjNew": "利益に対する調整項目合計",
    "SaleOfShares": "子会社株式売却損益",
    "ValuationDifferenceOnAvailableForSaleSecurities": "その他有価証券評価差額金",
    "ValuationAndTranslationAdjustments": "その他の包括利益累計額合計",
    "AociOldTotal": "その他の包括利益累計額",
}


class Seeder:
    def __init__(self) -> None:
        self.db = SessionLocal()
        self.elements: dict[tuple[str, str], int] = {}
        self.contexts: dict[tuple[int, str], int] = {}
        self.next_fact = 1000

    def _run(self, sql: str, params: dict) -> None:
        self.db.execute(text(sql), params)

    def stock(self, code: str, name: str) -> None:
        self._run("INSERT INTO stocks (code, name) VALUES (:c, :n)", {"c": code, "n": name})

    def filing(self, fid: int, source: str, period_type: str, end: str, submitted: str, *,
               code: str = CODE, standard: str = "JPGAAP", doc_type: str | None = None) -> None:
        kind = doc_type or ("annual" if period_type == "FY" else "quarterly")
        self._run(
            "INSERT INTO xbrl_filings (id, code, source, doc_id, doc_type, period_type, fiscal_year_end, "
            "submitted_at, accounting_standard, is_amendment, raw_path, parse_status, created_at) "
            "VALUES (:id, :code, :source, :doc, :kind, :pt, :end, :sub, :std, 0, 'x', 'parsed', '2026-01-01')",
            {"id": fid, "code": code, "source": source, "doc": f"D{fid}", "kind": kind, "pt": period_type,
             "end": end, "sub": submitted, "std": standard},
        )

    def _element(self, ns: str, local: str) -> int:
        key = (ns, local)
        if key not in self.elements:
            self.elements[key] = len(self.elements) + 1
            label = local.split("#")[0] if ns.startswith("pdf-") else LABELS.get(local, local)
            self._run(
                "INSERT INTO xbrl_elements (id, namespace_uri, namespace_key, local_name, label_ja, is_custom, "
                "first_seen_at) VALUES (:id, :uri, :ns, :local, :label, :custom, '2026-01-01')",
                {"id": self.elements[key], "uri": f"uri:{ns}", "ns": ns, "local": local, "label": label,
                 "custom": 0 if ns in fst.STANDARD_NS else 1},
            )
        return self.elements[key]

    def _context(self, fid: int, kind: str, end: str, *, consolidated: bool, dims: dict | None,
                 relative: str | None, start: str = "2024-04-01") -> int:
        dims = dims or {}
        ref = f"{kind}{'' if consolidated else '_NC'}{json.dumps(dims, sort_keys=True)}{relative}{start}"
        key = (fid, ref)
        if key not in self.contexts:
            self.contexts[key] = len(self.contexts) + 1
            self._run(
                "INSERT INTO xbrl_contexts (id, filing_id, context_ref, period_type, start_date, end_date, "
                "is_consolidated, relative_period, dimensions_json, has_dimensions) "
                "VALUES (:id, :fid, :ref, :pt, :start, :end, :consol, :rel, :dims, :has)",
                {"id": self.contexts[key], "fid": fid, "ref": ref, "pt": "instant" if kind == "i" else "duration",
                 "start": None if kind == "i" else start, "end": end, "consol": 1 if consolidated else 0,
                 "rel": relative, "dims": json.dumps(dims), "has": 1 if dims else 0},
            )
        return self.contexts[key]

    def facts(self, fid: int, end: str, rows: list[tuple], *, consolidated: bool = True,
              dims: dict | None = None, relative: str | None = "current", start: str = "2024-04-01") -> None:
        for row in rows:
            ns, local, kind, value = row[:4]
            unit = row[4] if len(row) > 4 else "JPY"
            ctx = self._context(fid, kind, end, consolidated=consolidated, dims=dims, relative=relative, start=start)
            self.next_fact += 1
            self._run(
                "INSERT INTO xbrl_facts (id, filing_id, element_id, context_id, unit, value_num, is_nil) "
                "VALUES (:id, :fid, :el, :ctx, :unit, :v, 0)",
                {"id": self.next_fact, "fid": fid, "el": self._element(ns, local), "ctx": ctx, "unit": unit,
                 "v": value},
            )

    def text_fact(self, fid: int, ns: str, local: str, value: str) -> None:
        ctx = self._context(fid, "i", "2025-03-31", consolidated=True, dims=None, relative=None)
        self.next_fact += 1
        self._run(
            "INSERT INTO xbrl_facts (id, filing_id, element_id, context_id, value_text, is_nil) "
            "VALUES (:id, :fid, :el, :ctx, :t, 0)",
            {"id": self.next_fact, "fid": fid, "el": self._element(ns, local), "ctx": ctx, "t": value},
        )

    def roles(self, fid: int, roles: list[tuple], *, status: str = "ok") -> None:
        for seq, (role, statement, consolidated, ifrs, items) in enumerate(roles):
            body = [i if isinstance(i, list) else _role_item(i) for i in items]
            self._run(
                "INSERT INTO xbrl_presentation_roles (filing_id, role, seq, statement, is_consolidated, is_ifrs, "
                "items_json, generated_at) VALUES (:fid, :role, :seq, :st, :c, :i, :items, '2026-01-01')",
                {"fid": fid, "role": role, "seq": seq, "st": statement, "c": int(consolidated), "i": int(ifrs),
                 "items": json.dumps(body)},
            )
        self._run("UPDATE xbrl_filings SET presentation_status = :s WHERE id = :fid", {"s": status, "fid": fid})

    def close(self) -> None:
        self.db.commit()
        self.db.close()


def _role_item(item: str | tuple) -> list:
    local, pref, ns = (*item, None)[:3] if isinstance(item, tuple) else (item, None, None)
    return [ns or ("jpigp_cor" if local.endswith("IFRS") else "jppfs_cor"), local, int(ns is not None), pref]


def x(local: str, kind: str, value: float, ns: str = "jppfs_cor") -> tuple:
    return (ns, local, kind, value * M)


def p(statement: str, local: str, value: float) -> tuple:
    kind = "i" if statement == "bs" else "d"
    return (f"pdf-{statement}", local, kind, value * M)


FY_BS = [
    x("CashAndDeposits", "i", 100), x("OtherCA", "i", 7), x("CurrentAssets", "i", 107),
    x("Land", "i", 13), x("OtherPPE", "i", 20), x("PropertyPlantAndEquipment", "i", 33),
    x("Goodwill", "i", 5), x("OtherIA", "i", 9), x("IntangibleAssets", "i", 14), x("Assets", "i", 154),
    x("ShortTermLoansPayable", "i", 40), x("OtherCL", "i", 7), x("Liabilities", "i", 47),
    x("NetAssets", "i", 107), x("LiabilitiesAndNetAssets", "i", 154),
]
FY_PL = [
    x("NetSales", "d", 500), x("CostOfSales", "d", 300), x("OperatingIncome", "d", 200),
    x("InterestExpensesNOE", "d", 2), x("IncomeBeforeIncomeTaxes", "d", 198), x("ProfitLoss", "d", 150),
]
FY_SS = [x("DividendsFromSurplus", "d", -30), x("TotalChangesOfItemsDuringThePeriod", "d", 120)]
FY_CF = [
    x("DepreciationAndAmortizationOpeCF", "d", 25), x("NetCashProvidedByUsedInOperatingActivities", "d", 180),
    x("NetIncreaseDecreaseInCashAndCashEquivalents", "d", 60), x("CashAndCashEquivalents", "i", 95),
]
FY_NOTES = [x("EmployeesSalariesSGA", "d", 33)]

FY_PDF_BS = [
    p("bs", "現金及び預金", 100), p("bs", "その他", 7), p("bs", "流動資産合計", 107),
    p("bs", "有形固定資産土地", 13), p("bs", "その他#2", 20), p("bs", "有形固定資産合計", 33),
    p("bs", "無形固定資産のれん", 5), p("bs", "その他#3", 9), p("bs", "無形固定資産合計", 14),
    p("bs", "資産合計", 154), p("bs", "短期借入金", 40), p("bs", "その他#4", 7), p("bs", "負債合計", 47),
    p("bs", "純資産合計", 107), p("bs", "負債純資産合計", 154),
]
FY_PDF_PL = [
    p("pl", "売上高", 500), p("pl", "売上原価", 300), p("pl", "営業利益", 200), p("pl", "支払利息", -2),
    p("pl", "税金等調整前当期純利益", 198), p("pl", "当期純利益", 150),
]
FY_PDF_CF = [
    p("cf", "税金等調整前当期純利益", 198), p("cf", "減価償却費", 25), p("cf", "営業活動によるキャッシュ・フロー", 180),
    p("cf", "現金及び現金同等物の増減額", 60), p("cf", "現金及び現金同等物の期首残高", 35),
    p("cf", "現金及び現金同等物の期末残高", 95),
]

Q_BS_XBRL = [
    x("CashAndDeposits", "i", 80), x("OtherCA", "i", 6), x("CurrentAssets", "i", 86),
    x("Goodwill", "i", 4), x("OtherIA", "i", 8), x("IntangibleAssets", "i", 12), x("Assets", "i", 98),
    x("ShortTermLoansPayable", "i", 30), x("OtherCL", "i", 8), x("Liabilities", "i", 38),
    x("NetAssets", "i", 60), x("LiabilitiesAndNetAssets", "i", 98),
]
Q_PDF_BS = [
    p("bs", "現金及び預金", 80), p("bs", "その他", 6), p("bs", "流動資産合計", 86),
    p("bs", "無形固定資産のれん", 4), p("bs", "その他#2", 8), p("bs", "無形固定資産合計", 12),
    p("bs", "資産合計", 98), p("bs", "短期借入金", 30), p("bs", "その他#3", 8), p("bs", "負債合計", 38),
    p("bs", "純資産合計", 60), p("bs", "負債純資産合計", 98),
]
TARGET_PDF_BS = [
    p("bs", "現金及び預金", 90), p("bs", "その他", 5), p("bs", "流動資産合計", 95),
    p("bs", "無形固定資産のれん", 3), p("bs", "その他#2", 11), p("bs", "無形固定資産合計", 14),
    p("bs", "資産合計", 109), p("bs", "短期借入金", 33), p("bs", "前受金", 4), p("bs", "その他#3", 6),
    p("bs", "負債合計", 43), p("bs", "純資産合計", 66), p("bs", "負債純資産合計", 109),
]
TARGET_PDF_PL = [
    p("pl", "売上高", 120), p("pl", "売上原価", 70), p("pl", "営業利益", 50), p("pl", "支払利息", -4),
    p("pl", "税金等調整前当期純利益", 46), p("pl", "当期純利益", 30),
]


def _company(s: Seeder, code: str, base: int, *, with_q_pair: bool = True) -> None:
    s.stock(code, "テスト")
    s.filing(base + 1, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=code)
    s.facts(base + 1, "2025-03-31", FY_BS + FY_PL + FY_SS + FY_CF + FY_NOTES)
    s.facts(base + 1, "2025-03-31", [x("CashAndDeposits", "i", 50), x("Assets", "i", 50)],
            consolidated=False, dims=NONCONSOL)
    s.facts(base + 1, "2025-03-31", [x("NetSales", "d", 999)], dims=SEGMENT)
    s.facts(base + 1, "2025-03-31", [x("CashAndDeposits", "i", 77)], relative=None)
    s.text_fact(base + 1, "jpdei_cor", fst.DEI_CONSOLIDATED, "true")
    s.facts(base + 1, "2025-03-31", [("jpcrp_cor", "NumberOfEmployees", "i", 12, "pure")])
    s.filing(base + 2, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00", code=code)
    s.facts(base + 2, "2025-03-31", FY_PDF_BS + FY_PDF_PL + FY_PDF_CF)
    if with_q_pair:
        s.filing(base + 3, "edinet", "Q2", "2024-09-30", "2024-11-12 15:00:00", code=code, doc_type="semiannual")
        s.facts(base + 3, "2024-09-30", Q_BS_XBRL)
        s.filing(base + 4, "tdnet_pdf", "Q2", "2024-09-30", "2024-11-08 15:00:00", code=code)
        s.facts(base + 4, "2024-09-30", Q_PDF_BS)
    s.filing(base + 5, "tdnet_pdf", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=code)
    s.facts(base + 5, "2025-06-30", TARGET_PDF_BS + TARGET_PDF_PL)
    s.filing(base + 6, "tdnet", "Q2", "2025-09-30", "2025-11-10 15:00:00", code=code)
    s.facts(base + 6, "2025-09-30", [
        x("CashAndDeposits", "i", 70), x("OtherCA", "i", 4), x("CurrentAssets", "i", 74), x("Assets", "i", 74),
        x("LiabilitiesAndNetAssets", "i", 74),
        x("NetSales", "d", 250), x("IncomeBeforeIncomeTaxes", "d", 90), x("ProfitLoss", "d", 60),
        x("DepreciationAndAmortizationOpeCF", "d", 12), x("NetCashProvidedByUsedInOperatingActivities", "d", 70),
        x("NetIncreaseDecreaseInCashAndCashEquivalents", "d", -10), x("CashAndCashEquivalents", "i", 85),
    ])
    s.filing(base + 7, "tdnet", "Q2", "2025-09-30", "2025-11-01 15:00:00", code=code)
    s.facts(base + 7, "2025-09-30", [x("CashAndDeposits", "i", 1), x("Assets", "i", 1)])
    s.filing(base + 8, "edinet", "Q3", "2025-12-31", "2026-02-12 15:00:00", code=code)
    s.facts(base + 8, "2025-12-31", [
        x("CashAndDeposits", "i", 60), x("CurrentAssets", "i", 60), x("Assets", "i", 66),
        x("LiabilitiesAndNetAssets", "i", 66),
    ])
    s.filing(base + 9, "tdnet", "Q3", "2025-12-31", "2026-02-10 15:00:00", code=code)
    s.facts(base + 9, "2025-12-31", [
        x("CashAndDeposits", "i", 60), x("CurrentAssets", "i", 60), x("Goodwill", "i", 6), x("Assets", "i", 66),
        x("LiabilitiesAndNetAssets", "i", 66),
    ])
    s.filing(base + 10, "tdnet_pdf", "FY", "2026-03-31", "2026-03-20 15:00:00", code=code,
             doc_type="forecast_revision")
    s.facts(base + 10, "2026-03-31", [p("bs", "現金及び預金", 999)])


def _quarter_pl(sales: float, pretax: float) -> list[tuple]:
    return [x("NetSales", "d", sales), x("IncomeBeforeIncomeTaxes", "d", pretax), x("ProfitLoss", "d", pretax - 10),
            ("jppfs_cor", "BasicEarningsLossPerShare", "d", pretax / 10, "JPYPerShares")]


def _quarter_cf(depreciation: float, operating: float, cash: float) -> list[tuple]:
    return [x("DepreciationAndAmortizationOpeCF", "d", depreciation),
            x("NetCashProvidedByUsedInOperatingActivities", "d", operating), x("CashAndCashEquivalents", "i", cash)]


def _quarterly(s: Seeder) -> None:
    s.stock(QUARTER, "四半期報告書")
    s.filing(401, "edinet", "Q2", "2024-09-30", "2024-11-12 15:00:00", code=QUARTER)
    s.facts(401, "2024-09-30", [x("CashAndDeposits", "i", 40), x("Assets", "i", 40),
                                x("LiabilitiesAndNetAssets", "i", 40)])
    s.facts(401, "2024-09-30", _quarter_pl(130, 70), start="2024-07-01")
    s.facts(401, "2024-09-30", _quarter_pl(250, 130))
    s.facts(401, "2024-09-30", _quarter_cf(12, 140, 45))
    s.filing(402, "tdnet_pdf", "Q2", "2024-09-30", "2024-11-08 15:00:00", code=QUARTER)
    s.facts(402, "2024-09-30", [
        p("bs", "現金及び預金", 40), p("bs", "資産合計", 40), p("bs", "負債純資産合計", 40),
        p("pl", "売上高", 250), p("pl", "税金等調整前中間純利益", 130), p("pl", "中間純利益", 120),
        p("cf", "税金等調整前中間純利益", 130), p("cf", "減価償却費", 12), p("cf", "営業活動によるキャッシュ・フロー", 140),
        p("cf", "現金及び現金同等物の中間期末残高", 45),
    ])
    s.filing(403, "edinet", "Q3", "2024-12-31", "2025-02-12 15:00:00", code=QUARTER)
    s.facts(403, "2024-12-31", [x("CashAndDeposits", "i", 50), x("Assets", "i", 50),
                                x("LiabilitiesAndNetAssets", "i", 50)])
    s.facts(403, "2024-12-31", _quarter_pl(390, 200))
    s.facts(403, "2024-12-31", _quarter_pl(140, 70), start="2024-10-01")
    s.facts(403, "2024-12-31", _quarter_cf(18, 210, 55))
    s.filing(404, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=QUARTER)
    s.facts(404, "2025-03-31", [x("CashAndDeposits", "i", 60), x("Assets", "i", 60),
                                x("LiabilitiesAndNetAssets", "i", 60)])
    s.facts(404, "2025-03-31", _quarter_pl(520, 260))
    s.facts(404, "2025-03-31", _quarter_cf(24, 280, 65))


def _twice(s: Seeder) -> None:
    s.stock(TWICE, "2 回印字")
    s.filing(501, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=TWICE)
    s.facts(501, "2025-03-31", [
        x("CashAndDeposits", "i", 90), x("Assets", "i", 90), x("LiabilitiesAndNetAssets", "i", 90),
        x("NetSales", "d", 500), x("ProfitLoss", "d", 150), x("ProfitLossAttributableToOwnersOfParent", "d", 140),
        x("ProfitLossAttributableToNonControllingInterests", "d", 10), x("ComprehensiveIncome", "d", 170),
        x("ComprehensiveIncomeAttributableToOwnersOfTheParent", "d", 160),
        x("ComprehensiveIncomeAttributableToNonControllingInterests", "d", 10),
    ])
    pl = ["売上高", "当期利益", "親会社の所有者", "非支配持分", "当期利益#2", "当期包括利益", "親会社の所有者#2",
          "非支配持分#2"]
    s.filing(502, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00", code=TWICE)
    s.facts(502, "2025-03-31", [p("pl", n, v) for n, v in zip(pl, [500, 150, 140, 10, 150, 170, 160, 10])])
    s.filing(503, "tdnet_pdf", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=TWICE)
    s.facts(503, "2025-06-30", [p("pl", n, v) for n, v in zip(pl, [120, 30, 28, 2, 30, 35, 33, 2])])
    s.filing(504, "tdnet_pdf", "Q2", "2025-09-30", "2025-11-08 15:00:00", code=TWICE)
    s.facts(504, "2025-09-30", [p("pl", n, v) for n, v in zip(pl, [250, 60, 56, 4, 25, 70, 66, 4])])


def _opening(s: Seeder) -> None:
    s.stock(OPENING, "期首残高")
    s.filing(601, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=OPENING)
    s.facts(601, "2025-03-31", FY_CF)
    s.filing(602, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00", code=OPENING)
    s.facts(602, "2025-03-31", FY_PDF_CF)
    s.filing(603, "tdnet_pdf", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=OPENING)
    s.facts(603, "2025-06-30", [
        p("cf", "減価償却費", 6), p("cf", "営業活動によるキャッシュ・フロー", 40),
        p("cf", "現金及び現金同等物の増減額", 3), p("cf", "VI現金及び現金同等物の期首残高", 95),
        p("cf", "会計方針の変更を反映した現金及び現金同等物の期首残高", 94),
        p("cf", "新規連結子会社の現金及び現金同等物の期首残高", 2), p("cf", "現金及び現金同等物の期末残高", 99),
    ], start="2025-04-01")


def _amended(s: Seeder) -> None:
    s.stock(AMENDED, "一部訂正")
    s.filing(751, "tdnet_pdf", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=AMENDED)
    s.facts(751, "2025-06-30", [
        p("bs", "現金及び預金", 10), p("bs", "売掛金", 5), p("bs", "その他", 1), p("bs", "建物", 3), p("bs", "土地", 2),
        p("bs", "資産合計", 21), p("pl", "売上高", 100), p("pl", "当期純利益", 10),
    ])
    s.filing(752, "tdnet_pdf", "Q1", "2025-06-30", "2025-09-10 15:00:00", code=AMENDED)
    s.facts(752, "2025-06-30", [p("bs", "売掛金", 6), p("bs", "その他", 9), p("bs", "資産合計", 22)])
    s.filing(753, "tdnet_pdf", "Q1", "2025-06-30", "2025-10-01 15:00:00", code=AMENDED)
    s.facts(753, "2025-06-30", [p("bs", "長期未払金", 99), p("bs", "資産合計", 23)])


DEFINED = "71110"
PAIRS = "71120"
NOCONSOL = "71130"
DEF_BS = ["AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "OtherCA", "CurrentAssets", "Assets",
          "LiabilitiesAbstract", "ShortTermLoansPayable", "Liabilities", "NetAssetsAbstract", "NetAssets",
          "LiabilitiesAndNetAssets"]
DEF_PL = ["NetSales", "CostOfSales", "OperatingIncome", "InterestExpensesNOE", "IncomeBeforeIncomeTaxes", "ProfitLoss"]
DEF_CI = ["ProfitLoss", "ComprehensiveIncome"]
DEF_CF = ["NetCashProvidedByUsedInOperatingActivitiesAbstract", "IncomeBeforeIncomeTaxes",
          "DepreciationAndAmortizationOpeCF", "NetCashProvidedByUsedInOperatingActivities",
          ("CashAndCashEquivalents", "periodStartLabel"), "NetIncreaseDecreaseInCashAndCashEquivalents",
          ("CashAndCashEquivalents", "periodEndLabel")]


def _presentation(s: Seeder) -> None:
    s.stock(DEFINED, "表示リンクあり")
    s.filing(701, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=DEFINED)
    s.facts(701, "2025-03-31", FY_BS + FY_PL + [x("ComprehensiveIncome", "d", 170)] + FY_SS + FY_CF + FY_NOTES)
    s.facts(701, "2025-03-31", [x("CashAndDeposits", "i", 50), x("Assets", "i", 50)],
            consolidated=False, dims=NONCONSOL)
    s.roles(701, [
        ("rol_ConsolidatedBalanceSheet", "BS", True, False, DEF_BS),
        ("rol_ConsolidatedStatementOfComprehensiveIncome", "PL", True, False, DEF_CI),
        ("rol_ConsolidatedStatementOfIncome", "PL", True, False, DEF_PL),
        ("rol_ConsolidatedStatementOfCashFlows-indirect", "CF", True, False, DEF_CF),
        ("rol_BalanceSheet", "BS", False, False, ["CashAndDeposits", "Assets"]),
    ])
    s.filing(702, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00", code=DEFINED)
    s.facts(702, "2025-03-31", FY_PDF_CF)
    s.filing(703, "tdnet", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=DEFINED)
    s.facts(703, "2025-06-30", [x("CashAndDeposits", "i", 70), x("Assets", "i", 70), x("NetSales", "d", 120),
                                x("ProfitLoss", "d", 30)] + _quarter_cf(6, 40, 99))
    s.roles(703, [("rol_QuarterlyConsolidatedBalanceSheetTDnet", "BS", True, False,
                   ["AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "Assets"]),
                  ("rol_YearToQuarterEndConsolidatedStatementOfIncomeTDnet", "PL", True, False,
                   ["NetSales", "ProfitLoss"])])
    s.stock(PAIRS, "2 組の表")
    two = [x("CashAndDeposits", "i", 10), x("Assets", "i", 10), x("NetSales", "d", 5),
           x("CashAndCashEquivalentsIFRS", "i", 11, "jpigp_cor"), x("AssetsIFRS", "i", 11, "jpigp_cor"),
           x("RevenueIFRS", "d", 6, "jpigp_cor"), x("ProfitLossBeforeTaxIFRS", "d", 3, "jpigp_cor")]
    roles = [
        ("rol_QuarterlyBalanceSheetTDnet", "BS", False, False, ["CashAndDeposits", "Assets"]),
        ("rol_YearToQuarterEndStatementOfIncomeTDnet", "PL", False, False, ["NetSales"]),
        ("rol_CondensedQuarterlyStatementOfFinancialPositionTDnetIFRS", "BS", False, True,
         ["CashAndCashEquivalentsIFRS", "AssetsIFRS"]),
        ("rol_CondensedYearToQuarterEndStatementOfProfitOrLossTDnetIFRS", "PL", False, True,
         ["RevenueIFRS", "ProfitLossBeforeTaxIFRS"]),
    ]
    for fid, end, std in ((711, "2025-07-31", "IFRS"), (712, "2025-10-31", "JPGAAP")):
        s.filing(fid, "tdnet", "Q1", end, "2025-09-01 15:00:00", code=PAIRS, standard=std)
        s.facts(fid, end, two, consolidated=False)
        s.roles(fid, roles)
    s.stock(NOCONSOL, "連結の区分が無い")
    s.filing(721, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=NOCONSOL, standard="IFRS")
    s.facts(721, "2025-03-31", [x("AssetsIFRS", "i", 900, "jpigp_cor"), x("RevenueIFRS", "d", 100, "jpigp_cor"),
                                x("ProfitLossIFRS", "d", 9, "jpigp_cor")])
    s.facts(721, "2025-03-31", [x("CashAndDeposits", "i", 30), x("Assets", "i", 30)],
            consolidated=False, dims=NONCONSOL)
    s.roles(721, [("rol_BalanceSheet", "BS", False, False, ["CashAndDeposits", "Assets"])])
    s.filing(722, "edinet", "FY", "2024-03-31", "2024-06-20 15:00:00", code=NOCONSOL, standard="IFRS")
    s.facts(722, "2024-03-31", [x("AssetsIFRS", "i", 800, "jpigp_cor"), x("LiabilitiesAndEquityIFRS", "i", 800, "jpigp_cor"),
                                x("RevenueIFRS", "d", 90, "jpigp_cor"), x("ProfitLossIFRS", "d", 8, "jpigp_cor")])
    s.roles(722, [], status="none")


def seed(more=None) -> None:
    s = Seeder()
    _company(s, CODE, 0)
    _company(s, LOOSE, 100, with_q_pair=False)
    s.stock(SINGLE, "個別だけ")
    s.filing(201, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=SINGLE)
    s.facts(201, "2025-03-31", FY_BS[:10] + [x("LiabilitiesAndNetAssets", "i", 154)])
    s.text_fact(201, "jpdei_cor", fst.DEI_CONSOLIDATED, "false")
    s.stock(NAMED, "名前で当てる")
    s.filing(301, "tdnet_pdf", "Q1", "2026-06-30", "2026-07-29 15:00:00", code=NAMED)
    s.facts(301, "2026-06-30", [
        p("bs", "現金及び預金", 10), p("bs", "流動資産合計", 10), p("bs", "資産合計", 10), p("bs", "借入金", 3),
        p("bs", "社債", 4), p("bs", "負債合計", 7), p("bs", "純資産合計", 3), p("bs", "負債純資産合計", 10),
    ])
    s.filing(302, "tdnet", "Q2", "2026-09-30", "2026-10-28 15:00:00", code=NAMED)
    s.facts(302, "2026-09-30", [
        x("CashAndDeposits", "i", 12), x("CurrentAssets", "i", 12), x("Assets", "i", 12),
        x("BorrowingsCLIFRS", "i", 1, "jpigp_cor"), x("CurrentLiabilities", "i", 1), x("BondsPayable", "i", 5),
        x("BorrowingsNCLIFRS", "i", 2, "jpigp_cor"), x("NoncurrentLiabilities", "i", 7), x("Liabilities", "i", 8),
        x("NetAssets", "i", 4), x("LiabilitiesAndNetAssets", "i", 12),
    ])
    s.roles(302, [("rol_QuarterlyConsolidatedBalanceSheetTDnet", "BS", True, False, [
        "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "CurrentAssets", "Assets",
        "LiabilitiesAbstract", "CurrentLiabilitiesAbstract", "BorrowingsCLIFRS", "CurrentLiabilities",
        "NoncurrentLiabilitiesAbstract", "BondsPayable", "BorrowingsNCLIFRS", "NoncurrentLiabilities", "Liabilities",
        "NetAssetsAbstract", "NetAssets", "LiabilitiesAndNetAssets",
    ])])
    _quarterly(s)
    _twice(s)
    _opening(s)
    _amended(s)
    if more:
        more(s)
    s.close()


DECLARED = "71140"
DECLARED_TREE = """
AssetsAbstract=資産の部
  CurrentAssetsAbstract=流動資産
    CashAndDeposits
    CurrentAssets
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
      Buildings
        AccumulatedDepreciationBuildings
      Land
      PropertyPlantAndEquipment
  Assets
"""


def _declared(s: Seeder) -> None:
    s.stock(DECLARED, "見出しの宣言あり")
    s.filing(731, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=DECLARED)
    s.facts(731, "2025-03-31", [
        x("CashAndDeposits", "i", 10), x("OtherCA", "i", 2), x("CurrentAssets", "i", 12), x("Land", "i", 5),
        x("PropertyPlantAndEquipment", "i", 5), x("LongTermPrepaidExpenses", "i", 1), x("Assets", "i", 18),
    ])
    s.roles(731, [("rol_ConsolidatedBalanceSheet", "BS", True, False, [
        "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "OtherCA", "CurrentAssets",
        "NoncurrentAssetsAbstract", "PropertyPlantAndEquipmentAbstract", "Land", "PropertyPlantAndEquipment",
        "LongTermPrepaidExpenses", "Assets",
    ])])
    s.filing(732, "tdnet", "Q1", "2025-06-30", "2025-08-08 15:00:00", code=DECLARED)
    s.facts(732, "2025-06-30", [
        x("CashAndDeposits", "i", 11), x("CurrentAssets", "i", 11), x("Buildings", "i", 4),
        x("AccumulatedDepreciationBuildings", "i", -1), x("Land", "i", 5), x("PropertyPlantAndEquipment", "i", 8),
        x("Assets", "i", 19),
    ])
    s.roles(732, [("rol_QuarterlyConsolidatedBalanceSheetTDnet", "BS", True, False, _tree_items(DECLARED_TREE))])


PARENT = "71150"
PARENT_FY_TREE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    InvestmentsAndOtherAssetsAbstract=投資その他の資産
      StocksOfSubsidiariesAndAffiliates
      InvestmentsAndOtherAssets
    NoncurrentAssets
  Assets
"""
PARENT_Q_TREE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    InvestmentsAndOtherAssets
      StocksOfSubsidiariesAndAffiliates
      OtherIOA
    NoncurrentAssets
  Assets
"""
SHIFTED = "71160"


def _moved(s: Seeder) -> None:
    s.stock(PARENT, "投資その他の資産の金額の下に内訳")
    s.filing(741, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=PARENT)
    s.facts(741, "2025-03-31", [
        x("StocksOfSubsidiariesAndAffiliates", "i", 9), x("InvestmentsAndOtherAssets", "i", 9),
        x("NoncurrentAssets", "i", 9), x("Assets", "i", 9),
    ])
    s.roles(741, [("rol_BalanceSheet", "BS", True, False, _tree_items(PARENT_FY_TREE))])
    s.filing(742, "edinet", "Q2", "2025-09-30", "2025-11-28 15:00:00", code=PARENT)
    s.facts(742, "2025-09-30", [
        x("InvestmentsAndOtherAssets", "i", 10), x("StocksOfSubsidiariesAndAffiliates", "i", 9), x("OtherIOA", "i", 1),
        x("NoncurrentAssets", "i", 10), x("Assets", "i", 10),
    ])
    s.roles(742, [("rol_QuarterlyBalanceSheet", "BS", True, False, _tree_items(PARENT_Q_TREE))])
    s.filing(743, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", code=PARENT)
    s.facts(743, "2026-03-31", [
        x("StocksOfSubsidiariesAndAffiliates", "i", 8), x("InvestmentsAndOtherAssets", "i", 8),
        x("NoncurrentAssets", "i", 8), x("Assets", "i", 8),
    ])
    s.roles(743, [("rol_BalanceSheet", "BS", True, False, _tree_items(PARENT_FY_TREE))])
    s.stock(SHIFTED, "流動資産の貸倒引当金が消える")
    tail = [p("bs", "負債合計", 7), p("bs", "純資産合計", 3)]
    s.filing(771, "tdnet_pdf", "FY", "2024-03-31", "2024-05-10 15:00:00", code=SHIFTED)
    s.facts(771, "2024-03-31", [
        p("bs", "現金及び預金", 100), p("bs", "貸倒引当金", -1), p("bs", "流動資産合計", 99),
        p("bs", "投資その他の資産投資有価証券", 50), p("bs", "貸倒引当金#2", -2), p("bs", "投資その他の資産合計", 48),
        p("bs", "固定資産合計", 48), p("bs", "資産合計", 147), *tail, p("bs", "負債純資産合計", 147),
    ])
    s.filing(772, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00", code=SHIFTED)
    s.facts(772, "2025-03-31", [
        p("bs", "現金及び預金", 120), p("bs", "流動資産合計", 120),
        p("bs", "投資その他の資産投資有価証券", 60), p("bs", "貸倒引当金", -3), p("bs", "投資その他の資産合計", 57),
        p("bs", "固定資産合計", 57), p("bs", "資産合計", 177), *tail, p("bs", "負債純資産合計", 177),
    ])


SPLIT_HEAD = "49410"
AOCI_HEAD = "49430"
TAIL_HEAD = "49440"
BREAKDOWN = "49420"
KANA_HEAD = "49450"
OLD_EXPENSES = """
ext:ExpenseOldAbstract=費用
  ImpairmentLossesPL
  ext:ShareOnly
  ext:TotalExpenses
"""
NEW_EXPENSES = """
ext:ExpenseNewAbstract=費用
  ImpairmentLossesPL
  FinanceCosts
  ext:TotalExpenses
"""
OLD_AOCI = """
NetAssetsAbstract=純資産の部
  ext:AociOldAbstract=その他の包括利益累計額
    ValuationDifferenceOnAvailableForSaleSecurities
    ext:AociOldTotal
"""
NEW_AOCI = """
NetAssetsAbstract=純資産の部
  ValuationAndTranslationAdjustmentsAbstract=その他の包括利益累計額
    ValuationDifferenceOnAvailableForSaleSecurities
    ValuationAndTranslationAdjustments
"""
OLD_ADJ = """
ext:AdjOldAbstract=利益に対する調整項目
  DepreciationAndAmortizationOpeCF
  ext:AdjNew
  ext:SaleOfShares
"""
NEW_ADJ = """
ext:AdjNewAbstract=利益に対する調整項目
  DepreciationAndAmortizationOpeCF
  ext:OtherAdj
  ext:AdjNew
"""
BOTH_BREAKDOWNS = """
ProfitLoss
ext:ProfitBreakAbstract=（内訳）
  ProfitLossAttributableToOwnersOfParent
  ProfitLossAttributableToNonControllingInterests
ComprehensiveIncome
ext:CiBreakAbstract=（内訳）
  ComprehensiveIncomeAttributableToOwnersOfTheParent
  ComprehensiveIncomeAttributableToNonControllingInterests
"""
OLD_KANA_HEAD = """
LiabilitiesAndEquityIFRSAbstract=負債および資本
  LiabilitiesIFRSAbstract=負債
    CurrentLiabilitiesIFRSAbstract=流動負債
      BorrowingsCLIFRS
      TotalCurrentLiabilitiesIFRS
    NonCurrentLiabilitiesIFRSAbstract=非流動負債
      BorrowingsNCLIFRS
      NonCurrentLabilitiesIFRS
  LiabilitiesAndEquityIFRS
"""
NEW_KANA_HEAD = """
LiabilitiesAndEquityIFRSAbstract=負債及び資本
  LiabilitiesIFRSAbstract=負債
    CurrentLiabilitiesIFRSAbstract=流動負債
      TradeAndOtherPayablesCLIFRS
      TotalCurrentLiabilitiesIFRS
    NonCurrentLiabilitiesIFRSAbstract=非流動負債
      BondsPayableNCLIFRS
      NonCurrentLabilitiesIFRS
  LiabilitiesAndEquityIFRS
"""


def _same_name_headings(s: Seeder) -> None:
    s.stock(SPLIT_HEAD, "費用の要素が期で分かれる")
    s.filing(901, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=SPLIT_HEAD)
    s.facts(901, "2025-03-31", [
        x("ImpairmentLossesPL", "d", 10), x("ShareOnly", "d", 3, "x"), x("TotalExpenses", "d", 13, "x"),
    ], start="2024-04-01")
    s.roles(901, [("rol_StatementOfIncome", "PL", True, False, _tree_items(OLD_EXPENSES))])
    s.filing(902, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", code=SPLIT_HEAD)
    s.facts(902, "2026-03-31", [
        x("ImpairmentLossesPL", "d", 11), x("FinanceCosts", "d", 4), x("TotalExpenses", "d", 15, "x"),
    ], start="2025-04-01")
    s.roles(902, [("rol_StatementOfIncome", "PL", True, False, _tree_items(NEW_EXPENSES))])
    s.stock(AOCI_HEAD, "包括利益累計額の要素が1期だけ違う")
    s.filing(921, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=AOCI_HEAD)
    s.facts(921, "2025-03-31", [
        x("ValuationDifferenceOnAvailableForSaleSecurities", "i", 5), x("AociOldTotal", "i", 5, "x"),
    ])
    s.roles(921, [("rol_BalanceSheet", "BS", True, False, _tree_items(OLD_AOCI))])
    s.filing(922, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", code=AOCI_HEAD)
    s.facts(922, "2026-03-31", [
        x("ValuationDifferenceOnAvailableForSaleSecurities", "i", 7), x("ValuationAndTranslationAdjustments", "i", 7),
    ])
    s.roles(922, [("rol_BalanceSheet", "BS", True, False, _tree_items(NEW_AOCI))])
    s.stock(TAIL_HEAD, "調整項目の要素が期で分かれる")
    s.filing(931, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=TAIL_HEAD)
    s.facts(931, "2025-03-31", [
        x("DepreciationAndAmortizationOpeCF", "d", 8), x("AdjNew", "d", 8, "x"), x("SaleOfShares", "d", 2, "x"),
    ], start="2024-04-01")
    s.roles(931, [("rol_CashFlows", "CF", True, False, _tree_items(OLD_ADJ))])
    s.filing(932, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", code=TAIL_HEAD)
    s.facts(932, "2026-03-31", [
        x("DepreciationAndAmortizationOpeCF", "d", 9), x("OtherAdj", "d", 1, "x"), x("AdjNew", "d", 10, "x"),
    ], start="2025-04-01")
    s.roles(932, [("rol_CashFlows", "CF", True, False, _tree_items(NEW_ADJ))])
    s.stock(BREAKDOWN, "内訳が2つ")
    s.filing(911, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", code=BREAKDOWN)
    s.facts(911, "2026-03-31", [
        x("ProfitLoss", "d", 100), x("ProfitLossAttributableToOwnersOfParent", "d", 80),
        x("ProfitLossAttributableToNonControllingInterests", "d", 20), x("ComprehensiveIncome", "d", 110),
        x("ComprehensiveIncomeAttributableToOwnersOfTheParent", "d", 90),
        x("ComprehensiveIncomeAttributableToNonControllingInterests", "d", 20),
    ], start="2025-04-01")
    s.roles(911, [("rol_StatementOfIncome", "PL", True, False, _tree_items(BOTH_BREAKDOWNS))])
    s.stock(KANA_HEAD, "負債および資本と負債及び資本")
    s.filing(941, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", standard="IFRS", code=KANA_HEAD)
    s.facts(941, "2025-03-31", [
        x("BorrowingsCLIFRS", "i", 3, "jpigp_cor"), x("TotalCurrentLiabilitiesIFRS", "i", 3, "jpigp_cor"),
        x("BorrowingsNCLIFRS", "i", 4, "jpigp_cor"), x("NonCurrentLabilitiesIFRS", "i", 4, "jpigp_cor"),
        x("LiabilitiesAndEquityIFRS", "i", 7, "jpigp_cor"),
    ])
    s.roles(941, [("rol_StatementOfFinancialPositionIFRS", "BS", True, True, _tree_items(OLD_KANA_HEAD, ifrs=True))])
    s.filing(942, "edinet", "FY", "2026-03-31", "2026-06-20 15:00:00", standard="IFRS", code=KANA_HEAD)
    s.facts(942, "2026-03-31", [
        x("TradeAndOtherPayablesCLIFRS", "i", 5, "jpigp_cor"), x("TotalCurrentLiabilitiesIFRS", "i", 5, "jpigp_cor"),
        x("BondsPayableNCLIFRS", "i", 6, "jpigp_cor"), x("NonCurrentLabilitiesIFRS", "i", 6, "jpigp_cor"),
        x("LiabilitiesAndEquityIFRS", "i", 11, "jpigp_cor"),
    ])
    s.roles(942, [("rol_StatementOfFinancialPositionIFRS", "BS", True, True, _tree_items(NEW_KANA_HEAD, ifrs=True))])


PRINTED = "71170"
PRINTED_SPLIT = [
    "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "NotesReceivableTrade", "AccountsReceivableTrade",
    "CurrentAssets", "NoncurrentAssetsAbstract", "Goodwill", "NoncurrentAssets", "Assets",
]
PRINTED_JOINED = [
    "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "NotesAndAccountsReceivableTrade",
    "CurrentAssets", "NoncurrentAssetsAbstract", "Goodwill", "NoncurrentAssets", "Assets",
]

PRINTED_CF_EDINET = [
    "NetCashProvidedByUsedInOperatingActivitiesAbstract", "IncomeBeforeIncomeTaxes",
    "ChangesInAssetsAndLiabilitiesOtherNetOpeCF", "NetCashProvidedByUsedInOperatingActivities",
]
PRINTED_CF_TDNET = [
    "NetCashProvidedByUsedInOperatingActivitiesAbstract", "IncomeBeforeIncomeTaxes",
    "DecreaseIncreaseInTradeReceivablesOpeCF", "NetCashProvidedByUsedInOperatingActivities",
]


def _printed(s: Seeder) -> None:
    s.stock(PRINTED, "有報は受取手形と売掛金を 1 行、短信は 2 行")
    s.filing(781, "edinet", "FY", "2025-06-30", "2025-09-26 15:00:00", code=PRINTED)
    s.facts(781, "2025-06-30", [
        x("CashAndDeposits", "i", 20), x("NotesReceivableTrade", "i", 1), x("AccountsReceivableTrade", "i", 9),
        x("CurrentAssets", "i", 30), x("Goodwill", "i", 4), x("NoncurrentAssets", "i", 4), x("Assets", "i", 34),
    ])
    s.roles(781, [("rol_ConsolidatedBalanceSheet", "BS", True, False, PRINTED_SPLIT)])
    s.filing(782, "edinet", "FY", "2026-06-30", "2026-09-25 15:00:00", code=PRINTED)
    s.facts(782, "2026-06-30", [
        x("CashAndDeposits", "i", 21), x("NotesAndAccountsReceivableTrade", "i", 8), x("CurrentAssets", "i", 29),
        x("NoncurrentAssets", "i", 3), x("Assets", "i", 32),
    ])
    s.roles(782, [("rol_ConsolidatedBalanceSheet", "BS", True, False, PRINTED_JOINED)])
    s.filing(783, "tdnet", "FY", "2026-06-30", "2026-08-08 15:00:00", code=PRINTED)
    s.facts(783, "2026-06-30", [
        x("CashAndDeposits", "i", 21), x("NotesReceivableTrade", "i", 1), x("AccountsReceivableTrade", "i", 7),
        x("CurrentAssets", "i", 29), x("Goodwill", "i", 3), x("NoncurrentAssets", "i", 3), x("Assets", "i", 32),
    ])
    s.roles(783, [("rol_ConsolidatedBalanceSheetTDnet", "BS", True, False, PRINTED_SPLIT)])
    s.facts(782, "2026-06-30", [
        x("IncomeBeforeIncomeTaxes", "d", 5), x("ChangesInAssetsAndLiabilitiesOtherNetOpeCF", "d", -2),
        x("NetCashProvidedByUsedInOperatingActivities", "d", 3),
    ])
    s.roles(782, [("rol_ConsolidatedStatementOfCashFlows-indirect", "CF", True, False, PRINTED_CF_EDINET)])
    s.facts(783, "2026-06-30", [
        x("IncomeBeforeIncomeTaxes", "d", 5), x("DecreaseIncreaseInTradeReceivablesOpeCF", "d", -2),
        x("NetCashProvidedByUsedInOperatingActivities", "d", 3),
    ])
    s.roles(783, [("rol_ConsolidatedStatementOfCashFlowsTDnet", "CF", True, False, PRINTED_CF_TDNET)])


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed(lambda s: (_presentation(s), _split(s), _declared(s), _moved(s), _same_name_headings(s), _printed(s)))


def table(code: str = CODE, statement: str = "BS", periods: int = fst.DEFAULT_PERIODS,
          consolidated: bool | None = True) -> dict:
    db = SessionLocal()
    try:
        return fst.financial_table(db, code, statement=statement, periods=periods, consolidated=consolidated)
    finally:
        db.close()


def rows(t: dict) -> dict[str, dict]:
    return {r["key"]: r for r in t["rows"]}


def labels(t: dict) -> list[str]:
    return [c["label"] for c in t["periods"]]


def value(t: dict, key: str, label: str) -> float | None:
    cell = rows(t)[key]["cells"][labels(t).index(label)]
    return None if cell is None else cell["value"]


def source(t: dict, key: str, label: str) -> str | None:
    cell = rows(t)[key]["cells"][labels(t).index(label)]
    return None if cell is None else cell["source"]


def filings(code: str = CODE, consolidated: bool = True) -> dict[int, fst.Filing]:
    db = SessionLocal()
    try:
        return {f.id: f for f in fst.load_filings(db, code, consolidated)}
    finally:
        db.close()


def test_split_xbrl_drops_equity_changes_and_notes() -> None:
    filing = filings()[1]
    keys = {s: [f.key for f in facts] for s, facts in filing.by_statement.items()}
    assert keys["BS"][0] == "CashAndDeposits" and keys["BS"][-1] == "LiabilitiesAndNetAssets"
    assert keys["PL"] == ["NetSales", "CostOfSales", "OperatingIncome", "InterestExpensesNOE",
                          "IncomeBeforeIncomeTaxes", "ProfitLoss"]
    assert keys["CF"] == ["DepreciationAndAmortizationOpeCF", "NetCashProvidedByUsedInOperatingActivities",
                          "NetIncreaseDecreaseInCashAndCashEquivalents", "CashAndCashEquivalents"]
    values = {f.key: f.value for f in filing.seq}
    assert values["CashAndDeposits"] == 100 * M
    assert "NumberOfEmployees" not in values


def split(*items: str) -> dict[str, list[str]]:
    seq = [
        fst.Fact(id=n, key=item[2:], local=item[2:], label="", instant=item[0] == "i", unit="JPY", value=n)
        for n, item in enumerate(items, 1)
    ]
    return {s: [f.local for f in facts] for s, facts in fst.split_xbrl(seq).items()}


JP_BS = ("i CashAndDeposits", "i CurrentAssets", "i Assets", "i Liabilities", "i NetAssets", "i LiabilitiesAndNetAssets")
JP_PL = ("d NetSales", "d OperatingIncome", "d OrdinaryIncome", "d ProfitLoss",
         "d ProfitLossAttributableToOwnersOfParent", "d OtherComprehensiveIncome", "d ComprehensiveIncome")
JP_SS = ("d DividendsFromSurplus", "d PurchaseOfTreasuryStock", "d TotalChangesOfItemsDuringThePeriod")
JP_CF = ("d DepreciationAndAmortizationOpeCF", "d NetCashProvidedByUsedInOperatingActivities",
         "d NetIncreaseDecreaseInCashAndCashEquivalents", "i CashAndCashEquivalents")
IFRS_BS = ("i CashAndCashEquivalentsIFRS", "i AssetsIFRS", "i LiabilitiesIFRS", "i EquityIFRS",
           "i LiabilitiesAndEquityIFRS")
IFRS_PL = ("d RevenueIFRS", "d OperatingProfitLossIFRS", "d ProfitLossBeforeTaxIFRS", "d ProfitLossIFRS",
           "d BasicEarningsLossPerShareIFRS", "d OtherComprehensiveIncomeIFRS", "d ComprehensiveIncomeIFRS",
           "d ComprehensiveIncomeAttributableToNonControllingInterestsIFRS")
IFRS_SS = ("d DividendsSSIFRS", "d PurchaseOfTreasurySharesSSIFRS", "d TotalTransactionsWithOwnersIFRS")
IFRS_CF = ("d DepreciationAndAmortizationOpeCFIFRS", "d NetCashProvidedByUsedInOperatingActivitiesIFRS")
HEAD = ("d RevenueShippingSummaryOfBusinessResults", "d ProfitLossSummaryOfBusinessResults",
        "i TotalEquityIFRSSummaryOfBusinessResults", "d GrossInvestmentReturnKeyFinancialData",
        "d TotalAcquisitionCostForIncreasedSharesEquitySecuritiesNotListedInvestmentEquitySecuritiesReportingCompany")
NOTES = ("d DepreciationSGA", "d ProvisionForBonusesSGA")


def names(items: tuple[str, ...]) -> list[str]:
    return [item[2:] for item in items]


def test_split_xbrl_pl_after_bs_is_unchanged() -> None:
    got = split(*HEAD, *JP_BS, *JP_PL, *JP_SS, *JP_CF, *NOTES)
    assert got == {"BS": names(JP_BS), "PL": names(JP_PL), "CF": names(JP_CF)}
    got = split(*HEAD, *IFRS_BS, *IFRS_PL, *IFRS_SS, *IFRS_CF)
    assert got == {"BS": names(IFRS_BS), "PL": names(IFRS_PL), "CF": names(IFRS_CF)}


def test_split_xbrl_takes_pl_printed_before_bs() -> None:
    got = split(*HEAD, *JP_PL, *JP_SS, *JP_BS, *JP_CF, *NOTES)
    assert got == {"BS": names(JP_BS), "PL": names(JP_PL), "CF": names(JP_CF)}
    got = split(*HEAD, *JP_PL, *JP_BS, *JP_SS, *JP_CF)
    assert got == {"BS": names(JP_BS), "PL": names(JP_PL), "CF": names(JP_CF)}
    got = split(*HEAD, *IFRS_PL, *IFRS_BS, *IFRS_SS, *IFRS_CF, "d ProfitLossBeforeTaxFromDiscontinuedOperationsIFRS")
    assert got == {"BS": names(IFRS_BS), "PL": names(IFRS_PL), "CF": names(IFRS_CF)}
    assert split(*IFRS_PL, *IFRS_BS, *IFRS_SS)["PL"] == names(IFRS_PL)


JP_CF_SCOPE = ("d DepreciationAndAmortizationOpeCF", "d NetCashProvidedByUsedInOperatingActivities",
               "d NetIncreaseDecreaseInCashAndCashEquivalents",
               "d IncreaseInCashAndCashEquivalentsFromNewlyConsolidatedSubsidiaryCCE",
               "d DecreaseInCashAndCashEquivalentsResultingFromExclusionOfSubsidiariesFromConsolidationCCE",
               "i CashAndCashEquivalents")
CASH_NOTE = ("i CashAndDeposits", "i TimeDepositsOverThreeMonths", "i CashAndCashEquivalents")


def test_split_xbrl_takes_cash_changes_from_scope_of_consolidation_into_cf() -> None:
    got = split(*HEAD, *JP_BS, *JP_PL, *JP_SS, *JP_CF_SCOPE, *NOTES)
    assert got == {"BS": names(JP_BS), "PL": names(JP_PL), "CF": names(JP_CF_SCOPE)}
    got = split(*JP_PL, *JP_BS, *JP_SS, *JP_CF_SCOPE)
    assert got == {"BS": names(JP_BS), "PL": names(JP_PL), "CF": names(JP_CF_SCOPE)}
    cf = (*JP_CF[:3], "d IncreaseInCashAndCashEquivalentsResultingFromMergerCCE")
    assert split(*JP_BS, *JP_PL, *cf, *NOTES)["CF"] == names(cf)
    cf = (*JP_CF[:3], "d IncreaseInCashAndCashEquivalentsFromShareExchange", "i CashAndCashEquivalents")
    assert split(*JP_BS, *JP_PL, *cf, *NOTES)["CF"] == names(cf)


def test_split_xbrl_cf_tail_stops_before_notes() -> None:
    got = split(*JP_BS, *JP_PL, *JP_CF_SCOPE, *CASH_NOTE, *NOTES)
    assert got["CF"] == names(JP_CF_SCOPE)
    got = split(*JP_BS, *JP_PL, *JP_CF, *NOTES, "d IncreaseInCashAndCashEquivalentsResultingFromMergerCCE")
    assert got["CF"] == names(JP_CF)
    got = split(*IFRS_BS, *IFRS_PL, *IFRS_CF, "i CashAndCashEquivalentsIFRS", "d CashAndCashEquivalentsInterestIncomeIFRS")
    assert got["CF"] == [*names(IFRS_CF), "CashAndCashEquivalentsIFRS"]


def test_split_xbrl_keeps_pl_of_the_chosen_bs_when_two_sets_are_printed() -> None:
    got = split(*IFRS_BS, *IFRS_PL, *JP_BS, "d NetSales", "d OperatingIncome", "d ProfitLoss")
    assert got == {"BS": names(JP_BS), "PL": ["NetSales", "OperatingIncome", "ProfitLoss"], "CF": []}


def test_learn_pair_uses_value_and_print_order() -> None:
    by_id = filings()
    learned = fst.learn_pair(by_id[2], by_id[1])
    bs = learned.maps["BS"]
    assert bs["その他"].key == "OtherCA"
    assert bs["その他#2"].key == "OtherPPE"
    assert bs["その他#3"].key == "OtherIA"
    assert bs["その他#4"].key == "OtherCL"
    assert bs["土地"].key == "Land"
    assert "有形固定資産土地" not in bs
    assert bs["有形固定資産合計"].key == "PropertyPlantAndEquipment"
    assert bs["資産合計"].key == "Assets"
    assert bs["負債純資産合計"].key == "LiabilitiesAndNetAssets"
    assert learned.maps["PL"]["支払利息"] == fst.Mapped("InterestExpensesNOE", -1)
    cf = learned.maps["CF"]
    assert cf["税金等調整前当期純利益"].key == "IncomeBeforeIncomeTaxes"
    assert cf["現金及び現金同等物の期末残高"].key == "CashAndCashEquivalents"
    assert "現金及び現金同等物の期首残高" not in cf
    assert learned.extras["CF"] == {"IncomeBeforeIncomeTaxes": None}


def test_lcs_prefers_order_for_repeated_values() -> None:
    pairs = fst._lcs([7, 107, 7, 141, 141], [7, 107, 141, 7, 141], 0.0)
    assert len(pairs) == 4
    assert (0, 0) in pairs and (1, 1) in pairs
    assert fst._lcs([0, 5], [0, 5], 0.0) == [(1, 1)]
    assert fst._lcs([7_156_000_000], [7_156_196_000], 1e6) == [(0, 0)]


def test_pdf_only_period_uses_nearest_same_kind_pair() -> None:
    t = table()
    assert (t["statement"], t["consolidated"], t["basis"]) == ("BS", True, "連結")
    assert t["accounting_standards"] == ["JPGAAP"] and t["cumulative"] is False and t["empty"] is None
    assert labels(t) == ["24.09", "25.03", "25.06", "25.09", "25.12"]
    assert value(t, "OtherIA", "25.06") == 11 * M
    assert value(t, "OtherCL", "25.06") == 6 * M
    assert value(t, "OtherCA", "25.06") == 5 * M
    assert value(t, "pdf:前受金", "25.06") == 4 * M
    accrued = rows(t)["pdf:前受金"]
    assert accrued["label"] == "前受金" and accrued["unmatched_pdf"] is True and accrued["unit"] == "money"
    assert rows(t)["OtherIA"]["unmatched_pdf"] is False
    assert [c["source"] for c in t["periods"]] == ["edinet", "edinet", "tdnet_pdf", "tdnet", "edinet"]
    assert [c["source_label"] for c in t["periods"]] == ["有報", "有報", "PDF", "XBRL", "有報"]
    jun = t["periods"][2]
    assert jun == {"period_end": "2025-06-30", "label": "25.06", "period_type": "Q1", "source": "tdnet_pdf",
                   "source_label": "PDF", "doc_id": "D5", "submitted_at": "2025-08-08 15:00",
                   "accounting_standard": "JPGAAP", "consolidated": True}


def test_pdf_only_period_matches_others_within_sections_across_kinds() -> None:
    t = table(LOOSE)
    assert value(t, "OtherIA", "25.06") == 11 * M
    assert value(t, "OtherCL", "25.06") == 6 * M
    assert "pdf:その他#2" not in rows(t) and "pdf:その他#3" not in rows(t)
    assert value(t, "Goodwill", "25.06") == 3 * M
    assert value(t, "OtherCA", "25.06") == 5 * M


def test_row_order_and_unmatched_rows_keep_print_position() -> None:
    t = table()
    keys = [r["key"] for r in t["rows"]]
    assert keys.index("ShortTermLoansPayable") + 1 == keys.index("pdf:前受金")
    assert keys.index("pdf:前受金") + 1 == keys.index("OtherCL")
    assert keys.index("Land") < keys.index("OtherPPE") < keys.index("PropertyPlantAndEquipment") < keys.index("Goodwill")
    assert rows(t)["Land"]["label"] == "土地"
    assert keys[0] == "CashAndDeposits" and keys[-1] == "LiabilitiesAndNetAssets"


def test_priority_and_supplement_keep_cell_source() -> None:
    t = table()
    assert value(t, "CashAndDeposits", "25.09") == 70 * M
    assert value(t, "Goodwill", "25.12") == 6 * M
    assert source(t, "Goodwill", "25.12") == "tdnet"
    assert value(t, "Assets", "25.12") == 66 * M
    assert source(t, "Assets", "25.12") == "edinet"
    assert t["periods"][-1]["source"] == "edinet"
    assert source(t, "OtherIA", "25.06") == "tdnet_pdf"
    assert value(t, "Land", "25.06") is None


def test_bs_is_not_supplemented_with_rows_the_primary_filing_does_not_print() -> None:
    t = table(PRINTED)
    assert labels(t) == ["25.06", "26.06"]
    assert value(t, "NotesAndAccountsReceivableTrade", "26.06") == 8 * M
    assert value(t, "NotesReceivableTrade", "26.06") is None
    assert value(t, "NotesReceivableTrade", "25.06") == 1 * M
    current = [r for r in t["rows"] if r["key"] in ("CashAndDeposits", "NotesReceivableTrade",
                                                    "AccountsReceivableTrade", "NotesAndAccountsReceivableTrade")]
    assert sum(r["cells"][1]["value"] for r in current if r["cells"][1]) == value(t, "CurrentAssets", "26.06")
    assert value(t, "Goodwill", "26.06") == 3 * M
    assert source(t, "Goodwill", "26.06") == "tdnet"


def test_cf_is_not_supplemented_with_rows_the_primary_filing_does_not_print() -> None:
    t = table(PRINTED, statement="CF")
    assert value(t, "ChangesInAssetsAndLiabilitiesOtherNetOpeCF", "26.06") == -2 * M
    assert "DecreaseIncreaseInTradeReceivablesOpeCF" not in rows(t)
    items = [r["cells"][-1]["value"] for r in t["rows"]
             if r["cells"][-1] and r["key"] != "NetCashProvidedByUsedInOperatingActivities"]
    assert sum(items) == value(t, "NetCashProvidedByUsedInOperatingActivities", "26.06")


def test_sign_follows_xbrl_and_pl_is_cumulative() -> None:
    t = table(statement="PL")
    assert t["cumulative"] is True
    assert value(t, "InterestExpensesNOE", "25.03") == 2 * M
    assert value(t, "InterestExpensesNOE", "25.06") == 4 * M
    assert rows(t)["IncomeBeforeIncomeTaxes"]["label"] == "税金等調整前当期純利益"
    assert "DividendsFromSurplus" not in rows(t) and "EmployeesSalariesSGA" not in rows(t)


def test_cf_keeps_learned_members_in_xbrl_only_periods() -> None:
    t = table(statement="CF")
    assert labels(t) == ["25.03", "25.09"]
    assert t["rows"][0]["key"] == "IncomeBeforeIncomeTaxes"
    assert [c["value"] for c in t["rows"][0]["cells"]] == [198 * M, 90 * M]
    assert rows(t)["CashAndCashEquivalents"]["label"] == "現金及び現金同等物の期末残高"
    assert value(t, "NetIncreaseDecreaseInCashAndCashEquivalents", "25.09") == -10 * M
    assert "pdf:現金及び現金同等物の期首残高" not in rows(t)


def test_cf_drops_unmatched_opening_cash_rows() -> None:
    t = table(OPENING, statement="CF")
    assert labels(t) == ["25.03", "25.06"]
    keys = [r["key"] for r in t["rows"]]
    assert [k for k in keys if k.startswith("pdf:")] == ["pdf:新規連結子会社の現金及び現金同等物の期首残高"]
    assert value(t, "pdf:新規連結子会社の現金及び現金同等物の期首残高", "25.06") == 2 * M
    assert value(t, "CashAndCashEquivalents", "25.06") == 99 * M
    assert keys[-1] == "CashAndCashEquivalents"
    db = SessionLocal()
    try:
        got = [(m.fact.local, m.mapped and m.mapped.key) for m in fst.pdf_only_matches(db, OPENING)]
    finally:
        db.close()
    assert got == [
        ("減価償却費", "DepreciationAndAmortizationOpeCF"),
        ("営業活動によるキャッシュ・フロー", "NetCashProvidedByUsedInOperatingActivities"),
        ("現金及び現金同等物の増減額", "NetIncreaseDecreaseInCashAndCashEquivalents"),
        ("新規連結子会社の現金及び現金同等物の期首残高", None),
        ("現金及び現金同等物の期末残高", "CashAndCashEquivalents"),
    ]


def test_partial_amendments_revise_the_original_pdf() -> None:
    t = table(AMENDED)
    assert labels(t) == ["25.06"]
    assert [r["key"] for r in t["rows"]] == ["pdf:現金及び預金", "pdf:売掛金", "pdf:その他", "pdf:建物", "pdf:土地",
                                             "pdf:資産合計"]
    assert [r["cells"][0]["value"] for r in t["rows"]] == [10 * M, 6 * M, 1 * M, 3 * M, 2 * M, 23 * M]
    assert value(table(AMENDED, statement="PL"), "pdf:売上高", "25.06") == 100 * M


def test_full_reissue_stays_the_base() -> None:
    old = fst.Filing(id=1, doc_id="A", source="tdnet_pdf", period_type="Q1", period_end=date(2025, 6, 30),
                     submitted="2025-08-08", standard="JPGAAP")
    new = fst.Filing(id=2, doc_id="B", source="tdnet_pdf", period_type="Q1", period_end=date(2025, 6, 30),
                     submitted="2025-09-10", standard="JPGAAP")
    old.by_statement = {"BS": [bs_fact(n, 1) for n in "abcdefghij"]}
    new.by_statement = {"BS": [bs_fact(n, 1) for n in "abcdefgh"]}
    assert fst._pdf_base([old, new], "BS") is new
    new.by_statement = {"BS": [bs_fact(n, 1) for n in "abcdef"]}
    assert fst._pdf_base([old, new], "BS") is old and fst._revisions([old, new], old) == [new]


def test_opening_cash_names() -> None:
    names = ["現金及び現金同等物の期首残高", "現金及び現金同等物期首残高", "現金および現金同等物の期首残高",
             "VI現金及び現金同等物の期首残高", "期首現金及び現金同等物残高", "現金及び現金同等物の期首残高#2",
             "現金及び現金同等物の期首残高(連結財政状態計算書計上額)", "会計方針の変更を反映した現金及び現金同等物の期首残高"]
    assert all(fst._opening_cash(f"pdf:{n}") for n in names)
    kept = ["新規連結子会社の現金及び現金同等物の期首残高", "被取得企業の現金同等物期首残高",
            "連結子会社の決算期変更に伴う現金及び現金同等物の期首残高増減額", "現金及び現金同等物の期末残高"]
    assert not any(fst._opening_cash(f"pdf:{n}") for n in kept)
    assert not fst._opening_cash("CashAndCashEquivalents")


def test_layout_keeps_print_order_and_places_borrowed_facts_by_position() -> None:
    items = lambda *names: [["jpigp_cor", n, 0, p] for n, p in names]
    layouts = fst.statement_layouts([
        (0, "rol_ConsolidatedStatementOfFinancialPositionIFRS", "BS", True, True,
         items(("CashAndCashEquivalentsIFRS", None), ("AssetsIFRS", None))),
        (1, "rol_ConsolidatedStatementOfProfitOrLossIFRS", "PL", True, True,
         items(("RevenueIFRS", None), ("ProfitLossBeforeTaxIFRS", None), ("ProfitLossAttributableToOwnersOfParentIFRS", None),
               ("ContinuingIFRS", None), ("ProfitLossAttributableToOwnersOfParentIFRS", "totalLabel"))),
        (2, "rol_ConsolidatedStatementOfCashFlowsIFRS", "CF", True, True,
         items(("ProfitLossBeforeTaxIFRS", None), ("DepreciationOpeCFIFRS", None),
               ("CashAndCashEquivalentsIFRS", "periodStartLabel"), ("TransferCFIFRS", None),
               ("CashAndCashEquivalentsIFRS", None))),
    ], "IFRS")[True]
    names = ["CashAndCashEquivalentsIFRS", "AssetsIFRS", "RevenueIFRS", "ProfitLossBeforeTaxIFRS", "ContinuingIFRS",
             "ProfitLossAttributableToOwnersOfParentIFRS", "DepreciationOpeCFIFRS", "TransferCFIFRS"]
    seq = [fst.Fact(n, k, k, "", k in ("CashAndCashEquivalentsIFRS", "AssetsIFRS"), "JPY", n)
           for n, k in enumerate(names, 1)]
    got = {s: [f.key for f in v] for s, v in fst.split_by_layout(seq, layouts).items()}
    assert got["BS"] == ["CashAndCashEquivalentsIFRS", "AssetsIFRS"]
    assert got["PL"] == ["RevenueIFRS", "ProfitLossBeforeTaxIFRS", "ContinuingIFRS",
                         "ProfitLossAttributableToOwnersOfParentIFRS"]
    assert got["CF"] == ["ProfitLossBeforeTaxIFRS", "DepreciationOpeCFIFRS", "TransferCFIFRS",
                         "CashAndCashEquivalentsIFRS"]


def test_layout_moves_facts_printed_before_the_statement_and_keeps_print_order_inside() -> None:
    items = lambda *names: [["jppfs_cor", n, 0, p] for n, p in names]
    layouts = fst.statement_layouts([
        (0, "rol_ConsolidatedBalanceSheet", "BS", True, False,
         items(("CashAndDeposits", None), ("Assets", None), ("NoncurrentLiabilities", "totalLabel"),
               ("DeferredTaxLiabilities", None), ("Liabilities", None), ("CapitalStock", None), ("NetAssets", None))),
    ], "JPGAAP")[True]
    names = ["CapitalStock", "NetAssets", "CashAndDeposits", "Assets", "DeferredTaxLiabilities",
             "NoncurrentLiabilities", "Liabilities"]
    seq = [fst.Fact(n, k, k, "", True, "JPY", n) for n, k in enumerate(names, 1)]
    assert [f.key for f in fst.split_by_layout(seq, layouts)["BS"]] == [
        "CashAndDeposits", "Assets", "DeferredTaxLiabilities", "NoncurrentLiabilities", "Liabilities",
        "CapitalStock", "NetAssets",
    ]


def test_name_index_ignores_opening_cash_labels() -> None:
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                   submitted="2025-06-20", standard="IFRS")
    x.by_statement = {"CF": [
        xbrl_fact(1, "NetCashProvidedByUsedInOperatingActivitiesIFRS", 10, "営業活動によるキャッシュ・フロー"),
        xbrl_fact(2, "CashAndCashEquivalentsIFRS", 50, "現金及び現金同等物の期首残高", True),
    ]}
    names = fst.name_index([x], "CF")
    assert set(names.keys) == {"営業活動によるキャッシュ・フロー"}
    assert all(name != "現金及び現金同等物の期首残高" for _, name in names.by_section)
    assert "CashAndCashEquivalentsIFRS" in names.order


def _keys(code: str, fid: int, consolidated: bool = True) -> dict[str, list[str]]:
    return {s: [f.key for f in v] for s, v in filings(code, consolidated)[fid].by_statement.items()}


def test_presentation_splits_by_definition() -> None:
    keys = _keys(DEFINED, 701)
    assert keys["BS"] == ["CashAndDeposits", "OtherCA", "CurrentAssets", "Assets", "ShortTermLoansPayable",
                          "Liabilities", "NetAssets", "LiabilitiesAndNetAssets"]
    assert keys["PL"] == DEF_PL + ["ComprehensiveIncome"]
    assert keys["CF"] == ["IncomeBeforeIncomeTaxes", "DepreciationAndAmortizationOpeCF",
                          "NetCashProvidedByUsedInOperatingActivities", "NetIncreaseDecreaseInCashAndCashEquivalents",
                          "CashAndCashEquivalents"]
    assert _keys(DEFINED, 701, consolidated=False) == {"BS": ["CashAndDeposits", "Assets"]}
    filing = filings(DEFINED)[701]
    pl = {f.key: f for f in filing.by_statement["PL"]}
    cf = {f.key: f for f in filing.by_statement["CF"]}
    assert pl["IncomeBeforeIncomeTaxes"] is cf["IncomeBeforeIncomeTaxes"]


def test_presentation_without_cf_has_no_cf() -> None:
    assert _keys(DEFINED, 703) == {"BS": ["CashAndDeposits", "Assets"], "PL": ["NetSales", "ProfitLoss"]}
    t = table(DEFINED, "CF")
    assert labels(t) == ["25.03"]


def test_presentation_cf_takes_pretax_from_definition_not_extras() -> None:
    by_id = filings(DEFINED)
    learned = fst.learn_pair(by_id[702], by_id[701])
    assert learned.maps["CF"]["税金等調整前当期純利益"].key == "IncomeBeforeIncomeTaxes"
    assert learned.extras["CF"] == {}
    t = table(DEFINED, "CF")
    assert [r["key"] for r in t["rows"]][:2] == ["IncomeBeforeIncomeTaxes", "DepreciationAndAmortizationOpeCF"]
    assert value(t, "IncomeBeforeIncomeTaxes", "25.03") == 198 * M
    assert source(t, "IncomeBeforeIncomeTaxes", "25.03") == "edinet"


def test_presentation_two_sets_follow_accounting_standard() -> None:
    assert _keys(PAIRS, 711, consolidated=False) == {
        "BS": ["CashAndCashEquivalentsIFRS", "AssetsIFRS"], "PL": ["RevenueIFRS", "ProfitLossBeforeTaxIFRS"]}
    assert _keys(PAIRS, 712, consolidated=False) == {"BS": ["CashAndDeposits", "Assets"], "PL": ["NetSales"]}
    only = fst.statement_layouts([(0, "rol_BalanceSheet", "BS", False, False, [["jppfs_cor", "Assets", 0, None]])],
                                 "IFRS")
    assert list(only[False]["BS"].first) == ["Assets"]


def test_presentation_without_consolidated_roles_has_no_consolidated_statements() -> None:
    assert _keys(NOCONSOL, 721) == {}
    assert _keys(NOCONSOL, 721, consolidated=False) == {"BS": ["CashAndDeposits", "Assets"]}
    assert set(_keys(NOCONSOL, 722)) == {"BS", "PL"}
    t = table(NOCONSOL, "PL", consolidated=True)
    assert labels(t) == ["24.03"]


def test_presentation_layout_puts_ci_after_pl_and_period_end_cash_last() -> None:
    items = lambda *names: [["jppfs_cor", n, 0, p] for n, p in names]
    got = fst.statement_layouts([
        (3, "rol_ConsolidatedStatementOfComprehensiveIncome", "PL", True, False,
         items(("ProfitLoss", None), ("ComprehensiveIncome", None))),
        (5, "rol_ConsolidatedStatementOfIncome", "PL", True, False, items(("NetSales", None), ("ProfitLoss", None))),
        (6, "rol_ConsolidatedStatementOfCashFlows-indirect", "CF", True, False,
         items(("CashAndCashEquivalents", "periodStartLabel"), ("NetIncreaseDecreaseInCashAndCashEquivalents", None),
               ("CashAndCashEquivalents", "periodEndLabel"))),
    ], "JPGAAP")[True]
    assert sorted(got["PL"].first, key=got["PL"].first.get) == ["NetSales", "ProfitLoss", "ComprehensiveIncome"]
    seq = [fst.Fact(1, "CashAndCashEquivalents", "CashAndCashEquivalents", "", True, "JPY", 9),
           fst.Fact(2, "NetIncreaseDecreaseInCashAndCashEquivalents", "x", "", False, "JPY", 1)]
    assert [f.id for f in fst.split_by_layout(seq, got)["CF"]] == [2, 1]


def test_non_consolidated() -> None:
    t = table(consolidated=False)
    assert (t["consolidated"], t["basis"]) == (False, "個別")
    assert labels(t) == ["25.03"]
    assert value(t, "CashAndDeposits", "25.03") == 50 * M


def test_auto_prefers_consolidated_and_falls_back_to_single_entity() -> None:
    assert table(consolidated=None)["consolidated"] is True
    t = table(SINGLE, consolidated=None)
    assert (t["consolidated"], t["basis"]) == (False, "個別")
    assert value(t, "CashAndDeposits", "25.03") == 100 * M
    empty = table(SINGLE, consolidated=True)
    assert empty["rows"] == [] and empty["periods"] == []
    assert empty["empty"]["reason"] == "no_material" and empty["empty"]["other_basis_available"] is True


def test_no_material_and_periods() -> None:
    t = table("99990")
    assert t["rows"] == [] and t["empty"]["other_basis_available"] is False
    assert table("99990", consolidated=None)["consolidated"] is True
    assert labels(table(periods=2)) == ["25.09", "25.12"]
    with pytest.raises(ValueError):
        table(statement="SS")


def test_labels_drop_section_heads_and_loss_wording() -> None:
    assert fst._pdf_label("有形固定資産建物及び構築物", "建物及び構築物") == "建物及び構築物"
    assert fst._pdf_label("有形固定資産の取得による支出", "有形固定資産の取得による支出") == "有形固定資産の取得による支出"
    assert fst._pdf_label("固定資産売却益", "") == "固定資産売却益"
    assert fst._pdf_label("営業外収益受取利息", "") == "受取利息"
    assert fst._pdf_label("資産合計", "資産") == "資産合計"
    assert fst._pdf_label("その他#4", "その他") == "その他"
    assert fst._clean_label("税引前当期純利益又は税引前当期純損失（△）") == "税引前当期純利益"
    assert fst._clean_label("棚卸資産の増減額（△は増加）") == "棚卸資産の増減額"
    assert fst._yymm(date(2025, 6, 30)) == "25.06"


def fact(local: str, value: float, key: str | None = None, label: str = "") -> fst.Fact:
    fact.n = getattr(fact, "n", 0) + 1
    return fst.Fact(id=fact.n, key=key or f"pdf:{local}", local=local, label=label, instant=True, unit="JPY",
                    value=value * M)


def bs_learned(maps: dict[str, str], order: list[str]) -> fst.Learned:
    return fst.Learned(period_end=date(2025, 9, 30), cls="Q",
                       maps={"BS": {k: fst.Mapped(v, 1) for k, v in maps.items()}}, order={"BS": order})


NAMES = fst.Names({"現金": "Cash", "社債": "BondsPayable", "資産": "Assets"},
                  ["Cash", "Assets", "BondsPayable", "Liabilities"])
LEARNED = bs_learned({"現金": "Cash", "資産合計": "Assets"}, ["Cash", "Assets"])


def apply(rows: list[fst.Fact], learned: list[fst.Learned], names: fst.Names | None = NAMES) -> list[str | None]:
    out = fst.apply_learned(rows, learned, "BS", date(2026, 6, 30), "Q", names)
    return [None if m is None else f"{m.key}{'' if m.sign == 1 else '-'}" for _, m in out]


def test_name_match_fills_rows_without_value_pairs() -> None:
    rows = [fact("現金", 1), fact("資産合計", 2), fact("社債", -3)]
    assert apply(rows, [LEARNED]) == ["Cash", "Assets", "BondsPayable"]
    assert apply(rows, [LEARNED], None) == ["Cash", "Assets", None]
    assert apply([fact("社債（△）", 3)], []) == ["BondsPayable"]


def test_name_match_skips_duplicated_pdf_names() -> None:
    assert apply([fact("現金", 1), fact("社債", 3), fact("社債#2", 4)], [LEARNED]) == ["Cash", None, None]


def test_value_pair_wins_over_name() -> None:
    learned = bs_learned({"現金": "Cash", "社債": "ConvertibleBonds"}, ["Cash", "ConvertibleBonds"])
    assert apply([fact("現金", 1), fact("社債", 3)], [learned]) == ["Cash", "ConvertibleBonds"]
    clash = bs_learned({"現金": "Cash", "社債": "Cash"}, ["Cash"])
    assert apply([fact("現金", 1), fact("社債", 3)], [clash]) == [None, None]


def test_name_match_skips_elements_taken_in_the_same_period() -> None:
    learned = bs_learned({"長期社債": "BondsPayable"}, ["BondsPayable"])
    assert apply([fact("長期社債", 5), fact("社債", 3)], [learned]) == ["BondsPayable", None]
    names = fst.Names({"社債": "BondsPayable", "社債等": "BondsPayable"}, ["BondsPayable"])
    assert apply([fact("社債", 3), fact("社債等", 4)], [], names) == [None, None]


def test_name_match_respects_print_order() -> None:
    names = fst.Names({"社債": "BondsPayable"}, ["BondsPayable", "Cash", "Assets"])
    assert apply([fact("現金", 1), fact("資産合計", 2), fact("社債", 3)], [LEARNED], names) == ["Cash", "Assets", None]


def test_name_match_skips_rows_whose_unit_kind_differs() -> None:
    assert apply([replace(fact("社債", 3), unit="JPYPerShares")], []) == [None]
    names = fst.Names({"1株当たり純資産": "NetAssetsPerShare"}, ["NetAssetsPerShare"], per_share={"NetAssetsPerShare"})
    assert apply([replace(fact("1株当たり純資産", 3), unit="JPYPerShares")], [], names) == ["NetAssetsPerShare"]
    assert apply([fact("1株当たり純資産", 3)], [], names) == [None]


def test_name_index_keeps_labels_of_one_element() -> None:
    xbrl = fst.Filing(id=1, doc_id="D", source="tdnet", period_type="Q2", period_end=date(2026, 9, 30),
                      submitted="2026-10-28", standard="IFRS")
    xbrl.by_statement = {"BS": [
        fact("A", 1, "BorrowingsCLIFRS", "借入金"), fact("B", 2, "BondsPayableNCLIFRS", "社債"),
        fact("C", 3, "BorrowingsNCLIFRS", "借入金"), fact("D", 4, "OtherCA", "その他"),
    ]}
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="Q1", period_end=date(2026, 6, 30),
                     submitted="2026-07-29", standard="IFRS")
    pdf.by_statement = {"BS": [fact("仮", 5, "pdf:仮", "仮")]}
    names = fst.name_index([pdf, xbrl], "BS")
    assert names.keys == {"社債": "BondsPayableNCLIFRS", "その他": "OtherCA"}
    assert names.order == ["BorrowingsCLIFRS", "BondsPayableNCLIFRS", "BorrowingsNCLIFRS", "OtherCA"]


def test_table_joins_pdf_rows_by_name() -> None:
    t = table(NAMED)
    assert labels(t) == ["26.06", "26.09"]
    assert [c["value"] for c in rows(t)["BondsPayable"]["cells"]] == [4 * M, 5 * M]
    assert [c["value"] for c in rows(t)["CashAndDeposits"]["cells"]] == [10 * M, 12 * M]
    assert "pdf:社債" not in rows(t) and "pdf:現金及び預金" not in rows(t)
    assert rows(t)["pdf:借入金"]["cells"][0]["value"] == 3 * M
    assert rows(t)["BorrowingsCLIFRS"]["cells"][0] is None
    assert heads(rows(t)["BondsPayable"])[-1] == "固定負債" and heads(rows(t)["BorrowingsCLIFRS"])[-1] == "流動負債"


JP_ITEMS = [
    "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "OtherCA", "CurrentAssets",
    "NoncurrentAssetsAbstract", "PropertyPlantAndEquipmentAbstract", "Land", "PropertyPlantAndEquipment",
    "IntangibleAssetsAbstract", "Goodwill", "IntangibleAssets", "InvestmentsAndOtherAssetsAbstract",
    "InvestmentSecurities", "InvestmentsAndOtherAssets", "NoncurrentAssets", "DeferredAssets", "Assets",
    "LiabilitiesAbstract", "CurrentLiabilitiesAbstract", "OtherCL", "CurrentLiabilities",
    "NoncurrentLiabilitiesAbstract", "LongTermLoansPayable", "NoncurrentLiabilities", "Liabilities",
    "NetAssetsAbstract", "ShareholdersEquityAbstract", "CapitalStock", "RetainedEarningsAbstract",
    "LegalRetainedEarnings", "RetainedEarnings", "ShareholdersEquity", "ValuationAndTranslationAdjustmentsAbstract",
    "ValuationDifferenceOnAvailableForSaleSecurities", "ValuationAndTranslationAdjustments",
    "NonControllingInterests", "NetAssets", "LiabilitiesAndNetAssets",
]
IFRS_ITEMS = [
    "AssetsIFRSAbstract", "CurrentAssetsIFRSAbstract", "CashAndCashEquivalentsIFRS", "CurrentAssetsIFRS",
    "NonCurrentAssetsIFRSAbstract", "GoodwillIFRS", "NonCurrentAssetsIFRS", "AssetsIFRS",
    "LiabilitiesAndEquityIFRSAbstract", "CurrentLiabilitiesIFRSAbstract", "BorrowingsCLIFRS",
    "TotalCurrentLiabilitiesIFRS", "NonCurrentLiabilitiesIFRSAbstract", "BorrowingsNCLIFRS",
    "NonCurrentLabilitiesIFRS", "LiabilitiesIFRS", "EquityIFRSAbstract", "ShareCapitalIFRS",
    "EquityAttributableToOwnersOfParentIFRS", "NonControllingInterestsIFRS", "EquityIFRS", "LiabilitiesAndEquityIFRS",
]


def declared(items: list, statement: str = "BS", ifrs: bool = False) -> dict[str, tuple]:
    body = [["x", i[4:], 1, None] if i.startswith("ext:") else ["jppfs_cor", i, 0, None] for i in items]
    return {k: (part.group, part.section) for k, part in fst._role_parts(body, statement, ifrs).items()}


def test_parts_follow_the_headings_jpgaap() -> None:
    got = declared(JP_ITEMS)
    assert got["OtherCA"] == got["CurrentAssets"] == ("Assets", "CurrentAssets")
    assert got["Land"] == got["PropertyPlantAndEquipment"] == ("Assets", "PropertyPlantAndEquipment")
    assert got["Goodwill"] == ("Assets", "IntangibleAssets")
    assert got["InvestmentSecurities"] == ("Assets", "InvestmentsAndOtherAssets")
    assert got["NoncurrentAssets"] == ("Assets", "NoncurrentAssets")
    assert got["DeferredAssets"] == ("Assets", "DeferredAssets") and got["Assets"] == ("Assets", None)
    assert got["OtherCL"] == ("Liabilities", "CurrentLiabilities")
    assert got["LongTermLoansPayable"] == ("Liabilities", "NoncurrentLiabilities")
    assert got["Liabilities"] == ("Liabilities", None)
    assert got["CapitalStock"] == got["LegalRetainedEarnings"] == got["RetainedEarnings"] == ("NetAssets", "ShareholdersEquity")
    assert got["ValuationDifferenceOnAvailableForSaleSecurities"] == ("NetAssets", "ValuationAndTranslationAdjustments")
    assert got["NonControllingInterests"] == got["NetAssets"] == ("NetAssets", None)
    assert got["LiabilitiesAndNetAssets"] == (None, None)
    assert not any("Abstract" in key for key in got)


def test_parts_follow_the_headings_ifrs() -> None:
    got = declared(IFRS_ITEMS, ifrs=True)
    assert got["CashAndCashEquivalentsIFRS"] == ("AssetsIFRS", "CurrentAssetsIFRS")
    assert got["GoodwillIFRS"] == got["NonCurrentAssetsIFRS"] == ("AssetsIFRS", "NonCurrentAssetsIFRS")
    assert got["BorrowingsCLIFRS"] == got["TotalCurrentLiabilitiesIFRS"] == ("LiabilitiesIFRS", "TotalCurrentLiabilitiesIFRS")
    assert got["BorrowingsNCLIFRS"] == ("LiabilitiesIFRS", "NonCurrentLabilitiesIFRS")
    assert got["ShareCapitalIFRS"] == got["EquityAttributableToOwnersOfParentIFRS"] == ("EquityIFRS", None)
    assert got["NonControllingInterestsIFRS"] == ("EquityIFRS", None)
    assert got["LiabilitiesAndEquityIFRS"] == (None, None)


def test_parts_skip_unknown_headings_and_close_at_the_next_heading() -> None:
    bank = declared(["AssetsAbstract", "CashAndDueFromBanksAssetsBNK", "Assets", "LiabilitiesAbstract",
                     "DepositsLiabilitiesBNK", "Liabilities"])
    assert bank["CashAndDueFromBanksAssetsBNK"] == ("Assets", None) and bank["DepositsLiabilitiesBNK"] == ("Liabilities", None)
    railway = declared(["AssetsAbstract", "NoncurrentAssetsAbstract", "NoncurrentAssetsRailwayBusinessAbstractRWY",
                        "ext:Tracks", "PropertyPlantAndEquipmentAbstract", "Land", "IntangibleAssetsAbstract",
                        "Goodwill", "InvestmentsAndOtherAssetsAbstract", "ext:PropertyPlantAndEquipmentAbstract",
                        "InvestmentSecurities", "NoncurrentAssets", "Assets"])
    assert railway["ext:Tracks"] == ("Assets", "NoncurrentAssets")
    assert railway["Land"] == ("Assets", "PropertyPlantAndEquipment") and railway["Goodwill"] == ("Assets", "IntangibleAssets")
    assert railway["InvestmentSecurities"] == ("Assets", "InvestmentsAndOtherAssets")
    assert railway["NoncurrentAssets"] == ("Assets", "NoncurrentAssets")
    power = declared(["AssetsAbstract", "NuclearFuel", "Assets", "LiabilitiesAndNetAssetsAbstractELE", "BondsPayable",
                      "Liabilities", "CapitalStock", "NetAssets", "LiabilitiesAndNetAssets"])
    assert power["BondsPayable"] == power["Liabilities"] == ("Liabilities", None)
    assert power["CapitalStock"] == ("NetAssets", None) and power["LiabilitiesAndNetAssets"] == (None, None)
    assert declared(["CashAndDeposits", "Assets"]) == {"CashAndDeposits": ("Assets", None), "Assets": ("Assets", None)}


def test_parts_stay_open_after_the_total_and_take_single_lines() -> None:
    late = declared(["AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", "CurrentAssets", "PrepaidExpenses",
                     "NoncurrentAssetsAbstract", "PropertyPlantAndEquipment", "IntangibleAssetsAbstract", "Software",
                     "IntangibleAssets", "Goodwill", "InvestmentsAndOtherAssetsGross", "NoncurrentAssets",
                     "LongTermPrepaidExpenses", "Assets",
                     "LiabilitiesAbstract", "CurrentLiabilitiesAbstract", "OtherCL", "CurrentLiabilities",
                     "ProvisionCLAbstract", "ProvisionForBonuses", "ReservesUnderTheSpecialLawsAbstract1",
                     "ReserveForFinancialProductsTransactionLiabilities", "Liabilities",
                     "NetAssetsAbstract", "ShareholdersEquityAbstract", "CapitalStock", "ShareholdersEquity",
                     "SubscriptionRightsToShares", "NetAssets"])
    assert late["PrepaidExpenses"] == ("Assets", "CurrentAssets")
    assert late["PropertyPlantAndEquipment"] == ("Assets", "PropertyPlantAndEquipment")
    assert late["Software"] == late["IntangibleAssets"] == ("Assets", "IntangibleAssets")
    assert late["Goodwill"] == late["LongTermPrepaidExpenses"] == ("Assets", "NoncurrentAssets")
    assert late["InvestmentsAndOtherAssetsGross"] == ("Assets", "InvestmentsAndOtherAssets")
    assert late["NoncurrentAssets"] == ("Assets", "NoncurrentAssets")
    assert late["ProvisionForBonuses"] == ("Liabilities", "CurrentLiabilities")
    assert late["ReserveForFinancialProductsTransactionLiabilities"] == ("Liabilities", "ReservesUnderTheSpecialLaws1")
    assert late["SubscriptionRightsToShares"] == ("NetAssets", None)
    cf = declared(["NetCashProvidedByUsedInOperatingActivitiesAbstract", "SubtotalOpeCF",
                   "NetCashProvidedByUsedInOperatingActivities", "IncomeTaxesRefundOpeCF",
                   "NetCashProvidedByUsedInInvestmentActivitiesAbstract", "NetCashProvidedByUsedInInvestmentActivities",
                   "ProceedsFromLiquidationOfSubsidiariesInvCF", "NetCashProvidedByUsedInFinancingActivitiesAbstract",
                   "NetCashProvidedByUsedInFinancingActivities", "CashAndCashEquivalents"], "CF")
    assert cf["IncomeTaxesRefundOpeCF"] == (None, "NetCashProvidedByUsedInOperatingActivities")
    assert cf["ProceedsFromLiquidationOfSubsidiariesInvCF"] == (None, "NetCashProvidedByUsedInInvestmentActivities")
    assert cf["CashAndCashEquivalents"] == (None, None)
    one = declared(["LiabilitiesAbstract", "CurrentLiabilitiesAbstract", "OtherCL", "CurrentLiabilities",
                    "NoncurrentLiabilities", "Liabilities", "NetAssetsAbstract", "NetAssets"])
    assert one["NoncurrentLiabilities"] == ("Liabilities", "NoncurrentLiabilities")
    bank = declared(["AssetsAbstract", "LoansAndBillsDiscountedAssetsBNK", "PropertyPlantAndEquipment", "BuildingsNet",
                     "Assets"])
    assert bank["PropertyPlantAndEquipment"] == bank["BuildingsNet"] == ("Assets", None)


def test_parts_cf_and_pl() -> None:
    cf = declared(["NetCashProvidedByUsedInOperatingActivitiesAbstract", "IncomeBeforeIncomeTaxes", "SubtotalOpeCF",
                   "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInInvestmentActivitiesAbstract",
                   "PurchaseOfPropertyPlantAndEquipmentInvCF", "NetCashProvidedByUsedInInvestmentActivities",
                   "NetIncreaseDecreaseInCashAndCashEquivalents", "CashAndCashEquivalents"], "CF")
    assert cf["IncomeBeforeIncomeTaxes"] == cf["NetCashProvidedByUsedInOperatingActivities"] == (
        None, "NetCashProvidedByUsedInOperatingActivities")
    assert cf["PurchaseOfPropertyPlantAndEquipmentInvCF"] == (None, "NetCashProvidedByUsedInInvestmentActivities")
    assert cf["CashAndCashEquivalents"] == (None, None)
    ifrs = declared(["CashFlowsFromInvestingActivitiesIFRSAbstract", "OtherInvCFIFRS",
                     "NetCashProvidedByUsedInInvestingActivitiesIFRS", "CashAndCashEquivalentsIFRS"], "CF", True)
    assert ifrs["OtherInvCFIFRS"] == (None, "NetCashProvidedByUsedInInvestingActivitiesIFRS")
    assert ifrs["CashAndCashEquivalentsIFRS"] == (None, None)
    pl = declared(["NetSales", "OperatingIncome", "NonOperatingIncomeAbstract", "InterestIncomeNOI", "NonOperatingIncome",
                   "OrdinaryIncome", "ExtraordinaryLossAbstract", "ImpairmentLossEL", "ExtraordinaryLoss", "ProfitLoss"],
                  "PL")
    assert pl["NetSales"] == pl["OrdinaryIncome"] == pl["ProfitLoss"] == (None, None)
    assert pl["InterestIncomeNOI"] == pl["NonOperatingIncome"] == (None, "NonOperatingIncome")
    assert pl["ImpairmentLossEL"] == (None, "ExtraordinaryLoss")
    assert declared(["AssetsAbstract", "CashAndDeposits"], "PL") == {"CashAndDeposits": (None, None)}


def _tree_items(text: str, ifrs: bool = False) -> list[list]:
    """1 行 1 要素。字下げ 2 つで 1 段。見出しは `要素=表示名` で表示名を付ける。"""
    out = []
    for line in text.strip("\n").splitlines():
        name, _, label = line.strip().partition("=")
        depth = (len(line) - len(line.lstrip())) // 2
        custom = name.startswith("ext:")
        ns = "x" if custom else ("jpigp_cor" if ifrs else "jppfs_cor")
        out.append([ns, name[4:] if custom else name, int(custom), None, depth, label or None])
    return out


def nested(text: str, statement: str = "BS", ifrs: bool = False) -> dict[str, tuple]:
    return {k: (p.group, p.section) for k, p in fst._role_parts(_tree_items(text), statement, ifrs).items()}


def shown(text: str, statement: str = "BS") -> dict[str, tuple]:
    """要素 → (見出しの表示名の道筋, 字下げ)。"""
    return {k: (tuple(n for _, n in p.heads), p.indent) for k, p in fst._role_parts(_tree_items(text), statement, False).items()}


def _flat(text: str) -> list[str]:
    return [line.strip().partition("=")[0] for line in text.strip("\n").splitlines()]


JP_TREE = """
AssetsAbstract
  CurrentAssetsAbstract
    CashAndDeposits
    OtherCA
    CurrentAssets
  NoncurrentAssetsAbstract
    PropertyPlantAndEquipmentAbstract
      Land
      PropertyPlantAndEquipment
    IntangibleAssetsAbstract
      Goodwill
      IntangibleAssets
    InvestmentsAndOtherAssetsAbstract
      InvestmentSecurities
      InvestmentsAndOtherAssets
    NoncurrentAssets
  DeferredAssets
  Assets
LiabilitiesAbstract
  CurrentLiabilitiesAbstract
    OtherCL
    CurrentLiabilities
  NoncurrentLiabilitiesAbstract
    LongTermLoansPayable
    NoncurrentLiabilities
  Liabilities
NetAssetsAbstract
  ShareholdersEquityAbstract
    CapitalStock
    RetainedEarningsAbstract
      LegalRetainedEarnings
      RetainedEarnings
    ShareholdersEquity
  ValuationAndTranslationAdjustmentsAbstract
    ValuationDifferenceOnAvailableForSaleSecurities
    ValuationAndTranslationAdjustments
  NonControllingInterests
  NetAssets
LiabilitiesAndNetAssets
"""
IFRS_TREE = """
AssetsIFRSAbstract
  CurrentAssetsIFRSAbstract
    CashAndCashEquivalentsIFRS
    CurrentAssetsIFRS
  NonCurrentAssetsIFRSAbstract
    GoodwillIFRS
    NonCurrentAssetsIFRS
  AssetsIFRS
LiabilitiesAndEquityIFRSAbstract
  CurrentLiabilitiesIFRSAbstract
    BorrowingsCLIFRS
    TotalCurrentLiabilitiesIFRS
  NonCurrentLiabilitiesIFRSAbstract
    BorrowingsNCLIFRS
    NonCurrentLabilitiesIFRS
  LiabilitiesIFRS
  EquityIFRSAbstract
    ShareCapitalIFRS
    EquityAttributableToOwnersOfParentIFRS
    NonControllingInterestsIFRS
    EquityIFRS
  LiabilitiesAndEquityIFRS
"""
LATE_TREE = """
AssetsAbstract
  CurrentAssetsAbstract
    CashAndDeposits
    CurrentAssets
    OperationalInvestmentSecuritiesCA
  NoncurrentAssetsAbstract
    PropertyPlantAndEquipmentAbstract
      OtherPPE
        AccumulatedDepreciationOtherPPE
      PropertyPlantAndEquipment
      BuildingsAndOtherFacilitiesHeatingIlluminationVentilationAndOtherPPEAbstract
        Buildings
          BuildingsNet
      ToolsFurnitureAndFixtures
    IntangibleAssetsAbstract
      Goodwill
      IntangibleAssets
      OtherIntangibleAssetsIAAbstract
        ext:CustomerRelationshipIA
    InvestmentsAndOtherAssetsAbstract
      InvestmentSecurities
      InvestmentsAndOtherAssets
      OtherLongTermAssetsIOAAbstract
        LeaseAndGuaranteeDeposits
    NoncurrentAssets
  Assets
LiabilitiesAbstract
  CurrentLiabilitiesAbstract
    OtherCL
    CurrentLiabilities
    ProvisionCLAbstract
      ProvisionForBonuses
    AccountsPayableOrdinaryTransactionsPaidInShortTermCLAbstract
      AccruedConsumptionTaxes
  Liabilities
NetAssetsAbstract
  ShareholdersEquityAbstract
    CapitalStock
    ShareholdersEquity
  NetAssets
"""
LATE_CF = """
NetCashProvidedByUsedInOperatingActivitiesAbstract
  SubtotalOpeCF
    InterestIncomeOpeCF
  NetCashProvidedByUsedInOperatingActivities
  IncomeTaxesPaidRefundOpeCF
NetCashProvidedByUsedInFinancingActivitiesAbstract
  PurchaseOfTreasuryStockFinCF
  NetCashProvidedByUsedInFinancingActivities
  RepaymentsOfLeaseObligationsFinCF
EffectOfExchangeRateChangeOnCashAndCashEquivalents
NetIncreaseDecreaseInCashAndCashEquivalents
CashAndCashEquivalents
"""


def test_parts_for_merging_come_from_the_order_even_with_depth() -> None:
    assert nested(JP_TREE) == declared(_flat(JP_TREE)) == declared(JP_ITEMS)
    assert nested(IFRS_TREE, ifrs=True) == declared(_flat(IFRS_TREE), ifrs=True)
    assert nested(LATE_TREE) == declared(_flat(LATE_TREE))
    assert nested(LATE_TREE)["Buildings"] == ("Assets", "NoncurrentAssets")


def test_heads_follow_the_declared_headings() -> None:
    got = shown(JP_TREE)
    assert got["CashAndDeposits"] == (("AssetsAbstract", "CurrentAssetsAbstract"), 0)
    assert got["Land"] == (("AssetsAbstract", "NoncurrentAssetsAbstract", "PropertyPlantAndEquipmentAbstract"), 0)
    assert got["NoncurrentAssets"] == (("AssetsAbstract", "NoncurrentAssetsAbstract"), 0)
    assert got["Assets"] == got["DeferredAssets"] == (("AssetsAbstract",), 0)
    assert got["LegalRetainedEarnings"] == (("NetAssetsAbstract", "ShareholdersEquityAbstract", "RetainedEarningsAbstract"), 0)
    assert got["LiabilitiesAndNetAssets"] == ((), 0)
    railway = shown("""
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    NoncurrentAssetsRailwayBusinessAbstractRWY=鉄道事業固定資産
      ext:Tracks
    ext:OwnAbstract=自社の見出し
      Land
""")
    assert railway["ext:Tracks"] == (("資産の部", "固定資産", "鉄道事業固定資産"), 0)
    assert railway["Land"] == (("資産の部", "固定資産", "自社の見出し"), 0)
    pl = shown("""
NetSales
NonOperatingIncomeAbstract=営業外収益
  InterestIncomeNOI
  NonOperatingIncome
OrdinaryIncome
""", "PL")
    assert pl["InterestIncomeNOI"] == pl["NonOperatingIncome"] == (("営業外収益",), 0)
    assert pl["NetSales"] == pl["OrdinaryIncome"] == ((), 0)


def test_heads_put_rows_after_the_total_under_the_heading() -> None:
    got = shown(LATE_TREE)
    ppe = ("AssetsAbstract", "NoncurrentAssetsAbstract", "PropertyPlantAndEquipmentAbstract")
    assert got["OperationalInvestmentSecuritiesCA"] == (("AssetsAbstract", "CurrentAssetsAbstract"), 0)
    assert got["OtherPPE"] == got["PropertyPlantAndEquipment"] == got["Buildings"] == (ppe, 0)
    assert got["AccumulatedDepreciationOtherPPE"] == got["BuildingsNet"] == (ppe, 1)
    assert got["ToolsFurnitureAndFixtures"] == (ppe, 0)
    assert got["ext:CustomerRelationshipIA"] == (("AssetsAbstract", "NoncurrentAssetsAbstract", "IntangibleAssetsAbstract"), 0)
    assert got["LeaseAndGuaranteeDeposits"] == (
        ("AssetsAbstract", "NoncurrentAssetsAbstract", "InvestmentsAndOtherAssetsAbstract"), 0)
    assert got["ProvisionForBonuses"] == got["AccruedConsumptionTaxes"] == (
        ("LiabilitiesAbstract", "CurrentLiabilitiesAbstract"), 0)
    cf = shown(LATE_CF, "CF")
    assert cf["InterestIncomeOpeCF"] == cf["IncomeTaxesPaidRefundOpeCF"] == (
        ("NetCashProvidedByUsedInOperatingActivitiesAbstract",), 0)
    assert cf["RepaymentsOfLeaseObligationsFinCF"] == (("NetCashProvidedByUsedInFinancingActivitiesAbstract",), 0)
    assert cf["EffectOfExchangeRateChangeOnCashAndCashEquivalents"] == cf["CashAndCashEquivalents"] == ((), 0)


def test_heads_indent_children_of_rows_with_values() -> None:
    bank = shown("""
AssetsAbstract=資産の部
  LoansAndBillsDiscountedAssetsBNK
  PropertyPlantAndEquipment
    BuildingsNet
    Land
  IntangibleAssets
  Assets
""")
    assert bank["PropertyPlantAndEquipment"] == bank["IntangibleAssets"] == (("資産の部",), 0)
    assert bank["BuildingsNet"] == bank["Land"] == (("資産の部",), 1)
    power = shown("""
AssetsAbstract=資産の部
  NoncurrentAssets
    ElectricUtilityPlantAndEquipmentAssetsELE
      HydroelectricPowerProductionFacilitiesNCAElectricELE
  Assets
LiabilitiesAndNetAssetsAbstractELE=負債及び純資産の部
  ShareholdersEquity
    CapitalStock
  NonControllingInterests
""")
    assert power["NoncurrentAssets"] == (("資産の部",), 0)
    assert power["ElectricUtilityPlantAndEquipmentAssetsELE"] == (("資産の部",), 1)
    assert power["HydroelectricPowerProductionFacilitiesNCAElectricELE"] == (("資産の部",), 2)
    assert power["CapitalStock"] == (("負債及び純資産の部",), 1)
    assert power["NonControllingInterests"] == (("負債及び純資産の部",), 0)


def test_heads_empty_headings_and_rows_outside_the_sections() -> None:
    got = shown("""
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
    Land
    OtherNetPPE
    PropertyPlantAndEquipment
    Software
    IntangibleAssetsAbstract=無形固定資産
      Goodwill
    NoncurrentAssets
  Assets
NetAssetsAbstract=純資産の部
  ShareholdersEquityAbstract=株主資本
    CapitalStock
    ShareholdersEquity
    ValuationAndTranslationAdjustmentsAbstract=評価・換算差額等
    ValuationDifferenceOnAvailableForSaleSecurities
    ValuationAndTranslationAdjustments
  ext:OtherAbstract=その他
    SubscriptionRightsToShares
  ValuationAndTranslationAdjustmentsAbstract=評価・換算差額等
    ForeignCurrencyTranslationAdjustment
    ValuationAndTranslationAdjustments
    NonControllingInterests
  NetAssets
""")
    assert got["Land"] == got["OtherNetPPE"] == got["PropertyPlantAndEquipment"] == (("資産の部", "固定資産", "有形固定資産"), 0)
    assert got["Software"] == got["NoncurrentAssets"] == (("資産の部", "固定資産"), 0)
    assert got["Goodwill"] == (("資産の部", "固定資産", "無形固定資産"), 0)
    assert got["ValuationDifferenceOnAvailableForSaleSecurities"] == (("純資産の部", "評価・換算差額等"), 0)
    assert got["SubscriptionRightsToShares"] == (("純資産の部", "その他"), 0)
    assert got["NonControllingInterests"] == got["NetAssets"] == (("純資産の部",), 0)
    pl = shown("""
NetSales
CostOfSalesAbstract=売上原価
  CostOfSalesOfCompletedConstructionContractsCNS
GrossProfit
SellingGeneralAndAdministrativeExpensesAbstract=販売費及び一般管理費
DirectorsCompensationsSGA
SellingGeneralAndAdministrativeExpenses
OperatingIncome
""", "PL")
    assert pl["CostOfSalesOfCompletedConstructionContractsCNS"] == (("売上原価",), 0)
    assert pl["GrossProfit"] == pl["OperatingIncome"] == ((), 0)
    assert pl["DirectorsCompensationsSGA"] == pl["SellingGeneralAndAdministrativeExpenses"] == (("販売費及び一般管理費",), 0)
    cf = shown("""
NetCashProvidedByUsedInFinancingActivitiesAbstract=財務活動によるキャッシュ・フロー
  PurchaseOfTreasuryStockFinCF
  NetCashProvidedByUsedInFinancingActivities
  EffectOfExchangeRateChangeOnCashAndCashEquivalents
  IncreaseInCashAndCashEquivalentsFromNewlyConsolidatedSubsidiaryCCE
""", "CF")
    assert cf["EffectOfExchangeRateChangeOnCashAndCashEquivalents"] == ((), 0)
    assert cf["IncreaseInCashAndCashEquivalentsFromNewlyConsolidatedSubsidiaryCCE"] == ((), 0)


def test_section_headings_after_the_parent_total_leave_the_closed_parent() -> None:
    ifrs = shown("""
LiabilitiesIFRSAbstract=負債の部
  CurrentLiabilitiesIFRSAbstract=流動負債
    BorrowingsCLIFRS
    TotalCurrentLiabilitiesIFRS
  LiabilitiesIFRS
  EquityIFRSAbstract=資本の部
    ShareCapitalIFRS
    EquityAttributableToOwnersOfParentIFRS
    NonControllingInterestsIFRS
    EquityIFRS
  LiabilitiesAndEquityIFRS
""")
    assert ifrs["BorrowingsCLIFRS"] == (("負債の部", "流動負債"), 0)
    assert ifrs["LiabilitiesIFRS"] == (("負債の部",), 0)
    assert ifrs["ShareCapitalIFRS"] == ifrs["EquityIFRS"] == ifrs["NonControllingInterestsIFRS"] == (("資本の部",), 0)
    assert ifrs["LiabilitiesAndEquityIFRS"] == ((), 0)
    jp = shown("""
LiabilitiesAbstract=負債の部
  CurrentLiabilitiesAbstract=流動負債
    OtherCL
    CurrentLiabilities
    NoncurrentLiabilitiesAbstract=固定負債
    ProvisionForLossOnBusinessOfSubsidiariesAndAffiliatesNCL
    NoncurrentLiabilities
  Liabilities
NetAssetsAbstract=純資産の部
  ShareholdersEquityAbstract=株主資本
    CapitalStock
    ShareholdersEquity
    ValuationAndTranslationAdjustmentsAbstract=評価・換算差額等
      ValuationDifferenceOnAvailableForSaleSecurities
      ValuationAndTranslationAdjustments
  NetAssets
""")
    assert jp["ProvisionForLossOnBusinessOfSubsidiariesAndAffiliatesNCL"] == jp["NoncurrentLiabilities"] == (
        ("負債の部", "固定負債"), 0)
    assert jp["Liabilities"] == (("負債の部",), 0)
    assert jp["ValuationDifferenceOnAvailableForSaleSecurities"] == jp["ValuationAndTranslationAdjustments"] == (
        ("純資産の部", "評価・換算差額等"), 0)
    assert jp["NetAssets"] == (("純資産の部",), 0)


def test_heads_only_when_every_item_has_a_depth() -> None:
    items = _tree_items(LATE_TREE)
    flat = fst._role_parts([item[:4] for item in items], "BS", False)
    assert {p.heads for p in flat.values()} == {None}
    mixed = [*items[:-1], [*items[-1][:4], None]]
    assert fst._role_parts(mixed, "BS", False) == flat
    deep = fst._role_parts(items, "BS", False)
    assert all(p.heads is not None for p in deep.values())
    assert {k: p.coarse for k, p in deep.items()} == {k: p.coarse for k, p in flat.items()}
    no_label = fst._role_parts([item[:5] for item in items], "BS", False)
    assert no_label["Buildings"].heads == deep["Buildings"].heads
    layouts = fst.statement_layouts([(0, "rol_BalanceSheet", "BS", False, False, items)], None)
    assert layouts[False]["BS"].parts["Buildings"].heads[-1] == (
        "PropertyPlantAndEquipmentAbstract", "PropertyPlantAndEquipmentAbstract")
    assert layouts[False]["BS"].first["Buildings"] == 11


def test_same_part_for_merging_ignores_heads() -> None:
    a = fst.Part("Assets", "CurrentAssets", False, (("AssetsAbstract", "資産の部"),), 0)
    b = fst.Part("Assets", "CurrentAssets", False, None, 0)
    c = fst.Part("Assets", "CurrentAssets", False, (("X", "流動資産"),), 1)
    assert fst._same_part(a, b) and fst._same_part(a, c)
    assert not fst._same_part(a, fst.Part("Assets", "NoncurrentAssets", False, a.heads, 0))
    assert fst._same_part(None, None) and not fst._same_part(a, None)


CA = fst.Part("Assets", "CurrentAssets", False)
NCA = fst.Part("Assets", "NoncurrentAssets", False)
TOP = fst.Part("Assets", None, False)


def test_row_parts_take_the_newest_filing_and_fill_rows_without_a_definition() -> None:
    old = column("2024-03-31", {"Tax": 1}, {"Tax": CA})
    new = column("2025-03-31", {"Cash": 1, "CurrentAssets": 1, "Tax": 1, "Assets": 1},
                 {"Cash": CA, "CurrentAssets": CA, "Tax": NCA, "Assets": TOP})
    keys = ["pdf:頭", "Cash", "pdf:売掛金", "CurrentAssets", "pdf:土地", "Tax", "Assets", "pdf:末尾"]
    assert fst.row_parts([old, new], keys) == [CA, CA, CA, CA, NCA, NCA, TOP, None]
    assert fst.row_parts([new, old], keys)[5] == CA
    assert fst.row_parts([column("2025-03-31", {"A": 1})], ["A", "pdf:B"]) == [None, None]


def heads(row: dict) -> tuple[str, ...]:
    return tuple(h["name"] for h in row["heads"])


def test_table_rows_carry_sections() -> None:
    got = {k: heads(r) for k, r in rows(table(DEFINED)).items()}
    assert got["CashAndDeposits"] == got["CurrentAssets"] == ("資産", "流動資産")
    assert got["Assets"] == ("資産",)
    assert got["ShortTermLoansPayable"] == ("負債",)
    assert got["NetAssets"] == ("純資産",)
    assert got["LiabilitiesAndNetAssets"] == ()
    assert {r["indent"] for r in rows(table(DEFINED)).values()} == {0}
    t = table(DEFINED, statement="CF")
    assert {heads(r) for r in t["rows"][:3]} == {("営業活動によるキャッシュ・フロー",)}
    assert heads(t["rows"][-1]) == ()
    assert {heads(r) for r in table(DEFINED, statement="PL")["rows"]} == {()}
    assert {heads(r) for r in table()["rows"]} == {()}


def test_group_headings_ignore_labels_that_are_not_totals() -> None:
    assert fst._heading("EquityIFRS", {"EquityIFRS": "四半期末残高"}, fst.BS_GROUPS) == "資本"
    assert fst._heading("Assets", {"Assets": "資産の部合計"}, fst.BS_GROUPS) == "資産の部"
    assert fst._heading("IntangibleAssets", {"IntangibleAssets": "無形固定資産"}, fst.SECTION_NAMES) == "無形固定資産"


def test_table_is_split_where_the_accounting_standard_changes() -> None:
    t = table(SWITCH)
    assert [p["accounting_standard"] for p in t["periods"]] == ["IFRS"] and t["accounting_standards"] == ["IFRS"]
    assert {k: heads(r) for k, r in rows(t).items()} == {
        "CashAndCashEquivalentsIFRS": ("資産", "流動資産"), "GoodwillIFRS": ("資産", "非流動資産"),
        "ext:Deposits": ("資産", "非流動資産"), "ShareCapitalIFRS": ("資本",),
    }
    assert all(r["merged_keys"] == [] and len(r["cells"]) == 1 for r in t["rows"])
    assert rows(t)["GoodwillIFRS"]["cells"][0]["value"] == 6 * M
    (old,) = t["earlier"]
    assert set(old) == {"consolidated", "basis", "accounting_standards", "periods", "rows"}
    assert [p["accounting_standard"] for p in old["periods"]] == ["JPGAAP"] and old["basis"] == "連結"
    got = {k: heads(r) for k, r in rows(old).items()}
    assert got["CashAndDeposits"] == got["ext:Deposits"] == ("資産", "流動資産")
    assert got["Land"] == ("資産", "有形固定資産") and got["Goodwill"] == ("資産", "無形固定資産")
    assert got["CapitalStock"] == ("純資産", "株主資本")
    assert rows(old)["Goodwill"]["cells"][0]["value"] == 5 * M and rows(old)["ext:Deposits"]["cells"][0]["value"] == 2 * M
    one = table(SWITCH, periods=1)
    assert one["earlier"] == [] and len(one["rows"]) == 4
    assert table()["earlier"] == [] and table(DEFINED)["earlier"] == []


def test_standard_runs_follow_the_roles_then_the_filing() -> None:
    ifrs = fst.Part(None, None, True)
    jp = fst.Part(None, None, False)
    cols = [column("2022-03-31", {"A": 1}, {"A": jp}), column("2023-03-31", {"A": 1}),
            column("2024-03-31", {"B": 1}, {"B": ifrs}), column("2025-03-31", {"B": 1})]
    cols[1].primary.standard = None
    cols[3].primary.standard = "IFRS"
    assert [len(run) for run in fst._standard_runs(cols)] == [2, 2]
    cols[0].primary.standard = "IFRS"
    assert [len(run) for run in fst._standard_runs(cols[:2])] == [2]
    assert fst._standard_runs([]) == []
    lone = [column("2022-03-31", {"B": 1}, {"B": ifrs}), column("2022-06-30", {"B": 1}),
            column("2022-09-30", {"B": 1}, {"B": ifrs}), column("2022-12-31", {"A": 1})]
    lone[1].primary.standard = "JPGAAP"
    lone[3].primary.standard = "JPGAAP"
    assert [len(run) for run in fst._standard_runs(lone)] == [3, 1]
    lone[1].parts["B"] = jp
    assert [len(run) for run in fst._standard_runs(lone)] == [1, 1, 1, 1]


def test_quarterly_report_takes_year_to_date_over_three_months() -> None:
    by_id = filings(QUARTER)
    for fid in (401, 403):
        keys = [f.key for f in by_id[fid].seq if not f.instant]
        assert len(keys) == len(set(keys))
    assert {f.key: f.value for f in by_id[401].by_statement["PL"]}["NetSales"] == 250 * M
    assert {f.key: f.value for f in by_id[403].by_statement["PL"]}["NetSales"] == 390 * M
    assert [f.key for f in by_id[401].by_statement["BS"]] == ["CashAndDeposits", "Assets", "LiabilitiesAndNetAssets"]
    t = table(QUARTER, statement="PL")
    assert labels(t) == ["24.09", "24.12", "25.03"]
    assert [r["key"] for r in t["rows"]] == ["NetSales", "IncomeBeforeIncomeTaxes", "ProfitLoss",
                                             "BasicEarningsLossPerShare"]
    assert [c["value"] for c in rows(t)["NetSales"]["cells"]] == [250 * M, 390 * M, 520 * M]
    assert [c["value"] for c in rows(t)["BasicEarningsLossPerShare"]["cells"]] == [13, 20, 26]
    assert [c["quarter"] for c in rows(t)["NetSales"]["cells"]] == [None, 140 * M, 130 * M]
    assert all("quarter" not in c for c in rows(t)["BasicEarningsLossPerShare"]["cells"])
    assert all("quarter" not in c for r in table(QUARTER, statement="CF")["rows"] for c in r["cells"] if c)
    assert [c["value"] for c in rows(table(QUARTER))["Assets"]["cells"]] == [40 * M, 50 * M, 60 * M]


def test_quarterly_report_cf_rows_from_pl_are_year_to_date() -> None:
    t = table(QUARTER, statement="CF")
    assert t["rows"][0]["key"] == "IncomeBeforeIncomeTaxes"
    assert [c["value"] for c in t["rows"][0]["cells"]] == [130 * M, 200 * M, 260 * M]


def test_learn_pair_matches_pdf_against_year_to_date() -> None:
    by_id = filings(QUARTER)
    learned = fst.learn_pair(by_id[402], by_id[401])
    assert {k: m.key for k, m in learned.maps["PL"].items()} == {
        "売上高": "NetSales", "税金等調整前中間純利益": "IncomeBeforeIncomeTaxes", "中間純利益": "ProfitLoss",
    }
    assert learned.maps["CF"]["税金等調整前中間純利益"].key == "IncomeBeforeIncomeTaxes"
    assert learned.extras["CF"] == {"IncomeBeforeIncomeTaxes": None}


def test_second_printing_of_the_same_value_is_the_same_item() -> None:
    by_id = filings(TWICE)
    learned = fst.learn_pair(by_id[502], by_id[501])
    assert learned.twice["PL"] == {"当期利益"}
    assert learned.maps["PL"]["当期利益"].key == "ProfitLoss"
    assert "当期利益#2" not in learned.maps["PL"]
    assert learned.maps["PL"]["非支配持分#2"].key == "ComprehensiveIncomeAttributableToNonControllingInterests"
    t = table(TWICE, statement="PL")
    assert labels(t) == ["25.03", "25.06", "25.09"]
    assert [c["value"] for c in rows(t)["ProfitLoss"]["cells"]] == [150 * M, 30 * M, 60 * M]
    assert "pdf:当期利益#2" in rows(t)
    assert [c and c["value"] for c in rows(t)["pdf:当期利益#2"]["cells"]] == [None, None, 25 * M]
    assert value(t, "ComprehensiveIncomeAttributableToNonControllingInterests", "25.06") == 2 * M
    assert value(t, "ProfitLossAttributableToNonControllingInterests", "25.06") == 2 * M


def pl_fact(local: str, value: float) -> fst.Fact:
    f = fact(local, value)
    return fst.Fact(id=f.id, key=f.key, local=local, label="", instant=False, unit="JPY", value=f.value)


def test_second_printing_survives_the_one_element_per_row_check() -> None:
    learned = fst.Learned(period_end=date(2025, 3, 31), cls="FY",
                          maps={"PL": {"当期利益": fst.Mapped("ProfitLoss", 1)}}, order={"PL": ["ProfitLoss"]},
                          twice={"PL": {"当期利益"}})
    got = fst.apply_learned([pl_fact("当期利益", 30), pl_fact("当期利益#2", 30)], [learned], "PL",
                            date(2025, 6, 30), "Q")
    assert [m and m.key for _, m in got] == ["ProfitLoss", "ProfitLoss"]
    got = fst.apply_learned([pl_fact("当期利益", 30), pl_fact("当期利益#2", 25)], [learned], "PL",
                            date(2025, 6, 30), "Q")
    assert [m and m.key for _, m in got] == ["ProfitLoss", None]
    plain = fst.Learned(period_end=date(2025, 3, 31), cls="FY",
                        maps={"PL": {"当期利益": fst.Mapped("ProfitLoss", 1), "当期利益#2": fst.Mapped("ProfitLoss", 1)}},
                        order={"PL": ["ProfitLoss"]})
    got = fst.apply_learned([pl_fact("当期利益", 30), pl_fact("当期利益#2", 30)], [plain], "PL",
                            date(2025, 6, 30), "Q")
    assert [m for _, m in got] == [None, None]


def test_pdf_rows_drop_section_heads_but_keep_totals() -> None:
    assert fst.strip_heads("特別損失固定資産除却損", "PL") == "固定資産除却損"
    assert fst.strip_heads("特別損失のれん償却額", "PL") == "のれん償却額"
    assert fst.strip_heads("営業外費用計量法関連費用", "PL") == "計量法関連費用"
    assert fst.strip_heads("有形固定資産無形固定資産のれん", "BS") == "のれん"
    assert fst.strip_heads("流動負債:借入金及び社債", "BS") == "借入金及び社債"
    for name, statement in [
        ("特別損失合計", "PL"), ("営業外収益合計", "PL"), ("営業外費用計", "PL"), ("無形固定資産合計", "BS"),
        ("有形固定資産合計#2", "BS"), ("その他の包括利益累計額合計", "BS"), ("有形固定資産(純額)", "BS"),
        ("その他の包括利益累計額の内訳その他有価証券", "BS"), ("有形固定資産及び無形固定資産の増加額", "BS"),
        ("有形固定資産の取得による支出", "CF"), ("営業活動によるキャッシュ・フロー計", "CF"), ("特別損失", "PL"),
        ("固定資産仮勘定", "BS"),
    ]:
        assert fst.strip_heads(name, statement) == name
    rows = [fact("有形固定資産建物", 1), fact("その他", 2), fact("無形固定資産その他", 3), fact("その他#2", 4)]
    info = fst.pdf_rows(rows, "BS")
    assert info.names == ["建物", "その他", "その他#2", "その他#3"]
    assert info.bases == ["建物", "その他", "その他", "その他"] and info.copies == {}


def xbrl_fact(fid: int, key: str, value: float, label: str = "", instant: bool = False) -> fst.Fact:
    return fst.Fact(id=fid, key=key, local=key, label=label, instant=instant, unit="JPY", value=value * M)


def pair(statement: str, xbrl: list[fst.Fact], pdf: list[fst.Fact], end: date = date(2025, 3, 31),
         kind: str = "FY") -> fst.Learned:
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type=kind, period_end=end, submitted="2025-06-20",
                   standard="JPGAAP")
    x.by_statement = {statement: xbrl}
    x.seq = list(xbrl)
    pdf_filing = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type=kind, period_end=end,
                            submitted="2025-05-10", standard="JPGAAP")
    pdf_filing.by_statement = {statement: pdf}
    return fst.learn_pair(pdf_filing, x)


def keys_of(got: list[tuple[fst.Fact, fst.Mapped | None]]) -> list[str | None]:
    return [None if m is None else m.key for _, m in got]


def test_section_heads_that_move_between_periods_keep_the_learned_item() -> None:
    learned = pair("PL", [
        xbrl_fact(1, "NetSales", 500), xbrl_fact(2, "LossOnRetirementOfNoncurrentAssetsEL", 7),
        xbrl_fact(3, "ImpairmentLossEL", 9),
    ], [pl_fact("売上高", 500), pl_fact("特別損失固定資産除却損", 7), pl_fact("減損損失", 9)])
    assert set(learned.maps["PL"]) == {"売上高", "固定資産除却損", "減損損失"}
    got = fst.apply_learned([pl_fact("売上高", 120), pl_fact("固定資産除却損", 1), pl_fact("特別損失減損損失", 3)],
                            [learned], "PL", date(2025, 6, 30), "Q")
    assert keys_of(got) == ["NetSales", "LossOnRetirementOfNoncurrentAssetsEL", "ImpairmentLossEL"]


def bs_fact(local: str, value: float) -> fst.Fact:
    return fact(local, value)


def test_rows_outside_bs_do_not_take_balances_of_other_statements() -> None:
    bs = [xbrl_fact(1, "CashAndCashEquivalentsIFRS", 950, instant=True), xbrl_fact(2, "IntangibleAssets", 410, instant=True),
          xbrl_fact(3, "CurrentLiabilities", 264, instant=True), xbrl_fact(4, "Assets", 3000, instant=True)]
    pl = [xbrl_fact(5, "NetSales", 5000), xbrl_fact(6, "IncomeBeforeIncomeTaxes", 1980), xbrl_fact(7, "ProfitLoss", 1500)]
    cf = [xbrl_fact(8, "NetCashProvidedByUsedInOperatingActivities", 1800),
          xbrl_fact(9, "NetIncreaseDecreaseInCashAndCashEquivalents", 686)]
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="Q2", period_end=date(2024, 6, 30),
                   submitted="2024-08-10", standard="IFRS")
    x.by_statement = {"BS": bs, "PL": pl, "CF": cf}
    x.seq = [*bs, *pl, *cf]
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="Q2", period_end=date(2024, 6, 30),
                     submitted="2024-08-05", standard="IFRS")
    balance = replace(pl_fact("現金及び現金同等物の期首残高", 264), instant=True)
    pdf.by_statement = {
        "BS": [bs_fact("無形資産", 410), bs_fact("流動負債合計", 264), bs_fact("資産合計", 3000)],
        "PL": [pl_fact("売上高", 5000), pl_fact("法人税等", 410), pl_fact("当期純利益", 1500)],
        "CF": [pl_fact("税引前利益", 1980), pl_fact("営業活動によるキャッシュ・フロー", 1800),
               pl_fact("現金及び現金同等物の増減額", 686), balance, pl_fact("現金及び現金同等物の期末残高", 950)],
    }
    learned = fst.learn_pair(pdf, x)
    assert set(learned.maps["PL"]) == {"売上高", "当期純利益"}
    cf_maps = {k: v.key for k, v in learned.maps["CF"].items()}
    assert cf_maps == {
        "税引前利益": "IncomeBeforeIncomeTaxes", "営業活動によるキャッシュ・フロー": "NetCashProvidedByUsedInOperatingActivities",
        "現金及び現金同等物の増減額": "NetIncreaseDecreaseInCashAndCashEquivalents",
        "現金及び現金同等物の期末残高": "CashAndCashEquivalentsIFRS",
    }
    assert learned.extras["CF"] == {"IncomeBeforeIncomeTaxes": None,
                                    "CashAndCashEquivalentsIFRS": "NetIncreaseDecreaseInCashAndCashEquivalents"}
    assert learned.maps["BS"]["流動負債合計"].key == "CurrentLiabilities"


def test_cf_rows_stay_on_their_side_of_the_subtotal_when_the_newest_period_has_none() -> None:
    def column(end: date, order: list[str]) -> fst.Column:
        f = fst.Filing(id=end.toordinal(), doc_id=str(end), source="edinet", period_type="FY", period_end=end,
                       submitted=str(end), standard="JPGAAP")
        return fst.Column(f, {k: 1.0 for k in order}, order, {k: "edinet" for k in order})

    old = column(date(2024, 3, 31), ["IncomeBeforeIncomeTaxes", "OtherNetOpeCF", "SubtotalOpeCF", "IncomeTaxesPaidOpeCF",
                                     "NetCashProvidedByUsedInOperatingActivities"])
    new = column(date(2024, 9, 30), ["IncomeBeforeIncomeTaxes", "IncomeTaxesPaidOpeCF", "OtherNetOpeCF",
                                     "NetCashProvidedByUsedInOperatingActivities"])
    assert fst._row_keys([old, new], "CF") == [
        "IncomeBeforeIncomeTaxes", "OtherNetOpeCF", "SubtotalOpeCF", "IncomeTaxesPaidOpeCF",
        "NetCashProvidedByUsedInOperatingActivities",
    ]


def test_cf_sections_keep_their_order_when_periods_lack_different_sections() -> None:
    def column(end: date, order: list[str]) -> fst.Column:
        f = fst.Filing(id=end.toordinal(), doc_id=str(end), source="edinet", period_type="FY", period_end=end,
                       submitted=str(end), standard="JPGAAP")
        return fst.Column(f, {k: 1.0 for k in order}, order, {k: "edinet" for k in order})

    op = ["IncomeBeforeIncomeTaxes", "NetCashProvidedByUsedInOperatingActivities"]
    without_inv = column(date(2026, 3, 31), [*op, "CashDividendsPaidFinCF", "NetCashProvidedByUsedInFinancingActivities",
                                             "NetIncreaseDecreaseInCashAndCashEquivalents"])
    without_fin = column(date(2026, 9, 30), [*op, "PurchaseOfPropertyPlantAndEquipmentInvCF",
                                             "NetCashProvidedByUsedInInvestmentActivities",
                                             "NetIncreaseDecreaseInCashAndCashEquivalents"])
    assert fst._row_keys([without_inv, without_fin], "CF") == [
        *op, "PurchaseOfPropertyPlantAndEquipmentInvCF", "NetCashProvidedByUsedInInvestmentActivities",
        "CashDividendsPaidFinCF", "NetCashProvidedByUsedInFinancingActivities", "NetIncreaseDecreaseInCashAndCashEquivalents",
    ]
def test_cf_rows_do_not_take_items_of_other_statements_when_the_paired_filing_has_no_cf() -> None:
    pl = [xbrl_fact(1, "NetSales", 5000), xbrl_fact(2, "InterestExpensesNOE", 77.681),
          xbrl_fact(3, "LossOnRetirementOfNoncurrentAssetsEL", 35.333),
          xbrl_fact(4, "ProvisionOfAllowanceForDoubtfulAccountsSGA", 40.951),
          xbrl_fact(5, "ForeignCurrencyTranslationAdjustmentNetOfTaxOCI", -487.974),
          xbrl_fact(6, "ShareBasedPaymentTransactionsSSIFRS", 102.13), xbrl_fact(7, "IncomeBeforeIncomeTaxes", 1098.817)]
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="Q1", period_end=date(2024, 6, 30),
                   submitted="2024-08-10", standard="JPGAAP")
    x.by_statement = {"PL": pl}
    x.seq = list(pl)
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="Q1", period_end=date(2024, 6, 30),
                     submitted="2024-08-05", standard="JPGAAP")
    pdf.by_statement = {"CF": [pl_fact("税金等調整前四半期純利益", 1098.817), pl_fact("支払利息", 77.681),
                               pl_fact("固定資産除却損", 35.333), pl_fact("貸倒引当金の増減額", 40.951),
                               pl_fact("その他の流動負債の増減額", -487.974), pl_fact("株式報酬費用", 102.13)]}
    learned = fst.learn_pair(pdf, x)
    assert learned.extras["CF"] == {"IncomeBeforeIncomeTaxes": None}


def test_bs_rows_do_not_take_income_statement_items() -> None:
    bs = [xbrl_fact(1, "CashAndDeposits", 943, instant=True), xbrl_fact(2, "CurrentAssets", 3430, instant=True),
          xbrl_fact(3, "Assets", 5587, instant=True)]
    pl = [xbrl_fact(4, "NetSales", 30000), xbrl_fact(5, "IncomeTaxesDeferred", -23.693)]
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2024, 3, 31),
                   submitted="2024-06-20", standard="JPGAAP")
    x.by_statement = {"BS": bs, "PL": pl}
    x.seq = [*bs, *pl]
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2024, 3, 31),
                     submitted="2024-05-10", standard="JPGAAP")
    pdf.by_statement = {"BS": [bs_fact("現金及び預金", 943), bs_fact("流動資産合計", 3430),
                               bs_fact("繰延税金資産", 23.693), bs_fact("資産合計", 5587)]}
    learned = fst.learn_pair(pdf, x)
    assert {k: v.key for k, v in learned.maps["BS"].items()} == {
        "現金及び預金": "CashAndDeposits", "流動資産合計": "CurrentAssets", "資産合計": "Assets",
    }
    assert learned.extras["BS"] == {}
    cf = [xbrl_fact(6, "CurrentPortionOfLongTermLoansPayable", 35.882, instant=True),
          xbrl_fact(7, "CurrentLiabilities", 100, instant=True), xbrl_fact(8, "RepaymentOfLongTermLoansPayableFinCF", -35.548)]
    x.by_statement = {"BS": cf[:2], "CF": cf[2:]}
    x.seq = list(cf)
    pdf.by_statement = {"BS": [bs_fact("1年内返済予定の長期借入金", 35.548), bs_fact("流動負債合計", 100)]}
    learned = fst.learn_pair(pdf, x)
    assert {k: v.key for k, v in learned.maps["BS"].items()} == {"流動負債合計": "CurrentLiabilities"}
    assert learned.extras["BS"] == {}


def _declared_bs(facts: list[fst.Fact]) -> dict[str, dict[str, fst.Part]]:
    return {"BS": {f.key: fst.Part("Liabilities", "CurrentLiabilities", False) for f in facts}}


def test_bs_rows_do_not_take_facts_outside_the_declared_balance_sheet() -> None:
    bs = [xbrl_fact(1, "AccountsPayableOther", 1453, instant=True), xbrl_fact(2, "OtherCL", 1708, instant=True),
          xbrl_fact(3, "CurrentLiabilities", 3161, instant=True)]
    note = xbrl_fact(4, "NotesReceivableTrade", 1462, instant=True)
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2023, 12, 31),
                   submitted="2024-03-28", standard="JPGAAP")
    x.by_statement = {"BS": bs}
    x.seq = [*bs, note]
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2023, 12, 31),
                     submitted="2024-02-09", standard="JPGAAP")
    pdf.by_statement = {"BS": [bs_fact("未払金", 1453), bs_fact("その他", 1462), bs_fact("流動負債合計", 3161)]}
    assert fst.learn_pair(pdf, x).extras["BS"] == {"NotesReceivableTrade": "AccountsPayableOther"}
    x.parts = _declared_bs(bs)
    learned = fst.learn_pair(pdf, x)
    assert learned.extras["BS"] == {}
    assert "その他" not in learned.maps["BS"]
    later = fst.Filing(id=3, doc_id="X2", source="edinet", period_type="FY", period_end=date(2024, 12, 31),
                       submitted="2025-03-28", standard="JPGAAP")
    later.by_statement = {"BS": list(bs)}
    later.seq = [*bs, note]
    old = fst.Learned(period_end=date(2023, 12, 31), cls="FY", standard="JPGAAP",
                      extras={"BS": {"NotesReceivableTrade": "AccountsPayableOther"}})
    assert [f.key for f in fst._with_extras(later, "BS", [old])] == [
        "AccountsPayableOther", "NotesReceivableTrade", "OtherCL", "CurrentLiabilities"]
    later.parts = _declared_bs(bs)
    assert [f.key for f in fst._with_extras(later, "BS", [old])] == ["AccountsPayableOther", "OtherCL", "CurrentLiabilities"]


def test_pl_rows_do_not_take_cash_flow_items() -> None:
    bs = [xbrl_fact(1, "CashAndDeposits", 950, instant=True), xbrl_fact(2, "Assets", 3000, instant=True)]
    pl = [xbrl_fact(3, "NetSales", 5000), xbrl_fact(4, "IncomeBeforeIncomeTaxes", 1062), xbrl_fact(5, "ProfitLoss", 912)]
    cf = [xbrl_fact(6, "DepreciationAndAmortizationOpeCF", 787),
          xbrl_fact(7, "NetCashProvidedByUsedInOperatingActivities", 529), xbrl_fact(9, "OperatingProfitLossIFRS", 333)]
    notes = [xbrl_fact(8, "ext:AircraftFuelCost", 1085)]
    x = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2024, 3, 31),
                   submitted="2024-06-20", standard="JPGAAP")
    x.by_statement = {"BS": bs, "PL": pl, "CF": cf}
    x.seq = [*bs, *pl, *cf, *notes]
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2024, 3, 31),
                     submitted="2024-04-30", standard="JPGAAP")
    pdf.by_statement = {
        "PL": [pl_fact("売上高", 5000), pl_fact("税引前当期純利益", 1062), pl_fact("当期純利益", 912),
               pl_fact("燃油費及び燃料税", 1085), pl_fact("その他", 787), pl_fact("賃借料", 529), pl_fact("営業利益", 333)],
        "CF": [pl_fact("税引前当期純利益", 1062), pl_fact("減価償却費", 787),
               pl_fact("営業活動によるキャッシュ・フロー", 529)],
    }
    learned = fst.learn_pair(pdf, x)
    assert {k: v.key for k, v in learned.maps["PL"].items()} == {
        "売上高": "NetSales", "税引前当期純利益": "IncomeBeforeIncomeTaxes", "当期純利益": "ProfitLoss",
        "燃油費及び燃料税": "ext:AircraftFuelCost", "営業利益": "OperatingProfitLossIFRS",
    }
    assert learned.extras["PL"] == {"ext:AircraftFuelCost": "ProfitLoss", "OperatingProfitLossIFRS": "ext:AircraftFuelCost"}
    assert learned.extras["CF"] == {"IncomeBeforeIncomeTaxes": None}


def section_names(*extra: fst.Fact) -> fst.Names:
    xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-20", standard="JPGAAP")
    xbrl.by_statement = {"BS": [
        xbrl_fact(1, "CashAndDeposits", 100, "現金及び預金", True),
        xbrl_fact(2, "AllowanceForDoubtfulAccountsCA", -1, "貸倒引当金", True),
        xbrl_fact(3, "OtherCA", 5, "その他", True),
        xbrl_fact(4, "CurrentAssets", 104, "流動資産合計", True),
        xbrl_fact(5, "InvestmentSecurities", 50, "投資有価証券", True),
        xbrl_fact(6, "AllowanceForDoubtfulAccountsIOAByGroup", -2, "貸倒引当金", True),
        xbrl_fact(7, "InvestmentsAndOtherAssets", 48, "投資その他の資産合計", True),
        xbrl_fact(8, "Assets", 152, "資産合計", True),
        *extra,
    ]}
    return fst.name_index([xbrl], "BS")


TOTALS = fst.Learned(
    period_end=date(2025, 3, 31), cls="FY",
    maps={"BS": {"流動資産合計": fst.Mapped("CurrentAssets", 1), "投資その他の資産合計": fst.Mapped("InvestmentsAndOtherAssets", 1),
                 "資産合計": fst.Mapped("Assets", 1)}},
    order={"BS": ["CurrentAssets", "InvestmentsAndOtherAssets", "Assets"]},
)


def bs_rows(*, second_ca: bool = False) -> list[fst.Fact]:
    rows = [bs_fact("現金", 90), bs_fact("貸倒引当金", -1)]
    if second_ca:
        rows.append(bs_fact("貸倒引当金#2", -3))
    rows += [bs_fact("その他", 4), bs_fact("流動資産合計", 90), bs_fact("投資有価証券", 40),
             bs_fact(f"貸倒引当金#{3 if second_ca else 2}", -2), bs_fact("投資その他の資産合計", 38),
             bs_fact("資産合計", 128)]
    return rows


def test_name_index_keeps_labels_within_sections() -> None:
    names = section_names()
    assert "貸倒引当金" not in names.keys
    assert names.by_section[("CurrentAssets", "貸倒引当金")] == "AllowanceForDoubtfulAccountsCA"
    assert names.by_section[("InvestmentsAndOtherAssets", "貸倒引当金")] == "AllowanceForDoubtfulAccountsIOAByGroup"
    assert names.by_section[("CurrentAssets", "その他")] == "OtherCA"


def test_name_match_within_sections() -> None:
    got = keys_of(fst.apply_learned(bs_rows(), [TOTALS], "BS", date(2025, 6, 30), "Q", section_names()))
    assert got == [None, "AllowanceForDoubtfulAccountsCA", "OtherCA", "CurrentAssets", "InvestmentSecurities",
                   "AllowanceForDoubtfulAccountsIOAByGroup", "InvestmentsAndOtherAssets", "Assets"]
    assert all(m.by == "name" for _, m in fst.apply_learned(bs_rows(), [TOTALS], "BS", date(2025, 6, 30), "Q",
                                                             section_names())[1:3])


def test_name_match_within_sections_needs_one_row_and_a_section() -> None:
    got = keys_of(fst.apply_learned(bs_rows(second_ca=True), [TOTALS], "BS", date(2025, 6, 30), "Q", section_names()))
    assert got[1] is None and got[2] is None and got[6] == "AllowanceForDoubtfulAccountsIOAByGroup"
    no_totals = fst.Learned(period_end=date(2025, 3, 31), cls="FY", maps={"BS": {"資産合計": fst.Mapped("Assets", 1)}},
                            order={"BS": ["Assets"]})
    got = keys_of(fst.apply_learned(bs_rows(), [no_totals], "BS", date(2025, 6, 30), "Q", section_names()))
    assert got[1] is None and got[5] is None


def test_generic_others_are_not_matched_by_name_within_sections() -> None:
    rows = [bs_fact("現金", 90), bs_fact("その他", 4), bs_fact("流動資産合計", 90), bs_fact("投資有価証券", 40),
            bs_fact("その他#2", 7), bs_fact("投資その他の資産合計", 38), bs_fact("資産合計", 128)]
    names = section_names(xbrl_fact(9, "OtherIOA", 7, "その他", True))
    assert names.by_section[("CurrentAssets", "その他")] == "OtherCA"
    got = keys_of(fst.apply_learned(rows, [TOTALS], "BS", date(2025, 6, 30), "Q", names))
    assert got[1] is None and got[4] is None


def test_two_elements_with_one_label_in_a_section_are_not_matched_by_name() -> None:
    names = section_names(xbrl_fact(9, "AllowanceForDoubtfulAccountsInvestmentsIOA", -1, "貸倒引当金", True))
    assert ("InvestmentsAndOtherAssets", "貸倒引当金") in names.by_section
    xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-20", standard="JPGAAP")
    xbrl.by_statement = {"BS": [
        xbrl_fact(1, "AllowanceForDoubtfulAccountsCA", -1, "貸倒引当金", True),
        xbrl_fact(2, "AllowanceForDoubtfulAccountsOtherCA", -1, "貸倒引当金", True),
        xbrl_fact(3, "CurrentAssets", 104, "流動資産合計", True),
    ]}
    assert ("CurrentAssets", "貸倒引当金") not in fst.name_index([xbrl], "BS").by_section


PL_X = [
    xbrl_fact(1, "OperatingIncome", 100), xbrl_fact(2, "InterestIncomeNOI", 3), xbrl_fact(3, "OtherNOI", 4),
    xbrl_fact(4, "NonOperatingIncome", 7), xbrl_fact(5, "InterestExpensesNOE", 2), xbrl_fact(6, "OtherNOE", 5),
    xbrl_fact(7, "NonOperatingExpenses", 7), xbrl_fact(8, "OrdinaryIncome", 100),
    xbrl_fact(9, "GainOnSalesOfNoncurrentAssetsEI", 6), xbrl_fact(10, "OtherEI", 8),
    xbrl_fact(11, "ExtraordinaryIncome", 14), xbrl_fact(12, "ImpairmentLossEL", 11), xbrl_fact(13, "OtherEL", 9),
    xbrl_fact(14, "ExtraordinaryLoss", 20), xbrl_fact(15, "ProfitLoss", 94),
]
PL_P = [
    pl_fact("営業利益", 100), pl_fact("受取利息", 3), pl_fact("その他", 4), pl_fact("営業外収益合計", 7),
    pl_fact("支払利息", 2), pl_fact("その他#2", 5), pl_fact("営業外費用合計", 7), pl_fact("経常利益", 100),
    pl_fact("固定資産売却益", 6), pl_fact("その他#3", 8), pl_fact("特別利益合計", 14), pl_fact("減損損失", 11),
    pl_fact("その他#4", 9), pl_fact("特別損失合計", 20), pl_fact("当期純利益", 94),
]


def test_value_pairs_for_others_are_kept_by_section_in_pl() -> None:
    learned = pair("PL", PL_X, PL_P)
    assert learned.by_section["PL"][("NonOperatingIncome", "その他")].key == "OtherNOI"
    assert learned.by_section["PL"][("ExtraordinaryLoss", "その他")].key == "OtherEL"
    rows = [
        pl_fact("営業利益", 30), pl_fact("受取利息", 1), pl_fact("その他", 2), pl_fact("営業外収益合計", 3),
        pl_fact("支払利息", 1), pl_fact("営業外費用合計", 1), pl_fact("経常利益", 32), pl_fact("減損損失", 4),
        pl_fact("その他#2", 5), pl_fact("特別損失合計", 9), pl_fact("当期純利益", 23),
    ]
    got = keys_of(fst.apply_learned(rows, [learned], "PL", date(2025, 6, 30), "Q"))
    assert got[2] == "OtherNOI" and got[8] == "OtherEL"
    assert None not in got


def test_value_pairs_for_others_are_kept_by_section_in_cf() -> None:
    learned = pair("CF", [
        xbrl_fact(1, "DepreciationAndAmortizationOpeCF", 10), xbrl_fact(2, "OtherNetOpeCF", 3),
        xbrl_fact(3, "NetCashProvidedByUsedInOperatingActivities", 13), xbrl_fact(4, "PurchaseOfPropertyPlantAndEquipmentInvCF", -8),
        xbrl_fact(5, "OtherNetInvCF", -1), xbrl_fact(6, "NetCashProvidedByUsedInInvestmentActivities", -9),
        xbrl_fact(7, "CashDividendsPaidFinCF", -4), xbrl_fact(8, "OtherNetFinCF", -2),
        xbrl_fact(9, "NetCashProvidedByUsedInFinancingActivities", -6),
    ], [
        pl_fact("減価償却費", 10), pl_fact("その他", 3), pl_fact("営業活動によるキャッシュ・フロー", 13),
        pl_fact("有形固定資産の取得による支出", -8), pl_fact("その他#2", -1), pl_fact("投資活動によるキャッシュ・フロー", -9),
        pl_fact("配当金の支払額", -4), pl_fact("その他#3", -2), pl_fact("財務活動によるキャッシュ・フロー", -6),
    ])
    rows = [
        pl_fact("減価償却費", 5), pl_fact("その他", 1), pl_fact("営業活動によるキャッシュ・フロー", 6),
        pl_fact("有形固定資産の取得による支出", -3), pl_fact("投資活動によるキャッシュ・フロー", -3),
        pl_fact("配当金の支払額", -4), pl_fact("その他#2", -7), pl_fact("財務活動によるキャッシュ・フロー", -11),
    ]
    got = keys_of(fst.apply_learned(rows, [learned], "CF", date(2025, 6, 30), "Q"))
    assert got[1] == "OtherNetOpeCF" and got[6] == "OtherNetFinCF"
    assert None not in got


def test_pdf_only_period_drops_numbered_others_without_sections() -> None:
    learned = fst.Learned(
        period_end=date(2025, 3, 31), cls="FY",
        maps={"BS": {"その他": fst.Mapped("OtherCA", 1), "その他#2": fst.Mapped("OtherPPE", 1),
                     "のれん": fst.Mapped("Goodwill", 1), "その他#3": fst.Mapped("OtherIA", 1),
                     "短期借入金": fst.Mapped("ShortTermLoansPayable", 1), "その他#4": fst.Mapped("OtherCL", 1)}},
        order={"BS": ["OtherCA", "OtherPPE", "Goodwill", "OtherIA", "ShortTermLoansPayable", "OtherCL"]},
    )
    rows = [bs_fact("その他", 5), bs_fact("のれん", 3), bs_fact("その他#2", 11), bs_fact("短期借入金", 33),
            bs_fact("その他#3", 6)]
    got = keys_of(fst.apply_learned(rows, [learned], "BS", date(2025, 6, 30), "Q"))
    assert got == ["OtherCA", "Goodwill", None, "ShortTermLoansPayable", None]


def test_order_check_follows_the_same_kind_of_period() -> None:
    names = {"未収入金": "AccountsReceivableOther", "棚卸資産": "OtherInventories", "賞与引当金": "ProvisionForBonuses",
             "工事損失引当金": "ProvisionForLossOnConstructionContracts"}
    maps = {"BS": {k: fst.Mapped(v, 1) for k, v in names.items()}}
    fy = fst.Learned(period_end=date(2026, 3, 31), cls="FY", maps=maps, order={"BS": [
        "OtherInventories", "AccountsReceivableOther", "ProvisionForLossOnConstructionContracts", "ProvisionForBonuses",
    ]})
    q = fst.Learned(period_end=date(2025, 9, 30), cls="Q", maps=maps, order={"BS": [
        "AccountsReceivableOther", "OtherInventories", "ProvisionForBonuses", "ProvisionForLossOnConstructionContracts",
    ]})
    rows = [bs_fact("未収入金", 6), bs_fact("棚卸資産", 2), bs_fact("賞与引当金", 4), bs_fact("工事損失引当金", 1)]
    assert None not in keys_of(fst.apply_learned(rows, [fy, q], "BS", date(2026, 6, 30), "Q"))
    assert keys_of(fst.apply_learned(rows, [fy], "BS", date(2026, 6, 30), "Q")).count(None) == 2
    fy_rows = [bs_fact("棚卸資産", 2), bs_fact("未収入金", 6), bs_fact("工事損失引当金", 1), bs_fact("賞与引当金", 4)]
    assert None not in keys_of(fst.apply_learned(fy_rows, [fy, q], "BS", date(2027, 3, 31), "FY"))


def test_signed_profit_labels_give_the_loss_wording() -> None:
    for label, profit in [
        ("営業利益又は営業損失（△）", "営業利益"),
        ("親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）", "親会社株主に帰属する当期純利益"),
        ("附帯事業営業利益又は営業損失（△）", "附帯事業営業利益"),
        ("当期利益（△損失）", "当期利益"),
        ("非継続事業からの当期利益（△は損失）", "非継続事業からの当期利益"),
    ]:
        assert fst._profit_name(label) == profit
    for label in [
        "持分法による投資利益", "持分法による投資損失", "持分法による投資損益（△は損失）", "為替差益", "為替差損益（△は損失）",
        "固定資産除却損", "減損損失", "法人税等の支払額又は還付額（△は支払）", "利益剰余金", "営業利益",
    ]:
        assert fst._profit_name(label) is None
    assert fst._loss_name("税金等調整前四半期純利益") == "税金等調整前四半期純損失"
    assert fst._loss_name("当期利益#2") is None and fst._loss_name("営業損失") is None


LOSS_X = [
    xbrl_fact(1, "NetSales", 500, "売上高"), xbrl_fact(2, "OperatingIncome", 40, "営業利益又は営業損失（△）"),
    xbrl_fact(3, "ForeignExchangeGainsNOI", 3, "為替差益"), xbrl_fact(4, "EquityInEarningsOfAffiliatesNOI", 6, "持分法による投資利益"),
    xbrl_fact(5, "OrdinaryIncome", 49, "経常利益又は経常損失（△）"),
    xbrl_fact(6, "LossOnRetirementOfNoncurrentAssetsEL", 7, "固定資産除却損"),
    xbrl_fact(7, "ImpairmentLossEL", 9, "減損損失"),
    xbrl_fact(8, "ProfitLossAttributableToOwnersOfParent", 33,
              "親会社株主に帰属する当期純利益又は親会社株主に帰属する当期純損失（△）"),
]
LOSS_P = [
    pl_fact("売上高", 500), pl_fact("営業利益", 40), pl_fact("為替差益", 3), pl_fact("持分法による投資利益", 6),
    pl_fact("経常利益", 49), pl_fact("固定資産除却損", 7), pl_fact("減損損失", 9),
    pl_fact("親会社株主に帰属する四半期純利益", 33),
]


def loss_keys(rows: list[fst.Fact], learned: list[fst.Learned], names: fst.Names | None = None) -> list[str | None]:
    return keys_of(fst.apply_learned(rows, learned, "PL", date(2025, 9, 30), "Q", names))


def test_loss_wording_takes_the_learned_signed_profit() -> None:
    learned = pair("PL", LOSS_X, LOSS_P, date(2025, 6, 30), "Q")
    assert learned.losses["PL"] == {
        "営業損失": fst.Mapped("OperatingIncome", 1), "経常損失": fst.Mapped("OrdinaryIncome", 1),
        "親会社株主に帰属する四半期純損失": fst.Mapped("ProfitLossAttributableToOwnersOfParent", 1),
        "親会社株主に帰属する当期純損失": fst.Mapped("ProfitLossAttributableToOwnersOfParent", 1),
    }
    rows = [
        pl_fact("売上高", 300), pl_fact("営業損失", -20), pl_fact("為替差損", 2), pl_fact("持分法による投資損失", 4),
        pl_fact("経常損失", -26), pl_fact("固定資産除却損", 1), pl_fact("減損損失", 3),
        pl_fact("親会社株主に帰属する四半期純損失", -30),
    ]
    got = fst.apply_learned(rows, [learned], "PL", date(2025, 9, 30), "Q")
    assert keys_of(got) == [
        "NetSales", "OperatingIncome", None, None, "OrdinaryIncome", "LossOnRetirementOfNoncurrentAssetsEL",
        "ImpairmentLossEL", "ProfitLossAttributableToOwnersOfParent",
    ]
    assert all(m is None or m.sign == 1 for _, m in got)


def test_loss_wording_keeps_rows_matched_before() -> None:
    learned = pair("PL", LOSS_X, LOSS_P, date(2025, 6, 30), "Q")
    rows = [pl_fact("売上高", 300), pl_fact("営業利益", 20), pl_fact("営業損失", -20), pl_fact("経常利益", 26)]
    assert loss_keys(rows, [learned]) == ["NetSales", "OperatingIncome", None, "OrdinaryIncome"]
    assert loss_keys([pl_fact("売上高", 300), pl_fact("営業損失", 20)], [learned]) == ["NetSales", None]
    assert loss_keys([pl_fact("営業損失", -20), pl_fact("営業損失#2", -20)], [learned]) == [None, None]
    unsigned = [replace(f, label="営業利益") if f.key == "OperatingIncome" else f for f in LOSS_X]
    plain = pair("PL", unsigned, LOSS_P, date(2025, 6, 30), "Q")
    assert "営業損失" not in plain.losses["PL"]
    assert loss_keys([pl_fact("売上高", 300), pl_fact("営業損失", -20)], [plain]) == ["NetSales", None]


def test_loss_wording_of_ifrs_labels() -> None:
    learned = pair("PL", [
        xbrl_fact(1, "RevenueIFRS", 500, "売上収益"), xbrl_fact(2, "OperatingProfitLossIFRS", 40, "営業利益（△損失）"),
        xbrl_fact(3, "ProfitLossIFRS", 30, "当期利益（△損失）"),
    ], [pl_fact("売上収益", 500), pl_fact("営業利益", 40), pl_fact("当期利益", 30)], date(2025, 6, 30), "Q")
    rows = [pl_fact("売上収益", 300), pl_fact("営業損失", -20), pl_fact("当期損失", -25)]
    assert loss_keys(rows, [learned]) == ["RevenueIFRS", "OperatingProfitLossIFRS", "ProfitLossIFRS"]


def test_loss_wording_is_matched_by_signed_label() -> None:
    xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-20", standard="JPGAAP")
    xbrl.by_statement = {"PL": list(LOSS_X)}
    names = fst.name_index([xbrl], "PL")
    assert names.losses == {
        "営業損失": "OperatingIncome", "経常損失": "OrdinaryIncome",
        "親会社株主に帰属する当期純損失": "ProfitLossAttributableToOwnersOfParent",
    }
    rows = [pl_fact("売上高", 300), pl_fact("営業損失", -20), pl_fact("持分法による投資損失", -4), pl_fact("経常損失", 26),
            pl_fact("親会社株主に帰属する当期純損失", 0)]
    got = fst.apply_learned(rows, [], "PL", date(2025, 9, 30), "Q", names)
    assert keys_of(got) == ["NetSales", "OperatingIncome", None, None, "ProfitLossAttributableToOwnersOfParent"]
    assert got[1][1] == fst.Mapped("OperatingIncome", 1, "name")


def xbrl_names(statement: str, facts: list[fst.Fact]) -> fst.Names:
    xbrl = fst.Filing(id=9, doc_id="N", source="edinet", period_type="FY", period_end=date(2026, 3, 31),
                      submitted="2026-06-20", standard="JPGAAP")
    xbrl.by_statement = {statement: facts}
    return fst.name_index([xbrl], statement)


LIAB_X = [
    xbrl_fact(1, "ShortTermLoansPayable", 100, "短期借入金", True), xbrl_fact(2, "CurrentLiabilities", 150, "流動負債合計", True),
    xbrl_fact(3, "LongTermLoansPayable", 200, "長期借入金", True),
    xbrl_fact(4, "AssetRetirementObligationsNCL", 78, "資産除去債務", True),
    xbrl_fact(5, "NoncurrentLiabilities", 278, "固定負債合計", True), xbrl_fact(6, "Liabilities", 428, "負債合計", True),
]
LIAB_P = [
    bs_fact("短期借入金", 100), bs_fact("流動負債合計", 150), bs_fact("長期借入金", 200), bs_fact("資産除去債務", 78),
    bs_fact("固定負債合計", 278), bs_fact("負債合計", 428),
]
LIAB_NAMES = [
    xbrl_fact(11, "ShortTermLoansPayable", 90, "短期借入金", True),
    xbrl_fact(12, "AssetRetirementObligationsCL", 70, "資産除去債務", True),
    xbrl_fact(13, "CurrentLiabilities", 160, "流動負債合計", True),
    xbrl_fact(14, "LongTermLoansPayable", 210, "長期借入金", True),
    xbrl_fact(15, "NoncurrentLiabilities", 210, "固定負債合計", True), xbrl_fact(16, "Liabilities", 370, "負債合計", True),
]


def liab_rows(*extra: fst.Fact, name: str = "資産除去債務") -> list[fst.Fact]:
    return [bs_fact("短期借入金", 90), *extra, bs_fact(name, 70), bs_fact("流動負債合計", 160), bs_fact("長期借入金", 210),
            bs_fact("固定負債合計", 210), bs_fact("負債合計", 370)]


def test_row_dropped_by_order_is_matched_by_name_within_its_section() -> None:
    learned = pair("BS", LIAB_X, LIAB_P)
    names = xbrl_names("BS", LIAB_NAMES)
    assert names.by_section[("CurrentLiabilities", "資産除去債務")] == "AssetRetirementObligationsCL"
    assert keys_of(fst.apply_learned(liab_rows(), [learned], "BS", date(2025, 6, 30), "Q"))[1] is None
    got = fst.apply_learned(liab_rows(), [learned], "BS", date(2025, 6, 30), "Q", names)
    assert got[1][1] == fst.Mapped("AssetRetirementObligationsCL", 1, "name")
    assert keys_of(got) == ["ShortTermLoansPayable", "AssetRetirementObligationsCL", "CurrentLiabilities",
                            "LongTermLoansPayable", "NoncurrentLiabilities", "Liabilities"]


def test_others_dropped_by_order_are_not_matched_by_name_within_the_section() -> None:
    x = [replace(f, label="その他") if f.key == "AssetRetirementObligationsNCL" else f for f in LIAB_X]
    p = [bs_fact("その他", 78) if f.local == "資産除去債務" else f for f in LIAB_P]
    names = xbrl_names("BS", [replace(f, key="OtherCL", local="OtherCL", label="その他")
                              if f.key == "AssetRetirementObligationsCL" else f for f in LIAB_NAMES])
    assert names.by_section[("CurrentLiabilities", "その他")] == "OtherCL"
    got = fst.apply_learned(liab_rows(name="その他"), [pair("BS", x, p)], "BS", date(2025, 6, 30), "Q", names)
    assert keys_of(got)[1] is None


def test_name_within_the_section_skips_elements_another_row_takes() -> None:
    x = [*LIAB_X[:1], xbrl_fact(7, "AssetRetirementObligationsCL", 7, "資産除去債務", True), *LIAB_X[1:]]
    p = [*LIAB_P[:1], bs_fact("除去債務引当金", 7), *LIAB_P[1:]]
    learned = pair("BS", x, p)
    rows = liab_rows(bs_fact("除去債務引当金", 6))
    got = keys_of(fst.apply_learned(rows, [learned], "BS", date(2025, 6, 30), "Q", xbrl_names("BS", LIAB_NAMES)))
    assert got[1] == "AssetRetirementObligationsCL" and got[2] is None


NOI_X = [
    xbrl_fact(1, "OperatingIncome", 100, "営業利益"), xbrl_fact(2, "InterestIncomeNOI", 3, "受取利息"),
    xbrl_fact(3, "ForeignExchangeGainsNOI", 5, "為替差益"),
    xbrl_fact(4, "EquityInEarningsOfAffiliatesNOI", 6, "持分法による投資利益"), xbrl_fact(5, "OtherNOI", 1, "その他"),
    xbrl_fact(6, "NonOperatingIncome", 15, "営業外収益"), xbrl_fact(7, "InterestExpensesNOE", 2, "支払利息"),
    xbrl_fact(8, "NonOperatingExpenses", 2, "営業外費用"), xbrl_fact(9, "OrdinaryIncome", 113, "経常利益"),
]
NOI_P = [
    pl_fact("営業利益", 100), pl_fact("受取利息", 3), pl_fact("為替差益", 5), pl_fact("持分法による投資利益", 6),
    pl_fact("その他", 1), pl_fact("営業外収益合計", 15), pl_fact("支払利息", 2), pl_fact("営業外費用合計", 2),
    pl_fact("経常利益", 113),
]


def noi_keys(rows: list[fst.Fact], names: fst.Names | None = None) -> list[str | None]:
    learned = pair("PL", NOI_X, NOI_P, date(2025, 6, 30), "Q")
    return keys_of(fst.apply_learned(rows, [learned], "PL", date(2024, 6, 30), "Q", names))


def test_value_pairs_reordered_within_their_section_are_kept() -> None:
    rows = [pl_fact("営業利益", 80), pl_fact("受取利息", 2), pl_fact("持分法による投資利益", 4), pl_fact("為替差益", 201),
            pl_fact("営業外収益合計", 207), pl_fact("支払利息", 1), pl_fact("営業外費用合計", 1), pl_fact("経常利益", 286)]
    assert noi_keys(rows) == ["OperatingIncome", "InterestIncomeNOI", "EquityInEarningsOfAffiliatesNOI",
                              "ForeignExchangeGainsNOI", "NonOperatingIncome", "InterestExpensesNOE",
                              "NonOperatingExpenses", "OrdinaryIncome"]
    others = [pl_fact("営業利益", 80), pl_fact("受取利息", 2), pl_fact("その他", 1), pl_fact("為替差益", 201),
              pl_fact("営業外収益合計", 204), pl_fact("経常利益", 284)]
    assert noi_keys(others)[2] == "OtherNOI"


def test_value_pairs_moved_to_another_section_stay_dropped() -> None:
    rows = [pl_fact("営業利益", 80), pl_fact("受取利息", 2), pl_fact("為替差益", 201), pl_fact("営業外収益合計", 203),
            pl_fact("支払利息", 1), pl_fact("持分法による投資利益", 4), pl_fact("営業外費用合計", 5), pl_fact("経常利益", 279)]
    names = xbrl_names("PL", NOI_X)
    assert noi_keys(rows)[5] is None
    assert noi_keys(rows, names)[5] is None
    assert None not in noi_keys(rows)[:5]


TOTAL_X = [
    xbrl_fact(1, "CashAndDeposits", 100, "現金及び預金", True),
    xbrl_fact(2, "AllowanceForDoubtfulAccountsCA", -1, "貸倒引当金", True),
    xbrl_fact(3, "CurrentAssets", 99, "流動資産", True),
    xbrl_fact(4, "InvestmentSecurities", 50, "投資有価証券", True),
    xbrl_fact(5, "AllowanceForDoubtfulAccountsIOAByGroup", -2, "貸倒引当金", True),
    xbrl_fact(6, "InvestmentsAndOtherAssets", 48, "投資その他の資産", True),
    xbrl_fact(7, "NoncurrentAssets", 48, "固定資産", True),
    xbrl_fact(8, "Assets", 147, "資産", True),
    xbrl_fact(9, "ShortTermLoansPayable", 40, "短期借入金", True),
    xbrl_fact(10, "Liabilities", 40, "負債", True),
    xbrl_fact(11, "CapitalStock", 100, "資本金", True),
    xbrl_fact(12, "NetAssets", 107, "純資産", True),
    xbrl_fact(13, "LiabilitiesAndNetAssets", 147, "負債純資産", True),
]


def total_rows(*, tail: str = "合計") -> list[fst.Fact]:
    return [
        bs_fact("現金及び預金", 90), bs_fact("貸倒引当金", -1), bs_fact(f"流動資産{tail}", 89), bs_fact("投資有価証券", 40),
        bs_fact("貸倒引当金#2", -2), bs_fact(f"投資その他の資産{tail}", 38), bs_fact(f"固定資産{tail}", 38),
        bs_fact(f"資産{tail}", 127), bs_fact("短期借入金", 30), bs_fact(f"負債{tail}", 30), bs_fact("資本金", 97),
        bs_fact(f"純資産{tail}", 97), bs_fact(f"負債純資産{tail}", 127),
    ]


def total_keys(rows: list[fst.Fact], learned: list[fst.Learned], facts: list[fst.Fact],
               statement: str = "BS") -> list[str | None]:
    return keys_of(fst.apply_learned(rows, learned, statement, date(2026, 6, 30), "Q", xbrl_names(statement, facts)))


def test_total_rows_are_matched_by_the_label_without_the_tail() -> None:
    got = fst.apply_learned(total_rows(), [], "BS", date(2026, 6, 30), "Q", xbrl_names("BS", TOTAL_X))
    assert keys_of(got) == [x.key for x in TOTAL_X]
    assert all(m.by == "name" and m.sign == 1 for _, m in got)
    assert total_keys(total_rows(tail="計"), [], TOTAL_X) == [x.key for x in TOTAL_X]


def test_total_rows_open_their_sections_for_the_other_rows() -> None:
    got = total_keys(total_rows(), [], TOTAL_X)
    assert got[1] == "AllowanceForDoubtfulAccountsCA" and got[4] == "AllowanceForDoubtfulAccountsIOAByGroup"
    no_totals = [replace(f, label=f"{f.label}の部") if f.key in ("CurrentAssets", "InvestmentsAndOtherAssets") else f
                 for f in TOTAL_X]
    got = total_keys(total_rows(), [], no_totals)
    assert got[1] is None and got[4] is None


def test_total_stem_needs_a_name_before_the_tail() -> None:
    assert fst._total_stem("資産合計") == "資産" and fst._total_stem("流動資産計") == "流動資産"
    assert fst._total_stem("負債純資産合計") == "負債純資産"
    for name in ("合計", "計", "小計", "累計", "会計", "設計", "統計", "減価償却累計額", "その他合計", "その他計", "資産"):
        assert fst._total_stem(name) is None


def test_words_ending_in_kei_are_not_totals() -> None:
    x = [xbrl_fact(1, "A", 5, "設", True), xbrl_fact(2, "B", 6, "会", True), xbrl_fact(3, "OtherCA", 7, "その他", True),
         xbrl_fact(4, "C", 8, "小", True), xbrl_fact(5, "Assets", 26, "資産", True)]
    rows = [bs_fact("設計", 5), bs_fact("会計", 6), bs_fact("その他合計", 7), bs_fact("小計", 8), bs_fact("合計", 26),
            bs_fact("計", 26)]
    assert total_keys(rows, [], x) == [None] * 6


def test_total_rows_need_one_element_with_the_label() -> None:
    x = [*TOTAL_X, xbrl_fact(20, "AssetsIFRS", 147, "資産", True)]
    assert total_keys(total_rows(), [], x)[7] is None
    assert total_keys([bs_fact("資産合計", 127), bs_fact("資産計", 127)], [], TOTAL_X) == [None, None]


def test_total_rows_keep_rows_matched_before() -> None:
    x = [xbrl_fact(1, "NetSales", 500, "売上高"), xbrl_fact(2, "CostOfSales", 300, "売上原価")]
    rows = [pl_fact("売上高", 200), pl_fact("売上高合計", 200), pl_fact("売上原価", 120)]
    assert total_keys(rows, [], x, "PL") == ["NetSales", None, "CostOfSales"]
    learned = pair("PL", x, [pl_fact("売上高合計", 500), pl_fact("売上原価", 300)])
    assert total_keys(rows, [learned], x, "PL") == [None, "NetSales", "CostOfSales"]


def test_total_rows_keep_exact_labels_apart() -> None:
    x = [replace(f, label="負債純資産合計") if f.key == "LiabilitiesAndNetAssets" else f for f in TOTAL_X]
    got = total_keys(total_rows(), [], x)
    assert got[7] == "Assets" and got[12] == "LiabilitiesAndNetAssets"


def test_total_rows_follow_the_standard_of_the_other_totals() -> None:
    ifrs = bs_learned({"流動資産合計": "CurrentAssetsIFRS", "非流動資産合計": "NonCurrentAssetsIFRS",
                       "負債合計": "LiabilitiesIFRS"},
                      ["CurrentAssetsIFRS", "NonCurrentAssetsIFRS", "LiabilitiesIFRS"])
    rows = [bs_fact("流動資産合計", 89), bs_fact("非流動資産合計", 38), bs_fact("資産合計", 127), bs_fact("負債合計", 30)]
    assert total_keys(rows, [ifrs], TOTAL_X) == ["CurrentAssetsIFRS", "NonCurrentAssetsIFRS", None, "LiabilitiesIFRS"]
    jp = [bs_fact("流動資産合計", 89), bs_fact("投資その他の資産合計", 38), bs_fact("固定資産合計", 38),
          bs_fact("資産合計", 127), bs_fact("負債合計", 30)]
    one = bs_learned({"流動資産合計": "CurrentAssetsIFRS"}, ["CurrentAssetsIFRS"])
    assert total_keys(jp, [one], TOTAL_X) == ["CurrentAssetsIFRS", "InvestmentsAndOtherAssets", "NoncurrentAssets",
                                              "Assets", "Liabilities"]
    tie = [bs_fact("流動資産合計", 89), bs_fact("資産合計", 127)]
    assert total_keys(tie, [one], TOTAL_X) == ["CurrentAssetsIFRS", None]


def test_total_rows_in_pl() -> None:
    labels = ["営業利益", "受取利息", "その他", "営業外収益", "支払利息", "その他", "営業外費用", "経常利益",
              "固定資産売却益", "その他", "特別利益", "減損損失", "その他", "特別損失", "法人税等", "当期純利益"]
    x = [*PL_X[:14], xbrl_fact(16, "IncomeTaxes", 30), xbrl_fact(15, "ProfitLoss", 64)]
    x = [replace(f, label=label) for f, label in zip(x, labels)]
    rows = [pl_fact("営業利益", 30), pl_fact("受取利息", 1), pl_fact("その他", 2), pl_fact("営業外収益合計", 3),
            pl_fact("支払利息", 1), pl_fact("営業外費用合計", 1), pl_fact("経常利益", 32), pl_fact("減損損失", 4),
            pl_fact("その他#2", 5), pl_fact("特別損失合計", 9), pl_fact("法人税等合計", 7), pl_fact("当期純利益", 16)]
    assert total_keys(rows, [], x, "PL") == [
        "OperatingIncome", "InterestIncomeNOI", None, "NonOperatingIncome", "InterestExpensesNOE", "NonOperatingExpenses",
        "OrdinaryIncome", "ImpairmentLossEL", None, "ExtraordinaryLoss", "IncomeTaxes", "ProfitLoss",
    ]


CUSTOM_NS = "jpcrp030000-asr_E00001-000"
SPLIT = "81110"
SWITCH = "81120"


def _split(s: Seeder) -> None:
    s.stock(SPLIT, "タグの付け替え")
    ends = ("2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31")
    for n, end in enumerate(ends):
        fid = 801 + n
        s.filing(fid, "edinet", "FY", end, f"{end[:4]}-06-20 15:00:00", code=SPLIT)
        receivable = "NotesAndAccountsReceivableTrade" if n < 2 else "AccountsReceivableTrade"
        facts = [x("CashAndDeposits", "i", 10 + n), x(receivable, "i", 20 + n)]
        if n < 3:
            facts.append(x("GuaranteeDeposits", "i", 30 + n))
        if n in (0, 3):
            facts.append(x("GuaranteeDeposits", "i", 40 + n, "jpcrp030000-asr_E00001-000"))
        s.facts(fid, end, facts)
    s.stock(SWITCH, "IFRS に移った")
    s.filing(811, "edinet", "FY", "2024-03-31", "2024-06-20 15:00:00", code=SWITCH)
    s.facts(811, "2024-03-31", [x("CashAndDeposits", "i", 10), x("Deposits", "i", 2, CUSTOM_NS), x("Land", "i", 7),
                                x("Goodwill", "i", 5), x("CapitalStock", "i", 3)])
    s.roles(811, [("rol_ConsolidatedBalanceSheet", "BS", True, False, [
        "AssetsAbstract", "CurrentAssetsAbstract", "CashAndDeposits", ("Deposits", None, CUSTOM_NS),
        "NoncurrentAssetsAbstract", "PropertyPlantAndEquipmentAbstract", "Land", "IntangibleAssetsAbstract", "Goodwill",
        "NetAssetsAbstract", "ShareholdersEquityAbstract", "CapitalStock",
    ])])
    s.filing(812, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=SWITCH, standard="IFRS")
    s.facts(812, "2025-03-31", [x("CashAndCashEquivalentsIFRS", "i", 12, "jpigp_cor"),
                                x("GoodwillIFRS", "i", 6, "jpigp_cor"), x("Deposits", "i", 4, CUSTOM_NS),
                                x("ShareCapitalIFRS", "i", 3, "jpigp_cor")])
    s.roles(812, [("rol_ConsolidatedStatementOfFinancialPositionIFRS", "BS", True, True, [
        "AssetsIFRSAbstract", "CurrentAssetsIFRSAbstract", "CashAndCashEquivalentsIFRS",
        "NonCurrentAssetsIFRSAbstract", "GoodwillIFRS", ("Deposits", None, CUSTOM_NS),
        "EquityIFRSAbstract", "ShareCapitalIFRS",
    ])])


ENDS = ("2022-03-31", "2023-03-31", "2024-03-31", "2025-03-31")


def column(end: str, values: dict[str, float], parts: dict[str, fst.Part] | None = None) -> fst.Column:
    filing = fst.Filing(id=0, doc_id="", source="edinet", period_type="FY", period_end=date.fromisoformat(end),
                        submitted="", standard="JPGAAP")
    return fst.Column(filing, dict(values), list(values), {k: "edinet" for k in values}, parts=dict(parts or {}))


def merged(seq: list[dict[str, float]], labels: dict[str, str], statement: str = "BS",
           units: dict | None = None, excluded: tuple = (),
           parts: dict[str, fst.Part] | None = None) -> tuple[dict[str, list[str]], list[str], list[fst.Column]]:
    cols = [column(end, values, {k: v for k, v in (parts or {}).items() if k in values})
            for end, values in zip(ENDS, seq)]
    got = fst.merge_split_rows(cols, statement, labels, units or {}, excluded)
    return got, fst._row_keys(cols, statement), cols


def test_split_tags_of_one_item_become_one_row_at_the_newest_place() -> None:
    labels = {"Cash": "現金及び預金", "NotesAR": "売掛金", "AR": "売掛金"}
    got, keys, cols = merged([{"Cash": 1, "NotesAR": 2}, {"Cash": 1, "NotesAR": 3}, {"AR": 4, "Cash": 1}], labels)
    assert got == {"AR": ["NotesAR"]}
    assert keys == ["AR", "Cash"]
    assert [c.values.get("AR") for c in cols] == [2, 3, 4]
    assert all("NotesAR" not in c.values and "NotesAR" not in c.sources for c in cols)


def test_split_rows_sharing_one_period_stay_apart() -> None:
    labels = {"NotesAR": "売掛金", "AR": "売掛金"}
    got, keys, _ = merged([{"NotesAR": 2}, {"NotesAR": 3, "AR": 3}, {"AR": 4}], labels)
    assert got == {} and set(keys) == {"NotesAR", "AR"}


def test_three_rows_merge_only_when_no_two_share_a_period() -> None:
    labels = {"A": "差入保証金", "B": "差入保証金", "C": "差入保証金"}
    got, keys, _ = merged([{"A": 1}, {"B": 2}, {"C": 3}], labels)
    assert got == {"C": ["B", "A"]} and keys == ["C"]
    got, keys, _ = merged([{"A": 1}, {"B": 2, "C": 2}, {"C": 3}], labels)
    assert got == {} and set(keys) == {"A", "B", "C"}


def test_generic_names_are_not_merged() -> None:
    for name in ("その他", "その他(純額)", "減価償却累計額", "減価償却累計額及び減損損失累計額", "減損損失累計額", "貸倒引当金"):
        got, _, _ = merged([{"A": 1}, {"B": 2}], {"A": name, "B": name})
        assert got == {}, name
    for name in ("その他有価証券評価差額金", "その他の固定負債", "その他の包括利益"):
        got, _, _ = merged([{"A": 1}, {"B": 2}], {"A": name, "B": name})
        assert got == {"B": ["A"]}, name


def test_bs_rows_in_other_sections_stay_apart() -> None:
    labels = {"A": "リース資産", "B": "リース資産"}
    seq = [{"A": 1}, {"B": 1}]
    got, _, _ = merged(seq, labels, parts={"A": CA, "B": NCA})
    assert got == {}
    got, _, _ = merged(seq, labels, parts={"A": NCA, "B": NCA})
    assert got == {"B": ["A"]}


def test_pl_rows_in_other_sections_stay_apart_within_one_standard() -> None:
    labels = {"GainNOI": "投資有価証券売却益", "GainEI": "投資有価証券売却益", "SubsidyNOI": "助成金収入",
              "ext:Subsidy": "助成金収入", "GainIFRS": "投資有価証券売却益", "SubsidyIFRS": "助成金収入"}
    noi = fst.Part(None, "NonOperatingIncome", False)
    ei = fst.Part(None, "ExtraordinaryIncome", False)
    parts = {"GainNOI": noi, "SubsidyNOI": noi, "ext:Subsidy": noi, "GainEI": ei,
             "GainIFRS": fst.Part(None, None, True), "SubsidyIFRS": fst.Part(None, None, True)}
    seq = [{"GainNOI": 1, "SubsidyNOI": 1}, {"ext:Subsidy": 2, "GainEI": 2}]
    got, keys, _ = merged(seq, labels, "PL", parts=parts)
    assert got == {"ext:Subsidy": ["SubsidyNOI"]}
    assert "GainNOI" in keys and "GainEI" in keys
    got, keys, _ = merged([*seq, {"GainIFRS": 3, "SubsidyIFRS": 3}], labels, "PL", parts=parts)
    assert got == {"ext:Subsidy": ["SubsidyNOI"]}
    assert {"GainNOI", "GainEI", "GainIFRS", "SubsidyIFRS"} <= set(keys)


def test_pdf_rows_and_other_units_are_not_merged() -> None:
    got, _, _ = merged([{"pdf:売掛金": 1}, {"AR": 2}], {"pdf:売掛金": "売掛金", "AR": "売掛金"})
    assert got == {}
    got, _, _ = merged([{"pdf:A": 1}, {"pdf:B": 2}], {"pdf:A": "売掛金", "pdf:B": "売掛金"})
    assert got == {}
    got, _, _ = merged([{"A": 1}, {"B": 2}], {"A": "1株当たり配当額", "B": "1株当たり配当額"}, "PL",
                       {"A": "JPYPerShares", "B": "JPY"})
    assert got == {}


def test_listed_pairs_are_not_merged() -> None:
    labels = {"A": "差入保証金", "B": "差入保証金", "C": "差入保証金"}
    got, _, _ = merged([{"A": 1}, {"B": 2}], labels, excluded=(frozenset({"A", "B"}),))
    assert got == {}
    got, _, _ = merged([{"A": 1}, {"B": 2}, {"C": 3}], labels, excluded=(frozenset({"A", "B"}),))
    assert got == {}
    assert isinstance(fst._merge_exclusions(), dict)


def test_table_merges_split_rows_over_all_periods(monkeypatch: pytest.MonkeyPatch) -> None:
    for periods in (2, 8):
        got = rows(table(SPLIT, periods=periods))
        assert "NotesAndAccountsReceivableTrade" not in got
        assert got["AccountsReceivableTrade"]["merged_keys"] == ["NotesAndAccountsReceivableTrade"]
        assert {"GuaranteeDeposits", "ext:GuaranteeDeposits"} <= set(got)
        assert got["GuaranteeDeposits"]["merged_keys"] == got["CashAndDeposits"]["merged_keys"] == []
    t = table(SPLIT)
    assert [c and c["value"] for c in rows(t)["AccountsReceivableTrade"]["cells"]] == [20 * M, 21 * M, 22 * M, 23 * M]
    assert [r["key"] for r in t["rows"]].index("AccountsReceivableTrade") == 1
    excluded = {(SPLIT, "BS"): {frozenset({"NotesAndAccountsReceivableTrade", "AccountsReceivableTrade"})}}
    monkeypatch.setattr(fst, "_merge_exclusions", lambda: excluded)
    assert {"NotesAndAccountsReceivableTrade", "AccountsReceivableTrade"} <= set(rows(table(SPLIT)))


def test_table_shows_declared_heads_and_lends_them_to_rows_of_older_filings() -> None:
    got = {k: (heads(r), r["indent"]) for k, r in rows(table(DECLARED)).items()}
    ppe = ("資産の部", "固定資産", "有形固定資産")
    assert got["CashAndDeposits"] == got["CurrentAssets"] == (("資産の部", "流動資産"), 0)
    assert got["OtherCA"] == (("資産の部", "流動資産"), 0)
    assert got["Buildings"] == got["Land"] == got["PropertyPlantAndEquipment"] == (ppe, 0)
    assert got["AccumulatedDepreciationBuildings"] == (ppe, 1)
    assert got["Assets"] == (("資産の部",), 0)
    assert got["LongTermPrepaidExpenses"] == (("資産", "固定資産"), 0)
    lent = rows(table(DECLARED))["OtherCA"]["heads"]
    assert [h["key"] for h in lent] == ["AssetsAbstract", "CurrentAssetsAbstract"]


def test_learn_pair_breaks_equal_value_ties_by_label() -> None:
    xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="Q1", period_end=date(2025, 6, 30),
                      submitted="2025-08-10", standard="JPGAAP")
    xbrl.by_statement = {"BS": [
        xbrl_fact(1, "ShortTermLoansPayable", 448, "短期借入金", True),
        xbrl_fact(2, "CurrentLiabilities", 448, "流動負債", True),
        xbrl_fact(3, "Liabilities", 448, "負債", True),
    ]}
    xbrl.seq = list(xbrl.by_statement["BS"])
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="Q1", period_end=date(2025, 6, 30),
                     submitted="2025-08-01", standard="JPGAAP")
    pdf.by_statement = {"BS": [bs_fact("短期借入金─", 448), bs_fact("負債合計", 448)]}
    learned = fst.learn_pair(pdf, xbrl)
    assert learned.maps["BS"] == {
        "短期借入金": fst.Mapped("ShortTermLoansPayable", 1),
        "負債合計": fst.Mapped("Liabilities", 1),
    }


def test_contention_falls_back_to_alternate_learned_element_and_total_tail() -> None:
    p1 = fst.Learned(
        period_end=date(2025, 3, 31), cls="FY",
        maps={"CF": {
            "現金及び現金同等物の期末残高": fst.Mapped("CashAndCashEquivalentsIFRS", 1),
            "現金及び現金同等物の四半期末残高(要約四半期連結財政状態計算書計上額)": fst.Mapped("CashAndCashEquivalentsIFRS", 1),
        }},
        order={"CF": ["CashAndCashEquivalentsIFRS"]},
    )
    p2 = fst.Learned(
        period_end=date(2024, 9, 30), cls="Q",
        maps={"CF": {
            "現金及び現金同等物の期末残高": fst.Mapped("CashAndCashEquivalentsIfDifferentFromBSBalanceIFRS", 1),
            "現金及び現金同等物の四半期末残高(要約四半期連結財政状態計算書計上額)": fst.Mapped("CashAndCashEquivalentsIFRS", 1),
        }},
        order={"CF": ["CashAndCashEquivalentsIfDifferentFromBSBalanceIFRS", "CashAndCashEquivalentsIFRS"]},
    )
    cf_rows = [
        fst.Fact(1, "pdf:現金及び現金同等物の期末残高", "現金及び現金同等物の期末残高", "現金及び現金同等物の期末残高", True, "JPY", 90),
        fst.Fact(
            2, "pdf:現金及び現金同等物の四半期末残高(要約四半期連結財政状態計算書計上額)",
            "現金及び現金同等物の四半期末残高(要約四半期連結財政状態計算書計上額)",
            "現金及び現金同等物の四半期末残高(要約四半期連結財政状態計算書計上額)", True, "JPY", 100,
        ),
    ]
    assert keys_of(fst.apply_learned(cf_rows, [p1, p2], "CF", date(2025, 6, 30), "Q")) == [
        "CashAndCashEquivalentsIfDifferentFromBSBalanceIFRS", "CashAndCashEquivalentsIFRS",
    ]
    t1 = fst.Learned(period_end=date(2025, 9, 30), cls="Q",
                     maps={"PL": {"法人税等": fst.Mapped("IncomeTaxes", 1)}}, order={"PL": ["IncomeTaxes"]})
    t2 = fst.Learned(period_end=date(2025, 3, 31), cls="FY",
                     maps={"PL": {"法人税等合計": fst.Mapped("IncomeTaxes", 1)}}, order={"PL": ["IncomeTaxes"]})
    tax_rows = [pl_fact("法人税等", 12), pl_fact("法人税等合計", 15)]
    assert keys_of(fst.apply_learned(tax_rows, [t1, t2], "PL", date(2025, 6, 30), "Q")) == [None, "IncomeTaxes"]


def test_global_order_keeps_canonical_section_order_and_nearby_period_order() -> None:
    with_ei = fst.Learned(
        period_end=date(2026, 3, 31), cls="FY",
        maps={"PL": {
            "経常利益": fst.Mapped("OrdinaryIncome", 1),
            "固定資産売却益": fst.Mapped("GainOnSalesOfNoncurrentAssetsEI", 1),
            "特別利益合計": fst.Mapped("ExtraordinaryIncome", 1),
            "税引前当期純利益": fst.Mapped("IncomeBeforeIncomeTaxes", 1),
        }},
        order={"PL": ["OrdinaryIncome", "GainOnSalesOfNoncurrentAssetsEI", "ExtraordinaryIncome", "IncomeBeforeIncomeTaxes"]},
    )
    with_el = fst.Learned(
        period_end=date(2025, 3, 31), cls="FY",
        maps={"PL": {
            "経常利益": fst.Mapped("OrdinaryIncome", 1),
            "固定資産除却損": fst.Mapped("LossOnRetirementOfNoncurrentAssetsEL", 1),
            "特別損失合計": fst.Mapped("ExtraordinaryLoss", 1),
            "税引前当期純利益": fst.Mapped("IncomeBeforeIncomeTaxes", 1),
        }},
        order={"PL": ["OrdinaryIncome", "LossOnRetirementOfNoncurrentAssetsEL", "ExtraordinaryLoss", "IncomeBeforeIncomeTaxes"]},
    )
    both_rows = [
        pl_fact("経常利益", 50), pl_fact("特別利益固定資産売却益", 5), pl_fact("特別利益合計", 5),
        pl_fact("特別損失固定資産除却損", 3), pl_fact("特別損失合計", 3), pl_fact("税引前当期純利益", 52),
    ]
    assert keys_of(fst.apply_learned(both_rows, [with_ei, with_el], "PL", date(2025, 12, 31), "Q")) == [
        "OrdinaryIncome", "GainOnSalesOfNoncurrentAssetsEI", "ExtraordinaryIncome",
        "LossOnRetirementOfNoncurrentAssetsEL", "ExtraordinaryLoss", "IncomeBeforeIncomeTaxes",
    ]
    old_q = fst.Learned(
        period_end=date(2024, 6, 30), cls="Q",
        maps={"PL": {
            "営業利益": fst.Mapped("OperatingProfitLossIFRS", 1),
            "金融収益": fst.Mapped("FinanceIncomeIFRS", 1),
            "金融費用": fst.Mapped("FinanceCostsIFRS", 1),
            "税引前利益": fst.Mapped("ProfitLossBeforeTaxIFRS", 1),
        }},
        order={"PL": ["OperatingProfitLossIFRS", "FinanceIncomeIFRS", "FinanceCostsIFRS", "ProfitLossBeforeTaxIFRS"]},
    )
    new_q = fst.Learned(
        period_end=date(2026, 6, 30), cls="Q",
        maps={"PL": {
            "金融収益": fst.Mapped("FinanceIncomeIFRS", 1),
            "金融費用": fst.Mapped("FinanceCostsIFRS", 1),
            "営業利益": fst.Mapped("OperatingProfitLossIFRS", 1),
            "税引前利益": fst.Mapped("ProfitLossBeforeTaxIFRS", 1),
        }},
        order={"PL": ["FinanceIncomeIFRS", "FinanceCostsIFRS", "OperatingProfitLossIFRS", "ProfitLossBeforeTaxIFRS"]},
    )
    old_rows = [pl_fact("営業利益", 20), pl_fact("金融収益", 2), pl_fact("金融費用", -1), pl_fact("税引前利益", 21)]
    assert keys_of(fst.apply_learned(old_rows, [old_q, new_q], "PL", date(2024, 9, 30), "Q")) == [
        "OperatingProfitLossIFRS", "FinanceIncomeIFRS", "FinanceCostsIFRS", "ProfitLossBeforeTaxIFRS",
    ]


def test_name_index_collapses_disjoint_split_elements_within_same_section() -> None:
    old_f = fst.Filing(id=1, doc_id="X1", source="edinet", period_type="FY", period_end=date(2024, 3, 31),
                       submitted="2024-06-20", standard="JPGAAP")
    old_f.by_statement = {"BS": [
        xbrl_fact(1, "DeferredTaxLiabilitiesNCL", 10, "繰延税金負債", True),
        xbrl_fact(2, "AllowanceForDoubtfulAccountsNCL", -1, "貸倒引当金", True),
        xbrl_fact(3, "NoncurrentLiabilities", 9, "固定負債合計", True),
    ]}
    new_f = fst.Filing(id=2, doc_id="X2", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                       submitted="2025-06-20", standard="JPGAAP")
    new_f.by_statement = {"BS": [
        xbrl_fact(4, "DeferredTaxLiabilities", 12, "繰延税金負債", True),
        xbrl_fact(5, "AllowanceForDoubtfulAccountsIOAByGroup", -2, "貸倒引当金", True),
        xbrl_fact(6, "NoncurrentLiabilities", 10, "固定負債合計", True),
    ]}
    names = fst.name_index([old_f, new_f], "BS")
    assert names.keys["繰延税金負債"] == "DeferredTaxLiabilities"
    assert "貸倒引当金" not in names.keys
    excluded = fst.name_index([old_f, new_f], "BS", {frozenset({"DeferredTaxLiabilitiesNCL", "DeferredTaxLiabilities"})})
    assert "繰延税金負債" not in excluded.keys


def test_pdf_matching_scopes_learned_and_names_to_accounting_standard() -> None:
    jp_pdf = fst.Filing(id=1, doc_id="P1", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                        submitted="2025-05-10", standard="JPGAAP")
    jp_pdf.by_statement = {"PL": [pl_fact("売上高", 100), pl_fact("営業利益", 15), pl_fact("当期純利益", 10)]}
    jp_xbrl = fst.Filing(id=2, doc_id="X1", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                         submitted="2025-06-20", standard="JPGAAP")
    jp_xbrl.by_statement = {"PL": [
        xbrl_fact(1, "NetSales", 100, "売上高"),
        xbrl_fact(2, "OperatingIncome", 15, "営業利益"),
        xbrl_fact(3, "ProfitLoss", 10, "当期純利益"),
    ]}
    jp_xbrl.seq = list(jp_xbrl.by_statement["PL"])
    ifrs_pdf = fst.Filing(id=3, doc_id="P2", source="tdnet_pdf", period_type="Q1", period_end=date(2025, 6, 30),
                          submitted="2025-08-05", standard="IFRS")
    ifrs_pdf.by_statement = {"PL": [
        pl_fact("売上収益", 30), pl_fact("営業利益", 5), pl_fact("税引前当期利益", 4),
        pl_fact("当期利益", 3), pl_fact("当期利益#2", 3),
    ]}
    ifrs_xbrl = fst.Filing(id=4, doc_id="X2", source="edinet", period_type="Q2", period_end=date(2025, 9, 30),
                           submitted="2025-11-10", standard="IFRS")
    ifrs_xbrl.by_statement = {"PL": [
        xbrl_fact(4, "RevenueIFRS", 62, "売上収益"),
        xbrl_fact(5, "OperatingProfitLossIFRS", 11, "営業利益（△損失）"),
        xbrl_fact(6, "ProfitLossBeforeTaxIFRS", 9, "税引前利益（△は損失）"),
        xbrl_fact(7, "ProfitLossIFRS", 6, "中間利益（△は損失）"),
    ]}
    ifrs_xbrl.seq = list(ifrs_xbrl.by_statement["PL"])
    filings = [jp_pdf, jp_xbrl, ifrs_pdf, ifrs_xbrl]
    learned = [fst.learn_pair(p, x) for p, x in fst._pairs(filings)]
    cols = fst.build_columns(filings, learned, "PL")
    q1 = next(c for c in cols if c.period_end == date(2025, 6, 30))
    assert q1.values == {
        "RevenueIFRS": 30 * M,
        "OperatingProfitLossIFRS": 5 * M,
        "ProfitLossBeforeTaxIFRS": 4 * M,
        "ProfitLossIFRS": 3 * M,
    }


def test_pairs_use_full_statement_pdf_when_amendment_is_partial() -> None:
    full_pdf = fst.Filing(id=1, doc_id="P1", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                          submitted="2025-05-10", standard="JPGAAP")
    full_pdf.by_statement = {
        "BS": [bs_fact("現金及び預金", 100), bs_fact("流動資産合計", 100), bs_fact("資産合計", 100)],
        "PL": [pl_fact("売上高", 200), pl_fact("営業利益", 30), pl_fact("経常利益", 31), pl_fact("当期純利益", 20)],
    }
    part_pdf = fst.Filing(id=2, doc_id="P2", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                          submitted="2025-06-01", standard="JPGAAP")
    part_pdf.by_statement = {"PL": [pl_fact("当期純利益", 21)]}
    xbrl = fst.Filing(id=3, doc_id="X1", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-25", standard="JPGAAP")
    xbrl.by_statement = {
        "BS": [xbrl_fact(1, "CashAndDeposits", 100, "現金及び預金", True),
               xbrl_fact(2, "CurrentAssets", 100, "流動資産合計", True),
               xbrl_fact(3, "Assets", 100, "資産合計", True)],
        "PL": [xbrl_fact(4, "NetSales", 200, "売上高"), xbrl_fact(5, "OperatingIncome", 30, "営業利益"),
               xbrl_fact(6, "OrdinaryIncome", 31, "経常利益"), xbrl_fact(7, "ProfitLoss", 21, "当期純利益")],
    }
    xbrl.seq = [*xbrl.by_statement["BS"], *xbrl.by_statement["PL"]]
    pairs = fst._pairs([full_pdf, part_pdf, xbrl])
    learned = fst.learn_pair(*pairs[0])
    assert "現金及び預金" in learned.maps["BS"]
    assert {"売上高", "営業利益", "経常利益"} <= set(learned.maps["PL"])


def test_pair_keeps_the_newest_pdf_when_the_longer_one_is_another_standard() -> None:
    def pdf(fid: int, submitted: str, standard: str, names: list[str]) -> fst.Filing:
        filing = fst.Filing(id=fid, doc_id=f"P{fid}", source="tdnet_pdf", period_type="FY",
                            period_end=date(2025, 3, 31), submitted=submitted, standard=standard)
        filing.by_statement = {"PL": [
            fst.Fact(fid * 100 + n, f"pdf:{name}", name, name, False, "JPY", 10 + n) for n, name in enumerate(names)
        ]}
        return filing

    jgaap = pdf(1, "2025-05-10", "JPGAAP", ["売上高", "売上原価", "売上総利益", "営業利益", "経常利益", "当期純利益"])
    ifrs = pdf(2, "2025-06-25", "IFRS", ["売上収益", "営業利益", "当期利益"])
    xbrl = fst.Filing(id=3, doc_id="X1", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-26", standard="IFRS")
    xbrl.by_statement = {"PL": [xbrl_fact(4, "RevenueIFRS", 10, "売上収益")]}
    (paired, _), = fst._pairs([jgaap, ifrs, xbrl])
    assert [f.local for f in paired.by_statement["PL"]] == ["売上収益", "営業利益", "当期利益"]
    part = pdf(5, "2025-07-01", "IFRS", ["当期利益"])
    (paired, _), = fst._pairs([jgaap, ifrs, part, xbrl])
    assert [f.local for f in paired.by_statement["PL"]] == ["売上収益", "営業利益", "当期利益"]


def test_cf_keeps_the_loss_wording_of_signed_profit_labels() -> None:
    xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                      submitted="2025-06-20", standard="JPGAAP")
    xbrl.by_statement = {"CF": [
        xbrl_fact(1, "IncomeBeforeIncomeTaxes", 50, "税金等調整前当期純利益又は税金等調整前当期純損失（△）"),
        xbrl_fact(2, "NetIncreaseDecreaseInCashAndCashEquivalents", 25, "現金及び現金同等物の増減額（△は減少）"),
    ]}
    xbrl.seq = list(xbrl.by_statement["CF"])
    names = fst.name_index([xbrl], "CF")
    assert names.losses == {
        "税金等調整前当期純損失": "IncomeBeforeIncomeTaxes",
        "現金及び現金同等物の減少額": "NetIncreaseDecreaseInCashAndCashEquivalents",
    }
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                     submitted="2025-05-10", standard="JPGAAP")
    pdf.by_statement = {"CF": [
        fst.Fact(11, "pdf:税金等調整前当期純利益", "税金等調整前当期純利益", "税金等調整前当期純利益", False, "JPY", 50 * M),
        fst.Fact(12, "pdf:現金及び現金同等物の増減額", "現金及び現金同等物の増減額", "現金及び現金同等物の増減額", False, "JPY", 25 * M),
    ]}
    learned = fst.learn_pair(pdf, xbrl)
    assert learned.losses["CF"] == {
        "税金等調整前当期純損失": fst.Mapped("IncomeBeforeIncomeTaxes", 1),
        "現金及び現金同等物の減少額": fst.Mapped("NetIncreaseDecreaseInCashAndCashEquivalents", 1),
    }
    rows = [
        fst.Fact(21, "pdf:税金等調整前当期純損失", "税金等調整前当期純損失", "税金等調整前当期純損失", False, "JPY", -9),
        fst.Fact(22, "pdf:現金及び現金同等物の減少額", "現金及び現金同等物の減少額", "現金及び現金同等物の減少額", False, "JPY", -7),
    ]
    for pool in ([learned], []):
        assert keys_of(fst.apply_learned(rows, pool, "CF", date(2025, 6, 30), "Q", names)) == [
            "IncomeBeforeIncomeTaxes", "NetIncreaseDecreaseInCashAndCashEquivalents",
        ]


def test_label_cleaning_canonical_wording_twice_printed_loss_and_cf_cash_decrease() -> None:
    assert fst._clean_label("営業利益（損失）") == "営業利益"
    assert fst._clean_label("デリバティブ評価益─") == "デリバティブ評価益"
    assert fst.strip_heads("販売費及び一般管理費運送費及び保管費", "PL") == "運送費及び保管費"
    assert fst.strip_heads("その他の包括利益その他有価証券評価差額金", "PL") == "その他有価証券評価差額金"
    assert fst.strip_heads("特別法上の準備金金融商品取引責任準備金", "BS") == "金融商品取引責任準備金"
    assert fst.strip_heads("負債及び資本営業債務及びその他の債務", "BS") == "営業債務及びその他の債務"
    assert fst.strip_heads("負債及び資本合計", "BS") == "負債及び資本合計"
    assert fst.strip_heads("その他の包括利益累計額合計", "BS") == "その他の包括利益累計額合計"
    assert fst._pdf_label("その他の包括利益累計額合計") == "その他の包括利益累計額合計"
    assert fst.strip_heads("1株当たり当期利益(円)基本的1株当たり当期利益(円)", "PL") == "基本的1株当たり当期利益(円)"
    assert fst.strip_heads("1株当たり当期利益基本的", "PL") == "1株当たり当期利益基本的"
    pl_xbrl = fst.Filing(id=1, doc_id="X", source="edinet", period_type="Q2", period_end=date(2025, 9, 30),
                         submitted="2025-11-10", standard="JPGAAP")
    pl_xbrl.by_statement = {"PL": [
        xbrl_fact(1, "IncomeBeforeIncomeTaxes", 50, "税引前当期純利益"),
        xbrl_fact(2, "ProfitLoss", 30, "中間純利益又は中間純損失（△）"),
        xbrl_fact(3, "ComprehensiveIncome", 35, "中間包括利益"),
        xbrl_fact(4, "ComprehensiveIncomeAttributableToOwnersOfTheParent", 34, "親会社株主に係る中間包括利益"),
    ]}
    pl_xbrl.seq = list(pl_xbrl.by_statement["PL"])
    pl_pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="Q2", period_end=date(2025, 9, 30),
                        submitted="2025-11-01", standard="JPGAAP")
    pl_pdf.by_statement = {"PL": [pl_fact("当期純利益", 30), pl_fact("当期純利益#2", 30)]}
    learned = fst.learn_pair(pl_pdf, pl_xbrl)
    names = fst.name_index([pl_xbrl], "PL")
    q1_rows = [
        pl_fact("税金等調整前当期純利益", -10), pl_fact("当期純損失", -10), pl_fact("当期純損失#2", -10),
        pl_fact("四半期包括利益", -8), pl_fact("親会社株主に係る四半期包括利益", -8),
    ]
    assert keys_of(fst.apply_learned(q1_rows, [learned], "PL", date(2025, 6, 30), "Q", names)) == [
        "IncomeBeforeIncomeTaxes", "ProfitLoss", "ProfitLoss",
        "ComprehensiveIncome", "ComprehensiveIncomeAttributableToOwnersOfTheParent",
    ]
    cf_xbrl = fst.Filing(id=3, doc_id="CX", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                         submitted="2025-06-20", standard="JPGAAP")
    cf_xbrl.by_statement = {"CF": [
        xbrl_fact(10, "NetIncreaseDecreaseInCashAndCashEquivalents", 25, "現金及び現金同等物の増減額（△は減少）"),
    ]}
    cf_xbrl.seq = list(cf_xbrl.by_statement["CF"])
    cf_pdf = fst.Filing(id=4, doc_id="CP", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                        submitted="2025-05-10", standard="JPGAAP")
    cf_pdf.by_statement = {"CF": [
        fst.Fact(11, "pdf:現金及び現金同等物の増減額", "現金及び現金同等物の増減額", "現金及び現金同等物の増減額", False, "JPY", 25),
    ]}
    cf_learned = fst.learn_pair(cf_pdf, cf_xbrl)
    cf_names = fst.name_index([cf_xbrl], "CF")
    dec_row = [
        fst.Fact(12, "pdf:現金及び現金同等物の減少額", "現金及び現金同等物の減少額", "現金及び現金同等物の減少額", False, "JPY", -7),
    ]
    assert keys_of(fst.apply_learned(dec_row, [cf_learned], "CF", date(2025, 6, 30), "Q", cf_names)) == [
        "NetIncreaseDecreaseInCashAndCashEquivalents",
    ]


def order(rows: list[tuple[str, tuple]], labels: dict[str, str] | None = None) -> list[str]:
    keys = [key for key, _ in rows]
    paths = [tuple((name, name) for name in path) for _, path in rows]
    return [keys[i] for i in fst.gather_heads(keys, paths, labels or {})]


def test_rows_of_one_heading_are_gathered_into_the_largest_block() -> None:
    loss, gain = ("特別損失",), ("特別利益",)
    rows = [("OrdinaryIncome", ()), ("Damages", loss), ("Subsidy", gain), ("ExtraordinaryIncome", gain),
            ("Impairment", loss), ("Disaster", loss), ("ExtraordinaryLoss", loss), ("IncomeBeforeIncomeTaxes", ())]
    assert order(rows) == ["OrdinaryIncome", "Subsidy", "ExtraordinaryIncome", "Damages", "Impairment", "Disaster",
                           "ExtraordinaryLoss", "IncomeBeforeIncomeTaxes"]
    late = [("Impairment", loss), ("LossTotal", loss), ("Subsidy", gain), ("Old", loss), ("Tax", ())]
    assert order(late, {"LossTotal": "特別損失合計"}) == ["Impairment", "Old", "LossTotal", "Subsidy", "Tax"]
    assert order(late) == ["Impairment", "LossTotal", "Old", "Subsidy", "Tax"]


def test_rows_under_an_outer_heading_between_inner_blocks_stay_in_place() -> None:
    fixed, ppe, ioa = ("資産", "固定資産"), ("資産", "固定資産", "有形"), ("資産", "固定資産", "投資")
    rows = [("Land", ppe), ("PPE", ppe), ("Intangible", fixed), ("Securities", ioa), ("IOA", ioa),
            ("NoncurrentAssets", fixed), ("Assets", ("資産",)), ("Total", ())]
    assert order(rows) == [key for key, _ in rows]
    split = [("Land", ppe), ("Intangible", fixed), ("Securities", ioa), ("OldBuilding", ppe), ("NoncurrentAssets", fixed)]
    assert order(split) == ["Land", "OldBuilding", "Intangible", "Securities", "NoncurrentAssets"]
    deep = [("A", ("負債", "流動")), ("X", ("資産", "流動")), ("B", ("負債", "固定")), ("C", ("負債", "流動"))]
    assert order(deep) == ["X", "A", "C", "B"]


def test_children_of_a_section_total_go_under_the_heading_other_filings_declare() -> None:
    got = rows(table(PARENT))
    ioa = ("資産の部", "固定資産", "投資その他の資産")
    assert (heads(got["OtherIOA"]), got["OtherIOA"]["indent"]) == (ioa, 0)
    assert (heads(got["StocksOfSubsidiariesAndAffiliates"]), got["StocksOfSubsidiariesAndAffiliates"]["indent"]) == (ioa, 0)
    keys = list(got)
    assert keys.index("OtherIOA") < keys.index("InvestmentsAndOtherAssets")


def test_pdf_rows_of_the_same_name_keep_their_section_when_one_drops_out(monkeypatch) -> None:
    totals = {"流動資産": "CurrentAssets", "投資その他の資産": "InvestmentsAndOtherAssets",
              "固定資産": "NoncurrentAssets", "資産": "Assets"}
    book = fst.standard_book({"labels": {"JPGAAP": {"BS": {
        name: {"any": key, "home": key, "within": {key: key}} for name, key in totals.items()
    }}}})
    monkeypatch.setattr(fst, "_standard_book", lambda: book)
    got = table(SHIFTED, consolidated=True)
    keys = [r["key"] for r in got["rows"]]
    cells = {r["key"]: [None if c is None else c["value"] for c in r["cells"]] for r in got["rows"]}
    allowance = [k for k in keys if k.startswith("pdf:貸倒引当金")]
    assert len(allowance) == 2
    current, invest = allowance
    assert cells[current] == [-1 * M, None]
    assert cells[invest] == [-2 * M, -3 * M]
    assert keys.index(current) < keys.index("CurrentAssets") < keys.index(invest) < keys.index("InvestmentsAndOtherAssets")
    assert {r["label"] for r in got["rows"] if r["key"] in allowance} == {"貸倒引当金"}


def cell_values(t: dict, key: str) -> list[float | None]:
    return [None if cell is None else cell["value"] for cell in rows(t)[key]["cells"]]


def test_same_name_headings_from_different_elements_print_once() -> None:
    pl = table(SPLIT_HEAD, "PL")
    assert [r["key"] for r in pl["rows"]] == [
        "ImpairmentLossesPL", "ext:ShareOnly", "FinanceCosts", "ext:TotalExpenses",
    ]
    assert emitted_heading_names(pl["rows"]).count("費用") == 1
    assert adjacent_same_headings(pl["rows"]) == []
    assert rows(pl)["ext:ShareOnly"]["heads"][0]["key"] == rows(pl)["FinanceCosts"]["heads"][0]["key"]
    assert cell_values(pl, "ImpairmentLossesPL") == [10 * M, 11 * M]
    assert cell_values(pl, "ext:ShareOnly") == [3 * M, None]
    assert cell_values(pl, "FinanceCosts") == [None, 4 * M]
    assert cell_values(pl, "ext:TotalExpenses") == [13 * M, 15 * M]
    bs = table(AOCI_HEAD, "BS")
    keys = [r["key"] for r in bs["rows"]]
    assert keys.index("ext:AociOldTotal") < keys.index("ValuationAndTranslationAdjustments")
    assert emitted_heading_names(bs["rows"]).count("その他の包括利益累計額") == 1
    assert adjacent_same_headings(bs["rows"]) == []
    assert cell_values(bs, "ValuationDifferenceOnAvailableForSaleSecurities") == [5 * M, 7 * M]
    assert cell_values(bs, "ext:AociOldTotal") == [5 * M, None]
    assert cell_values(bs, "ValuationAndTranslationAdjustments") == [None, 7 * M]


def test_a_later_fragment_of_the_same_heading_stays_before_the_total() -> None:
    cf = table(TAIL_HEAD, "CF")
    keys = [r["key"] for r in cf["rows"]]
    assert keys.index("ext:SaleOfShares") < keys.index("ext:AdjNew")
    assert emitted_heading_names(cf["rows"]).count("利益に対する調整項目") == 1
    assert adjacent_same_headings(cf["rows"]) == []
    assert rows(cf)["ext:SaleOfShares"]["heads"][0]["key"] == rows(cf)["ext:AdjNew"]["heads"][0]["key"]
    assert cell_values(cf, "DepreciationAndAmortizationOpeCF") == [8 * M, 9 * M]
    assert cell_values(cf, "ext:SaleOfShares") == [2 * M, None]
    assert cell_values(cf, "ext:OtherAdj") == [None, 1 * M]
    assert cell_values(cf, "ext:AdjNew") == [8 * M, 10 * M]


def test_two_breakdown_headings_in_one_filing_stay_apart() -> None:
    pl = table(BREAKDOWN, "PL")
    keys = [r["key"] for r in pl["rows"]]
    assert keys == [
        "ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToNonControllingInterests",
        "ComprehensiveIncome", "ComprehensiveIncomeAttributableToOwnersOfTheParent",
        "ComprehensiveIncomeAttributableToNonControllingInterests",
    ]
    assert emitted_heading_names(pl["rows"]).count("（内訳）") == 2
    assert adjacent_same_headings(pl["rows"]) == []
    profit = rows(pl)["ProfitLossAttributableToOwnersOfParent"]["heads"][0]["key"]
    comprehensive = rows(pl)["ComprehensiveIncomeAttributableToOwnersOfTheParent"]["heads"][0]["key"]
    assert profit != comprehensive
    assert cell_values(pl, "ProfitLossAttributableToOwnersOfParent") == [80 * M]
    assert cell_values(pl, "ComprehensiveIncomeAttributableToOwnersOfTheParent") == [90 * M]


def test_aliased_heads_keep_the_section_total_at_the_end() -> None:
    rows = [
        ("Dep", (("new", "調整"),)), ("Other", (("new", "調整"),)), ("Total", (("new", "調整"),)),
        ("Sale", (("old", "調整"),)),
    ]
    keys = [key for key, _ in rows]
    paths = [path for _, path in rows]
    got = [keys[i] for i in fst.gather_heads(keys, paths, {"Total": "調整合計"}, {("old", "調整"): "new"})]
    assert got == ["Dep", "Other", "Sale", "Total"]
    assert [keys[i] for i in fst.gather_heads(keys, paths, {"Total": "調整合計"})] == ["Dep", "Other", "Total", "Sale"]


def test_heading_order_diff_counts_changed_values_and_merged_breakdowns() -> None:
    breakdown = [
        {"key": "Profit", "label": "親", "heads": [["a", "（内訳）"]], "merged": [], "cells": [1.0]},
        {"key": "Gap", "label": "包括", "heads": [], "merged": [], "cells": [2.0]},
        {"key": "CI", "label": "親包括", "heads": [["b", "（内訳）"]], "merged": [], "cells": [3.0]},
    ]
    base = {"code": "49420", "basis": "consolidated", "statement": "PL", "which": 0, "rows": breakdown}
    same = diff_dumps([base], [base])
    assert same["codes"] == same["codes_all"] == 1
    assert same["failed"] == same["bad_values"] == same["bad_rows"] == same["bad_uchiwake"] == same["dup"] == 0
    changed = {**base, "rows": [{**breakdown[0], "cells": [9.0]}, *breakdown[1:]]}
    assert diff_dumps([base], [changed])["bad_values"] == 1
    merged = {**base, "rows": [breakdown[0], {**breakdown[2], "heads": [["a", "（内訳）"]]}, breakdown[1]]}
    assert uchiwake_count(merged["rows"]) == 1
    assert diff_dumps([base], [merged])["bad_uchiwake"] == 1
    dup = {**base, "rows": [
        {"key": "A", "label": "減損", "heads": [["new", "費用"]], "merged": [], "cells": [10.0]},
        {"key": "B", "label": "持分", "heads": [["old", "費用"]], "merged": [], "cells": [3.0]},
    ]}
    assert diff_dumps([dup], [dup])["dup"] == 1
    assert adjacent_same_headings(breakdown) == []
    failed = diff_dumps(
        [{"code": "1", "error": "x"}], [{"code": "1", "basis": "consolidated", "statement": "PL", "which": 0, "rows": []}],
    )
    assert failed["failed"] == 1


def test_kana_heading_variants_merge_into_latest_section_without_duplicating_subheadings() -> None:
    bs = table(KANA_HEAD, "BS")
    assert [r["key"] for r in bs["rows"]] == [
        "BorrowingsCLIFRS", "TradeAndOtherPayablesCLIFRS", "TotalCurrentLiabilitiesIFRS",
        "BorrowingsNCLIFRS", "BondsPayableNCLIFRS", "NonCurrentLabilitiesIFRS",
        "LiabilitiesAndEquityIFRS",
    ]
    assert emitted_heading_names(bs["rows"]) == ["負債及び資本", "負債", "流動負債", "非流動負債"]
    assert rows(bs)["BorrowingsCLIFRS"]["heads"][0] == {
        "key": "LiabilitiesAndEquityIFRSAbstract", "name": "負債及び資本",
    }
    assert rows(bs)["BorrowingsNCLIFRS"]["heads"][0] == {
        "key": "LiabilitiesAndEquityIFRSAbstract", "name": "負債及び資本",
    }


def test_row_roles_distinguish_items_subtotals_totals_and_grand_totals() -> None:
    bs_keys = [
        "CashAndDeposits", "CurrentAssets", "BuildingsAndStructures", "PropertyPlantAndEquipment",
        "NoncurrentAssets", "Assets", "CurrentAssetsOtherThanAssetsHeldForSaleCAIFRS",
        "PropertyPlantAndEquipmentIFRS", "ShareholdersEquity", "NetAssets", "LiabilitiesAndNetAssets",
    ]
    bs_labels = {
        "CashAndDeposits": "現金預金", "CurrentAssets": "流動資産合計",
        "BuildingsAndStructures": "建物・構築物", "PropertyPlantAndEquipment": "有形固定資産合計",
        "NoncurrentAssets": "固定資産合計", "Assets": "資産合計",
        "CurrentAssetsOtherThanAssetsHeldForSaleCAIFRS": "(小計)",
        "PropertyPlantAndEquipmentIFRS": "有形固定資産", "ShareholdersEquity": "株主資本合計",
        "NetAssets": "純資産合計", "LiabilitiesAndNetAssets": "負債純資産合計",
    }
    bs_roles = fst._row_roles("BS", bs_keys, bs_labels, ["money"] * len(bs_keys), [[] for _ in bs_keys], [0] * len(bs_keys))
    assert dict(zip(bs_keys, bs_roles)) == {
        "CashAndDeposits": "item",
        "CurrentAssets": "total",
        "BuildingsAndStructures": "item",
        "PropertyPlantAndEquipment": "subtotal",
        "NoncurrentAssets": "total",
        "Assets": "grand",
        "CurrentAssetsOtherThanAssetsHeldForSaleCAIFRS": "subtotal",
        "PropertyPlantAndEquipmentIFRS": "item",
        "ShareholdersEquity": "subtotal",
        "NetAssets": "total",
        "LiabilitiesAndNetAssets": "grand",
    }

    pl_jpgaap_keys = [
        "NetSales", "CostOfSales", "GrossProfit", "OperatingIncome", "NonOperatingIncome", "OrdinaryIncome",
        "IncomeBeforeIncomeTaxes", "IncomeTaxes", "ProfitLoss",
        "ProfitLossAttributableToNonControllingInterests", "ProfitLossAttributableToOwnersOfParent",
        "OtherComprehensiveIncome", "ComprehensiveIncome", "BasicEarningsLossPerShare",
    ]
    pl_jpgaap_units = ["money"] * (len(pl_jpgaap_keys) - 1) + ["per_share"]
    pl_jpgaap_heads = [
        [], [], [], [], [{"key": "h:noi", "name": "営業外収益"}], [],
        [], [], [], [], [], [{"key": "h:oci", "name": "その他の包括利益"}], [], [],
    ]
    pl_jpgaap_roles = fst._row_roles(
        "PL", pl_jpgaap_keys, {}, pl_jpgaap_units, pl_jpgaap_heads, [0] * len(pl_jpgaap_keys),
    )
    assert dict(zip(pl_jpgaap_keys, pl_jpgaap_roles)) == {
        "NetSales": "total",
        "CostOfSales": "item",
        "GrossProfit": "subtotal",
        "OperatingIncome": "total",
        "NonOperatingIncome": "subtotal",
        "OrdinaryIncome": "total",
        "IncomeBeforeIncomeTaxes": "subtotal",
        "IncomeTaxes": "subtotal",
        "ProfitLoss": "total",
        "ProfitLossAttributableToNonControllingInterests": "item",
        "ProfitLossAttributableToOwnersOfParent": "grand",
        "OtherComprehensiveIncome": "subtotal",
        "ComprehensiveIncome": "total",
        "BasicEarningsLossPerShare": "item",
    }

    pl_breakdown = table(BREAKDOWN, "PL")
    assert {r["key"]: r["role"] for r in pl_breakdown["rows"]} == {
        "ProfitLoss": "grand",
        "ProfitLossAttributableToOwnersOfParent": "item",
        "ProfitLossAttributableToNonControllingInterests": "item",
        "ComprehensiveIncome": "total",
        "ComprehensiveIncomeAttributableToOwnersOfTheParent": "item",
        "ComprehensiveIncomeAttributableToNonControllingInterests": "item",
    }

    cf_keys = [
        "IncomeBeforeIncomeTaxes", "SubtotalOpeCF", "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInInvestmentActivities", "NetCashProvidedByUsedInFinancingActivities",
        "NetIncreaseDecreaseInCashAndCashEquivalents", "CashAndCashEquivalents",
    ]
    cf_heads = [[{"key": "h:ope", "name": "営業CF"}]] * 3 + [[{"key": "h:inv", "name": "投資CF"}]] + [
        [{"key": "h:fin", "name": "財務CF"}], [], [],
    ]
    cf_roles = fst._row_roles("CF", cf_keys, {}, ["money"] * len(cf_keys), cf_heads, [0] * len(cf_keys))
    assert dict(zip(cf_keys, cf_roles)) == {
        "IncomeBeforeIncomeTaxes": "item",
        "SubtotalOpeCF": "subtotal",
        "NetCashProvidedByUsedInOperatingActivities": "total",
        "NetCashProvidedByUsedInInvestmentActivities": "total",
        "NetCashProvidedByUsedInFinancingActivities": "total",
        "NetIncreaseDecreaseInCashAndCashEquivalents": "subtotal",
        "CashAndCashEquivalents": "grand",
    }


def test_accumulated_rows_merge_only_under_one_parent() -> None:
    labels = {"RoU": "使用権資産", "AccExt": "減価償却累計額", "AccRoU": "減価償却累計額", "AccPPE": "減価償却累計額",
              "RoUNet": "使用権資産(純額)", "Bld": "建物", "AccBld": "減価償却累計額"}
    seq = [{"Bld": 9, "AccBld": -2, "RoU": 5, "AccExt": -1, "RoUNet": 4},
           {"Bld": 9, "AccBld": -3, "RoU": 5, "AccRoU": -2, "RoUNet": 3},
           {"Bld": 9, "AccBld": -4, "RoU": 5, "AccPPE": -3, "RoUNet": 2}]
    got, keys, cols = merged(seq, labels)
    assert got == {"AccPPE": ["AccRoU", "AccExt"]}
    assert keys == ["Bld", "AccBld", "RoU", "AccPPE", "RoUNet"]
    assert [c.values["AccPPE"] for c in cols] == [-1, -2, -3]


def test_accumulated_rows_under_other_parents_stay_apart() -> None:
    labels = {"Veh": "車両運搬具", "AccVeh": "減価償却累計額", "Mach": "機械装置及び運搬具", "AccMach": "減価償却累計額"}
    got, _, _ = merged([{"Veh": 5, "AccVeh": -1}, {"Mach": 8, "AccMach": -2}], labels)
    assert got == {}


def test_accumulated_rows_follow_a_parent_merged_across_tags() -> None:
    labels = {"RoUOld": "使用権資産", "RoU": "使用権資産", "AccOld": "減価償却累計額", "Acc": "減価償却累計額"}
    got, keys, _ = merged([{"RoUOld": 5, "AccOld": -1}, {"RoU": 5, "Acc": -2}], labels)
    assert got == {"RoU": ["RoUOld"], "Acc": ["AccOld"]}
    assert keys == ["RoU", "Acc"]

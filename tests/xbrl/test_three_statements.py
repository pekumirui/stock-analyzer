"""財務3表: 有報・短信 XBRL・短信 PDF を要素でそろえて 1 枚の表にする（docs/DESIGN_xbrl.md §17）。"""
from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.xbrl import three_statements as fst

M = 1_000_000
CODE = "12340"
LOOSE = "56780"
SINGLE = "24680"
NAMED = "13570"
QUARTER = "36790"
TWICE = "22820"
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

    def close(self) -> None:
        self.db.commit()
        self.db.close()


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


def seed() -> None:
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
    _quarterly(s)
    _twice(s)
    s.close()


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed()


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
                   "accounting_standard": "JPGAAP"}


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
    assert rows(t)["BondsPayable"]["section"] == "固定負債" and rows(t)["BorrowingsCLIFRS"]["section"] == "流動負債"


JP_KEYS = [
    "CashAndDeposits", "OtherCA", "CurrentAssets", "Land", "PropertyPlantAndEquipment", "Goodwill", "IntangibleAssets",
    "InvestmentSecurities", "InvestmentsAndOtherAssets", "NoncurrentAssets", "Assets",
    "OtherCL", "CurrentLiabilities", "LongTermLoansPayable", "NoncurrentLiabilities", "Liabilities",
    "CapitalStock", "ShareholdersEquity", "ValuationDifferenceOnAvailableForSaleSecurities",
    "ValuationAndTranslationAdjustments", "NonControllingInterests", "NetAssets", "LiabilitiesAndNetAssets",
]
JP_LABELS = {
    "CurrentAssets": "流動資産合計", "PropertyPlantAndEquipment": "有形固定資産合計", "IntangibleAssets": "無形固定資産",
    "InvestmentsAndOtherAssets": "投資その他の資産合計", "NoncurrentAssets": "固定資産合計", "Assets": "資産合計",
    "CurrentLiabilities": "流動負債合計", "NoncurrentLiabilities": "固定負債合計", "Liabilities": "負債合計",
    "ShareholdersEquity": "株主資本合計", "ValuationAndTranslationAdjustments": "評価・換算差額等合計",
    "NetAssets": "純資産合計",
}


def parts(keys: list[str], statement: str = "BS", labels: dict | None = None) -> dict[str, tuple]:
    return dict(zip(keys, fst.sections(keys, statement, labels if labels is not None else JP_LABELS)))


def test_sections_jpgaap() -> None:
    got = parts(JP_KEYS)
    assert got["OtherCA"] == got["CurrentAssets"] == ("資産", "流動資産")
    assert got["Land"] == got["PropertyPlantAndEquipment"] == ("資産", "有形固定資産")
    assert got["IntangibleAssets"] == ("資産", "無形固定資産")
    assert got["InvestmentSecurities"] == ("資産", "投資その他の資産")
    assert got["NoncurrentAssets"] == ("資産", "固定資産")
    assert got["Assets"] == ("資産", None)
    assert got["OtherCL"] == ("負債", "流動負債") and got["LongTermLoansPayable"] == ("負債", "固定負債")
    assert got["Liabilities"] == ("負債", None)
    assert got["CapitalStock"] == ("純資産", "株主資本")
    assert got["ValuationDifferenceOnAvailableForSaleSecurities"] == ("純資産", "評価・換算差額等")
    assert got["NonControllingInterests"] == got["NetAssets"] == ("純資産", None)
    assert got["LiabilitiesAndNetAssets"] == (None, None)


def test_sections_ifrs_and_inserted_rows() -> None:
    keys = ["CashAndCashEquivalentsIFRS", "CurrentAssetsIFRS", "pdf:投資有価証券", "PropertyPlantAndEquipmentIFRS",
            "NonCurrentAssetsIFRS", "AssetsIFRS", "BorrowingsCLIFRS", "TotalCurrentLiabilitiesIFRS",
            "BorrowingsNCLIFRS", "NonCurrentLabilitiesIFRS", "LiabilitiesIFRS", "ShareCapitalIFRS",
            "EquityAttributableToOwnersOfParentIFRS", "NonControllingInterestsIFRS", "EquityIFRS",
            "LiabilitiesAndEquityIFRS"]
    labels = {"CurrentAssetsIFRS": "流動資産合計", "NonCurrentAssetsIFRS": "非流動資産合計", "AssetsIFRS": "資産合計",
              "TotalCurrentLiabilitiesIFRS": "流動負債合計", "NonCurrentLabilitiesIFRS": "非流動負債合計",
              "LiabilitiesIFRS": "負債合計", "EquityAttributableToOwnersOfParentIFRS": "親会社の所有者に帰属する持分合計",
              "EquityIFRS": "資本合計"}
    got = parts(keys, labels=labels)
    assert got["CashAndCashEquivalentsIFRS"] == ("資産", "流動資産")
    assert got["pdf:投資有価証券"] == got["PropertyPlantAndEquipmentIFRS"] == ("資産", "非流動資産")
    assert got["BorrowingsCLIFRS"] == ("負債", "流動負債") and got["BorrowingsNCLIFRS"] == ("負債", "非流動負債")
    assert got["ShareCapitalIFRS"] == ("資本", "親会社の所有者に帰属する持分")
    assert got["NonControllingInterestsIFRS"] == ("資本", None)
    assert got["LiabilitiesAndEquityIFRS"] == (None, None)


def test_sections_absent_without_totals_or_in_odd_order() -> None:
    bank = ["CashAndDueFromBanksAssetsBNK", "LoansAndBillsDiscountedAssetsBNK", "DepositsLiabilitiesBNK"]
    assert set(parts(bank).values()) == {(None, None)}
    odd = ["CashAndDeposits", "Liabilities", "CurrentAssets", "Assets", "NetAssets"]
    assert set(parts(odd).values()) == {(None, None)}
    assert parts(["Assets", "Liabilities"], labels={})["Assets"] == ("資産", None)
    assert set(parts(JP_KEYS, "PL").values()) == {(None, None)}


def test_sections_cf() -> None:
    keys = ["IncomeBeforeIncomeTaxes", "SubtotalOpeCF", "NetCashProvidedByUsedInOperatingActivities",
            "PurchaseOfPropertyPlantAndEquipmentInvCF", "NetCashProvidedByUsedInInvestmentActivities",
            "DividendsPaidFinCF", "NetCashProvidedByUsedInFinancingActivities",
            "NetIncreaseDecreaseInCashAndCashEquivalents", "CashAndCashEquivalents"]
    labels = {"NetCashProvidedByUsedInOperatingActivities": "営業活動によるキャッシュ・フロー",
              "NetCashProvidedByUsedInInvestmentActivities": "投資活動によるキャッシュ・フロー計",
              "NetCashProvidedByUsedInFinancingActivities": "財務活動によるキャッシュ・フロー"}
    got = parts(keys, "CF", labels)
    assert got["IncomeBeforeIncomeTaxes"] == got["SubtotalOpeCF"] == (None, "営業活動によるキャッシュ・フロー")
    assert got["PurchaseOfPropertyPlantAndEquipmentInvCF"] == (None, "投資活動によるキャッシュ・フロー")
    assert got["NetCashProvidedByUsedInFinancingActivities"] == (None, "財務活動によるキャッシュ・フロー")
    assert got["CashAndCashEquivalents"] == (None, None)
    swapped = [keys[4], keys[2]]
    assert set(parts(swapped, "CF", labels).values()) == {(None, None)}


def test_table_rows_carry_sections() -> None:
    got = {k: (r["group"], r["section"]) for k, r in rows(table()).items()}
    assert got["CashAndDeposits"] == ("資産", "流動資産")
    assert got["Goodwill"] == got["IntangibleAssets"] == ("資産", "無形固定資産")
    assert got["ShortTermLoansPayable"] == got["pdf:前受金"] == ("負債", None)
    assert got["LiabilitiesAndNetAssets"] == (None, None)
    t = table(statement="CF")
    assert {(r["group"], r["section"]) for r in t["rows"][:2]} == {(None, "営業活動によるキャッシュ・フロー")}
    assert {(r["group"], r["section"]) for r in table(statement="PL")["rows"]} == {(None, None)}


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

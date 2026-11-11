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
                 relative: str | None) -> int:
        dims = dims or {}
        ref = f"{kind}{'' if consolidated else '_NC'}{json.dumps(dims, sort_keys=True)}{relative}"
        key = (fid, ref)
        if key not in self.contexts:
            self.contexts[key] = len(self.contexts) + 1
            self._run(
                "INSERT INTO xbrl_contexts (id, filing_id, context_ref, period_type, start_date, end_date, "
                "is_consolidated, relative_period, dimensions_json, has_dimensions) "
                "VALUES (:id, :fid, :ref, :pt, :start, :end, :consol, :rel, :dims, :has)",
                {"id": self.contexts[key], "fid": fid, "ref": ref, "pt": "instant" if kind == "i" else "duration",
                 "start": None if kind == "i" else "2024-04-01", "end": end, "consol": 1 if consolidated else 0,
                 "rel": relative, "dims": json.dumps(dims), "has": 1 if dims else 0},
            )
        return self.contexts[key]

    def facts(self, fid: int, end: str, rows: list[tuple], *, consolidated: bool = True,
              dims: dict | None = None, relative: str | None = "current") -> None:
        for row in rows:
            ns, local, kind, value = row[:4]
            unit = row[4] if len(row) > 4 else "JPY"
            ctx = self._context(fid, kind, end, consolidated=consolidated, dims=dims, relative=relative)
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


def seed() -> None:
    s = Seeder()
    _company(s, CODE, 0)
    _company(s, LOOSE, 100, with_q_pair=False)
    s.stock(SINGLE, "個別だけ")
    s.filing(201, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=SINGLE)
    s.facts(201, "2025-03-31", FY_BS[:10] + [x("LiabilitiesAndNetAssets", "i", 154)])
    s.text_fact(201, "jpdei_cor", fst.DEI_CONSOLIDATED, "false")
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
    assert bs["有形固定資産土地"].key == "Land"
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


def test_pdf_only_period_drops_order_conflicts_without_matching_kind() -> None:
    t = table(LOOSE)
    assert value(t, "OtherIA", "25.06") is None
    assert value(t, "OtherCL", "25.06") is None
    assert value(t, "pdf:その他#2", "25.06") == 11 * M
    assert value(t, "pdf:その他#3", "25.06") == 6 * M
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

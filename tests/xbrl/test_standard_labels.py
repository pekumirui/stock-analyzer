from __future__ import annotations

import json
from datetime import date

import pytest

from app.config import XBRL_STANDARD_LABELS_PATH
from app.xbrl import three_statements as fst
from tools.xbrl import label_dictionary as ld

M = 1_000_000
END = date(2025, 6, 30)


def xfact(n: int, key: str, label: str, value: float = 1, unit: str | None = "JPY", instant: bool = True) -> fst.Fact:
    return fst.Fact(id=n, key=key, local=key.removeprefix("ext:"), label=label, instant=instant, unit=unit,
                    value=value * M)


def pdf_row(n: int, name: str, value: float = 1, unit: str | None = "JPY") -> fst.Fact:
    return fst.Fact(id=1000 + n, key=f"pdf:{name}", local=name, label="", instant=True, unit=unit, value=value * M)


def xbrl_filing(n: int, statement: str, facts: list[fst.Fact], standard: str = "JPGAAP") -> fst.Filing:
    f = fst.Filing(id=n, doc_id=f"X{n}", source="edinet", period_type="FY", period_end=date(2025, 3, 31),
                   submitted="2025-06-20", standard=standard)
    f.by_statement = {statement: facts}
    f.seq = list(facts)
    return f


def seen_of(companies: dict[str, list[fst.Filing]], major: dict[str, str] | None = None) -> ld.Seen:
    seen = ld.Seen()
    seen.major = dict(major or {})
    owners: dict[str, set[str]] = {}
    for key, name in seen.major.items():
        owners.setdefault(name, set()).add(key)
    seen.owners = owners
    for code, filings in companies.items():
        ld.observe(seen, code, filings)
    return seen


def bs_company(n: int, *rows: tuple[str, str]) -> list[fst.Filing]:
    facts = [xfact(i, key, label) for i, (key, label) in enumerate(rows, 1)]
    return [xbrl_filing(n, "BS", facts)]


def labels_of(book: dict, standard: str = "JPGAAP", statement: str = "BS") -> dict:
    return book["labels"].get(standard, {}).get(statement, {})


def test_label_needs_enough_companies_and_one_element() -> None:
    companies = {f"{i:04d}0": bs_company(i, ("CashAndDeposits", "現金及び預金"), ("CurrentAssets", "流動資産"))
                 for i in range(5)}
    companies["99990"] = bs_company(99, ("Goodwill", "のれん"), ("CurrentAssets", "流動資産"))
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    got = labels_of(book)
    assert got["現金及び預金"]["any"] == "CashAndDeposits"
    assert got["現金及び預金"]["within"] == {"CurrentAssets": "CashAndDeposits"}
    assert "のれん" not in got
    assert ld.build(seen_of(companies), min_codes=7, min_share=0.95)["labels"] == {}


def test_label_split_between_elements_is_not_listed() -> None:
    companies = {f"{i:04d}0": bs_company(i, ("DeferredTaxAssets", "繰延税金資産"), ("NoncurrentAssets", "固定資産"))
                 for i in range(5)}
    companies.update({f"{i:04d}1": bs_company(i, ("DeferredTaxAssetsIOA", "繰延税金資産"), ("NoncurrentAssets", "固定資産"))
                      for i in range(2)})
    assert "繰延税金資産" not in labels_of(ld.build(seen_of(companies), min_codes=5, min_share=0.95))


def test_company_elements_generic_names_and_opening_cash_are_not_listed() -> None:
    companies = {}
    for i in range(5):
        bs = xbrl_filing(i, "BS", [xfact(1, "ext:Special", "特別勘定"), xfact(2, "OtherCA", "その他"),
                                   xfact(3, "AllowanceForDoubtfulAccountsCA", "貸倒引当金"),
                                   xfact(4, "CurrentAssets", "流動資産")])
        cf = xbrl_filing(i, "CF", [xfact(5, "CashAndCashEquivalents", "現金及び現金同等物の期首残高")])
        companies[f"{i:04d}0"] = [bs, cf]
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    assert set(labels_of(book)) == {"流動資産"}
    assert labels_of(book, statement="CF") == {}


def test_names_in_sections_pick_the_element_of_the_section() -> None:
    companies = {f"{i:04d}0": bs_company(
        i, ("LeaseObligationsCL", "リース債務"), ("CurrentLiabilities", "流動負債"),
        ("LeaseObligationsNCL", "リース債務"), ("NoncurrentLiabilities", "固定負債"),
    ) for i in range(5)}
    got = labels_of(ld.build(seen_of(companies), min_codes=5, min_share=0.95))["リース債務"]
    assert got == {"within": {"CurrentLiabilities": "LeaseObligationsCL", "NoncurrentLiabilities": "LeaseObligationsNCL"}}


def test_standards_are_told_apart_by_the_elements_of_the_statement() -> None:
    companies = {}
    for i in range(5):
        companies[f"{i:04d}0"] = bs_company(i, ("Assets", "資産"), ("CurrentAssets", "流動資産"))
        ifrs = xbrl_filing(i, "BS", [xfact(1, "AssetsIFRS", "資産"), xfact(2, "CurrentAssetsIFRS", "流動資産")],
                           standard="IFRS")
        companies[f"{i:04d}1"] = [ifrs]
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    assert labels_of(book)["資産"]["any"] == "Assets"
    assert labels_of(book, "IFRS")["資産"]["any"] == "AssetsIFRS"


def test_label_borrowed_from_another_element_is_not_counted() -> None:
    companies = {}
    for i in range(5):
        cf = xbrl_filing(i, "CF", [xfact(1, "ProceedsFromLongTermLoansPayableFinCF", "長期借入れによる収入", instant=False),
                                   xfact(2, "RepaymentOfLongTermLoansPayableFinCF", "長期借入れによる収入", instant=False),
                                   xfact(3, "NetCashProvidedByUsedInFinancingActivities", "財務活動によるキャッシュ・フロー",
                                         instant=False)])
        companies[f"{i:04d}0"] = [cf]
    major = {"ProceedsFromLongTermLoansPayableFinCF": "長期借入れによる収入",
             "RepaymentOfLongTermLoansPayableFinCF": "長期借入金の返済による支出"}
    book = ld.build(seen_of(companies, major), min_codes=5, min_share=0.95)
    assert labels_of(book, statement="CF")["長期借入れによる収入"]["any"] == "ProceedsFromLongTermLoansPayableFinCF"
    assert labels_of(ld.build(seen_of(companies), min_codes=5, min_share=0.95), statement="CF").get(
        "長期借入れによる収入", {}).get("any") is None


def test_per_share_elements_are_marked_and_output_is_stable(tmp_path) -> None:
    companies = {f"{i:04d}0": [xbrl_filing(i, "PL", [
        xfact(1, "NetSales", "売上高", instant=False),
        xfact(2, "BasicEarningsLossPerShare", "1株当たり当期純利益", unit="JPYPerShares", instant=False),
    ])] for i in range(5)}
    seen = seen_of(companies)
    book = ld.build(seen, min_codes=5, min_share=0.95)
    assert book["per_share"] == ["BasicEarningsLossPerShare"]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    ld.write(book, a)
    ld.write(ld.build(seen, min_codes=5, min_share=0.95), b)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
    assert json.loads(a.read_text(encoding="utf-8"))["labels"]["JPGAAP"]["PL"]["売上高"]["any"] == "NetSales"


def test_pdf_row_names_and_xbrl_labels_use_the_same_normalization() -> None:
    for statement, local in (("PL", "営業外収益受取利息#2"), ("PL", "四半期純利益"), ("BS", "流動資産現金及び預金"),
                             ("CF", "長期借入れによる収入─")):
        row = pdf_row(1, local)
        assert ld.label_key(local, statement) == fst._canon_name(fst._base(row, statement))


def test_committed_dictionary_has_the_shape_the_reader_takes() -> None:
    raw = json.loads(XBRL_STANDARD_LABELS_PATH.read_text(encoding="utf-8"))
    assert {"labels", "per_share", "min_codes", "min_share"} <= set(raw)
    fst.standard_book(raw)


def book(**labels: dict) -> fst.StandardBook:
    raw: dict = {"labels": {}, "per_share": ["BasicEarningsLossPerShare"]}
    for spec, item in labels.items():
        standard, statement, name = spec.split("__")
        raw["labels"].setdefault(standard, {}).setdefault(statement, {})[name] = item
    return fst.standard_book(raw)


BOOK = {
    "JPGAAP__BS__現金及び預金": {"any": "CashAndDeposits", "home": "CurrentAssets", "within": {"CurrentAssets": "CashAndDeposits"}},
    "JPGAAP__BS__流動資産": {"any": "CurrentAssets", "home": "CurrentAssets", "within": {"CurrentAssets": "CurrentAssets"}},
    "JPGAAP__BS__資産": {"any": "Assets", "home": "Assets", "within": {"Assets": "Assets"}},
    "JPGAAP__BS__リース債務": {"within": {"CurrentLiabilities": "LeaseObligationsCL",
                                         "NoncurrentLiabilities": "LeaseObligationsNCL"}},
    "JPGAAP__BS__流動負債": {"any": "CurrentLiabilities", "home": "CurrentLiabilities",
                             "within": {"CurrentLiabilities": "CurrentLiabilities"}},
    "JPGAAP__BS__固定負債": {"any": "NoncurrentLiabilities", "home": "NoncurrentLiabilities",
                             "within": {"NoncurrentLiabilities": "NoncurrentLiabilities"}},
    "JPGAAP__BS__未収入金": {"any": "AccountsReceivableOther", "home": "CurrentAssets",
                             "within": {"CurrentAssets": "AccountsReceivableOther"}},
    "JPGAAP__BS__未収金": {"any": "AccountsReceivableOther", "home": "CurrentAssets",
                           "within": {"CurrentAssets": "AccountsReceivableOther"}},
    "JPGAAP__BS__1株当たり純資産": {"any": "NetAssetsPerShare", "home": "-", "within": {}},
    "JPGAAP__PL__受取和解金": {"any": "SettlementReceivedEI", "home": "ExtraordinaryIncome",
                               "within": {"ExtraordinaryIncome": "SettlementReceivedEI"}},
    "JPGAAP__PL__営業外収益": {"any": "NonOperatingIncome", "home": "NonOperatingIncome",
                               "within": {"NonOperatingIncome": "NonOperatingIncome"}},
    "IFRS__BS__資産": {"any": "AssetsIFRS", "home": "AssetsIFRS", "within": {"AssetsIFRS": "AssetsIFRS"}},
}


@pytest.fixture()
def standard_book(monkeypatch):
    def use(entries: dict | None = None) -> None:
        got = book(**(BOOK if entries is None else entries))
        monkeypatch.setattr(fst, "_standard_book", lambda: got)
    return use


def apply(rows: list[fst.Fact], statement: str = "BS", standard: str | None = "JPGAAP",
          learned: list[fst.Learned] | None = None) -> list[tuple[str | None, str | None]]:
    got = fst.apply_learned(rows, learned or [], statement, END, "Q", fst.Names(), standard)
    return [(None, None) if m is None else (m.key, m.by) for _, m in got]


def test_without_a_dictionary_nothing_changes(standard_book) -> None:
    standard_book({})
    rows = [pdf_row(1, "現金及び預金", 5), pdf_row(2, "流動資産合計", 9)]
    assert apply(rows) == [(None, None), (None, None)]
    assert apply(rows, standard=None) == [(None, None), (None, None)]


def test_rows_without_history_take_the_standard_element(standard_book) -> None:
    standard_book()
    rows = [pdf_row(1, "現金及び預金", 5), pdf_row(2, "流動資産合計", 9), pdf_row(3, "資産合計", 20)]
    assert apply(rows) == [("CashAndDeposits", "standard"), ("CurrentAssets", "standard"), ("Assets", "standard")]


def test_dictionary_of_another_standard_or_unknown_standard_is_not_used(standard_book) -> None:
    standard_book()
    rows = [pdf_row(1, "現金及び預金", 5), pdf_row(2, "資産合計", 20)]
    assert apply(rows, standard="IFRS") == [(None, None), ("AssetsIFRS", "standard")]
    assert apply(rows, standard=None) == [(None, None), (None, None)]


def test_section_picks_the_element_and_other_sections_are_refused(standard_book) -> None:
    standard_book()
    rows = [pdf_row(1, "リース債務", 3), pdf_row(2, "流動負債合計", 10), pdf_row(3, "リース債務", 4),
            pdf_row(4, "固定負債合計", 12)]
    assert [k for k, _ in apply(rows)] == ["LeaseObligationsCL", "CurrentLiabilities", "LeaseObligationsNCL",
                                           "NoncurrentLiabilities"]
    pl = [pdf_row(1, "受取和解金", 3), pdf_row(2, "営業外収益合計", 10)]
    assert [k for k, _ in apply(pl, "PL")] == [None, "NonOperatingIncome"]


def test_two_rows_on_one_element_are_both_dropped(standard_book) -> None:
    standard_book()
    rows = [pdf_row(1, "未収入金", 3), pdf_row(2, "未収金", 4), pdf_row(3, "流動資産合計", 10)]
    assert [k for k, _ in apply(rows)] == [None, None, "CurrentAssets"]


def test_units_must_agree(standard_book) -> None:
    standard_book()
    assert apply([pdf_row(1, "1株当たり純資産", 900, unit="JPYPerShares")]) == [(None, None)]
    standard_book({"JPGAAP__BS__1株当たり純資産": {"any": "BasicEarningsLossPerShare", "home": "-", "within": {}}})
    assert apply([pdf_row(1, "1株当たり純資産", 900, unit="JPYPerShares")]) == [("BasicEarningsLossPerShare", "standard")]
    assert apply([pdf_row(1, "1株当たり純資産", 900)]) == [(None, None)]


def test_rows_matched_by_history_keep_their_element_and_its_element_is_not_reused(standard_book) -> None:
    standard_book()
    x = xbrl_filing(1, "BS", [xfact(1, "ext:CashOnHand", "手許現金", 50), xfact(2, "CurrentAssets", "流動資産", 90)])
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                     submitted="2025-05-10", standard="JPGAAP")
    pdf.by_statement = {"BS": [pdf_row(1, "現金及び預金", 50), pdf_row(2, "流動資産合計", 90)]}
    learned = [fst.learn_pair(pdf, x)]
    rows = [pdf_row(3, "現金及び預金", 60), pdf_row(4, "流動資産合計", 95), pdf_row(5, "流動資産", 1)]
    assert apply(rows, learned=learned) == [("ext:CashOnHand", "value"), ("CurrentAssets", "value"), (None, None)]


def cf_company(n: int, *rows: tuple[str, str]) -> list[fst.Filing]:
    facts = [xfact(i, key, label, instant=False) for i, (key, label) in enumerate(rows, 1)]
    return [xbrl_filing(n, "CF", facts)]


def test_others_are_listed_by_section_and_subtotal() -> None:
    companies = {}
    for i in range(5):
        bs = bs_company(i, ("OtherCA", "その他"), ("CurrentAssets", "流動資産"), ("OtherIOA", "その他"),
                        ("InvestmentsAndOtherAssets", "投資その他の資産"))[0]
        cf = cf_company(i, ("OtherNetOpeCF", "その他"), ("SubtotalOpeCF", "小計"), ("OtherNetOpeCFSubtotal", "その他"),
                        ("NetCashProvidedByUsedInOperatingActivities", "営業活動によるキャッシュ・フロー"))[0]
        companies[f"{i:04d}0"] = [bs, cf]
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    assert book["others"]["JPGAAP"]["BS"] == {"CurrentAssets": "OtherCA", "InvestmentsAndOtherAssets": "OtherIOA"}
    assert book["others"]["JPGAAP"]["CF"] == {
        "NetCashProvidedByUsedInOperatingActivities": "OtherNetOpeCFSubtotal",
        "NetCashProvidedByUsedInOperatingActivities:小計まで": "OtherNetOpeCF",
    }
    assert "その他" not in labels_of(book)
    assert ld.build(seen_of(companies), min_codes=5, min_share=0.95, drop="00000")["others"] == {}


def test_others_twice_in_a_section_or_split_between_elements_are_not_listed() -> None:
    companies = {f"{i:04d}0": bs_company(i, ("OtherCA", "その他"), ("ext:OtherCA2", "その他"), ("CurrentAssets", "流動資産"))
                 for i in range(5)}
    companies.update({f"{i:04d}1": bs_company(i, ("OtherCL", "その他"), ("CurrentLiabilities", "流動負債"))
                      for i in range(5)})
    companies.update({f"{i:04d}2": bs_company(i, ("ext:OtherCL", "その他"), ("CurrentLiabilities", "流動負債"))
                      for i in range(5)})
    companies.update({f"{i:04d}3": bs_company(i, ("OtherCLX", "その他"), ("CurrentLiabilities", "流動負債"))
                      for i in range(2)})
    assert ld.build(seen_of(companies), min_codes=5, min_share=0.95)["others"] == {}


def test_other_before_accumulated_depreciation_is_its_own_place() -> None:
    companies = {}
    for i in range(5):
        companies[f"{i:04d}0"] = bs_company(
            i, ("OtherPPE", "その他"), ("AccumulatedDepreciationOtherPPE", "減価償却累計額"), ("OtherNetPPE", "その他（純額）"),
            ("PropertyPlantAndEquipment", "有形固定資産"))
        companies[f"{i:04d}1"] = bs_company(i, ("OtherNetPPE", "その他"), ("PropertyPlantAndEquipment", "有形固定資産"))
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    assert book["others"]["JPGAAP"]["BS"] == {"PropertyPlantAndEquipment": "OtherNetPPE",
                                              "PropertyPlantAndEquipment:累計額の前": "OtherPPE"}


def test_accumulated_rows_are_listed_by_the_asset_above_them() -> None:
    companies = {}
    for i in range(5):
        companies[f"{i:04d}0"] = bs_company(
            i, ("Buildings", "建物"), ("AccumulatedDepreciationBuildings", "減価償却累計額"),
            ("AccumulatedImpairmentLossBuildings", "減損損失累計額"), ("BuildingsNet", "建物（純額）"),
            ("ext:Rental", "賃貸資産"), ("ext:AccumulatedDepreciationRental", "減価償却累計額"), ("ext:RentalNet", "賃貸資産（純額）"),
            ("Land", "土地"), ("AccumulatedDepreciationPPEByGroup", "減価償却累計額"),
            ("PropertyPlantAndEquipment", "有形固定資産"))
    book = ld.build(seen_of(companies), min_codes=5, min_share=0.95)
    assert book["accumulated"] == {"JPGAAP": {
        "減価償却累計額": {"Buildings": "AccumulatedDepreciationBuildings"},
        "減損損失累計額": {"Buildings": "AccumulatedImpairmentLossBuildings"},
    }}
    assert not any("累計額" in name for name in labels_of(book))


def test_dictionary_without_new_parts_is_read_as_empty() -> None:
    got = fst.standard_book({"labels": {}, "per_share": []})
    assert got.others == {} and got.accumulated == {}
    raw = json.loads(XBRL_STANDARD_LABELS_PATH.read_text(encoding="utf-8"))
    fst.standard_book({k: v for k, v in raw.items() if k not in ("others", "accumulated")})


def full_book(others: dict | None = None, accumulated: dict | None = None, **labels: dict) -> fst.StandardBook:
    raw: dict = {"labels": {}, "per_share": [], "others": others or {}, "accumulated": accumulated or {}}
    for spec, item in labels.items():
        standard, statement, name = spec.split("__")
        raw["labels"].setdefault(standard, {}).setdefault(statement, {})[name] = item
    return fst.standard_book(raw)


@pytest.fixture()
def use_book(monkeypatch):
    def use(got: fst.StandardBook) -> None:
        monkeypatch.setattr(fst, "_standard_book", lambda: got)
    return use


def section(key: str) -> dict:
    return {"any": key, "home": key, "within": {key: key}}


OTHERS = {"JPGAAP": {
    "BS": {"CurrentAssets": "OtherCA", "CurrentLiabilities": "OtherCL", "PropertyPlantAndEquipment:累計額の前": "OtherPPE"},
    "CF": {"NetCashProvidedByUsedInOperatingActivities": "OtherNetOpeCFSubtotal",
           "NetCashProvidedByUsedInOperatingActivities:小計まで": "OtherNetOpeCF",
           "NetCashProvidedByUsedInInvestmentActivities": "OtherNetInvCF"},
}}
SECTIONS = {
    "JPGAAP__BS__流動資産": section("CurrentAssets"), "JPGAAP__BS__流動負債": section("CurrentLiabilities"),
    "JPGAAP__BS__有形固定資産": section("PropertyPlantAndEquipment"),
    "JPGAAP__CF__営業活動によるキャッシュ・フロー": section("NetCashProvidedByUsedInOperatingActivities"),
    "JPGAAP__CF__投資活動によるキャッシュ・フロー": section("NetCashProvidedByUsedInInvestmentActivities"),
    "JPGAAP__CF__小計": {"any": "SubtotalOpeCF", "home": "NetCashProvidedByUsedInOperatingActivities",
                         "within": {"NetCashProvidedByUsedInOperatingActivities": "SubtotalOpeCF"}},
}


def apply_with(rows: list[fst.Fact], statement: str = "BS", names: fst.Names | None = None,
               learned: list[fst.Learned] | None = None) -> list[str | None]:
    got = fst.apply_learned(rows, learned or [], statement, END, "Q", names or fst.Names(), "JPGAAP")
    return [None if m is None else m.key for _, m in got]


def test_other_takes_the_element_of_its_section(use_book) -> None:
    use_book(full_book(OTHERS, **SECTIONS))
    rows = [pdf_row(1, "現金及び預金", 5), pdf_row(2, "その他", 1), pdf_row(3, "流動資産合計", 6),
            pdf_row(4, "その他", 2), pdf_row(5, "流動負債合計", 3)]
    assert apply_with(rows) == [None, "OtherCA", "CurrentAssets", "OtherCL", "CurrentLiabilities"]
    got = fst.apply_learned(rows, [], "BS", END, "Q", fst.Names(), "JPGAAP")
    assert {m.by for _, m in got if m is not None} == {"standard"}


def test_other_without_section_or_twice_in_a_section_is_left(use_book) -> None:
    use_book(full_book(OTHERS, **SECTIONS))
    assert apply_with([pdf_row(1, "その他", 1), pdf_row(2, "未収入金", 2)]) == [None, None]
    rows = [pdf_row(1, "その他", 1), pdf_row(2, "その他", 2), pdf_row(3, "流動資産合計", 6)]
    assert apply_with(rows) == [None, None, "CurrentAssets"]


def test_other_in_operating_cash_flow_needs_the_subtotal(use_book) -> None:
    use_book(full_book(OTHERS, **SECTIONS))
    rows = [pdf_row(1, "その他", 1), pdf_row(2, "小計", 3), pdf_row(3, "その他", 2),
            pdf_row(4, "営業活動によるキャッシュ・フロー", 5), pdf_row(5, "その他", 4),
            pdf_row(6, "投資活動によるキャッシュ・フロー", 7)]
    assert apply_with(rows, "CF") == ["OtherNetOpeCF", "SubtotalOpeCF", "OtherNetOpeCFSubtotal",
                                      "NetCashProvidedByUsedInOperatingActivities", "OtherNetInvCF",
                                      "NetCashProvidedByUsedInInvestmentActivities"]
    no_subtotal = [pdf_row(1, "その他", 1), pdf_row(2, "営業活動によるキャッシュ・フロー", 5)]
    assert apply_with(no_subtotal, "CF") == [None, "NetCashProvidedByUsedInOperatingActivities"]


def test_other_of_the_company_comes_before_the_dictionary(use_book) -> None:
    use_book(full_book(OTHERS, **SECTIONS))
    names = fst.Names(others={"CurrentAssets": "ext:OtherCurrent"})
    rows = [pdf_row(1, "その他", 1), pdf_row(2, "流動資産合計", 6)]
    assert apply_with(rows, names=names) == ["ext:OtherCurrent", "CurrentAssets"]


def test_other_before_accumulated_depreciation_is_gross(use_book) -> None:
    use_book(full_book(OTHERS, {"JPGAAP": {"減価償却累計額": {"OtherPPE": "AccumulatedDepreciationOtherPPE"}}},
                       **SECTIONS))
    rows = [pdf_row(1, "その他", 9), pdf_row(2, "減価償却累計額", -4), pdf_row(3, "その他(純額)", 5),
            pdf_row(4, "有形固定資産合計", 5)]
    assert apply_with(rows) == ["OtherPPE", "AccumulatedDepreciationOtherPPE", None, "PropertyPlantAndEquipment"]
    assert apply_with([pdf_row(1, "その他", 5), pdf_row(2, "有形固定資産合計", 5)]) == [None, "PropertyPlantAndEquipment"]


ACCUMULATED = {"JPGAAP": {"減価償却累計額": {"Buildings": "AccumulatedDepreciationBuildings"},
                          "減損損失累計額": {"Buildings": "AccumulatedImpairmentLossBuildings"}}}


def history(rows: list[tuple[str, str, float]], statement: str = "BS") -> list[fst.Learned]:
    x = xbrl_filing(1, statement, [xfact(i, key, key, value) for i, (_, key, value) in enumerate(rows, 1)])
    pdf = fst.Filing(id=2, doc_id="P", source="tdnet_pdf", period_type="FY", period_end=date(2025, 3, 31),
                     submitted="2025-05-10", standard="JPGAAP")
    pdf.by_statement = {statement: [pdf_row(i, name, value) for i, (name, _, value) in enumerate(rows, 1)]}
    return [fst.learn_pair(pdf, x)]


def test_accumulated_rows_follow_the_asset_above(use_book) -> None:
    use_book(full_book(None, ACCUMULATED))
    learned = history([("建物", "Buildings", 100), ("建物(純額)", "BuildingsNet", 60)])
    rows = [pdf_row(1, "建物", 110), pdf_row(2, "減価償却累計額", -30), pdf_row(3, "減損損失累計額", -5),
            pdf_row(4, "建物(純額)", 75)]
    assert apply_with(rows, learned=learned) == [
        "Buildings", "AccumulatedDepreciationBuildings", "AccumulatedImpairmentLossBuildings", "BuildingsNet"]


def test_accumulated_rows_are_left_without_the_asset_or_the_net_row(use_book) -> None:
    use_book(full_book(None, ACCUMULATED))
    rows = [pdf_row(1, "建物", 110), pdf_row(2, "減価償却累計額", -30), pdf_row(3, "建物(純額)", 80)]
    assert apply_with(rows) == [None, None, None]
    learned = history([("建物", "Buildings", 100), ("土地", "Land", 60)])
    rows = [pdf_row(1, "建物", 110), pdf_row(2, "減価償却累計額", -30), pdf_row(3, "土地", 70)]
    assert apply_with(rows, learned=learned) == ["Buildings", None, "Land"]


def test_accumulated_rows_of_company_elements_use_the_company_history(use_book) -> None:
    use_book(full_book(None, ACCUMULATED))
    learned = history([("賃貸資産", "ext:Rental", 100), ("賃貸資産(純額)", "ext:RentalNet", 60)])
    names = fst.Names(accumulated={("減価償却累計額", "ext:Rental"): "ext:AccumulatedDepreciationRental"})
    rows = [pdf_row(1, "賃貸資産", 110), pdf_row(2, "減価償却累計額", -30), pdf_row(3, "賃貸資産(純額)", 80)]
    assert apply_with(rows, learned=learned) == ["ext:Rental", None, "ext:RentalNet"]
    assert apply_with(rows, names=names, learned=learned) == [
        "ext:Rental", "ext:AccumulatedDepreciationRental", "ext:RentalNet"]


def test_own_history_of_others_and_accumulated_rows_comes_from_the_xbrl() -> None:
    facts = [xfact(1, "OtherCA", "その他"), xfact(2, "CurrentAssets", "流動資産"), xfact(3, "ext:Rental", "賃貸資産"),
             xfact(4, "ext:AccumulatedDepreciationRental", "減価償却累計額"), xfact(5, "ext:RentalNet", "賃貸資産（純額）")]
    names = fst.name_index([xbrl_filing(1, "BS", facts)], "BS")
    assert names.others == {"CurrentAssets": "OtherCA"}
    assert names.accumulated == {("減価償却累計額", "ext:Rental"): "ext:AccumulatedDepreciationRental"}


CASH = {"JPGAAP__CF__現金及び現金同等物の残高": {"any": "CashAndCashEquivalents", "home": "-",
                                                  "within": {"-": "CashAndCashEquivalents"}}}


def test_closing_cash_takes_the_cash_element_and_opening_cash_is_left(use_book) -> None:
    use_book(full_book(**CASH))
    rows = [pdf_row(1, "現金及び現金同等物の期首残高", 10), pdf_row(2, "現金及び現金同等物の期末残高", 12)]
    assert apply_with(rows, "CF") == [None, "CashAndCashEquivalents"]
    assert apply_with([pdf_row(1, "現金及び現金同等物の四半期末残高", 12)], "CF") == ["CashAndCashEquivalents"]
    names = fst.Names({"現金及び現金同等物の残高": "ext:CashEnd"})
    assert apply_with([pdf_row(1, "現金及び現金同等物の期末残高", 12)], "CF", names) == ["ext:CashEnd"]


EQUITY = {
    "JPGAAP__BS__資本準備金": {"any": "LegalCapitalSurplus", "home": "ShareholdersEquity",
                               "within": {"ShareholdersEquity": "LegalCapitalSurplus"}},
    "JPGAAP__BS__繰越利益剰余金": {"any": "RetainedEarningsBroughtForward", "home": "ShareholdersEquity",
                                   "within": {"ShareholdersEquity": "RetainedEarningsBroughtForward"}},
    "JPGAAP__BS__株主資本": section("ShareholdersEquity"),
    "JPGAAP__BS__評価・換算差額等": section("ValuationAndTranslationAdjustments"),
    "JPGAAP__BS__その他有価証券評価差額金": {"any": "ValuationDifferenceOnAvailableForSaleSecurities",
                                            "home": "ValuationAndTranslationAdjustments",
                                            "within": {"ValuationAndTranslationAdjustments":
                                                       "ValuationDifferenceOnAvailableForSaleSecurities"}},
    "JPGAAP__BS__純資産": section("NetAssets"),
}


def test_equity_rows_with_heads_drop_the_heads(use_book) -> None:
    use_book(full_book(**EQUITY))
    rows = [pdf_row(1, "資本剰余金資本準備金", 3), pdf_row(2, "利益剰余金その他利益剰余金繰越利益剰余金", 4),
            pdf_row(3, "利益剰余金合計", 4), pdf_row(4, "株主資本合計", 7)]
    assert apply_with(rows) == ["LegalCapitalSurplus", "RetainedEarningsBroughtForward", None, "ShareholdersEquity"]


def test_accumulated_other_comprehensive_income_takes_the_valuation_total(use_book) -> None:
    use_book(full_book(**EQUITY))
    rows = [pdf_row(1, "その他の包括利益累計額その他有価証券評価差額金", 3), pdf_row(2, "その他の包括利益累計額合計", 3),
            pdf_row(3, "純資産合計", 10)]
    assert apply_with(rows) == ["ValuationDifferenceOnAvailableForSaleSecurities", "ValuationAndTranslationAdjustments",
                                "NetAssets"]


def test_new_rules_do_not_move_rows_matched_by_history(use_book) -> None:
    use_book(full_book(OTHERS, ACCUMULATED, **SECTIONS))
    learned = history([("その他", "ext:OtherCurrentAssets", 1), ("流動資産合計", "CurrentAssets", 6),
                       ("建物", "Buildings", 100), ("減価償却累計額", "ext:Depreciation", -30),
                       ("建物(純額)", "BuildingsNet", 70)])
    rows = [pdf_row(1, "その他", 2), pdf_row(2, "流動資産合計", 7), pdf_row(3, "建物", 110),
            pdf_row(4, "減価償却累計額", -35), pdf_row(5, "建物(純額)", 75)]
    assert apply_with(rows, learned=learned) == ["ext:OtherCurrentAssets", "CurrentAssets", "Buildings",
                                                 "ext:Depreciation", "BuildingsNet"]
    taken = [pdf_row(1, "未収入金", 1), pdf_row(2, "その他", 2), pdf_row(3, "流動資産合計", 7)]
    learned = history([("未収入金", "OtherCA", 1), ("流動資産合計", "CurrentAssets", 6)])
    assert apply_with(taken, learned=learned) == ["OtherCA", None, "CurrentAssets"]


def test_net_only_history_does_not_take_the_gross_row_when_the_breakdown_is_printed(use_book) -> None:
    use_book(full_book(None, ACCUMULATED))
    learned = history([("土地", "Land", 50), ("使用権資産", "RightOfUseAssetsNet", 39)])
    facts = [xfact(1, "Land", "土地"), xfact(2, "RightOfUseAssets", "使用権資産"),
             xfact(3, "AccumulatedDepreciationRightOfUseAssets", "減価償却累計額"),
             xfact(4, "RightOfUseAssetsNet", "使用権資産（純額）")]
    names = fst.name_index([xbrl_filing(1, "BS", facts)], "BS")
    rows = [pdf_row(1, "土地", 50), pdf_row(2, "使用権資産", 55), pdf_row(3, "減価償却累計額", -17),
            pdf_row(4, "使用権資産(純額)", 38)]
    assert apply_with(rows, names=names, learned=learned) == [
        "Land", "RightOfUseAssets", "AccumulatedDepreciationRightOfUseAssets", "RightOfUseAssetsNet"]
    assert apply_with([pdf_row(1, "土地", 50), pdf_row(2, "使用権資産", 39)], names=names, learned=learned) == [
        "Land", "RightOfUseAssetsNet"]


def test_learned_pdf_name_does_not_take_the_label_of_another_element() -> None:
    facts = [xfact(1, "RightOfUseAssets", "使用権資産"), xfact(2, "RightOfUseAssetsNet", "使用権資産（純額）"),
             xfact(3, "BuildingsAndStructures", "建物及び構築物")]
    learned = history([("使用権資産", "RightOfUseAssetsNet", 39), ("建物等", "BuildingsAndStructures", 50)])
    labels = fst._labels([xbrl_filing(1, "BS", facts)], learned)
    assert labels["RightOfUseAssetsNet"] == "使用権資産(純額)"
    assert labels["BuildingsAndStructures"] == "建物等"

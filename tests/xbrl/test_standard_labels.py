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

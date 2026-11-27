from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.config import XBRL_ROW_MERGE_EXCLUSIONS_PATH, XBRL_ROW_PLACE_FIXES_PATH
from app.db import SessionLocal
from app.xbrl import three_statements as fst
from tests.xbrl.test_three_statements import NONCONSOL, M, Seeder, _tree_items, x

ALLOWANCE_INSIDE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    InvestmentsAndOtherAssets
      InvestmentSecurities
      OtherIOA
      AllowanceForDoubtfulAccountsIOAByGroup
    NoncurrentAssets
  Assets
"""
ALLOWANCE_OUTSIDE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    InvestmentsAndOtherAssets
      InvestmentSecurities
      OtherIOA
    AllowanceForDoubtfulAccountsIOAByGroup
    NoncurrentAssets
  Assets
"""
TAX_ON_TOP = """
AssetsAbstract=資産の部
  ReinsuranceAccountsReceivableAssetsINS
  OtherAssetsAssetsINS
    AccountsReceivableAssetsINS
    PrepaidExpenses
    AccruedIncome
    CashSegregatedAsDepositsAssetsINS
    SuspensePaymentsAssetsINS
  DeferredTaxAssets
  Assets
"""
TAX_UNDER_OTHER = """
AssetsAbstract=資産の部
  ReinsuranceAccountsReceivableAssetsINS
  OtherAssetsAssetsINS
    AccountsReceivableAssetsINS
    PrepaidExpenses
    AccruedIncome
    CashSegregatedAsDepositsAssetsINS
    SuspensePaymentsAssetsINS
    DeferredTaxAssets
  Assets
"""
IA_ONE_LINE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    IntangibleAssets
    InvestmentsAndOtherAssetsAbstract=投資その他の資産
{ioa}
      InvestmentsAndOtherAssets
    NoncurrentAssets
  Assets
"""
IA_SPLIT = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    IntangibleAssetsAbstract=無形固定資産
      {ia}
      OtherIA
      IntangibleAssets
    InvestmentsAndOtherAssetsAbstract=投資その他の資産
      InvestmentSecurities
      OtherIOA
      InvestmentsAndOtherAssets
    NoncurrentAssets
  Assets
"""
RELOCATION_NCL = """
LiabilitiesAbstract=負債の部
  CurrentLiabilitiesAbstract=流動負債
    AccountsPayableOther
    CurrentLiabilities
  NoncurrentLiabilitiesAbstract=固定負債
    ext:AllowanceForLossOfTransferringHeadquarterNCL
    NoncurrentLiabilities
  Liabilities
"""
RELOCATION_CL = """
LiabilitiesAbstract=負債の部
  CurrentLiabilitiesAbstract=流動負債
    AccountsPayableOther
    ext:{key}
    CurrentLiabilities
  Liabilities
"""
OWN_USE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
      ext:PropertyPlantAndEquipmentForOwnCompanyAbstract=自社有形固定資産
        ext:LandForOwnUse
        ext:MachineryEquipmentAndVehiclesForOwnUse
          ext:AccumulatedDepreciationMachineryEquipmentAndVehiclesForOwnUse
        ext:MachineryEquipmentAndVehiclesForOwnUseNet
        ext:LeaseAssetsForOwnUse
          ext:AccumulatedDepreciationLeaseAssetsForOwnUse
        ext:LeaseAssetsForOwnUseNet
        ext:PropertyPlantAndEquipmentForOwnCompany
      PropertyPlantAndEquipment
    NoncurrentAssets
  Assets
"""
STANDARD_PPE = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
      Land
      MachineryEquipmentAndVehicles
        AccumulatedDepreciationMachineryEquipmentAndVehicles
      MachineryEquipmentAndVehiclesNet
      LeaseAssetsPPE
        AccumulatedDepreciationLeaseAssetsPPE
      LeaseAssetsNetPPE
      PropertyPlantAndEquipment
    NoncurrentAssets
  Assets
"""
RAILWAY_HEAD = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    {head}=事業固定資産
      {gross}
        {acc}
      {net}
      {ia}
      {total}
    NoncurrentAssets
  Assets
"""
LABELS = {
    "AllowanceForDoubtfulAccountsIOAByGroup": "貸倒引当金", "OtherIOA": "その他", "OtherIA": "その他",
    "InvestmentsAndOtherAssets": "投資その他の資産", "IntangibleAssets": "無形固定資産", "Goodwill": "のれん",
    "AllowanceForLossOfTransferringHeadquarterNCL": "本社移転損失引当金",
    "ProvisionForHeadOfficeRelocationExpenseCL": "本社移転損失引当金",
    "MachineryEquipmentAndVehiclesForOwnUse": "機械装置及び運搬具", "MachineryEquipmentAndVehicles": "機械装置及び運搬具",
    "MachineryEquipmentAndVehiclesForOwnUseNet": "機械装置及び運搬具（純額）",
    "MachineryEquipmentAndVehiclesNet": "機械装置及び運搬具（純額）",
    "LeaseAssetsForOwnUse": "リース資産", "LeaseAssetsPPE": "リース資産",
    "LeaseAssetsForOwnUseNet": "リース資産（純額）", "LeaseAssetsNetPPE": "リース資産（純額）",
    "AccumulatedDepreciationMachineryEquipmentAndVehiclesForOwnUse": "減価償却累計額",
    "AccumulatedDepreciationLeaseAssetsForOwnUse": "減価償却累計額",
    "AccumulatedDepreciationMachineryEquipmentAndVehicles": "減価償却累計額",
    "AccumulatedDepreciationLeaseAssetsPPE": "減価償却累計額",
    "PropertyPlantAndEquipmentIncidentalRWY": "有形固定資産", "PropertyRealEstateAndLeisureFixedAsset": "有形固定資産",
    "PropertyPlantAndEquipmentNetIncidentalRWY": "有形固定資産（純額）",
    "PropertyPlantAndEquipmentNetProperty": "有形固定資産（純額）",
    "IntangibleAssetsIncidentalRWY": "無形固定資産", "IntangibleAssetsRealEstateAndLeisureFixedAsset": "無形固定資産",
    "PropertyPlantAndEquipmentOtherRWY": "有形固定資産", "PropertyPlantAndEquipmentNCALivingServicesRWY": "有形固定資産",
    "PropertyPlantAndEquipmentNetNCAOther": "有形固定資産（純額）",
    "PropertyPlantAndEquipmentNetNCALivingServicesRWY": "有形固定資産（純額）",
    "IntangibleAssetsOtherRWY": "無形固定資産", "IntangibleAssetsNCALivingServicesRWY": "無形固定資産",
    "AccumulatedDepreciationIncidentalRWY": "減価償却累計額", "AccumulatedDepreciationOtherRWY": "減価償却累計額",
    "AccumulatedDepreciationNCALivingServicesRWY": "減価償却累計額",
}
CUSTOM = "x"


def ext(local: str, kind: str, value: float) -> tuple:
    return x(local, kind, value, CUSTOM)


def _filing(s: Seeder, fid: int, doc: str, code: str, period_type: str, end: str, submitted: str, rows: list,
            tree: str, *, consolidated: bool = True) -> None:
    s.filing(fid, "edinet", period_type, end, submitted, code=code)
    s.db.execute(text("UPDATE xbrl_filings SET doc_id = :d WHERE id = :id"), {"d": doc, "id": fid})
    s.facts(fid, end, rows, consolidated=consolidated, dims=None if consolidated else NONCONSOL)
    s.roles(fid, [("rol_BalanceSheet", "BS", consolidated, False, _tree_items(tree))])


def _allowance(s: Seeder) -> None:
    s.stock("87060", "最新の書類だけ貸倒引当金を投資その他の資産の外に宣言")
    _filing(s, 5911, "S100Q1KY", "87060", "Q1", "2026-06-30", "2026-08-10 15:00:00", [
        x("InvestmentsAndOtherAssets", "i", 27835), x("InvestmentSecurities", "i", 26255), x("OtherIOA", "i", 1651),
        x("AllowanceForDoubtfulAccountsIOAByGroup", "i", -71), x("NoncurrentAssets", "i", 27835), x("Assets", "i", 27835),
    ], ALLOWANCE_INSIDE)
    _filing(s, 5912, "S100ZGH9", "87060", "Q2", "2026-09-30", "2026-11-13 15:00:00", [
        x("InvestmentsAndOtherAssets", "i", 28622), x("InvestmentSecurities", "i", 27027), x("OtherIOA", "i", 1666),
        x("AllowanceForDoubtfulAccountsIOAByGroup", "i", -71), x("NoncurrentAssets", "i", 28622), x("Assets", "i", 28622),
    ], ALLOWANCE_OUTSIDE)


def _insurer_tax(s: Seeder) -> None:
    s.stock("71570", "半期報告書だけ繰延税金資産をその他資産の子に宣言")
    _filing(s, 5921, "S100YC7R", "71570", "FY", "2026-03-31", "2026-06-16 15:00:00", [
        x("ReinsuranceAccountsReceivableAssetsINS", "i", 8372), x("OtherAssetsAssetsINS", "i", 2585),
        x("AccountsReceivableAssetsINS", "i", 1886), x("PrepaidExpenses", "i", 237), x("AccruedIncome", "i", 162),
        x("CashSegregatedAsDepositsAssetsINS", "i", 296), x("SuspensePaymentsAssetsINS", "i", 2),
        x("DeferredTaxAssets", "i", 1056), x("Assets", "i", 12013),
    ], TAX_ON_TOP, consolidated=False)
    _filing(s, 5922, "S100ZFJD", "71570", "Q2", "2026-09-30", "2026-11-12 15:00:00", [
        x("ReinsuranceAccountsReceivableAssetsINS", "i", 10250), x("OtherAssetsAssetsINS", "i", 1701),
        x("AccountsReceivableAssetsINS", "i", 965), x("PrepaidExpenses", "i", 257), x("AccruedIncome", "i", 178),
        x("CashSegregatedAsDepositsAssetsINS", "i", 296), x("SuspensePaymentsAssetsINS", "i", 3),
        x("DeferredTaxAssets", "i", 1187), x("Assets", "i", 13138),
    ], TAX_UNDER_OTHER, consolidated=False)


def _ioa_tree(*keys: str) -> str:
    return IA_ONE_LINE.format(ioa="\n".join(f"      {k}" for k in keys))


def _other_ia(s: Seeder) -> None:
    s.stock("18660", "第1四半期だけ投資その他の資産のその他を OtherIA で付けた")
    _filing(s, 5931, "S100GP70", "18660", "Q1", "2019-06-30", "2019-08-08 14:24:00", [
        x("IntangibleAssets", "i", 256.501), x("InvestmentSecurities", "i", 6133.829),
        x("LongTermLoansReceivable", "i", 52.383), x("NetDefinedBenefitAsset", "i", 601.953),
        x("DeferredTaxAssets", "i", 46.265), x("OtherIA", "i", 1577.741),
        x("AllowanceForDoubtfulAccountsIOAByGroup", "i", -33.856), x("InvestmentsAndOtherAssets", "i", 8378.315),
        x("NoncurrentAssets", "i", 8634.816), x("Assets", "i", 8634.816),
    ], _ioa_tree("InvestmentSecurities", "LongTermLoansReceivable", "NetDefinedBenefitAsset", "DeferredTaxAssets",
                 "OtherIA", "AllowanceForDoubtfulAccountsIOAByGroup"))
    _filing(s, 5932, "S100Y1866", "18660", "FY", "2026-03-31", "2026-06-26 15:00:00", [
        x("Software", "i", 100), x("OtherIA", "i", 20), x("IntangibleAssets", "i", 120),
        x("InvestmentSecurities", "i", 7000), x("OtherIOA", "i", 1500), x("InvestmentsAndOtherAssets", "i", 8500),
        x("NoncurrentAssets", "i", 8620), x("Assets", "i", 8620),
    ], IA_SPLIT.format(ia="Software"))


def _goodwill(s: Seeder) -> None:
    s.stock("43950", "第3四半期だけのれんを投資その他の資産の中に印字")
    _filing(s, 5941, "S100MV45", "43950", "Q3", "2021-09-30", "2021-11-12 15:24:00", [
        x("IntangibleAssets", "i", 54.008), x("Goodwill", "i", 299.367),
        x("ClaimsProvableInBankruptcyClaimsProvableInRehabilitationAndOther", "i", 0.162), x("OtherIOA", "i", 63.071),
        x("AllowanceForDoubtfulAccountsIOAByGroup", "i", -0.162), x("InvestmentsAndOtherAssets", "i", 362.438),
        x("NoncurrentAssets", "i", 416.446), x("Assets", "i", 416.446),
    ], _ioa_tree("Goodwill", "ClaimsProvableInBankruptcyClaimsProvableInRehabilitationAndOther", "OtherIOA",
                 "AllowanceForDoubtfulAccountsIOAByGroup"))
    _filing(s, 5942, "S100O1BC", "43950", "FY", "2022-03-31", "2022-03-25 15:00:00", [
        x("Goodwill", "i", 236.187), x("OtherIA", "i", 311.518), x("IntangibleAssets", "i", 547.705),
        x("InvestmentSecurities", "i", 0), x("OtherIOA", "i", 52.871), x("InvestmentsAndOtherAssets", "i", 52.871),
        x("NoncurrentAssets", "i", 600.576), x("Assets", "i", 600.576),
    ], IA_SPLIT.format(ia="Goodwill"))


def _relocation(s: Seeder) -> None:
    s.stock("60370", "本社移転損失引当金の要素が固定負債から流動負債へ")
    _filing(s, 5951, "S1009VPY", "60370", "Q2", "2017-01-31", "2017-03-14 15:06:00", [
        x("AccountsPayableOther", "i", 36.2), x("CurrentLiabilities", "i", 36.2),
        ext("AllowanceForLossOfTransferringHeadquarterNCL", "i", 17.884), x("NoncurrentLiabilities", "i", 17.884),
        x("Liabilities", "i", 54.084),
    ], RELOCATION_NCL, consolidated=False)
    _filing(s, 5952, "S100KYAS", "60370", "Q2", "2021-01-31", "2021-03-12 15:00:00", [
        x("AccountsPayableOther", "i", 50), ext("AllowanceForLossOfTransferringHeadquarterNCL", "i", 32),
        x("CurrentLiabilities", "i", 82), x("Liabilities", "i", 82),
    ], RELOCATION_CL.format(key="AllowanceForLossOfTransferringHeadquarterNCL"), consolidated=False)
    _filing(s, 5953, "S100R6037", "60370", "Q2", "2023-01-31", "2023-03-14 15:00:00", [
        x("AccountsPayableOther", "i", 40), ext("ProvisionForHeadOfficeRelocationExpenseCL", "i", 10),
        x("CurrentLiabilities", "i", 50), x("Liabilities", "i", 50),
    ], RELOCATION_CL.format(key="ProvisionForHeadOfficeRelocationExpenseCL"), consolidated=False)


def _own_use(s: Seeder) -> None:
    s.stock("76010", "自社有形固定資産の科目を有形固定資産の直下の要素に替えた")
    _filing(s, 5961, "S100A7601", "76010", "FY", "2017-02-28", "2017-05-26 15:00:00", [
        ext("LandForOwnUse", "i", 1870.7), ext("MachineryEquipmentAndVehiclesForOwnUse", "i", 400.6),
        ext("AccumulatedDepreciationMachineryEquipmentAndVehiclesForOwnUse", "i", -388.4),
        ext("MachineryEquipmentAndVehiclesForOwnUseNet", "i", 12.2), ext("LeaseAssetsForOwnUse", "i", 1782.6),
        ext("AccumulatedDepreciationLeaseAssetsForOwnUse", "i", -1133.0), ext("LeaseAssetsForOwnUseNet", "i", 649.6),
        ext("PropertyPlantAndEquipmentForOwnCompany", "i", 2532.5), x("PropertyPlantAndEquipment", "i", 2532.5),
        x("NoncurrentAssets", "i", 2532.5), x("Assets", "i", 2532.5),
    ], OWN_USE)
    _filing(s, 5962, "S100B7601", "76010", "FY", "2021-02-28", "2021-05-28 15:00:00", [
        x("Land", "i", 1800), x("MachineryEquipmentAndVehicles", "i", 400),
        x("AccumulatedDepreciationMachineryEquipmentAndVehicles", "i", -390), x("MachineryEquipmentAndVehiclesNet", "i", 10),
        x("LeaseAssetsPPE", "i", 1700), x("AccumulatedDepreciationLeaseAssetsPPE", "i", -1100),
        x("LeaseAssetsNetPPE", "i", 600), x("PropertyPlantAndEquipment", "i", 2410), x("NoncurrentAssets", "i", 2410),
        x("Assets", "i", 2410),
    ], STANDARD_PPE)


RAILWAYS = {
    "90060": (
        ("NoncurrentAssetsIncidentalBusinessIncidentalRWY", "PropertyPlantAndEquipmentIncidentalRWY",
         "AccumulatedDepreciationIncidentalRWY", "PropertyPlantAndEquipmentNetIncidentalRWY",
         "IntangibleAssetsIncidentalRWY"),
        ("ext:RealEstateAndLeisureFixedAssetTotalRealEstateAndLeisureFixedAsset",
         "ext:PropertyRealEstateAndLeisureFixedAsset", "ext:AccumulatedDepreciationRealEstateAndLeisure",
         "ext:PropertyPlantAndEquipmentNetProperty", "ext:IntangibleAssetsRealEstateAndLeisureFixedAsset"),
    ),
    "90070": (
        ("NoncurrentAssetsOtherOtherRWY", "PropertyPlantAndEquipmentOtherRWY", "AccumulatedDepreciationOtherRWY",
         "ext:PropertyPlantAndEquipmentNetNCAOther", "IntangibleAssetsOtherRWY"),
        ("ext:NonCurrentAssetsLivingServicesRWY", "ext:PropertyPlantAndEquipmentNCALivingServicesRWY",
         "ext:AccumulatedDepreciationNCALivingServicesRWY", "ext:PropertyPlantAndEquipmentNetNCALivingServicesRWY",
         "ext:IntangibleAssetsNCALivingServicesRWY"),
    ),
}


def _fact(key: str, value: float) -> tuple:
    return ext(key[4:], "i", value) if key.startswith("ext:") else x(key, "i", value)


def _railway(s: Seeder, code: str, fid: int, end: str, keys: tuple[str, ...], values: tuple[float, ...]) -> None:
    total, gross, acc, net, ia = keys
    head = f"ext:{total[4:]}Abstract" if total.startswith("ext:") else f"{total}Abstract"
    tree = RAILWAY_HEAD.format(head=head, gross=gross, acc=acc, net=net, ia=ia, total=total)
    g, a, n, i = values
    _filing(s, fid, f"S{fid}", code, "FY", end, f"{end[:4]}-06-20 15:00:00", [
        _fact(gross, g), _fact(acc, a), _fact(net, n), _fact(ia, i), _fact(total, n + i),
        x("NoncurrentAssets", "i", n + i), x("Assets", "i", n + i),
    ], tree, consolidated=False)


def _railways(s: Seeder) -> None:
    for n, (code, (old, new)) in enumerate(RAILWAYS.items()):
        s.stock(code, "事業の区分が替わった鉄道会社")
        _railway(s, code, 5971 + 2 * n, "2017-03-31", old, (221722, -118242, 103480, 926))
        _railway(s, code, 5972 + 2 * n, "2025-03-31", new, (22585, -10922, 11663, 16))


def _labels(s: Seeder) -> None:
    for local, label in LABELS.items():
        s.db.execute(text("UPDATE xbrl_elements SET label_ja = :l WHERE local_name = :n"), {"l": label, "n": local})


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    s = Seeder()
    for seed in (_allowance, _insurer_tax, _other_ia, _goodwill, _relocation, _own_use, _railways, _labels):
        seed(s)
    s.close()


def table(code: str, consolidated: bool = True) -> dict:
    db = SessionLocal()
    try:
        return fst.financial_table(db, code, consolidated=consolidated)
    finally:
        db.close()


def rows(t: dict) -> dict[str, dict]:
    return {r["key"]: r for r in t["rows"]}


def place(row: dict) -> tuple[tuple[str, ...], int]:
    return tuple(h["name"] for h in row["heads"]), row["indent"]


def cells(row: dict) -> list[float | None]:
    return [None if c is None else c["value"] for c in row["cells"]]


def children(t: dict, parent: str) -> list[dict]:
    keys = [r["key"] for r in t["rows"]]
    at = keys.index(parent)
    out = []
    for r in t["rows"][at + 1:]:
        if r["indent"] == 0:
            break
        out.append(r)
    return out


def section(t: dict, head: str) -> list[dict]:
    return [r for r in t["rows"] if r["heads"] and r["heads"][-1]["name"] == head]


def total(rs: list[dict], n: int) -> float:
    return sum((r["cells"][n] or {"value": 0})["value"] for r in rs)


def _no_fixes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fst, "_place_fixes", dict)


def test_allowance_follows_the_older_declarations_when_the_newest_one_is_listed(monkeypatch) -> None:
    t = table("87060")
    got = rows(t)
    allowance = got["AllowanceForDoubtfulAccountsIOAByGroup"]
    assert place(allowance) == place(got["OtherIOA"]) == (("資産の部", "固定資産"), 1)
    under = children(t, "InvestmentsAndOtherAssets")
    assert [r["key"] for r in under] == ["InvestmentSecurities", "OtherIOA", "AllowanceForDoubtfulAccountsIOAByGroup"]
    for n, ioa in enumerate(cells(got["InvestmentsAndOtherAssets"])):
        assert total(under, n) == pytest.approx(ioa)
    _no_fixes(monkeypatch)
    assert place(rows(table("87060"))["AllowanceForDoubtfulAccountsIOAByGroup"]) == (("資産の部", "固定資産"), 0)


def test_deferred_tax_stays_on_top_when_the_half_year_report_is_listed(monkeypatch) -> None:
    t = table("71570", consolidated=False)
    got = rows(t)
    assert place(got["DeferredTaxAssets"]) == place(got["OtherAssetsAssetsINS"]) == (("資産の部",), 0)
    under = children(t, "OtherAssetsAssetsINS")
    assert "DeferredTaxAssets" not in [r["key"] for r in under]
    top = [r for r in t["rows"] if r["indent"] == 0 and r["key"] != "Assets"]
    for n, (other, assets) in enumerate(zip(cells(got["OtherAssetsAssetsINS"]), cells(got["Assets"]))):
        assert abs(total(under, n) - other) <= 2 * M
        assert total(top, n) == pytest.approx(assets)
    _no_fixes(monkeypatch)
    assert place(rows(table("71570", consolidated=False))["DeferredTaxAssets"])[1] == 1


def test_other_ia_of_the_listed_quarters_is_the_other_of_investments(monkeypatch) -> None:
    t = table("18660")
    got = rows(t)
    assert cells(got["OtherIOA"]) == [1577.741 * M, 1500 * M]
    assert cells(got["OtherIA"]) == [None, 20 * M]
    ioa = section(t, "投資その他の資産")
    assert "OtherIOA" in [r["key"] for r in ioa] and "OtherIA" not in [r["key"] for r in ioa]
    for n, value in enumerate(cells(got["InvestmentsAndOtherAssets"])):
        assert total([r for r in ioa if r["key"] != "InvestmentsAndOtherAssets"], n) == pytest.approx(value)
    _no_fixes(monkeypatch)
    assert cells(rows(table("18660"))["OtherIA"]) == [1577.741 * M, 20 * M]


def test_goodwill_printed_in_investments_gets_its_own_row() -> None:
    t = table("43950")
    got = rows(t)
    split = got["Goodwill#S100MV45"]
    assert split["label"] == "のれん" and cells(split) == [299.367 * M, None]
    assert place(split) == (("資産の部", "固定資産", "投資その他の資産"), 0)
    assert cells(got["Goodwill"]) == [None, 236.187 * M]
    assert place(got["Goodwill"]) == (("資産の部", "固定資産", "無形固定資産"), 0)
    for head, end in (("投資その他の資産", "InvestmentsAndOtherAssets"), ("無形固定資産", "IntangibleAssets")):
        rs = [r for r in section(t, head) if r["key"] != end]
        for n, value in enumerate(cells(got[end])):
            if rs and any(r["cells"][n] for r in rs):
                assert total(rs, n) == pytest.approx(value), (head, n)


def test_relocation_allowance_of_the_listed_quarter_stays_in_noncurrent_liabilities() -> None:
    t = table("60370", consolidated=False)
    got = rows(t)
    old = got["ext:AllowanceForLossOfTransferringHeadquarterNCL#S1009VPY"]
    assert cells(old) == [17.884 * M, None, None]
    assert place(old) == (("負債の部", "固定負債"), 0)
    current = got["ext:ProvisionForHeadOfficeRelocationExpenseCL"]
    assert current["merged_keys"] == ["ext:AllowanceForLossOfTransferringHeadquarterNCL"]
    assert cells(current) == [None, 32 * M, 10 * M]
    assert place(current) == (("負債の部", "流動負債"), 0)


def test_own_use_rows_stay_under_their_heading() -> None:
    t = table("76010")
    got = rows(t)
    own = ("資産の部", "固定資産", "有形固定資産", "自社有形固定資産")
    for key in ("ext:MachineryEquipmentAndVehiclesForOwnUse", "ext:MachineryEquipmentAndVehiclesForOwnUseNet",
                "ext:LeaseAssetsForOwnUse", "ext:LeaseAssetsForOwnUseNet"):
        assert place(got[key]) == (own, 0) and got[key]["cells"][1] is None, key
    for key in ("MachineryEquipmentAndVehicles", "LeaseAssetsPPE"):
        assert got[key]["merged_keys"] == [] and got[key]["cells"][0] is None
    assert place(got["ext:AccumulatedDepreciationLeaseAssetsForOwnUse"]) == (own, 1)
    leaves = [r for r in section(t, "自社有形固定資産") if r["key"] in (
        "ext:LandForOwnUse", "ext:MachineryEquipmentAndVehiclesForOwnUseNet", "ext:LeaseAssetsForOwnUseNet")]
    assert total(leaves, 0) == pytest.approx(cells(got["ext:PropertyPlantAndEquipmentForOwnCompany"])[0])


@pytest.mark.parametrize("code", sorted(RAILWAYS))
def test_railway_rows_stay_under_the_old_business_heading(code: str) -> None:
    got = rows(table(code, consolidated=False))
    old, new = RAILWAYS[code]
    for before, after in zip(old[1:], new[1:]):
        assert cells(got[before])[1] is None and cells(got[after])[0] is None, (before, after)
        assert got[after]["merged_keys"] == []
    total_key, gross, acc, net, ia = old
    assert cells(got[net])[0] + cells(got[ia])[0] == pytest.approx(cells(got[total_key])[0])
    assert place(got[acc])[1] == 1 and place(got[acc])[0] == place(got[gross])[0]


def test_place_fixes_are_well_formed() -> None:
    raw = json.loads(XBRL_ROW_PLACE_FIXES_PATH.read_text(encoding="utf-8"))
    seen = set()
    for item in raw["fixes"]:
        assert len(item["code"]) == 5 and isinstance(item["consolidated"], bool)
        assert item["statement"] in fst.STATEMENTS and item["doc_id"] and item["key"] and item["evidence"]
        assert item.get("as") != item["key"]
        key = (item["code"], item["consolidated"], item["doc_id"], item["statement"], item["key"])
        assert key not in seen
        seen.add(key)
    assert sum(len(v) for by_doc in fst._place_fixes().values() for v in by_doc.values()) == len(raw["fixes"])


def test_merge_exclusions_are_well_formed() -> None:
    raw = json.loads(XBRL_ROW_MERGE_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    for item in raw["pairs"]:
        assert len(item["code"]) == 5 and item["statement"] in fst.STATEMENTS and item["evidence"]
        assert len(set(item["keys"])) == 2

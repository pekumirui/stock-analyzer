from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from app.xbrl import three_statements as fst
from tests.xbrl.test_three_statements import M, Seeder, x
from tests.xbrl.test_three_statements_kab591 import _filing, ext

GROSS_AND_NET = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
      {gross}
        {acc}
      {net}
      PropertyPlantAndEquipment
    NoncurrentAssets
  Assets
"""
NET_ONLY = """
AssetsAbstract=資産の部
  NoncurrentAssetsAbstract=固定資産
    PropertyPlantAndEquipmentAbstract=有形固定資産
      ext:RightOfUseAssetPPE
      PropertyPlantAndEquipment
    NoncurrentAssets
  Assets
"""
QUARTER = "xq"
LABELS = {
    "RightOfUseAssetPPE": "使用権資産", "AccumulatedDepreciationRightOfUseAssetPPE": "減価償却累計額",
    "RightOfUseAssetNetPPE": "使用権資産（純額）", "RightOfUseAssets": "使用権資産",
    "AccumulatedDepreciationRightOfUseAssets": "減価償却累計額", "RightOfUseAssetsNet": "使用権資産（純額）",
}


def _totals(value: float) -> list[tuple]:
    return [x("PropertyPlantAndEquipment", "i", value), x("NoncurrentAssets", "i", value), x("Assets", "i", value)]


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    s = Seeder()
    s.stock("49980", "四半期は使用権資産の純額を総額の会社独自の要素で載せた")
    _filing(s, 6201, "S100NGG8", "49980", "Q3", "2021-12-31", "2022-02-10 15:00:00",
            [x("RightOfUseAssetPPE", "i", 616, QUARTER), *_totals(616)], NET_ONLY)
    _filing(s, 6202, "S100OGRQ", "49980", "FY", "2022-03-31", "2022-06-29 15:00:00", [
        ext("RightOfUseAssetPPE", "i", 1264), ext("AccumulatedDepreciationRightOfUseAssetPPE", "i", -486),
        ext("RightOfUseAssetNetPPE", "i", 777), *_totals(777),
    ], GROSS_AND_NET.format(gross="ext:RightOfUseAssetPPE", acc="ext:AccumulatedDepreciationRightOfUseAssetPPE",
                            net="ext:RightOfUseAssetNetPPE"))
    _filing(s, 6203, "S100P0JU", "49980", "Q1", "2022-06-30", "2022-08-10 15:00:00",
            [x("RightOfUseAssetPPE", "i", 796, QUARTER), *_totals(796)], NET_ONLY)
    _filing(s, 6204, "S100RALU", "49980", "FY", "2023-03-31", "2023-06-29 15:00:00", [
        x("RightOfUseAssets", "i", 1328), x("AccumulatedDepreciationRightOfUseAssets", "i", -600),
        x("RightOfUseAssetsNet", "i", 727), *_totals(727),
    ], GROSS_AND_NET.format(gross="RightOfUseAssets", acc="AccumulatedDepreciationRightOfUseAssets",
                            net="RightOfUseAssetsNet"))
    for local, label in LABELS.items():
        s.db.execute(text("UPDATE xbrl_elements SET label_ja = :l WHERE local_name = :n"), {"l": label, "n": local})
    s.db.execute(text("UPDATE xbrl_elements SET label_ja = '使用権資産（純額）' WHERE namespace_key = :ns"), {"ns": QUARTER})
    s.close()


def _rows() -> list[tuple[str, str, list[float | None]]]:
    db = SessionLocal()
    try:
        t = fst.financial_table(db, "49980")
    finally:
        db.close()
    return [(r["label"], r["key"], [None if c is None else c["value"] / M for c in r["cells"]])
            for r in t["rows"] if "使用権" in r["label"] or "累計額" in r["label"]]


def test_net_of_the_listed_quarters_joins_the_net_row() -> None:
    assert [(label, values) for label, _, values in _rows()] == [
        ("使用権資産", [None, 1264, None, 1328]),
        ("減価償却累計額", [None, -486, None, -600]),
        ("使用権資産(純額)", [616, 777, 796, 727]),
    ]


def test_without_the_list_the_quarters_put_the_net_on_the_gross_row(monkeypatch) -> None:
    monkeypatch.setattr(fst, "_place_fixes", dict)
    assert ("使用権資産(純額)", "ext:RightOfUseAssetPPE", [616, 1264, 796, None]) in _rows()



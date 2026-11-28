from __future__ import annotations

from tools.xbrl.split_rows import split_candidates

PPE = [{"key": "PropertyPlantAndEquipmentAbstract", "name": "有形固定資産"}]


def view(rows: list[tuple[str, str, list[float | None]]], heads: list[dict] | None = None) -> dict:
    n = len(rows[0][2])
    return {
        "periods": [{"label": f"2{i}.03"} for i in range(n)],
        "rows": [{"key": key, "label": label, "unit": "JPY", "heads": heads if heads is not None else PPE,
                  "cells": [None if v is None else {"value": v} for v in values]} for key, label, values in rows],
    }


def test_rows_with_one_name_and_no_shared_period_are_a_split() -> None:
    got = split_candidates(view([
        ("RightOfUseAssetsPPE", "使用権資産", [5, 5, 5]),
        ("ext:AccumulatedDepreciationRightOfUseAssetsPPE", "減価償却累計額", [-1, None, None]),
        ("AccumulatedDepreciationRightOfUseAssets", "減価償却累計額", [None, -2, None]),
        ("AccumulatedDepreciationRightOfUseAssetsPPE", "減価償却累計額", [None, None, -3]),
        ("RightOfUseAssetsNetPPE", "使用権資産(純額)", [4, 3, 2]),
    ]))
    assert [(c["kind"], c["keys"]) for c in got] == [
        ("accumulated_same_gross", "ext:AccumulatedDepreciationRightOfUseAssetsPPE,AccumulatedDepreciationRightOfUseAssets"),
        ("accumulated_same_gross", "ext:AccumulatedDepreciationRightOfUseAssetsPPE,AccumulatedDepreciationRightOfUseAssetsPPE"),
        ("accumulated_same_gross", "AccumulatedDepreciationRightOfUseAssets,AccumulatedDepreciationRightOfUseAssetsPPE"),
    ]
    assert got[0]["spans"] == "20.03..20.03:1,21.03..21.03:1"
    assert all(c["overlap_in_group"] == 0 for c in got)


def test_one_overlapping_pair_is_marked_on_the_group() -> None:
    got = split_candidates(view([
        ("A", "使用権資産(純額)", [1, 2, None]),
        ("B", "使用権資産(純額)", [None, 3, None]),
        ("C", "使用権資産(純額)", [None, None, 4]),
    ]))
    assert sorted(c["keys"] for c in got) == ["A,C", "B,C"]
    assert all(c["overlap_in_group"] == 1 for c in got)


def test_rows_under_other_heads_or_sharing_a_period_are_not_split() -> None:
    other = [{"key": "IntangibleAssetsAbstract", "name": "無形固定資産"}]
    rows = view([("A", "その他", [1, None]), ("B", "その他", [2, 2])])
    assert split_candidates(rows) == []
    a = view([("A", "リース資産", [1, None])])
    b = view([("B", "リース資産", [None, 2])], other)
    assert split_candidates({"periods": a["periods"], "rows": a["rows"] + b["rows"]}) == []


def test_kinds() -> None:
    got = split_candidates(view([
        ("A", "その他", [1, None]), ("B", "その他", [None, 2]),
        ("pdf:土地", "土地", [3, None]), ("Land", "土地", [None, 3]),
        ("C", "建物", [4, None]), ("D", "建物", [None, 4]),
    ]))
    assert [c["kind"] for c in got] == ["generic", "pdf", "row"]


def test_net_name_right_above_accumulated_is_listed() -> None:
    got = split_candidates(view([
        ("ext:RightOfUseAssetPPE", "使用権資産(純額)", [616, 1264]),
        ("ext:AccumulatedDepreciationRightOfUseAssetPPE", "減価償却累計額", [None, -486]),
        ("ext:RightOfUseAssetNetPPE", "使用権資産(純額)", [None, 777]),
    ]))
    assert [(c["kind"], c["keys"]) for c in got] == [
        ("net_above_accumulated", "ext:RightOfUseAssetPPE,ext:AccumulatedDepreciationRightOfUseAssetPPE"),
    ]


def test_accumulated_rows_under_other_gross_rows_are_told_apart() -> None:
    got = split_candidates(view([
        ("Vehicles", "車両運搬具", [5, None]), ("AccumulatedDepreciationVehicles", "減価償却累計額", [-1, None]),
        ("Machinery", "機械装置及び運搬具", [None, 8]),
        ("AccumulatedDepreciationMachinery", "減価償却累計額", [None, -2]),
    ]))
    assert [c["kind"] for c in got] == ["accumulated_other_gross"]


def test_net_name_above_a_lump_accumulated_row_is_not_listed() -> None:
    got = split_candidates(view([
        ("OtherNetPPE", "その他(純額)", [7, 7]),
        ("AccumulatedImpairmentLossPPEByGroup", "減損損失累計額", [-3, -3]),
        ("Goodwill", "のれん", [2, 2]),
    ]))
    assert got == []

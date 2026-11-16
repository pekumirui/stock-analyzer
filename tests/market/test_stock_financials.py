"""銘柄の財務3表 API（docs/DESIGN_stock.md の API）。"""
from __future__ import annotations

import pytest

from app.xbrl import three_statements as fst
from tests.xbrl.test_three_statements import (
    CODE, DEFINED, NONCONSOL, SINGLE, M, Seeder, _presentation, seed, x,
)

STITCH = "55550"


def _bs(cash: float, *extra: tuple) -> list[tuple]:
    return [x("CashAndDeposits", "i", cash), x("Assets", "i", cash), *extra, x("LiabilitiesAndNetAssets", "i", cash)]


def _stitched(s: Seeder) -> None:
    s.stock(STITCH, "途中から連結")
    s.filing(901, "edinet", "FY", "2024-03-31", "2024-06-20 15:00:00", code=STITCH)
    s.facts(901, "2024-03-31", _bs(10))
    s.text_fact(901, "jpdei_cor", fst.DEI_CONSOLIDATED, "false")
    s.filing(902, "tdnet", "Q2", "2024-09-30", "2024-11-10 15:00:00", code=STITCH)
    s.facts(902, "2024-09-30", _bs(20, x("NonControllingInterests", "i", 2)))
    s.filing(903, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00", code=STITCH)
    s.facts(903, "2025-03-31", _bs(30, x("NonControllingInterests", "i", 3)))
    s.facts(903, "2025-03-31", _bs(25), consolidated=False, dims=NONCONSOL)
    s.text_fact(903, "jpdei_cor", fst.DEI_CONSOLIDATED, "true")


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed(lambda s: (_stitched(s), _presentation(s)))


def test_financials(c) -> None:
    r = c.get("/api/stock/1234/financials")
    assert r.status_code == 200
    d = r.json()
    assert (d["code"], d["name"], d["statement"], d["consolidated"], d["basis"]) == (CODE, "テスト", "BS", True, "連結")
    assert d["unit"] == "JPY" and d["accounting_standards"] == ["JPGAAP"] and d["empty"] is None
    assert [p["label"] for p in d["periods"]] == ["24.09", "25.03", "25.06", "25.09", "25.12"]
    assert d["periods"][-1] == {"period_end": "2025-12-31", "label": "25.12", "period_type": "Q3", "source": "edinet",
                                "source_label": "有報", "doc_id": "D8", "submitted_at": "2026-02-12 15:00",
                                "accounting_standard": "JPGAAP", "consolidated": True}
    rows = {row["key"]: row for row in d["rows"]}
    assert rows["Goodwill"]["cells"][-1] == {"value": 6 * M, "source": "tdnet"}
    assert rows["Goodwill"]["cells"][2] == {"value": 3 * M, "source": "tdnet_pdf"}
    assert rows["Land"]["cells"][2] is None
    assert rows["pdf:前受金"]["unmatched_pdf"] is True and rows["pdf:前受金"]["label"] == "前受金"
    assert set(rows["Goodwill"]) == {"key", "label", "unit", "unmatched_pdf", "group", "section", "merged_keys", "cells"}
    assert rows["Goodwill"]["merged_keys"] == []
    assert (rows["Goodwill"]["group"], rows["Goodwill"]["section"]) == (None, None)
    defined = {row["key"]: row for row in c.get(f"/api/stock/{DEFINED}/financials").json()["rows"]}
    assert (defined["CashAndDeposits"]["group"], defined["CashAndDeposits"]["section"]) == ("資産", "流動資産")
    assert (defined["LiabilitiesAndNetAssets"]["group"], defined["LiabilitiesAndNetAssets"]["section"]) == (None, None)

    d = c.get(f"/api/stock/{CODE}/financials?statement=PL&periods=2").json()
    assert d["cumulative"] is True and [p["label"] for p in d["periods"]] == ["25.06", "25.09"]


def test_financials_consolidated_param(c) -> None:
    d = c.get(f"/api/stock/{SINGLE}/financials").json()
    assert (d["consolidated"], d["basis"], d["empty"]) == (False, "個別", None)
    d = c.get(f"/api/stock/{SINGLE}/financials?consolidated=true").json()
    assert d["consolidated"] is True and d["rows"] == [] and d["periods"] == []
    assert d["empty"]["reason"] == "no_material" and d["empty"]["other_basis_available"] is True
    d = c.get(f"/api/stock/{CODE}/financials?consolidated=false").json()
    assert d["consolidated"] is False and [p["label"] for p in d["periods"]] == ["25.03"]


def test_financials_auto_fills_periods_before_consolidation_with_single(c) -> None:
    d = c.get(f"/api/stock/{STITCH}/financials").json()
    assert (d["consolidated"], d["basis"], d["empty"]) == (None, "連結・個別", None)
    assert [(p["label"], p["consolidated"]) for p in d["periods"]] == [
        ("24.03", False), ("24.09", True), ("25.03", True)]
    rows = {row["key"]: row for row in d["rows"]}
    assert [cell and cell["value"] for cell in rows["CashAndDeposits"]["cells"]] == [10 * M, 20 * M, 30 * M]
    assert [cell and cell["value"] for cell in rows["NonControllingInterests"]["cells"]] == [None, 2 * M, 3 * M]

    d = c.get(f"/api/stock/{STITCH}/financials?periods=2").json()
    assert (d["consolidated"], d["basis"]) == (True, "連結")
    d = c.get(f"/api/stock/{STITCH}/financials?consolidated=true").json()
    assert [p["label"] for p in d["periods"]] == ["24.09", "25.03"]
    d = c.get(f"/api/stock/{STITCH}/financials?consolidated=false").json()
    assert [(p["label"], p["consolidated"]) for p in d["periods"]] == [("24.03", False), ("25.03", False)]


def test_financials_errors(c) -> None:
    assert c.get("/api/stock/9999/financials").status_code == 404
    assert c.get("/api/stock/xx/financials").status_code == 400
    for q in ("statement=SS", "periods=0", "periods=21", "consolidated=yes"):
        assert c.get(f"/api/stock/{CODE}/financials?{q}").status_code == 422

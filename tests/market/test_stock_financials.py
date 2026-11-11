"""銘柄の財務3表 API（docs/DESIGN_stock.md の API）。"""
from __future__ import annotations

import pytest

from tests.xbrl.test_three_statements import CODE, SINGLE, M, seed


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed()


def test_financials(c) -> None:
    r = c.get("/api/stock/1234/financials")
    assert r.status_code == 200
    d = r.json()
    assert (d["code"], d["name"], d["statement"], d["consolidated"], d["basis"]) == (CODE, "テスト", "BS", True, "連結")
    assert d["unit"] == "JPY" and d["accounting_standards"] == ["JPGAAP"] and d["empty"] is None
    assert [p["label"] for p in d["periods"]] == ["24.09", "25.03", "25.06", "25.09", "25.12"]
    assert d["periods"][-1] == {"period_end": "2025-12-31", "label": "25.12", "period_type": "Q3", "source": "edinet",
                                "source_label": "有報", "doc_id": "D8", "submitted_at": "2026-02-12 15:00",
                                "accounting_standard": "JPGAAP"}
    rows = {row["key"]: row for row in d["rows"]}
    assert rows["Goodwill"]["cells"][-1] == {"value": 6 * M, "source": "tdnet"}
    assert rows["Goodwill"]["cells"][2] == {"value": 3 * M, "source": "tdnet_pdf"}
    assert rows["Land"]["cells"][2] is None
    assert rows["pdf:前受金"]["unmatched_pdf"] is True and rows["pdf:前受金"]["label"] == "前受金"
    assert set(rows["Goodwill"]) == {"key", "label", "unit", "unmatched_pdf", "group", "section", "cells"}
    assert (rows["Goodwill"]["group"], rows["Goodwill"]["section"]) == ("資産", "無形固定資産")
    assert (rows["LiabilitiesAndNetAssets"]["group"], rows["LiabilitiesAndNetAssets"]["section"]) == (None, None)

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


def test_financials_errors(c) -> None:
    assert c.get("/api/stock/9999/financials").status_code == 404
    assert c.get("/api/stock/xx/financials").status_code == 400
    for q in ("statement=SS", "periods=0", "periods=21", "consolidated=yes"):
        assert c.get(f"/api/stock/{CODE}/financials?{q}").status_code == 422

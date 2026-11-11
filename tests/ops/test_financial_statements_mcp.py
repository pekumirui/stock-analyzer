"""MCP の財務3表: 銘柄の API（/api/stock/{code}/financials）を TSV にするだけ。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")

import earnings_tools as et  # noqa: E402
import log_mcp as m  # noqa: E402

M = 1_000_000


def _period(end: str, label: str, source: str, source_label: str) -> dict:
    return {"period_end": end, "label": label, "period_type": "FY", "source": source, "source_label": source_label,
            "doc_id": "D1", "submitted_at": "2025-06-20 15:00", "accounting_standard": "JPGAAP"}


TABLE = {
    "code": "12340", "name": "テスト", "statement": "PL", "consolidated": True, "basis": "連結",
    "accounting_standards": ["IFRS", "JPGAAP"], "cumulative": True, "unit": "JPY",
    "periods": [_period("2025-03-31", "25.03", "edinet", "有報"), _period("2025-06-30", "25.06", "tdnet_pdf", "PDF")],
    "rows": [
        {"key": "NetSales", "label": "売上高", "unit": "money", "unmatched_pdf": False,
         "cells": [{"value": 1234567 * M, "source": "edinet"}, {"value": 120 * M, "source": "tdnet_pdf"}]},
        {"key": "ext:Other", "label": "その他\t収益", "unit": "money", "unmatched_pdf": False,
         "cells": [None, {"value": -4.4 * M, "source": "tdnet"}]},
        {"key": "pdf:前受金", "label": "前受金", "unit": "money", "unmatched_pdf": True,
         "cells": [None, {"value": 4 * M, "source": "tdnet_pdf"}]},
        {"key": "BasicEarningsLossPerShare", "label": "1株当たり当期純利益", "unit": "per_share",
         "unmatched_pdf": False, "cells": [{"value": 1234.5, "source": "edinet"}, None]},
    ],
    "empty": None,
}
EMPTY = {
    "code": "12340", "name": "テスト", "statement": "BS", "consolidated": True, "basis": "連結",
    "accounting_standards": [], "cumulative": False, "unit": "JPY", "periods": [], "rows": [],
    "empty": {"reason": "no_material", "message": "有報・短信 XBRL・短信 PDF の本表が無い。米国基準は PDF を読んでいない",
              "other_basis_available": True},
}


def _getter(payload: dict, seen: list):
    def get_json(path, params, timeout):
        seen.append((path, params, timeout))
        return payload
    return get_json


def test_tsv_shape() -> None:
    seen: list = []
    text = et.tool_financial_statements({"statement": "pl", "periods": 3}, _getter(TABLE, seen), "12340")
    assert seen == [("/api/stock/12340/financials", {"statement": "PL", "periods": 3, "consolidated": "true"},
                     et.APP_TIMEOUT)]
    assert text.splitlines() == [
        "12340 テスト PL 連結 単位:百万円（1株当たりは円） 会計基準:IFRS/JPGAAP 期首からの累計",
        "科目\t要素\t25.03\t25.06",
        "売上高\tNetSales\t1,234,567\t120",
        "その他 収益\text:Other\t\t-4",
        "前受金\tpdf:前受金\t\t4",
        "1株当たり当期純利益\tBasicEarningsLossPerShare\t1,234.50\t",
        "出どころ\t\t有報\tPDF",
    ]


def test_bs_head_without_name_and_standard() -> None:
    payload = {**TABLE, "statement": "BS", "name": None, "accounting_standards": [], "cumulative": False}
    head = et.tool_financial_statements({}, _getter(payload, []), "12340").splitlines()[0]
    assert head == "12340 BS 連結 単位:百万円（1株当たりは円） 会計基準:-"


def test_no_material_line() -> None:
    seen: list = []
    text = et.tool_financial_statements({}, _getter(EMPTY, seen), "12340")
    assert seen[0][1] == {"statement": "BS", "periods": 8, "consolidated": "true"}
    assert text == ("12340: BS（連結）の材料がない（有報・短信 XBRL・短信 PDF の本表が無い。米国基準は PDF を読んでいない）"
                    "。個別は consolidated=false")
    single = {**EMPTY, "consolidated": False, "basis": "個別"}
    text = et.tool_financial_statements({"consolidated": False}, _getter(single, seen), "12340")
    assert seen[-1][1]["consolidated"] == "false"
    assert text.startswith("12340: BS（個別）の材料がない") and "consolidated=false" not in text


def test_tool_via_log_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    monkeypatch.setattr(m, "_earnings_get", lambda: (_getter(TABLE, seen), None))
    text, err = m.call_tool("financial_statements", {"code": "1234", "statement": "pl", "periods": 500})
    assert not err, text
    assert seen[-1][0] == "/api/stock/12340/financials" and seen[-1][1]["periods"] == 20
    for bad in ({"code": "1234", "statement": "XX"}, {"code": "1234", "consolidated": "no"},
                {"code": "1234", "periods": 0}, {"code": "12/4"}):
        msg, err = m.call_tool("financial_statements", bad)
        assert err, msg
    assert len(seen) == 1

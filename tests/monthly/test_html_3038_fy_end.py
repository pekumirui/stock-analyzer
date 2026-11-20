from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.html_issuers.i3038 import parse

FIXTURE = ROOT / "tests" / "monthly" / "testdata" / "html_3038"
_PROFIT = ("経常利益", "売上総利益", "営業利益")
_PURCHASE = (
    "直轄エリア 仕入れ 既存店",
    "直轄エリア 仕入れ 全店",
    "全国※ 仕入れ 全店",
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _load(name: str) -> str:
    return (FIXTURE / name).read_text(encoding="utf-8")


def _row(got: dict, element: str, unit: str) -> dict:
    hits = [
        r for r in got["rows"] if r.get("element") == element and r.get("unit") == unit
    ]
    if len(hits) != 1:
        _fail(
            f"{element} {unit}: {[(r.get('element'), r.get('unit')) for r in got['rows']]}"
        )
    return hits[0]


def _no_profit(got: dict) -> None:
    for row in got["rows"]:
        element = str(row.get("element") or "")
        if any(name in element for name in _PROFIT):
            _fail(f"profit statement leaked {got['rows']}")


def test_3038_full_year_keeps_sales_table() -> None:
    got = parse(_load("20261120.html"))
    if got["as_of"] != "2026-10-31":
        _fail(f"as_of {got['as_of']}")
    if got["months"] != [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        _fail(f"months {got['months']}")
    sales = _row(got, "売上高", "number")
    if sales["values"] != {
        "11": 47188,
        "12": 52864,
        "1": 43146,
        "2": 44805,
        "3": 49071,
        "4": 52403,
        "5": 49860,
        "6": 47522,
        "7": 50638,
        "8": 48242,
        "9": 48286,
        "10": 52108,
    }:
        _fail(f"sales {sales['values']}")
    yoy = _row(got, "売上高", "yoy_index")
    if yoy["values"].get("10") != 103.9 or yoy["values"].get("11") != 106.9:
        _fail(f"yoy {yoy['values']}")
    if set(yoy["values"]) != {str(m) for m in got["months"]}:
        _fail(f"yoy months {yoy['values']}")
    stores = _row(got, "総店舗数", "number")
    if stores["values"].get("10") != 1157:
        _fail(f"stores {stores['values']}")
    if [
        r["element"] for r in got["rows"] if "仕入れ" in str(r.get("element") or "")
    ] != list(_PURCHASE):
        _fail(f"purchases {got['rows']}")
    for element, october in zip(_PURCHASE, (101.7, 103.1, 103.7)):
        row = _row(got, element, "yoy_index")
        if row["values"].get("10") != october:
            _fail(f"{element} {row['values']}")
    _no_profit(got)


def test_3038_day_before_full_year_stays_on_sales() -> None:
    got = parse(_load("20261119.html"))
    if got["as_of"] != "2026-09-30":
        _fail(f"as_of {got['as_of']}")
    if got["months"] != [11, 12, 1, 2, 3, 4, 5, 6, 7, 8, 9]:
        _fail(f"months {got['months']}")
    sales = _row(got, "売上高", "number")
    if sales["values"] != {
        "11": 47188,
        "12": 52864,
        "1": 43146,
        "2": 44805,
        "3": 49071,
        "4": 52403,
        "5": 49860,
        "6": 47522,
        "7": 50638,
        "8": 48242,
        "9": 48286,
    }:
        _fail(f"sales {sales['values']}")
    if "10" in sales["values"]:
        _fail(f"october leaked {sales}")
    yoy = _row(got, "売上高", "yoy_index")
    if yoy["values"] != {
        "11": 106.9,
        "12": 106.5,
        "1": 107.7,
        "2": 105.3,
        "3": 102.9,
        "4": 102.3,
        "5": 103,
        "6": 105.4,
        "7": 107.6,
        "8": 105.9,
        "9": 105.6,
    }:
        _fail(f"yoy {yoy['values']}")
    stores = _row(got, "総店舗数", "number")
    if stores["values"].get("9") != 1150 or "10" in stores["values"]:
        _fail(f"stores {stores['values']}")
    if [
        r["element"] for r in got["rows"] if "仕入れ" in str(r.get("element") or "")
    ] != list(_PURCHASE):
        _fail(f"purchases {got['rows']}")
    _no_profit(got)
    if [r["element"] for r in got["rows"]] != [
        "売上高",
        "売上高",
        "総店舗数",
        *_PURCHASE,
    ]:
        _fail(f"rows {[r['element'] for r in got['rows']]}")

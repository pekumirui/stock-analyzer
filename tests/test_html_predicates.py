"""HTML 月次の判定関数。True 側と否定側をリテラルで検査する。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))

from html_issuers import i3028, i3222, i7416, i8011, i8194  # noqa: E402
from html_issuers._branded import (  # noqa: E402
    Section,
    _grid_self_dated,
    _is_count_only_parsed,
    _is_store_event_parsed,
    _merge_companion_store_scopes,
    _merge_companion_total_stores,
)
from html_issuers._page import _count_only as page_count_only  # noqa: E402
from html_issuers._page import _store_event as page_store_event  # noqa: E402
from html_issuers.i8278 import _count_only as fuji_count_only  # noqa: E402
from html_issuers.i8278 import _store_event as fuji_store_event  # noqa: E402
from html_issuers.i9989 import _CaptionParser, _plain, _table_captions  # noqa: E402
from html_monthly import (  # noqa: E402
    _is_caption_cell,
    _is_count_metric,
    _is_pre_merger_reference_section,
    unit_mark_context,
)

SALES_HTML = """
<p>2026年5月度 月次</p>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr>
<tr><td>全店 売上高</td><td>1.2%</td><td>2.3%</td><td>3.4%</td></tr>
<tr><td>既存店 売上高</td><td>0.5%</td><td>1.1%</td><td>1.8%</td></tr>
</table>
"""


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_caption_and_count_and_merger() -> None:
    if not _is_caption_cell("2026年3月期"):
        _fail("fy caption")
    if not _is_caption_cell("2026年度"):
        _fail("fy year caption")
    if _is_caption_cell("売上高"):
        _fail("metric is not a caption")
    if _is_caption_cell("2026年3月期 売上高"):
        _fail("caption plus leftover label")
    if not _is_count_metric("店舗数"):
        _fail("店舗数 is a count")
    if not _is_count_metric("増加(店)"):
        _fail("store delta is a count")
    if _is_count_metric("売上高"):
        _fail("売上高 is not a count")
    if not _is_pre_merger_reference_section("経営統合前の参考"):
        _fail("pre-merger heading")
    if _is_pre_merger_reference_section("月次営業速報"):
        _fail("normal heading")


def test_unit_mark_context() -> None:
    got = unit_mark_context("<p>（単位：%）</p><p>売上</p>")
    if "単位" not in got or "%" not in got:
        _fail(f"unit mark: {got!r}")
    if unit_mark_context("<p>hello</p>") != "":
        _fail(f"no unit mark: {unit_mark_context('<p>hello</p>')!r}")


def test_store_event_and_count_only() -> None:
    event = {
        "rows": [
            {"element": "A店 店舗面積", "unit": "number"},
            {"element": "B店 店舗面積", "unit": "number"},
        ]
    }
    sales = {
        "rows": [
            {"element": "全店 売上高", "unit": "yoy_pct", "values": {"4": 1.2}},
            {"element": "既存店 売上高", "unit": "yoy_pct", "values": {"4": 0.5}},
        ]
    }
    stores = {"rows": [{"element": "店舗数", "unit": "number", "values": {"4": 10}}]}
    empty = {"rows": []}
    for fn in (_is_store_event_parsed, page_store_event, fuji_store_event):
        if not fn(event):
            _fail(f"{fn.__module__} store event true")
        if fn(sales) or fn(empty):
            _fail(f"{fn.__module__} store event false")
    for fn in (_is_count_only_parsed, page_count_only, fuji_count_only):
        if not fn(stores):
            _fail(f"{fn.__module__} count-only true")
        if fn(sales) or fn(empty) or fn(event):
            _fail(f"{fn.__module__} count-only false")


def test_merge_companion_rows() -> None:
    primary = {
        "months": [4, 5],
        "rows": [{"element": "全店 売上高", "unit": "yoy_pct", "values": {"4": 1.0}}],
    }
    stores = {
        "months": [4, 5],
        "rows": [{"element": "総店舗数", "unit": "number", "values": {"4": 100}}],
    }
    scoped = {
        "months": [4, 5],
        "rows": [{"element": "売上高", "unit": "yoy_pct", "values": {"4": 2.0}}],
    }
    event = {
        "months": [4, 5],
        "rows": [{"element": "A店 店舗面積", "unit": "number"}],
    }
    candidates = [
        (0, [], primary, None),
        (1, [], stores, None),
        (2, [], event, None),
        (3, [], scoped, None),
    ]
    _merge_companion_total_stores(primary, candidates, primary_index=0)
    els = [r["element"] for r in primary["rows"]]
    if "総店舗数" not in els:
        _fail(f"total stores not merged: {els}")
    if any("店舗面積" in e for e in els):
        _fail("store-event rows must not merge")
    _merge_companion_store_scopes(
        primary,
        candidates,
        [Section(None, None), Section(None, None), Section(None, None), Section(None, "既存店")],
        primary_index=0,
    )
    els = [r["element"] for r in primary["rows"]]
    if not any("既存" in e for e in els):
        _fail(f"scoped companion not merged: {els}")


def test_grid_self_dated() -> None:
    if not _grid_self_dated([["2026年4月", "売上高"], ["全店", "1.0"]]):
        _fail("year in header is self-dated")
    if _grid_self_dated([["4月", "5月"], ["全店", "1.0"]]):
        _fail("month-only grid is not self-dated")


def test_caption_parser_and_plain() -> None:
    html = "<table><caption>グループ合計</caption><tr><td>x</td></tr></table><table><tr><td>y</td></tr></table>"
    captions = _table_captions(html, 2)
    if captions != ["グループ合計", ""]:
        _fail(f"captions: {captions!r}")
    if _plain("グループ <b>合計</b>") != "グループ 合計":
        _fail(f"plain: {_plain('グループ <b>合計</b>')!r}")
    parser = _CaptionParser()
    parser.feed("<table><caption>DG</caption></table>")
    parser.close()
    if parser.captions != ["DG"]:
        _fail(f"parser captions: {parser.captions}")


def test_page_parsers_read_sales_table() -> None:
    for mod in (i3028, i3222, i7416, i8011, i8194):
        got = mod.parse(SALES_HTML, as_of="2026-05-31")
        els = [r["element"] for r in got.get("rows") or []]
        blob = "\n".join(els)
        if "売上高" not in blob:
            _fail(f"{mod.__name__} rows: {els}")
        if not got.get("months"):
            _fail(f"{mod.__name__} months: {got}")
        try:
            mod.parse("<p>no table</p>")
        except ValueError:
            pass
        else:
            _fail(f"{mod.__name__} empty page")


def main() -> None:
    test_caption_and_count_and_merger()
    test_unit_mark_context()
    test_store_event_and_count_only()
    test_merge_companion_rows()
    test_grid_self_dated()
    test_caption_parser_and_plain()
    test_page_parsers_read_sales_table()
    print("ok: html predicates")


if __name__ == "__main__":
    main()

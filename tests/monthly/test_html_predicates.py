"""HTML 月次の判定分岐。parse の結果で見る。実ページが要る表選択・結合だけ内部関数を直接呼ぶ。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.html_issuers import i3028, i3222, i7416, i8011, i8194
from jobs.monthly.html_issuers._branded import (
    Section,
    _grid_self_dated,
    _is_count_only_parsed,
    _is_store_event_parsed,
    _merge_companion_store_scopes,
    _merge_companion_total_stores,
)
from jobs.monthly.html_issuers._page import (
    _apply_element_aliases,
    _count_only as page_count_only,
    _store_event as page_store_event,
)
from jobs.monthly.html_issuers.i8278 import _count_only as fuji_count_only
from jobs.monthly.html_issuers.i8278 import _store_event as fuji_store_event
from jobs.monthly.html_issuers.i9989 import _CaptionParser, _plain, _table_captions
from jobs.monthly.html_monthly import (
    _is_count_header,
    _is_prior_year_absolute,
    parse_grid,
    unit_mark_context,
)

SALES_HTML = """
<p>2026年5月度 月次売上高（前年同月比）</p>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr>
<tr><td>全店 売上高</td><td>101.2%</td><td>102.3%</td><td>103.4%</td></tr>
<tr><td>既存店 売上高</td><td>100.5%</td><td>101.1%</td><td>101.8%</td></tr>
</table>
"""


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_fy_caption_cells_are_not_labels() -> None:
    head = ["", "", "4月", "5月", "6月"]
    beside = parse_grid([
        head,
        ["2026年3月期", "全店 売上高", "101.2%", "99.8%", "100.0%"],
        ["2026年3月期", "既存店 売上高", "100.1%", "98.7%", "99.0%"],
    ])
    own_row = parse_grid([
        head[1:],
        ["2026年度", "", "", ""],
        ["全店 売上高", "101.2%", "99.8%", "100.0%"],
    ])
    for name, got, want in (
        ("caption column", beside, ["全店 売上高", "既存店 売上高"]),
        ("caption row", own_row, ["全店 売上高"]),
    ):
        els = [r["element"] for r in (got or {}).get("rows") or []]
        if els != want:
            _fail(f"{name}: {els}")
    if beside["rows"][0]["values"] != {"4": 101.2, "5": 99.8, "6": 100}:
        _fail(f"caption column values: {beside['rows'][0]}")


def test_count_rows_ignore_page_yoy_context() -> None:
    got = parse_grid(
        [
            ["", "4月", "5月", "6月"],
            ["全店", "101.2", "99.8", "100.0"],
            ["店舗数", "120", "121", "122"],
            ["営業所", "30", "30", "31"],
            ["店数", "120", "121", "122"],
        ],
        context="月次売上高 前年同月比",
    )
    rows = {r["element"]: r["unit"] for r in (got or {}).get("rows") or []}
    if rows != {"全店 売上高前年同月比": "yoy_index", "店舗数": "number", "営業所": "number", "店数": "number"}:
        _fail(f"count rows must keep their name and stay number: {rows}")


def test_pre_merger_reference_table_is_not_picked() -> None:
    def table(a: str, b: str, c: str) -> str:
        return (
            "<table><tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr>"
            f"<tr><td>全店 売上高</td><td>{a}%</td><td>{b}%</td><td>{c}%</td></tr></table>"
        )

    # 見出しは表の直前 160 字で見る。前の表の見出しが混ざらないよう間を空ける。
    pad = "<p>" + "月次の数値は速報値です。" * 20 + "</p>"
    html = (
        f"<h3>経営統合前の参考</h3>{table('555.5', '666.6', '777.7')}{pad}"
        f"<h3>月次売上高</h3>{table('101.2', '99.8', '100.0')}"
    )
    got = i3028.parse(html, as_of="2026-06-30")
    if [r["values"] for r in got["rows"]] != [{"4": 101.2, "5": 99.8, "6": 100}]:
        _fail(f"pre-merger reference table was picked: {got['rows']}")


def test_unit_mark_context() -> None:
    got = unit_mark_context("<p>（単位：%）</p><p>売上</p>")
    if "単位" not in got or "%" not in got:
        _fail(f"unit mark: {got!r}")
    if unit_mark_context("<p>hello</p>") != "":
        _fail(f"no unit mark: {unit_mark_context('<p>hello</p>')!r}")


def test_count_header_ten_su_not_month_end() -> None:
    """店数は件数見出し。月末は汎用語なので共通判定に入れない。"""
    if not _is_count_header("店数") or not _is_count_header("スポーツデポ・アルペン 店数"):
        _fail("店数 must be a count header")
    if _is_count_header("月末") or _is_count_header("月末 売上高前年同月比"):
        _fail("月末 must not be a global count header")
    stores = {
        "rows": [
            {"element": "開店", "unit": "number"},
            {"element": "閉店", "unit": "number"},
            {"element": "店数", "unit": "number"},
        ]
    }
    if not page_count_only(stores):
        _fail("開店/閉店/店数 table must be count-only")
    month_end = {
        "rows": [
            {"element": "出店", "unit": "number"},
            {"element": "退店", "unit": "number"},
            {"element": "月末 売上高前年同月比", "unit": "yoy_index"},
        ]
    }
    if page_count_only(month_end):
        _fail("月末 yoy row must keep the table out of count-only")
    _apply_element_aliases(month_end, (("月末", "店舗数"),))
    if [r["element"] for r in month_end["rows"]] != ["出店", "退店", "店舗数"]:
        _fail(f"月末 alias {month_end['rows']}")
    if month_end["rows"][2]["unit"] != "number":
        _fail(f"月末 alias unit {month_end['rows'][2]}")
    if not page_count_only(month_end):
        _fail("aliased 月末 table must be count-only")


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
    want = [
        {"element": "全店 売上高", "unit": "yoy_index", "values": {"4": 101.2, "5": 102.3, "6": 103.4}},
        {"element": "既存店 売上高", "unit": "yoy_index", "values": {"4": 100.5, "5": 101.1, "6": 101.8}},
    ]
    for mod in (i3028, i3222, i7416, i8011, i8194):
        got = mod.parse(SALES_HTML, as_of="2026-05-31")
        if got.get("months") != [4, 5, 6] or got.get("rows") != want:
            _fail(f"{mod.__name__}: {got}")
        try:
            mod.parse("<p>no table</p>")
        except ValueError:
            pass
        else:
            _fail(f"{mod.__name__} empty page")


def test_prior_year_absolute_store_rows() -> None:
    if not _is_prior_year_absolute("前年店舗数(店)"):
        _fail("前年店舗数 is a prior-year value row")
    if not _is_prior_year_absolute("前年 全店 店舗数"):
        _fail("前年 全店 店舗数 is a prior-year value row")
    if _is_prior_year_absolute("当年 全店 店舗数"):
        _fail("当年 is current-month, not prior")
    if _is_prior_year_absolute("全店 売上高(前年比)"):
        _fail("前年比 is a ratio")
    if _is_prior_year_absolute("前年差"):
        _fail("前年差 is a delta")
    if _is_prior_year_absolute("MRR前年増減率"):
        _fail("前年増減率 is a ratio")


def main() -> None:
    test_fy_caption_cells_are_not_labels()
    test_count_rows_ignore_page_yoy_context()
    test_pre_merger_reference_table_is_not_picked()
    test_unit_mark_context()
    test_count_header_ten_su_not_month_end()
    test_store_event_and_count_only()
    test_merge_companion_rows()
    test_grid_self_dated()
    test_caption_parser_and_plain()
    test_page_parsers_read_sales_table()
    test_prior_year_absolute_store_rows()
    print("ok: html predicates")


if __name__ == "__main__":
    main()

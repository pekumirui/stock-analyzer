from __future__ import annotations

import pytest

from tools.screen.render import OVERVIEW, PAGE_DIR, REBUILD, ROOT, _type, render, stale, transitions
from tools.screen.sources import SHARED, Call, Screen, calls, file_tab, match, screens, sub_tabs, tabs


def test_screens_doc_matches_routes_js_calls_openapi_and_dataflow():
    bad = stale()
    assert not bad, f"static/js・app/api・docs/dataflow/entries.json と合わない。`{REBUILD}` で作り直してコミットする: {bad}"


def test_every_screen_is_on_the_overview_and_on_its_tab_page():
    pages = render()
    for s in screens(ROOT):
        assert f"| {s.label} | `{s.id}` |" in pages[OVERVIEW], s.id
        assert f"| {s.label} | `{s.id}` |" in pages[f"{PAGE_DIR}/{s.tab}.md"], s.id


def test_every_api_call_in_js_resolves_to_a_route_and_appears_on_a_page():
    from tools.screen.render import api_routes

    routes = list(api_routes())
    pages = "\n".join(text for p, text in render().items() if p != OVERVIEW)
    found = calls(ROOT)
    assert found
    for call in found:
        label = match(call, routes)
        assert label, call
        assert f"### {label}\n" in pages, label


def test_match_fits_slots_only_to_parameters_and_prefers_literal_segments():
    routes = ["GET /api/holders/activity", "GET /api/holders/search", "GET /api/holders/{edinet}",
              "GET /api/stock/pdf/{no}", "GET /api/stock/{code}/holders", "PUT /api/stock/{code}/holders"]
    assert match(Call("a.js", 1, "GET", "/api/holders/\x00"), routes) == "GET /api/holders/{edinet}"
    assert match(Call("a.js", 1, "GET", "/api/holders/search"), routes) == "GET /api/holders/search"
    assert match(Call("a.js", 1, "GET", "/api/stock/\x00/holders"), routes) == "GET /api/stock/{code}/holders"
    assert match(Call("a.js", 1, "PUT", "/api/stock/\x00/holders"), routes) == "PUT /api/stock/{code}/holders"
    assert match(Call("a.js", 1, "GET", "/api/stock/\x00/news"), routes) is None
    assert match(Call("a.js", 1, "GET", "/api/none"), routes) is None


def test_calls_cut_the_query_and_turn_template_slots_into_placeholders(tmp_path):
    js = tmp_path / "static" / "js"
    js.mkdir(parents=True)
    (js / "x.js").write_text(
        'const a = api(`/api/screener?${st?`stage=${st}&`:""}rs_min=${rs}`);\n'
        "const b = api(`/api/v1/tables/${code}/${y}`, {method: \"PUT\"});\n"
        'const c = fetch("/static/row.json");\n',
        encoding="utf-8",
    )
    assert calls(tmp_path) == [
        Call("x.js", 1, "GET", "/api/screener"),
        Call("x.js", 2, "PUT", "/api/v1/tables/\x00/\x00"),
    ]


def test_file_tab_uses_the_tab_name_then_the_sub_screen_name():
    names = tabs(ROOT)
    subs = sub_tabs(screens(ROOT))
    assert file_tab("stock-price.js", names, subs) == "stock"
    assert file_tab("holders.js", names, subs) == "holders"
    assert file_tab("pts.js", names, subs) == "ranking"
    assert file_tab("chart-core.js", names, subs) == SHARED


def test_transitions_keep_defaults_and_cross_tab_and_drop_sibling_switches():
    all_screens = [
        Screen("stock", "stock", "銘柄", ("",), None, ()),
        Screen("stock-a", "stock", "A", (":c/a",), "stock", ("stock", "stock-b", "earnings")),
        Screen("stock-b", "stock", "B", (":c/b",), "stock", ("stock", "stock-a")),
        Screen("stock-b-detail", "stock", "B 詳細", (":c/b/:id",), "stock-b", ("stock-b",)),
        Screen("earnings", "earnings", "決算", ("",), None, ()),
    ]
    assert transitions(all_screens) == [
        ("stock", "stock-a", True),
        ("earnings", "stock-a", False),
        ("stock", "stock-b", True),
        ("stock-b", "stock-b-detail", True),
    ]


@pytest.mark.parametrize("schema, text", [
    ({"type": "string"}, "string"),
    ({"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}, "string（date）"),
    ({"anyOf": [{"type": "string", "pattern": "^x$"}, {"type": "null"}]}, "string（`^x$`）"),
    ({"type": "string", "enum": ["a", "b"]}, "a / b"),
    ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, "integer / string"),
])
def test_type_text_drops_null_and_shows_format_pattern_and_enum(schema, text):
    assert _type(schema) == text

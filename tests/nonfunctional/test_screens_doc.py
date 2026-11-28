from __future__ import annotations

import pytest

from tools.screen.render import OVERVIEW, PAGE_DIR, REBUILD, ROOT, _type, edges, render, stale
from tools.screen.sources import (
    NAV_TARGETS, SHARED, Call, Screen, calls, file_screen, file_tab, links, match, screens, sub_tabs, tabs,
)


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


def test_every_nav_function_is_defined_in_js_and_opens_a_real_screen():
    js = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "static" / "js").glob("*.js"))
    ids = {s.id for s in screens(ROOT)}
    for name, target in NAV_TARGETS.items():
        assert f"function {name}(" in js, name
        assert target in ids, target


def test_transitions_come_from_the_calling_file_and_skip_the_nav_functions_themselves():
    moves = edges(links(ROOT, screens(ROOT), tabs(ROOT)))
    for edge in [("earnings", "stock-price"), ("ranking-pts", "stock-price"), ("supply", "stock-price"),
                 ("stock-holders", "holders-detail"), ("holders", "holders-detail"), ("monthly", "monthly-detail")]:
        assert edge in moves, edge
    assert not any(src == "stock-price" for src, _ in moves)
    assert all(src != dst for src, dst in moves)


def test_links_read_calls_and_hrefs_but_not_calls_inside_a_nav_function(tmp_path):
    js = tmp_path / "static" / "js"
    js.mkdir(parents=True)
    (js / "a.js").write_text(
        "function gotoChart(code){\n  showStock(code);\n  gotoHolder(code);\n}\n"
        "const x = `<a href=\"#b/${id}\">`;\n"
        "el.onclick = () => gotoHolder(e);\n",
        encoding="utf-8",
    )
    all_screens = [Screen("a", "a", "A", ("",)), Screen("b", "b", "B", ("",)), Screen("b-detail", "b", "B 詳細", (":id",)),
                   Screen("stock-price", "stock", "株価", (":code/price",)), Screen("holders-detail", "holders", "保有者", (":e",))]
    found = [(link.line, link.source, link.target) for link in links(tmp_path, all_screens, ["a", "b", "stock", "holders"])]
    assert found == [(5, "a", "b-detail"), (6, "a", "holders-detail")]
    assert file_screen("a.js", all_screens, ["a", "b"]) == "a"


@pytest.mark.parametrize("schema, text", [
    ({"type": "string"}, "string"),
    ({"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]}, "string（date）"),
    ({"anyOf": [{"type": "string", "pattern": "^x$"}, {"type": "null"}]}, "string（`^x$`）"),
    ({"type": "string", "enum": ["a", "b"]}, "a / b"),
    ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, "integer / string"),
])
def test_type_text_drops_null_and_shows_format_pattern_and_enum(schema, text):
    assert _type(schema) == text

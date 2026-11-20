from tools.schema.render import OVERVIEW, PAGE_DIR, REBUILD, render, stale, write


def test_schema_doc_matches_models():
    bad = stale()
    assert not bad, f"app/models.py と合わない。`{REBUILD}` で作り直してコミットする: {bad}"


def test_every_table_is_on_exactly_one_page():
    from app.models import Base

    pages = {p: text for p, text in render().items() if p != OVERVIEW}
    for name in Base.metadata.tables:
        hits = [p for p, text in pages.items() if f"### {name}\n" in text]
        assert len(hits) == 1, (name, hits)


def test_stale_reports_changed_missing_and_leftover_pages(tmp_path):
    assert write(tmp_path)
    assert stale(tmp_path) == []
    pages = sorted((tmp_path / PAGE_DIR).glob("*.md"))
    pages[0].write_text("x", encoding="utf-8")
    pages[1].unlink()
    (tmp_path / PAGE_DIR / "gone.md").write_text("x", encoding="utf-8")
    assert stale(tmp_path) == sorted(
        [f"{PAGE_DIR}/{pages[0].name}", f"{PAGE_DIR}/{pages[1].name}", f"{PAGE_DIR}/gone.md"]
    )
    write(tmp_path)
    assert stale(tmp_path) == []


def test_tbls_has_every_table_and_reference_as_foreign_key():
    import json

    from app.models import Base
    from tools.schema.render import TBLS, declared_refs

    doc = json.loads(render()[TBLS])
    tables = {t["name"]: t for t in doc["tables"]}
    assert set(tables) == set(Base.metadata.tables)
    for name, t in Base.metadata.tables.items():
        fks = [c for c in tables[name]["constraints"] if c["type"] == "FOREIGN KEY"]
        names = [c["name"] for c in tables[name]["constraints"]]
        assert len(names) == len(set(names)), name
        want = {(tuple(c.name for c in fk.columns), fk.referred_table.name) for fk in t.foreign_key_constraints}
        want |= {(cols, target) for cols, target, _to in declared_refs(t)}
        assert {(tuple(c["columns"]), c["referenced_table"]) for c in fks} == want, name
    virtual = [r for r in doc["relations"] if r.get("virtual")]
    assert len(virtual) == sum(len(declared_refs(t)) for t in Base.metadata.tables.values())
    assert len(doc["relations"]) - len(virtual) == sum(len(t.foreign_key_constraints) for t in Base.metadata.tables.values())


def test_erd_link_hides_tables_outside_the_domain():
    import base64
    import zlib
    from urllib.parse import parse_qs, urlsplit

    from app.models import Base
    from tools.schema.render import ERD_PAGE, erd_link

    monthly = [t for n, t in Base.metadata.tables.items() if n.startswith("monthly_")]
    url = urlsplit(erd_link(monthly))
    assert url.path == ERD_PAGE
    packed = parse_qs(url.query)["hidden"][0]
    hidden = set(zlib.decompress(base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4))).decode().split(","))
    shown = set(Base.metadata.tables) - hidden
    assert shown == {t.name for t in monthly} | {"stocks"}
    assert erd_link() == ERD_PAGE


def test_model_types_have_a_note_and_comments_reach_the_doc():
    from sqlalchemy.types import TypeEngine

    from app.models import Base
    from tools.schema.render import TYPE_NOTES

    own = {type(c.type) for t in Base.metadata.tables.values() for c in t.columns
           if not type(c.type).__module__.startswith("sqlalchemy")}
    assert own == set(TYPE_NOTES)
    assert all(issubclass(k, TypeEngine) for k in own)
    pages = "\n".join(text for p, text in render().items() if p.endswith(".md"))
    for t in Base.metadata.tables.values():
        if t.comment:
            assert f"### {t.name}\n\n{t.comment}\n" in pages, t.name
        for c in t.columns:
            if c.comment:
                assert c.comment.replace("|", "\\|") in pages, f"{t.name}.{c.name}"


def test_value_meanings_reach_the_doc_and_tbls():
    import json

    from sqlalchemy import Enum

    from app.models import Base
    from tools.schema.render import TBLS

    out = render()
    pages = "\n".join(text for p, text in out.items() if p.endswith(".md"))
    notes = {(t["name"], c["name"]): c.get("comment", "") for t in json.loads(out[TBLS])["tables"] for c in t["columns"]}
    said = [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, Enum) and c.type.meanings]
    assert said
    for c in said:
        cell = "<br>".join(f"`{v}` {c.type.meanings[v]}" if v in c.type.meanings else f"`{v}`" for v in c.type.enums)
        assert f"| {cell} |" in pages, f"{c.table.name}.{c.name}"
        note = notes[(c.table.name, c.name)]
        for v, m in c.type.meanings.items():
            assert f"{v}（{m}）" in note, f"{c.table.name}.{c.name}"
    plain = [c for t in Base.metadata.tables.values() for c in t.columns if isinstance(c.type, Enum) and not c.type.meanings]
    for c in plain:
        assert "値: " + " / ".join(c.type.enums) in notes[(c.table.name, c.name)]


def test_every_table_links_to_an_existing_dataflow_heading():
    import re

    from tools.dataflow.render import PAGE_DIR as FLOW_DIR
    from tools.schema.render import ROOT

    pages = {p: text for p, text in render().items() if p.startswith(PAGE_DIR + "/")}
    for text in pages.values():
        for domain, table in re.findall(r"\(\.\./dataflow/(\w+)\.md#(\w+)\)", text):
            flow = (ROOT / FLOW_DIR / f"{domain}.md").read_text(encoding="utf-8")
            assert f"### {table}\n" in flow, (domain, table)


def test_unobserved_tables_say_so_instead_of_linking():
    text = "\n".join(t for p, t in render().items() if p.startswith(PAGE_DIR + "/"))
    assert "テストでは観測されていない" in text


def test_hand_written_states_page_is_not_reported_as_leftover():
    assert f"{PAGE_DIR}/states.md" not in stale()

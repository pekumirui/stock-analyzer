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

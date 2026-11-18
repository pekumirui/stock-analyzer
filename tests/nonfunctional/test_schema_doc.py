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

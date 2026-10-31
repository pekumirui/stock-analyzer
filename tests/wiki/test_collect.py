"""MkDocs wiki 用の Markdown 収集。元ファイルは動かさない。"""
from __future__ import annotations

from pathlib import Path

from tools.wiki.collect import build_nav, collect_into

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_collect_keeps_layout_and_rewrites_readme(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "README.md", "# 根\n\n[設計](DESIGN.md)\n")
    _write(repo / "DESIGN.md", "# 設計\n\n[README.md](README.md)\n")
    _write(repo / "docs" / "a.md", "# A\n\n[根](../README.md)\n\n[logs](logs/)\n")
    _write(repo / "data" / "skip.md", "# 除外\n")
    _write(repo / ".venv" / "hidden.md", "# venv\n")
    _write(repo / "node_modules" / "pkg" / "x.md", "# npm\n")
    _write(repo / "docs" / "pic.png", "x")
    _write(repo / "analyzer" / "README.md", "# analyzer\n")
    _write(repo / "docs" / "logs" / "a.json", "{}")
    dest = tmp_path / "out"

    result = collect_into(repo, dest)

    assert (dest / "index.md").is_file()
    assert not (dest / "README.md").exists()
    assert (dest / "DESIGN.md").is_file()
    assert (dest / "docs" / "a.md").is_file()
    assert (dest / "docs" / "pic.png").is_file()
    assert (dest / "docs" / "logs" / "a.json").is_file()
    assert (dest / "docs" / "logs" / "index.md").is_file()
    assert (dest / "docs" / "index.md").is_file()
    assert (dest / "analyzer" / "README.md").is_file()
    assert not (dest / "analyzer" / "index.md").exists()
    assert "](logs/index.md)" in (dest / "docs" / "a.md").read_text(encoding="utf-8")
    assert not (dest / "data").exists()
    assert not (dest / ".venv").exists()
    assert not (dest / "node_modules").exists()
    assert (repo / "README.md").read_text(encoding="utf-8").startswith("# 根")
    design = (dest / "DESIGN.md").read_text(encoding="utf-8")
    assert "[README.md](index.md)" in design
    assert "](../index.md)" in (dest / "docs" / "a.md").read_text(encoding="utf-8")
    assert "index.md" in result.pages
    titles = {next(iter(item)) for item in result.nav if isinstance(item, dict)}
    assert "根" in titles
    assert "docs" in titles


def test_build_nav_follows_folders() -> None:
    pages = ["index.md", "AGENTS.md", "docs/a.md", "docs/b.md"]
    titles = {
        "index.md": "ホーム",
        "AGENTS.md": "AGENTS",
        "docs/a.md": "A",
        "docs/b.md": "B",
    }
    nav = build_nav(pages, titles)
    assert nav[0] == {"ホーム": "index.md"}
    assert {"AGENTS": "AGENTS.md"} in nav
    docs = next(item["docs"] for item in nav if "docs" in item)
    assert docs == [{"A": "docs/a.md"}, {"B": "docs/b.md"}]


def test_collect_real_repo_excludes_generated(tmp_path: Path) -> None:
    dest = tmp_path / "wiki"
    result = collect_into(ROOT, dest)
    assert (dest / "index.md").is_file()
    assert (dest / "AGENTS.md").is_file()
    assert (dest / "docs" / "DESIGN_monthly.md").is_file()
    assert (dest / "docs" / "wiki-setup.md").is_file()
    assert (dest / "analyzer" / "README.md").is_file()
    assert not (dest / "data").exists()
    assert not any(p.startswith("data/") for p in result.pages)
    xbrl = (dest / "docs" / "DESIGN_xbrl.md").read_text(encoding="utf-8")
    assert "](../DESIGN.md)" in xbrl
    design = (dest / "DESIGN.md").read_text(encoding="utf-8")
    assert "(index.md)" in design
    assert (dest / "analyzer" / "momentum" / "spread_by_year_3m.png").is_file()
    cursor_pages = [p for p in result.pages if p.startswith(".cursor/")]
    assert cursor_pages

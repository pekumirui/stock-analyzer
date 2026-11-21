"""MkDocs wiki 用の Markdown 収集。対象は docs/ と AGENTS.md だけ。"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.wiki.collect import build_nav, collect_into, dest_rel_for

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_collect_docs_and_agents_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "README.md", "# 根\n")
    _write(repo / "DESIGN.md", "# 全体\n")
    _write(repo / "AGENTS.md", "# 約束\n")
    _write(repo / "app" / "api" / "AGENTS.md", "# api\n")
    _write(repo / "static" / "AGENTS.md", "# static\n")
    _write(
        repo / "docs" / "index.md",
        "# ホーム\n\n## 区分\n\n[A](a.md)\n[約束](../AGENTS.md)\n"
        "[api](../app/api/AGENTS.md)\n[static](../static/AGENTS.md)\n",
    )
    _write(
        repo / "docs" / "a.md",
        "# A\n\n[`DESIGN.md`](../DESIGN.md)\n"
        "[`AGENTS.md`](../AGENTS.md)\n[B](b.md)\n"
        "![pic](pic.png)\n",
    )
    _write(repo / "docs" / "b.md", "# B\n")
    _write(repo / "docs" / "pic.png", "x")
    _write(repo / "analyzer" / "README.md", "# analyzer\n")
    _write(repo / ".cursor" / "skills" / "SKILL.md", "# skill\n")
    _write(repo / ".cursor" / "AGENTS.md", "# cursor agents\n")
    _write(repo / "data" / "skip.md", "# 除外\n")
    dest = tmp_path / "out"

    result = collect_into(repo, dest)

    assert (dest / "index.md").is_file()
    assert (dest / "a.md").is_file()
    assert (dest / "b.md").is_file()
    assert (dest / "pic.png").is_file()
    assert (dest / "AGENTS.md").is_file()
    assert (dest / "app" / "api" / "AGENTS.md").is_file()
    assert (dest / "static" / "AGENTS.md").is_file()
    assert not (dest / "README.md").exists()
    assert not (dest / "DESIGN.md").exists()
    assert not (dest / "analyzer").exists()
    assert not (dest / ".cursor").exists()
    assert not (dest / "data").exists()
    assert (repo / "docs" / "a.md").read_text(encoding="utf-8").count(
        "](../DESIGN.md)"
    )
    copied = (dest / "a.md").read_text(encoding="utf-8")
    assert "](../DESIGN.md)" not in copied
    assert "`DESIGN.md`" in copied
    assert "](AGENTS.md)" in copied
    assert "](pic.png)" in copied
    home = (dest / "index.md").read_text(encoding="utf-8")
    assert "](AGENTS.md)" in home
    assert "index.md" in result.pages
    assert "app/api/AGENTS.md" in result.pages
    assert not any(p.startswith(".cursor/") for p in result.pages)


def test_dest_rel_strips_docs_keeps_agents() -> None:
    repo = Path("/repo")
    assert dest_rel_for(repo / "docs" / "a.md", repo) == Path("a.md")
    assert dest_rel_for(repo / "AGENTS.md", repo) == Path("AGENTS.md")
    assert dest_rel_for(repo / "app" / "api" / "AGENTS.md", repo) == Path(
        "app/api/AGENTS.md"
    )
    assert dest_rel_for(repo / "README.md", repo) is None
    assert dest_rel_for(repo / "DESIGN.md", repo) is None
    assert dest_rel_for(repo / "docs" / "pic.png", repo) == Path("pic.png")
    assert dest_rel_for(repo / "docs" / "dataflow" / "viewer.html", repo) == Path("dataflow/viewer.html")
    assert dest_rel_for(repo / "static" / "index.html", repo) is None
    assert dest_rel_for(repo / ".cursor" / "AGENTS.md", repo) is None
    assert dest_rel_for(repo / "analyzer" / "README.md", repo) is None


def test_build_nav_follows_home_sections_and_hangs_unlisted_pages_under_the_hub() -> None:
    home = "# ホーム\n\n[前置き](a.md)\n\n## 設計\n\n| [一覧](HUB.md) | x |\n- [A](a.md)\n\n## 約束\n\n- [`AGENTS.md`](AGENTS.md)\n"
    pages = ["index.md", "AGENTS.md", "HUB.md", "a.md", "hub/x.md", "hub/y.md"]
    titles = {"index.md": "ホーム", "HUB.md": "一覧", "hub/x.md": "一覧: X", "hub/y.md": "一覧: Y"}
    links = {"HUB.md": ["hub/y.md", "hub/x.md"], "a.md": ["hub/x.md"]}
    assert build_nav(home, pages, titles, links) == [
        {"ホーム": "index.md"},
        {"設計": [{"一覧": [{"一覧": "HUB.md"}, {"Y": "hub/y.md"}, {"X": "hub/x.md"}]}, {"A": "a.md"}]},
        {"約束": [{"AGENTS.md": "AGENTS.md"}]},
    ]


def test_build_nav_refuses_a_page_nothing_links_to() -> None:
    with pytest.raises(ValueError, match="lost.md"):
        build_nav("# ホーム\n\n## 設計\n\n[A](a.md)\n", ["index.md", "a.md", "lost.md"], {}, {})


def test_real_home_puts_every_page_in_the_nav(tmp_path: Path) -> None:
    result = collect_into(ROOT, tmp_path / "wiki")

    def pages(items: list) -> list[str]:
        return [p for it in items for v in it.values() for p in (pages(v) if isinstance(v, list) else [v])]

    listed = pages(result.nav)
    assert sorted(set(listed)) == result.pages


def test_collect_real_repo_scope(tmp_path: Path) -> None:
    dest = tmp_path / "wiki"
    result = collect_into(ROOT, dest)
    assert (dest / "index.md").is_file()
    assert (dest / "wiki-setup.md").is_file()
    assert (dest / "screen" / "monthly.md").is_file()
    assert (dest / "OVERVIEW.md").is_file()
    assert (dest / "AGENTS.md").is_file()
    assert (dest / "app" / "api" / "AGENTS.md").is_file()
    assert (dest / "static" / "AGENTS.md").is_file()
    assert not (dest / "app" / "api" / "index.md").exists()
    assert not (dest / "README.md").exists()
    assert not (dest / "DESIGN.md").exists()
    assert not (dest / "analyzer").exists()
    assert not (dest / ".cursor").exists()
    assert not (dest / "data").exists()
    assert not any(p.startswith("docs/") for p in result.pages)
    xbrl = (dest / "jobs" / "xbrl.md").read_text(encoding="utf-8")
    assert "](../OVERVIEW.md)" in xbrl
    assert "](../AGENTS.md)" in xbrl
    backup = (dest / "DESIGN_backup.md").read_text(encoding="utf-8")
    assert "](../deploy/README.md)" not in backup
    assert "deploy/README.md" in backup
    home = (dest / "index.md").read_text(encoding="utf-8")
    assert "](AGENTS.md)" in home
    assert "](app/api/AGENTS.md)" in home


def test_link_to_built_file_keeps_query_and_missing_one_is_dropped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / "docs" / "index.md", "# ホーム\n\n## 区分\n\n[S](SCHEMA.md)\n")
    _write(repo / "docs" / "SCHEMA.md", "# S\n\n[図](schema/erd/index.html?hidden=abc)\n[無い](gone/index.html)\n")
    dest = tmp_path / "out"

    def build(out: Path) -> None:
        _write(out / "schema" / "erd" / "index.html", "<html></html>")

    collect_into(repo, dest, build)
    text = (dest / "SCHEMA.md").read_text(encoding="utf-8")
    assert "[図](schema/erd/index.html?hidden=abc)" in text
    assert "](gone/index.html)" not in text

    collect_into(repo, dest)
    assert "](schema/erd/index.html" not in (dest / "SCHEMA.md").read_text(encoding="utf-8")

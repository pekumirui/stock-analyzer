"""MkDocs wiki 用の Markdown 収集。対象は docs/ と AGENTS.md だけ。"""
from __future__ import annotations

from pathlib import Path

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
        "# ホーム\n\n[A](a.md)\n[約束](../AGENTS.md)\n",
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


def test_build_nav_home_first() -> None:
    pages = ["index.md", "AGENTS.md", "a.md"]
    titles = {"index.md": "ホーム", "AGENTS.md": "約束", "a.md": "A"}
    nav = build_nav(pages, titles)
    assert nav[0] == {"ホーム": "index.md"}
    assert {"約束": "AGENTS.md"} in nav
    assert {"A": "a.md"} in nav


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
    assert (dest / "app" / "api" / "index.md").is_file()
    assert (dest / "static" / "index.md").is_file()
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

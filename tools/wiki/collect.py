"""docs/ の Markdown とリポジトリ内の AGENTS.md を MkDocs の docs_dir へ集める。"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "htmlcov",
        "dist",
        "build",
        ".tox",
        ".ipynb_checkpoints",
        "tmp",
        ".wiki-docs",
        "site",
        ".cursor",
        ".claude",
    }
)
ROOT_SKIP_DIR_NAMES = frozenset({"data", "forecast-exp", "DESIGN"})
# 画像と、1 枚で完結する HTML（自動生成の docs/dataflow/viewer.html）
ASSET_SUFFIXES = frozenset(
    {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".js"}
)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*<?([^>\s)]+)>?\s*\)")
HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
FRONT_MATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n?", re.DOTALL)

HEADERS_FILE = """\
/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
  X-Content-Type-Options: nosniff
"""


@dataclass
class CollectResult:
    pages: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    titles: dict[str, str] = field(default_factory=dict)
    nav: list = field(default_factory=list)


def collect_into(repo: Path, dest: Path, build: Callable[[Path], object] | None = None) -> CollectResult:
    repo = repo.resolve()
    dest = dest.resolve()
    _reset_dest(dest)

    sources: list[Path] = []
    images: list[Path] = []
    for path in _iter_repo_files(repo):
        mapped = dest_rel_for(path, repo)
        if mapped is None:
            continue
        if path.suffix.lower() == ".md":
            sources.append(path)
        elif path.suffix.lower() in ASSET_SUFFIXES:
            images.append(path)

    for src in sources:
        _copy_mapped(src, repo, dest)
    for src in images:
        _copy_mapped(src, repo, dest)

    if not (dest / "index.md").is_file():
        (dest / "index.md").write_text(
            "# 株式分析ツール wiki\n\n`docs/` の設計と `AGENTS.md`。\n",
            encoding="utf-8",
        )

    if build is not None:
        build(dest)
    _rewrite_links(dest, repo)
    _ensure_section_indexes(dest)
    (dest / "_headers").write_text(HEADERS_FILE, encoding="utf-8")

    pages = sorted(
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*.md")
        if p.is_file()
    )
    titles = {page: _page_title(dest / page, page) for page in pages}
    return CollectResult(
        pages=pages,
        assets=sorted(
            p.relative_to(dest).as_posix()
            for p in dest.rglob("*")
            if p.is_file()
            and p.suffix.lower() != ".md"
            and p.name != ".gitkeep"
        ),
        titles=titles,
        nav=build_nav(pages, titles),
    )


def dest_rel_for(src: Path, repo: Path) -> Path | None:
    rel = _rel_inside_repo(src, repo)
    if rel is None:
        return None
    suffix = src.suffix.lower()
    if suffix != ".md" and suffix not in ASSET_SUFFIXES:
        return None
    if rel.parts[:1] == ("docs",) and len(rel.parts) > 1:
        return Path(*rel.parts[1:])
    if src.name == "AGENTS.md":
        return rel
    return None


def build_nav(pages: list[str], titles: dict[str, str]) -> list:
    root: dict = {"files": [], "dirs": {}}
    for page in pages:
        parts = page.split("/")
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"files": [], "dirs": {}})
        node["files"].append(page)
    return _render_nav(root, titles)


def _render_nav(node: dict, titles: dict[str, str]) -> list:
    items: list = []
    for page in _sort_pages(node["files"], titles):
        items.append({titles.get(page, Path(page).stem): page})
    for name in sorted(node["dirs"].keys(), key=str.lower):
        children = _render_nav(node["dirs"][name], titles)
        if children:
            items.append({name: children})
    return items


def _sort_pages(pages: list[str], titles: dict[str, str]) -> list[str]:
    def key(page: str) -> tuple[int, str]:
        home = Path(page).name.lower() == "index.md"
        return (0 if home else 1, titles.get(page, page).lower())

    return sorted(pages, key=key)


def _reset_dest(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for child in dest.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _iter_repo_files(repo: Path):
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        rel_dir = Path(dirpath).relative_to(repo)
        at_root = rel_dir == Path(".")
        keep: list[str] = []
        for name in dirnames:
            if name in SKIP_DIR_NAMES:
                continue
            if at_root and name in ROOT_SKIP_DIR_NAMES:
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            yield Path(dirpath) / name


def _copy_mapped(src: Path, repo: Path, dest: Path) -> Path | None:
    mapped = dest_rel_for(src, repo)
    if mapped is None:
        return None
    target = dest / mapped
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return target


def _source_for_dest(current: Path, dest: Path, repo: Path) -> Path:
    rel = current.relative_to(dest)
    if rel.name == "AGENTS.md":
        return repo / rel
    return repo / "docs" / rel


def _rewrite_links(dest: Path, repo: Path) -> None:
    for md in dest.rglob("*.md"):
        src = _source_for_dest(md, dest, repo)
        text = md.read_text(encoding="utf-8", errors="replace")
        new = MD_LINK_RE.sub(
            lambda m, source=src, current=md: _replace_url(
                m, source, current, dest, repo
            ),
            text,
        )
        if new != text:
            md.write_text(new, encoding="utf-8")


def _replace_url(
    match: re.Match[str],
    source: Path,
    current: Path,
    dest: Path,
    repo: Path,
) -> str:
    url = match.group(2)
    target = _resolve_local(source, url)
    if target is None:
        return match.group(0)
    resolved = target.resolve()
    mapped = dest_rel_for(resolved, repo)
    if mapped is not None and (dest / mapped).is_file():
        dest_tgt = dest / mapped
        rest = url[len(re.split(r"[?#]", url, maxsplit=1)[0]):]
        new_url = Path(os.path.relpath(dest_tgt, current.parent)).as_posix()
        return f"[{match.group(1)}]({new_url}{rest})"
    if _is_http(url):
        return match.group(0)
    return _neutralize(match)


def _neutralize(match: re.Match[str]) -> str:
    text = match.group(1)
    url = match.group(2).split("#", 1)[0]
    stripped = text.strip("`")
    if stripped == url or stripped == Path(url).name:
        return text if text.startswith("`") else f"`{stripped}`"
    return f"{text} (`{url}`)"


def _is_http(url: str) -> bool:
    return url.startswith(("http://", "https://", "mailto:", "tel:"))


def _resolve_local(src_file: Path, url: str) -> Path | None:
    if _is_http(url):
        return None
    path_part = url.split("#", 1)[0].split("?", 1)[0]
    if not path_part or path_part.startswith("/"):
        return None
    return src_file.parent / path_part


def _ensure_section_indexes(dest: Path) -> None:
    md_files = [p for p in dest.rglob("*.md") if p.is_file()]
    dirs_with_md: set[Path] = set()
    for path in md_files:
        rel_parent = path.parent.relative_to(dest)
        if rel_parent == Path("."):
            continue
        current = rel_parent
        while current != Path("."):
            dirs_with_md.add(current)
            current = current.parent
    for rel in sorted(dirs_with_md, key=lambda p: len(p.parts)):
        index = dest / rel / "index.md"
        readme = dest / rel / "README.md"
        if index.exists() or readme.exists():
            continue
        folder = dest / rel
        child_pages = sorted(
            p.name for p in folder.glob("*.md") if p.name != "index.md"
        )
        child_dirs = sorted(
            p.name
            for p in folder.iterdir()
            if p.is_dir() and any(p.rglob("*.md"))
        )
        lines = [f"# {rel.name}", "", f"`{rel.as_posix()}`", ""]
        lines.extend(f"- [{name}]({name})" for name in child_pages)
        for name in child_dirs:
            child = folder / name
            href = (
                f"{name}/README.md"
                if (child / "README.md").is_file()
                else f"{name}/index.md"
            )
            lines.append(f"- [{name}]({href})")
        lines.append("")
        index.write_text("\n".join(lines), encoding="utf-8")


def _rel_inside_repo(src: Path, repo: Path) -> Path | None:
    try:
        rel = src.resolve().relative_to(repo)
    except ValueError:
        return None
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return None
    if rel.parts and rel.parts[0] in ROOT_SKIP_DIR_NAMES:
        return None
    return rel


def _page_title(path: Path, rel: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = FRONT_MATTER_RE.sub("", text, count=1)
    match = HEADING_RE.search(text)
    if match:
        return match.group(1).strip()
    if Path(rel).name.lower() == "index.md":
        return "ホーム"
    return Path(rel).stem


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    dest = repo / ".wiki-docs"
    result = collect_into(repo, dest)
    print(f"copied {len(result.pages)} pages → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

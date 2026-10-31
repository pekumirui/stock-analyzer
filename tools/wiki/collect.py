"""リポジトリ内の Markdown を MkDocs の docs_dir へ、配置を保ったまま集める。"""
from __future__ import annotations

import os
import re
import shutil
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
    }
)
ROOT_SKIP_DIR_NAMES = frozenset({"data", "forecast-exp", "DESIGN"})
IMAGE_SUFFIXES = frozenset(
    {".png", ".svg", ".jpg", ".jpeg", ".gif", ".webp"}
)
LINKED_SUFFIXES = IMAGE_SUFFIXES | {
    ".json",
    ".csv",
    ".txt",
    ".yaml",
    ".yml",
    ".html",
}
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^>\s)]+)>?\s*\)")
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


def collect_into(repo: Path, dest: Path) -> CollectResult:
    repo = repo.resolve()
    dest = dest.resolve()
    _reset_dest(dest)

    md_sources: list[Path] = []
    images: list[Path] = []
    for path in _iter_repo_files(repo):
        suffix = path.suffix.lower()
        if suffix == ".md":
            md_sources.append(path)
        elif suffix in IMAGE_SUFFIXES:
            images.append(path)

    root_readme = _root_readme(repo, md_sources)
    for src in md_sources:
        if src == root_readme:
            continue
        _copy_file(src, repo, dest)

    if root_readme is not None:
        index_dest = dest / "index.md"
        index_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root_readme, index_dest)

    for src in images:
        _copy_file(src, repo, dest)

    for src in md_sources:
        _copy_linked_extras(src, repo, dest)

    _rewrite_readme_links(dest, repo)
    _rewrite_dir_index_links(dest, repo)
    _ensure_section_indexes(dest)
    (dest / "_headers").write_text(HEADERS_FILE, encoding="utf-8")

    pages = sorted(
        p.relative_to(dest).as_posix()
        for p in dest.rglob("*.md")
        if p.is_file()
    )
    titles = {page: _page_title(dest / page, page) for page in pages}
    result = CollectResult(
        pages=pages,
        assets=sorted(
            p.relative_to(dest).as_posix()
            for p in dest.rglob("*")
            if p.is_file() and p.suffix.lower() != ".md" and p.name != ".gitkeep"
        ),
        titles=titles,
        nav=build_nav(pages, titles),
    )
    return result


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
        name = Path(page).name.lower()
        home = name == "index.md"
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


def _root_readme(repo: Path, md_sources: list[Path]) -> Path | None:
    for src in md_sources:
        if src.parent == repo and src.name.lower() == "readme.md":
            return src
    return None


def _copy_file(src: Path, repo: Path, dest: Path) -> Path | None:
    rel = _rel_inside_repo(src, repo)
    if rel is None:
        return None
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    return target


def _copy_linked_extras(md_src: Path, repo: Path, dest: Path) -> None:
    text = md_src.read_text(encoding="utf-8", errors="replace")
    for url in MD_LINK_RE.findall(text):
        target = _resolve_local(md_src, url)
        if target is None:
            continue
        if target.is_dir():
            copied = False
            for child in target.rglob("*"):
                if child.is_file() and child.suffix.lower() in LINKED_SUFFIXES:
                    _copy_file(child, repo, dest)
                    copied = True
            if copied:
                _write_dir_index(target, repo, dest)
            continue
        if target.is_file() and target.suffix.lower() in LINKED_SUFFIXES:
            _copy_file(target, repo, dest)


def _rewrite_readme_links(dest: Path, repo: Path) -> None:
    readme = repo / "README.md"
    if not readme.is_file():
        return
    readme_resolved = readme.resolve()
    for md in dest.rglob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        new = MD_LINK_RE.sub(
            lambda m, current=md: _replace_readme_url(
                m, current, dest, repo, readme_resolved
            ),
            text,
        )
        if new != text:
            md.write_text(new, encoding="utf-8")


def _source_for_dest(current: Path, dest: Path, repo: Path) -> Path:
    rel = current.relative_to(dest)
    if rel.as_posix() == "index.md":
        return repo / "README.md"
    return repo / rel


def _replace_readme_url(
    match: re.Match[str],
    current: Path,
    dest: Path,
    repo: Path,
    readme_resolved: Path,
) -> str:
    url = match.group(1)
    source = _source_for_dest(current, dest, repo)
    target = _resolve_local(source, url)
    if target is None or target.resolve() != readme_resolved:
        return match.group(0)
    rest = ""
    if "#" in url:
        rest = "#" + url.split("#", 1)[1]
    rel = Path(os.path.relpath(dest / "index.md", current.parent)).as_posix()
    full = match.group(0)
    start = match.start(1) - match.start(0)
    end = match.end(1) - match.start(0)
    return full[:start] + rel + rest + full[end:]


def _rewrite_dir_index_links(dest: Path, repo: Path) -> None:
    for md in dest.rglob("*.md"):
        text = md.read_text(encoding="utf-8", errors="replace")
        new = MD_LINK_RE.sub(
            lambda m, current=md: _replace_dir_index_url(m, current, dest, repo),
            text,
        )
        if new != text:
            md.write_text(new, encoding="utf-8")


def _replace_dir_index_url(
    match: re.Match[str],
    current: Path,
    dest: Path,
    repo: Path,
) -> str:
    url = match.group(1)
    source = _source_for_dest(current, dest, repo)
    target = _resolve_local(source, url)
    if target is None or not target.is_dir():
        return match.group(0)
    rel = _rel_inside_repo(target, repo)
    if rel is None:
        return match.group(0)
    index = dest / rel / "index.md"
    if not index.is_file():
        return match.group(0)
    rest = ""
    if "#" in url:
        rest = "#" + url.split("#", 1)[1]
    new_url = Path(os.path.relpath(index, current.parent)).as_posix()
    full = match.group(0)
    start = match.start(1) - match.start(0)
    end = match.end(1) - match.start(0)
    return full[:start] + new_url + rest + full[end:]


def _resolve_local(src_file: Path, url: str) -> Path | None:
    if url.startswith(("http://", "https://", "mailto:", "tel:")):
        return None
    path_part = url.split("#", 1)[0].split("?", 1)[0]
    if not path_part or path_part.startswith("/"):
        return None
    return (src_file.parent / path_part)


def _ensure_section_indexes(dest: Path) -> None:
    """フォルダをクリックしても 404 にならないよう、index.md が無い階層に目次を足す。"""
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
        # MkDocs は README.md をそのフォルダの index として使う。両方あると --strict で落ちる。
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
            if (child / "README.md").is_file():
                href = f"{name}/README.md"
            else:
                href = f"{name}/index.md"
            lines.append(f"- [{name}]({href})")
        lines.append("")
        index.write_text("\n".join(lines), encoding="utf-8")


def _write_dir_index(dir_src: Path, repo: Path, dest: Path) -> None:
    rel = _rel_inside_repo(dir_src, repo)
    if rel is None:
        return
    index = dest / rel / "index.md"
    if index.exists():
        return
    names = sorted(
        p.name
        for p in dir_src.iterdir()
        if p.is_file() and p.suffix.lower() in LINKED_SUFFIXES
    )
    if not names:
        return
    lines = [f"# {dir_src.name}", ""]
    lines.extend(f"- [{name}]({name})" for name in names)
    lines.append("")
    index.parent.mkdir(parents=True, exist_ok=True)
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

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from tools.dataflow.callgraph import VENDOR

SOURCE_ROOTS = ("app", "jobs", "mcp", "tools", "analyzer")
SPLIT_ROOTS = ("app", "jobs", "tools")
VENDOR_BOX = "vendor"
VENDOR_DIRS = tuple(v.rstrip("/") for v in VENDOR)


def python_files(root: Path) -> list[str]:
    out = []
    for top in SOURCE_ROOTS:
        base = root / top
        if base.is_dir():
            out += [p.relative_to(root).as_posix() for p in base.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(out)


def package_of(rel: str) -> str:
    if rel.startswith(tuple(d + "/" for d in VENDOR_DIRS)):
        return VENDOR_BOX
    parts = rel.split("/")
    if parts[0] in SPLIT_ROOTS and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _absolute(rel: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    here = rel.removesuffix(".py").split("/")[:-1]
    base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
    return ".".join(base + ([node.module] if node.module else []))


def _target_file(root: Path, dotted: str) -> str | None:
    parts = dotted.split(".")
    if parts[0] not in SOURCE_ROOTS:
        return None
    for i in range(len(parts), 0, -1):
        base = "/".join(parts[:i])
        if (root / f"{base}.py").is_file():
            return f"{base}.py"
        if (root / base / "__init__.py").is_file():
            return f"{base}/__init__.py"
    return None


def _is_module(root: Path, dotted: str) -> bool:
    base = dotted.replace(".", "/")
    return (root / f"{base}.py").is_file() or (root / base / "__init__.py").is_file()


def _targets(root: Path, rel: str, tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(t for t in (_target_file(root, a.name) for a in node.names) if t)
        elif isinstance(node, ast.ImportFrom):
            mod = _absolute(rel, node)
            names = [a.name for a in node.names]
            attrs = [n for n in names if n == "*" or not _is_module(root, f"{mod}.{n}")]
            out.update(t for t in (_target_file(root, f"{mod}.{n}") for n in names if n not in attrs) if t)
            base = _target_file(root, mod)
            if base and (attrs or not names):
                out.add(base)
    return out


def dependencies(root: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for rel in python_files(root):
        src = package_of(rel)
        if src == VENDOR_BOX:
            continue
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        out[src].update(package_of(t) for t in _targets(root, rel, tree) if package_of(t) != src)
    return {k: v for k, v in out.items() if v}

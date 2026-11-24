from __future__ import annotations

import re
from pathlib import Path

from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "jobs" / "market.md"
_SECTION = re.compile(r"^### 実行手順\n(.*?)(?=^### )", re.M | re.S)
_DIAGRAM = re.compile(
    r"```mermaid\n---\nconfig:\n  layout: elk\n---\nflowchart TD\n(.*?)```",
    re.S,
)
_BASIS = re.compile(r"`([\w/.]+\.py)` `(\w+)`")
_TICK = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def _section() -> str:
    body = _SECTION.search(PAGE.read_text(encoding="utf-8"))
    assert body
    return body.group(1)


def _diagram() -> str:
    found = _DIAGRAM.search(_section())
    assert found, "実行手順に ELK の flowchart が無い"
    return found.group(1)


def test_basis_functions_exist_and_the_diagram_names_them():
    rows = _BASIS.findall(_section())
    assert rows
    named = set(_TICK.findall(_diagram()))
    for path, func in rows:
        source = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"^\s*(?:async )?def {func}\b", source, re.M), (path, func)
        assert func in named, func


def test_diagram_ticks_are_tables_or_those_functions():
    named = set(_TICK.findall(_diagram()))
    tables = set(Base.metadata.tables)
    funcs = {func for _, func in _BASIS.findall(_section())}
    assert named & tables
    assert named <= tables | funcs

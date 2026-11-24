from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Enum

from app.models import Base

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "schema" / "states.md"
_SECTION = re.compile(r"^## (\w+)\.(\w+)\n(.*?)(?=^## |\Z)", re.M | re.S)
_DIAGRAM = re.compile(r"```mermaid\n---\nconfig:\n  layout: elk\n---\nstateDiagram-v2\n(.*?)```", re.S)
_EDGE = re.compile(r"^\s+(\[\*\]|\w+) --> (\[\*\]|\w+)(?::.*)?$", re.M)
_ROW = re.compile(r"^\| `(\[\*\]|\w+)` \| `(\[\*\]|\w+)` \| .*? \| (.+) \|$", re.M)
_BASIS = re.compile(r"`([\w/.]+\.py)` `(\w+)`")


def _sections() -> dict[tuple[str, str], str]:
    text = PAGE.read_text(encoding="utf-8")
    return {(m.group(1), m.group(2)): m.group(3) for m in _SECTION.finditer(text) if _DIAGRAM.search(m.group(3))}


SECTIONS = _sections()


def _enum_values(table: str, column: str) -> set[str]:
    col = Base.metadata.tables[table].columns[column]
    assert isinstance(col.type, Enum), f"{table}.{column} は Enum ではない"
    return set(col.type.enums)


def _edges(body: str) -> set[tuple[str, str]]:
    diagram = _DIAGRAM.search(body)
    assert diagram
    return set(_EDGE.findall(diagram.group(1)))


def test_the_page_has_state_diagrams():
    assert len(SECTIONS) >= 3


@pytest.mark.parametrize(("table", "column"), sorted(SECTIONS))
def test_diagram_states_are_exactly_the_enum_values(table, column):
    states = {s for edge in _edges(SECTIONS[(table, column)]) for s in edge} - {"[*]"}
    values = _enum_values(table, column)
    assert states == values, {"図にだけある": sorted(states - values), "Enum にだけある": sorted(values - states)}


@pytest.mark.parametrize(("table", "column"), sorted(SECTIONS))
def test_every_arrow_has_a_row_with_a_basis_and_the_other_way_round(table, column):
    body = SECTIONS[(table, column)]
    rows = {(a, b): basis for a, b, basis in _ROW.findall(body)}
    assert set(rows) == _edges(body)
    for edge, basis in rows.items():
        found = _BASIS.findall(basis)
        assert found, edge
        for path, func in found:
            source = (ROOT / path).read_text(encoding="utf-8")
            assert re.search(rf"^\s*(?:async )?def {func}\b", source, re.M), (edge, path, func)


@pytest.mark.parametrize(("table", "column"), sorted(SECTIONS))
def test_every_diagram_says_where_a_row_starts(table, column):
    edges = _edges(SECTIONS[(table, column)])
    assert any(a == "[*]" for a, _ in edges)

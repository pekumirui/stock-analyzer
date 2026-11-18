from __future__ import annotations

from pathlib import Path

from sqlalchemy import Enum, Table
from sqlalchemy.dialects import sqlite

from app.models import Base
from tools.dataflow.render import DOMAINS, HUB, table_domain

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = "docs/SCHEMA.md"
PAGE_DIR = "docs/schema"
REBUILD = "python -m tools.schema.render"
_DIALECT = sqlite.dialect()
MASTERS = HUB | {"edinet_filers"}


def _tables() -> list[Table]:
    return sorted(Base.metadata.tables.values(), key=lambda t: t.name)


def _type(col) -> str:
    return col.type.compile(dialect=_DIALECT)


def _refs(col) -> list[str]:
    return sorted(fk.target_fullname for fk in col.foreign_keys)


def _default(col) -> str:
    if col.server_default is not None:
        return f"`{col.server_default.arg}`（DB）"
    if col.default is None:
        return ""
    if col.default.is_scalar:
        return f"`{col.default.arg!r}`"
    return "（関数）"


def _names(text: str) -> tuple[str, ...]:
    return tuple(n.strip() for n in text.split(","))


def _target(spec: str) -> tuple[str, tuple[str, ...]]:
    table, _, cols = spec.partition(".")
    return table, _names(cols)


def declared_refs(t: Table) -> list[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    out = [((c.name,), *_target(c.info["ref"])) for c in t.columns if "ref" in c.info]
    out += [(_names(cols), *_target(spec)) for cols, spec in t.info.get("ref", {}).items()]
    return out


def _spell(table: str, cols: tuple[str, ...]) -> str:
    return f"{table}.{cols[0]}" if len(cols) == 1 else f"{table}.({', '.join(cols)})"


def _key(col) -> str:
    out = ["PK"] if col.primary_key else []
    out += [f"FK → `{r}`" for r in _refs(col)]
    out += dict.fromkeys(f"→ `{_spell(table, to)}`（制約なし）" for cols, table, to in declared_refs(col.table) if col.name in cols)
    return " / ".join(out)


def _values(col) -> str:
    if not isinstance(col.type, Enum):
        return ""
    return " / ".join(f"`{v}`" for v in col.type.enums)


def _table_section(t: Table) -> list[str]:
    lines = [f"### {t.name}", ""]
    if t.dialect_options["sqlite"].get("with_rowid") is False:
        lines += ["`WITHOUT ROWID`", ""]
    lines += ["| 列 | 型 | NULL | キー | 値 | 既定 |", "|---|---|---|---|---|---|"]
    for c in t.columns:
        null = "" if not c.nullable else "可"
        lines.append(f"| `{c.name}` | {_type(c)} | {null} | {_key(c)} | {_values(c)} | {_default(c)} |")
    lines.append("")
    uniques = sorted(
        (u for u in t.constraints if type(u).__name__ == "UniqueConstraint"),
        key=lambda u: u.name or "",
    )
    indexes = sorted(t.indexes, key=lambda i: i.name or "")
    if uniques or indexes:
        lines += ["| 索引・一意 | 列 | 一意 |", "|---|---|---|"]
        for u in uniques:
            cols = ", ".join(c.name for c in u.columns)
            lines.append(f"| `{u.name}` | {cols} | 一意 |")
        for i in indexes:
            cols = ", ".join(str(getattr(e, "name", e)) for e in i.expressions)
            lines.append(f"| `{i.name}` | {cols} | {'一意' if i.unique else ''} |")
        lines.append("")
    return lines


def _links(t: Table):
    for c in t.columns:
        for fk in c.foreign_keys:
            yield (c.name,), fk.column.table, (fk.column.name,), "--"
    for cols, table, to in declared_refs(t):
        if table not in MASTERS:
            yield cols, Base.metadata.tables[table], to, ".."


def _unique(t: Table, cols: tuple[str, ...]) -> bool:
    keys = [{c.name for c in t.primary_key.columns}]
    keys += [{c.name for c in u.columns} for u in t.constraints if type(u).__name__ == "UniqueConstraint"]
    keys += [{c.name for c in i.columns} for i in t.indexes if i.unique]
    return set(cols) in keys


def _mermaid(own: list[Table]) -> list[str]:
    own_names = {t.name for t in own}
    edges = []
    shown: dict[str, set[str]] = {t.name: set() for t in own}
    linked: dict[str, set[str]] = {t.name: set() for t in own}
    tables = {t.name: t for t in own}
    for t in own:
        for cols, target, to, line in _links(t):
            tables[target.name] = target
            shown.setdefault(target.name, set()).update(to)
            linked[t.name].update(cols)
            optional = any(t.columns[c].nullable for c in cols)
            many = not _unique(target, to)
            left = ("}" if many else "|") + ("o" if optional else "|")
            label = cols[0] if len(cols) == 1 else f'"{", ".join(cols)}"'
            edges.append(f"  {target.name} {left}{line}o{{ {t.name} : {label}")
    lines = ["```mermaid", "erDiagram"]
    for name in sorted(tables):
        t = tables[name]
        keep = linked.get(name, set()) | shown[name]
        cols = [c for c in t.columns if c.primary_key or c.name in keep]
        lines.append(f"  {name} {{")
        for c in cols:
            fk = name in own_names and c.name in linked[name]
            mark = ", ".join(m for m, on in (("PK", c.primary_key), ("FK", fk)) if on)
            lines.append(f"    {type(c.type).__name__} {c.name} {mark}")
        lines.append("  }")
    lines += sorted(edges)
    lines.append("```")
    return lines


def _page(key: str, label: str, own: list[Table]) -> str:
    lines = [
        f"# スキーマ: {label}",
        "",
        f"**自動生成。手で直さない。** 正は `app/models.py`。作り直しは `{REBUILD}`。全体は [SCHEMA.md](../SCHEMA.md)。",
        "",
        "図は主キーと参照の列だけ。実線は外部キー、点線はモデルで宣言しただけの参照（DB の制約は無い）。`stocks` と `edinet_filers` への参照は線にしない（表には出す）。親の側が `}|` の線は、親の列が一意でない（同じ値の行が親に何行もある）。ほかの機能の表は主キーと指される列だけ出す。",
        "",
        *_mermaid(own),
        "",
        "## テーブル",
        "",
    ]
    for t in own:
        lines += _table_section(t)
    return "\n".join(lines)


def render() -> dict[str, str]:
    tables = _tables()
    out = {}
    rows = []
    for key, label in DOMAINS:
        own = [t for t in tables if table_domain(t.name) == key]
        if not own:
            continue
        out[f"{PAGE_DIR}/{key}.md"] = _page(key, label, own)
        names = " ".join(f"`{t.name}`" for t in own)
        rows.append(f"| [{label}](schema/{key}.md) | {len(own)} | {names} |")
    out[OVERVIEW] = "\n".join([
        "# DB スキーマ（テーブル・列・ER 図）",
        "",
        f"**このファイルと `{PAGE_DIR}/` は自動生成。手で直さない。** 正は `app/models.py`。作り直しは `{REBUILD}`。",
        "",
        "合わなければ `tests/nonfunctional/test_schema_doc.py` が落ちる。",
        "",
        "| 機能 | テーブル数 | テーブル |",
        "|---|---|---|",
        *rows,
        "",
    ])
    return out


def stale(root: Path = ROOT) -> list[str]:
    want = render()
    bad = [p for p, text in want.items() if not (root / p).is_file() or (root / p).read_text(encoding="utf-8") != text]
    page_dir = root / PAGE_DIR
    if page_dir.is_dir():
        bad += [f"{PAGE_DIR}/{p.name}" for p in page_dir.glob("*.md") if f"{PAGE_DIR}/{p.name}" not in want]
    return sorted(bad)


def write(root: Path = ROOT) -> list[str]:
    changed = stale(root)
    want = render()
    for p in changed:
        path = root / p
        if p in want:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want[p], encoding="utf-8")
        else:
            path.unlink()
    return changed


if __name__ == "__main__":
    for p in write():
        print(p)

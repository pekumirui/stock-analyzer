from __future__ import annotations

from pathlib import Path

from sqlalchemy import Table
from sqlalchemy.dialects import sqlite

from app.models import Base
from tools.dataflow.render import DOMAINS, table_domain

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = "docs/SCHEMA.md"
PAGE_DIR = "docs/schema"
REBUILD = "python -m tools.schema.render"
_DIALECT = sqlite.dialect()


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


def _key(col) -> str:
    out = ["PK"] if col.primary_key else []
    out += [f"FK → `{r}`" for r in _refs(col)]
    return " / ".join(out)


def _table_section(t: Table) -> list[str]:
    lines = [f"### {t.name}", ""]
    if t.dialect_options["sqlite"].get("with_rowid") is False:
        lines += ["`WITHOUT ROWID`", ""]
    lines += ["| 列 | 型 | NULL | キー | 既定 |", "|---|---|---|---|---|"]
    for c in t.columns:
        null = "" if not c.nullable else "可"
        lines.append(f"| `{c.name}` | {_type(c)} | {null} | {_key(c)} | {_default(c)} |")
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


def _mermaid(own: list[Table]) -> list[str]:
    own_names = {t.name for t in own}
    edges = []
    outside = {}
    for t in own:
        for c in t.columns:
            for fk in c.foreign_keys:
                target = fk.column.table
                if target.name not in own_names:
                    outside[target.name] = target
                left = "|o" if c.nullable else "||"
                edges.append(f"  {target.name} {left}--o{{ {t.name} : {c.name}")
    lines = ["```mermaid", "erDiagram"]
    for t in sorted([*own, *outside.values()], key=lambda t: t.name):
        cols = [c for c in t.columns if c.primary_key or (c.foreign_keys and t.name in own_names)]
        lines.append(f"  {t.name} {{")
        for c in cols:
            mark = ", ".join(m for m, on in (("PK", c.primary_key), ("FK", bool(c.foreign_keys))) if on)
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
        "図は主キーと外部キーの列だけ。線は外部キー（列名つき）。ほかの機能の表は主キーだけ出す。",
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

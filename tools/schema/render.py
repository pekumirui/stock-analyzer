from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

from sqlalchemy import Enum, Table
from sqlalchemy.dialects import sqlite

from app.models import Base, JstDateTime, UtcDateTime
from tools.schema.erd import ERD_DIR, TBLS
from tools.dataflow.render import DOMAINS, PAGE_DIR as FLOW_DIR, table_domain

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = "docs/SCHEMA.md"
PAGE_DIR = "docs/schema"
REBUILD = "python -m tools.schema.render"
HAND_WRITTEN = (f"{PAGE_DIR}/states.md",)
ERD_PAGE = f"{ERD_DIR}/index.html"
_DIALECT = sqlite.dialect()
LAYOUT = ["---", "config:", "  layout: elk", "---"]
TYPE_NOTES = {
    JstDateTime: "日本時間をタイムゾーンなしで持つ",
    UtcDateTime: "UTC をタイムゾーンなしで持つ",
}


def _tables() -> list[Table]:
    return sorted(Base.metadata.tables.values(), key=lambda t: t.name)


def _type(col) -> str:
    return col.type.compile(dialect=_DIALECT)


def _own_type(col) -> type | None:
    return type(col.type) if type(col.type) in TYPE_NOTES else None


def _base_type(col) -> str:
    return next(k.__name__ for k in type(col.type).__mro__ if k.__module__.startswith("sqlalchemy"))


def _type_cell(col) -> str:
    own = _own_type(col)
    return _type(col) if own is None else f"{_type(col)}（`{own.__name__}`）"


def _cell(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _type_legend(tables: list[Table]) -> list[str]:
    used = {_own_type(c) for t in tables for c in t.columns} - {None}
    return [f"`{k.__name__}`: {TYPE_NOTES[k]}。" for k in sorted(used, key=lambda k: k.__name__)]


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


def meanings(col) -> dict[str, str]:
    return getattr(col.type, "meanings", {})


def _values(col) -> str:
    if not isinstance(col.type, Enum):
        return ""
    said = meanings(col)
    if not said:
        return " / ".join(f"`{v}`" for v in col.type.enums)
    return "<br>".join(f"`{v}` {_cell(said[v])}" if v in said else f"`{v}`" for v in col.type.enums)


def _flow_line(table: str) -> str:
    domain = table_domain(table)
    page = ROOT / FLOW_DIR / f"{domain}.md"
    seen = page.is_file() and f"### {table}\n" in page.read_text(encoding="utf-8")
    head = "書く入口・読むだけの入口（バッチと API）: "
    return head + (f"[データの流れ](../dataflow/{domain}.md#{table})" if seen else "テストでは観測されていない")


def _table_section(t: Table) -> list[str]:
    lines = [f"### {t.name}", ""]
    if t.comment:
        lines += [t.comment, ""]
    lines += [_flow_line(t.name), ""]
    if t.dialect_options["sqlite"].get("with_rowid") is False:
        lines += ["`WITHOUT ROWID`", ""]
    lines += ["| 列 | 型 | NULL | キー | 値 | 既定 | 説明 |", "|---|---|---|---|---|---|---|"]
    for c in t.columns:
        null = "" if not c.nullable else "可"
        lines.append(
            f"| `{c.name}` | {_type_cell(c)} | {null} | {_key(c)} | {_values(c)} | {_default(c)} | {_cell(c.comment)} |"
        )
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
    lines = ["```mermaid", *LAYOUT, "erDiagram", "  direction LR"]
    for name in sorted(tables):
        t = tables[name]
        keep = linked.get(name, set()) | shown[name]
        cols = [c for c in t.columns if c.primary_key or c.name in keep]
        lines.append(f"  {name} {{")
        for c in cols:
            fk = name in own_names and c.name in linked[name]
            mark = ", ".join(m for m, on in (("PK", c.primary_key), ("FK", fk)) if on)
            lines.append(f"    {_base_type(c)} {c.name} {mark}")
        lines.append("  }")
    lines += sorted(edges)
    lines.append("```")
    return lines


def _cols(cols) -> str:
    return ", ".join(cols)


def _ref_name(t: Table, kind: str, cols: tuple[str, ...]) -> str:
    return f"{kind}_{t.name}_{'_'.join(cols)}"


def _fk_links(t: Table):
    for fk in sorted(t.foreign_key_constraints, key=lambda f: [c.name for c in f.columns]):
        cols = tuple(c.name for c in fk.columns)
        to = tuple(e.column.name for e in fk.elements)
        action = f" ON DELETE {fk.ondelete}" if fk.ondelete else ""
        yield cols, fk.referred_table.name, to, fk.name or _ref_name(t, "fk", cols), action


def _tbls_default(col) -> str | None:
    if col.server_default is not None:
        return str(col.server_default.arg)
    if col.default is None or not col.default.is_scalar:
        return None
    return str(col.default.arg)


def _tbls_note(col) -> str:
    out = [col.comment] if col.comment else []
    own = _own_type(col)
    if own is not None:
        out.append(f"{own.__name__}: {TYPE_NOTES[own]}")
    out += [f"FK → {r}" for r in _refs(col)]
    out += dict.fromkeys(
        f"→ {_spell(table, to)}（宣言だけ。DB の制約なし）" for cols, table, to in declared_refs(col.table) if col.name in cols
    )
    if isinstance(col.type, Enum):
        said = meanings(col)
        out.append("値: " + " / ".join(f"{v}（{said[v]}）" if v in said else v for v in col.type.enums).replace("） / ", "）/ "))
    return "\n".join(out)


def _tbls_column(col) -> dict:
    out = {"name": col.name, "type": _type(col), "nullable": bool(col.nullable), "default": _tbls_default(col)}
    note = _tbls_note(col)
    if note:
        out["comment"] = note
    return out


def _tbls_table(t: Table, label: str) -> dict:
    pk = tuple(c.name for c in t.primary_key.columns)
    cons = [{"name": f"pk_{t.name}", "type": "PRIMARY KEY", "def": f"PRIMARY KEY ({_cols(pk)})", "table": t.name, "columns": list(pk)}]
    for cols, target, to, name, action in _fk_links(t):
        cons.append({
            "name": name,
            "type": "FOREIGN KEY",
            "def": f"FOREIGN KEY ({_cols(cols)}) REFERENCES {target} ({_cols(to)}){action}",
            "table": t.name,
            "referenced_table": target,
            "columns": list(cols),
            "referenced_columns": list(to),
        })
    for cols, target, to in declared_refs(t):
        cons.append({
            "name": _ref_name(t, "ref", cols),
            "type": "FOREIGN KEY",
            "def": f"宣言だけ（DB の制約なし）: ({_cols(cols)}) → {target} ({_cols(to)})",
            "table": t.name,
            "referenced_table": target,
            "columns": list(cols),
            "referenced_columns": list(to),
        })
    uniques = sorted((u for u in t.constraints if type(u).__name__ == "UniqueConstraint"), key=lambda u: u.name or "")
    for u in uniques:
        cols = [c.name for c in u.columns]
        cons.append({"name": u.name or _ref_name(t, "uq", tuple(cols)), "type": "UNIQUE", "def": f"UNIQUE ({_cols(cols)})", "table": t.name, "columns": cols})
    indexes = []
    for i in sorted(t.indexes, key=lambda i: i.name or ""):
        cols = [str(getattr(e, "name", e)) for e in i.expressions]
        unique = "UNIQUE " if i.unique else ""
        indexes.append({"name": i.name, "def": f"CREATE {unique}INDEX {i.name} ON {t.name} ({_cols(cols)})", "table": t.name, "columns": cols})
    comment = f"機能: {label}"
    if t.dialect_options["sqlite"].get("with_rowid") is False:
        comment += " / WITHOUT ROWID"
    if t.comment:
        comment = f"{t.comment}\n{comment}"
    return {
        "name": t.name,
        "type": "table",
        "comment": comment,
        "columns": [_tbls_column(c) for c in t.columns],
        "indexes": indexes,
        "constraints": cons,
    }


def _tbls_relations(t: Table) -> list[dict]:
    out = [
        {"table": t.name, "columns": list(cols), "parent_table": target, "parent_columns": list(to), "def": f"FOREIGN KEY ({_cols(cols)}) REFERENCES {target} ({_cols(to)}){action}"}
        for cols, target, to, _name, action in _fk_links(t)
    ]
    out += [
        {"table": t.name, "columns": list(cols), "parent_table": target, "parent_columns": list(to), "def": f"宣言だけ（DB の制約なし）: ({_cols(cols)}) → {target} ({_cols(to)})", "virtual": True}
        for cols, target, to in declared_refs(t)
    ]
    return out


def tbls() -> dict:
    label = dict(DOMAINS)
    tables = _tables()
    return {
        "name": "stock-analyzer",
        "desc": f"自動生成。正は app/models.py。作り直しは {REBUILD}",
        "tables": [_tbls_table(t, label[table_domain(t.name)]) for t in tables],
        "relations": [r for t in tables for r in _tbls_relations(t)],
        "driver": {"name": "sqlite"},
    }


def erd_link(own: list[Table] | None = None) -> str:
    if own is None:
        return ERD_PAGE
    keep = {t.name for t in own} | {target.name for t in own for _c, target, _t, _l in _links(t)}
    hidden = ",".join(n for n in sorted(Base.metadata.tables) if n not in keep)
    packed = base64.urlsafe_b64encode(zlib.compress(hidden.encode())).decode().rstrip("=")
    return f"{ERD_PAGE}?hidden={packed}"


def _page(key: str, label: str, own: list[Table]) -> str:
    lines = [
        f"# スキーマ: {label}",
        "",
        f"**自動生成。手で直さない。** 正は `app/models.py`。作り直しは `{REBUILD}`。全体は [SCHEMA.md](../SCHEMA.md)。",
        "",
        "図は主キーと参照の列だけ。実線は外部キー、点線はモデルで宣言しただけの参照（DB の制約は無い）。親の側が `}|` の線は、親の列が一意でない（同じ値の行が親に何行もある）。ほかの機能の表は主キーと指される列だけ出す。",
        "",
        *_mermaid(own),
        "",
        "## テーブル",
        "",
        "見出しの下の文と「説明」の列は、モデルの `comment`（DB には出ない）。型の列は DDL の型で、括弧の中はモデルの型。",
        "",
    ]
    legend = _type_legend(own)
    if legend:
        lines += [" ".join(legend), ""]
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
        rows.append(f"| [{label}](schema/{key}.md) | [開く]({erd_link(own)}) | {len(own)} | {names} |")
    out[OVERVIEW] = "\n".join([
        "# DB スキーマ（テーブル・列・ER 図）",
        "",
        f"**このファイルと `{PAGE_DIR}/` は自動生成。手で直さない。** 正は `app/models.py`。作り直しは `{REBUILD}`。",
        "",
        "合わなければ `tests/nonfunctional/test_schema_doc.py` が落ちる。",
        "",
        (
            f"**[動かせる ER 図（Liam ERD）]({erd_link()})**: 全テーブルを 1 枚に出す。"
            "拡大・移動・テーブル名の検索ができ、テーブルを押すとつながる相手だけが光る。"
            "表の「Liam」の列は、その機能のテーブルと、そこから指す先のテーブルだけを出す。"
            f"wiki のビルドで作るので wiki でだけ開ける（リポジトリには入力の `{TBLS}` だけを置く）。"
        ),
        "",
        (
            "Liam の図では外部キーも宣言だけの参照も同じ線になる。"
            "見分けは制約名（`fk_` が外部キー、`ref_` が宣言だけ）と列の説明。"
        ),
        "",
        "| 機能 | Liam | テーブル数 | テーブル |",
        "|---|---|---|---|",
        *rows,
        "",
    ])
    out[TBLS] = json.dumps(tbls(), ensure_ascii=False, indent=1) + "\n"
    return out


def stale(root: Path = ROOT) -> list[str]:
    want = render()
    bad = [p for p, text in want.items() if not (root / p).is_file() or (root / p).read_text(encoding="utf-8") != text]
    page_dir = root / PAGE_DIR
    if page_dir.is_dir():
        bad += [
            f"{PAGE_DIR}/{p.name}" for p in page_dir.glob("*.md")
            if f"{PAGE_DIR}/{p.name}" not in want and f"{PAGE_DIR}/{p.name}" not in HAND_WRITTEN
        ]
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

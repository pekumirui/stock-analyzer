from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Arg:
    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: str
    path: str
    args: tuple[Arg, ...]


def load_tools(root: Path) -> list[Tool]:
    found: list[Tool] = []
    seen: set[str] = set()
    for path in sorted((root / "mcp").glob("*.py")):
        rel = path.relative_to(root).as_posix()
        for tool in _tools_in(path.read_text(encoding="utf-8"), rel):
            if tool.name in seen:
                raise ValueError(f"MCP のツール名が重なる: {tool.name}")
            seen.add(tool.name)
            found.append(tool)
    if not found:
        raise ValueError("mcp/*.py に TOOLS が無い")
    return found


def _tools_in(source: str, rel: str) -> list[Tool]:
    tree = ast.parse(source)
    out = []
    for node in tree.body:
        value = _tools_value(node)
        if value is None:
            continue
        if not isinstance(value, ast.Dict):
            raise ValueError(f"{rel} の TOOLS が辞書ではない")
        for key, raw in zip(value.keys, value.values):
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                raise ValueError(f"{rel} の TOOLS のキーが文字列ではない")
            if not isinstance(raw, ast.Tuple) or len(raw.elts) != 3:
                raise ValueError(f"{rel} の {key.value} は (説明, schema, 関数) ではない")
            desc, schema, handler = raw.elts
            out.append(Tool(
                key.value,
                _text(desc).strip(),
                _handler(handler),
                rel,
                tuple(_args(schema, f"{rel} {key.value}")),
            ))
    return out


def _tools_value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOOLS":
        return node.value
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets):
        return node.value
    return None


def _text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                hole = ast.unparse(value.value)
                if value.format_spec is not None:
                    hole += ":" + _text(value.format_spec)
                parts.append("{" + hole + "}")
            else:
                parts.append("{" + ast.unparse(value) + "}")
        return "".join(parts)
    if isinstance(node, ast.Tuple):
        return "".join(_text(elt) for elt in node.elts)
    raise ValueError(f"説明が文字列ではない: {ast.unparse(node)}")


def _handler(node: ast.AST) -> str:
    if isinstance(node, (ast.Name, ast.Attribute)):
        return ast.unparse(node)
    raise ValueError(f"関数が名前ではない: {ast.unparse(node)}")


def _mapping(node: ast.AST) -> dict[str, ast.AST]:
    if not isinstance(node, ast.Dict):
        raise ValueError(f"schema が辞書ではない: {ast.unparse(node)}")
    out = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError(f"schema のキーが文字列ではない: {ast.unparse(key) if key else '**'}")
        if key.value in out:
            raise ValueError(f"schema のキーが重なる: {key.value}")
        out[key.value] = value
    return out


def _args(schema: ast.AST, where: str) -> list[Arg]:
    try:
        fields = _mapping(schema)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc
    required = _required(fields.get("required"))
    props = fields.get("properties")
    if props is None:
        return []
    out = []
    for name, raw in _mapping(props).items():
        body = _mapping(raw)
        out.append(Arg(name, _type_of(body), name in required, _text(body["description"]).strip() if "description" in body else ""))
    return out


def _required(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    if not isinstance(node, ast.List) or not all(isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in node.elts):
        raise ValueError(f"required が文字列の配列ではない: {ast.unparse(node)}")
    return {elt.value for elt in node.elts}


def _type_of(body: dict[str, ast.AST]) -> str:
    if "enum" in body:
        return _enum(body["enum"])
    type_node = body.get("type")
    type_s = _simple(type_node) if type_node is not None else ""
    if type_s == "array" and isinstance(body.get("items"), ast.Dict):
        inner = _type_of(_mapping(body["items"]))
        if inner:
            return f"array[{inner}]"
    return type_s


def _simple(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.List):
        return " / ".join(_simple(elt) for elt in node.elts)
    return ast.unparse(node)


def _enum(node: ast.AST) -> str:
    if isinstance(node, ast.List) and all(isinstance(elt, ast.Constant) for elt in node.elts):
        return " / ".join(str(elt.value) for elt in node.elts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "list" and len(node.args) == 1 and not node.keywords:
        return ast.unparse(node.args[0])
    return ast.unparse(node)

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Arg:
    names: tuple[str, ...]
    kind: str
    default: str
    help: str

    @property
    def positional(self) -> bool:
        return not self.names[0].startswith("-")


def _text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _choices(node: ast.AST) -> str:
    try:
        return " / ".join(str(v) for v in ast.literal_eval(node))
    except ValueError:
        return ast.unparse(node)


def _kind(kw: dict[str, ast.AST]) -> str:
    action = _text(kw.get("action"))
    if action in ("store_true", "store_false"):
        return "フラグ"
    parts = []
    if "choices" in kw:
        parts.append(_choices(kw["choices"]))
    elif "type" in kw:
        parts.append(_text(kw["type"]))
    if action:
        parts.append(action)
    if "nargs" in kw:
        parts.append(f"nargs={_text(kw['nargs'])}")
    return "、".join(parts)


def job_args(path: Path) -> list[Arg]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
            continue
        names = tuple(a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str))
        if not names:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        out.append((node.lineno, Arg(names, _kind(kw), _text(kw.get("default")), _text(kw.get("help")))))
    return [a for _, a in sorted(out, key=lambda x: x[0])]

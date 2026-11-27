"""`app/` と `jobs/` の関数単位の参照グラフ。入口（ジョブの `__main__`、API のルート関数）から届く関数を求める。

辺は「関数の本体（中の入れ子の関数・lambda を含む）が名前で参照する、別の関数・クラス」。呼び出しだけでなく
コールバックとして渡す参照も辺にする。クラスを参照したらそのメソッド全部へ、`self.x` / `cls.x` は同じクラスのメソッドへ。
型の分からないオブジェクトのメソッド（`obj.f()`）はたどらない。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

VENDOR = ("app/xbrlp/", "app/edinet_wrapper/")

Key = tuple[str, str]  # (モジュール名, 関数の修飾名。メソッドは "Class.method")


def module_name(rel: str) -> str:
    mod = rel.removesuffix(".py").replace("/", ".")
    return mod.removesuffix(".__init__")


def is_project_file(rel: str) -> bool:
    return rel.endswith(".py") and rel.startswith(("app/", "jobs/")) and not rel.startswith(VENDOR)


@dataclass
class _Module:
    name: str
    is_pkg: bool
    imports: dict[str, str] = field(default_factory=dict)  # ローカル名 → 点区切りの参照先
    funcs: set[str] = field(default_factory=set)
    classes: dict[str, set[str]] = field(default_factory=dict)  # クラス → メソッド
    refs: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)  # 関数 → [(点区切りの名前, 自分のクラス)]
    main_refs: list[tuple[str, str | None]] = field(default_factory=list)
    api_urls: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # 関数 → {(メソッド, パス)}


def _dotted(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _names_in(nodes, cls: str | None) -> list[tuple[str, str | None]]:
    out = []
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
                d = _dotted(n)
                if d:
                    out.append((d, cls))
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                out.append((n.id, cls))
    return out


_PLACEHOLDER = "\x00"
_HTTP_VERBS = ("post", "put", "delete", "patch")


def _url_text(node: ast.AST) -> str | None:
    """`/api/` を含む文字列・f-string を、差し込み部分を _PLACEHOLDER にした文字列で返す。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(v.value if isinstance(v, ast.Constant) else _PLACEHOLDER for v in node.values)
    else:
        return None
    i = text.find("/api/")
    if i < 0:
        return None
    return text[i:].split("?")[0]


def _api_urls_in(nodes) -> set[tuple[str, str]]:
    """プロセスの外の API を HTTP で呼ぶ URL。呼び出しの名前に post / put / delete / patch があればそのメソッド、無ければ GET。"""
    out: set[tuple[str, str]] = set()
    in_call: set[int] = set()
    for top in nodes:
        for n in ast.walk(top):
            if isinstance(n, ast.JoinedStr):
                in_call.update(id(v) for v in n.values)
            if not isinstance(n, ast.Call):
                continue
            fname = (_dotted(n.func) or "").rsplit(".", 1)[-1].lower()
            method = next((v.upper() for v in _HTTP_VERBS if v in fname), "GET")
            for a in n.args:
                url = _url_text(a)
                if url:
                    out.add((method, url))
                    in_call.add(id(a))
        for n in ast.walk(top):
            if id(n) not in in_call and isinstance(n, (ast.Constant, ast.JoinedStr)):
                url = _url_text(n)
                if url:
                    out.add(("GET", url))
    return out


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in t.comparators)
    )


def _parse(root: Path, rel: str) -> _Module:
    name = module_name(rel)
    mod = _Module(name=name, is_pkg=rel.endswith("__init__.py"))
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    pkg = name if mod.is_pkg else name.rpartition(".")[0]
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    mod.imports[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    mod.imports[top] = top
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg.split(".")
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                src = ".".join(base + ([n.module] if n.module else []))
            else:
                src = n.module or ""
            for a in n.names:
                if a.name != "*":
                    mod.imports[a.asname or a.name] = f"{src}.{a.name}"
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.funcs.add(node.name)
            mod.refs[node.name] = _names_in(node.body + node.args.defaults, None)
            mod.api_urls[node.name] = _api_urls_in(node.body)
        elif isinstance(node, ast.ClassDef):
            methods = set()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
                    mod.refs[f"{node.name}.{item.name}"] = _names_in(item.body, node.name)
                    mod.api_urls[f"{node.name}.{item.name}"] = _api_urls_in(item.body)
            mod.classes[node.name] = methods
        elif _is_main_guard(node):
            mod.main_refs = _names_in(node.body, None)
    return mod


class CallGraph:
    def __init__(self, root: Path):
        self.mods: dict[str, _Module] = {}
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if is_project_file(rel):
                m = _parse(root, rel)
                self.mods[m.name] = m
        self._edges: dict[Key, set[Key]] = {}

    def _resolve(self, dotted: str, mod: _Module, cls: str | None, depth: int = 0) -> set[Key]:
        """点区切りの名前を、それが指す関数（クラスならそのメソッド全部）にする。分からなければ空。"""
        if depth > 8:
            return set()
        head, _, rest = dotted.partition(".")
        if head in ("self", "cls") and cls:
            meth = rest.split(".")[0]
            return {(mod.name, f"{cls}.{meth}")} if meth in mod.classes.get(cls, ()) else set()
        if head in mod.funcs and not rest:
            return {(mod.name, head)}
        if head in mod.classes:
            meth = rest.split(".")[0]
            if meth in mod.classes[head]:
                return {(mod.name, f"{head}.{meth}")}
            return {(mod.name, f"{head}.{m}") for m in mod.classes[head]}
        if head in mod.imports:
            target = mod.imports[head] + (f".{rest}" if rest else "")
            return self._resolve_global(target, depth + 1)
        return set()

    def _resolve_global(self, dotted: str, depth: int) -> set[Key]:
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            name = ".".join(parts[:i])
            if name in self.mods:
                rest = ".".join(parts[i:])
                if not rest:
                    return set()
                return self._resolve(rest, self.mods[name], None, depth)
        return set()

    def edges(self, key: Key) -> set[Key]:
        if key not in self._edges:
            mod = self.mods.get(key[0])
            out: set[Key] = set()
            if mod is not None:
                for dotted, cls in mod.refs.get(key[1], ()):
                    out |= self._resolve(dotted, mod, cls)
            out.discard(key)
            self._edges[key] = out
        return self._edges[key]

    def main_roots(self, module: str) -> set[Key]:
        mod = self.mods[module]
        out: set[Key] = set()
        for dotted, cls in mod.main_refs:
            out |= self._resolve(dotted, mod, cls)
        return out

    def reachable(self, roots: set[Key], skip: frozenset[Key] = frozenset()) -> set[Key]:
        seen: set[Key] = set()
        stack = [r for r in roots if r not in skip]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            stack.extend(e for e in self.edges(k) if e not in seen and e not in skip)
        return seen

    def api_urls(self, keys: set[Key]) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for mod, fn in keys:
            out |= self.mods[mod].api_urls.get(fn, set()) if mod in self.mods else set()
        return out


def match_route(url: str, template: str) -> bool:
    """`/api/v1/tables/\x00/ingest` と `/api/v1/tables/{code}/ingest` のように、差し込みと `{...}` はどの区切りとも合う。"""
    a, b = url.rstrip("/").split("/"), template.rstrip("/").split("/")
    if len(a) != len(b):
        return False
    return all(x == y or (y.startswith("{") and y.endswith("}")) for x, y in zip(a, b))

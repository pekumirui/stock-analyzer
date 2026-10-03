"""月次表の親子関係（発行体ごと）。正本は config/monthly_row_order.json の `tree`。

DB と行名は変えない。year GET が各行に `tree_path` を付け、表に `tree` を足すだけ。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from app.config import MONTHLY_ROW_ORDER_PATH


class TreeNode:
    __slots__ = ("children", "heading", "name", "prefix", "rows")

    def __init__(self, name: str, prefix: str, rows: tuple[str, ...], heading: bool,
                 children: tuple[TreeNode, ...]) -> None:
        self.name = name
        self.prefix = prefix
        self.rows = rows
        self.heading = heading
        self.children = children


def _parse_node(raw: Any, parent_prefix: str) -> TreeNode | None:
    # 文字列の子は「親の prefix + 空白 + 名前」を prefix にする（大丸 → 心斎橋店 = `大丸 心斎橋店`）
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        prefix = f"{parent_prefix} {name}" if parent_prefix else name
        return TreeNode(name, prefix, (), False, ())
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    prefix = str(raw.get("prefix") or "").strip()
    rows_raw = raw.get("rows")
    rows = tuple(str(r).strip() for r in rows_raw if str(r).strip()) if isinstance(rows_raw, list) else ()
    heading = raw.get("heading") is True
    kids_raw = raw.get("children")
    kids: list[TreeNode] = []
    if isinstance(kids_raw, list):
        for k in kids_raw:
            node = _parse_node(k, prefix)
            if node is not None:
                kids.append(node)
    return TreeNode(name, prefix, rows, heading, tuple(kids))


def load_monthly_trees(path: Path | None = None) -> dict[str, tuple[TreeNode, ...]]:
    """issuer code → 根の並び。tree の無い銘柄は含めない。"""
    raw = json.loads((path or MONTHLY_ROW_ORDER_PATH).read_text(encoding="utf-8"))
    out: dict[str, tuple[TreeNode, ...]] = {}
    if not isinstance(raw, dict):
        return out
    for code, cfg in raw.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("tree"), list):
            continue
        roots = [n for n in (_parse_node(r, "") for r in cfg["tree"]) if n is not None]
        if roots:
            out[str(code)] = tuple(roots)
    return out


MONTHLY_TREES = load_monthly_trees()


def _walk(nodes: Sequence[TreeNode], path: tuple[str, ...]):
    for n in nodes:
        p = (*path, n.name)
        yield n, p
        yield from _walk(n.children, p)


def assign_tree_paths(code: str, elements: Sequence[str],
                      trees: dict[str, tuple[TreeNode, ...]] | None = None,
                      ) -> dict[str, tuple[str, ...]]:
    """element → 節の名前の列（根から自分の節まで）。どの節にも当たらない行は含めない。

    `rows` の完全一致が先。無ければ `prefix` が空白区切りで前方一致する節のうち最長。見出し節は行を持たない。
    """
    roots = (trees if trees is not None else MONTHLY_TREES).get(str(code))
    if not roots:
        return {}
    exact: dict[str, tuple[str, ...]] = {}
    by_prefix: list[tuple[str, tuple[str, ...]]] = []
    for node, path in _walk(roots, ()):
        if node.heading:
            continue
        for r in node.rows:
            exact.setdefault(r, path)
        if node.prefix:
            by_prefix.append((node.prefix, path))
    by_prefix.sort(key=lambda t: -len(t[0]))
    out: dict[str, tuple[str, ...]] = {}
    for el in elements:
        if el in exact:
            out[el] = exact[el]
            continue
        for prefix, path in by_prefix:
            if el.startswith(prefix + " "):
                out[el] = path
                break
    return out


def tree_leaf(code: str, element: str, path: Sequence[str],
              trees: dict[str, tuple[TreeNode, ...]] | None = None) -> str:
    """節の中での行名（節の prefix を剥がした残り）。剥がせなければ element のまま。"""
    roots = (trees if trees is not None else MONTHLY_TREES).get(str(code)) or ()
    node = None
    for n, p in _walk(roots, ()):
        if p == tuple(path):
            node = n
            break
    if node is not None and node.prefix and element.startswith(node.prefix + " "):
        return element[len(node.prefix) + 1:]
    return element


def tree_payload(code: str, elements: Sequence[str],
                 trees: dict[str, tuple[TreeNode, ...]] | None = None) -> list[dict] | None:
    """year GET の `tree`。節の形は config のまま（年で変えない）、`elements` はその年の行だけ。

    各節 `{name, heading, elements, children}`。`elements` は `rows` の順、残りは引数の順（登録順）。tree の無い銘柄は None。
    """
    roots = (trees if trees is not None else MONTHLY_TREES).get(str(code))
    if not roots:
        return None
    paths = assign_tree_paths(code, elements, trees)
    by_path: dict[tuple[str, ...], list[str]] = {}
    for el in elements:
        p = paths.get(el)
        if p is not None:
            by_path.setdefault(p, []).append(el)

    def dump(nodes: Sequence[TreeNode], path: tuple[str, ...]) -> list[dict]:
        out = []
        for n in nodes:
            p = (*path, n.name)
            els = by_path.get(p, [])
            if n.rows:
                # rows に書いた順が先（先頭がその節の代表行）
                rank = {r: i for i, r in enumerate(n.rows)}
                els = sorted(els, key=lambda e: rank.get(e, len(rank)))
            out.append({
                "name": n.name,
                "heading": n.heading,
                "elements": els,
                "children": dump(n.children, p),
            })
        return out

    return dump(roots, ())

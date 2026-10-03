"""月次表の親子関係（発行体ごと）。正本は config/monthly_row_order.json の `tree` と `metrics`。

木の節は事業・会社・店・品目。指標（総額売上高 / 売上収益(IFRS) / 入店客数 …）は節の子にしない。
同じ節を別の物差しで測った行は、葉の名前で `metrics` のどれかに振り分ける。
DB と行名は変えない。year GET が各行に `tree` を付け、表に `tree` / `tree_metrics` を足すだけ。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from app.config import MONTHLY_ROW_ORDER_PATH


class TreeNode:
    __slots__ = ("children", "heading", "name", "part", "prefix", "rows")

    def __init__(self, name: str, prefix: str, rows: tuple[str, ...], heading: bool, part: bool,
                 children: tuple[TreeNode, ...]) -> None:
        self.name = name
        self.prefix = prefix
        self.rows = rows
        self.heading = heading
        self.part = part
        self.children = children


class IssuerTree:
    __slots__ = ("metrics", "roots")

    def __init__(self, roots: tuple[TreeNode, ...], metrics: tuple[tuple[str, frozenset[str]], ...]) -> None:
        self.roots = roots
        # (指標名, その指標に当たる葉の集合)。並びがタブの並び（先頭が既定）
        self.metrics = metrics


def _parse_node(raw: Any, parent_prefix: str) -> TreeNode | None:
    # 文字列の子は「親の prefix + 空白 + 名前」を prefix にする（大丸 → 心斎橋店 = `大丸 心斎橋店`）
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        prefix = f"{parent_prefix} {name}" if parent_prefix else name
        return TreeNode(name, prefix, (), False, False, ())
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    prefix = str(raw.get("prefix") or "").strip()
    rows_raw = raw.get("rows")
    rows = tuple(str(r).strip() for r in rows_raw if str(r).strip()) if isinstance(rows_raw, list) else ()
    kids_raw = raw.get("children")
    kids: list[TreeNode] = []
    if isinstance(kids_raw, list):
        for k in kids_raw:
            node = _parse_node(k, prefix)
            if node is not None:
                kids.append(node)
    return TreeNode(name, prefix, rows, raw.get("heading") is True, raw.get("part") is True, tuple(kids))


def _parse_metrics(raw: Any) -> tuple[tuple[str, frozenset[str]], ...]:
    out: list[tuple[str, frozenset[str]]] = []
    if not isinstance(raw, list):
        return ()
    for m in raw:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        leaves = m.get("leaves")
        if name and isinstance(leaves, list):
            out.append((name, frozenset(str(x).strip() for x in leaves if str(x).strip())))
    return tuple(out)


def parse_issuer_tree(cfg: Any) -> IssuerTree | None:
    if not isinstance(cfg, dict) or not isinstance(cfg.get("tree"), list):
        return None
    roots = tuple(n for n in (_parse_node(r, "") for r in cfg["tree"]) if n is not None)
    if not roots:
        return None
    return IssuerTree(roots, _parse_metrics(cfg.get("metrics")))


def load_monthly_trees(path: Path | None = None) -> dict[str, IssuerTree]:
    """issuer code → 木。tree の無い銘柄は含めない。"""
    raw = json.loads((path or MONTHLY_ROW_ORDER_PATH).read_text(encoding="utf-8"))
    out: dict[str, IssuerTree] = {}
    if not isinstance(raw, dict):
        return out
    for code, cfg in raw.items():
        tree = parse_issuer_tree(cfg)
        if tree is not None:
            out[str(code)] = tree
    return out


MONTHLY_TREES = load_monthly_trees()


def _walk(nodes: Sequence[TreeNode], path: tuple[str, ...]):
    for n in nodes:
        p = (*path, n.name)
        yield n, p
        yield from _walk(n.children, p)


def _tree(code: str, trees: dict[str, IssuerTree] | None) -> IssuerTree | None:
    return (trees if trees is not None else MONTHLY_TREES).get(str(code))


def assign_tree_rows(code: str, elements: Sequence[str],
                     trees: dict[str, IssuerTree] | None = None,
                     ) -> dict[str, dict[str, Any]]:
    """element → `{path, leaf, metric}`。どの節にも当たらない行は含めない。

    節は `rows` の完全一致が先。無ければ `prefix` が空白区切りで前方一致する節のうち最長。見出し節は行を持たない。
    leaf は節の prefix を剥がした残り（剥がせなければ element のまま）。metric は leaf が載っている `metrics` の名前、無ければ None。
    """
    tree = _tree(code, trees)
    if tree is None:
        return {}
    exact: dict[str, tuple[TreeNode, tuple[str, ...]]] = {}
    by_prefix: list[tuple[str, TreeNode, tuple[str, ...]]] = []
    for node, path in _walk(tree.roots, ()):
        if node.heading:
            continue
        for r in node.rows:
            exact.setdefault(r, (node, path))
        if node.prefix:
            by_prefix.append((node.prefix, node, path))
    by_prefix.sort(key=lambda t: -len(t[0]))
    out: dict[str, dict[str, Any]] = {}
    for el in elements:
        hit = exact.get(el)
        if hit is None:
            hit = next(((n, p) for prefix, n, p in by_prefix if el.startswith(prefix + " ")), None)
        if hit is None:
            continue
        node, path = hit
        leaf = el[len(node.prefix) + 1:] if node.prefix and el.startswith(node.prefix + " ") else el
        metric = next((name for name, leaves in tree.metrics if leaf in leaves), None)
        out[el] = {"path": list(path), "leaf": leaf, "metric": metric}
    return out


def tree_payload(code: str, elements: Sequence[str],
                 trees: dict[str, IssuerTree] | None = None) -> dict[str, Any] | None:
    """year GET に足す `{tree, tree_metrics}`。節の形は config のまま（年で変えない）、`elements` はその年の行だけ。

    各節 `{name, heading, part, elements, children}`。`elements` は `rows` の順、残りは引数の順（登録順）。
    `tree_metrics` は指標名の並び（先頭が既定）。tree の無い銘柄は None。
    """
    tree = _tree(code, trees)
    if tree is None:
        return None
    rows = assign_tree_rows(code, elements, trees)
    by_path: dict[tuple[str, ...], list[str]] = {}
    for el in elements:
        r = rows.get(el)
        if r is not None:
            by_path.setdefault(tuple(r["path"]), []).append(el)

    def dump(nodes: Sequence[TreeNode], path: tuple[str, ...]) -> list[dict]:
        out = []
        for n in nodes:
            p = (*path, n.name)
            els = by_path.get(p, [])
            if n.rows:
                rank = {r: i for i, r in enumerate(n.rows)}
                els = sorted(els, key=lambda e: rank.get(e, len(rank)))
            out.append({
                "name": n.name,
                "heading": n.heading,
                "part": n.part,
                "elements": els,
                "children": dump(n.children, p),
            })
        return out

    return {"tree": dump(tree.roots, ()), "tree_metrics": [name for name, _ in tree.metrics]}

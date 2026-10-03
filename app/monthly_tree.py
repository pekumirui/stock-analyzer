"""月次表の親子関係（発行体ごと）。正本は config/monthly_row_order.json の `tree` と `metrics`。

木の節は事業・会社・店・品目。指標（総額売上高 / 売上収益(IFRS) / 入店客数 …）は節の子にしない。
同じ節を別の物差しで測った行は、葉の名前で `metrics` のどれかに振り分ける。
DB と行名は変えない。year GET が各行に `tree` を付け、表に `tree` / `tree_metrics` を足すだけ。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.config import MONTHLY_ROW_ORDER_PATH
from app.monthly_ingest import (
    canonicalize_element_label, issuer_segment_tail, monthly_element_label,
    monthly_row_breakdown_rank, monthly_row_channel_rank, monthly_row_scope_rank, monthly_row_slot,
    monthly_row_unit_rank, strip_yoy_fragments, union_monthly_elements,
)


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


# ---- 自動の木（config に tree の無い発行体） ----
# 根は会社全体。ゲートした group を根の子の節、売上高の内訳をその行の節の子の節にする。
# ゲートは表示する 3 年（最新とその前 2 年）で年ごとに数え、`label.group` があり metric / channel / scope のどれかを持つ行が
# 同じ group で 2 本以上ある年があればその group を節にする（年をまたいで 1+1 にはしない）。
# 指標は葉（group と内訳を剥がした表示名）から (前年比) を外したもの。節 2 つ以上に出る指標だけ tree_metrics に載せる。
# tree_metrics は並び順、ただし先頭（既定のタブ）だけはいちばん多くの節に出る指標。

AUTO_ROOT = "全体"


def monthly_display_years(years: Sequence[int]) -> list[int]:
    """最新とその前 2 年（データのある年）。SPA の monthlyDisplayYears と同じ。"""
    ys = sorted({int(y) for y in years}, reverse=True)
    return [y for y in ys if y >= ys[0] - 2] if ys else []


def auto_gate_groups(years: Mapping[int, Sequence[tuple[str, str]]]) -> set[str]:
    gated: set[str] = set()
    for rows in years.values():
        counts: dict[str, int] = {}
        for element, unit in rows:
            L = monthly_element_label(element, unit)
            if L["group"] and (L["metric"] or L["channel"] or L["scope"]):
                counts[L["group"]] = counts.get(L["group"], 0) + 1
        gated.update(g for g, n in counts.items() if n >= 2)
    return gated


def _auto_leaf(label: Mapping[str, Any], group: str) -> str:
    s = str(label["display"])
    if group and s.startswith(group + " "):
        s = s[len(group) + 1:]
    bd = str(label["breakdown"] or "")
    if bd:
        i = s.rfind(" " + bd)
        if i >= 0:
            s = s[:i] + s[i + 1 + len(bd):]
    return s or str(label["display"])


class AutoTree:
    __slots__ = ("metrics", "nodes", "rank", "rows")

    def __init__(self, nodes: list[tuple[str, list]], rows: dict[str, dict[str, Any]],
                 rank: dict[str, tuple], metrics: list[str]) -> None:
        # nodes は (名前, 子) の入れ子。rows は保存名 → {path, leaf, metric, order}。rank は節の中の並び。
        self.nodes = nodes
        self.rows = rows
        self.rank = rank
        self.metrics = metrics


def build_auto_tree(code: str, years: Mapping[int, Sequence[tuple[str, str]]],
                    gate_years: Iterable[int] | None = None) -> AutoTree:
    """years は 暦年 → [(保存名, unit)]（値のある行だけ、sort_order 順）。gate_years はゲートを数える年（既定は表示する 3 年）。"""
    gy = set(gate_years) if gate_years is not None else set(monthly_display_years(list(years)))
    gated = auto_gate_groups({y: r for y, r in years.items() if y in gy})
    newest = sorted(years, reverse=True)
    union = union_monthly_elements(
        [{"year": y, "elements": [{"element": e, "unit": u} for e, u in years[y]]} for y in newest if y in gy] or
        [{"year": y, "elements": [{"element": e, "unit": u} for e, u in years[y]]} for y in newest],
        code=code,
    )
    union_idx = {it["element"]: i for i, it in enumerate(union)}
    tail = {name: i for i, name in enumerate(issuer_segment_tail(code))}
    group_first: dict[str, int] = {}
    bd_first: dict[tuple[str, str], tuple[int, int]] = {}
    rows: dict[str, dict[str, Any]] = {}
    rank: dict[str, tuple] = {}
    node_metrics: dict[tuple[str, ...], set[str]] = {}
    sales_metrics: set[str] = set()
    seen = 0
    for y in newest:
        for element, unit in years[y]:
            if element in rows:
                continue
            L = monthly_element_label(element, unit)
            group = L["group"] if L["group"] in gated else ""
            bd = str(L["breakdown"] or "")
            if group:
                group_first.setdefault(group, seen)
            canon = canonicalize_element_label(element, unit)
            if bd:
                bd_first.setdefault((group, bd), (monthly_row_breakdown_rank(canon), seen))
            seen += 1
            path = (AUTO_ROOT, *((group,) if group else ()), *((bd,) if bd else ()))
            leaf = _auto_leaf(L, group)
            metric = strip_yoy_fragments(leaf) if monthly_row_unit_rank(unit) else leaf
            if L["metric_kind"] == "sales":
                sales_metrics.add(metric)
            rows[element] = {"path": list(path), "leaf": leaf, "metric": metric}
            node_metrics.setdefault(path, set()).add(metric)
            idx = union_idx.get(canon, len(union_idx) + seen)
            # ゲートした節と、ゲートがあるときの会社全体は チャネル → スコープ → 指標 → 実数/前年比 → 内訳。国では並べない。
            rank[element] = (
                (monthly_row_channel_rank(canon), monthly_row_scope_rank(canon), monthly_row_slot(canon, unit),
                 monthly_row_unit_rank(unit), monthly_row_breakdown_rank(canon), idx)
                if gated else (idx,)
            )
    counts: dict[str, int] = {}
    for ms in node_metrics.values():
        for m in ms:
            counts[m] = counts.get(m, 0) + 1
    shared = {m for m, n in counts.items() if n >= 2}
    for r in rows.values():
        if r["metric"] not in shared:
            r["metric"] = None
    metric_order: list[str] = []
    for i, element in enumerate(sorted(rows, key=lambda e: rank[e])):
        # order は表示する 3 年を通した並び。年ごとの elements だけでは片方の年にしか無い行どうしの前後が決まらない。
        rows[element]["order"] = i
        m = rows[element]["metric"]
        if m and m not in metric_order:
            metric_order.append(m)
    # 既定（先頭）のタブは、いちばん多くの節に出る指標（同数なら売上高、次に並び順）。全店が会社にしか無い銘柄でも各節が数字で並ぶ
    if metric_order:
        best = min(metric_order, key=lambda m: (-counts[m], m not in sales_metrics, metric_order.index(m)))
        metric_order.remove(best)
        metric_order.insert(0, best)

    def bd_nodes(group: str) -> list[tuple[str, list]]:
        names = sorted((bd for (g, bd) in bd_first if g == group), key=lambda bd: bd_first[(group, bd)])
        return [(bd, []) for bd in names]

    groups = sorted(group_first, key=lambda g: (g in tail, tail.get(g, 0), group_first[g]))
    root_kids = bd_nodes("") + [(g, bd_nodes(g)) for g in groups]
    return AutoTree([(AUTO_ROOT, root_kids)], rows, rank, metric_order)


def auto_tree_payload(auto: AutoTree, elements: Sequence[str]) -> dict[str, Any]:
    """year GET に足す `{tree, tree_metrics, tree_auto}`。節の形は表示する 3 年で同じ、`elements` はその年の行だけ。"""
    by_path: dict[tuple[str, ...], list[str]] = {}
    for el in elements:
        r = auto.rows.get(el)
        if r is not None:
            by_path.setdefault(tuple(r["path"]), []).append(el)

    def dump(nodes: Sequence[tuple[str, list]], path: tuple[str, ...]) -> list[dict]:
        out = []
        for name, kids in nodes:
            p = (*path, name)
            out.append({
                "name": name,
                "heading": False,
                "part": False,
                "elements": sorted(by_path.get(p, []), key=lambda e: auto.rank[e]),
                "children": dump(kids, p),
            })
        return out

    return {"tree": dump(auto.nodes, ()), "tree_metrics": list(auto.metrics), "tree_auto": True}

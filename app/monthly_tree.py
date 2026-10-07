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
    monthly_row_breakdown_rank, monthly_row_slot, monthly_row_unit_rank, strip_yoy_fragments, union_monthly_elements,
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


def load_group_metrics(path: Path | None = None) -> dict[str, dict[str, str]]:
    """issuer code → `group_metric`（自動の木で節の行の指標を寄せる。指標 → 寄せ先の指標）。"""
    raw = json.loads((path or MONTHLY_ROW_ORDER_PATH).read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for code, cfg in raw.items():
        gm = cfg.get("group_metric") if isinstance(cfg, dict) else None
        if isinstance(gm, dict):
            out[str(code)] = {str(k).strip(): str(v).strip() for k, v in gm.items() if str(k).strip() and str(v).strip()}
    return out


MONTHLY_GROUP_METRICS = load_group_metrics()


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
# 根は会社全体。行名の group（ブランド・事業）を根の子の節、売上高の内訳をその行の節の子の節にする。
# 横持ちは指標が一番上の行で、その下に節の木を出す（既存 売上高 ▸ 焼肉 / ラーメン …）。
# 指標は葉（group と内訳を剥がした表示名）から (前年比) を外したもの。会社の行も節の行も同じ葉なら同じ指標。
# 指標どうしもチャネルと国で親子にする（合計店舗数 ▸ 国内 ▸ 直営 / FC、海外）。
# `日本+海外` と `グループ` は行名の解析では group になるが、会社全体の数字なので根に置き、葉からは剥がす（物語 合計店舗数 932）。

AUTO_ROOT = "全体"
# 会社全体を指す group。マツキヨココカラの `グループ 店舗数` はグループ全体の店舗数
_COMPANY_GROUPS = frozenset({"日本+海外", "グループ"})
# group の中の「その節の合計」を表す語。節にも葉にも残さない（イオン北海道 `衣料 企業計 全店 売上高` は 衣料 の全店、
# `企業計 …` だけなら会社全体。バロー グループ総計、ブックオフ チェーン合計、アートネイチャー 総売上高、ラクス 全社（連結）、
# TABAYA 既存店カウント店舗数）
_TOTAL_TOKENS = frozenset({"企業計", "グループ総", "チェーン", "総", "全社（連結）", "カウント"})
# group の中の指標の語。節にせず葉に残す（LOIVE `ピラティスK 増減 店舗数` は ピラティスK の 増減 店舗数、
# スズデン 増減額、MV東海 / 大黒天 の 同規模・一品（単価）・買上点数）
_METRIC_TOKENS = frozenset({"増減", "増減額", "同規模", "一品", "買上点数"})
# 指標の並びは見る順: チャネル（無印・直営+FC → 直営 → FC）→ 国（無印 → 日本 → 海外）→ 既存 → 全店 → 売上高 → 客数 → 客単価 → 店舗 …
_AUTO_CHANNEL_RANK = {"": 0, "直営+FC": 0, "直営": 1, "FC": 2}
_AUTO_COUNTRY_RANK = {"": 0, "日本+海外": 0, "日本": 1, "海外": 2}
_AUTO_SCOPE_RANK = {"既存": 0, "全店": 1, "全社": 1, "": 1}
# 指標どうしの親子（チャネルと国）。直営 / FC は 直営+FC（無ければ無印）の子、日本 / 海外 は国なしの子
_AUTO_COUNTRIES = frozenset({"日本", "海外", "日本+海外"})
_AUTO_PART_CHANNELS = frozenset({"直営", "FC"})


def monthly_display_years(years: Sequence[int]) -> list[int]:
    """最新とその前 2 年（データのある年）。SPA の monthlyDisplayYears と同じ。"""
    ys = sorted({int(y) for y in years}, reverse=True)
    return [y for y in ys if y >= ys[0] - 2] if ys else []


def _auto_split(label: Mapping[str, Any]) -> tuple[str, str]:
    """(節にする group, 葉)。行名全体が group（指標の無い行）と会社全体の合計は節にしない。
    group の中の合計の語は節からも葉からも外し、指標の語は節から外して葉の頭に残す。内訳はこのあと葉から外す。
    """
    s = str(label["display"])
    g = str(label["group"] or "")
    if not g or not s.startswith(g + " "):
        # 指標の無い行（`グループ 稼働率`）も会社全体の語は葉から剥がす
        head = next((c for c in _COMPANY_GROUPS if s.startswith(c + " ")), "")
        return "", s[len(head) + 1:] if head else s
    rest = s[len(g) + 1:]
    if g in _COMPANY_GROUPS:
        return "", rest
    toks = g.split(" ")
    group = " ".join(t for t in toks if t not in _TOTAL_TOKENS and t not in _METRIC_TOKENS)
    leaf = " ".join([*(t for t in toks if t in _METRIC_TOKENS), rest])
    return group, leaf


def _auto_strip_breakdown(label: Mapping[str, Any], leaf: str) -> str:
    s = leaf
    bd = str(label["breakdown"] or "")
    if bd:
        i = s.rfind(" " + bd)
        if i >= 0:
            s = s[:i] + s[i + 1 + len(bd):]
    return s or str(label["display"])


class AutoTree:
    __slots__ = ("metric_parent", "metrics", "nodes", "rows")

    def __init__(self, nodes: list[tuple[str, list]], rows: dict[str, dict[str, Any]], metrics: list[str],
                 metric_parent: dict[str, str] | None = None) -> None:
        # nodes は (名前, 子) の入れ子。rows は保存名 → {path, leaf, metric, order}。metrics は見る順の指標
        # metric_parent は 指標 → 親の指標（直営 全店 店舗数 → 直営+FC 全店 店舗数）
        self.nodes = nodes
        self.rows = rows
        self.metrics = metrics
        self.metric_parent = metric_parent or {}


def _metric_parts(metric: str) -> tuple[str, str, tuple[str, ...]]:
    """指標を (国, チャネル, 残り) に分ける。行名は 国 → チャネル → スコープ → 指標 の順"""
    toks = metric.split(" ")
    country = toks[0] if toks and toks[0] in _AUTO_COUNTRIES else ""
    rest = toks[1:] if country else toks
    channel = rest[0] if rest and rest[0] in (_AUTO_PART_CHANNELS | {"直営+FC"}) else ""
    return country, channel, tuple(rest[1:] if channel else rest)


def auto_metric_parents(metrics: Sequence[str], domestic: Iterable[str] = ()) -> dict[str, str]:
    """直営 / FC の指標は 直営+FC（無ければ無印）の子、日本 / 海外 の指標は国なしの子。
    国付きの 直営 / FC は、同じ国の合計が無ければ国なしの同じチャネルの子。
    domestic は会社の `日本 ` 付きの行を代表にした指標（物語 直営 全店 店舗数 = 国内直営 553）。国内の 直営+FC を先に探す。
    """
    have = set(metrics)
    dom = set(domestic)
    join = lambda *parts: " ".join(p for p in parts if p)  # noqa: E731
    out: dict[str, str] = {}
    for m in metrics:
        country, channel, rest = _metric_parts(m)
        r = " ".join(rest)
        if channel in _AUTO_PART_CHANNELS:
            cands = [join("日本", "直営+FC", r)] if m in dom else []
            # 国の合計が無ければ国なしの同じチャネル（ハードオフ 海外 直営 店舗数 14 は 直営 店舗数 568 の内訳）
            cands += [join(country, "直営+FC", r), join(country, r), join(channel, r)]
        elif country in ("日本", "海外"):
            cands = [join(channel, r), join("直営+FC", r), r]
        else:
            continue
        parent = next((c for c in cands if c != m and c in have), None)
        if parent:
            out[m] = parent
    return out


def build_auto_tree(code: str, years: Mapping[int, Sequence[tuple[str, str]]],
                    group_metric: Mapping[str, str] | None = None) -> AutoTree:
    """years は 暦年 → [(保存名, unit)]（値のある行だけ、sort_order 順）。節の形と並びは渡した年すべてで決める。

    group_metric は節の行の指標を寄せる（既定は config の `group_metric`）。物語のブランド別の合計店舗数は国内の数字なので
    `直営+FC 全店 店舗数`（合計 932）ではなく `日本 直営+FC 全店 店舗数`（国内 816）の下に出す。
    """
    gmap = MONTHLY_GROUP_METRICS.get(str(code), {}) if group_metric is None else dict(group_metric)
    newest = sorted(years, reverse=True)
    union = union_monthly_elements(
        [{"year": y, "elements": [{"element": e, "unit": u} for e, u in years[y]]} for y in newest], code=code)
    union_idx = {it["element"]: i for i, it in enumerate(union)}
    tail = {name: i for i, name in enumerate(issuer_segment_tail(code))}
    group_first: dict[str, int] = {}
    bd_first: dict[tuple[str, str], tuple[int, int]] = {}
    rows: dict[str, dict[str, Any]] = {}
    metric_key: dict[str, tuple] = {}
    sales: dict[tuple[str, ...], set[str]] = {}
    seen = 0

    def place(element: str, unit: str, L: Mapping[str, Any], canon: str, group: str, leaf: str,
              metric: str | None = None) -> None:
        nonlocal seen
        bd = str(L["breakdown"] or "")
        if group:
            group_first.setdefault(group, seen)
        if bd:
            bd_first.setdefault((group, bd), (monthly_row_breakdown_rank(canon), seen))
        seen += 1
        path = (AUTO_ROOT, *((group,) if group else ()), *((bd,) if bd else ()))
        leaf = _auto_strip_breakdown(L, leaf)
        if metric is None:
            metric = strip_yoy_fragments(leaf) if monthly_row_unit_rank(unit) else leaf
            if len(path) > 1:
                metric = gmap.get(metric, metric)
        idx = union_idx.get(canon, len(union_idx) + seen)
        rows[element] = {"path": list(path), "leaf": leaf, "metric": metric, "order": idx}
        if L["metric_kind"] == "sales" and not monthly_row_unit_rank(unit):
            sales.setdefault(path, set()).add(metric)
        # 指標の並びは会社の行を代表にする（無ければ最初に見た節の行）
        key = (
            _AUTO_CHANNEL_RANK.get(str(L["channel"]), 3),
            _AUTO_COUNTRY_RANK.get(str(L["country"]), 3),
            _AUTO_SCOPE_RANK.get(str(L["scope"]), 2),
            monthly_row_slot(canon, unit),
            idx,
        )
        if metric not in metric_key or (len(path) == 1 and metric_key[metric][0]):
            metric_key[metric] = (len(path) > 1, key)

    # 指標の語の無い前年比（アスモ `アスモトレーディング(前年比)`、`合計(前年比)`）は、その節の金額の売上高の行が
    # 1 つに決まればその指標の前年比にする（決まらなければ行名のまま根）。節の金額の行を先に見てから置く。
    bare: list[tuple[str, str, Mapping[str, Any], str, str, str, str]] = []
    deferred: set[str] = set()
    for y in newest:
        for element, unit in years[y]:
            if element in rows or element in deferred:
                continue
            L = monthly_element_label(element, unit)
            canon = canonicalize_element_label(element, unit)
            group, leaf = _auto_split(L)
            if monthly_row_unit_rank(unit) and not any(L[k] for k in ("metric", "breakdown", "scope", "channel")):
                g = str(L["group"] or "")
                own = g if g and strip_yoy_fragments(str(L["display"])) == g else ""
                bare.append((element, unit, L, canon, own, group, leaf))
                deferred.add(element)
                continue
            place(element, unit, L, canon, group, leaf)
    for element, unit, L, canon, own, group, leaf in bare:
        cands = sales.get((AUTO_ROOT, *((own,) if own else ())), set())
        if len(cands) == 1:
            m = next(iter(cands))
            place(element, unit, L, canon, own, m + str(L["display"])[len(strip_yoy_fragments(str(L["display"]))):], m)
        else:
            place(element, unit, L, canon, group, leaf)
    # 節にしか無い指標は、会社の `日本 ` 付きの行を親にする（物語: 直営 全店 店舗数 の親は 日本 直営 全店 店舗数 553）。
    # 会社に同じ指標の行があるとき（直営+FC 全店 店舗数 は 日本+海外 の 932）は寄せない。
    company = {r["metric"] for r in rows.values() if len(r["path"]) == 1}
    folded: list[str] = []
    for m in {r["metric"] for r in rows.values() if len(r["path"]) > 1} - company:
        domestic = "日本 " + m
        if domestic not in company:
            continue
        for r in rows.values():
            if len(r["path"]) == 1 and r["metric"] == domestic:
                r["metric"] = m
        metric_key[m] = metric_key.pop(domestic)
        folded.append(m)
    # 会社の行だけ `全社 ` が付く発行体（柿安 `全社 売上高` と各事業の `売上高`）も節の指標に寄せる。
    # 寄せないと 全社 売上高（会社だけ）と 売上高（会社の数字が無い）の 2 つの指標に割れる
    for m in {r["metric"] for r in rows.values() if len(r["path"]) > 1} - company:
        whole = "全社 " + m
        if whole not in company:
            continue
        for r in rows.values():
            if len(r["path"]) == 1 and r["metric"] == whole:
                r["metric"] = m
        metric_key[m] = metric_key.pop(whole)
    metrics = sorted(metric_key, key=lambda m: metric_key[m][1])

    def bd_nodes(group: str) -> list[tuple[str, list]]:
        names = sorted((bd for (g, bd) in bd_first if g == group), key=lambda bd: bd_first[(group, bd)])
        return [(bd, []) for bd in names]

    groups = sorted(group_first, key=lambda g: (g in tail, tail.get(g, 0), group_first[g]))
    root_kids = bd_nodes("") + [(g, bd_nodes(g)) for g in groups]
    return AutoTree([(AUTO_ROOT, root_kids)], rows, metrics, auto_metric_parents(metrics, folded))


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
                "elements": sorted(by_path.get(p, []), key=lambda e: auto.rows[e]["order"]),
                "children": dump(kids, p),
            })
        return out

    return {"tree": dump(auto.nodes, ()), "tree_metrics": list(auto.metrics),
            "tree_metric_parent": dict(auto.metric_parent), "tree_auto": True}

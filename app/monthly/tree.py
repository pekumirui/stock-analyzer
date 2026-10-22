"""月次表の親子関係（発行体ごと）。正本は config/monthly/monthly_row_order.json の `tree` と `metrics`。
自動の木の寄せは同じファイルの `group_metric` / `company_metric`。

木の節は事業・会社・店・品目。指標（総額売上高 / 売上収益(IFRS) / 入店客数 …）は節の子にしない。
同じ節を別の物差しで測った行は、葉の名前で `metrics` のどれかに振り分ける。
DB と行名は変えない。year GET が各行に `tree` を付け、表に `tree` / `tree_metrics` を足すだけ。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.config import MONTHLY_ROW_ORDER_PATH
from app.monthly.ingest import (
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


def _issuer_str_map(key: str, path: Path | None = None) -> dict[str, dict[str, str]]:
    raw = json.loads((path or MONTHLY_ROW_ORDER_PATH).read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for code, cfg in raw.items():
        raw_map = cfg.get(key) if isinstance(cfg, dict) else None
        if isinstance(raw_map, dict):
            out[str(code)] = {str(k).strip(): str(v).strip()
                              for k, v in raw_map.items() if str(k).strip() and str(v).strip()}
    return out


def load_group_metrics(path: Path | None = None) -> dict[str, dict[str, str]]:
    """issuer code → `group_metric`（自動の木で節の行の指標を寄せる。指標 → 寄せ先の指標）。"""
    return _issuer_str_map("group_metric", path)


def load_company_metrics(path: Path | None = None) -> dict[str, dict[str, str]]:
    """issuer code → `company_metric`（自動の木で会社の行の指標を寄せる。指標 → 寄せ先の指標）。"""
    return _issuer_str_map("company_metric", path)


MONTHLY_GROUP_METRICS = load_group_metrics()
MONTHLY_COMPANY_METRICS = load_company_metrics()


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


AUTO_ROOT = "全体"
_COMPANY_GROUPS = frozenset({"日本+海外", "グループ"})
_TOTAL_TOKENS = frozenset({"企業計", "グループ総", "チェーン", "総", "全社（連結）", "カウント"})
_METRIC_TOKENS = frozenset({"増減", "増減額", "同規模", "一品", "買上点数"})
_GROUP_CHANNEL_TOKENS = frozenset({"TCC"})
_YOY_RATE_SUFFIX = "増減率"
_INNER_MARKS = ("内訳：", "内訳:", "内訳", "うち")
_GLUED_INNERS = ("現物", "信用")
_FOLD_SKIP_GROUPS = _COMPANY_GROUPS | _TOTAL_TOKENS | frozenset({
    "全社", "全店", "既存", "直営", "FC", "直営+FC", "日本", "海外", "日本+海外",
})
_AUTO_CHANNEL_RANK = {"": 0, "直営+FC": 0, "直営": 1, "FC": 2}
_AUTO_COUNTRY_RANK = {"": 0, "日本+海外": 0, "日本": 1, "海外": 2}
_AUTO_SCOPE_RANK = {"既存": 0, "全店": 1, "全社": 1, "": 1}
_AUTO_COUNTRIES = frozenset({"日本", "海外", "日本+海外"})
_AUTO_PART_CHANNELS = frozenset({"直営", "FC"})


def monthly_display_years(years: Sequence[int]) -> list[int]:
    """最新とその前 2 年（データのある年）。SPA の monthlyDisplayYears と同じ。"""
    ys = sorted({int(y) for y in years}, reverse=True)
    return [y for y in ys if y >= ys[0] - 2] if ys else []


def _auto_split(label: Mapping[str, Any]) -> tuple[str, str]:
    """(節にする group, 葉)。行名全体が group（指標の無い行）と会社全体の合計は節にしない。
    group の中の合計の語は節からも葉からも外し、指標の語とチャネルの語は節から外して葉の頭に残す。内訳はこのあと葉から外す。
    国の語（`日本` / `海外`）が group の前に出る行（31930 `日本 鳥貴族 店舗数`、7134 `海外 アップガレージ 店舗数`）は
    国の語を剥がして group に当て、国の語は葉の頭に残す（#590）。括弧で始まる group（43430 `(曜日調整前)`）は節にしない
    """
    s = str(label["display"])
    g = str(label["group"] or "")
    country = next((c for c in ("日本", "海外") if s.startswith(c + " ")), "")
    if g and country and not g.startswith("(") and s.startswith(f"{country} {g} "):
        s = s[len(country) + 1:]
    else:
        country = ""
    if not g or not s.startswith(g + " "):
        head = next((c for c in _COMPANY_GROUPS if s.startswith(c + " ")), "")
        return "", s[len(head) + 1:] if head else s
    rest = s[len(g) + 1:]
    if g in _COMPANY_GROUPS:
        return "", rest
    toks = g.split(" ")
    group = " ".join(t for t in toks if t not in _TOTAL_TOKENS and t not in _METRIC_TOKENS
                     and t not in _GROUP_CHANNEL_TOKENS)
    leaf = " ".join([*((country,) if country else ()),
                     *(t for t in toks if t in _GROUP_CHANNEL_TOKENS or t in _METRIC_TOKENS), rest])
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
    domestic は国内の 直営+FC を先に探す指標。
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
            cands += [join(country, "直営+FC", r), join(country, r), join(channel, r)]
        elif country in ("日本", "海外"):
            cands = [join(channel, r), join("直営+FC", r), r]
        else:
            continue
        parent = next((c for c in cands if c != m and c in have), None)
        if parent:
            out[m] = parent
    return out


def _auto_inner_name(rest: str) -> str:
    s = rest.strip()
    for mark in _INNER_MARKS:
        if s.startswith(mark):
            s = s[len(mark):].strip()
            break
    return s


def _glued_inner(leaf: str, metrics: Sequence[str]) -> tuple[str, str] | None:
    """`一日平均売買代金現物` → (`一日平均売買代金`, `現物`)。親の指標が既にあるときだけ。"""
    for m in metrics:
        if not m or leaf == m:
            continue
        rest = leaf[len(m):] if leaf.startswith(m) else ""
        if rest in _GLUED_INNERS:
            return m, rest
    return None


def _fold_skip_section(name: str) -> bool:
    """合計・チャネル・範囲・国・指標の語を含む名前は節にしない（`日本 総` / `増減`）。"""
    if not name:
        return True
    return any(t in _FOLD_SKIP_GROUPS or t in _METRIC_TOKENS for t in name.split())


def _auto_is_baked(row: Mapping[str, Any], element: str) -> bool:
    """指標語が無く行名全体が group / 指標になった行。既に節へ割れている行は触らない。
    `既存 売上高` のように辞書の指標語がある会社行は寄せない（#822 の 既存 を指標にしない）。
    """
    if len(row["path"]) != 1 or row.get("has_scope_or_channel") or not row.get("unknown_metric"):
        return False
    return row["metric"] == strip_yoy_fragments(element) or row["metric"] == strip_yoy_fragments(row["leaf"])


def _fold_yoy_rate_metrics(rows: dict[str, dict[str, Any]]) -> None:
    """指標語の無い `増減率` 前年比を実数の指標へ寄せる（#578。21240 `従業員数増減率` → `従業員数`）。"""
    have = {r["metric"] for r in rows.values()}
    for r in rows.values():
        m = r["metric"]
        if m.endswith(_YOY_RATE_SUFFIX) and len(m) > len(_YOY_RATE_SUFFIX):
            base = m[:-len(_YOY_RATE_SUFFIX)]
            if base in have:
                r["metric"] = base


def _fold_consolidated_metrics(rows: dict[str, dict[str, Any]]) -> None:
    """会社の `連結X` を、節に `X` があるとき `X` へ寄せる（#577。71850 `連結営業収益` / `単体 営業収益`）。"""
    have = {r["metric"] for r in rows.values()}
    for r in rows.values():
        if len(r["path"]) != 1:
            continue
        m = r["metric"]
        if m.startswith("連結") and len(m) > 2:
            core = m[2:]
            if core in have:
                r["metric"] = core


def _fold_baked_auto_rows(rows: dict[str, dict[str, Any]]) -> None:
    """辞書に無い指標語で根にばらけた行を、既知の指標・節へ戻す（#577 / #579）。

    - 行名が既知の指標で始まる（`保有契約年換算保険料 個人保険`）→ その指標の節（内訳）
    - 行名が既知の節で始まる（`単体 営業収益`）→ その節の葉
    - 行名が既知の指標で終わる（`リテール事業 預かり資産`）→ その指標の節
    既に group + 葉に割れている行は触らない。
    """
    changed = True
    while changed:
        changed = False
        groups = sorted(
            {r["path"][1] for r in rows.values() if len(r["path"]) >= 2},
            key=len, reverse=True,
        )
        metrics = sorted({r["metric"] for r in rows.values()}, key=len, reverse=True)
        for element, r in rows.items():
            if not _auto_is_baked(r, element):
                continue
            base = strip_yoy_fragments(element)
            yoy = element[len(base):] if element.startswith(base) else ""
            # #577: 既知の節名で始まる（膠着の現物/信用より先。クリック 一日平均売買代金現物）
            hit_g = next((g for g in groups if g and not _fold_skip_section(g) and base.startswith(g + " ")), None)
            if hit_g:
                leaf = base[len(hit_g) + 1:]
                if leaf:
                    glued = _glued_inner(leaf, metrics)
                    if glued:
                        m, inner = glued
                        r["path"] = [AUTO_ROOT, hit_g, inner]
                        r["metric"] = m
                        r["leaf"] = m + yoy
                    else:
                        r["path"] = [AUTO_ROOT, hit_g]
                        r["metric"] = leaf
                        r["leaf"] = leaf + yoy
                    changed = True
                    continue
            # #579: 合計行のあとに内訳が続く（自分自身の指標名は除く）
            hit_m = next((m for m in metrics if m and base != m and (
                base.startswith(m + " ") or (base.startswith(m) and base[len(m):] in _GLUED_INNERS)
            )), None)
            if hit_m:
                rest = _auto_inner_name(base[len(hit_m):])
                if rest and rest != hit_m and not _fold_skip_section(rest):
                    r["path"] = [AUTO_ROOT, rest]
                    r["metric"] = hit_m
                    r["leaf"] = hit_m + yoy
                    changed = True
                    continue
            # #577: 既知の指標名で終わる
            hit_m = next((m for m in metrics if m and base.endswith(" " + m) and base != m), None)
            if hit_m:
                rest = _auto_inner_name(base[:-(len(hit_m) + 1)])
                if rest and rest != hit_m and not _fold_skip_section(rest):
                    r["path"] = [AUTO_ROOT, rest]
                    r["metric"] = hit_m
                    r["leaf"] = hit_m + yoy
                    changed = True
        if _fold_glued_inners(rows):
            changed = True


def _fold_glued_inners(rows: dict[str, dict[str, Any]]) -> bool:
    """節に入ったあとの `現物` / `信用` 膠着を 3 段目にする（#579。71770 `クリック 一日平均売買代金現物`）。"""
    metrics = sorted({r["metric"] for r in rows.values()}, key=len, reverse=True)
    changed = False
    for r in rows.values():
        if len(r["path"]) != 2:
            continue
        base = strip_yoy_fragments(r["leaf"])
        glued = _glued_inner(base, metrics)
        if glued is None:
            continue
        m, inner = glued
        yoy = r["leaf"][len(base):] if r["leaf"].startswith(base) else ""
        r["path"] = [r["path"][0], r["path"][1], inner]
        r["metric"] = m
        r["leaf"] = m + yoy
        changed = True
    return changed


def _rebuild_auto_groups(rows: Mapping[str, Mapping[str, Any]]
                         ) -> tuple[dict[str, int], dict[tuple[str, str], tuple[int, int]]]:
    group_first: dict[str, int] = {}
    bd_first: dict[tuple[str, str], tuple[int, int]] = {}
    for r in sorted(rows.values(), key=lambda x: x["order"]):
        path = r["path"]
        if len(path) >= 2:
            group_first.setdefault(path[1], r["order"])
        if len(path) >= 3:
            bd_first.setdefault((path[1], path[2]), (0, r["order"]))
    return group_first, bd_first


def _rebuild_metric_key(rows: Mapping[str, Mapping[str, Any]],
                        old: Mapping[str, tuple]) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for r in sorted(rows.values(), key=lambda x: x["order"]):
        m = r["metric"]
        in_node = len(r["path"]) > 1
        key = old[m][1] if m in old else (9, 9, 9, 9, r["order"])
        if m not in out or (not in_node and out[m][0]):
            out[m] = (in_node, key)
    return out


def _yoy_sales_parent(L: Mapping[str, Any], cands: Sequence[tuple[str, str, str, str]]) -> str | None:
    """指標語の無い前年比の親の 売上高。(channel, scope, country, metric) が同じ 売上高 が 1 つだけのとき寄せる。
    チャネル `直営+FC` は同じ 範囲 のチャネル無しの 売上高（全店）に寄せる。0 か 2 以上は決められない。
    """
    ch, sc, co = str(L["channel"]), str(L["scope"]), str(L["country"])
    metrics = {c[3] for c in cands if c[0] == ch and c[1] == sc and c[2] == co}
    if len(metrics) == 1:
        return next(iter(metrics))
    if ch == "直営+FC":
        nochan = {c[3] for c in cands if not c[0] and c[1] == sc and c[2] == co}
        if len(nochan) == 1:
            return next(iter(nochan))
    return None


def build_auto_tree(code: str, years: Mapping[int, Sequence[tuple[str, str]]],
                    group_metric: Mapping[str, str] | None = None,
                    company_metric: Mapping[str, str] | None = None) -> AutoTree:
    """years は 暦年 → [(保存名, unit)]（値のある行だけ、sort_order 順）。節の形と並びは渡した年すべてで決める。

    group_metric は節の行の指標を寄せる（既定は config の `group_metric`）。
    company_metric は会社の行の指標を寄せる（既定は config の `company_metric`）。
    """
    gmap = MONTHLY_GROUP_METRICS.get(str(code), {}) if group_metric is None else dict(group_metric)
    cmap = MONTHLY_COMPANY_METRICS.get(str(code), {}) if company_metric is None else dict(company_metric)
    newest = sorted(years, reverse=True)
    union = union_monthly_elements(
        [{"year": y, "elements": [{"element": e, "unit": u} for e, u in years[y]]} for y in newest], code=code)
    union_idx = {it["element"]: i for i, it in enumerate(union)}
    tail = {name: i for i, name in enumerate(issuer_segment_tail(code))}
    group_first: dict[str, int] = {}
    bd_first: dict[tuple[str, str], tuple[int, int]] = {}
    rows: dict[str, dict[str, Any]] = {}
    metric_key: dict[str, tuple] = {}
    sales: dict[tuple[str, ...], list[tuple[str, str, str, str]]] = {}
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
        rows[element] = {
            "path": list(path), "leaf": leaf, "metric": metric, "order": idx,
            "unknown_metric": not str(L["metric"] or ""),
            "has_scope_or_channel": bool(L["channel"] or L["scope"]),
        }
        if L["metric_kind"] == "sales" and not monthly_row_unit_rank(unit):
            sales.setdefault(path, []).append((str(L["channel"]), str(L["scope"]), str(L["country"]), metric))
        key = (
            _AUTO_CHANNEL_RANK.get(str(L["channel"]), 3),
            _AUTO_COUNTRY_RANK.get(str(L["country"]), 3),
            _AUTO_SCOPE_RANK.get(str(L["scope"]), 2),
            monthly_row_slot(canon, unit, code),
            idx,
        )
        if metric not in metric_key or (len(path) == 1 and metric_key[metric][0]):
            metric_key[metric] = (len(path) > 1, key)

    # 指標の語の無い前年比（アスモ `アスモトレーディング(前年比)`、`合計(前年比)`）は、その節の金額の売上高の行が
    # 1 つに決まればその指標の前年比にする（決まらなければ行名のまま根）。節の金額の行を先に見てから置く。
    bare: list[tuple[str, str, Mapping[str, Any], str, str, str, str]] = []
    # 指標の語が無くて チャネル / 範囲 だけの前年比（7134 `直営 全店(前年比)`、`FC 既存(前年比)`）は、
    # 同じ節の 同じ チャネル + 範囲 の 売上高 の行に寄せる（#578 / #580）。直営+FC はチャネル無しの売上高へ。
    # 売上高が 複数で決まらなければ行名のまま根。節の売上高の行を先に見てから寄せるので、いったん持ち送り。
    pending: list[tuple[str, str, Mapping[str, Any], str, str, str]] = []
    deferred: set[str] = set()
    for y in newest:
        for element, unit in years[y]:
            if element in rows or element in deferred:
                continue
            L = monthly_element_label(element, unit, code)
            canon = canonicalize_element_label(element, unit, code)
            group, leaf = _auto_split(L)
            if monthly_row_unit_rank(unit) and not any(L[k] for k in ("metric", "breakdown", "scope", "channel")):
                g = str(L["group"] or "")
                own = g if g and strip_yoy_fragments(str(L["display"])) == g else ""
                bare.append((element, unit, L, canon, own, group, leaf))
                deferred.add(element)
                continue
            if monthly_row_unit_rank(unit) and not L["metric"] and not L["breakdown"] \
                    and (L["channel"] or L["scope"]):
                pending.append((element, unit, L, canon, group, leaf))
                deferred.add(element)
                continue
            place(element, unit, L, canon, group, leaf)
    for element, unit, L, canon, group, leaf in pending:
        m = _yoy_sales_parent(L, sales.get((AUTO_ROOT, *((group,) if group else ())), []))
        if m is not None:
            suffix = str(L["display"])[len(strip_yoy_fragments(str(L["display"]))):]
            place(element, unit, L, canon, group, m + suffix, m)
        else:
            place(element, unit, L, canon, group, leaf)
    for element, unit, L, canon, own, group, leaf in bare:
        cands = {c[3] for c in sales.get((AUTO_ROOT, *((own,) if own else ())), [])}
        if len(cands) == 1:
            m = next(iter(cands))
            place(element, unit, L, canon, own, m + str(L["display"])[len(strip_yoy_fragments(str(L["display"]))):], m)
        else:
            place(element, unit, L, canon, group, leaf)
    _fold_yoy_rate_metrics(rows)
    _fold_baked_auto_rows(rows)
    _fold_consolidated_metrics(rows)
    group_first, bd_first = _rebuild_auto_groups(rows)
    metric_key = _rebuild_metric_key(rows, metric_key)
    company = {r["metric"] for r in rows.values() if len(r["path"]) == 1}
    node_only = {r["metric"] for r in rows.values() if len(r["path"]) > 1} - company
    folded: list[str] = []
    for src, dst in cmap.items():
        if dst not in node_only:
            continue
        hit = False
        for r in rows.values():
            if len(r["path"]) == 1 and r["metric"] == src:
                r["metric"] = dst
                hit = True
        if not hit or src not in metric_key:
            continue
        metric_key[dst] = metric_key.pop(src)
        if src == "日本 " + dst:
            folded.append(dst)
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

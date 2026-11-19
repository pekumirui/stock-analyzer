"""サプライチェーンの素材マスタ（`config/supply/supply_chain.json`）の読込と検査。

素材は有向グラフ。1 つの素材が複数の原料から合流する（PET 樹脂 ← エチレングリコール・テレフタル酸）。
リサイクル資源（recycled）は flows の入力を持たず、使い終わった製品からの回収（returns）を別に持つ。
returns は循環を作るので、flows の検査（循環なし）と画面の配置・祖先と子孫には使わない。
画面仕様は `docs/screen/supply.md`。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import SUPPLY_CHAIN_PATH, SUPPLY_COMPANIES_PATH
from .material_stats import TRADE_SOURCE

KINDS = ("raw", "mid", "prod", "recycled")
SOURCE_KINDS = ("raw", "recycled")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SupplyChainError(ValueError):
    pass


def validate_supply_chain(data: Any) -> dict[str, list]:
    """素材と流れと回収を検査し、`{materials, flows, returns}` を返す。違反は SupplyChainError。"""
    if not isinstance(data, dict):
        raise SupplyChainError("top level must be an object")
    materials = data.get("materials")
    flows = data.get("flows")
    returns = data.get("returns", [])
    if not isinstance(materials, list) or not isinstance(flows, list) or not isinstance(returns, list):
        raise SupplyChainError("materials, flows and returns must be lists")
    unknown_top = set(data) - {"materials", "flows", "returns"}
    if unknown_top:
        raise SupplyChainError(f"unknown top-level keys {sorted(unknown_top)}")

    ids: dict[str, str] = {}
    names: set[str] = set()
    out_materials: list[dict[str, Any]] = []
    for m in materials:
        if not isinstance(m, dict):
            raise SupplyChainError(f"material must be an object: {m!r}")
        mid, name, kind = m.get("id"), m.get("name"), m.get("kind")
        if not isinstance(mid, str) or not _ID_RE.match(mid):
            raise SupplyChainError(f"bad material id: {mid!r}")
        if mid in ids:
            raise SupplyChainError(f"duplicate material id: {mid}")
        if not isinstance(name, str) or not name.strip():
            raise SupplyChainError(f"{mid}: name is required")
        if name in names:
            raise SupplyChainError(f"duplicate material name: {name}")
        if kind not in KINDS:
            raise SupplyChainError(f"{mid}: kind must be one of {KINDS}: {kind!r}")
        note = m.get("note")
        if note is not None and not isinstance(note, str):
            raise SupplyChainError(f"{mid}: note must be a string")
        unknown = set(m) - {"id", "name", "kind", "note"}
        if unknown:
            raise SupplyChainError(f"{mid}: unknown keys {sorted(unknown)}")
        ids[mid] = kind
        names.add(name)
        out_materials.append({"id": mid, "name": name, "kind": kind, "note": note or None})

    seen: set[tuple[str, str]] = set()
    parents: dict[str, list[str]] = {mid: [] for mid in ids}
    children: dict[str, list[str]] = {mid: [] for mid in ids}
    for f in flows:
        if not (isinstance(f, list) and len(f) == 2 and all(isinstance(x, str) for x in f)):
            raise SupplyChainError(f"flow must be [from, to]: {f!r}")
        src, dst = f
        for x in (src, dst):
            if x not in ids:
                raise SupplyChainError(f"flow {src}->{dst}: unknown material {x}")
        if src == dst:
            raise SupplyChainError(f"self loop: {src}")
        if (src, dst) in seen:
            raise SupplyChainError(f"duplicate flow: {src}->{dst}")
        seen.add((src, dst))
        parents[dst].append(src)
        children[src].append(dst)

    returned: dict[str, list[str]] = {mid: [] for mid in ids}
    seen_ret: set[tuple[str, str]] = set()
    for r in returns:
        if not (isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r)):
            raise SupplyChainError(f"return must be [from, to]: {r!r}")
        src, dst = r
        for x in (src, dst):
            if x not in ids:
                raise SupplyChainError(f"return {src}->{dst}: unknown material {x}")
        if ids[dst] != "recycled":
            raise SupplyChainError(f"return {src}->{dst}: {dst} must be recycled")
        if ids[src] in SOURCE_KINDS:
            raise SupplyChainError(f"return {src}->{dst}: {src} is {ids[src]}")
        if (src, dst) in seen_ret:
            raise SupplyChainError(f"duplicate return: {src}->{dst}")
        seen_ret.add((src, dst))
        returned[dst].append(src)

    for mid, kind in ids.items():
        if kind in SOURCE_KINDS and parents[mid]:
            raise SupplyChainError(f"{mid}: {kind} material must not have inputs ({parents[mid]})")
        if kind not in SOURCE_KINDS and not parents[mid]:
            raise SupplyChainError(f"{mid}: {kind} needs at least one input")
        if kind == "recycled" and not returned[mid]:
            raise SupplyChainError(f"{mid}: recycled needs at least one return")

    _reject_cycles(children)
    return {"materials": out_materials, "flows": [[f[0], f[1]] for f in flows],
            "returns": [[r[0], r[1]] for r in returns]}


def _reject_cycles(children: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}  # 1=探索中, 2=済

    def visit(node: str, path: list[str]) -> None:
        state[node] = 1
        for nxt in children[node]:
            if state.get(nxt) == 1:
                cycle = path[path.index(nxt):] + [node, nxt]
                raise SupplyChainError(f"cycle: {' -> '.join(cycle)}")
            if state.get(nxt) is None:
                visit(nxt, path + [node])
        state[node] = 2

    for node in children:
        if state.get(node) is None:
            visit(node, [])


def load_supply_chain(path: Path = SUPPLY_CHAIN_PATH) -> dict[str, list]:
    return validate_supply_chain(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def supply_graph() -> dict[str, list]:
    """API 用。マスタは git 管理でデプロイ時に再起動するため、プロセス内で一度だけ読む。"""
    return load_supply_chain()


STATUSES = ("listed", "private", "foreign")
_CODE_RE = re.compile(r"^[1-9][0-9A-Z]{3}[0-9]$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _keys(obj: dict, allowed: set[str], where: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise SupplyChainError(f"{where}: unknown keys {sorted(unknown)}")


def _opt_str(obj: dict, key: str, where: str) -> str | None:
    v = obj.get(key)
    if v is not None and (not isinstance(v, str) or not v.strip()):
        raise SupplyChainError(f"{where}: {key} must be a non-empty string")
    return v


def validate_companies(data: Any, material_ids: set[str]) -> dict[str, Any]:
    """企業マスタを検査し、API の形にする。違反は SupplyChainError。

    makes は明示の組と、能力表に載る組（出典は能力表の出典）の和。
    parent は `parent_id`、無ければ 50% を超える出資者。
    """
    if not isinstance(data, dict):
        raise SupplyChainError("top level must be an object")
    _keys(data, {"sources", "companies", "makes", "capacities", "trade"}, "top level")
    sources = data.get("sources") or {}
    if not isinstance(sources, dict):
        raise SupplyChainError("sources must be an object")
    out_sources: dict[str, dict[str, Any]] = {}
    for key, s in sources.items():
        if not isinstance(s, dict):
            raise SupplyChainError(f"source {key}: must be an object")
        _keys(s, {"title", "url", "checked"}, f"source {key}")
        if _opt_str(s, "title", f"source {key}") is None:
            raise SupplyChainError(f"source {key}: title is required")
        if not isinstance(s.get("checked"), str) or not _DATE_RE.match(s["checked"]):
            raise SupplyChainError(f"source {key}: checked must be YYYY-MM-DD")
        out_sources[key] = {"title": s["title"], "url": _opt_str(s, "url", f"source {key}"), "checked": s["checked"]}

    def src(v: Any, where: str) -> str | None:
        if v is not None and v not in out_sources:
            raise SupplyChainError(f"{where}: unknown source {v!r}")
        return v

    companies = data.get("companies")
    if not isinstance(companies, list):
        raise SupplyChainError("companies must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for c in companies:
        if not isinstance(c, dict):
            raise SupplyChainError(f"company must be an object: {c!r}")
        cid, name, status = c.get("id"), c.get("name"), c.get("status")
        if not isinstance(cid, str) or not _ID_RE.match(cid):
            raise SupplyChainError(f"bad company id: {cid!r}")
        # ハッシュ #supply/<id> を素材と共有するので、素材の id とも重ならない
        if cid in by_id or cid in material_ids:
            raise SupplyChainError(f"duplicate id: {cid}")
        _keys(c, {"id", "name", "code", "status", "parent_id", "owners", "note", "source"}, cid)
        if not isinstance(name, str) or not name.strip():
            raise SupplyChainError(f"{cid}: name is required")
        if name in names:
            raise SupplyChainError(f"duplicate company name: {name}")
        if status not in STATUSES:
            raise SupplyChainError(f"{cid}: status must be one of {STATUSES}: {status!r}")
        code = c.get("code")
        if (status == "listed") != (code is not None):
            raise SupplyChainError(f"{cid}: code is required for listed companies only")
        if code is not None and (not isinstance(code, str) or not _CODE_RE.match(code)):
            raise SupplyChainError(f"{cid}: bad code {code!r}")
        owners = c.get("owners")
        if owners is not None:
            if c.get("parent_id") is not None:
                raise SupplyChainError(f"{cid}: use either parent_id or owners")
            if not (isinstance(owners, list) and owners and all(
                    isinstance(o, list) and len(o) == 2 and isinstance(o[0], str)
                    and isinstance(o[1], (int, float)) and 0 < o[1] <= 100 for o in owners)):
                raise SupplyChainError(f"{cid}: owners must be [[company id, percent], ...]")
            if sum(o[1] for o in owners) > 100.001:
                raise SupplyChainError(f"{cid}: owners exceed 100%")
        names.add(name)
        by_id[cid] = {"id": cid, "name": name, "code": code, "status": status,
                      "parent_id": c.get("parent_id"), "owners": [[o[0], o[1]] for o in owners or []],
                      "note": _opt_str(c, "note", cid), "source": src(c.get("source"), cid)}
    for c in by_id.values():
        refs = [o[0] for o in c["owners"]] + ([c["parent_id"]] if c["parent_id"] is not None else [])
        for r in refs:
            if r not in by_id or r == c["id"]:
                raise SupplyChainError(f"{c['id']}: bad parent or owner {r!r}")
        if len(set(refs)) != len(refs):
            raise SupplyChainError(f"{c['id']}: duplicate owner")
        if c["parent_id"] is None:
            c["parent_id"] = next((o[0] for o in c["owners"] if o[1] > 50), None)
    for cid in by_id:
        seen, cur = {cid}, by_id[cid]["parent_id"]
        while cur is not None:
            if cur in seen:
                raise SupplyChainError(f"parent cycle at {cid}")
            seen.add(cur)
            cur = by_id[cur]["parent_id"]

    capacities = data.get("capacities") or []
    if not isinstance(capacities, list):
        raise SupplyChainError("capacities must be a list")
    out_caps: list[dict[str, Any]] = []
    cap_ids: set[str] = set()
    for t in capacities:
        if not isinstance(t, dict):
            raise SupplyChainError(f"capacity must be an object: {t!r}")
        tid = t.get("id")
        if not isinstance(tid, str) or not _ID_RE.match(tid) or tid in cap_ids:
            raise SupplyChainError(f"bad or duplicate capacity id: {tid!r}")
        where = f"capacity {tid}"
        _keys(t, {"id", "materials", "label", "unit", "as_of", "source", "url", "note", "rows"}, where)
        cap_ids.add(tid)
        mats = t.get("materials")
        if not (isinstance(mats, list) and mats and all(m in material_ids for m in mats)) or len(set(mats)) != len(mats):
            raise SupplyChainError(f"{where}: materials must be known material ids")
        for k in ("label", "unit"):
            if _opt_str(t, k, where) is None:
                raise SupplyChainError(f"{where}: {k} is required")
        if not isinstance(t.get("as_of"), str) or not _MONTH_RE.match(t["as_of"]):
            raise SupplyChainError(f"{where}: as_of must be YYYY-MM")
        if t.get("source") is None:
            raise SupplyChainError(f"{where}: source is required")
        rows = t.get("rows")
        if not (isinstance(rows, list) and rows):
            raise SupplyChainError(f"{where}: rows must be a non-empty list")
        seen_rows: set[str] = set()
        for r in rows:
            if not (isinstance(r, list) and len(r) == 2 and r[0] in by_id
                    and isinstance(r[1], (int, float)) and not isinstance(r[1], bool) and r[1] > 0):
                raise SupplyChainError(f"{where}: row must be [company id, value > 0]: {r!r}")
            if r[0] in seen_rows:
                raise SupplyChainError(f"{where}: duplicate company {r[0]}")
            seen_rows.add(r[0])
        out_caps.append({"id": tid, "materials": list(mats), "label": t["label"], "unit": t["unit"],
                         "as_of": t["as_of"], "source": src(t["source"], where),
                         "url": _opt_str(t, "url", where), "note": _opt_str(t, "note", where),
                         "rows": [[r[0], r[1]] for r in rows]})

    makes: dict[tuple[str, str], str | None] = {}
    for m in data.get("makes") or []:
        if not (isinstance(m, list) and len(m) in (2, 3) and m[0] in by_id and m[1] in material_ids):
            raise SupplyChainError(f"make must be [company id, material id(, source)]: {m!r}")
        if (m[0], m[1]) in makes:
            raise SupplyChainError(f"duplicate make: {m[0]}->{m[1]}")
        makes[(m[0], m[1])] = src(m[2] if len(m) == 3 else None, f"make {m[0]}->{m[1]}")
    for t in out_caps:
        for cid, _ in t["rows"]:
            for mat in t["materials"]:
                if makes.get((cid, mat)) is None:
                    makes[(cid, mat)] = t["source"]

    trade: list[dict[str, Any]] = []
    seen_trade: set[tuple[str, str]] = set()
    for tr in data.get("trade") or []:
        if not (isinstance(tr, list) and len(tr) in (2, 3) and tr[0] in by_id and tr[1] in by_id and tr[0] != tr[1]):
            raise SupplyChainError(f"trade must be [seller id, buyer id(, source)]: {tr!r}")
        if (tr[0], tr[1]) in seen_trade:
            raise SupplyChainError(f"duplicate trade: {tr[0]}->{tr[1]}")
        seen_trade.add((tr[0], tr[1]))
        trade.append({"seller": tr[0], "buyer": tr[1], "source": src(tr[2] if len(tr) == 3 else None, "trade")})

    return {
        "sources": out_sources,
        "companies": list(by_id.values()),
        "makes": [{"company": c, "material": m, "source": s} for (c, m), s in makes.items()],
        "capacities": out_caps,
        "trade": trade,
    }


def load_companies(path: Path = SUPPLY_COMPANIES_PATH) -> dict[str, Any]:
    ids = {m["id"] for m in supply_graph()["materials"]}
    return validate_companies(json.loads(path.read_text(encoding="utf-8")), ids)


@lru_cache(maxsize=1)
def supply_companies() -> dict[str, Any]:
    """API 用。素材マスタと同じくプロセス内で一度だけ読む。"""
    return load_companies()


METRIC_ORDER = ("production", "shipment", "inventory", "import", "export", "processing", "domestic_sales", "apparent")


def apparent_consumption(series: dict[str, dict[str, float]]) -> dict[str, float]:
    """見掛け消費 = 生産 + 輸入 − 輸出 − 在庫の増加分。4 つと前月在庫が揃う月だけ（保存しない）"""
    need = ("production", "import", "export", "inventory")
    if not all(k in series for k in need):
        return {}
    inv = series["inventory"]
    out: dict[str, float] = {}
    for month in sorted(series["production"]):
        y, m = int(month[:4]), int(month[5:])
        prev = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
        if prev not in inv or not all(month in series[k] for k in need):
            continue
        out[month] = (series["production"][month] + series["import"][month]
                      - series["export"][month] - (inv[month] - inv[prev]))
    return out


def monthly_payload(rows: list[Any], material_id: str,
                    trade_months: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """material_monthly の行 → {material_id, series: [{metric, unit, sources, points}]}。

    points は [YYYY-MM, 値, revision]（1 速報 / 2 確報 / 3 年報）。見掛け消費の revision は材料の最小。
    同じ指標に単位の違う行があれば（統計の取り方が違う）別の系列にする。
    同じ指標・単位を複数の出典が持てば足す（ダイカスト 2560 + 鋳物 2550）。全部の出典がそろう月だけ。
    trade_months は {指標: {公表済みの月: revision}}。貿易統計は 0 の月を保存しないので、
    貿易だけの系列はその月を 0 で埋める（埋めないと 0 と未公表が区別できない）。
    """
    cells: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    sources: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        key = (r.metric, r.unit)
        cells.setdefault(key, {}).setdefault(r.month, {})[r.source] = r
        sources.setdefault(key, set()).add(r.source)
    by: dict[tuple[str, str], dict[str, float]] = {}
    revs: dict[tuple[str, str], dict[str, int]] = {}
    for key, months in cells.items():
        for month, got in months.items():
            if set(got) != sources[key]:
                continue
            by.setdefault(key, {})[month] = sum(r.value for r in got.values())
            revs.setdefault(key, {})[month] = min(r.revision for r in got.values())
    for key, pts in by.items():
        if sources[key] != {TRADE_SOURCE}:
            continue
        for month, rev in (trade_months or {}).get(key[0], {}).items():
            if month not in pts:
                pts[month] = 0.0
                revs[key][month] = rev
    for unit in sorted({u for _, u in by}):
        app = apparent_consumption({m: pts for (m, u), pts in by.items() if u == unit})
        if app:
            parts = [revs[(m, unit)] for m in ("production", "import", "export", "inventory")]
            by[("apparent", unit)] = app
            revs[("apparent", unit)] = {month: min(p.get(month, 3) for p in parts) for month in app}
            sources[("apparent", unit)] = set()
    series = [
        {"metric": metric, "unit": unit, "sources": sorted(sources[(metric, unit)]),
         "points": [[m, v, revs[(metric, unit)][m]] for m, v in sorted(pts.items())]}
        for (metric, unit), pts in sorted(by.items(), key=lambda kv: (METRIC_ORDER.index(kv[0][0]), kv[0][1]))
    ]
    return {"material_id": material_id, "series": series}


def _month_add(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:]) + n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def country_payload(rows: list[Any], material_id: str, flow: str, months: int = 12) -> dict[str, Any]:
    """material_trade_country の行 → 直近 months か月の相手国別の合計と割合。

    期間は保存済みの最新月から遡る。単位が混ざれば最新月の単位の行だけ足す。
    """
    out: dict[str, Any] = {"material_id": material_id, "flow": flow, "unit": None, "from": None, "to": None,
                           "sokuho": False, "total": 0.0, "countries": []}
    if not rows:
        return out
    latest = max(r.month for r in rows)
    unit = next(r.unit for r in rows if r.month == latest)
    start = _month_add(latest, 1 - months)
    sums: dict[str, float] = {}
    names: dict[str, str] = {}
    for r in rows:
        if r.unit != unit or not (start <= r.month <= latest):
            continue
        sums[r.country] = sums.get(r.country, 0.0) + r.value
        names[r.country] = r.country_name
    total = sum(sums.values())
    out.update({
        "unit": unit, "from": start, "to": latest, "total": total,
        "sokuho": any(r.revision == 1 for r in rows if r.month == latest),
        "countries": [{"code": c, "name": names[c], "value": v, "share": v / total if total else 0.0}
                      for c, v in sorted(sums.items(), key=lambda kv: -kv[1])],
    })
    return out

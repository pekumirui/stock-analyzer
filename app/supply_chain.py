"""サプライチェーンの素材マスタ（`config/supply_chain.json`）の読込と検査。

素材は有向グラフ。1 つの素材が複数の原料から合流する（PET 樹脂 ← エチレングリコール・テレフタル酸）。
画面仕様は `docs/DESIGN_supply.md`。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import SUPPLY_CHAIN_PATH

KINDS = ("raw", "mid", "prod")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SupplyChainError(ValueError):
    pass


def validate_supply_chain(data: Any) -> dict[str, list]:
    """素材と流れを検査し、`{materials, flows}` を返す。違反は SupplyChainError。"""
    if not isinstance(data, dict):
        raise SupplyChainError("top level must be an object")
    materials = data.get("materials")
    flows = data.get("flows")
    if not isinstance(materials, list) or not isinstance(flows, list):
        raise SupplyChainError("materials and flows must be lists")

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

    for mid, kind in ids.items():
        # 原料は地球から直接採るもの。他の素材から作られるなら中間財か製品
        if kind == "raw" and parents[mid]:
            raise SupplyChainError(f"{mid}: raw material must not have inputs ({parents[mid]})")
        if kind != "raw" and not parents[mid]:
            raise SupplyChainError(f"{mid}: {kind} needs at least one input")

    _reject_cycles(children)
    return {"materials": out_materials, "flows": [[f[0], f[1]] for f in flows]}


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

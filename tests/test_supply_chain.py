"""サプライチェーンの素材マスタ (config/supply_chain.json) の検査と /api/supply/graph。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-supply-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.supply_chain import SupplyChainError, load_supply_chain, validate_supply_chain  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _graph(materials: list, flows: list) -> dict:
    return {"materials": materials, "flows": flows}


RAW = {"id": "crude", "name": "原油", "kind": "raw"}
MID = {"id": "naphtha", "name": "ナフサ", "kind": "mid"}
PROD = {"id": "bag", "name": "レジ袋", "kind": "prod"}


def _rejects(data: object, needle: str) -> None:
    try:
        validate_supply_chain(data)
    except SupplyChainError as e:
        if needle not in str(e):
            _fail(f"expected {needle!r} in {e}")
        return
    _fail(f"accepted invalid graph ({needle}): {data}")


def test_config_is_valid() -> None:
    g = load_supply_chain()
    by_id = {m["id"]: m for m in g["materials"]}
    if "crude" not in by_id or by_id["crude"]["kind"] != "raw":
        _fail("crude oil must be a raw material")
    # 合流: PET 樹脂はエチレン系 (EG) とキシレン系 (PTA) の 2 本から作られる
    pet_inputs = {a for a, b in g["flows"] if b == "pet"}
    if pet_inputs != {"eg", "pta"}:
        _fail(f"pet inputs: {pet_inputs}")


def test_valid_minimal() -> None:
    g = validate_supply_chain(_graph([RAW, MID, PROD], [["crude", "naphtha"], ["naphtha", "bag"]]))
    if g["materials"][0] != {**RAW, "note": None} or g["flows"] != [["crude", "naphtha"], ["naphtha", "bag"]]:
        _fail(g)


def test_rejects() -> None:
    _rejects(_graph([RAW, RAW], []), "duplicate material id")
    _rejects(_graph([RAW, {**MID, "name": "原油"}], [["crude", "naphtha"]]), "duplicate material name")
    _rejects(_graph([{**RAW, "kind": "fuel"}], []), "kind")
    _rejects(_graph([{**RAW, "id": "Crude"}], []), "bad material id")
    _rejects(_graph([{**RAW, "makers": []}], []), "unknown keys")
    _rejects(_graph([RAW, MID], [["crude", "naphtha"], ["crude", "nope"]]), "unknown material nope")
    _rejects(_graph([RAW, MID], [["crude", "naphtha"], ["crude", "naphtha"]]), "duplicate flow")
    _rejects(_graph([RAW, MID], [["crude", "naphtha"], ["naphtha", "crude"]]), "raw material must not have inputs")
    _rejects(_graph([RAW, MID], []), "needs at least one input")
    _rejects(_graph([RAW, MID], [["crude", "naphtha"], ["naphtha", "naphtha"]]), "self loop")
    _rejects(
        _graph([RAW, MID, PROD], [["crude", "naphtha"], ["naphtha", "bag"], ["bag", "naphtha"]]),
        "cycle",
    )
    _rejects(_graph([RAW], [["crude"]]), "flow must be")


def test_api() -> None:
    r = TestClient(app).get("/api/supply/graph")
    if r.status_code != 200:
        _fail(f"{r.status_code} {r.text[:200]}")
    body = r.json()
    if body != load_supply_chain():
        _fail("api payload differs from config")
    client = TestClient(app)
    r = client.get("/api/supply/monthly/crude")
    if r.status_code != 200 or r.json() != {"material_id": "crude", "series": []}:
        _fail(f"monthly empty: {r.status_code} {r.text[:200]}")
    if client.get("/api/supply/monthly/nope").status_code != 404:
        _fail("unknown material must be 404")


def main() -> None:
    test_config_is_valid()
    test_valid_minimal()
    test_rejects()
    test_api()
    print("ok: supply_chain")


if __name__ == "__main__":
    main()

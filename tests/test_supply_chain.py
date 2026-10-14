"""サプライチェーンの素材マスタ (config/supply_chain.json)・企業マスタ (config/supply_companies.json) の検査と /api/supply/*。"""
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
from app.supply_chain import (  # noqa: E402
    SupplyChainError,
    load_companies,
    load_supply_chain,
    validate_companies,
    validate_supply_chain,
)


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


MATS = {"crude", "naphtha", "ethylene"}
SRC = {"s": {"title": "能力調査", "checked": "2026-10-14"}}


def _co(**kw: object) -> dict:
    return {"id": "a", "name": "A社", "status": "private", **kw}


def _companies(companies: list, **kw: object) -> dict:
    return {"sources": SRC, "companies": companies, **kw}


def _rejects_co(data: object, needle: str) -> None:
    try:
        validate_companies(data, MATS)
    except SupplyChainError as e:
        if needle not in str(e):
            _fail(f"expected {needle!r} in {e}")
        return
    _fail(f"accepted invalid companies ({needle}): {data}")


def test_companies_config_is_valid() -> None:
    d = load_companies()
    by_id = {c["id"]: c for c in d["companies"]}
    # 50% を超える出資者が親になる（PS ジャパンは旭化成 62%）
    if by_id["psj"]["parent_id"] != "asahikasei" or by_id["mcae"]["parent_id"] is not None:
        _fail(f"parent from owners: {by_id['psj']} {by_id['mcae']}")
    eth = next(t for t in d["capacities"] if t["id"] == "ethylene")
    if sum(v for _, v in eth["rows"]) != 6162:
        _fail("ethylene capacity total must match METI (6,162 kt)")
    makes = {(m["company"], m["material"]): m["source"] for m in d["makes"]}
    if makes.get(("tosoh", "ethylene")) != "meti_cap_2024" or ("toyota", "car") not in makes:
        _fail("makes must include capacity rows and explicit pairs")


def test_companies_valid_minimal() -> None:
    d = validate_companies(_companies(
        [_co(), _co(id="b", name="B社", owners=[["a", 60], ["c", 40]], source="s"),
         _co(id="c", name="C社", status="listed", code="72030")],
        makes=[["a", "crude"]],
        capacities=[{"id": "eth", "materials": ["ethylene"], "label": "エチレン", "unit": "千t/年",
                     "as_of": "2024-12", "source": "s", "rows": [["b", 100], ["c", 50]]}],
        trade=[["b", "c"]]), MATS)
    by_id = {c["id"]: c for c in d["companies"]}
    if by_id["b"]["parent_id"] != "a" or by_id["c"]["code"] != "72030":
        _fail(by_id)
    makes = {(m["company"], m["material"]): m["source"] for m in d["makes"]}
    if makes != {("a", "crude"): None, ("b", "ethylene"): "s", ("c", "ethylene"): "s"}:
        _fail(makes)
    if d["trade"] != [{"seller": "b", "buyer": "c", "source": None}]:
        _fail(d["trade"])


def test_companies_rejects() -> None:
    _rejects_co(_companies([_co(), _co()]), "duplicate id")
    _rejects_co(_companies([_co(id="crude")]), "duplicate id")
    _rejects_co(_companies([_co(), _co(id="b")]), "duplicate company name")
    _rejects_co(_companies([_co(status="listed")]), "code is required")
    _rejects_co(_companies([_co(code="72030")]), "code is required")
    _rejects_co(_companies([_co(status="listed", code="7203")]), "bad code")
    _rejects_co(_companies([_co(parent_id="nope")]), "bad parent")
    _rejects_co(_companies([_co(parent_id="b"), _co(id="b", name="B社", parent_id="a")]), "parent cycle")
    _rejects_co(_companies([_co(owners=[["a", 120]])]), "owners must be")
    _rejects_co(_companies([_co(), _co(id="b", name="B社", owners=[["a", 60], ["a", 40]])]), "duplicate owner")
    _rejects_co(_companies([_co(source="nope")]), "unknown source")
    _rejects_co(_companies([_co(makers=1)]), "unknown keys")
    _rejects_co(_companies([_co()], makes=[["a", "nope"]]), "make must be")
    _rejects_co(_companies([_co()], makes=[["a", "crude"], ["a", "crude"]]), "duplicate make")
    cap = {"id": "eth", "materials": ["ethylene"], "label": "エチレン", "unit": "千t/年",
           "as_of": "2024-12", "source": "s", "rows": [["a", 1]]}
    _rejects_co(_companies([_co()], capacities=[{**cap, "as_of": "2024"}]), "as_of")
    _rejects_co(_companies([_co()], capacities=[{**cap, "rows": [["a", 0]]}]), "row must be")
    _rejects_co(_companies([_co()], capacities=[{**cap, "rows": [["a", 1], ["a", 2]]}]), "duplicate company")
    _rejects_co(_companies([_co()], capacities=[{**cap, "materials": ["nope"]}]), "materials")
    _rejects_co(_companies([_co()], trade=[["a", "a"]]), "trade must be")


def test_companies_api() -> None:
    r = TestClient(app).get("/api/supply/companies")
    if r.status_code != 200 or r.json() != load_companies():
        _fail(f"companies api: {r.status_code} {r.text[:200]}")


def main() -> None:
    test_config_is_valid()
    test_valid_minimal()
    test_rejects()
    test_api()
    test_companies_config_is_valid()
    test_companies_valid_minimal()
    test_companies_rejects()
    test_companies_api()
    print("ok: supply_chain")


if __name__ == "__main__":
    main()

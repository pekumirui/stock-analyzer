"""シード DB（data/seed → jobs/seed_db.py）。手元の stock.db は触らない。"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-seed-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from jobs import seed_db  # noqa: E402
from tools.seed.build_seed import load_codes  # noqa: E402

SEED = ROOT / "data" / "seed"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_csv_columns_match_models() -> None:
    """スキーマを変えたらシードも作り直す（python tools/seed/build_seed.py）。"""
    for path in sorted(SEED.glob("*.csv")):
        table = Base.metadata.tables.get(path.stem)
        if table is None:
            _fail(f"{path.name}: モデルに無い表")
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        want = [c.name for c in table.columns]
        if header != want:
            _fail(f"{path.name}: 列がモデルと違う（build_seed.py で作り直す）\n  csv={header}\n  model={want}")


def test_codes_are_in_master() -> None:
    with (SEED / "stocks.csv").open(encoding="utf-8", newline="") as f:
        master = {r["code"] for r in csv.DictReader(f)}
    missing = [c for c in load_codes() if c not in master]
    if missing:
        _fail(f"codes.txt の銘柄が stocks.csv に無い: {missing}")


def test_load_and_serve() -> None:
    if seed_db.main() != 0:
        _fail("seed_db failed")
    if not seed_db.is_seed_db():
        _fail("seed DB must set PRAGMA user_version")
    manifest = json.loads((SEED / "manifest.json").read_text(encoding="utf-8"))
    with engine.connect() as conn:
        for table, n in manifest["counts"].items():
            got = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # nosec B608 (manifest の表名)
            if got != n:
                _fail(f"{table}: manifest {n} / loaded {got}")
        if not conn.execute(text("SELECT COUNT(*) FROM indicators")).scalar():
            _fail("indicators must be recomputed")
        if conn.execute(text("SELECT COUNT(*) FROM indicators WHERE code = '89510'")).scalar():
            _fail("REIT must not get indicators")
    if seed_db.main() != 1:
        _fail("seed into a non-empty DB must refuse")

    c = TestClient(app)
    # verify skill の doctor と同じ条件
    r = c.get("/api/screener", params={"stage": 2, "rs_min": 70})
    if r.status_code != 200 or not r.json().get("results"):
        _fail(f"screener stage=2 rs_min=70 must not be empty: {r.status_code} {r.text[:200]}")
    hits = c.get("/api/stocks/search", params={"q": "トヨタ"}).json()
    if not any(h["code"] == "72030" for h in hits):
        _fail(f"search トヨタ: {hits!r}")
    r = c.get("/api/chart/72030")
    if r.status_code != 200 or len(r.json().get("candles") or []) < 200:
        _fail(f"chart 72030 needs 200+ candles for MA200: {r.status_code}")
    r = c.get("/api/v1/tables/78230/2026")
    if r.status_code != 200 or not r.json().get("elements"):
        _fail(f"monthly 78230/2026: {r.status_code}")
    r = c.get("/api/v1/tables")
    if r.status_code != 200 or len(r.json()) != manifest["counts"]["monthly_issuers"]:
        _fail(f"monthly list must cover every issuer: {r.status_code}")


def main() -> None:
    test_csv_columns_match_models()
    test_codes_are_in_master()
    test_load_and_serve()
    print("ok: seed db")


if __name__ == "__main__":
    main()

"""CI smoke: empty DB で起動し、デモ投入後に主要 API が空でないこと。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
assert _spec and _spec.loader
_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        raise SystemExit(f"{label}: {resp.status_code} {resp.text[:400]}")


def main() -> None:
    from app.config import apply_dotenv

    got: dict[str, str] = {"FOO": "keep"}
    apply_dotenv(
        'JQUANTS_API_KEY=secret\n# c\nexport BAR="quoted"\nFOO=new\n=bad\nNOT KEY=x\n',
        got,
    )
    if got.get("JQUANTS_API_KEY") != "secret" or got.get("BAR") != "quoted" or got.get("FOO") != "keep":
        raise SystemExit(f"apply_dotenv: {got}")
    if "NOT KEY" in got or "" in got:
        raise SystemExit(f"apply_dotenv accepted bad keys: {got}")

    client = TestClient(app)

    r = client.get("/")
    _expect(r, 200, "GET /")
    if len(r.content) < 200:
        raise SystemExit("GET / returned too little content")

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (empty)")

    print("seeding demo data...")
    _seed.main()

    r = client.get("/api/stocks/search", params={"q": "デモ"})
    _expect(r, 200, "GET /api/stocks/search")
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        raise SystemExit("search for デモ returned no rows after seed")
    if not str(rows[0].get("code", "")).startswith("0"):
        raise SystemExit(f"demo code should start with 0, got {rows[0]!r}")

    r = client.get("/api/screener")
    _expect(r, 200, "GET /api/screener (seeded)")
    body = r.json()
    results = body.get("results") if isinstance(body, dict) else body
    if not results:
        raise SystemExit("screener empty after seed")

    code = rows[0]["code"]
    r = client.get(f"/api/chart/{code}")
    _expect(r, 200, f"GET /api/chart/{code}")
    candles = r.json().get("candles") or []
    if not candles:
        raise SystemExit("chart had no candles")

    print(
        f"ok: {len(rows)} search hits, "
        f"screener {len(results)}, chart {len(candles)} candles"
    )


if __name__ == "__main__":
    main()

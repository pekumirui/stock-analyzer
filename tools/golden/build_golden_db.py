#!/usr/bin/env python3
"""DB のゴールデンデータ（tests/golden/db/*.csv）を実データから作る。読み込みは jobs/golden_db.py（./run.sh golden）。

  python tools/golden/build_golden_db.py --db stock.db --api https://kabukpi.com

- 株価・決算・決算予定: --db（backfill 済みの stock.db）から tests/golden/db/codes.txt の銘柄だけ。
  銘柄マスタと TOPIX は全件。indicators は持たない（読み込み時に計算し直す）
- 月次: --api の公開 GET から全銘柄・全年
- 為替: jobs/fx_rates.py を一時 DB に向けて、株価と同じ期間
- 素材: jobs/supply/material_stats.py を一時 DB に向けて、--material-years の年（ESTAT_APP_ID が要る）
- XBRL は持たない（R2 のマウントが要る）

--skip で表の組を飛ばすと、その CSV は前のまま残す。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402

from app.models import Base  # noqa: E402

OUT = ROOT / "tests" / "golden" / "db"
CODES = OUT / "codes.txt"
BY_CODE = ("daily_prices", "statements", "earnings_events", "earnings_date_pubs")
MARKET = ("stocks", "topix", "earnings_date_fetches", *BY_CODE)
MONTHLY = ("monthly_issuers", "monthly_tables", "monthly_elements", "monthly_values")
FX = ("fx_rates",)
MATERIAL = ("material_monthly", "material_trade_country")


def load_codes(path: Path = CODES) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.split()[0])
    return out


def _columns(table: str) -> list[str]:
    return [c.name for c in Base.metadata.tables[table].columns]


def write_csv(out: Path, table: str, rows: list[dict] | list[tuple]) -> int:
    cols = _columns(table)
    out.mkdir(parents=True, exist_ok=True)
    with (out / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            vals = [r.get(c) for c in cols] if isinstance(r, dict) else list(r)
            w.writerow(["" if v is None else v for v in vals])
    return len(rows)


def count_rows(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def _dump(con: sqlite3.Connection, out: Path, table: str, where: str = "", args: tuple = ()) -> int:
    cols = _columns(table)
    have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    sel = ", ".join(c if c in have else "NULL" for c in cols)
    order = ", ".join(c.name for c in Base.metadata.tables[table].primary_key.columns)
    rows = list(con.execute(f"SELECT {sel} FROM {table} {where} ORDER BY {order}", args))  # nosec B608 (表と列はモデルから)
    return write_csv(out, table, rows)


def build_market(db: Path, out: Path, codes: list[str]) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        missing = [c for c in codes if con.execute("SELECT 1 FROM stocks WHERE code=?", (c,)).fetchone() is None]
        if missing:
            raise SystemExit(f"codes.txt にあって stocks に無い銘柄: {missing}")
        ph = ",".join("?" * len(codes))
        counts = {}
        for t in ("stocks", "topix", "earnings_date_fetches"):
            counts[t] = _dump(con, out, t)
        for t in BY_CODE:
            counts[t] = _dump(con, out, t, f"WHERE code IN ({ph})", tuple(codes))
        lo, hi = con.execute("SELECT MIN(date), MAX(date) FROM daily_prices").fetchone()
        counts["_price_range"] = f"{lo}..{hi}"
        return counts
    finally:
        con.close()


def _get_json(url: str) -> object:
    if not url.startswith("https://"):
        raise ValueError(f"https only: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "stock-analyzer build_golden_db"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (https に限る)
        return json.load(resp)


def build_monthly(origin: str, out: Path) -> dict[str, int]:
    base = origin.rstrip("/") + "/api/v1/tables"
    listing = _get_json(base)
    jobs = [(x["code"], int(y)) for x in listing for y in x["years"]]
    with ThreadPoolExecutor(max_workers=4) as ex:
        tables = dict(zip(jobs, ex.map(lambda j: _get_json(f"{base}/{j[0]}/{j[1]}"), jobs)))
    issuers = [{"code": x["code"], "industry": x.get("industry") or "その他",
                "last_ingested_at": x.get("last_ingested_at")} for x in listing]
    t_rows, e_rows, v_rows = [], [], []
    for (code, year), t in sorted(tables.items()):
        t_rows.append({"code": code, "year": year})
        for i, e in enumerate(t["elements"], 1):
            e_rows.append({"code": code, "year": year, "element": e["element"],
                           "sort_order": i, "unit": e["unit"]})
            for m, v in sorted(e["values"].items(), key=lambda kv: int(kv[0])):
                if v is not None:
                    v_rows.append({"code": code, "year": year, "element": e["element"],
                                   "month": int(m), "value": v})
    return {
        "monthly_issuers": write_csv(out, "monthly_issuers", sorted(issuers, key=lambda r: r["code"])),
        "monthly_tables": write_csv(out, "monthly_tables", t_rows),
        "monthly_elements": write_csv(out, "monthly_elements", e_rows),
        "monthly_values": write_csv(out, "monthly_values", v_rows),
    }


def _run_job(db: Path, argv: list[str]) -> None:
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
    subprocess.run([sys.executable, *argv], cwd=ROOT, env=env, check=True)  # nosec B603 (固定のジョブ)


def build_job_tables(out: Path, argv: list[str], tables: tuple[str, ...]) -> dict[str, int]:
    tmp = Path(tempfile.mkdtemp(prefix="build-golden-db-")) / "stock.db"
    # ジョブは表がある DB を前提にする
    engine = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(engine)
    engine.dispose()
    _run_job(tmp, argv)
    con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    try:
        return {t: _dump(con, out, t) for t in tables}
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "stock.db", help="backfill 済みの stock.db")
    ap.add_argument("--api", default="https://kabukpi.com", help="月次の公開 GET の origin")
    ap.add_argument("--material-years", default=None, help="素材の e-Stat の年（2025 / 2024-2026）")
    ap.add_argument("--skip", action="append", default=[],
                    choices=("market", "monthly", "fx", "material"))
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    manifest_path = args.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    today = date.today().isoformat()
    if "market" not in args.skip:
        got = build_market(args.db, args.out, load_codes())
        manifest["price_range"] = got.pop("_price_range")
        manifest["market_built"] = today
    if "monthly" not in args.skip:
        build_monthly(args.api, args.out)
        manifest["monthly_built"] = today
    if "fx" not in args.skip:
        lo = (manifest.get("price_range") or "").split("..")[0] or today
        build_job_tables(args.out, ["jobs/fx_rates.py", "--from", lo, "--to", today], FX)
        manifest["fx_built"] = today
    if "material" not in args.skip:
        if not os.environ.get("ESTAT_APP_ID"):
            raise SystemExit("素材は ESTAT_APP_ID が要る（--skip material で飛ばせる）")
        job = ["jobs/supply/material_stats.py"]
        if args.material_years:
            job += ["--years", args.material_years]
        build_job_tables(args.out, job, MATERIAL)
        manifest["material_built"] = today
    # 件数は途中で止まった回があっても合うように、最後に CSV を数え直す
    manifest["counts"] = {p.stem: count_rows(p) for p in sorted(args.out.glob("*.csv"))}
    manifest["codes"] = len(load_codes())
    manifest["generated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for k, v in manifest["counts"].items():
        print(f"{k}\t{v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

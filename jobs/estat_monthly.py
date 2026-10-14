"""e-Stat の生産動態統計と貿易統計を material_monthly へ UPSERT。

使い方:
  python jobs/estat_monthly.py                  # 前年と今年の表（冪等）
  python jobs/estat_monthly.py --years 2021-2026
  python jobs/estat_monthly.py --only trade     # production / trade の片方だけ

ESTAT_APP_ID が無ければ何もせず終わる。対応表は config/estat_mapping.json、設計は docs/DESIGN_supply.md。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ESTAT_APP_ID  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.estat import (  # noqa: E402
    EstatClient, load_mapping, match_codes, month_of, now_utc, parse_production_label,
    product_name, production_dims, production_rows, production_table, published_through, table_title, trade_rows, trade_table,
)
from app.ingest import upsert  # noqa: E402
from app.models import MaterialMonthly  # noqa: E402
from app.supply_chain import load_supply_chain  # noqa: E402


def parse_years(raw: str | None, today: date) -> list[int]:
    if not raw:
        return [today.year - 1, today.year]
    lo, _, hi = raw.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def _filter(dim: str) -> str:
    """分類 id → getStatsData の絞り込み引数名（cat01 → cdCat01、time → cdTime）"""
    return "cd" + dim[:1].upper() + dim[1:]


def ingest_production(client: EstatClient, db, mapping: dict, years: list[int]) -> int:
    conf = mapping["production"]
    items, metrics = conf["items"], conf["metrics"]
    total = 0
    for year in years:
        tables = client.tables(conf["stats_code"], str(year))
        for report in sorted({it["report"] for it in items}):
            table = production_table(tables, report)
            if table is None:
                print(f"production {year} {report}: no table")
                continue
            sid = table["@id"]
            meta = client.meta(sid)
            products = {product_name(p) for it in items if it["report"] == report for p in it["products"]}
            item_dim, month_dim = production_dims(meta)
            codes = [code for code, name in meta[item_dim].items()
                     if (lab := parse_production_label(name)) and lab[0] in products and lab[1] in metrics]
            months = [code for code, name in meta[month_dim].items() if month_of(name)]
            values = client.values(sid, **{_filter(item_dim): ",".join(codes),
                                           _filter(month_dim): ",".join(months)})
            rows, warnings = production_rows(
                report=report, items=items, metrics=metrics, labels=meta[item_dim],
                months=meta[month_dim], item_dim=item_dim, month_dim=month_dim, values=values,
                stats_code=conf["stats_code"], stats_data_id=sid,
            )
            # 秘匿などで品目が欠けた月は出さない。素材ごとに件数だけ出す
            for material, n in sorted(Counter(w.split()[0] for w in warnings).items()):
                print(f"  warn {report} {material}: {n} month-metrics skipped (e.g. {next(w for w in warnings if w.startswith(material + ' '))})")
            now = now_utc()
            upsert(db, MaterialMonthly, [r.as_dict(now) for r in rows])
            total += len(rows)
            print(f"production {year} {report} {sid} rows={len(rows)} 「{table_title(table)}」")
    return total


def ingest_trade(client: EstatClient, db, mapping: dict, years: list[int]) -> int:
    conf = mapping["trade"]
    total = 0
    for year in years:
        tables = client.tables(conf["stats_code"], str(year))
        for flow in ("import", "export"):
            table = trade_table(tables, flow)
            if table is None:
                print(f"trade {year} {flow}: no table")
                continue
            sid, title = table["@id"], table_title(table)
            meta = client.meta(sid)
            items = []
            for it in conf["items"]:
                codes = match_codes(it.get(flow) or [], meta.get("cat01", {}))
                if it.get(flow) and not codes:
                    print(f"  warn trade {flow} {it['material']}: no code matches {it[flow]}")
                if codes:
                    items.append({"material": it["material"], "codes": codes})
            want = sorted({c for it in items for c in it["codes"]})
            kinds = [c for c, n in meta.get("cat02", {}).items() if re.fullmatch(r"単位[12]|\d{1,2}月_数量[12]", n)]
            values: list[dict] = []
            for i in range(0, len(want), 50):  # URL を長くしすぎない
                values += client.values(sid, cdCat01=",".join(want[i:i + 50]), cdCat02=",".join(kinds))
            rows = trade_rows(
                flow=flow, items=items, cat02=meta["cat02"], times=meta.get("time", {}), values=values,
                last_month=published_through(title), stats_code=conf["stats_code"], stats_data_id=sid,
            )
            now = now_utc()
            upsert(db, MaterialMonthly, [r.as_dict(now) for r in rows])
            total += len(rows)
            print(f"trade {year} {flow} {sid} rows={len(rows)} 「{title}」")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="2025 または 2021-2026。省略時は前年と今年")
    ap.add_argument("--only", choices=("production", "trade"))
    args = ap.parse_args()
    if not ESTAT_APP_ID:
        print("estat skip: ESTAT_APP_ID is not set")
        return 0
    years = parse_years(args.years, date.today())
    material_ids = [m["id"] for m in load_supply_chain()["materials"]]
    mapping = load_mapping(material_ids=material_ids)

    init_db()
    db = SessionLocal()
    client = EstatClient()
    try:
        n = 0
        if args.only in (None, "production"):
            n += ingest_production(client, db, mapping, years)
        if args.only in (None, "trade"):
            n += ingest_trade(client, db, mapping, years)
        print(f"estat done years={years[0]}-{years[-1]} rows={n}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

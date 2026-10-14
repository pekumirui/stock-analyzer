"""素材の月次量（material_monthly）を統計から UPSERT。対応表は config/material_stats.json。

使い方:
  python jobs/material_stats.py                    # 経産省 Excel（生産動態・石油統計の速報・確報）+ e-Stat（前年と今年）
  python jobs/material_stats.py --years 2021-2026  # e-Stat の年を指定（初回）
  python jobs/material_stats.py --source meti      # meti / estat の片方だけ
  python jobs/material_stats.py --source meti --refetch  # 取り込み済みの年報も落とし直す（正誤訂正のとき）
  python jobs/material_stats.py --only trade       # seidou（生産動態）/ sekiyu（石油統計）/ trade（貿易）のどれかだけ

e-Stat は ESTAT_APP_ID が無ければ飛ばす。経産省 Excel はキー不要。設計は DESIGN.md §3「素材の月次量」。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (  # noqa: E402
    ESTAT_APP_ID, METI_SEIDOU_URL, METI_SEKIYU_KAKUHO_URL, METI_SEKIYU_SOKUHO_URL,
)
from app.db import SessionLocal, init_db  # noqa: E402
from app.estat import (  # noqa: E402
    EstatClient, match_codes, month_of, parse_production_label, production_dims, production_table,
    published_through, seidou_records, sokuho_month, table_title, trade_rows, trade_table,
)
from app.material_stats import (  # noqa: E402
    REVISION_KAKUHO, REVISION_NENPO, REVISION_SOKUHO, SEKIYU_SOURCE, load_mapping, product_groups, prune_unmapped, sekiyu_rows,
    seidou_rows, summarize_warnings, write_rows,
)
from app.meti import (  # noqa: E402
    MetiChallenged, MetiClient, sekiyu_files, sekiyu_kakuho_cells, sekiyu_nenpo_cells, sekiyu_nenpo_inventory_cells,
    sekiyu_sokuho_cells, seidou_files,
)
from app.meti import seidou_records as meti_seidou_records  # noqa: E402
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


def _report_warnings(prefix: str, warnings: list[str]) -> None:
    for line in summarize_warnings(warnings):
        print(f"  warn {prefix} {line}")


def ingested_origins(db) -> set[str]:
    """取り込み済みのファイル名（生産動態の年間補正号）。変わらないので毎回は落とさない"""
    return {o for (o,) in db.query(MaterialMonthly.origin).distinct() if o}


def meti_targets(files: list, done: set[str], refetch: bool) -> list:
    """final のファイルは done（取り込み済み）なら飛ばす"""
    return [f for f in files if refetch or not f.final or f.name not in done]


def sekiyu_done(db, items: list[dict], files: list) -> set[str]:
    """石油統計の final のファイルのうち、対応表の項目がそのファイル名の行で全部そろっているもの。
    項目を足した後は、そのファイルを落とし直す。12 月の確報は年報に無い項目（製品の生産）だけ見る
    （ほかの項目は年報の行で上書きされ、確報のファイル名では残らない）"""
    have = {(o, m, metric) for o, m, metric in db.query(
        MaterialMonthly.origin, MaterialMonthly.material_id, MaterialMonthly.metric,
    ).filter(MaterialMonthly.source == SEKIYU_SOURCE).distinct()}
    done = set()
    for f in files:
        if not f.final:
            continue
        if f.revision == REVISION_NENPO:
            need = [it for it in items if it.get("nenpo")]
        else:
            need = [it for it in items if it.get("kakuho") and not it.get("nenpo")]
        if all((f.name, it["material"], it["metric"]) in have for it in need):
            done.add(f.name)
    return done


def _nenpo_cells(data: bytes) -> list:
    return sekiyu_nenpo_cells(data) + sekiyu_nenpo_inventory_cells(data)


def ingest_meti_seidou(db, mapping: dict, refetch: bool = False) -> int:
    conf = mapping["seidou"]
    reports = {it["report"] for it in conf["items"]}
    client = MetiClient()
    files = seidou_files(client.get(METI_SEIDOU_URL).decode("utf-8", "replace"))
    if not files:
        print("meti seidou: no Excel link on the list page")
        return 0
    total = 0
    for f in meti_targets(files, ingested_origins(db), refetch):
        try:
            records = meti_seidou_records(client.get(f.url), reports)
        except MetiChallenged as e:
            print(f"fail meti seidou {f.name}: {e}. stop and retry next run")
            break
        except Exception as e:  # noqa: BLE001  1 本壊れていても他の Excel は取り込む
            print(f"fail meti seidou {f.name}: {e}")
            continue
        rows, warnings = seidou_rows(records, items=conf["items"], metrics=conf["metrics"],
                                     revision=f.revision, origin=f.name)
        _report_warnings(f.name, warnings)
        n = write_rows(db, rows)
        total += n
        months = sorted({r.month for r in rows})
        span = f"{months[0]}..{months[-1]}" if months else "-"
        print(f"meti seidou {f.name} revision={f.revision} rows={len(rows)} written={n} months={span}")
    return total


def ingest_meti_sekiyu(db, mapping: dict, refetch: bool = False) -> int:
    items = mapping["sekiyu"]["items"]
    client = MetiClient()
    files = sekiyu_files(client.get(METI_SEKIYU_KAKUHO_URL).decode("utf-8", "replace"),
                         client.get(METI_SEKIYU_SOKUHO_URL).decode("utf-8", "replace"),
                         METI_SEKIYU_KAKUHO_URL, METI_SEKIYU_SOKUHO_URL)
    if not files:
        print("meti sekiyu: no Excel link on the list pages")
        return 0
    total = 0
    parsers = {REVISION_NENPO: ("nenpo", _nenpo_cells), REVISION_KAKUHO: ("kakuho", sekiyu_kakuho_cells),
               REVISION_SOKUHO: ("sokuho", sekiyu_sokuho_cells)}
    for f in meti_targets(files, sekiyu_done(db, items, files), refetch):
        kind, parse = parsers[f.revision]
        try:
            cells = parse(client.get(f.url))
        except MetiChallenged as e:
            print(f"fail meti sekiyu {f.name}: {e}. stop and retry next run")
            break
        except Exception as e:  # noqa: BLE001  1 本壊れていても他の Excel は取り込む
            print(f"fail meti sekiyu {f.name}: {e}")
            continue
        rows, warnings = sekiyu_rows(cells, items=items, kind=kind, revision=f.revision, origin=f.name)
        _report_warnings(f.name, warnings)
        n = write_rows(db, rows)
        total += n
        months = sorted({r.month for r in rows})
        span = f"{months[0]}..{months[-1]}" if months else "-"
        print(f"meti sekiyu {f.name} revision={f.revision} rows={len(rows)} written={n} months={span}")
    return total


def ingest_estat_seidou(client: EstatClient, db, mapping: dict, years: list[int]) -> int:
    conf = mapping["seidou"]
    items, metrics = conf["items"], conf["metrics"]
    total = 0
    for year in years:
        tables = client.tables(conf["estat_stats_code"], str(year))
        for report in sorted({it["report"] for it in items}):
            table = production_table(tables, report)
            if table is None:
                print(f"estat seidou {year} {report}: no table")
                continue
            sid = table["@id"]
            meta = client.meta(sid)
            products = {p for it in items if it["report"] == report for g in product_groups(it) for p in g}
            item_dim, month_dim = production_dims(meta)
            codes = [code for code, name in meta[item_dim].items()
                     if (lab := parse_production_label(name)) and lab[0] in products and lab[1] in metrics]
            months = [code for code, name in meta[month_dim].items() if month_of(name)]
            values = client.values(sid, **{_filter(item_dim): ",".join(codes),
                                           _filter(month_dim): ",".join(months)})
            rows, warnings = seidou_rows(seidou_records(report, meta, values), items=items,
                                         metrics=metrics, revision=REVISION_NENPO, origin=sid)
            _report_warnings(f"{year} {report}", warnings)
            n = write_rows(db, rows)
            total += n
            print(f"estat seidou {year} {report} {sid} rows={len(rows)} written={n} 「{table_title(table)}」")
    return total


def ingest_estat_trade(client: EstatClient, db, mapping: dict, years: list[int]) -> int:
    conf = mapping["trade"]
    total = 0
    for year in years:
        tables = client.tables(conf["estat_stats_code"], str(year))
        for flow in ("import", "export"):
            table = trade_table(tables, flow)
            if table is None:
                print(f"estat trade {year} {flow}: no table")
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
                last_month=published_through(title), stats_data_id=sid, sokuho=sokuho_month(title),
            )
            n = write_rows(db, rows)
            total += n
            print(f"estat trade {year} {flow} {sid} rows={len(rows)} written={n} 「{title}」")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="e-Stat の年。2025 または 2021-2026。省略時は前年と今年")
    ap.add_argument("--source", choices=("meti", "estat"))
    ap.add_argument("--only", choices=("seidou", "sekiyu", "trade"))
    ap.add_argument("--refetch", action="store_true", help="取り込み済みの年報・年間補正号・過去年の 12 月の確報も落とし直す")
    args = ap.parse_args()
    years = parse_years(args.years, date.today())
    material_ids = [m["id"] for m in load_supply_chain()["materials"]]
    mapping = load_mapping(material_ids=material_ids)

    init_db()
    db = SessionLocal()
    try:
        pruned = prune_unmapped(db, mapping)
        if pruned:
            print(f"material_stats pruned={pruned} (no longer in config/material_stats.json)")
        n = 0
        if args.source in (None, "estat"):
            if not ESTAT_APP_ID:
                print("estat skip: ESTAT_APP_ID is not set")
            else:
                client = EstatClient()
                if args.only in (None, "seidou"):
                    n += ingest_estat_seidou(client, db, mapping, years)
                if args.only in (None, "trade"):
                    n += ingest_estat_trade(client, db, mapping, years)
        # Excel は e-Stat の後。同じ revision なら後から書いたほうが残る
        if args.source in (None, "meti") and args.only in (None, "seidou"):
            n += ingest_meti_seidou(db, mapping, args.refetch)
        if args.source in (None, "meti") and args.only in (None, "sekiyu"):
            n += ingest_meti_sekiyu(db, mapping, args.refetch)
        print(f"material_stats done written={n}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""素材の月次量（material_monthly）を統計から UPSERT。対応表は config/supply/material_stats.json。

使い方:
  python jobs/supply/material_stats.py                    # 経産省 Excel（生産動態・石油統計の速報・確報）+ e-Stat（前年と今年）
  python jobs/supply/material_stats.py --years 2021-2026  # e-Stat の年を指定（初回）
  python jobs/supply/material_stats.py --source meti      # meti / estat の片方だけ
  python jobs/supply/material_stats.py --source meti --refetch  # 取り込み済みの年報も経産省から落とし直す（正誤訂正のとき）
  python jobs/supply/material_stats.py --source meti --reread   # 取り込み済みの年報も data/stats/meti の控えから読み直す（対応表を変えたとき）
  python jobs/supply/material_stats.py --only trade       # seidou（生産動態）/ sekiyu（石油統計）/ trade（貿易）のどれかだけ

経産省の Excel は data/stats/meti/{seidou,sekiyu}/ に控えを残し、変わらないファイルは控えから読む。

e-Stat は ESTAT_APP_ID が無ければ飛ばす。経産省 Excel はキー不要。設計は DESIGN.md §3「素材の月次量」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs._r2common import MountDisconnected, check_r2_mount  # noqa: E402
from jobs.share_paths import share_path  # noqa: E402

from app.config import (  # noqa: E402
    ESTAT_APP_ID, METI_FAIL_PAGE_DAYS, METI_SEIDOU_URL, METI_SEKIYU_KAKUHO_URL, METI_SEKIYU_SOKUHO_URL,
)
from app.db import SessionLocal  # noqa: E402
from app.supply.estat import (  # noqa: E402
    EstatClient, match_codes, month_of, parse_production_label, production_dims, production_table,
    published_through, seidou_records, sokuho_month, table_title, trade_country_rows, trade_rows, trade_table,
)
from app.supply.material_stats import (  # noqa: E402
    REVISION_KAKUHO, REVISION_NENPO, REVISION_SOKUHO, SEKIYU_SOURCE, load_mapping, product_groups, prune_unmapped, sekiyu_rows,
    seidou_rows, summarize_warnings, write_country_rows, write_rows,
)
from app.supply.meti import (  # noqa: E402
    MetiChallenged, MetiClient, MetiError, sekiyu_files, sekiyu_kakuho_cells, sekiyu_nenpo_cells,
    sekiyu_nenpo_inventory_cells, sekiyu_sokuho_cells, seidou_december_kakuho, seidou_files,
)
from app.supply.meti import seidou_records as meti_seidou_records  # noqa: E402
from app.models import MaterialMonthly  # noqa: E402
from app.supply.chain import load_supply_chain  # noqa: E402


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


class MetiArchive:
    """経産省 Excel の控え（data/stats/meti/<kind>/<ファイル名>）。検証と再投入に使い、変わらないファイルは落とし直さない。

    中身が同じなら書かない。同じ名前で中身が変わったら（正誤訂正）<名前>_YYYYMMDD で別に残し、前のものは消さない。
    控えのディレクトリが使えなければ控えなしで取り込む（取り込みは止めない）。
    """

    def __init__(self, root: Path | None) -> None:
        self.root = root

    @classmethod
    def open(cls) -> MetiArchive:
        root = share_path("stats") / "meti"
        try:
            check_r2_mount(root, require_existing=False, retries=0)
            root.mkdir(parents=True, exist_ok=True)
        except (MountDisconnected, OSError) as e:
            print(f"warn meti archive off: {e}")
            return cls(None)
        return cls(root)

    def _versions(self, kind: str, name: str) -> list[Path]:
        """控えの版。元の名前が先、日付つきは古い順"""
        if self.root is None:
            return []
        stem, suffix = name.rsplit(".", 1)
        plain = self.root / kind / name
        dated = sorted(p for p in (self.root / kind).glob(f"{stem}_*.{suffix}")
                       if re.fullmatch(rf"{re.escape(stem)}_\d{{8}}\.{re.escape(suffix)}", p.name))
        return ([plain] if plain.exists() else []) + dated

    def missing(self, kind: str, names: set[str]) -> set[str]:
        """控えの無いファイル名。R2 が使えなければ空（控えのために落とし直さない）"""
        if self.root is None:
            return set()
        try:
            return {n for n in names if not self._versions(kind, n)}
        except OSError:
            return set()

    def get(self, client: MetiClient, f, kind: str, refetch: bool) -> tuple[bytes, str]:
        """(中身, 取得元 meti / r2)。final は控えがあれば最新の版を読む。--refetch は必ず落とす"""
        if f.final and not refetch:
            try:
                versions = self._versions(kind, f.name)
                if versions:
                    return versions[-1].read_bytes(), "r2"
            except OSError as e:
                print(f"warn meti archive read {kind}/{f.name}: {e}")
        data = client.get(f.url)
        self.save(kind, f.name, data)
        return data, "meti"

    def save(self, kind: str, name: str, data: bytes) -> None:
        if self.root is None:
            return
        try:
            versions = self._versions(kind, name)
            digest = hashlib.sha256(data).hexdigest()
            if any(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p in versions):
                return
            stem, suffix = name.rsplit(".", 1)
            path = self.root / kind / (f"{stem}_{date.today():%Y%m%d}.{suffix}" if versions else name)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            print(f"meti archive saved {kind}/{path.name}")
        except OSError as e:
            print(f"warn meti archive save {kind}/{name}: {e}")


class MetiLedger:
    """経産省 Excel の取得の台帳（{kind: {"ok": 最後に全部取れた日, "fail_since": 取れなくなった日}}）。

    行単位の `fail meti …` は page しない。page はジョブ最終行の failed=K。台帳は続けて取れない日数の診断用で、
    METI_FAIL_PAGE_DAYS 日たっても取れなければ `not fetched for N days` を出す（failed= は付けない）。
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.data: dict[str, dict[str, str]] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                print(f"warn meti ledger {path}: {e}")

    def record(self, kind: str, fails: list[str], today: date, page_days: int = METI_FAIL_PAGE_DAYS) -> str | None:
        """今回の結果を記録し、page する行（無ければ None）を返す"""
        st = self.data.setdefault(kind, {})
        if not fails:
            st["ok"] = today.isoformat()
            st.pop("fail_since", None)
            return None
        st.setdefault("fail_since", today.isoformat())
        days = (today - date.fromisoformat(st.get("ok") or st["fail_since"])).days
        if days < page_days:
            return None
        return (f"material_stats meti {kind} not fetched for {days} days (last ok {st.get('ok', '-')}) "
                f"n={len(fails)} {','.join(fails[:5])}")

    def save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".part")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            print(f"warn meti ledger save {self.path}: {e}")


def _nenpo_cells(data: bytes) -> list:
    return sekiyu_nenpo_cells(data) + sekiyu_nenpo_inventory_cells(data)


def ingest_meti_seidou(db, mapping: dict, refetch: bool = False, december: list[int] | None = None,
                       archive: MetiArchive | None = None, reread: bool = False,
                       fails: list[str] | None = None) -> int:
    """fails に取れなかったファイル名（一覧ページは list）を足す"""
    fails = [] if fails is None else fails
    conf = mapping["seidou"]
    reports = {it["report"] for it in conf["items"]}
    client = MetiClient()
    archive = archive or MetiArchive(None)
    try:
        files = seidou_files(client.get(METI_SEIDOU_URL).decode("utf-8", "replace"))
    except MetiChallenged as e:
        print(f"fail meti seidou list: {e}. stop and retry next run")
        fails.append("list")
        return 0
    files[:0] = [seidou_december_kakuho(y) for y in december or []]  # 確報なので年間補正号より前に書く
    if not files:
        print("meti seidou: no Excel link on the list page")
        fails.append("list")
        return 0
    total = 0
    done = ingested_origins(db)
    done -= archive.missing("seidou", done & {f.name for f in files})
    for f in meti_targets(files, done, refetch or reread):
        try:
            data, got = archive.get(client, f, "seidou", refetch)
            records = meti_seidou_records(data, reports)
        except MetiChallenged as e:
            print(f"fail meti seidou {f.name}: {e}. stop and retry next run")
            fails.append(f.name)
            break
        except (MetiError, MountDisconnected, OSError) as e:
            print(f"fail meti seidou {f.name}: {e}")
            fails.append(f.name)
            continue
        rows, warnings = seidou_rows(records, items=conf["items"], metrics=conf["metrics"],
                                     revision=f.revision, origin=f.name)
        _report_warnings(f.name, warnings)
        n = write_rows(db, rows)
        total += n
        months = sorted({r.month for r in rows})
        span = f"{months[0]}..{months[-1]}" if months else "-"
        print(f"meti seidou {f.name} revision={f.revision} rows={len(rows)} written={n} months={span} from={got}")
    return total


def ingest_meti_sekiyu(db, mapping: dict, refetch: bool = False, archive: MetiArchive | None = None,
                       reread: bool = False, fails: list[str] | None = None) -> int:
    """fails に取れなかったファイル名（一覧ページは list）を足す"""
    fails = [] if fails is None else fails
    items = mapping["sekiyu"]["items"]
    client = MetiClient()
    archive = archive or MetiArchive(None)
    try:
        files = sekiyu_files(client.get(METI_SEKIYU_KAKUHO_URL).decode("utf-8", "replace"),
                             client.get(METI_SEKIYU_SOKUHO_URL).decode("utf-8", "replace"),
                             METI_SEKIYU_KAKUHO_URL, METI_SEKIYU_SOKUHO_URL)
    except MetiChallenged as e:
        print(f"fail meti sekiyu list: {e}. stop and retry next run")
        fails.append("list")
        return 0
    if not files:
        print("meti sekiyu: no Excel link on the list pages")
        fails.append("list")
        return 0
    total = 0
    parsers = {REVISION_NENPO: ("nenpo", _nenpo_cells), REVISION_KAKUHO: ("kakuho", sekiyu_kakuho_cells),
               REVISION_SOKUHO: ("sokuho", sekiyu_sokuho_cells)}
    done = sekiyu_done(db, items, files)
    done -= archive.missing("sekiyu", done)
    for f in meti_targets(files, done, refetch or reread):
        kind, parse = parsers[f.revision]
        try:
            data, got = archive.get(client, f, "sekiyu", refetch)
            cells = parse(data)
        except MetiChallenged as e:
            print(f"fail meti sekiyu {f.name}: {e}. stop and retry next run")
            fails.append(f.name)
            break
        except (MetiError, MountDisconnected, OSError) as e:
            print(f"fail meti sekiyu {f.name}: {e}")
            fails.append(f.name)
            continue
        rows, warnings = sekiyu_rows(cells, items=items, kind=kind, revision=f.revision, origin=f.name)
        _report_warnings(f.name, warnings)
        n = write_rows(db, rows)
        total += n
        months = sorted({r.month for r in rows})
        span = f"{months[0]}..{months[-1]}" if months else "-"
        print(f"meti sekiyu {f.name} revision={f.revision} rows={len(rows)} written={n} months={span} from={got}")
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
            items, country_only = [], []
            for it in conf["items"] + (conf.get("country_items") or []):
                codes = match_codes(it.get(flow) or [], meta.get("cat01", {}))
                if it.get(flow) and not codes:
                    print(f"  warn trade {flow} {it['material']}: no code matches {it[flow]}")
                if codes:
                    (items if it in conf["items"] else country_only).append({"material": it["material"], "codes": codes})
            want = sorted({c for it in items + country_only for c in it["codes"]})
            kinds = [c for c, n in meta.get("cat02", {}).items() if re.fullmatch(r"単位[12]|\d{1,2}月_数量[12]", n)]
            values: list[dict] = []
            for i in range(0, len(want), 50):
                values += client.values(sid, cdCat01=",".join(want[i:i + 50]), cdCat02=",".join(kinds))
            rows = trade_rows(
                flow=flow, items=items, cat02=meta["cat02"], times=meta.get("time", {}), values=values,
                last_month=published_through(title), stats_data_id=sid, sokuho=sokuho_month(title),
            )
            n = write_rows(db, rows)
            crows = trade_country_rows(
                flow=flow, items=items + country_only, cat02=meta["cat02"], times=meta.get("time", {}),
                areas=meta.get("area", {}), values=values, last_month=published_through(title),
                stats_data_id=sid, sokuho=sokuho_month(title),
            )
            nc = write_country_rows(db, crows)
            total += n + nc
            print(f"estat trade {year} {flow} {sid} rows={len(rows)} written={n} "
                  f"country_rows={len(crows)} written={nc} 「{title}」")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", help="e-Stat の年。2025 または 2021-2026。省略時は前年と今年")
    ap.add_argument("--source", choices=("meti", "estat"))
    ap.add_argument("--only", choices=("seidou", "sekiyu", "trade"))
    ap.add_argument("--refetch", action="store_true", help="取り込み済みの年報・年間補正号・過去年の 12 月の確報も経産省から落とし直す")
    ap.add_argument("--reread", action="store_true", help="取り込み済みの年報なども読み直す。控えがあれば経産省には行かない")
    ap.add_argument("--ledger", help="経産省 Excel の取得の台帳（JSON）。続けて取れないとき page する。cron は /var/log/app/material_stats_ledger.json")
    ap.add_argument("--december", help="生産動態の過去年の 12 月の確報も読む（一覧ページに無い年）。2024 または 2023-2024。手で一度だけ")
    args = ap.parse_args()
    years = parse_years(args.years, date.today())
    material_ids = [m["id"] for m in load_supply_chain()["materials"]]
    mapping = load_mapping(material_ids=material_ids)

    db = SessionLocal()
    try:
        pruned = prune_unmapped(db, mapping)
        if pruned:
            print(f"material_stats pruned={pruned} (no longer in config/supply/material_stats.json)")
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
        archive = MetiArchive.open() if args.source in (None, "meti") and args.only != "trade" else None
        ledger = MetiLedger(Path(args.ledger) if args.ledger else None)
        fails: dict[str, list[str]] = {}
        if args.source in (None, "meti") and args.only in (None, "seidou"):
            fails["seidou"] = []
            n += ingest_meti_seidou(db, mapping, args.refetch,
                                     parse_years(args.december, date.today()) if args.december else None, archive,
                                     args.reread, fails["seidou"])
        if args.source in (None, "meti") and args.only in (None, "sekiyu"):
            fails["sekiyu"] = []
            n += ingest_meti_sekiyu(db, mapping, args.refetch, archive, args.reread, fails["sekiyu"])
        if args.ledger:
            for kind, failed in fails.items():
                line = ledger.record(kind, failed, date.today())
                if line:
                    print(line)
            ledger.save()
        k = sum(len(v) for v in fails.values())
        print(f"material_stats done written={n} failed={k}")
        return 1 if k else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

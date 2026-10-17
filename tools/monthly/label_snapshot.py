#!/usr/bin/env python3
"""月次の行名スナップショット。正規化を変えたとき「どの行が変わったか」を本番の全行で出す。

入力は 銘柄 / 年 / 並び / 経路 / unit / 送られてきたままの行名。出力は今のコードで通した
保存名・label・ツリー。出力は一時 DB を組み立てて API と同じ table_year_payload で作る。

  python tools/monthly/label_snapshot.py check              # 差分を出す。あれば終了 1
  python tools/monthly/label_snapshot.py update             # 出力列を今のコードで書き直す
  python tools/monthly/label_snapshot.py collect --api https://kabukpi.com
  python tools/monthly/label_snapshot.py collect --db stock.db

collect は入力を取り直す。--api は公開 GET の保存名（via=legacy）。--db は
monthly_element_sources（無ければ monthly_elements を legacy）。どちらも
labels_known.tsv の行を足し、保存名と今の正規化がずれている行を表示する。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import tempfile
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api.monthly.tables import table_year_payload  # noqa: E402
from app.models import Base, MonthlyElement, MonthlyTable, MonthlyValue  # noqa: E402
from app.monthly.ingest import (  # noqa: E402
    MonthlyIngestError, apply_issuer_aliases, canonicalize_element_label, coerce_monthly_storage,
    reject_prior_period_element,
)

GOLDEN = ROOT / "tests" / "monthly" / "golden" / "labels.tsv"
KNOWN = ROOT / "tests" / "monthly" / "golden" / "labels_known.tsv"
INPUT_COLS = ("code", "year", "order", "via", "unit", "source_label")
OUTPUT_COLS = (
    "element", "stored_unit", "display", "metric_kind", "segment", "scope", "metric",
    "tree_path", "tree_metric",
)
COLS = INPUT_COLS + OUTPUT_COLS
SHOW = 200


def resolve(code: str, year: int, label: str, unit: str, via: str) -> str:
    """元の行名 → 保存名。PUT doc は名前をそのまま使う。それ以外は POST elements と同じ通し方。

    ingest は as_of の銘柄エイリアスのあとに列の暦年のエイリアスを掛けるが、as_of は残して
    いないので暦年だけで通す（エイリアスは暦年で切り替わる）。
    """
    if via == "doc":
        return label
    try:
        reject_prior_period_element(label)
    except MonthlyIngestError as exc:
        return f"!{exc.code}"
    s = canonicalize_element_label(
        apply_issuer_aliases(code, canonicalize_element_label(label, unit), year=year), unit,
    )
    if not s or len(s) > 50:
        return "!INVALID_PARAM"
    return s


def compute(inputs: list[dict]) -> list[dict]:
    """入力の各行に出力列を付けて返す。並びは入力のまま。"""
    tmp = tempfile.mkdtemp(prefix="label-snapshot-")
    engine = create_engine(f"sqlite:///{tmp}/snapshot.db")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    rows = [dict(r) for r in inputs]
    try:
        tables: dict[tuple[str, int], dict[str, tuple[int, str]]] = defaultdict(dict)
        for r in rows:
            code, year = r["code"], int(r["year"])
            r["element"] = resolve(code, year, r["source_label"], r["unit"], r["via"])
            r["stored_unit"] = coerce_monthly_storage(r["unit"], None)[0]
            if r["element"].startswith("!"):
                continue
            # 同じ保存名に寄る行は 1 行。並びは一番前、unit は最初に来た行
            seen = tables[(code, year)]
            order = int(r["order"])
            if r["element"] not in seen:
                seen[r["element"]] = (order, r["stored_unit"])
            elif order < seen[r["element"]][0]:
                seen[r["element"]] = (order, seen[r["element"]][1])
        for (code, year), elements in tables.items():
            db.add(MonthlyTable(code=code, year=year))
            for element, (order, unit) in elements.items():
                db.add(MonthlyElement(code=code, year=year, element=element,
                                      sort_order=order, unit=unit))
                db.add(MonthlyValue(code=code, year=year, element=element, month=1, value=100.0))
        db.commit()
        out: dict[tuple[str, int], dict[str, dict]] = {}
        for code, year in tables:
            payload = table_year_payload(db, code, year)
            out[(code, year)] = {e["element"]: e for e in payload["elements"]}
    finally:
        db.close()
        engine.dispose()
    for r in rows:
        got = out.get((r["code"], int(r["year"])), {}).get(r["element"])
        label = (got or {}).get("label") or {}
        tree = (got or {}).get("tree") or {}
        r["display"] = label.get("display", "")
        for k in ("metric_kind", "segment", "scope", "metric"):
            r[k] = label.get(k) or ""
        r["tree_path"] = " > ".join(tree.get("path") or [])
        r["tree_metric"] = tree.get("metric") or ""
    return rows


def _sort_key(r: dict) -> tuple:
    return (r["code"], int(r["year"]), int(r["order"]), r["source_label"], r["unit"])


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, delimiter="\t", lineterminator="\n",
                           extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=_sort_key):
            w.writerow({k: r.get(k, "") for k in COLS})


def diff(golden: list[dict], current: list[dict]) -> list[tuple[dict, list[tuple[str, str, str]]]]:
    """出力列が変わった行と、(列, 前, 後)。"""
    changed = []
    for old, new in zip(golden, current):
        cols = [(c, old.get(c, ""), new.get(c, "")) for c in OUTPUT_COLS
                if old.get(c, "") != new.get(c, "")]
        if cols:
            changed.append((old, cols))
    return changed


def format_diff(changed: list[tuple[dict, list[tuple[str, str, str]]]], show_all: bool = False) -> str:
    by_col: dict[str, int] = defaultdict(int)
    for _r, cols in changed:
        for c, _a, _b in cols:
            by_col[c] += 1
    lines = [f"出力が変わった行: {len(changed)} 行（列ごと: "
             + ", ".join(f"{c}={n}" for c, n in sorted(by_col.items())) + "）"]
    for r, cols in changed if show_all else changed[:SHOW]:
        head = f"  {r['code']} {r['year']} [{r['unit']}] {r['source_label']!r}"
        lines.append(head)
        for c, a, b in cols:
            lines.append(f"      {c}: {a!r} → {b!r}")
    if not show_all and len(changed) > SHOW:
        lines.append(f"  … ほか {len(changed) - SHOW} 行（--all で全部）")
    return "\n".join(lines)


def check(golden_path: Path = GOLDEN) -> list[tuple[dict, list[tuple[str, str, str]]]]:
    golden = sorted(read_tsv(golden_path), key=_sort_key)
    return diff(golden, compute([{k: r[k] for k in INPUT_COLS} for r in golden]))


def _known_inputs(existing: list[dict]) -> list[dict]:
    """labels_known.tsv: 本番に残っていない元の行名（手で分かっているもの）。並びは表の末尾。"""
    if not KNOWN.is_file():
        return []
    last: dict[tuple[str, int], int] = defaultdict(int)
    for r in existing:
        key = (r["code"], int(r["year"]))
        last[key] = max(last[key], int(r["order"]))
    out = []
    for r in read_tsv(KNOWN):
        key = (r["code"], int(r["year"]))
        last[key] += 1
        out.append({"code": r["code"], "year": r["year"], "order": str(last[key]),
                    "via": "known", "unit": r["unit"], "source_label": r["source_label"]})
    return out


def _get_json(url: str) -> object:
    if not url.startswith("https://"):
        raise ValueError(f"https only: {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "stock-analyzer label_snapshot"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 (https に限る)
        return json.load(resp)


def collect_api(origin: str) -> tuple[list[dict], dict[tuple, str]]:
    if not origin.startswith("https://"):
        raise SystemExit("--api は https:// の URL です")
    base = origin.rstrip("/") + "/api/v1/tables"
    listing = _get_json(base)
    jobs = [(x["code"], int(y)) for x in listing for y in x["years"]]

    def _one(job: tuple[str, int]) -> tuple[tuple[str, int], dict]:
        return job, _get_json(f"{base}/{job[0]}/{job[1]}")

    with ThreadPoolExecutor(max_workers=4) as ex:
        tables = dict(ex.map(_one, jobs))
    rows, stored = [], {}
    for (code, year), t in sorted(tables.items()):
        for i, e in enumerate(t["elements"], 1):
            r = {"code": code, "year": str(year), "order": str(i), "via": "legacy",
                 "unit": e["unit"], "source_label": e["element"]}
            rows.append(r)
            stored[(code, str(year), e["element"], e["unit"])] = e["element"]
    return rows, stored


def collect_db(path: Path) -> tuple[list[dict], dict[tuple, str]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        has_sources = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='monthly_element_sources'",
        ).fetchone() is not None
        if has_sources:
            q = ("SELECT s.code, s.year, e.sort_order, s.via, s.unit, s.source_label, s.element "
                 "FROM monthly_element_sources s JOIN monthly_elements e "
                 "ON e.code = s.code AND e.year = s.year AND e.element = s.element")
        else:
            q = ("SELECT code, year, sort_order, 'legacy', COALESCE(unit, 'number'), element, element "
                 "FROM monthly_elements")
        rows, stored = [], {}
        for code, year, order, via, unit, label, element in con.execute(q):
            rows.append({"code": code, "year": str(year), "order": str(order), "via": via,
                         "unit": unit, "source_label": label})
            stored[(code, str(year), label, unit)] = element
        return rows, stored
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--golden", type=Path, default=GOLDEN)
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("update")
    p.add_argument("--golden", type=Path, default=GOLDEN)
    p = sub.add_parser("collect")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--api", help="公開サイトの origin（https://kabukpi.com）")
    src.add_argument("--db", type=Path, help="stock.db（読み取り専用で開く）")
    p.add_argument("--golden", type=Path, default=GOLDEN)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        changed = check(args.golden)
        if not changed:
            print(f"ok: 行名スナップショット {len(read_tsv(args.golden))} 行に差分なし")
            return 0
        print(format_diff(changed, args.all))
        print("\n意図した変更なら: python tools/monthly/label_snapshot.py update して差分を PR に載せる")
        return 1
    if args.cmd == "update":
        golden = read_tsv(args.golden)
        rows = compute([{k: r[k] for k in INPUT_COLS} for r in golden])
        changed = diff(sorted(golden, key=_sort_key), sorted(rows, key=_sort_key))
        write_tsv(args.golden, rows)
        print(f"書き直し: {len(rows)} 行（出力が変わった行 {len(changed)}）")
        return 0

    inputs, stored = collect_api(args.api) if args.api else collect_db(args.db)
    inputs += _known_inputs(inputs)
    rows = compute(inputs)
    write_tsv(args.golden, rows)
    drift = [r for r in rows if r["via"] != "known"
             and stored.get((r["code"], r["year"], r["source_label"], r["unit"])) not in (None, r["element"])]
    print(f"collect: {len(rows)} 行を {args.golden.relative_to(ROOT)} に書いた")
    print(f"保存名と今の正規化がずれている行: {len(drift)}")
    for r in drift[:SHOW]:
        key = (r["code"], r["year"], r["source_label"], r["unit"])
        print(f"  {r['code']} {r['year']} {stored[key]!r} → {r['element']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

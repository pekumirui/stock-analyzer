"""素材の月次量（material_monthly）の共通部分。統計ごとの取得は app/estat.py（e-Stat API）と app/meti.py（経産省 Excel）。

- 対応表 config/material_stats.json: 統計の品名・統計品目番号 → 素材 id
- 生産動態統計は e-Stat（年報）と経産省 Excel（速報・確報）の両方から同じ source に書く。
  revision（速報 1 < 確報 2 < 年報 3）が下がる上書きはしない
設計は DESIGN.md §3「素材の月次量」。
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from .config import MATERIAL_STATS_PATH
from .ingest import upsert
from .models import MaterialMonthly

METRICS = ("production", "shipment", "inventory", "import", "export", "processing", "domestic_sales")
REVISION_SOKUHO, REVISION_KAKUHO, REVISION_NENPO = 1, 2, 3
SEIDOU_SOURCE = "seidou:{report}"
TRADE_SOURCE = "trade"
SEKIYU_SOURCE = "sekiyu"
_CODE_RE = re.compile(r"^[0-9]{4,9}$")
# 生産動態の単位。同じ素材の品目で 本 と 千本 が混ざる（タイヤ）
_SEIDOU_UNITS = {"千本": ("本", 1000.0)}
_ZERO = {"-", "(-)", "*-", "* -", "―", "－", "‐"}  # 皆無または該当なし


class MappingError(ValueError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def product_name(name: Any) -> str:
    return unicodedata.normalize("NFKC", str(name)).strip()


def parse_num(raw: Any) -> float | None:
    """数値。"-" 系は 0。秘匿 "X"、不詳 "…"、得られない "***"、空欄は None"""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    s = unicodedata.normalize("NFKC", str(raw if raw is not None else "")).strip()
    if s in _ZERO:
        return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


# ---------- 対応表 ----------

def product_groups(item: dict) -> list[list[str]]:
    """`products` は品名の並び、または候補の並び（年や速報で品目の分け方が違うとき）。正規化した候補の並びで返す"""
    products = item.get("products") or []
    groups = products if products and all(isinstance(p, list) for p in products) else [products]
    return [[product_name(p) for p in g] for g in groups]


def load_mapping(path: Path = MATERIAL_STATS_PATH, material_ids: Iterable[str] | None = None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_mapping(data, material_ids)
    return data


def validate_mapping(data: dict, material_ids: Iterable[str] | None = None) -> None:
    known = set(material_ids) if material_ids is not None else None
    seidou = data.get("seidou") or {}
    for name, metric in (seidou.get("metrics") or {}).items():
        if metric not in METRICS:
            raise MappingError(f"seidou.metrics[{name}]: unknown metric {metric}")
    seen: set[tuple[str, str]] = set()
    for it in seidou.get("items") or []:
        m = it.get("material")
        if known is not None and m not in known:
            raise MappingError(f"seidou: unknown material {m}")
        if not re.fullmatch(r"[0-9]{4}", str(it.get("report", ""))):
            raise MappingError(f"seidou {m}: report must be 4 digits")
        products = it.get("products") or []
        flat = [p for g in products for p in (g if isinstance(g, list) else [g])]
        if not flat or not all(isinstance(p, str) and p.strip() for p in flat):
            raise MappingError(f"seidou {m}: products must be product names")
        if any(isinstance(p, list) for p in products) and not all(isinstance(p, list) and p for p in products):
            raise MappingError(f"seidou {m}: mix of names and alternatives")
        key = (m, it["report"])
        if key in seen:
            raise MappingError(f"seidou: duplicate {m} in report {it['report']}")
        seen.add(key)
    seen_sekiyu: set[tuple[str, str]] = set()
    for it in (data.get("sekiyu") or {}).get("items") or []:
        m, metric = it.get("material"), it.get("metric")
        if known is not None and m not in known:
            raise MappingError(f"sekiyu: unknown material {m}")
        if metric not in METRICS:
            raise MappingError(f"sekiyu {m}: unknown metric {metric}")
        if (m, metric) in seen_sekiyu:
            raise MappingError(f"sekiyu: duplicate {m} {metric}")
        seen_sekiyu.add((m, metric))
        for kind in ("nenpo", "kakuho", "sokuho"):
            spec = it.get(kind)
            if spec is not None and (not spec.get("blocks") or not spec.get("column")):
                raise MappingError(f"sekiyu {m}.{kind}: blocks and column are required")
        if not any(it.get(k) for k in ("nenpo", "kakuho", "sokuho")):
            raise MappingError(f"sekiyu {m}: nenpo, kakuho or sokuho is required")
    seen_trade: set[str] = set()
    for it in (data.get("trade") or {}).get("items") or []:
        m = it.get("material")
        if known is not None and m not in known:
            raise MappingError(f"trade: unknown material {m}")
        if m in seen_trade:
            raise MappingError(f"trade: duplicate material {m}")
        seen_trade.add(m)
        if not it.get("import") and not it.get("export"):
            raise MappingError(f"trade {m}: import or export is required")
        for flow in ("import", "export"):
            for code in it.get(flow) or []:
                if not isinstance(code, str) or not _CODE_RE.match(code):
                    raise MappingError(f"trade {m}.{flow}: bad code {code!r}")


# ---------- 行 ----------

@dataclass(frozen=True)
class Row:
    material_id: str
    month: str
    metric: str
    source: str
    value: float
    unit: str
    revision: int
    origin: str  # e-Stat の表 ID や Excel のファイル名

    def as_dict(self, now: datetime) -> dict:
        return {
            "material_id": self.material_id, "month": self.month, "metric": self.metric,
            "source": self.source, "value": self.value, "unit": self.unit,
            "revision": self.revision, "origin": self.origin, "is_estimate": False, "updated_at": now,
        }


class SeidouRecord(NamedTuple):
    """生産動態統計の 1 セル。表の形（e-Stat / Excel）によらない"""
    report: str
    product: str  # NFKC の品名
    metric: str   # 統計の指標名（生産 / 月末在庫 …）
    unit: str
    month: str    # YYYY-MM
    raw: Any


def seidou_rows(records: Iterable[SeidouRecord], *, items: list[dict], metrics: dict[str, str],
                revision: int, origin: str) -> tuple[list[Row], list[str]]:
    """品名の値を素材ごとに足す。(行, 警告)

    素材ごとに、候補のうち品名が全部その表にある最初の並びを使う（無ければ警告して書かない）。
    その並びの品名が 1 つでも欠けた月（秘匿・不詳）は書かない（過小な合計で系列が跳ねるのを避ける）。
    """
    records = [r for r in records if r.metric in metrics]
    present: dict[str, set[str]] = defaultdict(set)
    for r in records:
        present[r.report].add(r.product)
    want: dict[tuple[str, str], str] = {}
    need: dict[tuple[str, str], set[str]] = {}
    warnings: list[str] = []
    for it in items:
        if it["report"] not in present:
            continue
        group = next((g for g in product_groups(it) if set(g) <= present[it["report"]]), None)
        if group is None:
            warnings.append(f"{it['material']} -: products not in table {product_groups(it)}")
            continue
        need[(it["report"], it["material"])] = set(group)
        want.update({(it["report"], p): it["material"] for p in group})
    cells: dict[tuple[str, str, str, str], dict[str, tuple[float, str]]] = defaultdict(dict)
    for r in records:
        material = want.get((r.report, r.product))
        if material is None:
            continue
        num = parse_num(r.raw)
        if num is None:
            continue
        unit, scale = _SEIDOU_UNITS.get(r.unit, (r.unit, 1.0))
        cells[(r.report, material, r.month, metrics[r.metric])][r.product] = (num * scale, unit)
    rows: list[Row] = []
    for (report, material, month, metric), got in sorted(cells.items()):
        missing = need[(report, material)] - set(got)
        if missing:
            warnings.append(f"{material} {month} {metric}: missing {sorted(missing)}")
            continue
        units = {u for _, u in got.values()}
        if len(units) != 1:
            raise MappingError(f"{material} {metric}: mixed units {sorted(units)} in report {report}")
        rows.append(Row(material, month, metric, SEIDOU_SOURCE.format(report=report),
                        sum(n for n, _ in got.values()), units.pop(), revision, origin))
    return rows, warnings


def sekiyu_rows(cells: Iterable[Any], *, items: list[dict], kind: str, revision: int,
                origin: str) -> tuple[list[Row], list[str]]:
    """石油統計のセル（block, column, unit, month, raw）→ 行。kind は nenpo / kakuho / sokuho。

    blocks を足す（ジェット燃料油 + 灯油 など）。1 つでも欠けた月は書かない。
    column は候補の並びでもよい（年報の表題は年で語順が違う）。月ごとに値のある最初の候補を使う。
    """
    index: dict[tuple[str, str, str], tuple[Any, str]] = {}
    for c in cells:
        index.setdefault((product_name(c.block), product_name(c.column), c.month), (c.raw, c.unit))
    months = sorted({c.month for c in cells}) if cells else []
    rows: list[Row] = []
    warnings: list[str] = []
    for it in items:
        spec = it.get(kind)
        if not spec:
            continue
        blocks = [product_name(b) for b in spec["blocks"]]
        columns = [product_name(c) for c in (spec["column"] if isinstance(spec["column"], list) else [spec["column"]])]
        for month in months:
            got = next((g for col in columns
                        if any(g := [index.get((b, col, month)) for b in blocks])), None)
            if got is None:
                continue
            nums = [parse_num(g[0]) if g else None for g in got]
            if any(n is None for n in nums):
                warnings.append(f"{it['material']} {month} {it['metric']}: missing in {spec['blocks']}")
                continue
            units = {g[1] for g in got if g}
            if len(units) != 1:
                raise MappingError(f"{it['material']} {it['metric']}: mixed units {sorted(units)}")
            rows.append(Row(it["material"], month, it["metric"], SEKIYU_SOURCE, sum(nums), units.pop(),
                            revision, origin))
    for it in items:
        spec = it.get(kind)
        if spec and not any(r.material_id == it["material"] and r.metric == it["metric"] for r in rows):
            warnings.append(f"{it['material']} -: {it['metric']} not in table {spec}")
    return rows, warnings


def summarize_warnings(warnings: list[str]) -> list[str]:
    """素材ごとに 1 行。秘匿などで書かなかった月の数と例"""
    by: dict[str, list[str]] = defaultdict(list)
    for w in warnings:
        by[w.split()[0]].append(w)
    return [f"{m}: {len(ws)} skipped (e.g. {ws[0]})" for m, ws in sorted(by.items())]


def write_rows(db: Session, rows: list[Row]) -> int:
    """UPSERT。保存済みより revision が低い行は書かない（確報を速報で上書きしない）。書いた行数"""
    if not rows:
        return 0
    keys = [(r.material_id, r.month, r.metric, r.source) for r in rows]
    have: dict[tuple, int] = {}
    pk = (MaterialMonthly.material_id, MaterialMonthly.month, MaterialMonthly.metric, MaterialMonthly.source)
    for i in range(0, len(keys), 500):
        q = db.query(*pk, MaterialMonthly.revision).filter(tuple_(*pk).in_(keys[i:i + 500]))
        have.update({(a, b, c, d): rev for a, b, c, d, rev in q})
    keep = [r for r, k in zip(rows, keys) if r.revision >= have.get(k, 0)]
    now = now_utc()
    upsert(db, MaterialMonthly, [r.as_dict(now) for r in keep])
    return len(keep)

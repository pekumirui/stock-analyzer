"""e-Stat API → material_monthly。統計の品目と素材の対応は config/estat_mapping.json。

- 生産: 経済産業省生産動態統計（00550200）の年ごとの「時系列表(<報告番号>_…)」。表側は `製品_<品目>_<品名>_<記号>_<指標>_<単位>`
- 輸出入: 普通貿易統計（00350300）の「品別国別表」。統計品目番号 9 桁 × 国 × 月。国は合計する
設計は docs/DESIGN_supply.md。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .config import ESTAT_APP_ID, ESTAT_BASE, ESTAT_MAPPING_PATH

METRICS = ("production", "shipment", "inventory", "import", "export")
_CODE_RE = re.compile(r"^[0-9]{4,9}$")
_PRODUCT_RE = re.compile(r"^[0-9]{4}$")
# 輸出入の数量単位。KG は t に寄せる。それ以外の単位はそのまま
_TRADE_UNITS = {"KG": ("t", 0.001), "MT": ("t", 1.0), "KL": ("kl", 1.0), "NO": ("本", 1.0)}
# 生産動態の単位。同じ素材の品目で 本 と 千本 が混ざる（タイヤ）
_PRODUCTION_UNITS = {"千本": ("本", 1000.0)}


class EstatError(RuntimeError):
    pass


class EstatMappingError(ValueError):
    pass


def _list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _text(x: Any) -> str:
    return x.get("$", "") if isinstance(x, dict) else (x or "")


# ---------- 対応表 ----------

def load_mapping(path: Path = ESTAT_MAPPING_PATH, material_ids: Iterable[str] | None = None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_mapping(data, material_ids)
    return data


def validate_mapping(data: dict, material_ids: Iterable[str] | None = None) -> None:
    known = set(material_ids) if material_ids is not None else None
    prod = data.get("production") or {}
    metrics = prod.get("metrics") or {}
    for name, metric in metrics.items():
        if metric not in METRICS:
            raise EstatMappingError(f"production.metrics[{name}]: unknown metric {metric}")
    seen: set[tuple[str, str]] = set()
    for it in prod.get("items") or []:
        m = it.get("material")
        if known is not None and m not in known:
            raise EstatMappingError(f"production: unknown material {m}")
        if not re.fullmatch(r"[0-9]{4}", str(it.get("report", ""))):
            raise EstatMappingError(f"production {m}: report must be 4 digits")
        products = it.get("products") or []
        if not products or not all(isinstance(p, str) and p.strip() for p in products):
            raise EstatMappingError(f"production {m}: products must be product names")
        key = (m, it["report"])
        if key in seen:
            raise EstatMappingError(f"production: duplicate {m} in report {it['report']}")
        seen.add(key)
    seen_trade: set[str] = set()
    for it in (data.get("trade") or {}).get("items") or []:
        m = it.get("material")
        if known is not None and m not in known:
            raise EstatMappingError(f"trade: unknown material {m}")
        if m in seen_trade:
            raise EstatMappingError(f"trade: duplicate material {m}")
        seen_trade.add(m)
        if not it.get("import") and not it.get("export"):
            raise EstatMappingError(f"trade {m}: import or export is required")
        for flow in ("import", "export"):
            for code in it.get(flow) or []:
                if not isinstance(code, str) or not _CODE_RE.match(code):
                    raise EstatMappingError(f"trade {m}.{flow}: bad code {code!r}")


# ---------- API ----------

class EstatClient:
    def __init__(self, app_id: str = ESTAT_APP_ID, base: str = ESTAT_BASE,
                 http: httpx.Client | None = None, pause: float = 0.5) -> None:
        if not app_id:
            raise EstatError("ESTAT_APP_ID is not set")
        self.app_id = app_id
        self.base = base.rstrip("/") + "/"
        self.http = http or httpx.Client(timeout=120)
        self.pause = pause

    def call(self, endpoint: str, root: str, **params: Any) -> dict:
        params["appId"] = self.app_id
        for attempt in range(4):
            try:
                r = self.http.get(self.base + endpoint, params=params)
                r.raise_for_status()
                body = r.json()[root]
                break
            except (httpx.HTTPError, ValueError, KeyError):
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        status = int(body["RESULT"].get("STATUS", 0))
        # 0=正常, 1=該当なし, 2=一部。100 以上はエラー
        if status >= 100:
            raise EstatError(f"{endpoint} {status}: {body['RESULT'].get('ERROR_MSG')}")
        time.sleep(self.pause)
        return body

    def tables(self, stats_code: str, survey_years: str) -> list[dict]:
        out: list[dict] = []
        start = 1
        while True:
            body = self.call("getStatsList", "GET_STATS_LIST", statsCode=stats_code,
                             surveyYears=survey_years, limit=1000, startPosition=start)
            dl = body.get("DATALIST_INF")
            if not isinstance(dl, dict):
                return out
            out.extend(_list(dl.get("TABLE_INF")))
            nxt = (dl.get("RESULT_INF") or {}).get("NEXT_KEY")
            if not nxt:
                return out
            start = int(nxt)

    def meta(self, stats_data_id: str) -> dict[str, dict[str, str]]:
        """{分類 id: {コード: 名前}}"""
        body = self.call("getMetaInfo", "GET_META_INFO", statsDataId=stats_data_id)
        objs = _list(body["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"])
        return {o["@id"]: {c["@code"]: c["@name"] for c in _list(o.get("CLASS"))} for o in objs}

    def values(self, stats_data_id: str, **filters: str) -> list[dict]:
        out: list[dict] = []
        start = 1
        while True:
            body = self.call("getStatsData", "GET_STATS_DATA", statsDataId=stats_data_id,
                             metaGetFlg="N", limit=100000, startPosition=start, **filters)
            sd = body.get("STATISTICAL_DATA") or {}
            out.extend(_list((sd.get("DATA_INF") or {}).get("VALUE")))
            nxt = (sd.get("RESULT_INF") or {}).get("NEXT_KEY")
            if not nxt:
                return out
            start = int(nxt)


def table_title(t: dict) -> str:
    title = t.get("TITLE")
    return _text(title) if isinstance(title, dict) else (title or "")


def production_table(tables: list[dict], report: str) -> dict | None:
    hits = [t for t in tables if f"時系列表({report}_" in table_title(t)]
    return hits[0] if hits else None


def trade_table(tables: list[dict], flow: str) -> dict | None:
    name = f"全国分 品別国別表 {'輸入' if flow == 'import' else '輸出'}"
    hits = [t for t in tables if _text(t.get("STATISTICS_NAME")).endswith(name)]
    return hits[0] if hits else None


def published_through(title: str) -> int:
    """貿易統計の表題に書かれた最後の月。「(1-7月：確報、8月：輸入9桁速報)」なら 8。未公表の月は 0 で入っている"""
    months = [int(m) for m in re.findall(r"(\d{1,2})月", unicodedata.normalize("NFKC", title))]
    return max(months) if months else 12


# ---------- 解析 ----------

@dataclass(frozen=True)
class Row:
    material_id: str
    month: str
    metric: str
    source: str
    value: float
    unit: str
    stats_data_id: str

    def as_dict(self, now: datetime) -> dict:
        return {
            "material_id": self.material_id, "month": self.month, "metric": self.metric,
            "source": self.source, "value": self.value, "unit": self.unit,
            "stats_data_id": self.stats_data_id, "is_estimate": False, "updated_at": now,
        }


_ZERO = {"-", "(-)", "*-", "* -"}  # 皆無または該当なし


def _num(raw: Any) -> float | None:
    """数値。"-" 系は 0。秘匿 "X"、不詳 "…"、得られない "***" は None"""
    s = unicodedata.normalize("NFKC", str(raw)).strip()
    if s in _ZERO:
        return 0.0
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def product_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip()


def parse_production_label(name: str) -> tuple[str, str, str] | None:
    """`製品_0101_ポリエチレン（…）_A_生産_t` → (品名, 指標, 単位)。品名は NFKC。製品以外の区分は None

    品目番号は年で振り直される（2023 年に化学繊維、2024 年に ABS）ので使わない。
    """
    parts = name.split("_")
    if len(parts) < 6 or parts[0] != "製品" or not _PRODUCT_RE.match(parts[1]):
        return None
    return product_name("_".join(parts[2:-3])), parts[-2], parts[-1]


def month_of(name: str) -> str | None:
    """時間軸の名前 → YYYY-MM。`202501` / `2022000101_2022年1月(Jan.)`。年・年度・四半期は None"""
    m = re.fullmatch(r"(\d{4})(\d{2})", name) or re.search(r"(\d{4})年(\d{1,2})月", name)
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None


def production_dims(meta: dict[str, dict[str, str]]) -> tuple[str, str]:
    """(品目の分類 id, 月の分類 id)。2025 年の表は cat01 / time、2021〜2024 年は cat02 / cat01 か time"""
    item = next((k for k, names in meta.items()
                 if any(parse_production_label(n) for n in names.values())), None)
    month = next((k for k, names in meta.items()
                  if k != item and any(month_of(n) for n in names.values())), None)
    if not item or not month:
        raise EstatError(f"production table: cannot find item/month dimensions in {sorted(meta)}")
    return item, month


def production_rows(*, report: str, items: list[dict], metrics: dict[str, str],
                    labels: dict[str, str], months: dict[str, str], item_dim: str, month_dim: str,
                    values: list[dict], stats_code: str,
                    stats_data_id: str) -> tuple[list[Row], list[str]]:
    """品名の値を素材ごとに足す。表にある品名が 1 つでも欠けた月は出さない（過小に足さない）。(行, 警告)

    labels は品目の分類（コード → 表側名）、months は月の分類（コード → 名前）。
    対応表の品名のうちその年の表に無いものは数えない（AS 樹脂・ABS 樹脂は 2024 年に 1 品目へまとまった）。
    """
    present = {lab[0] for n in labels.values() if (lab := parse_production_label(n))}
    want: dict[str, str] = {}
    need: dict[str, set[str]] = {}
    warnings: list[str] = []
    for it in items:
        if it["report"] != report:
            continue
        names = {product_name(p) for p in it["products"]} & present
        if not names:
            warnings.append(f"{it['material']} -: no product in table ({it['products']})")
            continue
        need[it["material"]] = names
        want.update({n: it["material"] for n in names})
    cells: dict[tuple[str, str, str], dict[str, tuple[float, str]]] = defaultdict(dict)
    for v in values:
        label = parse_production_label(labels.get(v.get("@" + item_dim, ""), ""))
        if not label:
            continue
        product, metric_name, unit = label
        metric = metrics.get(metric_name)
        if product not in want or not metric:
            continue
        month = month_of(months.get(v.get("@" + month_dim, ""), ""))
        if not month:
            continue
        num = _num(v.get("$"))
        if num is None:
            continue
        unit, scale = _PRODUCTION_UNITS.get(unit, (unit, 1.0))
        cells[(want[product], month, metric)][product] = (num * scale, unit)
    rows: list[Row] = []
    source = f"estat:{stats_code}:{report}"
    for (material, month, metric), got in sorted(cells.items()):
        if set(got) != need[material]:
            warnings.append(f"{material} {month} {metric}: missing {sorted(need[material] - set(got))}")
            continue
        units = {u for _, u in got.values()}
        if len(units) != 1:
            raise EstatMappingError(f"{material} {metric}: mixed units {sorted(units)} in report {report}")
        rows.append(Row(material, month, metric, source, sum(n for n, _ in got.values()),
                        units.pop(), stats_data_id))
    return rows, warnings


def match_codes(prefixes: list[str], codes: Iterable[str]) -> list[str]:
    return sorted(c for c in codes if any(c.startswith(p) for p in prefixes))


def trade_rows(*, flow: str, items: list[dict], cat02: dict[str, str], times: dict[str, str],
               values: list[dict], last_month: int, stats_code: str,
               stats_data_id: str) -> list[Row]:
    """品別国別表の数量を国と品目で足す。最新年の last_month より後（未公表で 0）は捨てる。

    数量は 単位1 があれば 数量1、無ければ 数量2（化学品の多くは 数量2 の KG だけ）。

    items の各要素は `codes` に表の統計品目番号（前方一致を解決済み）を持つ。
    """
    material_of: dict[str, list[str]] = defaultdict(list)
    for it in items:
        for code in it.get("codes", []):
            material_of[code].append(it["material"])
    years = {code: name[:4] for code, name in times.items() if re.match(r"\d{4}年", name)}
    latest = max(years.values()) if years else ""
    cell_unit: dict[tuple[str, str, str, str], str] = {}
    for v in values:
        m = re.fullmatch(r"単位([12])", cat02.get(v.get("@cat02", ""), ""))
        unit = unicodedata.normalize("NFKC", v.get("$", "")).strip()
        if m and unit and v.get("@cat01") in material_of:
            key = (v["@cat01"], v.get("@area", ""), years.get(v.get("@time", ""), ""), m.group(1))
            cell_unit[key] = unit
    sums: dict[tuple[str, str], float] = defaultdict(float)
    unit_of: dict[tuple[str, str], str] = {}
    for v in values:
        code = v.get("@cat01", "")
        year = years.get(v.get("@time", ""))
        m = re.fullmatch(r"(\d{1,2})月_数量([12])", cat02.get(v.get("@cat02", ""), ""))
        if code not in material_of or not year or not m:
            continue
        area = v.get("@area", "")
        qty = "1" if (code, area, year, "1") in cell_unit else "2"
        if m.group(2) != qty:
            continue
        month = int(m.group(1))
        if year == latest and month > last_month:
            continue
        num = _num(v.get("$"))
        if not num:
            continue
        unit = cell_unit.get((code, area, year, qty))
        if not unit:
            raise EstatError(f"{code} {year}: quantity without unit")
        unit, scale = _TRADE_UNITS.get(unit, (unit, 1.0))
        for material in material_of[code]:
            key = (material, f"{year}-{month:02d}")
            if unit_of.setdefault(key, unit) != unit:
                raise EstatMappingError(f"{material} {flow}: mixed units ({unit_of[key]}, {unit})")
            sums[key] += num * scale
    rows: list[Row] = []
    for (material, month), total in sorted(sums.items()):
        rows.append(Row(material, month, flow, f"estat:{stats_code}", total, unit_of[(material, month)], stats_data_id))
    return rows


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

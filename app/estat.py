"""e-Stat API。素材の月次量（app/material_stats.py）のうち e-Stat から取る部分。

- 生産動態統計（00550200）の年ごとの「時系列表(<報告番号>_…)」。年間補正後の年報（revision 3）。
  表側は `製品_<品目>_<品名>_<記号>_<指標>_<単位>`
- 普通貿易統計（00350300）の「品別国別表」。統計品目番号 9 桁 × 国 × 月。国は合計する
"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

import httpx

from .config import ESTAT_APP_ID, ESTAT_BASE
from .material_stats import (
    REVISION_KAKUHO, REVISION_SOKUHO, TRADE_SOURCE, MappingError, Row, SeidouRecord, parse_num, product_name,
)

_PRODUCT_RE = re.compile(r"^[0-9]{4}$")
# 輸出入の数量単位。KG は t に寄せる。それ以外の単位はそのまま
_TRADE_UNITS = {"KG": ("t", 0.001), "MT": ("t", 1.0), "KL": ("kl", 1.0), "NO": ("本", 1.0)}


class EstatError(RuntimeError):
    pass


def _list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _text(x: Any) -> str:
    return x.get("$", "") if isinstance(x, dict) else (x or "")


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


def sokuho_month(title: str) -> int | None:
    """貿易統計の表題で速報の月。「(1-7月：確報、8月：輸入9桁速報)」なら 8"""
    m = re.search(r"(\d{1,2})月[:：][^、)]*速報", unicodedata.normalize("NFKC", title))
    return int(m.group(1)) if m else None


def published_through(title: str) -> int:
    """貿易統計の表題に書かれた最後の月。「(1-7月：確報、8月：輸入9桁速報)」なら 8。未公表の月は 0 で入っている"""
    months = [int(m) for m in re.findall(r"(\d{1,2})月", unicodedata.normalize("NFKC", title))]
    return max(months) if months else 12


# ---------- 解析 ----------

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


def seidou_records(report: str, meta: dict[str, dict[str, str]], values: list[dict]) -> list[SeidouRecord]:
    """時系列表の値 → 表の形によらないセル"""
    item_dim, month_dim = production_dims(meta)
    labels, months = meta[item_dim], meta[month_dim]
    out: list[SeidouRecord] = []
    for v in values:
        label = parse_production_label(labels.get(v.get("@" + item_dim, ""), ""))
        month = month_of(months.get(v.get("@" + month_dim, ""), ""))
        if label and month:
            out.append(SeidouRecord(report, label[0], label[1], label[2], month, v.get("$")))
    return out


def match_codes(prefixes: list[str], codes: Iterable[str]) -> list[str]:
    return sorted(c for c in codes if any(c.startswith(p) for p in prefixes))


def trade_rows(*, flow: str, items: list[dict], cat02: dict[str, str], times: dict[str, str],
               values: list[dict], last_month: int, stats_data_id: str,
               sokuho: int | None = None) -> list[Row]:
    """品別国別表の数量を国と品目で足す。最新年の last_month より後（未公表で 0）は捨てる。

    数量は 単位1 があれば 数量1、無ければ 数量2（化学品の多くは 数量2 の KG だけ）。
    最新年の sokuho の月は revision 1（速報）、それ以外は 2。

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
        num = parse_num(v.get("$"))
        if not num:
            continue
        unit = cell_unit.get((code, area, year, qty))
        if not unit:
            raise EstatError(f"{code} {year}: quantity without unit")
        unit, scale = _TRADE_UNITS.get(unit, (unit, 1.0))
        for material in material_of[code]:
            key = (material, f"{year}-{month:02d}")
            if unit_of.setdefault(key, unit) != unit:
                raise MappingError(f"{material} {flow}: mixed units ({unit_of[key]}, {unit})")
            sums[key] += num * scale
    rows: list[Row] = []
    for (material, month), total in sorted(sums.items()):
        revision = REVISION_SOKUHO if sokuho and month == f"{latest}-{sokuho:02d}" else REVISION_KAKUHO
        rows.append(Row(material, month, flow, TRADE_SOURCE, total, unit_of[(material, month)],
                        revision, stats_data_id))
    return rows

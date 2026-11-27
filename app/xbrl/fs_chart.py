from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping, Protocol, Sequence

from app.xbrl.mappings import load_mapping_specs

CHART_ITEMS = {
    "BS": ("current_assets", "noncurrent_assets", "total_assets", "current_liabilities", "noncurrent_liabilities",
           "total_liabilities", "net_assets"),
    "PL": ("net_sales", "gross_profit", "operating_income", "ordinary_income", "net_income"),
    "CF": ("operating_cf", "investing_cf", "financing_cf", "cash_end"),
}
STATEMENT_NAMESPACES = {"jppfs_cor", "jpigp_cor"}
NET_SALES_LABELS = {"売上高", "売上収益", "営業収益", "営業収益合計", "売上高合計", "収益合計", "経常収益", "売上高及び営業収益"}
GROSS_PROFIT_LABEL = "売上総利益"
PREVIOUS_QUARTER = {"Q2": "Q1", "Q3": "Q2", "FY": "Q3"}
QUARTER_GAP_DAYS = (60, 120)


class ChartColumn(Protocol):
    values: Mapping[str, float]
    consolidated: bool

    @property
    def period_end(self) -> Any: ...

    @property
    def primary(self) -> Any: ...


@lru_cache(maxsize=1)
def element_priorities() -> dict[str, list[str]]:
    ranked: dict[str, dict[str, int]] = {}
    for spec in load_mapping_specs():
        std = str(spec.get("std_item"))
        for el in spec.get("elements") or ():
            if el.get("ns") not in STATEMENT_NAMESPACES:
                continue
            name, priority = str(el["name"]), int(el.get("priority", 100))
            best = ranked.setdefault(std, {})
            best[name] = min(priority, best.get(name, priority))
    return {std: sorted(names, key=names.__getitem__) for std, names in ranked.items()}


def _label_keys(std: str, labels: Mapping[str, str]) -> list[str]:
    if std == "net_sales":
        return [k for k, v in labels.items() if str(v).strip() in NET_SALES_LABELS]
    if std == "gross_profit":
        return [k for k, v in labels.items() if str(v).strip().startswith(GROSS_PROFIT_LABEL)]
    return []


def candidate_keys(std: str, labels: Mapping[str, str]) -> list[str]:
    keys = list(element_priorities().get(std, ()))
    return keys + [k for k in _label_keys(std, labels) if k not in keys]


def pick(column: ChartColumn, keys: Sequence[str]) -> float | None:
    for key in keys:
        value = column.values.get(key)
        if value is not None:
            return float(value)
    return None


def _previous_quarter(columns: Sequence[ChartColumn], i: int) -> ChartColumn | None:
    column = columns[i]
    want = PREVIOUS_QUARTER.get(column.primary.period_type)
    if want is None or i == 0:
        return None
    prev = columns[i - 1]
    gap = (column.period_end - prev.period_end).days
    if prev.primary.period_type != want or prev.consolidated != column.consolidated:
        return None
    return prev if QUARTER_GAP_DAYS[0] <= gap <= QUARTER_GAP_DAYS[1] else None


def quarter_value(columns: Sequence[ChartColumn], i: int, keys: Sequence[str]) -> float | None:
    value = pick(columns[i], keys)
    if value is None or columns[i].primary.period_type == "Q1":
        return value
    prev = _previous_quarter(columns, i)
    before = None if prev is None else pick(prev, keys)
    return None if before is None else value - before


def chart_series(
    run: Sequence[ChartColumn], visible: Sequence[ChartColumn], statement: str, labels: Mapping[str, str],
) -> dict[str, Any]:
    where = {id(c): i for i, c in enumerate(run)}
    series: dict[str, list[float | None]] = {}
    quarter: dict[str, list[float | None]] = {}
    for std in CHART_ITEMS.get(statement, ()):
        keys = candidate_keys(std, labels)
        series[std] = [pick(c, keys) for c in visible]
        if statement == "PL":
            quarter[std] = [quarter_value(run, where[id(c)], keys) for c in visible]
    out: dict[str, Any] = {"series": series}
    if statement == "PL":
        out["quarter"] = quarter
    return out

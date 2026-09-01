"""PDF 月次列順と事業年度末月から暦年を割り当てる。year は呼び出し側が渡さない。"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Sequence

# 保存値は PDF 原文。unit は表示だけ。値の大きさから推測しない。
MONTHLY_UNITS = ("number", "yoy_index", "yoy_pct")
_YOY_UNITS = frozenset({"yoy_index", "yoy_pct"})
# 長い語を先に。前年同月比 を 前年同月 より先、括弧付きを 前年比 より先。
_YOY_FRAGMENTS = ("前年同月比", "前年同月", "(前年比)", "（前年比）", "前年比")
_YOY_SUFFIX = "(前年比)"
_UNIT_MSG = "unit は number / yoy_index / yoy_pct です。"


class MonthlyIngestError(Exception):
    """API の error.code に載せる。DB には触らない。"""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def parse_monthly_unit(raw: Any, *, omitted_ok: bool = False) -> str | None:
    """許可文字列だけ。省略時は omitted_ok なら None。大きさでは決めない。"""
    if raw is None and omitted_ok:
        return None
    if not isinstance(raw, str) or raw not in MONTHLY_UNITS:
        raise MonthlyIngestError(400, "INVALID_PARAM", _UNIT_MSG)
    return raw


def strip_yoy_fragments(name: str) -> str:
    """PDF 由来の前年比語を繰り返し除去し、残り空白を畳む。"""
    s = name.strip()
    while True:
        prev = s
        for frag in _YOY_FRAGMENTS:
            s = s.replace(frag, "")
        s = re.sub(r"\s+", " ", s).strip()
        if s == prev:
            return s


def canonicalize_element_label(element: str, unit: str | None) -> str:
    """保存名・表示名。yoy は語を剥がして (前年比) を1回。number はトリムのみ。"""
    trimmed = element.strip()
    if (unit or "number") not in _YOY_UNITS:
        return trimmed
    base = strip_yoy_fragments(trimmed)
    if base.endswith(_YOY_SUFFIX):
        return base
    return f"{base}{_YOY_SUFFIX}"


def same_yoy_element(
    stored_name: str,
    stored_unit: str | None,
    incoming_name: str,
    incoming_unit: str | None,
) -> bool:
    """incoming は canonicalize 済み。yoy 既存行を同じ規則で正規化して同一行か見る。

    number の `売上高` は yoy の `売上高(前年比)` とは別行。
    """
    if stored_name == incoming_name:
        return True
    stored_u = stored_unit or "number"
    if stored_u not in _YOY_UNITS:
        return False
    if canonicalize_element_label(stored_name, stored_u) == incoming_name:
        return True
    if canonicalize_element_label(stored_name, incoming_unit) == incoming_name:
        return True
    return False


def plan_yoy_duplicate_merges(
    rows: Sequence[tuple[str, int, str, str | None]],
) -> list[tuple[str, int, str, list[str]]]:
    """同一 (code, year) の yoy 行で正規化名が同じもの。短い名を正規名へ寄せる。

    number は見ない（売上高 と 売上高(前年比) は別行のまま）。
    戻りは (code, year, canonical, short_names)。
    """
    groups: dict[tuple[str, int, str], set[str]] = {}
    for code, year, element, unit in rows:
        if (unit or "number") not in _YOY_UNITS:
            continue
        canonical = canonicalize_element_label(element, unit)
        if not canonical:
            continue
        groups.setdefault((code, year, canonical), set()).add(element)
    out: list[tuple[str, int, str, list[str]]] = []
    for (code, year, canonical), names in groups.items():
        if len(names) < 2:
            continue
        shorts = sorted(name for name in names if name != canonical)
        if not shorts:
            continue
        out.append((code, year, canonical, shorts))
    out.sort()
    return out


def monthly_element_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", s)


def monthly_series_kind(element: str, unit: str | None) -> str | None:
    """TOP/チャート用。all_store / existing / stores。どれでもなければ None。"""
    key = monthly_element_key(element)
    u = unit or "number"
    if "店舗数" in key and u == "number":
        return "stores"
    if u not in _YOY_UNITS:
        return None
    if "既存" in key:
        return "existing"
    if "全店" in key:
        return "all_store"
    return None


def yoy_display_number(value: float, unit: str | None) -> float:
    n = float(value)
    if (unit or "number") == "yoy_pct":
        n = 100.0 + n
    return n


def format_monthly_cell(value: float | None, unit: str | None) -> str:
    """空セルは空。表は指数 100=フラット。保存値は変えない。"""
    if value is None:
        return ""
    n = float(value)
    if n != n or n in (float("inf"), float("-inf")):
        return ""
    u = unit or "number"
    if u == "yoy_pct":
        n = 100.0 + n
    text = f"{n:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_as_of(raw: Any, *, default: date | None = None) -> date:
    if raw is None:
        return default if default is not None else date.today()
    if not isinstance(raw, str) or not raw:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。") from None
    if parsed.isoformat() != raw:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。")
    return parsed


def resolve_fy_end_month(
    rows: Sequence[tuple[date | None, date | None]],
    as_of: date,
) -> int:
    """as_of 以前の最新開示の事業年度末月。同日は期末の新しい方。過去の期変更は見ない。"""
    eligible: list[tuple[date, date]] = []
    for disclosed, fy_end in rows:
        if disclosed is None or fy_end is None:
            continue
        if disclosed <= as_of:
            eligible.append((disclosed, fy_end))
    if not eligible:
        raise MonthlyIngestError(
            400, "NEED_FY_END",
            "この銘柄の決算に事業年度末 (fiscal_year_end) がありません。",
        )
    _disclosed, fy_end = max(eligible, key=lambda item: (item[0], item[1]))
    return fy_end.month


def _is_calendar_layout(months: Sequence[int]) -> bool:
    # 先頭が1で暦月昇順。欠月は可。年をまたぐ並び (12→1) は暦年レイアウトではない。
    if not months or months[0] != 1:
        return False
    for i in range(1, len(months)):
        if months[i] <= months[i - 1]:
            return False
    return True


def _fiscal_start_month(fy_end_month: int) -> int:
    return (fy_end_month % 12) + 1


def _anchor_year(last_month: int, as_of: date) -> int:
    year = as_of.year
    if date(year, last_month, 1) <= as_of:
        return year
    return year - 1


def _assign_years(months: Sequence[int], as_of: date) -> dict[int, int]:
    year = _anchor_year(months[-1], as_of)
    assigned: dict[int, int] = {months[-1]: year}
    for i in range(len(months) - 2, -1, -1):
        prev_month = months[i]
        curr_month = months[i + 1]
        if prev_month > curr_month:
            year -= 1
        assigned[prev_month] = year
    return {month: assigned[month] for month in months}


def plan_column_years(
    months: Sequence[int],
    fy_end_month: int,
    as_of: date,
) -> tuple[str, dict[int, int]]:
    """列順 → 暦年。layout は calendar | fiscal | partial。並びでは拒否しない。"""
    if not months:
        raise MonthlyIngestError(400, "INVALID_PARAM", "months は1〜12の重複しない整数の配列です。")
    start = months[0]
    if _is_calendar_layout(months):
        layout = "calendar"
    elif start == _fiscal_start_month(fy_end_month):
        layout = "fiscal"
    else:
        layout = "partial"
    return layout, _assign_years(months, as_of)


def map_pdf_columns(
    months: Sequence[int],
    fy_end_rows: Sequence[tuple[date | None, date | None]],
    as_of: date,
) -> tuple[int, str, dict[int, int]]:
    fy_end_month = resolve_fy_end_month(fy_end_rows, as_of)
    layout, mapping = plan_column_years(months, fy_end_month, as_of)
    return fy_end_month, layout, mapping

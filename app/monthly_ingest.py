"""PDF 月次列順と事業年度末月から暦年を割り当てる。year は呼び出し側が渡さない。"""
from __future__ import annotations

from datetime import date
from typing import Any, Sequence


class MonthlyIngestError(Exception):
    """API の error.code に載せる。DB には触らない。"""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


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


def resolve_fy_end_month(fy_ends: Sequence[date | None]) -> int:
    """disclosed_date 降順の fiscal_year_end から月末月を取る。月が二種なら推測しない。"""
    distinct: list[int] = []
    seen: set[int] = set()
    for fy_end in fy_ends:
        if fy_end is None:
            continue
        month = fy_end.month
        if month not in seen:
            seen.add(month)
            distinct.append(month)
    if not distinct:
        raise MonthlyIngestError(
            400, "NEED_FY_END",
            "この銘柄の決算に事業年度末 (fiscal_year_end) がありません。",
        )
    if len(distinct) >= 2:
        a, b = distinct[0], distinct[1]
        raise MonthlyIngestError(
            409, "FY_END_CHANGED",
            f"事業年度末月が {a} と {b} で異なります。年は推測しません。",
        )
    return distinct[0]


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
    """列順 → 暦年。layout は calendar | fiscal。"""
    if not months:
        raise MonthlyIngestError(400, "INVALID_PARAM", "months は1〜12の重複しない整数の配列です。")
    start = months[0]
    if _is_calendar_layout(months):
        layout = "calendar"
    elif start == _fiscal_start_month(fy_end_month):
        layout = "fiscal"
    else:
        raise MonthlyIngestError(
            400, "AMBIGUOUS_LAYOUT",
            f"列の先頭月 {start} は暦年でも会計年度開始月でもありません（事業年度末月 {fy_end_month}）。",
        )
    return layout, _assign_years(months, as_of)


def map_pdf_columns(
    months: Sequence[int],
    fy_ends: Sequence[date | None],
    as_of: date,
) -> tuple[int, str, dict[int, int]]:
    fy_end_month = resolve_fy_end_month(fy_ends)
    layout, mapping = plan_column_years(months, fy_end_month, as_of)
    return fy_end_month, layout, mapping

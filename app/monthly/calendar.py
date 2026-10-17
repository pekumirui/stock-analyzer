"""暦年レイアウトの右端月から (year, month) を割り当てる。"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date


def anchor_year(last_month: int, as_of: date) -> int:
    if date(as_of.year, last_month, 1) <= as_of:
        return as_of.year
    return as_of.year - 1


def column_years(months: Sequence[int], as_of: date) -> list[tuple[int, int]]:
    if not months:
        return []
    year = anchor_year(months[-1], as_of)
    out = [(year, months[-1])]
    for pos in range(len(months) - 2, -1, -1):
        if months[pos] > months[pos + 1]:
            year -= 1
        out.append((year, months[pos]))
    return out[::-1]

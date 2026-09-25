"""アークス。全店と既存店。合計列は採らない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='9948',
    table=None,
    unit=None,
    rows=('売上高 全店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    parsed = PAGE.parse(html, as_of=as_of)
    # 客数・客単価は前年比の指数。number の店舗数などは触らない。
    for row in parsed["rows"]:
        name = str(row.get("element") or "")
        if row.get("unit") == "number" and ("客数" in name or "客単価" in name):
            row["unit"] = "yoy_index"
    return parsed

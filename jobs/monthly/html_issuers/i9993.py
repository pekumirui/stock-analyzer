"""ヤマザワ。当期の全店と既存店。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='9993',
    table=None,
    unit=None,
    rows=('全店 売上',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    parsed = PAGE.parse(html, as_of=as_of)
    # 客数・客単価は前年比の指数。number の店舗数などは触らない。
    for row in parsed["rows"]:
        name = str(row.get("element") or "")
        if row.get("unit") == "number" and ("客数" in name or "客単価" in name):
            row["unit"] = "yoy_index"
    return parsed

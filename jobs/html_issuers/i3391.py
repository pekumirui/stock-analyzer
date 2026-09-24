"""ツルハホールディングス。増減比は yoy_pct。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='3391',
    table=None,
    unit=None,
    rows=('既存店 単月売上',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""ゼビオホールディングス。当期の単月。unit は yoy_index。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='8281',
    table=None,
    unit='yoy_index',
    rows=('ゼビオ 単月 既存店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

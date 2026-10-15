"""ヴィレッジヴァンガード。unit は yoy_index。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='2769',
    table=None,
    unit='yoy_index',
    rows=('全店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

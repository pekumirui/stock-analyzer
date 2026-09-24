"""ＮＳＤ。当期の単月。前期の埋まった表は採らない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='9759',
    table=None,
    unit=None,
    rows=('売上高（百万円）',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

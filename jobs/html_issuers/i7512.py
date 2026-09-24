"""イオン北海道。当年の企業計。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='7512',
    table=None,
    unit=None,
    rows=('企業計',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

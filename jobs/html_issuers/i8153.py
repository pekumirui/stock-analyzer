"""モスフードサービス。先頭表。半期計は採らない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='8153',
    table=0,
    unit=None,
    rows=('既存店売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

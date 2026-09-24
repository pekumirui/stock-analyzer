"""ベガコーポレーション。当期の月次。前年期は採らない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='3542',
    table=None,
    unit=None,
    rows=('売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

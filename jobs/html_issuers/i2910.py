"""ロック・フィールド。見出しの単位は指数。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='2910',
    table=None,
    unit=None,
    rows=('RF1 売上高前年同月比',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

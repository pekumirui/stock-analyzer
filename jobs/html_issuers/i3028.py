"""アルペン。月次の売上表。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='3028',
    table=None,
    unit=None,
    rows=('売上高', '全店', '既存店'),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

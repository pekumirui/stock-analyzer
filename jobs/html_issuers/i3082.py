"""きちりホールディングス。全店と既存店。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='3082',
    table=None,
    unit=None,
    rows=('全店 売上高(％)',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""コナカ。既存店と全店。帯行は直後の指標に付ける。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='7494',
    table=None,
    unit=None,
    rows=('売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

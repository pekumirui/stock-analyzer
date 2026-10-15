"""クスリのアオキホールディングス。先頭表。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='3549',
    table=0,
    unit=None,
    rows=('売上高 全店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

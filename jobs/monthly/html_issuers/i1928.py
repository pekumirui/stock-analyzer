"""積水ハウス。受注の単月。累計列は採らない。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='1928',
    table=None,
    unit=None,
    rows=('【戸建住宅】',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

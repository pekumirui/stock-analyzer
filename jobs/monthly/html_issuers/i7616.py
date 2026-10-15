"""コロワイド。先頭表。合計列は採らない。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='7616',
    table=0,
    unit=None,
    rows=('既存店舗（全業態） 売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

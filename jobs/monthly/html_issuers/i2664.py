"""カワチ薬品。先頭表の全店と既存店。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='2664',
    table=0,
    unit=None,
    rows=('全店 売上高前年同月比',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

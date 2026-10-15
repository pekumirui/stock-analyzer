"""レントラックス。取扱高と売上高。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='6045',
    table=None,
    unit=None,
    rows=('取扱高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""カッパ・クリエイト。既存店舗と全店舗。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='7421',
    table=None,
    unit=None,
    rows=('既存店舗 売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

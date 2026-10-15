"""ヒマラヤ。年タブの既存店と全店。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='7514',
    table=None,
    unit=None,
    rows=('既存店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""ヒマラヤ。年タブの既存店と全店。店舗表の月末は店舗数にしてから合流する。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='7514',
    table=None,
    unit=None,
    rows=('既存店',),
    element_aliases=(('月末', '店舗数'),),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

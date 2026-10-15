"""ソフトウェア・サービス。売上高と前年同月比。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='3733',
    table=None,
    unit=None,
    rows=('売上高(百万円)',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

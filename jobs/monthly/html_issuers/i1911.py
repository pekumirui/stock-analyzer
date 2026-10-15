"""住友林業。戸建注文・賃貸・リフォーム。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='1911',
    table=None,
    unit=None,
    rows=('戸建注文住宅',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""トライアルホールディングス。トライアル既存店と全店。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='141A',
    table=0,
    unit=None,
    rows=('トライアル既存店 売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

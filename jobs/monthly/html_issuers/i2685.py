"""アンドエスティＨＤ。前年比の売上・客数・客単価。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='2685',
    table=None,
    unit=None,
    rows=('前年比（％） 売上高 全店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

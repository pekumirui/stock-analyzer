"""ＷＡＳＨハウス。全店の月次。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='6537',
    table=None,
    unit=None,
    rows=('全店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""オークワ。先頭表。月は新しい順でも暦月に戻す。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='8217',
    table=0,
    unit=None,
    rows=('既存店',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

"""神戸物産。上期と下期を同じ行に足す。全店は付けない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='3038',
    table=None,
    unit=None,
    rows=('売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

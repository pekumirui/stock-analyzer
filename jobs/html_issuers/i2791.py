"""大黒天物産。同月は先勝ち。年度帯は見出しに入れない。"""
from __future__ import annotations

from html_issuers._page import Page

PAGE = Page(
    code='2791',
    table=None,
    unit=None,
    rows=('前年比 売上高',),
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    return PAGE.parse(html, as_of=as_of)

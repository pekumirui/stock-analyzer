"""ヤマザワ。当期の全店と既存店。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='9993',
    table=None,
    unit=None,
    rows=('全店 売上',),
    force_number_marks=('客数', '客単価'),
    force_number_unit='yoy_index',
)

parse = PAGE.parse

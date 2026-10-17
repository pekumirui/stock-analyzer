"""アークス。全店と既存店。合計列は採らない。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page

PAGE = Page(
    code='9948',
    table=None,
    unit=None,
    rows=('売上高 全店',),
    force_number_marks=('客数', '客単価'),
    force_number_unit='yoy_index',
)

parse = PAGE.parse

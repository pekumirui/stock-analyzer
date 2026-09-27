"""ＮＳＤ。当期の単月。前期の埋まった表は採らない。"""
from __future__ import annotations

import re
import unicodedata

from html_issuers._page import Page

PAGE = Page(
    code='9759',
    table=None,
    unit=None,
    rows=('売上高（百万円）',),
)

_RATE_DST = '売上高(前期比)'


def _compact(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or ''))


def _keep_rows(rows: list[dict]) -> list[dict]:
    """売上高(百万円)はそのまま。増減率は売上高(前期比)。前期比(百万円)は落とす。"""
    out: list[dict] = []
    for row in rows:
        el = str(row.get('element') or '')
        compact = _compact(el)
        if not compact:
            continue
        if '増減率' in compact:
            out.append({
                **row,
                'element': _RATE_DST,
                'values': dict(row.get('values') or {}),
            })
            continue
        if '前期比' in compact:
            continue
        if '売上高' in compact:
            out.append(row)
    return out


def parse(html: str, *, as_of: str | None = None) -> dict:
    payload = PAGE.parse(html, as_of=as_of)
    payload['rows'] = _keep_rows(list(payload.get('rows') or []))
    if not payload['rows']:
        raise ValueError('9759 page does not match declaration')
    return payload

"""ハークスレイ。ほっかほっか亭と店舗流通ネットの両方に接頭辞を付ける。"""
from __future__ import annotations

from jobs.monthly.html_issuers._branded import parse_branded

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ほっかほっか亭", "ほっかほっか亭"),
    ("店舗流通ネット", "店舗流通ネット"),
)
_BRANDS = ("ほっかほっか亭", "店舗流通ネット")


def parse(html: str, *, as_of: str | None = None) -> dict:
    text = html or ""
    if not any(brand in text for brand in _BRANDS):
        raise ValueError("7561 page does not match declaration")
    return parse_branded(html, "7561", _PREFIXES, (), as_of=as_of)

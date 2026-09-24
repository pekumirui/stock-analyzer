"""ワタミ。海外事業→海外、宅食事業→宅食。国内外食は接頭辞にしない。"""
from __future__ import annotations

import re

from html_issuers._branded import parse_branded

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("海外事業", "海外"),
    ("宅食事業", "宅食"),
)
_RESET_MARKS: tuple[str, ...] = ("国内外食",)
# 事業見出しが無い四半期表は通す。他銘柄の見出しだけ落とす。
_FOREIGN_RE = re.compile(
    r"ロイヤルホスト|てんや|ほっかほっか亭|店舗流通ネット|グループ合計|ドラッグストア事業"
)


def parse(html: str, *, as_of: str | None = None) -> dict:
    if _FOREIGN_RE.search(html or ""):
        raise ValueError("7522 page does not match declaration")
    return parse_branded(html, "7522", _PREFIXES, _RESET_MARKS, as_of=as_of)

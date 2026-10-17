"""クリエイトSD。見出しは伸び率、セルは100基準。unit は yoy_index。"""
from __future__ import annotations

import re

from jobs.monthly.html_issuers._read import emit, read_page
from jobs.monthly.html_monthly import _apply_force_unit

_ASCII_WORD_RE = re.compile(r"[A-Za-z]+")


def _drop_latin_words(text: str) -> str:
    return " ".join(_ASCII_WORD_RE.sub(" ", text).split())


def parse(html: str, *, as_of: str | None = None) -> dict:
    if "伸び率" not in (html or ""):
        raise ValueError("3148 page does not match declaration")
    _sections, candidates = read_page(html)
    chosen = next((c for c in candidates if c[0] == 0), None)
    if chosen is None:
        raise ValueError("3148 page does not match declaration")
    parsed = chosen[2]
    rows = [dict(r, values=dict(r.get("values") or {})) for r in parsed["rows"]]
    for row in rows:
        row["element"] = _drop_latin_words(row["element"])
    _apply_force_unit(rows, "yoy_index")
    return emit(html, "3148", chosen, rows, as_of=as_of)

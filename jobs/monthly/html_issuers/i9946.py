"""ミニストップ。全店売上の売上高合計だけ。1店1日当りと内訳は採らない。"""
from __future__ import annotations

import re
import unicodedata

from jobs.monthly.html_issuers._read import emit, pick_scored, read_page
from jobs.monthly.html_monthly import _PAREN_NOTE_RE

# 表セルの小数（95.3）を注番号と誤認して見出しまで消さない
_NUMBERED_NOTE_RE = re.compile(r"(?<![\d.])\d{1,2}\.(?!\d)[^。]*。")
_ALL_STORE_SALES_HEAD_RE = re.compile(
    r"全店売上(?:（％）|\(%\)|％|%)?(?=※|この表|$)"
)


def _tail(section_text: str) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    s = _NUMBERED_NOTE_RE.sub("", s)
    s = _PAREN_NOTE_RE.sub("", s)
    return s[-160:] if len(s) > 160 else s


def _totals_only(rows: list[dict]) -> list[dict]:
    totals = [r for r in rows if "売上高合計" in str(r.get("element") or "")]
    return totals or rows


def parse(html: str, *, as_of: str | None = None) -> dict:
    sections, candidates = read_page(html)
    matched = [
        c
        for c in candidates
        if _ALL_STORE_SALES_HEAD_RE.search(_tail(sections[c[0]] if c[0] < len(sections) else ""))
    ]
    if not matched:
        raise ValueError("9946 page does not match declaration")
    chosen = pick_scored(html, matched)
    parsed = chosen[2]
    rows = [dict(r, values=dict(r.get("values") or {})) for r in _totals_only(parsed["rows"])]
    # 表は「全店売上」見出しで選んでいるので、そのスコープを行名に残す
    for r in rows:
        r["element"] = "全店 " + str(r.get("element") or "")
    return emit(html, "9946", chosen, rows, as_of=as_of)

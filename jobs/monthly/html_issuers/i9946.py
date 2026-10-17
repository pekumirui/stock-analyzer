"""ミニストップ。全店売上の売上高合計だけ。1店1日当りと内訳は採らない。"""
from __future__ import annotations

import re
import unicodedata

from jobs.monthly.html_issuers._read import Candidate, emit, pick_scored, read_page
from jobs.monthly.html_monthly import _PAREN_NOTE_RE, _is_fiscal_seq

# 表セルの小数（95.3）を注番号と誤認して見出しまで消さない
_NUMBERED_NOTE_RE = re.compile(r"(?<![\d.])\d{1,2}\.(?!\d)[^。]*。")
_ALL_STORE_SALES_HEAD_RE = re.compile(
    r"全店売上(?:（％）|\(%\)|％|%)?(?=※|この表|$)"
)
_ALL_STORE_SALES_ROW_MARKS = (
    "売上高合計前年比",
    "CVS前年比",
    "店内加工FF前年比",
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


def _copy_rows(parsed: dict) -> list[dict]:
    return [dict(r, values=dict(r.get("values") or {})) for r in parsed.get("rows") or []]


def _month_axis(parsed: dict) -> list[int]:
    return [m for m in (parsed.get("months") or []) if isinstance(m, int) and 1 <= m <= 12]


def _element_names(rows: list[dict]) -> list[str]:
    return [str(r.get("element") or "") for r in rows]


def _is_all_store_sales_rows(rows: list[dict]) -> bool:
    blob = "\n".join(_element_names(rows))
    return all(mark in blob for mark in _ALL_STORE_SALES_ROW_MARKS)


def _months_continue(left: list[int], right: list[int]) -> bool:
    if not left or not right or set(left) & set(right):
        return False
    union = left + [m for m in right if m not in left]
    return _is_fiscal_seq(union) and right[0] == left[-1] % 12 + 1


def _add_row_values(left: list[dict], right: list[dict]) -> None:
    by_el = {str(r.get("element") or ""): r.get("values") or {} for r in right}
    for row in left:
        extra = by_el.get(str(row.get("element") or ""))
        if not extra:
            continue
        dest = row.setdefault("values", {})
        for k, v in extra.items():
            if v is None:
                continue
            if dest.get(k) is None:
                dest[k] = v


def _merge_continuing_half(
    rows: list[dict],
    months: list[int],
    candidates: list[Candidate],
    primary_index: int,
) -> list[int]:
    """選んだ全店売上表の次の表が同じ行で月が続くなら、同じ行に足す。"""
    nxt = next((c for c in candidates if c[0] == primary_index + 1), None)
    if nxt is None:
        return months
    other = nxt[2]
    other_rows = other.get("rows") or []
    other_months = _month_axis(other)
    if not _is_all_store_sales_rows(rows) or not _is_all_store_sales_rows(other_rows):
        return months
    if _element_names(rows) != _element_names(other_rows):
        return months
    if not _months_continue(months, other_months):
        return months
    _add_row_values(rows, other_rows)
    return months + [m for m in other_months if m not in months]


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
    rows = _copy_rows(parsed)
    months = _merge_continuing_half(rows, _month_axis(parsed), candidates, chosen[0])
    parsed["months"] = months
    rows = _totals_only(rows)
    # 表は「全店売上」見出しで選んでいるので、そのスコープを行名に残す
    for r in rows:
        r["element"] = "全店 " + str(r.get("element") or "")
    return emit(html, "9946", chosen, rows, as_of=as_of)

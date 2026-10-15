"""やまや。既存店と全店を足す。卸計は採らない。当年は売上高、前年は落とす。"""
from __future__ import annotations

import re
import unicodedata

from jobs.monthly.html_issuers._read import Candidate, emit, pick_scored, read_page
from jobs.monthly.html_monthly import _apply_force_unit

_SCOPE_RE = re.compile(r"(既存店|全店)(?=(?:・卸計)?(?:\(単位|（単位|単位|$))")
_METRICS = frozenset({"売上高", "来客数", "客数", "客単価"})


def _tail(section_text: str, n: int = 80) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    return s[-n:] if len(s) > n else s


def _scope(section_text: str) -> str | None:
    tail = _tail(section_text)
    if "卸計" in tail:
        return None
    m = _SCOPE_RE.search(tail)
    if not m:
        return None
    return "既存店" if m.group(1) == "既存店" else "全店"


def _needs_scope(rows: list[dict]) -> bool:
    if not rows:
        return False
    for row in rows:
        el = str(row.get("element") or "").strip()
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", el))
        if compact not in _METRICS:
            return False
        if "既存" in el or "全店" in el:
            return False
    return True


def _apply_scope(rows: list[dict], scope: str) -> None:
    for row in rows:
        el = str(row.get("element") or "").strip()
        if not el:
            continue
        if scope in el or ("既存" in scope and "既存" in el) or (scope == "全店" and "全店" in el):
            continue
        row["element"] = f"{scope} {el}".strip()


def _merge(primary_rows: list[dict], extra: list[dict]) -> None:
    have = {(str(r.get("element") or ""), r.get("unit")) for r in primary_rows}
    for row in extra:
        el = str(row.get("element") or "")
        key = (el, row.get("unit"))
        if not el or key in have:
            continue
        primary_rows.append(row)
        have.add(key)


def parse(html: str, *, as_of: str | None = None) -> dict:
    sections, candidates = read_page(html)
    scoped: list[tuple[Candidate, str]] = []
    for cand in candidates:
        scope = _scope(sections[cand[0]] if cand[0] < len(sections) else "")
        if scope in ("既存店", "全店"):
            scoped.append((cand, scope))
    if not scoped:
        raise ValueError("9994 page does not match declaration")
    best = 2 if any(scope == "全店" for _c, scope in scoped) else 1
    pool = [c for c, scope in scoped if (2 if scope == "全店" else 1) == best]
    chosen = pick_scored(html, pool)
    scope = next(s for c, s in scoped if c[0] == chosen[0])
    rows = [dict(r, values=dict(r.get("values") or {})) for r in chosen[2]["rows"]]
    if _needs_scope(rows):
        _apply_scope(rows, scope)
    primary_months = list(chosen[2].get("months") or [])
    for cand, other_scope in scoped:
        if cand[0] == chosen[0]:
            continue
        if list(cand[2].get("months") or []) != primary_months:
            continue
        if not _needs_scope(cand[2].get("rows") or []):
            continue
        copied = [dict(r, values=dict(r.get("values") or {})) for r in cand[2]["rows"]]
        _apply_scope(copied, other_scope)
        _merge(rows, copied)
    _apply_force_unit(rows, "yoy_index")
    return emit(html, "9994", chosen, rows, as_of=as_of)

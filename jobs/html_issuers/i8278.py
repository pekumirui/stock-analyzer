"""フジ。改装の店舗面積リストは採らず、営業速報に総店舗数だけ足す。"""
from __future__ import annotations

from html_issuers._read import Candidate, emit, pick_scored, read_page
from html_monthly import _is_count_header


def _store_event(parsed: dict) -> bool:
    rows = parsed.get("rows") or []
    if not rows:
        return False
    n_area = sum(1 for r in rows if "店舗面積" in str(r.get("element") or ""))
    return n_area >= max(1, (len(rows) + 1) // 2)


def _count_only(parsed: dict) -> bool:
    rows = parsed.get("rows") or []
    if not rows:
        return False
    return all(
        r.get("unit") == "number" and _is_count_header(str(r.get("element") or ""))
        for r in rows
    )


def _has_existing(parsed: dict) -> bool:
    return any("既存店" in str(r.get("element") or "") for r in parsed.get("rows") or [])


def _merge_total_stores(rows: list[dict], candidates: list[Candidate], primary: int, months: list) -> None:
    have = {str(r.get("element") or "") for r in rows}
    for idx, _grid, other, _fy in candidates:
        if idx == primary or _store_event(other):
            continue
        if list(other.get("months") or []) != months:
            continue
        for row in other.get("rows") or []:
            el = str(row.get("element") or "")
            if "総店舗数" not in el or el in have:
                continue
            rows.append(dict(row, values=dict(row.get("values") or {})))
            have.add(el)


def parse(html: str, *, as_of: str | None = None) -> dict:
    _sections, candidates = read_page(html)
    sales = [
        c
        for c in candidates
        if not _store_event(c[2]) and not _count_only(c[2]) and _has_existing(c[2])
    ]
    if not sales:
        raise ValueError("8278 page does not match declaration")
    chosen = pick_scored(html, sales)
    parsed = chosen[2]
    rows = [dict(r, values=dict(r.get("values") or {})) for r in parsed["rows"]]
    _merge_total_stores(rows, candidates, chosen[0], list(parsed.get("months") or []))
    return emit(html, "8278", chosen, rows, as_of=as_of)

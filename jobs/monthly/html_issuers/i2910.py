"""ロック・フィールド。既存店売上と全店売上を分けて取る。見出しの単位は指数。"""
from __future__ import annotations

import re
import unicodedata

from jobs.monthly.html_issuers._page import Page
from jobs.monthly.html_issuers._read import Candidate, emit, pick_scored, read_page
from jobs.monthly.html_monthly import json_number, parse_month_cell, parse_number

_SCOPE_RE = re.compile(r"(既存店|全店)売上(?!高)")
_BRAND_YOY_SUFFIX = "売上高前年同月比"

PAGE = Page(
    code='2910',
    table=None,
    unit=None,
    rows=('RF1 売上高前年同月比',),
)


def _tail(section_text: str, n: int = 80) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    return s[-n:] if len(s) > n else s


def _scope(section_text: str) -> str | None:
    """表直前の「既存店売上」「全店売上」。卸計は対象外。"""
    tail = _tail(section_text)
    if "卸計" in tail:
        return None
    found: list[tuple[int, str]] = []
    for m in _SCOPE_RE.finditer(tail):
        found.append((m.start(), "既存店" if m.group(1) == "既存店" else "全店"))
    if not found:
        return None
    found.sort()
    return found[-1][1]


def _brand_yoy_base(element: str) -> str | None:
    """業態名 + ページ由来の売上高前年同月比。指標行そのものは対象外。"""
    el = str(element or "").strip()
    if not el.endswith(_BRAND_YOY_SUFFIX):
        return None
    base = el[: -len(_BRAND_YOY_SUFFIX)].strip()
    if not base or "売上" in base or "既存" in base or "全店" in base:
        return None
    return base


def _needs_brand_scope(rows: list[dict]) -> bool:
    """業態名の行だけ。裸の売上高/来客数は別経路。"""
    if not rows:
        return False
    return all(_brand_yoy_base(str(r.get("element") or "")) is not None for r in rows)


def _apply_scope(rows: list[dict], scope: str) -> None:
    for row in rows:
        el = str(row.get("element") or "").strip()
        if not el:
            continue
        if scope in el or ("既存" in scope and "既存" in el) or (scope == "全店" and "全店" in el):
            continue
        row["element"] = f"{scope} {el}".strip()


def _copy_rows(rows: list[dict]) -> list[dict]:
    return [dict(r, values=dict(r.get("values") or {})) for r in rows]


def _axis_label(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip()
    return re.sub(r"\s+", "", s)


def _month_cols(grid: list[list[str]]) -> tuple[int, list[tuple[int, int]]] | None:
    for i, row in enumerate(grid):
        cols: list[tuple[int, int]] = []
        for c, cell in enumerate(row):
            month = parse_month_cell(cell)
            if month is not None:
                cols.append((c, month))
        if len(cols) >= 3:
            return i, cols
    return None


def _company_total_row(grid: list[list[str]], scope: str) -> dict | None:
    """表の最下行 `合計` は会社計。parse_grid は合計を軸と見て落とす。"""
    found = _month_cols(grid)
    if found is None:
        return None
    header_i, month_cols = found
    for row in grid[header_i + 1 :]:
        if not row or _axis_label(row[0]) != "合計":
            continue
        values: dict[str, int | float] = {}
        for c, month in month_cols:
            if c >= len(row):
                continue
            n = parse_number(row[c])
            if n is None:
                continue
            values[str(month)] = json_number(n)
        if not values:
            return None
        return {
            "element": f"{scope} 売上高前年同月比",
            "unit": "yoy_index",
            "values": values,
        }
    return None


def _with_company_total(rows: list[dict], grid: list[list[str]], scope: str) -> list[dict]:
    total = _company_total_row(grid, scope)
    if total is None:
        return rows
    key = (str(total.get("element") or ""), total.get("unit"))
    have = {(str(r.get("element") or ""), r.get("unit")) for r in rows}
    if key in have:
        return rows
    return [total, *rows]


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
        if scope not in ("既存店", "全店"):
            continue
        if not _needs_brand_scope(cand[2].get("rows") or []):
            continue
        scoped.append((cand, scope))
    if not scoped:
        return PAGE.parse(html, as_of=as_of)
    chosen = pick_scored(html, [c for c, _s in scoped])
    scope = next(s for c, s in scoped if c[0] == chosen[0])
    rows = _copy_rows(chosen[2]["rows"])
    _apply_scope(rows, scope)
    rows = _with_company_total(rows, chosen[1], scope)
    primary_months = list(chosen[2].get("months") or [])
    for cand, other_scope in scoped:
        if cand[0] == chosen[0]:
            continue
        if list(cand[2].get("months") or []) != primary_months:
            continue
        copied = _copy_rows(cand[2]["rows"])
        _apply_scope(copied, other_scope)
        copied = _with_company_total(copied, cand[1], other_scope)
        _merge(rows, copied)
    payload = emit(html, "2910", chosen, rows, as_of=as_of)
    blob = "\n".join(str(r.get("element") or "") for r in payload["rows"])
    if not any(token in blob for token in PAGE.rows):
        raise ValueError("2910 page does not match declaration")
    return payload

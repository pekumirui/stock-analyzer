"""神戸物産。上期と下期を同じ行に足す。店舗仕入れは仕入れ。全店は付けない。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import (
    _count_only,
    _merge_total_stores,
    _prefer_section,
    _store_event,
)
from jobs.monthly.html_issuers._read import Candidate, emit, pick_scored, read_page
from jobs.monthly.html_monthly import _section_heading_tail

_OTHER_STATEMENT = ("売上総利益", "営業利益", "経常利益")


def _is_sales_table(parsed: dict) -> bool:
    rows = parsed.get("rows") or []
    if not rows:
        return False
    lead = str(rows[0].get("element") or "").strip()
    if any(name in lead for name in _OTHER_STATEMENT):
        return False
    token = lead.split()[0] if lead else ""
    return token == "売上高"


def _is_purchase_section(section_text: str) -> bool:
    """表直前が見出し「…仕入れ前年対比」。脚注の商品出荷は見ない。"""
    tail = _section_heading_tail(section_text, 48)
    return tail.endswith(("仕入れ前年対比", "仕入れ前年同月比"))


def _insert_purchase_label(element: str) -> str:
    """エリアの直後、スコープの前に仕入れを置く。"""
    el = str(element or "").strip()
    if not el or "仕入れ" in el:
        return el
    parts = el.split()
    for i, tok in enumerate(parts):
        if "既存" in tok or "全店" in tok:
            parts.insert(i, "仕入れ")
            return " ".join(parts)
    return f"{el} 仕入れ"


def _merge_purchases(
    rows: list[dict],
    candidates: list[Candidate],
    sections: list[str],
    primary: int,
    months: list[int],
) -> None:
    """仕入れ前年対比の表を足す。セグメントは仕入れ。"""
    have = {(str(r.get("element") or ""), r.get("unit")) for r in rows}
    for idx, _grid, other, _fy in candidates:
        if idx == primary or _store_event(other):
            continue
        if list(other.get("months") or []) != months:
            continue
        text = sections[idx] if idx < len(sections) else ""
        if not _is_purchase_section(text):
            continue
        for row in other.get("rows") or []:
            el = _insert_purchase_label(str(row.get("element") or ""))
            key = (el, row.get("unit"))
            if not el or key in have:
                continue
            copied = dict(row)
            copied["element"] = el
            copied["values"] = dict(row.get("values") or {})
            rows.append(copied)
            have.add(key)


def parse(html: str, *, as_of: str | None = None) -> dict:
    sections, candidates = read_page(html)
    monthly = [c for c in candidates if not _store_event(c[2])]
    sales = [c for c in monthly if not _count_only(c[2]) and _is_sales_table(c[2])]
    if not sales:
        raise ValueError("3038 page does not match declaration")
    pool = _prefer_section(sales, sections)
    chosen = pick_scored(html, pool)
    parsed = chosen[2]
    rows = [dict(r, values=dict(r.get("values") or {})) for r in parsed["rows"]]
    months = list(parsed.get("months") or [])
    primary = {"months": months, "rows": rows}
    _merge_total_stores(primary, candidates, primary_index=chosen[0])
    _merge_purchases(rows, candidates, sections, chosen[0], months)
    return emit(html, "3038", chosen, rows, as_of=as_of)

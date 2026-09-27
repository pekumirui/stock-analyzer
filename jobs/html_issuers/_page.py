"""宣言した表・行・単位で月次を読む。宣言に無いページは ValueError。"""
from __future__ import annotations

from dataclasses import dataclass

from html_monthly import (
    _apply_force_unit,
    _drop_ytd_when_monthly_pair,
    _is_count_header,
    _parsed_score,
    _section_pick_rank,
    _sticky_section_fy,
    as_of_from_table,
    drop_trailing_empty_months,
    page_context,
    parse_as_of_text,
    parse_grid,
    parse_month_year,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)
from tdnet_monthly_r2 import display_code

Candidate = tuple[int, list[list[str]], dict, tuple[int, int] | None]


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


def _grid_has_cell_years(grid: list[list[str]]) -> bool:
    """セルに `YYYY年M月` がある表。ページ全体の現在日より表の年を使う。"""
    for row in grid:
        for cell in row:
            year, _month = parse_month_year(cell)
            if year is not None:
                return True
    return False


def _table_as_of(
    html: str,
    grid: list[list[str]],
    parsed: dict,
    section_fy: tuple[int, int] | None,
) -> str | None:
    return as_of_from_table(
        grid, html, parsed.get("rows") or [], parsed.get("months"), section_fy
    )


def _merge_total_stores(
    primary: dict,
    candidates: list[Candidate],
    *,
    primary_index: int,
) -> None:
    primary_months = list(primary.get("months") or [])
    have = {str(r.get("element") or "") for r in primary.get("rows") or []}
    for idx, _grid, other, _fy in candidates:
        if idx == primary_index or _store_event(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        for row in other.get("rows") or []:
            el = str(row.get("element") or "")
            if "総店舗数" not in el or el in have:
                continue
            primary.setdefault("rows", []).append(row)
            have.add(el)


def _merge_count_companions(
    primary: dict,
    candidates: list[Candidate],
    *,
    primary_index: int,
    html: str,
) -> None:
    """同じ月軸・同じ表内年の店舗数表を売上表へ足す。ページ全体の現在日は見ない。"""
    by_idx = {c[0]: c for c in candidates}
    primary_c = by_idx.get(primary_index)
    if primary_c is None:
        return
    _i, primary_grid, _p, primary_fy = primary_c
    primary_as_of = _table_as_of(html, primary_grid, primary, primary_fy)
    if not primary_as_of:
        return
    primary_months = list(primary.get("months") or [])
    have = {
        (str(r.get("element") or ""), r.get("unit")) for r in primary.get("rows") or []
    }
    for idx, grid, other, fy in candidates:
        if idx == primary_index or _store_event(other):
            continue
        if not _count_only(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        if _table_as_of(html, grid, other, fy) != primary_as_of:
            continue
        for row in other.get("rows") or []:
            el = str(row.get("element") or "")
            key = (el, row.get("unit"))
            if not el or key in have:
                continue
            primary.setdefault("rows", []).append(
                dict(row, values=dict(row.get("values") or {}))
            )
            have.add(key)


def _prefer_section(candidates: list[Candidate], sections: list[str]) -> list[Candidate]:
    if not candidates:
        return candidates
    ranks = [
        _section_pick_rank(sections[i] if i < len(sections) else "")
        for i, _g, _p, _fy in candidates
    ]
    best = max(ranks)
    return [c for c, r in zip(candidates, ranks) if r == best]


@dataclass(frozen=True)
class Page:
    """table は None なら全表。rows のいずれかが行名に含まれること。unit で yoy を上書きする。"""

    code: str
    table: int | None
    unit: str | None
    rows: tuple[str, ...]

    def parse(self, html: str, *, as_of: str | None = None) -> dict:
        payload = _select(html, self.code, table=self.table, force_unit=self.unit, as_of=as_of)
        blob = "\n".join(str(r.get("element") or "") for r in payload["rows"])
        if not any(token in blob for token in self.rows):
            raise ValueError(f"{self.code} page does not match declaration")
        return payload


def _select(
    html: str,
    code: str,
    *,
    as_of: str | None,
    table: int | None,
    force_unit: str | None,
) -> dict:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    section_texts = table_section_texts(html)
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    if table is None:
        indices = list(range(len(tables)))
    else:
        if table < 0 or table >= len(tables):
            raise ValueError(f"table index {table} out of range ({len(tables)})")
        indices = [table]
    last_err: str | None = None
    sticky_fy = _sticky_section_fy(section_texts)
    candidates: list[Candidate] = []
    for i in indices:
        section_text = section_texts[i] if i < len(section_texts) else ""
        ctx = " ".join(p for p in (base_ctx, section_text, unit_marks) if p)
        try:
            parsed = parse_grid(tables[i], ctx, html)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if parsed and parsed.get("rows"):
            section_fy = sticky_fy[i] if i < len(sticky_fy) else None
            if section_fy is None:
                section_fy = section_fy_end_from_text(section_text, parsed.get("months"))
            candidates.append((i, tables[i], parsed, section_fy))
            if table is not None:
                break
    if not candidates:
        raise ValueError(last_err or "no monthly rows")
    monthly_cands = [c for c in candidates if not _store_event(c[2])]
    sales_cands = [c for c in monthly_cands if not _count_only(c[2])]
    pick_pool = _prefer_section(sales_cands or monthly_cands or candidates, section_texts)
    table_index, grid_used, parsed, section_fy = max(
        pick_pool,
        key=lambda it: _parsed_score(html, it[1], it[2], table_index=it[0], section_fy=it[3]),
    )
    _merge_total_stores(parsed, candidates, primary_index=table_index)
    _merge_count_companions(
        parsed, candidates, primary_index=table_index, html=html
    )
    derived = _table_as_of(html, grid_used, parsed, section_fy)
    as_of_s = resolve_as_of(
        html, grid_used, parsed["rows"], as_of, parsed.get("months"), section_fy
    )
    # 行に YYYY年M月 がある表は、ページ全体の「現在」が別の年なら表の年を使う
    page_as_of = parse_as_of_text(html)
    if (
        derived
        and _grid_has_cell_years(grid_used)
        and page_as_of
        and page_as_of[:4] != derived[:4]
    ):
        as_of_s = derived
    _apply_force_unit(parsed["rows"], force_unit)
    parsed["rows"] = _drop_ytd_when_monthly_pair(parsed["rows"])
    months = drop_trailing_empty_months(list(parsed.get("months") or []), parsed["rows"])
    return {
        "code": display_code(code),
        "months": months,
        "rows": parsed["rows"],
        "as_of": as_of_s,
    }

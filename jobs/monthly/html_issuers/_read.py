"""表を読んで候補にするだけ。銘柄の照合は各 i<code>.py。"""
from __future__ import annotations

from jobs.monthly.html_monthly import (
    _drop_ytd_when_monthly_pair,
    _parsed_score,
    _sticky_section_fy,
    drop_trailing_empty_months,
    page_context,
    parse_grid,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)
from jobs.monthly.tdnet_monthly_r2 import display_code

Candidate = tuple[int, list[list[str]], dict, tuple[int, int] | None]


def read_page(html: str) -> tuple[list[str], list[Candidate]]:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    section_texts = table_section_texts(html)
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    sticky_fy = _sticky_section_fy(section_texts)
    last_err: str | None = None
    candidates: list[Candidate] = []
    for i, grid in enumerate(tables):
        section_text = section_texts[i] if i < len(section_texts) else ""
        ctx = " ".join(p for p in (base_ctx, section_text, unit_marks) if p)
        try:
            parsed = parse_grid(grid, ctx, html)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if not parsed or not parsed.get("rows"):
            continue
        section_fy = sticky_fy[i] if i < len(sticky_fy) else None
        if section_fy is None:
            section_fy = section_fy_end_from_text(section_text, parsed.get("months"))
        candidates.append((i, grid, parsed, section_fy))
    if not candidates:
        raise ValueError(last_err or "no monthly rows")
    return section_texts, candidates


def pick_scored(html: str, pool: list[Candidate]) -> Candidate:
    return max(
        pool,
        key=lambda it: _parsed_score(
            html, it[1], it[2], table_index=it[0], section_fy=it[3]
        ),
    )


def emit(
    html: str,
    code: str,
    chosen: Candidate,
    rows: list[dict],
    *,
    as_of: str | None = None,
) -> dict:
    _idx, grid, parsed, section_fy = chosen
    as_of_s = resolve_as_of(
        html, grid, rows, as_of, parsed.get("months"), section_fy
    )
    rows = _drop_ytd_when_monthly_pair(rows)
    months = drop_trailing_empty_months(list(parsed.get("months") or []), rows)
    return {
        "code": display_code(code),
        "months": months,
        "rows": rows,
        "as_of": as_of_s,
    }

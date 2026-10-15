"""8179 だけ。同じ事業の上期と下期を一つの月軸に足す。"""
from __future__ import annotations

from datetime import date

from jobs.monthly.html_issuers._branded import (
    Candidate,
    Section,
    _Series,
    _grid_self_dated,
    _is_store_event_parsed,
    _member_months,
    _union_ingest_months,
)
from jobs.monthly.html_monthly import _is_fiscal_seq, as_of_from_table, last_filled_month, year_for_month


def _tileable(members: list[Candidate], html: str) -> _Series | None:
    """Same-segment halves of one fiscal run. Self-dated year-in-cell tables never tile."""
    if len(members) < 2:
        return None
    if any(_grid_self_dated(grid) for _idx, grid, _parsed, _fy in members):
        return None
    month_sets: list[set[int]] = []
    as_ofs: list[str] = []
    for _idx, grid, parsed, section_fy in members:
        month_sets.append(set(_member_months(parsed)))
        derived = as_of_from_table(
            grid, html, parsed.get("rows") or [], parsed.get("months"), section_fy
        )
        if derived:
            as_ofs.append(derived)
    for i, left in enumerate(month_sets):
        for right in month_sets[i + 1 :]:
            if left & right:
                return None
    union = _union_ingest_months([m for s in month_sets for m in s])
    if not _is_fiscal_seq(union):
        return None
    if not as_ofs:
        return None
    series_as_of = max(as_ofs)
    series_date = date.fromisoformat(series_as_of)
    for _idx, grid, parsed, section_fy in members:
        last_m = last_filled_month(parsed.get("rows") or [], parsed.get("months"))
        if last_m is None:
            continue
        member_as = as_of_from_table(
            grid, html, parsed.get("rows") or [], parsed.get("months"), section_fy
        )
        if member_as is None:
            continue
        window_y = year_for_month(last_m, (series_date.year, series_date.month), None)
        if date.fromisoformat(member_as).year != window_y:
            return None
    return _Series(
        indices=tuple(idx for idx, _g, _p, _fy in members),
        months=tuple(union),
        as_of=series_as_of,
    )


def tile_segment_series(
    candidates: list[Candidate],
    sections: list[Section],
    html: str,
) -> list[_Series]:
    by_seg: dict[str, list[Candidate]] = {}
    for cand in candidates:
        idx, _grid, parsed, _fy = cand
        if _is_store_event_parsed(parsed):
            continue
        seg = sections[idx].segment if idx < len(sections) else None
        if not seg:
            continue
        by_seg.setdefault(seg, []).append(cand)
    out: list[_Series] = []
    for members in by_seg.values():
        tiled = _tileable(members, html)
        if tiled:
            out.append(tiled)
            continue
        for idx, grid, parsed, section_fy in members:
            derived = as_of_from_table(
                grid, html, parsed.get("rows") or [], parsed.get("months"), section_fy
            )
            out.append(
                _Series(
                    (idx,),
                    tuple(_union_ingest_months(_member_months(parsed))),
                    derived,
                )
            )
    return out

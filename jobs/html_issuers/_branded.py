"""事業見出しを接頭辞にする月次（7522 / 7561 / 8179）。上期∪下期は 8179 だけ。"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from html_monthly import (
    _SECTION_LOOKBACK,
    _apply_force_unit,
    _drop_ytd_when_monthly_pair,
    _is_count_header,
    _is_fiscal_seq,
    _parsed_score,
    _section_pick_rank,
    _sticky_section_fy,
    as_of_from_table,
    drop_trailing_empty_months,
    normalize_month_order,
    page_context,
    parse_grid,
    parse_month_year,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)
from tdnet_monthly_r2 import display_code

_SCOPED_STORE_UNIT_HEAD_RE = re.compile(
    r"(既存店|全店)(?=(?:・卸計)?(?:\(単位|（単位|単位|$))"
)
# Royal form: （既存店前年比）. Scope is inside the parens.
_SCOPED_STORE_YOY_HEAD_RE = re.compile(
    r"[（(](既存店|全店)[^）)]*前年(?:同月)?比[）)]"
)
_SCOPE_METRICS = frozenset({"売上高", "来客数", "客数", "客単価"})

Candidate = tuple[int, list[list[str]], dict, tuple[int, int] | None]


@dataclass(frozen=True)
class Section:
    """Heading band above a table. Sticky across unheaded continuation tables."""

    segment: str | None
    scope: str | None


@dataclass(frozen=True)
class _Series:
    """Same-segment tables on one month axis."""

    indices: tuple[int, ...]
    months: tuple[int, ...]
    as_of: str | None


SeriesFn = Callable[[list[Candidate], list[Section], str], list[_Series]]


def _is_store_event_parsed(parsed: dict) -> bool:
    """開店/閉店/改装の店名×店舗面積リスト。月次速報ではない。"""
    rows = parsed.get("rows") or []
    if not rows:
        return False
    n_area = sum(1 for r in rows if "店舗面積" in str(r.get("element") or ""))
    return n_area >= max(1, (len(rows) + 1) // 2)


def _is_count_only_parsed(parsed: dict) -> bool:
    rows = parsed.get("rows") or []
    if not rows:
        return False
    return all(
        r.get("unit") == "number" and _is_count_header(str(r.get("element") or ""))
        for r in rows
    )


def _section_tail(section_text: str, n: int = 160) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    return s[-n:] if len(s) > n else s


def _section_mark_last(tail: str, marks: tuple[str, ...]) -> int:
    last = -1
    for mark in marks:
        i = tail.rfind(mark)
        if i > last:
            last = i
    return last


def _section_segment(
    section_text: str,
    prefixes: tuple[tuple[str, str], ...],
    reset_marks: tuple[str, ...],
) -> str | None:
    """表直前見出しの事業。右端の見出しだけ。reset がそれより右なら付けない。"""
    tail = _section_tail(section_text, _SECTION_LOOKBACK)
    last_i = -1
    last_prefix: str | None = None
    for mark, prefix in prefixes:
        i = tail.rfind(mark)
        if i > last_i:
            last_i = i
            last_prefix = prefix
    if last_prefix is None:
        return None
    if _section_mark_last(tail, reset_marks) > last_i:
        return None
    return last_prefix


def _store_scope_from_section(section_text: str) -> str | None:
    """表直前の見出し帯だけ。卸計は対象外。"""
    tail = _section_tail(section_text, 80)
    if "卸計" in tail:
        return None
    m = _SCOPED_STORE_UNIT_HEAD_RE.search(tail) or _SCOPED_STORE_YOY_HEAD_RE.search(tail)
    if not m:
        return None
    return "既存店" if m.group(1) == "既存店" else "全店"


def _rows_need_store_scope(rows: list[dict]) -> bool:
    """裸のスコープ層指標（売上高/来客数/客数/客単価）だけスコープを付ける。"""
    if not rows:
        return False
    for row in rows:
        el = str(row.get("element") or "").strip()
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", el))
        if compact not in _SCOPE_METRICS:
            return False
        if "既存" in el or "全店" in el:
            return False
    return True


def _apply_store_scope(rows: list[dict], scope: str) -> None:
    for row in rows:
        el = str(row.get("element") or "").strip()
        if not el:
            continue
        if scope in el or ("既存" in scope and "既存" in el) or (scope == "全店" and "全店" in el):
            continue
        row["element"] = f"{scope} {el}".strip()


def _sticky_sections(
    section_texts: list[str],
    prefixes: tuple[tuple[str, str], ...],
    reset_marks: tuple[str, ...],
) -> list[Section]:
    """半年表は直前の事業見出しとスコープを引き継ぐ。reset 見出しで切る。"""
    out: list[Section] = []
    prev = Section(None, None)
    brand_marks = tuple(mark for mark, _prefix in prefixes)
    for text in section_texts:
        seg = _section_segment(text, prefixes, reset_marks)
        scope = _store_scope_from_section(text)
        tail = _section_tail(text, _SECTION_LOOKBACK)
        reset = _section_mark_last(tail, reset_marks) > _section_mark_last(tail, brand_marks)
        if seg:
            cur_scope = scope if scope or seg != prev.segment else prev.scope
            cur = Section(seg, cur_scope)
        elif reset:
            cur = Section(None, scope)
        elif prev.segment:
            cur = Section(prev.segment, scope or prev.scope)
        else:
            cur = Section(None, scope)
        out.append(cur)
        prev = cur
    return out


def _apply_section_segment(rows: list[dict], segment: str | None) -> None:
    if not segment:
        return
    for row in rows:
        el = str(row.get("element") or "").strip()
        if not el:
            continue
        if el == segment or el.startswith(segment + " "):
            continue
        row["element"] = f"{segment} {el}".strip()


def _brand_rank(
    section_text: str,
    segment: str | None,
    prefixes: tuple[tuple[str, str], ...],
    reset_marks: tuple[str, ...],
) -> int:
    if segment or _section_segment(section_text, prefixes, reset_marks):
        return 0
    return _section_pick_rank(section_text)


def _prefer_branded(
    candidates: list[Candidate],
    section_texts: list[str],
    sections: list[Section],
    prefixes: tuple[tuple[str, str], ...],
    reset_marks: tuple[str, ...],
) -> list[Candidate]:
    if not candidates:
        return candidates
    ranks = []
    for i, _g, _p, _fy in candidates:
        text = section_texts[i] if i < len(section_texts) else ""
        seg = sections[i].segment if i < len(sections) else None
        ranks.append(_brand_rank(text, seg, prefixes, reset_marks))
    best = max(ranks)
    return [c for c, r in zip(candidates, ranks) if r == best]


def _drop_all_store_sales_breakdowns(rows: list[dict], section_text: str) -> list[dict]:
    """全店売上セクションで売上高合計があるときだけ、内訳行（CVS/FF）を落とす。"""
    if "全店売上" not in _section_tail(section_text):
        return rows
    totals = [r for r in rows if "売上高合計" in str(r.get("element") or "")]
    return totals or rows


def _merge_companion_total_stores(
    primary: dict,
    candidates: list[Candidate],
    *,
    primary_index: int,
) -> None:
    primary_months = list(primary.get("months") or [])
    have = {str(r.get("element") or "") for r in primary.get("rows") or []}
    for idx, _grid, other, _fy in candidates:
        if idx == primary_index or _is_store_event_parsed(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        for row in other.get("rows") or []:
            el = str(row.get("element") or "")
            if "総店舗数" not in el:
                continue
            if el in have:
                continue
            primary.setdefault("rows", []).append(row)
            have.add(el)


def _merge_companion_store_scopes(
    primary: dict,
    candidates: list[Candidate],
    sections: list[Section],
    *,
    primary_index: int,
) -> None:
    primary_months = list(primary.get("months") or [])
    have = {
        (str(r.get("element") or ""), r.get("unit")) for r in primary.get("rows") or []
    }
    for idx, _grid, other, _fy in candidates:
        if idx == primary_index or _is_store_event_parsed(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        scope = sections[idx].scope if idx < len(sections) else None
        if scope is None or not _rows_need_store_scope(other.get("rows") or []):
            continue
        for row in other.get("rows") or []:
            merged = dict(row)
            _apply_store_scope([merged], scope)
            el = str(merged.get("element") or "")
            key = (el, merged.get("unit"))
            if not el or key in have:
                continue
            primary.setdefault("rows", []).append(merged)
            have.add(key)


def _union_ingest_months(months: list[int]) -> list[int]:
    seen: list[int] = []
    for m in months:
        if isinstance(m, int) and 1 <= m <= 12 and m not in seen:
            seen.append(m)
    cal = sorted(seen)
    if _is_fiscal_seq(cal):
        return cal
    wrapped = normalize_month_order(seen)
    if _is_fiscal_seq(wrapped):
        return wrapped
    if 1 in seen:
        return cal
    return wrapped


def _valued_months(rows: list[dict]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for row in rows:
        for k in row.get("values") or {}:
            try:
                m = int(k)
            except (TypeError, ValueError):
                continue
            if 1 <= m <= 12 and m not in seen:
                seen.add(m)
                out.append(m)
    return out


def _member_months(parsed: dict) -> list[int]:
    months = [m for m in (parsed.get("months") or []) if isinstance(m, int) and 1 <= m <= 12]
    return months or _valued_months(parsed.get("rows") or [])


def _grid_self_dated(grid: list[list[str]]) -> bool:
    for row in grid:
        for cell in row:
            year, _month = parse_month_year(cell)
            if year is not None:
                return True
    return False


def untiled_series(
    candidates: list[Candidate],
    sections: list[Section],
    html: str,
) -> list[_Series]:
    """各表は独立。重なる月やセルに年がある表は一つにしない。"""
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


def _merge_other_section_companions(
    primary: dict,
    candidates: list[Candidate],
    *,
    primary_index: int,
    sections: list[Section],
    html: str,
    primary_as_of: str | None,
    series_fn: SeriesFn,
) -> None:
    """同じ as_of のブランド系列を足す。"""
    have = {
        (str(r.get("element") or ""), r.get("unit")): r for r in primary.get("rows") or []
    }
    extra_months: list[int] = []
    by_idx = {c[0]: c for c in candidates}
    for series in series_fn(candidates, sections, html):
        if primary_as_of and series.as_of != primary_as_of:
            continue
        for idx in series.indices:
            if idx == primary_index:
                continue
            other_c = by_idx.get(idx)
            if other_c is None:
                continue
            _i, _grid, other, _fy = other_c
            if _is_store_event_parsed(other):
                continue
            tag = sections[idx] if idx < len(sections) else Section(None, None)
            rows = [
                dict(row, values=dict(row.get("values") or {})) for row in other.get("rows") or []
            ]
            if tag.scope and _rows_need_store_scope(rows):
                _apply_store_scope(rows, tag.scope)
            _apply_section_segment(rows, tag.segment)
            for merged in rows:
                el = str(merged.get("element") or "")
                key = (el, merged.get("unit"))
                if not el:
                    continue
                existing = have.get(key)
                if existing is not None:
                    vals = existing.setdefault("values", {})
                    for mk, mv in merged["values"].items():
                        vals.setdefault(mk, mv)
                    continue
                primary.setdefault("rows", []).append(merged)
                have[key] = merged
            extra_months.extend(_valued_months(other.get("rows") or []))
    if extra_months:
        primary["months"] = _union_ingest_months(
            [*_valued_months(primary.get("rows") or []), *extra_months]
        )


def parse_branded(
    html: str,
    code: str,
    prefixes: tuple[tuple[str, str], ...],
    reset_marks: tuple[str, ...],
    *,
    as_of: str | None = None,
    force_unit: str | None = None,
    series_fn: SeriesFn | None = None,
) -> dict:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    section_texts = table_section_texts(html)
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    last_err: str | None = None
    sticky_fy = _sticky_section_fy(section_texts)
    candidates: list[Candidate] = []
    for i, grid in enumerate(tables):
        section_text = section_texts[i] if i < len(section_texts) else ""
        ctx = " ".join(p for p in (base_ctx, section_text, unit_marks) if p)
        try:
            parsed = parse_grid(grid, ctx, html)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if parsed and parsed.get("rows"):
            section_fy = sticky_fy[i] if i < len(sticky_fy) else None
            if section_fy is None:
                section_fy = section_fy_end_from_text(section_text, parsed.get("months"))
            candidates.append((i, grid, parsed, section_fy))
    if not candidates:
        raise ValueError(last_err or "no monthly rows")
    monthly_cands = [c for c in candidates if not _is_store_event_parsed(c[2])]
    sales_cands = [c for c in monthly_cands if not _is_count_only_parsed(c[2])]
    pick_pool = sales_cands or monthly_cands or candidates
    sections = _sticky_sections(section_texts, prefixes, reset_marks)
    pick_pool = _prefer_branded(pick_pool, section_texts, sections, prefixes, reset_marks)
    table_index, grid_used, parsed, section_fy = max(
        pick_pool,
        key=lambda it: _parsed_score(html, it[1], it[2], table_index=it[0], section_fy=it[3]),
    )
    section_text = section_texts[table_index] if table_index < len(section_texts) else ""
    scope = sections[table_index].scope if table_index < len(sections) else None
    if scope and _rows_need_store_scope(parsed["rows"]):
        _apply_store_scope(parsed["rows"], scope)
    _merge_companion_total_stores(parsed, candidates, primary_index=table_index)
    _merge_companion_store_scopes(parsed, candidates, sections, primary_index=table_index)
    _apply_section_segment(
        parsed["rows"],
        sections[table_index].segment if table_index < len(sections) else None,
    )
    as_of_s = resolve_as_of(
        html,
        grid_used,
        parsed["rows"],
        as_of,
        parsed.get("months"),
        section_fy,
    )
    _merge_other_section_companions(
        parsed,
        monthly_cands,
        primary_index=table_index,
        sections=sections,
        html=html,
        primary_as_of=as_of_s,
        series_fn=series_fn or untiled_series,
    )
    _apply_force_unit(parsed["rows"], force_unit)
    parsed["rows"] = _drop_ytd_when_monthly_pair(parsed["rows"])
    parsed["rows"] = _drop_all_store_sales_breakdowns(parsed["rows"], section_text)
    months = drop_trailing_empty_months(list(parsed.get("months") or []), parsed["rows"])
    return {
        "code": display_code(code),
        "months": months,
        "rows": parsed["rows"],
        "as_of": as_of_s,
    }

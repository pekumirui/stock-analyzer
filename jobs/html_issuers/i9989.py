"""サンドラッグ。グループ合計を主、DG/DS は接頭辞、総店舗数は number。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from html_monthly import (
    _TAG_RE,
    _detect_col_header,
    _drop_ytd_when_monthly_pair,
    _first_data_row,
    drop_trailing_empty_months,
    last_filled_month,
    page_context,
    parse_grid,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)
from tdnet_monthly_r2 import display_code

_GROUP_RE = re.compile(r"^グループ合計")
_DRUG_RE = re.compile(r"^ドラッグストア事業")
_DISCOUNT_RE = re.compile(r"^ディスカウントストア事業")
_STORES_RE = re.compile(r"総店舗数")
_CLAIM_RE = re.compile(r"ドラッグストア事業|ディスカウントストア事業")
_TABLES_LO = 12
_TABLES_HI = 40


@dataclass(frozen=True)
class _Role:
    name: str
    header_re: re.Pattern[str] | None
    label_re: re.Pattern[str] | None
    segment: str | None
    unit: str
    primary: bool = False


_ROLES: tuple[_Role, ...] = (
    _Role("group", _GROUP_RE, None, None, "yoy_pct", primary=True),
    _Role("drug", _DRUG_RE, None, "ドラッグストア事業", "yoy_pct"),
    _Role("discount", _DISCOUNT_RE, None, "ディスカウントストア事業", "yoy_pct"),
    _Role("stores", None, _STORES_RE, None, "number"),
)


@dataclass(frozen=True)
class _Fact:
    index: int
    caption: str
    section: str
    header_cells: tuple[str, ...]
    label_cells: tuple[str, ...]
    months: tuple[int, ...]
    fy_end: tuple[int, int] | None
    last_filled: int | None
    grid: tuple[tuple[str, ...], ...]


class _CaptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captions: list[str] = []
        self._in_table = 0
        self._in_caption = False
        self._text: list[str] = []
        self._got = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._got = False
        elif tag == "caption" and self._in_table == 1 and not self._got:
            self._in_caption = True
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "caption" and self._in_caption:
            self.captions.append(_plain("".join(self._text)))
            self._in_caption = False
            self._got = True
        elif tag == "table" and self._in_table:
            if self._in_table == 1 and not self._got:
                self.captions.append("")
            self._in_table -= 1
            self._in_caption = False

    def handle_data(self, data: str) -> None:
        if self._in_caption:
            self._text.append(data)


def _plain(html_frag: str) -> str:
    t = _TAG_RE.sub(" ", html_frag or "")
    return re.sub(r"\s+", " ", t).strip()


def _table_captions(html: str, n: int) -> list[str]:
    parser = _CaptionParser()
    parser.feed(html or "")
    parser.close()
    out = list(parser.captions)
    if len(out) < n:
        out.extend([""] * (n - len(out)))
    return out[:n]


def _cells(grid: list[list[str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    header_i = _detect_col_header(grid)
    data_i = _first_data_row(grid)
    if header_i is not None:
        header = tuple(grid[header_i]) if header_i < len(grid) else ()
        labels = tuple((row[0] if row else "") for row in grid[header_i + 1 :] if row)
        return header, labels
    if data_i is not None:
        header = tuple(c for row in grid[:data_i] for c in row)
        labels = tuple((row[0] if row else "") for row in grid[data_i:] if row)
        return header, labels
    header = tuple(grid[0]) if grid else ()
    return header, ()


def _facts(html: str) -> list[_Fact]:
    grids = parse_tables(html)
    sections = table_section_texts(html)
    captions = _table_captions(html, len(grids))
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    prev_fy: tuple[int, int] | None = None
    out: list[_Fact] = []
    for i, grid in enumerate(grids):
        section = sections[i] if i < len(sections) else ""
        ctx = " ".join(p for p in (base_ctx, section, unit_marks) if p)
        header, labels = _cells(grid)
        parsed = None
        try:
            parsed = parse_grid(grid, ctx, html)
        except Exception:
            parsed = None
        months = tuple(parsed.get("months") or []) if parsed else ()
        rows = parsed.get("rows") or [] if parsed else []
        last_m = last_filled_month(rows, list(months)) if rows else None
        fy = section_fy_end_from_text(section, list(months) if months else None)
        # 見出しの無い表は直前の表の期末を引き継ぐ。
        if fy is None:
            fy = prev_fy
        else:
            prev_fy = fy
        out.append(
            _Fact(
                index=i,
                caption=captions[i] if i < len(captions) else "",
                section=section,
                header_cells=header,
                label_cells=labels,
                months=months,
                fy_end=fy,
                last_filled=last_m,
                grid=tuple(tuple(r) for r in grid),
            )
        )
    return out


def _matches(role: _Role, fact: _Fact) -> bool:
    if role.header_re is not None and not any(
        role.header_re.search(cell or "") for cell in fact.header_cells
    ):
        return False
    if role.label_re is not None and not any(
        role.label_re.search(cell or "") for cell in fact.label_cells
    ):
        return False
    return role.header_re is not None or role.label_re is not None


def _latest_key(fact: _Fact) -> tuple[int, int, int, int]:
    fy_y, fy_m = fact.fy_end if fact.fy_end is not None else (0, 0)
    last_m = fact.last_filled or 0
    complete = (
        fact.last_filled is not None
        and fy_m != 0
        and fact.last_filled == fy_m
        and len(fact.months) >= 12
    )
    # 同じ期の 12 か月埋まり表は、後の部分月より下に並べる。
    filled_key = 0 if complete else last_m
    return (fy_y, fy_m, filled_key, -fact.index)


def _pick_latest(matched: list[_Fact]) -> _Fact | None:
    if not matched:
        return None
    return max(matched, key=_latest_key)


def _prefix(element: str, segment: str | None) -> str:
    if not segment:
        return element
    if element == segment or element.startswith(segment + " "):
        return element
    return f"{segment} {element}".strip()


def _read(role: _Role, fact: _Fact, html: str) -> list[dict]:
    grid = [list(r) for r in fact.grid]
    ctx = " ".join(p for p in (page_context(html), fact.section, unit_mark_context(html)) if p)
    parsed = parse_grid(grid, ctx, html)
    if not parsed or not parsed.get("rows"):
        return []
    rows = [dict(r, values=dict(r.get("values") or {})) for r in parsed["rows"]]
    for row in rows:
        row["element"] = _prefix(str(row.get("element") or ""), role.segment)
        row["unit"] = role.unit
    return _drop_ytd_when_monthly_pair(rows)


def _clip(rows: list[dict], months: list[int]) -> list[dict]:
    allowed = {str(m) for m in months}
    out: list[dict] = []
    for row in rows:
        vals = {k: v for k, v in (row.get("values") or {}).items() if str(k) in allowed}
        if not vals:
            continue
        copied = dict(row)
        copied["values"] = vals
        out.append(copied)
    return out


def _merge(base: list[dict], extra: list[dict]) -> None:
    have = {(str(r.get("element") or ""), r.get("unit")): r for r in base}
    for row in extra:
        key = (str(row.get("element") or ""), row.get("unit"))
        if not key[0]:
            continue
        existing = have.get(key)
        if existing is None:
            base.append(row)
            have[key] = row
            continue
        vals = existing.setdefault("values", {})
        for mk, mv in (row.get("values") or {}).items():
            vals.setdefault(mk, mv)


def _claim_hit(fact: _Fact) -> bool:
    if _CLAIM_RE.search(fact.caption or "") or _CLAIM_RE.search(fact.section or ""):
        return True
    return any(_CLAIM_RE.search(cell or "") for cell in fact.header_cells)


def parse(html: str, *, as_of: str | None = None) -> dict:
    facts = _facts(html)
    n = len(facts)
    if not (_TABLES_LO <= n <= _TABLES_HI):
        raise ValueError(f"9989 page tables={n} want [{_TABLES_LO},{_TABLES_HI}]")
    picked: dict[str, _Fact] = {}
    claimed: set[int] = set()
    for role in _ROLES:
        matched = [f for f in facts if _matches(role, f)]
        claimed.update(f.index for f in matched)
        chosen = _pick_latest(matched)
        if chosen is None:
            raise ValueError(f"9989 {role.name} matched no tables")
        picked[role.name] = chosen
    for fact in facts:
        if fact.index in claimed:
            continue
        if _claim_hit(fact):
            hdr = fact.header_cells[0] if fact.header_cells else ""
            raise ValueError(f"9989 unclaimed table[{fact.index}] header={hdr!r}")
    primary_role = _ROLES[0]
    primary_fact = picked[primary_role.name]
    rows = _read(primary_role, primary_fact, html)
    if not rows:
        raise ValueError("no monthly rows")
    months = [m for m in primary_fact.months if isinstance(m, int)]
    grid = [list(r) for r in primary_fact.grid]
    as_of_s = resolve_as_of(html, grid, rows, as_of, months, primary_fact.fy_end)
    for role in _ROLES[1:]:
        extra = _clip(_read(role, picked[role.name], html), months)
        _merge(rows, extra)
    rows = _drop_ytd_when_monthly_pair(rows)
    months = drop_trailing_empty_months(months, rows)
    return {
        "code": display_code("9989"),
        "months": months,
        "rows": rows,
        "as_of": as_of_s,
    }

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, TypedDict

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ir_monthly_html_r2 import (  # noqa: E402
    _apply_force_unit,
    _detect_col_header,
    _drop_ytd_when_monthly_pair,
    _first_data_row,
    display_code,
    drop_trailing_empty_months,
    heading_year_month,
    last_filled_month,
    page_context,
    parse_grid,
    parse_month_cell,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)

Unit = Literal["number", "yoy_index", "yoy_pct"]
Role = Literal["primary", "companion"]
Pick = Literal["latest", "same_as_primary", "tile", "all"]
Level = Literal["error", "warn"]
_VALID_UNITS = frozenset({"number", "yoy_index", "yoy_pct"})
_VALID_PICKS = frozenset({"latest", "same_as_primary", "tile", "all"})
_RECIPE_KEYS = frozenset(
    {"id", "match", "role", "pick", "segment", "unit", "rows", "expect"}
)
_MATCH_KEYS = frozenset(
    {"caption_re", "header_re", "section_re", "label_re", "index", "auto"}
)
_EXPECT_KEYS = frozenset({"tables", "rows", "units", "months_min", "fingerprint"})
_DRIFT_KEYS = frozenset({"tables", "claim_re"})
_ROW_FILTER_KEYS = frozenset({"keep_re", "drop_re"})
_SEGMENT_KEYS = frozenset({"literal", "capture"})
_DIGIT_RUN_RE = re.compile(r"\d+")
_TAG_RE = re.compile(r"<[^>]+>")


class Sidecar(TypedDict):
    code: str
    months: list[int]
    rows: list[dict[str, Any]]
    as_of: str | None


@dataclass(frozen=True)
class TableMatch:
    caption_re: re.Pattern[str] | None = None
    header_re: re.Pattern[str] | None = None
    section_re: re.Pattern[str] | None = None
    label_re: re.Pattern[str] | None = None
    index: int | None = None
    auto: bool = False

    def __post_init__(self) -> None:
        preds = [
            self.caption_re,
            self.header_re,
            self.section_re,
            self.label_re,
            self.index,
        ]
        n = sum(1 for p in preds if p is not None) + (1 if self.auto else 0)
        if n == 0:
            raise ValueError("TableMatch needs at least one predicate")
        if self.auto and n > 1:
            raise ValueError("TableMatch.auto cannot combine with other predicates")


@dataclass(frozen=True)
class SegmentRule:
    literal: str | None = None
    capture: int | None = None

    def __post_init__(self) -> None:
        if (self.literal is None) == (self.capture is None):
            raise ValueError("SegmentRule needs exactly one of literal / capture")


@dataclass(frozen=True)
class RowFilter:
    keep_re: re.Pattern[str] | None = None
    drop_re: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Expect:
    tables: tuple[int, int] | None = None
    rows: tuple[re.Pattern[str], ...] = ()
    units: frozenset[str] | None = None
    months_min: int | None = None
    fingerprint: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableRecipe:
    id: str
    match: TableMatch
    role: Role = "companion"
    pick: Pick | None = None
    segment: SegmentRule | None = None
    unit: Unit | None = None
    rows: RowFilter = field(default_factory=RowFilter)
    expect: Expect = field(default_factory=Expect)

    @property
    def effective_pick(self) -> Pick:
        if self.pick is not None:
            return self.pick
        # Companions default to latest of their own match set. same_as_primary
        # is opt-in because section FY lookback is noisy on multi-year pages.
        return "latest"


@dataclass(frozen=True)
class DriftPolicy:
    tables: tuple[int, int] | None = None
    claim_re: re.Pattern[str] | None = None


@dataclass(frozen=True)
class RecipeSet:
    recipes: tuple[TableRecipe, ...]
    drift: DriftPolicy = field(default_factory=DriftPolicy)
    default_unit: Unit | None = None
    explicit: bool = True

    def __post_init__(self) -> None:
        if not self.recipes:
            raise ValueError("RecipeSet needs at least one recipe")
        primaries = [r for r in self.recipes if r.role == "primary"]
        if len(primaries) != 1:
            raise ValueError("RecipeSet needs exactly one primary recipe")
        ids = [r.id for r in self.recipes]
        if len(ids) != len(set(ids)):
            raise ValueError("RecipeSet recipe ids must be unique")

    @property
    def primary(self) -> TableRecipe:
        for r in self.recipes:
            if r.role == "primary":
                return r
        raise RuntimeError("no primary")


@dataclass(frozen=True)
class TableFacts:
    index: int
    caption: str
    section: str
    header_cells: tuple[str, ...]
    label_cells: tuple[str, ...]
    orientation: Literal["col_months", "row_months", "snapshot", "none"]
    months: tuple[int, ...]
    fy_end: tuple[int, int] | None
    last_filled_month: int | None
    fingerprint: str
    grid: tuple[tuple[str, ...], ...] = field(repr=False, compare=False)


@dataclass(frozen=True)
class Finding:
    level: Level
    recipe_id: str | None
    kind: str
    detail: str


@dataclass(frozen=True)
class Selection:
    recipe_id: str
    matched: tuple[int, ...]
    selected: tuple[int, ...]
    captures: dict[int, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class RecipeResult:
    sidecar: Sidecar
    selections: tuple[Selection, ...]
    findings: tuple[Finding, ...]
    facts: tuple[TableFacts, ...] = ()

    @property
    def fingerprints(self) -> dict[str, tuple[str, ...]]:
        by_id: dict[str, list[str]] = {}
        for sel in self.selections:
            fps = [
                self.facts[i].fingerprint
                for i in sel.selected
                if 0 <= i < len(self.facts)
            ]
            by_id[sel.recipe_id] = fps
        return {k: tuple(v) for k, v in by_id.items()}


class RecipeDrift(ValueError):
    def __init__(self, code: str, findings: Sequence[Finding]) -> None:
        self.code = code
        self.findings = tuple(findings)
        super().__init__(
            f"{code}: "
            + "; ".join(f"{f.recipe_id or '-'}:{f.kind}" for f in findings)
        )


@dataclass(frozen=True)
class ExplainReport:
    code: str
    facts: tuple[TableFacts, ...]
    selections: tuple[Selection, ...]
    findings: tuple[Finding, ...]
    fingerprints: dict[str, tuple[str, ...]]

    def render(self) -> str:
        lines: list[str] = [f"code={self.code} tables={len(self.facts)}"]
        for sel in self.selections:
            fps = self.fingerprints.get(sel.recipe_id, ())
            hdr = ""
            fy = ""
            months: tuple[int, ...] = ()
            if sel.selected and 0 <= sel.selected[0] < len(self.facts):
                f = self.facts[sel.selected[0]]
                hdr = f.header_cells[0] if f.header_cells else ""
                fy = str(f.fy_end)
                months = f.months
            lines.append(
                f"  recipe={sel.recipe_id} matched={sel.matched} "
                f"selected={sel.selected} header={hdr!r} fy_end={fy} "
                f"months={months} fp={fps}"
            )
        for f in self.findings:
            lines.append(f"  {f.level} {f.recipe_id or '-'} {f.kind}: {f.detail}")
        pin = {k: list(v) for k, v in self.fingerprints.items()}
        lines.append(f"  pin expect.fingerprint {pin}")
        return "\n".join(lines)


def _unknown_keys(raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    extra = [k for k in raw if k not in allowed]
    if extra:
        raise ValueError(f"{path} unknown keys: {sorted(extra)}")


def _compile_re(raw: object, path: str) -> re.Pattern[str]:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{path} must be a non-empty string")
    try:
        return re.compile(raw)
    except re.error as e:
        raise ValueError(f"{path} invalid regex: {e}") from e


def _minmax(raw: object, path: str) -> tuple[int, int]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{path} must be [min, max]")
    a, b = raw
    if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool):
        raise ValueError(f"{path} must be ints")
    return a, b


def _parse_segment(raw: object, path: str) -> SegmentRule | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            raise ValueError(f"{path} must be a non-empty string")
        return SegmentRule(literal=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be string or object")
    _unknown_keys(raw, _SEGMENT_KEYS, path)
    lit = raw.get("literal")
    cap = raw.get("capture")
    if lit is not None and not isinstance(lit, str):
        raise ValueError(f"{path}.literal must be string")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int)):
        raise ValueError(f"{path}.capture must be int")
    return SegmentRule(
        literal=str(lit) if lit is not None else None,
        capture=int(cap) if cap is not None else None,
    )


def _parse_expect(raw: object, path: str) -> Expect:
    if raw is None:
        return Expect()
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be an object")
    _unknown_keys(raw, _EXPECT_KEYS, path)
    tables_t: tuple[int, int] | None = None
    if "tables" in raw:
        tables_t = _minmax(raw.get("tables"), f"{path}.tables")
    rows_raw = raw.get("rows") or []
    if not isinstance(rows_raw, list):
        raise ValueError(f"{path}.rows must be a list")
    rows = tuple(_compile_re(r, f"{path}.rows[{i}]") for i, r in enumerate(rows_raw))
    units_raw = raw.get("units")
    units: frozenset[str] | None = None
    if units_raw is not None:
        if not isinstance(units_raw, list) or not all(isinstance(u, str) for u in units_raw):
            raise ValueError(f"{path}.units must be string list")
        units = frozenset(units_raw)
    months_min = raw.get("months_min")
    if months_min is not None and (
        isinstance(months_min, bool) or not isinstance(months_min, int)
    ):
        raise ValueError(f"{path}.months_min must be int")
    fps_raw = raw.get("fingerprint") or []
    if not isinstance(fps_raw, list) or not all(isinstance(x, str) for x in fps_raw):
        raise ValueError(f"{path}.fingerprint must be string list")
    return Expect(
        tables=tables_t,
        rows=rows,
        units=units,
        months_min=months_min,
        fingerprint=tuple(fps_raw),
    )


def _parse_row_filter(raw: object, path: str) -> RowFilter:
    if raw is None:
        return RowFilter()
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be an object")
    _unknown_keys(raw, _ROW_FILTER_KEYS, path)
    keep = raw.get("keep_re")
    drop = raw.get("drop_re")
    return RowFilter(
        keep_re=_compile_re(keep, f"{path}.keep_re") if keep is not None else None,
        drop_re=_compile_re(drop, f"{path}.drop_re") if drop is not None else None,
    )


def _parse_match(raw: object, path: str) -> TableMatch:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be an object")
    _unknown_keys(raw, _MATCH_KEYS, path)
    idx = raw.get("index")
    if idx is not None and (isinstance(idx, bool) or not isinstance(idx, int)):
        raise ValueError(f"{path}.index must be int")
    auto = raw.get("auto", False)
    if not isinstance(auto, bool):
        raise ValueError(f"{path}.auto must be bool")
    return TableMatch(
        caption_re=_compile_re(raw["caption_re"], f"{path}.caption_re")
        if "caption_re" in raw
        else None,
        header_re=_compile_re(raw["header_re"], f"{path}.header_re")
        if "header_re" in raw
        else None,
        section_re=_compile_re(raw["section_re"], f"{path}.section_re")
        if "section_re" in raw
        else None,
        label_re=_compile_re(raw["label_re"], f"{path}.label_re")
        if "label_re" in raw
        else None,
        index=idx,
        auto=auto,
    )


def _parse_recipe(raw: object, path: str) -> TableRecipe:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be an object")
    _unknown_keys(raw, _RECIPE_KEYS, path)
    rid = raw.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError(f"{path}.id required")
    role = raw.get("role", "companion")
    if role not in ("primary", "companion"):
        raise ValueError(f"{path}.role invalid")
    pick = raw.get("pick")
    if pick is not None and pick not in _VALID_PICKS:
        raise ValueError(f"{path}.pick invalid")
    unit = raw.get("unit")
    if unit is not None and unit not in _VALID_UNITS:
        raise ValueError(f"{path}.unit invalid")
    return TableRecipe(
        id=rid.strip(),
        match=_parse_match(raw.get("match"), f"{path}.match"),
        role=role,
        pick=pick,
        segment=_parse_segment(raw.get("segment"), f"{path}.segment"),
        unit=unit,
        rows=_parse_row_filter(raw.get("rows"), f"{path}.rows"),
        expect=_parse_expect(raw.get("expect"), f"{path}.expect"),
    )


def _parse_drift(raw: object, path: str) -> DriftPolicy:
    if raw is None:
        return DriftPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be an object")
    _unknown_keys(raw, _DRIFT_KEYS, path)
    tables_t: tuple[int, int] | None = None
    if "tables" in raw:
        tables_t = _minmax(raw.get("tables"), f"{path}.tables")
    claim = raw.get("claim_re")
    return DriftPolicy(
        tables=tables_t,
        claim_re=_compile_re(claim, f"{path}.claim_re") if claim is not None else None,
    )


def compile_recipes(raw_issuer: Mapping[str, Any]) -> RecipeSet:
    force = raw_issuer.get("force_unit")
    default_unit: Unit | None = None
    if force is not None:
        if force not in _VALID_UNITS:
            raise ValueError(f"force_unit invalid: {force!r}")
        default_unit = force

    if "recipes" in raw_issuer:
        if "table" in raw_issuer and raw_issuer.get("table") != 0:
            raise ValueError("recipes cannot combine with non-default table")
        recipes_raw = raw_issuer.get("recipes")
        if not isinstance(recipes_raw, list) or not recipes_raw:
            raise ValueError("recipes must be a non-empty list")
        recipes = tuple(
            _parse_recipe(item, f"recipes[{i}]") for i, item in enumerate(recipes_raw)
        )
        return RecipeSet(
            recipes=recipes,
            drift=_parse_drift(raw_issuer.get("drift"), "drift"),
            default_unit=default_unit,
            explicit=True,
        )

    table = raw_issuer.get("table", 0)
    if table is None:
        match = TableMatch(auto=True)
    elif isinstance(table, int) and not isinstance(table, bool):
        match = TableMatch(index=table)
    else:
        raise ValueError("table must be int or null")
    return RecipeSet(
        recipes=(TableRecipe(id="auto", match=match, role="primary"),),
        default_unit=default_unit,
        explicit=False,
    )


def structural_fingerprint(
    orientation: str,
    header_cells: Sequence[str],
    label_cells: Sequence[str],
    n_label_cols: int,
) -> str:
    def norm(s: str) -> str:
        return _DIGIT_RUN_RE.sub("#", s)

    blob = "|".join(
        [
            orientation,
            str(n_label_cols),
            ",".join(norm(c) for c in header_cells),
            ",".join(norm(c) for c in label_cells),
        ]
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
            self.captions.append(_plain_text("".join(self._text)))
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


def _plain_text(html_frag: str) -> str:
    t = _TAG_RE.sub(" ", html_frag or "")
    return re.sub(r"\s+", " ", t).strip()


def _table_captions(html: str, n: int) -> list[str]:
    p = _CaptionParser()
    p.feed(html or "")
    p.close()
    out = list(p.captions)
    if len(out) < n:
        out.extend([""] * (n - len(out)))
    return out[:n]


def _n_label_cols(
    orientation: str, header_cells: Sequence[str]
) -> int:
    if orientation != "col_months":
        return 1 if orientation == "row_months" else 0
    n = 0
    for cell in header_cells:
        if parse_month_cell(cell) is not None:
            break
        n += 1
    return n


def facts_for(html: str) -> tuple[TableFacts, ...]:
    grids = parse_tables(html)
    sections = table_section_texts(html)
    captions = _table_captions(html, len(grids))
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    snap = heading_year_month(html)
    out: list[TableFacts] = []
    prev_fy: tuple[int, int] | None = None
    for i, grid in enumerate(grids):
        section = sections[i] if i < len(sections) else ""
        ctx = " ".join(p for p in (base_ctx, section, unit_marks) if p)
        header_i = _detect_col_header(grid)
        data_i = _first_data_row(grid)
        if header_i is not None:
            orientation: Literal["col_months", "row_months", "snapshot", "none"] = (
                "col_months"
            )
            header_cells = tuple(grid[header_i]) if header_i < len(grid) else ()
            label_cells = tuple(
                (row[0] if row else "") for row in grid[header_i + 1 :] if row
            )
        elif data_i is not None:
            orientation = "row_months"
            header_cells = tuple(c for row in grid[:data_i] for c in row)
            label_cells = tuple(
                (row[0] if row else "") for row in grid[data_i:] if row
            )
        elif snap:
            orientation = "snapshot"
            header_cells = tuple(grid[0]) if grid else ()
            label_cells = tuple((row[0] if row else "") for row in grid[1:] if row)
        else:
            orientation = "none"
            header_cells = tuple(grid[0]) if grid else ()
            label_cells = ()
        parsed = None
        try:
            parsed = parse_grid(grid, ctx, html)
        except Exception:
            parsed = None
        months = tuple(parsed.get("months") or []) if parsed else ()
        rows = parsed.get("rows") or [] if parsed else []
        last_m = last_filled_month(rows, list(months)) if rows else None
        fy = section_fy_end_from_text(section, list(months) if months else None)
        if fy is None:
            fy = prev_fy
        else:
            prev_fy = fy
        n_label = _n_label_cols(orientation, header_cells)
        fp = structural_fingerprint(orientation, header_cells, label_cells, n_label)
        out.append(
            TableFacts(
                index=i,
                caption=captions[i] if i < len(captions) else "",
                section=section,
                header_cells=header_cells,
                label_cells=label_cells,
                orientation=orientation,
                months=months,
                fy_end=fy,
                last_filled_month=last_m,
                fingerprint=fp,
                grid=tuple(tuple(r) for r in grid),
            )
        )
    return tuple(out)


def _match_table(
    recipe: TableRecipe, facts: TableFacts
) -> tuple[bool, tuple[str, ...]]:
    m = recipe.match
    if m.auto:
        return False, ()
    if m.index is not None and facts.index != m.index:
        return False, ()
    first_caps: tuple[str, ...] | None = None

    def _re(rx: re.Pattern[str] | None, text: str) -> bool:
        nonlocal first_caps
        if rx is None:
            return True
        hit = rx.search(text or "")
        if not hit:
            return False
        if first_caps is None and hit.groups():
            first_caps = tuple(g or "" for g in hit.groups())
        return True

    if m.caption_re is not None and not _re(m.caption_re, facts.caption):
        return False, ()
    if m.header_re is not None:
        if not any(_re(m.header_re, cell) for cell in facts.header_cells):
            return False, ()
    if m.section_re is not None and not _re(m.section_re, facts.section):
        return False, ()
    if m.label_re is not None:
        if not any(_re(m.label_re, cell) for cell in facts.label_cells):
            return False, ()
    return True, first_caps or ()


def _fy_key(fy: tuple[int, int] | None) -> tuple[int, int]:
    return fy if fy is not None else (0, 0)


def _latest_sort_key(f: TableFacts) -> tuple[int, int, int, int]:
    fy_y, fy_m = _fy_key(f.fy_end)
    last_m = f.last_filled_month or 0
    complete = (
        f.last_filled_month is not None
        and fy_m != 0
        and f.last_filled_month == fy_m
        and len(f.months) >= 12
    )
    filled_key = 0 if complete else last_m
    return (fy_y, fy_m, filled_key, -f.index)


def _pick_latest(matched: Sequence[TableFacts]) -> tuple[TableFacts, ...]:
    if not matched:
        return ()
    return (max(matched, key=_latest_sort_key),)


def _select(
    recipe: TableRecipe,
    facts: Sequence[TableFacts],
    primary: Selection | None,
) -> Selection:
    matched: list[TableFacts] = []
    captures: dict[int, tuple[str, ...]] = {}
    for f in facts:
        ok, caps = _match_table(recipe, f)
        if ok:
            matched.append(f)
            if caps:
                captures[f.index] = caps
    matched_idx = tuple(f.index for f in matched)
    pick = recipe.effective_pick
    if pick in ("all", "tile"):
        selected = matched
    elif pick == "same_as_primary":
        selected = []
        if primary is not None and primary.selected:
            p_fy = None
            for f in facts:
                if f.index == primary.selected[0]:
                    p_fy = f.fy_end
                    break
            selected = [f for f in matched if f.fy_end == p_fy and p_fy is not None]
            if not selected:
                p_last = None
                for f in facts:
                    if f.index == primary.selected[0]:
                        p_last = f.last_filled_month
                        break
                selected = [
                    f for f in matched if f.last_filled_month == p_last and p_last is not None
                ]
        if not selected:
            selected = list(_pick_latest(matched))
    else:
        selected = list(_pick_latest(matched))
    return Selection(
        recipe_id=recipe.id,
        matched=matched_idx,
        selected=tuple(f.index for f in selected),
        captures=captures,
    )


def _apply_segment(
    element: str, recipe: TableRecipe, sel: Selection, table_index: int
) -> str:
    if recipe.segment is None:
        return element
    prefix: str | None = None
    if recipe.segment.literal is not None:
        prefix = recipe.segment.literal
    elif recipe.segment.capture is not None:
        caps = sel.captures.get(table_index, ())
        n = recipe.segment.capture
        if 1 <= n <= len(caps):
            prefix = caps[n - 1]
    if not prefix:
        return element
    if element == prefix or element.startswith(prefix + " "):
        return element
    return f"{prefix} {element}".strip()


def _filter_rows(rows: list[dict[str, Any]], rf: RowFilter) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        el = str(row.get("element") or "")
        if rf.keep_re is not None and not rf.keep_re.search(el):
            continue
        if rf.drop_re is not None and rf.drop_re.search(el):
            continue
        out.append(row)
    return out


def _read(
    recipe: TableRecipe,
    sel: Selection,
    facts: Sequence[TableFacts],
    html: str,
    as_of_hint: str | None,
    default_unit: Unit | None,
) -> tuple[list[dict[str, Any]], list[int], str | None]:
    base_ctx = page_context(html)
    unit_marks = unit_mark_context(html)
    by_idx = {f.index: f for f in facts}
    all_rows: list[dict[str, Any]] = []
    months: list[int] = []
    as_of_s: str | None = None
    grid_used: list[list[str]] | None = None
    section_fy: tuple[int, int] | None = None
    for idx in sel.selected:
        f = by_idx.get(idx)
        if f is None:
            continue
        grid = [list(r) for r in f.grid]
        ctx = " ".join(p for p in (base_ctx, f.section, unit_marks) if p)
        parsed = parse_grid(grid, ctx, html)
        if not parsed or not parsed.get("rows"):
            continue
        rows = [dict(r, values=dict(r.get("values") or {})) for r in parsed["rows"]]
        rows = _filter_rows(rows, recipe.rows)
        for row in rows:
            row["element"] = _apply_segment(
                str(row.get("element") or ""), recipe, sel, idx
            )
        if recipe.unit:
            for row in rows:
                row["unit"] = recipe.unit
        elif default_unit:
            _apply_force_unit(rows, default_unit)
        all_rows.extend(rows)
        for m in parsed.get("months") or []:
            if m not in months:
                months.append(m)
        grid_used = grid
        section_fy = f.fy_end
    if recipe.role == "primary" and all_rows and grid_used is not None:
        as_of_s = resolve_as_of(
            html, grid_used, all_rows, as_of_hint, months, section_fy
        )
    all_rows = _drop_ytd_when_monthly_pair(all_rows)
    months = drop_trailing_empty_months(months, all_rows)
    return all_rows, months, as_of_s


def _clip_months(
    rows: list[dict[str, Any]], months: Sequence[int]
) -> list[dict[str, Any]]:
    allowed = {str(m) for m in months}
    out: list[dict[str, Any]] = []
    for row in rows:
        vals = {
            k: v for k, v in (row.get("values") or {}).items() if str(k) in allowed
        }
        if not vals:
            continue
        clipped = dict(row)
        clipped["values"] = vals
        out.append(clipped)
    return out


def _merge_onto_axis(
    base: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    months: list[int],
    pick: Pick,
) -> list[dict[str, Any]]:
    if pick != "tile":
        extra = _clip_months(extra, months)
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
    if pick == "tile":
        for row in extra:
            for mk in row.get("values") or {}:
                try:
                    m = int(mk)
                except (TypeError, ValueError):
                    continue
                if m not in months:
                    months.append(m)
    return base


def _claim_text_hit(facts: TableFacts, rx: re.Pattern[str]) -> bool:
    if rx.search(facts.caption or "") or rx.search(facts.section or ""):
        return True
    return any(rx.search(c or "") for c in facts.header_cells)


def _check(
    recipes: RecipeSet,
    facts: Sequence[TableFacts],
    selections: Sequence[Selection],
    rows: Sequence[dict[str, Any]],
    months: Sequence[int],
    rows_by_recipe: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    by_id = {s.recipe_id: s for s in selections}
    claimed: set[int] = set()
    for sel in selections:
        claimed.update(sel.matched)
    for recipe in recipes.recipes:
        sel = by_id.get(recipe.id)
        matched_n = len(sel.matched) if sel else 0
        selected_n = len(sel.selected) if sel else 0
        recipe_rows = (
            rows_by_recipe.get(recipe.id, ()) if rows_by_recipe is not None else rows
        )
        if recipe.role == "primary" and matched_n == 0:
            findings.append(
                Finding("error", recipe.id, "no_match", "primary matched no tables")
            )
        elif (
            recipe.expect.tables is not None
            and recipe.expect.tables[0] >= 1
            and matched_n == 0
        ):
            findings.append(
                Finding("error", recipe.id, "no_match", "recipe matched no tables")
            )
        if recipe.expect.tables is not None and matched_n != 0:
            lo, hi = recipe.expect.tables
            if not (lo <= selected_n <= hi):
                findings.append(
                    Finding(
                        "error",
                        recipe.id,
                        "cardinality",
                        f"selected={selected_n} want [{lo},{hi}]",
                    )
                )
        for rx in recipe.expect.rows:
            if not any(rx.search(str(r.get("element") or "")) for r in rows):
                findings.append(
                    Finding(
                        "error",
                        recipe.id,
                        "missing_row",
                        f"no element matching {rx.pattern!r}",
                    )
                )
        if recipe.expect.units is not None:
            bad = [
                str(r.get("element"))
                for r in recipe_rows
                if r.get("unit") not in recipe.expect.units
            ]
            if bad:
                findings.append(
                    Finding(
                        "error",
                        recipe.id,
                        "unit",
                        f"unexpected units on {bad[:3]}",
                    )
                )
        if recipe.expect.months_min is not None and len(months) < recipe.expect.months_min:
            findings.append(
                Finding(
                    "error",
                    recipe.id,
                    "months_min",
                    f"months={len(months)} < {recipe.expect.months_min}",
                )
            )
        if recipe.expect.fingerprint and sel is not None:
            got = tuple(
                facts[i].fingerprint for i in sel.selected if 0 <= i < len(facts)
            )
            want = set(recipe.expect.fingerprint)
            if any(fp not in want for fp in got):
                findings.append(
                    Finding(
                        "error",
                        recipe.id,
                        "fingerprint",
                        f"got {got} want {recipe.expect.fingerprint}",
                    )
                )
        if recipe.role == "primary" and selected_n > 0 and not rows:
            findings.append(
                Finding("error", recipe.id, "no_primary_rows", "primary yielded no rows")
            )
    if recipes.drift.tables is not None:
        lo, hi = recipes.drift.tables
        n = len(facts)
        if not (lo <= n <= hi):
            findings.append(
                Finding(
                    "error",
                    None,
                    "page_tables",
                    f"page tables={n} want [{lo},{hi}]",
                )
            )
    if recipes.drift.claim_re is not None:
        rx = recipes.drift.claim_re
        for f in facts:
            if f.index in claimed:
                continue
            if _claim_text_hit(f, rx):
                hdr = f.header_cells[0] if f.header_cells else ""
                findings.append(
                    Finding(
                        "error",
                        None,
                        "unclaimed",
                        f"table[{f.index}] header={hdr!r} matches claim_re",
                    )
                )
    return tuple(findings)


def _run(
    html: str,
    code: str,
    recipes: RecipeSet,
    *,
    as_of: str | None,
) -> tuple[
    Sidecar | None,
    tuple[Selection, ...],
    tuple[Finding, ...],
    tuple[TableFacts, ...],
    str | None,
    bool,
]:
    facts = facts_for(html)
    sel_primary = _select(recipes.primary, facts, primary=None)
    rows_p, months_p, as_of_s = _read(
        recipes.primary, sel_primary, facts, html, as_of, recipes.default_unit
    )
    selections: list[Selection] = [sel_primary]
    out_rows = list(rows_p)
    months = list(months_p)
    rows_by_recipe: dict[str, list[dict[str, Any]]] = {recipes.primary.id: list(rows_p)}
    for recipe in recipes.recipes:
        if recipe.role == "primary":
            continue
        sel = _select(recipe, facts, primary=sel_primary)
        selections.append(sel)
        rows_c, months_c, _ = _read(
            recipe, sel, facts, html, as_of_s, recipes.default_unit
        )
        rows_by_recipe[recipe.id] = list(rows_c)
        out_rows = _merge_onto_axis(
            out_rows, rows_c, months, recipe.effective_pick
        )
        if recipe.effective_pick == "tile" and months_c:
            for m in months_c:
                if m not in months:
                    months.append(m)
    months = drop_trailing_empty_months(months, out_rows)
    findings = _check(
        recipes, facts, selections, out_rows, months, rows_by_recipe
    )
    errors = [f for f in findings if f.level == "error"]
    sidecar: Sidecar | None = None
    if not errors and out_rows and as_of_s is not None:
        sidecar = {
            "code": display_code(code),
            "months": months,
            "rows": out_rows,
            "as_of": as_of_s,
        }
    return sidecar, tuple(selections), findings, facts, as_of_s, bool(out_rows)


def execute_recipes(
    html: str,
    code: str,
    recipes: RecipeSet,
    *,
    as_of: str | None = None,
) -> RecipeResult:
    if not recipes.explicit or recipes.primary.match.auto:
        raise ValueError("execute_recipes requires explicit non-auto RecipeSet")
    sidecar, selections, findings, facts, as_of_s, has_rows = _run(
        html, code, recipes, as_of=as_of
    )
    errors = [f for f in findings if f.level == "error"]
    if errors:
        raise RecipeDrift(display_code(code), findings)
    if not has_rows or sidecar is None:
        if has_rows and as_of_s is None:
            raise ValueError("no as_of")
        raise ValueError("no monthly rows")
    return RecipeResult(
        sidecar=sidecar,
        selections=selections,
        findings=tuple(f for f in findings if f.level == "warn"),
        facts=facts,
    )


def explain_recipes(html: str, code: str, recipes: RecipeSet) -> ExplainReport:
    _sidecar, selections, findings, facts, _as_of_s, _has_rows = _run(
        html, code, recipes, as_of=None
    )
    fps = {
        s.recipe_id: tuple(
            facts[i].fingerprint for i in s.selected if 0 <= i < len(facts)
        )
        for s in selections
    }
    return ExplainReport(
        code=display_code(code),
        facts=facts,
        selections=selections,
        findings=findings,
        fingerprints=fps,
    )

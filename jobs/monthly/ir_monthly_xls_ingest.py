"""IR 表計算（xls/xlsx）を monthly ingest JSON にする。

使い方:
  python jobs/monthly/ir_monthly_xls_ingest.py --xls /tmp/history_1.xls --as-of 2026-09-02
  python jobs/monthly/ir_monthly_xls_ingest.py --dest /tmp/r2/monthly --dry-run
  python jobs/monthly/ir_monthly_xls_ingest.py --xls history.xls --as-of 2026-09-02 --post
  python jobs/monthly/ir_monthly_xls_ingest.py --code 7606 --xls sokuho2027.xlsx --dry-run

対象と列・行の正本は config/monthly/ir_monthly_xls.json。取得は ir_monthly_r2.py。
このスクリプトは JSON 化と loopback POST だけ。year は送らない。HTML ingest の cron には混ぜない。
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs._r2common import MountDisconnected, check_r2_mount  # noqa: E402
from jobs.monthly.ir_monthly_html_ingest import UrllibClient, assert_loopback_base  # noqa: E402
from jobs.monthly.ir_monthly_r2 import DEFAULT_DEST  # noqa: E402
from jobs.monthly.tdnet_monthly_r2 import display_code  # noqa: E402
from tools.monthly.read_xls import read_sheet, read_xlsx_sheet, xlsx_sheet_names  # noqa: E402

CODE = "1878"
DEFAULT_FROM_YEAR = 2024
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_XLS_CONFIG = ROOT / "config" / "monthly" / "ir_monthly_xls.json"
_AFIELD_DATE_RE = re.compile(r"/(19\d{2}|20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/")
_STEM_DATE_RE = re.compile(r"^(\d{8})_")
_YEAR_TEXT_RE = re.compile(r"(19\d{2}|20\d{2})\s*年")
_MONTH_TEXT_RE = re.compile(
    r"^([0-9０-９]{1,2})\s*月"
)
_FW = str.maketrans("０１２３４５６７８９", "0123456789")
Kind = Literal["number", "occupancy"]


@dataclass(frozen=True)
class ColumnSpec:
    element: str
    col: int | None = None
    kind: Kind = "number"
    unit: str = "number"
    label: str | None = None
    section: str | None = None
    digits: int | None = None


@dataclass(frozen=True)
class SectionSpec:
    header: str
    unit: str
    rows: tuple[ColumnSpec, ...]


@dataclass(frozen=True)
class IssuerXlsSpec:
    code: str
    layout: str
    exts: tuple[str, ...]
    force: bool
    digits: int | None = None
    columns: tuple[ColumnSpec, ...] = ()
    month_col: int = 1
    year_col: int = 0
    header_value: str = "月"
    fy_start_month: int = 4
    sheet: str = "first"
    label_col: int = 1
    as_of_cell: tuple[int, int] | None = None
    skip_month_re: str = ""
    stop_section_re: str = ""
    sections: tuple[SectionSpec, ...] = ()


@dataclass(frozen=True)
class MonthPoint:
    year: int
    month: int
    values: Mapping[str, float]


def _norm_label(raw: Any) -> str:
    if raw is None or isinstance(raw, bool):
        return ""
    if isinstance(raw, datetime):
        return ""
    text = str(raw).translate(_FW)
    return re.sub(r"[\s\u3000]+", " ", text).strip()


def _as_month(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if raw != raw:
            return None
        month = int(raw)
        if month == raw and 1 <= month <= 12:
            return month
        return None
    text = _norm_label(raw)
    if not text:
        return None
    m = _MONTH_TEXT_RE.match(text)
    if not m:
        return None
    month = int(m.group(1).translate(_FW))
    if 1 <= month <= 12:
        return month
    return None


def _as_year(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        return raw.year
    if isinstance(raw, date):
        return raw.year
    if isinstance(raw, (int, float)):
        if raw != raw:
            return None
        year = int(raw)
        if year == raw and 1900 <= year <= 2100:
            return year
        return None
    text = _norm_label(raw)
    if text.isdigit() and len(text) == 4:
        year = int(text)
        if 1900 <= year <= 2100:
            return year
        return None
    m = _YEAR_TEXT_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def cell_number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        return None
    if isinstance(raw, (int, float)):
        if raw != raw or raw in (float("inf"), float("-inf")):
            return None
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip().translate(_FW).replace(",", "")
        text = text.replace("％", "").replace("%", "")
        if not text or text in {"-", "‐", "–", "—", "―"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def scale_occupancy(value: float) -> float:
    """Excel percent (0.9785) -> 97.85. Already-percent stays. See issue #195."""
    if 0 <= value <= 1.5:
        return round(value * 100.0, 4)
    return value


def round_digits(value: float, digits: int) -> float:
    return round(value, digits)


def _parse_kind(raw: object) -> Kind:
    kind = str(raw or "number")
    if kind not in ("number", "occupancy"):
        raise ValueError(f"invalid kind: {raw!r}")
    return kind  # type: ignore[return-value]


def _parse_digits(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    digits = int(raw)
    if digits < 0:
        raise ValueError(f"invalid digits: {raw!r}")
    return digits


def _parse_column(
    raw: object,
    *,
    section: str | None = None,
    unit: str | None = None,
    default_digits: int | None = None,
) -> ColumnSpec:
    if not isinstance(raw, dict):
        raise ValueError("column must be an object")
    element = str(raw.get("element") or "").strip()
    if not element:
        raise ValueError("column missing element")
    col = raw.get("col")
    unit_s = str(raw.get("unit") or unit or "number")
    digits = _parse_digits(raw.get("digits"))
    if digits is None and unit_s == "yoy_index":
        digits = default_digits
    return ColumnSpec(
        element=element,
        col=int(col) if col is not None and col != "" else None,
        kind=_parse_kind(raw.get("kind")),
        unit=unit_s,
        label=str(raw["label"]).strip() if raw.get("label") else None,
        section=section,
        digits=digits,
    )


def _parse_spec(code: str, raw: object) -> IssuerXlsSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{code} spec must be an object")
    layout = str(raw.get("layout") or "").strip()
    if layout not in {"fy_blocks", "month_columns"}:
        raise ValueError(f"{code} invalid layout: {layout!r}")
    exts_raw = raw.get("exts") or [".xls"]
    if not isinstance(exts_raw, list) or not exts_raw:
        raise ValueError(f"{code} missing exts")
    exts = tuple(str(x).lower() if str(x).startswith(".") else f".{str(x).lower()}" for x in exts_raw)
    digits = _parse_digits(raw.get("digits"))
    columns = tuple(_parse_column(c, default_digits=digits) for c in (raw.get("columns") or []))
    sections: list[SectionSpec] = []
    for sec in raw.get("sections") or []:
        if not isinstance(sec, dict):
            raise ValueError(f"{code} section must be an object")
        header = str(sec.get("header") or "").strip()
        unit = str(sec.get("unit") or "number")
        sec_digits = _parse_digits(sec.get("digits"))
        if sec_digits is None and unit == "yoy_index":
            sec_digits = digits
        rows = tuple(
            _parse_column(r, section=header, unit=unit, default_digits=sec_digits)
            for r in (sec.get("rows") or [])
        )
        sections.append(SectionSpec(header=header, unit=unit, rows=rows))
    as_of_cell = raw.get("as_of_cell")
    cell: tuple[int, int] | None = None
    if isinstance(as_of_cell, list) and len(as_of_cell) == 2:
        cell = (int(as_of_cell[0]), int(as_of_cell[1]))
    return IssuerXlsSpec(
        code=display_code(code),
        layout=layout,
        exts=exts,
        force=bool(raw.get("force", False)),
        digits=digits,
        columns=columns,
        month_col=int(raw.get("month_col", 1)),
        year_col=int(raw.get("year_col", 0)),
        header_value=str(raw.get("header_value") or "月"),
        fy_start_month=int(raw.get("fy_start_month", 4)),
        sheet=str(raw.get("sheet") or "first"),
        label_col=int(raw.get("label_col", 1)),
        as_of_cell=cell,
        skip_month_re=str(raw.get("skip_month_re") or ""),
        stop_section_re=str(raw.get("stop_section_re") or ""),
        sections=tuple(sections),
    )


@lru_cache(maxsize=4)
def load_xls_specs(path: str | None = None) -> dict[str, IssuerXlsSpec]:
    cfg = Path(path) if path else DEFAULT_XLS_CONFIG
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{cfg} has no issuer specs")
    out: dict[str, IssuerXlsSpec] = {}
    for code, item in raw.items():
        spec = _parse_spec(str(code), item)
        out[spec.code] = spec
    return out


def spec_for(code: str, path: str | None = None) -> IssuerXlsSpec:
    key = display_code(code)
    specs = load_xls_specs(path)
    if key not in specs:
        raise KeyError(f"no xls spec for {key}")
    return specs[key]


COLUMNS: tuple[ColumnSpec, ...] = spec_for(CODE).columns


def header_row_index(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec | None = None) -> int:
    month_col = spec.month_col if spec is not None else 1
    want = spec.header_value if spec is not None else "月"
    for i, row in enumerate(grid):
        if len(row) > month_col and row[month_col] == want:
            return i
    raise ValueError("no 月 header row")


def data_rows(
    grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec | None = None,
) -> list[Sequence[Any]]:
    start = header_row_index(grid, spec) + 1
    month_col = spec.month_col if spec is not None else 1
    out: list[Sequence[Any]] = []
    for row in grid[start:]:
        month = _as_month(row[month_col] if len(row) > month_col else None)
        if month is None:
            continue
        out.append(row)
    if not out:
        raise ValueError("no month data rows")
    return out


def fy_blocks(
    rows: Sequence[Sequence[Any]], spec: IssuerXlsSpec | None = None,
) -> list[list[Sequence[Any]]]:
    month_col = spec.month_col if spec is not None else 1
    fy_start = spec.fy_start_month if spec is not None else 4
    blocks: list[list[Sequence[Any]]] = []
    cur: list[Sequence[Any]] = []
    for row in rows:
        month = _as_month(row[month_col])
        if month == fy_start and cur:
            blocks.append(cur)
            cur = []
        cur.append(row)
    if cur:
        blocks.append(cur)
    return blocks


def fy_start_year(
    block: Sequence[Sequence[Any]], spec: IssuerXlsSpec | None = None,
) -> int | None:
    year_col = spec.year_col if spec is not None else 0
    for row in block:
        year = _as_year(row[year_col] if len(row) > year_col else None)
        if year is not None:
            return year
    return None


def _apply_kind(num: float, spec: ColumnSpec) -> float:
    if spec.kind == "occupancy":
        num = scale_occupancy(num)
    if spec.digits is not None:
        num = round_digits(num, spec.digits)
    return num


def fy_block_points(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec) -> list[MonthPoint]:
    points: list[MonthPoint] = []
    fy_start = spec.fy_start_month
    for block in fy_blocks(data_rows(grid, spec), spec):
        start_year = fy_start_year(block, spec)
        if start_year is None:
            raise ValueError("FY block has no calendar year on the May row")
        for row in block:
            month = _as_month(row[spec.month_col])
            if month is None:
                continue
            year = start_year if month >= fy_start else start_year + 1
            values: dict[str, float] = {}
            for col in spec.columns:
                if col.col is None:
                    continue
                raw = row[col.col] if col.col < len(row) else None
                num = cell_number(raw)
                if num is None:
                    continue
                values[col.element] = _apply_kind(num, col)
            points.append(MonthPoint(year=year, month=month, values=values))
    return points


def _skip_month_header(text: str, spec: IssuerXlsSpec) -> bool:
    if not spec.skip_month_re:
        return False
    return re.search(spec.skip_month_re, text) is not None


def _month_header_row(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec) -> int:
    for i, row in enumerate(grid):
        months = [
            c for c, cell in enumerate(row)
            if _as_month(cell) is not None and not _skip_month_header(_norm_label(cell), spec)
        ]
        if len(months) >= 6:
            return i
    raise ValueError("no month header row")


def month_column_points(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec) -> list[MonthPoint]:
    header_i = _month_header_row(grid, spec)
    header = grid[header_i]
    year_row = grid[header_i - 1] if header_i > 0 else []
    cols: list[int] = []
    months: list[int] = []
    years: list[int] = []
    carried: int | None = None
    for c, cell in enumerate(header):
        month = _as_month(cell)
        if month is None or _skip_month_header(_norm_label(cell), spec):
            continue
        year = _as_year(year_row[c] if c < len(year_row) else None)
        if year is not None:
            carried = year
        cols.append(c)
        months.append(month)
        years.append(carried if carried is not None else 0)
    if not cols:
        raise ValueError("no month columns")
    if any(y == 0 for y in years):
        start = next((y for y in years if y), None)
        if start is None:
            raise ValueError("month columns have no year")
        fy_start = spec.fy_start_month
        years = [
            start if m >= fy_start else start + 1 if y == 0 else y
            for m, y in zip(months, years)
        ]
    section_headers = {_norm_label(sec.header): sec for sec in spec.sections}
    stop_re = re.compile(spec.stop_section_re) if spec.stop_section_re else None
    current: SectionSpec | None = None
    seen: set[str] = set()
    row_values: dict[str, list[float | None]] = {}
    for row in grid[header_i + 1:]:
        label = _norm_label(row[spec.label_col] if len(row) > spec.label_col else None)
        if not label:
            continue
        if stop_re and stop_re.search(label):
            current = None
            continue
        if label in section_headers:
            current = section_headers[label]
            continue
        if current is None:
            continue
        col_spec = next((c for c in current.rows if _norm_label(c.label) == label), None)
        if col_spec is None:
            continue
        key = col_spec.element
        if key in seen:
            continue
        seen.add(key)
        vals: list[float | None] = []
        for c in cols:
            raw = row[c] if c < len(row) else None
            num = cell_number(raw)
            vals.append(_apply_kind(num, col_spec) if num is not None else None)
        row_values[key] = vals
    if not row_values:
        raise ValueError("no labeled data rows")
    points: list[MonthPoint] = []
    for i, (year, month) in enumerate(zip(years, months)):
        values = {
            element: nums[i]
            for element, nums in row_values.items()
            if i < len(nums) and nums[i] is not None
        }
        points.append(MonthPoint(year=year, month=month, values=values))
    return points


def month_points(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec | None = None) -> list[MonthPoint]:
    resolved = spec if spec is not None else spec_for(CODE)
    if resolved.layout == "fy_blocks":
        return fy_block_points(grid, resolved)
    if resolved.layout == "month_columns":
        return month_column_points(grid, resolved)
    raise ValueError(f"unsupported layout: {resolved.layout}")


def as_of_from_url(url: str) -> date | None:
    m = _AFIELD_DATE_RE.search(url)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _as_of_from_cell(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    return None


def as_of_from_grid(grid: Sequence[Sequence[Any]], spec: IssuerXlsSpec) -> date | None:
    if spec.as_of_cell is not None:
        r, c = spec.as_of_cell
        if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
            got = _as_of_from_cell(grid[r][c])
            if got is not None:
                return got
    for row in grid[:8]:
        for cell in row:
            got = _as_of_from_cell(cell)
            if got is not None:
                return got
    return None


def last_filled_month_end(points: Sequence[MonthPoint]) -> date | None:
    filled = [p for p in points if p.values]
    if not filled:
        return None
    last = filled[-1]
    day = calendar.monthrange(last.year, last.month)[1]
    return date(last.year, last.month, day)


def resolve_as_of(
    *,
    explicit: date | None,
    source_url: str | None,
    points: Sequence[MonthPoint],
    grid: Sequence[Sequence[Any]] | None = None,
    spec: IssuerXlsSpec | None = None,
) -> date:
    if explicit is not None:
        return explicit
    if source_url:
        from_url = as_of_from_url(source_url)
        if from_url is not None:
            return from_url
    if grid is not None and spec is not None:
        from_grid = as_of_from_grid(grid, spec)
        if from_grid is not None:
            return from_grid
    derived = last_filled_month_end(points)
    if derived is None:
        raise ValueError("no as_of")
    return derived


def drop_trailing_empty(months: list[int], rows: list[dict[str, Any]]) -> list[int]:
    last: int | None = None
    for i in range(len(months) - 1, -1, -1):
        if any(
            isinstance(row.get("values"), list)
            and i < len(row["values"])
            and row["values"][i] is not None
            for row in rows
        ):
            last = i
            break
    if last is None:
        return months
    return months[: last + 1]


def _output_columns(spec: IssuerXlsSpec) -> list[ColumnSpec]:
    if spec.columns:
        return list(spec.columns)
    if spec.sections:
        return [col for sec in spec.sections for col in sec.rows]
    raise ValueError(f"{spec.code} has no columns or sections")


def ingest_from_points(
    points: Sequence[MonthPoint],
    as_of: date,
    *,
    from_year: int = DEFAULT_FROM_YEAR,
    spec: IssuerXlsSpec | None = None,
) -> dict[str, Any]:
    resolved = spec if spec is not None else spec_for(CODE)
    kept = [p for p in points if p.year >= from_year]
    if not kept:
        raise ValueError(f"no months at or after {from_year}")
    months = [p.month for p in kept]
    rows: list[dict[str, Any]] = []
    for col in _output_columns(resolved):
        values = [p.values.get(col.element) for p in kept]
        if all(v is None for v in values):
            continue
        row: dict[str, Any] = {
            "element": col.element,
            "unit": col.unit,
        }
        if resolved.force:
            row["force"] = True
        row["values"] = values
        rows.append(row)
    months = drop_trailing_empty(months, rows)
    n = len(months)
    for row in rows:
        row["values"] = row["values"][:n]
    return {"months": months, "rows": rows, "as_of": as_of.isoformat()}


def ingest_from_grid(
    grid: Sequence[Sequence[Any]],
    *,
    as_of: date | None = None,
    source_url: str | None = None,
    from_year: int = DEFAULT_FROM_YEAR,
    spec: IssuerXlsSpec | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    resolved = spec if spec is not None else spec_for(code or CODE)
    points = month_points(grid, resolved)
    resolved_as_of = resolve_as_of(
        explicit=as_of, source_url=source_url, points=points, grid=grid, spec=resolved,
    )
    return ingest_from_points(points, resolved_as_of, from_year=from_year, spec=resolved)


def pick_xlsx_sheet(names: Sequence[str], how: str) -> str:
    if not names:
        raise ValueError("no sheets")
    if how == "first":
        return names[0]
    if how == "latest_ym":
        scored: list[tuple[int, int, str]] = []
        for name in names:
            m = re.fullmatch(r"(19\d{2}|20\d{2})\.(\d{1,2})", name.strip())
            if m:
                scored.append((int(m.group(1)), int(m.group(2)), name))
        if not scored:
            raise ValueError(f"no YYYY.MM sheet in {list(names)}")
        return max(scored)[2]
    if how == "latest_int":
        scored_i = [(int(name), name) for name in names if name.isdigit()]
        if not scored_i:
            raise ValueError(f"no integer sheet in {list(names)}")
        return max(scored_i)[1]
    if how in names:
        return how
    raise ValueError(f"unknown sheet {how!r}")


def read_grid_for_spec(path: Path, spec: IssuerXlsSpec) -> list[list[Any]]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        sheet = pick_xlsx_sheet(xlsx_sheet_names(path), spec.sheet)
        return read_xlsx_sheet(path, sheet)
    if suffix == ".xls":
        return read_sheet(path)
    raise ValueError(f"unsupported workbook ext: {path.suffix}")


def ingest_from_xls(
    path: Path,
    *,
    as_of: date | None = None,
    source_url: str | None = None,
    from_year: int = DEFAULT_FROM_YEAR,
    code: str = CODE,
    spec: IssuerXlsSpec | None = None,
) -> dict[str, Any]:
    resolved = spec if spec is not None else spec_for(code)
    return ingest_from_grid(
        read_grid_for_spec(path, resolved),
        as_of=as_of, source_url=source_url, from_year=from_year, spec=resolved,
    )


def filename_ymd(path: Path) -> date | None:
    m = _STEM_DATE_RE.match(path.name)
    if not m:
        return None
    raw = m.group(1)
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def latest_workbook(dest: Path, code: str, exts: Sequence[str]) -> Path:
    folder = dest / display_code(code)
    want = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exts}
    files = [p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in want]
    if not files:
        raise FileNotFoundError(f"no {sorted(want)} under {folder}")
    return max(files, key=lambda p: (filename_ymd(p) or date.min, p.name))


def latest_xls(dest: Path, code: str = CODE) -> Path:
    return latest_workbook(dest, code, spec_for(code).exts)


def ingest_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "months": list(payload["months"]),
        "rows": list(payload["rows"]),
        "as_of": payload["as_of"],
        "via": "ir_xls",
    }


def post_ingest(
    origin: str,
    payload: Mapping[str, Any],
    *,
    code: str = CODE,
    client: UrllibClient | None = None,
) -> tuple[int, Any]:
    http = client if client is not None else UrllibClient()
    return http.post_json(f"{origin}/api/v1/tables/{display_code(code)}/ingest", ingest_body(payload))


def run(
    *,
    xls: Path | None,
    dest: Path,
    as_of: date | None,
    source_url: str | None,
    from_year: int,
    dry_run: bool,
    post: bool,
    base_url: str,
    client: UrllibClient | None = None,
    code: str = CODE,
) -> int:
    resolved = spec_for(code)
    if xls is None:
        try:
            check_r2_mount(dest)
        except MountDisconnected as e:
            print(f"ir_monthly_xls_ingest {e}", flush=True)
            return 1
        except OSError:
            pass
    path = xls if xls is not None else latest_workbook(dest, resolved.code, resolved.exts)
    payload = ingest_from_xls(
        path, as_of=as_of, source_url=source_url, from_year=from_year, spec=resolved,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if dry_run:
        return 0
    if not post:
        return 0
    try:
        origin = assert_loopback_base(base_url)
    except ValueError as e:
        print(f"fail {e}", flush=True)
        return 1
    status, body = post_ingest(origin, payload, code=resolved.code, client=client)
    print(json.dumps(body, ensure_ascii=False, indent=2) if not isinstance(body, str) else body)
    return 0 if status == 200 else 1


def _parse_as_of_flag(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise argparse.ArgumentTypeError("as_of must be YYYY-MM-DD") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build monthly ingest JSON from IR xls/xlsx")
    ap.add_argument("--xls", default="", help="path to workbook")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="monthly dest; used when --xls is omitted")
    ap.add_argument("--code", default=CODE, help="issuer code (default 1878)")
    ap.add_argument("--as-of", default="", type=str, help="disclosure date YYYY-MM-DD")
    ap.add_argument("--source-url", default="", help="IR file URL (afieldfile /YYYY/MM/DD/ becomes as_of)")
    ap.add_argument("--from-year", type=int, default=DEFAULT_FROM_YEAR, help="first calendar year to keep")
    ap.add_argument("--dry-run", action="store_true", help="print JSON, do not POST")
    ap.add_argument("--post", action="store_true", help="POST to loopback /api/v1/tables/{code}/ingest")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="loopback origin")
    args = ap.parse_args(argv)
    if args.dry_run and args.post:
        print("error: --dry-run and --post cannot be combined", flush=True)
        return 2
    as_of = _parse_as_of_flag(args.as_of) if args.as_of else None
    xls = Path(args.xls) if args.xls else None
    source = args.source_url.strip() or None
    if source and urlparse(source).scheme not in ("http", "https"):
        print("error: --source-url must be http(s)", flush=True)
        return 2
    try:
        spec_for(args.code)
    except KeyError as e:
        print(f"error: {e}", flush=True)
        return 2
    return run(
        xls=xls,
        dest=Path(args.dest),
        as_of=as_of,
        source_url=source,
        from_year=args.from_year,
        dry_run=args.dry_run,
        post=args.post,
        base_url=args.base_url,
        code=args.code,
    )


if __name__ == "__main__":
    raise SystemExit(main())

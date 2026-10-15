"""大東建託 (1878) の history.xls を monthly ingest JSON にする。

使い方:
  python jobs/ir_monthly_xls_ingest.py --xls /tmp/history_1.xls --as-of 2026-09-02
  python jobs/ir_monthly_xls_ingest.py --dest /tmp/r2/monthly --dry-run
  python jobs/ir_monthly_xls_ingest.py --xls history.xls --as-of 2026-09-02 --post

取得は ir_monthly_r2.py。このスクリプトは JSON 化と loopback POST だけ。
year は送らない。HTML ingest の cron には混ぜない。
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
for _p in (str(ROOT), str(SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _r2common import MountDisconnected, check_r2_mount  # noqa: E402
from ir_monthly_html_ingest import UrllibClient, assert_loopback_base  # noqa: E402
from ir_monthly_r2 import DEFAULT_DEST  # noqa: E402
from tdnet_monthly_r2 import display_code  # noqa: E402
from tools.monthly.read_xls import read_sheet  # noqa: E402

CODE = "1878"
DEFAULT_FROM_YEAR = 2024
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_AFIELD_DATE_RE = re.compile(r"/(19\d{2}|20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/")
_STEM_DATE_RE = re.compile(r"^(\d{8})_")
Kind = Literal["number", "occupancy"]


@dataclass(frozen=True)
class ColumnSpec:
    col: int
    element: str
    kind: Kind = "number"


# col 7 受注トレンド（6ヶ月移動平均）と col 16–18 管理戸数は入れない。
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(2, "新規受注高"),
    ColumnSpec(3, "キャンセル額"),
    ColumnSpec(4, "受注高"),
    ColumnSpec(5, "営業人員数"),
    ColumnSpec(6, "1人当たり受注高"),
    ColumnSpec(8, "入居者斡旋件数"),
    ColumnSpec(9, "家賃ベース入居率", "occupancy"),
    ColumnSpec(10, "家賃ベース入居率 事業用", "occupancy"),
    ColumnSpec(11, "家賃ベース入居率 居住用", "occupancy"),
    ColumnSpec(12, "空室在庫"),
    ColumnSpec(13, "空室数"),
    ColumnSpec(14, "新築 空室数"),
    ColumnSpec(15, "既存 空室数"),
)


@dataclass(frozen=True)
class MonthPoint:
    year: int
    month: int
    values: Mapping[str, float]


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
    return None


def _as_year(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if raw != raw:
            return None
        year = int(raw)
        if year == raw and 1900 <= year <= 2100:
            return year
        return None
    if isinstance(raw, str) and raw.isdigit() and len(raw) == 4:
        year = int(raw)
        if 1900 <= year <= 2100:
            return year
    return None


def cell_number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if raw != raw or raw in (float("inf"), float("-inf")):
            return None
        return float(raw)
    return None


def scale_occupancy(value: float) -> float:
    """Excel percent (0.9785) -> 97.85. Already-percent stays. See issue #195."""
    if 0 <= value <= 1.5:
        return round(value * 100.0, 4)
    return value


def header_row_index(grid: Sequence[Sequence[Any]]) -> int:
    for i, row in enumerate(grid):
        if len(row) > 1 and row[1] == "月":
            return i
    raise ValueError("no 月 header row")


def data_rows(grid: Sequence[Sequence[Any]]) -> list[Sequence[Any]]:
    start = header_row_index(grid) + 1
    out: list[Sequence[Any]] = []
    for row in grid[start:]:
        month = _as_month(row[1] if len(row) > 1 else None)
        if month is None:
            continue
        out.append(row)
    if not out:
        raise ValueError("no month data rows")
    return out


def fy_blocks(rows: Sequence[Sequence[Any]]) -> list[list[Sequence[Any]]]:
    blocks: list[list[Sequence[Any]]] = []
    cur: list[Sequence[Any]] = []
    for row in rows:
        month = _as_month(row[1])
        if month == 4 and cur:
            blocks.append(cur)
            cur = []
        cur.append(row)
    if cur:
        blocks.append(cur)
    return blocks


def fy_start_year(block: Sequence[Sequence[Any]]) -> int | None:
    for row in block:
        year = _as_year(row[0] if row else None)
        if year is not None:
            return year
    return None


def month_points(grid: Sequence[Sequence[Any]]) -> list[MonthPoint]:
    points: list[MonthPoint] = []
    for block in fy_blocks(data_rows(grid)):
        start_year = fy_start_year(block)
        if start_year is None:
            raise ValueError("FY block has no calendar year on the May row")
        for row in block:
            month = _as_month(row[1])
            if month is None:
                continue
            year = start_year if month >= 4 else start_year + 1
            values: dict[str, float] = {}
            for spec in COLUMNS:
                raw = row[spec.col] if spec.col < len(row) else None
                num = cell_number(raw)
                if num is None:
                    continue
                if spec.kind == "occupancy":
                    num = scale_occupancy(num)
                values[spec.element] = num
            points.append(MonthPoint(year=year, month=month, values=values))
    return points


def as_of_from_url(url: str) -> date | None:
    m = _AFIELD_DATE_RE.search(url)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


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
) -> date:
    if explicit is not None:
        return explicit
    if source_url:
        from_url = as_of_from_url(source_url)
        if from_url is not None:
            return from_url
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


def ingest_from_points(
    points: Sequence[MonthPoint],
    as_of: date,
    *,
    from_year: int = DEFAULT_FROM_YEAR,
) -> dict[str, Any]:
    kept = [p for p in points if p.year >= from_year]
    if not kept:
        raise ValueError(f"no months at or after {from_year}")
    months = [p.month for p in kept]
    rows: list[dict[str, Any]] = []
    for spec in COLUMNS:
        values = [p.values.get(spec.element) for p in kept]
        if all(v is None for v in values):
            continue
        rows.append({
            "element": spec.element,
            "unit": "number",
            "force": True,
            "values": values,
        })
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
) -> dict[str, Any]:
    points = month_points(grid)
    resolved = resolve_as_of(explicit=as_of, source_url=source_url, points=points)
    return ingest_from_points(points, resolved, from_year=from_year)


def ingest_from_xls(
    path: Path,
    *,
    as_of: date | None = None,
    source_url: str | None = None,
    from_year: int = DEFAULT_FROM_YEAR,
) -> dict[str, Any]:
    return ingest_from_grid(
        read_sheet(path), as_of=as_of, source_url=source_url, from_year=from_year,
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


def latest_xls(dest: Path, code: str = CODE) -> Path:
    folder = dest / display_code(code)
    files = [p for p in folder.glob("*.xls") if p.is_file()]
    if not files:
        raise FileNotFoundError(f"no .xls under {folder}")
    return max(files, key=lambda p: (filename_ymd(p) or date.min, p.name))


def ingest_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "months": list(payload["months"]),
        "rows": list(payload["rows"]),
        "as_of": payload["as_of"],
    }


def post_ingest(
    origin: str,
    payload: Mapping[str, Any],
    *,
    code: str = CODE,
    client: UrllibClient | None = None,
) -> tuple[int, Any]:
    http = client if client is not None else UrllibClient()
    return http.post_json(f"{origin}/api/v1/tables/{code}/ingest", ingest_body(payload))


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
) -> int:
    if xls is None:
        try:
            check_r2_mount(dest)
        except MountDisconnected as e:
            print(f"ir_monthly_xls_ingest {e}", flush=True)
            return 1
        except OSError:
            pass
    path = xls if xls is not None else latest_xls(dest)
    payload = ingest_from_xls(path, as_of=as_of, source_url=source_url, from_year=from_year)
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
    status, body = post_ingest(origin, payload, client=client)
    print(json.dumps(body, ensure_ascii=False, indent=2) if not isinstance(body, str) else body)
    return 0 if status == 200 else 1


def _parse_as_of_flag(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise argparse.ArgumentTypeError("as_of must be YYYY-MM-DD") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build monthly ingest JSON from 1878 history.xls")
    ap.add_argument("--xls", default="", help="path to history.xls")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="R2 monthly dest; used when --xls is omitted")
    ap.add_argument("--as-of", default="", type=str, help="disclosure date YYYY-MM-DD")
    ap.add_argument("--source-url", default="", help="IR file URL (afieldfile /YYYY/MM/DD/ becomes as_of)")
    ap.add_argument("--from-year", type=int, default=DEFAULT_FROM_YEAR, help="first calendar year to keep")
    ap.add_argument("--dry-run", action="store_true", help="print JSON, do not POST")
    ap.add_argument("--post", action="store_true", help="POST to loopback /api/v1/tables/1878/ingest")
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
    return run(
        xls=xls,
        dest=Path(args.dest),
        as_of=as_of,
        source_url=source,
        from_year=args.from_year,
        dry_run=args.dry_run,
        post=args.post,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())

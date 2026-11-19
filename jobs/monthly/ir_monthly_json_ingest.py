"""IR の公開 JSON を monthly ingest して loopback POST する。

使い方:
  python jobs/monthly/ir_monthly_json_ingest.py --json tests/monthly/testdata/7649_monthlyreport.json --dry-run
  python jobs/monthly/ir_monthly_json_ingest.py --code 7649 --dry-run
  python jobs/monthly/ir_monthly_json_ingest.py --code 7649

対象とフィールドの正本は config/monthly/ir_monthly_json.json。
year は送らない。値の無い月は送らない（null で既存セルを消さない）。
銘柄クラスは作らない。HTML ingest のレジストリには混ぜない。

VPS cron (JST, with ir_monthly_html_ingest.py): 10:30 / 16:30
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.monthly.ir_monthly_html_ingest import UrllibClient, assert_loopback_base
from jobs.monthly.ir_monthly_r2 import http_get
from jobs.monthly.tdnet_monthly_r2 import display_code

CODE = "7649"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_JSON_CONFIG = ROOT / "config" / "monthly" / "ir_monthly_json.json"
SUMMARY_LABEL = "ir_monthly_json_ingest"
_AS_OF_RE = re.compile(r"(19\d{2}|20\d{2})-(\d{2})-(\d{2})")
_FW = str.maketrans("０１２３４５６７８９", "0123456789")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    element: str


@dataclass(frozen=True)
class IssuerJsonSpec:
    code: str
    name: str
    url: str
    fy_end_month: int
    digits: int
    unit: str
    add: float
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True)
class MonthPoint:
    year: int
    month: int
    values: Mapping[str, float]


def round_digits(value: float, digits: int) -> float:
    return round(value, digits)


def cell_number(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
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


def _parse_digits(raw: object) -> int:
    digits = int(raw)
    if digits < 0:
        raise ValueError(f"invalid digits: {raw!r}")
    return digits


def _parse_fy_end_month(raw: object) -> int:
    month = int(raw)
    if not 1 <= month <= 12:
        raise ValueError(f"invalid fy_end_month: {raw!r}")
    return month


def _parse_url(raw: object, code: str) -> str:
    url = str(raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{code} invalid url: {url!r}")
    return url


def _parse_field(raw: object) -> FieldSpec:
    if not isinstance(raw, dict):
        raise ValueError("field must be an object")
    key = str(raw.get("key") or "").strip()
    element = str(raw.get("element") or "").strip()
    if not key or not element:
        raise ValueError("field missing key or element")
    return FieldSpec(key=key, element=element)


def _parse_spec(code: str, raw: object) -> IssuerJsonSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"{code} spec must be an object")
    fields_raw = raw.get("fields") or []
    if not isinstance(fields_raw, list) or not fields_raw:
        raise ValueError(f"{code} missing fields")
    unit = str(raw.get("unit") or "yoy_index")
    if unit not in ("number", "yoy_index", "yoy_pct"):
        raise ValueError(f"{code} invalid unit: {unit!r}")
    add = raw.get("add", 0)
    if isinstance(add, bool) or not isinstance(add, (int, float)):
        raise ValueError(f"{code} invalid add: {add!r}")
    return IssuerJsonSpec(
        code=display_code(code),
        name=str(raw.get("name") or "").strip() or display_code(code),
        url=_parse_url(raw.get("url"), code),
        fy_end_month=_parse_fy_end_month(raw.get("fy_end_month", 2)),
        digits=_parse_digits(raw.get("digits", 1)),
        unit=unit,
        add=float(add),
        fields=tuple(_parse_field(f) for f in fields_raw),
    )


@lru_cache(maxsize=4)
def load_json_specs(path: str | None = None) -> dict[str, IssuerJsonSpec]:
    cfg = Path(path) if path else DEFAULT_JSON_CONFIG
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{cfg} has no issuer specs")
    out: dict[str, IssuerJsonSpec] = {}
    for code, item in raw.items():
        spec = _parse_spec(str(code), item)
        out[spec.code] = spec
    return out


def spec_for(code: str, path: str | None = None) -> IssuerJsonSpec:
    key = display_code(code)
    specs = load_json_specs(path)
    if key not in specs:
        raise KeyError(f"no json spec for {key}")
    return specs[key]


def calendar_year(year: int, month: int) -> int:
    """JSON year は HTML data-year（暦年）。2027年2月期の3月は year=2026 month=3。"""
    if not 1 <= month <= 12:
        raise ValueError(f"invalid month: {month}")
    if not 1900 <= year <= 2100:
        raise ValueError(f"invalid year: {year}")
    return year


def fiscal_start_month(fy_end_month: int) -> int:
    return (fy_end_month % 12) + 1


def fy_start_year(year: int, month: int, fy_end_month: int) -> int:
    """暦年月が属する会計年度の期首年（2月期末なら3月の年）。"""
    start = fiscal_start_month(fy_end_month)
    if month >= start:
        return year
    return year - 1


def in_fiscal_year(year: int, month: int, fy_start: int, fy_end_month: int) -> bool:
    start = fiscal_start_month(fy_end_month)
    if month >= start:
        return year == fy_start
    return year == fy_start + 1 and month <= fy_end_month


def parse_as_of(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, bool):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    m = _AS_OF_RE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _row_year_month(row: Mapping[str, Any]) -> tuple[int, int] | None:
    year = cell_number(row.get("year"))
    month = cell_number(row.get("month"))
    if year is None or month is None:
        return None
    year_i = int(year)
    month_i = int(month)
    if year != year_i or month != month_i:
        return None
    try:
        return calendar_year(year_i, month_i), month_i
    except ValueError:
        return None


def _apply_value(num: float, spec: IssuerJsonSpec) -> float:
    return round_digits(num + spec.add, spec.digits)


def month_points(payload: Mapping[str, Any], spec: IssuerJsonSpec) -> list[MonthPoint]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("json missing data")
    points: list[MonthPoint] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ym = _row_year_month(row)
        if ym is None:
            continue
        year, month = ym
        values: dict[str, float] = {}
        for field in spec.fields:
            num = cell_number(row.get(field.key))
            if num is None:
                continue
            values[field.element] = _apply_value(num, spec)
        points.append(MonthPoint(year=year, month=month, values=values))
    if not points:
        raise ValueError("no month rows")
    points.sort(key=lambda p: (p.year, p.month))
    return points


def current_fy_points(points: Sequence[MonthPoint], spec: IssuerJsonSpec) -> list[MonthPoint]:
    filled = [p for p in points if p.values]
    if not filled:
        raise ValueError("no filled months")
    last = filled[-1]
    fy_start = fy_start_year(last.year, last.month, spec.fy_end_month)
    kept = [p for p in points if in_fiscal_year(p.year, p.month, fy_start, spec.fy_end_month)]
    if not any(p.values for p in kept):
        raise ValueError("no months in current fiscal year")
    return kept


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


def resolve_as_of(
    payload: Mapping[str, Any],
    points: Sequence[MonthPoint],
    *,
    explicit: date | None = None,
) -> date:
    if explicit is not None:
        return explicit
    from_last = parse_as_of(payload.get("last_updated"))
    if from_last is not None:
        return from_last
    filled = [p for p in points if p.values]
    if not filled:
        raise ValueError("no as_of")
    last = filled[-1]
    return date(last.year, last.month, 1)


def ingest_from_points(
    points: Sequence[MonthPoint],
    as_of: date,
    spec: IssuerJsonSpec,
) -> dict[str, Any]:
    kept = current_fy_points(points, spec)
    months = [p.month for p in kept]
    rows: list[dict[str, Any]] = []
    for field in spec.fields:
        values = [p.values.get(field.element) for p in kept]
        if all(v is None for v in values):
            continue
        rows.append({"element": field.element, "unit": spec.unit, "values": values})
    months = drop_trailing_empty(months, rows)
    n = len(months)
    for row in rows:
        row["values"] = row["values"][:n]
    if not months or not rows:
        raise ValueError("no current-year values")
    return {"months": months, "rows": rows, "as_of": as_of.isoformat()}


def ingest_from_payload(
    payload: Mapping[str, Any],
    *,
    as_of: date | None = None,
    spec: IssuerJsonSpec | None = None,
    code: str = CODE,
) -> dict[str, Any]:
    resolved = spec if spec is not None else spec_for(code)
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    points = month_points(payload, resolved)
    resolved_as_of = resolve_as_of(payload, points, explicit=as_of)
    return ingest_from_points(points, resolved_as_of, resolved)


def ingest_from_json(
    path: Path,
    *,
    as_of: date | None = None,
    spec: IssuerJsonSpec | None = None,
    code: str = CODE,
) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("json root must be an object")
    return ingest_from_payload(raw, as_of=as_of, spec=spec, code=code)


def fetch_payload(url: str, *, getter: Callable[[str], bytes] = http_get) -> dict[str, Any]:
    body = getter(url)
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("json root must be an object")
    return data


def _row_values_without_nulls(months: Sequence[int], values_raw: Any) -> dict[str, float]:
    """null は既存セルを消すので送らない。months に重複が無いとき {月: 値}。"""
    if isinstance(values_raw, dict):
        out: dict[str, float] = {}
        for key, val in values_raw.items():
            if val is None:
                continue
            out[str(int(key) if str(key).isdigit() else key)] = val
        return out
    if not isinstance(values_raw, list):
        raise ValueError("values must be a list or object")
    if len(values_raw) != len(months):
        raise ValueError("values length must match months")
    return {
        str(month): val
        for month, val in zip(months, values_raw)
        if val is not None
    }


def ingest_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    months = list(payload["months"])
    rows: list[dict[str, Any]] = []
    for row in payload["rows"]:
        values = _row_values_without_nulls(months, row.get("values"))
        if not values:
            continue
        item: dict[str, Any] = {
            "element": row["element"],
            "unit": row["unit"],
            "values": values,
        }
        if row.get("force") is True:
            item["force"] = True
        rows.append(item)
    return {
        "months": months,
        "rows": rows,
        "as_of": payload["as_of"],
        "via": "ir_json",
    }


def post_ingest(
    origin: str,
    payload: Mapping[str, Any],
    *,
    code: str = CODE,
    client: UrllibClient | None = None,
) -> tuple[int, Any]:
    http = client if client is not None else UrllibClient()
    return http.post_json(
        f"{origin}/api/v1/tables/{display_code(code)}/ingest",
        ingest_body(payload),
    )


def _print_done(posted: int, skipped: int, failed: int) -> None:
    print(
        f"{SUMMARY_LABEL} done posted={posted} skipped={skipped} failed={failed}",
        flush=True,
    )


def run(
    *,
    json_path: Path | None,
    as_of: date | None,
    code: str = CODE,
    getter: Callable[[str], bytes] = http_get,
    dry_run: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    client: UrllibClient | None = None,
) -> int:
    posted = skipped = failed = 0
    try:
        spec = spec_for(code)
        if json_path is not None:
            payload = ingest_from_json(json_path, as_of=as_of, spec=spec)
        else:
            payload = ingest_from_payload(
                fetch_payload(spec.url, getter=getter), as_of=as_of, spec=spec,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if dry_run:
            skipped = 1
            _print_done(posted, skipped, failed)
            return 0
        origin = assert_loopback_base(base_url)
        status, body = post_ingest(origin, payload, code=spec.code, client=client)
        print(json.dumps(body, ensure_ascii=False, indent=2) if not isinstance(body, str) else body)
        if status == 200:
            posted = 1
            _print_done(posted, skipped, failed)
            return 0
        failed = 1
        print(f"fail {spec.code} http={status}", flush=True)
        _print_done(posted, skipped, failed)
        return 1
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as e:
        print(f"fail {e}", flush=True)
        _print_done(0, 0, 1)
        return 1


def _parse_as_of_flag(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise argparse.ArgumentTypeError("as_of must be YYYY-MM-DD") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GET IR CloudFront JSON and POST monthly ingest")
    ap.add_argument("--json", default="", help="path to fixture JSON; omit to GET the spec URL")
    ap.add_argument("--code", default=CODE, help="issuer code (default 7649)")
    ap.add_argument("--as-of", default="", type=str, help="disclosure date YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true", help="print JSON, do not POST")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="loopback origin")
    args = ap.parse_args(argv)
    as_of = _parse_as_of_flag(args.as_of) if args.as_of else None
    json_path = Path(args.json) if args.json else None
    try:
        spec_for(args.code)
    except KeyError as e:
        print(f"error: {e}", flush=True)
        return 2
    if json_path is not None and not json_path.is_file():
        print(f"error: json not found: {json_path}", flush=True)
        return 2
    return run(
        json_path=json_path,
        as_of=as_of,
        code=args.code,
        dry_run=args.dry_run,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""IR HTML の `.ingest.json` を loopback FastAPI へ POST する。producer は触らない。

使い方:
  python jobs/ir_monthly_html_ingest.py --dest /tmp/r2/monthly
  python jobs/ir_monthly_html_ingest.py --dest /tmp/r2/monthly --only 2664
  python jobs/ir_monthly_html_ingest.py --dest /tmp/r2/monthly --dry-run
  python jobs/ir_monthly_html_ingest.py --ledger /var/log/app/html_ingest_ledger.json --force --only 3082 --months 2026-07,2026-08

--only は 4桁 (3082) でも 5桁の J-Quants コード (30820) でもよい。
--months は暦年の YYYY-MM。列の年はサーバの mapping（rows=[] の probe POST）に合わせる。
--months の部分投入は ledger を更新しない。--force だけで sidecar 全体を送ったときは更新する。
行の force（unit 上書き・類似名の登録）は --force のときだけ付ける。cron では付けない。

VPS cron (JST, after ir_monthly_html_r2.py): 10:30 / 16:30 with
  --ledger /var/log/app/html_ingest_ledger.json
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _r2common import jst_today  # noqa: E402
from ir_monthly_r2 import DEFAULT_CONFIG, DEFAULT_DEST, atomic_write, sha256_bytes  # noqa: E402
from tdnet_monthly_r2 import display_code  # noqa: E402

DEFAULT_LEDGER = Path("/tmp/html_ingest_ledger.json")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
HTTP_TIMEOUT = 30.0
_STEM_DATE_RE = re.compile(r"^(\d{8})_")
_CODE_DIR_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_MONTH_TOKEN_RE = re.compile(r"^(\d{4})-(\d{2})$")
_LOOPBACK = frozenset({"127.0.0.1", "localhost"})
_FORCE_HINT_ERRORS = frozenset({"UNIT_CONFLICT", "ELEMENT_SIMILAR_EXISTS"})
_DUP_VALUES_MSG ='months に重複があるとき values は配列か列番号キー（"0"…）のオブジェクトです。'


class AsOfUnusable(ValueError):
    """Selected sidecar as_of is still a known-bad class. Fail the run, do not skip."""


class MappingMismatch(ValueError):
    """POST の mapping や GET years が sidecar・probe と食い違った。"""


class HttpClient(Protocol):
    def post_json(self, url: str, body: Mapping[str, Any]) -> tuple[int, Any]: ...
    def get_json(self, url: str) -> tuple[int, Any]: ...


@dataclass(frozen=True)
class Sidecar:
    code: str
    path: Path
    filename_date: date
    as_of: date
    months: tuple[int, ...]
    rows: tuple[Mapping[str, Any], ...]
    sha256: str


@dataclass(frozen=True)
class LedgerEntry:
    sha256: str
    code: str
    rel: str
    as_of: str
    posted_at: str


class PostedLedger:
    """Skip identity is sidecar bytes. Path rename with the same hash still skips."""

    def __init__(self, path: Path, entries: dict[str, LedgerEntry] | None = None) -> None:
        self.path = path
        self._entries = dict(entries or {})

    def has(self, digest: str) -> bool:
        return digest in self._entries

    def record(self, entry: LedgerEntry) -> None:
        self._entries[entry.sha256] = entry

    def save(self) -> None:
        payload = {
            "entries": {
                digest: {
                    "sha256": e.sha256,
                    "code": e.code,
                    "rel": e.rel,
                    "as_of": e.as_of,
                    "posted_at": e.posted_at,
                }
                for digest, e in self._entries.items()
            }
        }
        atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")

    @staticmethod
    def load(path: Path) -> PostedLedger:
        if not path.is_file():
            return PostedLedger(path, {})
        raw = json.loads(path.read_text(encoding="utf-8"))
        blob = raw.get("entries") if isinstance(raw, dict) else raw
        if not isinstance(blob, dict):
            raise ValueError(f"ledger {path} is not an object")
        entries: dict[str, LedgerEntry] = {}
        for digest, item in blob.items():
            if not isinstance(item, dict):
                continue
            sha = str(item.get("sha256") or digest)
            entries[sha] = LedgerEntry(
                sha256=sha,
                code=str(item.get("code") or ""),
                rel=str(item.get("rel") or ""),
                as_of=str(item.get("as_of") or ""),
                posted_at=str(item.get("posted_at") or ""),
            )
        return PostedLedger(path, entries)


@dataclass(frozen=True)
class PostOk:
    code: str
    fy_end_month: int
    layout: str
    as_of: date
    mapping: dict[str, int] | list[dict[str, int]]
    updated: list[dict[str, Any]]


@dataclass(frozen=True)
class PostErr:
    code: str
    status: int
    error: str
    body: str


def filename_ymd(path: Path) -> date | None:
    m = _STEM_DATE_RE.match(path.name)
    if not m:
        return None
    raw = m.group(1)
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def rank_key(s: Sidecar) -> tuple[date, date]:
    # Filename date first: 9948's older file has a larger (future) as_of.
    return (s.filename_date, s.as_of)


def select_latest_per_code(sidecars: Sequence[Sidecar]) -> dict[str, Sidecar]:
    chosen: dict[str, Sidecar] = {}
    for s in sidecars:
        prev = chosen.get(s.code)
        if prev is None or rank_key(s) > rank_key(prev):
            chosen[s.code] = s
    return chosen


def ingest_body(s: Sidecar, *, force: bool = False) -> dict[str, Any]:
    # force は reparse 後の unit 訂正（伸び率→yoy_pct 等）を人が通すときだけ。
    # cron で常に付けると UNIT_CONFLICT / ELEMENT_SIMILAR_EXISTS が効かない。
    rows = []
    for r in s.rows:
        row = dict(r)
        if force:
            row["force"] = True
        rows.append(row)
    return {
        "months": list(s.months),
        "rows": rows,
        "as_of": s.as_of.isoformat(),
    }


def parse_month_filter(tokens: Sequence[str]) -> frozenset[tuple[int, int]]:
    """'2026-07,2026-08' やその繰り返しを (year, month) にする。"""
    raw_tokens: Sequence[str] = (tokens,) if isinstance(tokens, str) else tokens
    found: set[tuple[int, int]] = set()
    for token in raw_tokens:
        if not isinstance(token, str):
            raise ValueError(f"invalid month token: {token!r}")
        for piece in token.split(","):
            piece = piece.strip()
            if not piece:
                continue
            m = _MONTH_TOKEN_RE.fullmatch(piece)
            if not m:
                raise ValueError(f"invalid month token: {piece!r}")
            year = int(m.group(1))
            month = int(m.group(2))
            if month < 1 or month > 12:
                raise ValueError(f"invalid month token: {piece!r}")
            found.add((year, month))
    if not found:
        raise ValueError("invalid month token: empty")
    return frozenset(found)


def _fmt_ym(pairs: Iterable[tuple[int, int]]) -> str:
    return ",".join(f"{year:04d}-{month:02d}" for year, month in sorted(set(pairs)))


def _column_index_key(key: object) -> int | None:
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key
    if isinstance(key, str) and key.isdigit():
        return int(key)
    return None


def _month_number_key(key: object) -> int | None:
    month = _column_index_key(key)
    if month is None or month < 1 or month > 12:
        return None
    return month


def _ambiguous_column_keys(keys: set[int], months: Sequence[int]) -> bool:
    # 0 が無く、キーが全部「months に含まれる 1..12」だと月番号と列番号が区別できない。
    return bool(
        keys
        and 0 not in keys
        and set(keys) <= set(months)
        and all(1 <= k <= 12 for k in keys)
    )


def column_values(row_values: Any, months: Sequence[int]) -> dict[int, Any]:
    """列インデックス → 値。api._parse_ingest_values_by_column と同じキー規則。"""
    month_list = [int(m) for m in months]
    n = len(month_list)
    has_dup = len(month_list) != len(set(month_list))
    if isinstance(row_values, list):
        if len(row_values) != n:
            raise ValueError(f"values length {len(row_values)} != months {n}")
        return {i: val for i, val in enumerate(row_values)}
    if not isinstance(row_values, dict):
        raise ValueError("values must be a list or object")
    if has_dup:
        by_col: dict[int, Any] = {}
        for key, val in row_values.items():
            idx = _column_index_key(key)
            if idx is None or idx < 0 or idx >= n:
                raise ValueError(f"invalid column key {key!r}")
            by_col[idx] = val
        if _ambiguous_column_keys(set(by_col), month_list):
            raise ValueError(_DUP_VALUES_MSG)
        return by_col
    by_col = {}
    month_set = set(month_list)
    for key, val in row_values.items():
        month = _month_number_key(key)
        if month is None or month not in month_set:
            raise ValueError(f"invalid month key {key!r}")
        by_col[month_list.index(month)] = val
    return by_col


def filter_body(body: dict[str, Any], keep_cols: set[int]) -> dict[str, Any]:
    """months と as_of はそのまま。keep 以外の値キーは落とす（null にはしない）。"""
    months_raw = body.get("months")
    if not isinstance(months_raw, list) or not months_raw:
        raise ValueError("months must be a list")
    months = [int(m) for m in months_raw]
    has_dup = len(months) != len(set(months))
    rows_out: list[dict[str, Any]] = []
    for row in body.get("rows") or []:
        if not isinstance(row, dict):
            raise ValueError("row must be an object")
        parsed = column_values(row.get("values"), months)
        kept = {col: parsed[col] for col in sorted(parsed) if col in keep_cols}
        if not kept:
            continue
        if has_dup and _ambiguous_column_keys(set(kept), months):
            raise ValueError(_DUP_VALUES_MSG)
        if has_dup:
            serialized: dict[str, Any] = {str(col): kept[col] for col in sorted(kept)}
        else:
            serialized = {str(months[col]): kept[col] for col in sorted(kept)}
        new_row = dict(row)
        new_row["values"] = serialized
        rows_out.append(new_row)
    return {
        "months": list(months_raw),
        "rows": rows_out,
        "as_of": body.get("as_of"),
    }


def columns_from_mapping(
    mapping: dict[str, int] | list[dict[str, int]],
    months: Sequence[int],
) -> list[tuple[int, int]]:
    """列順の (month, year)。sidecar months と長さか月が違えば MappingMismatch。"""
    month_list = [int(m) for m in months]
    if isinstance(mapping, dict):
        if len(month_list) != len(set(month_list)) or len(mapping) != len(month_list):
            raise MappingMismatch(
                f"mapping length {len(mapping)} disagrees with months {month_list}"
            )
        cols: list[tuple[int, int]] = []
        for month in month_list:
            if str(month) not in mapping:
                raise MappingMismatch(f"mapping missing month {month} for {month_list}")
            year = mapping[str(month)]
            if isinstance(year, bool) or not isinstance(year, int):
                raise MappingMismatch(f"mapping year {year!r} for month {month}")
            cols.append((month, year))
        return cols
    if isinstance(mapping, list):
        if len(mapping) != len(month_list):
            raise MappingMismatch(
                f"mapping length {len(mapping)} disagrees with months {month_list}"
            )
        cols = []
        for i, item in enumerate(mapping):
            month = item.get("month") if isinstance(item, dict) else None
            year = item.get("year") if isinstance(item, dict) else None
            if isinstance(month, bool) or not isinstance(month, int) or month != month_list[i]:
                raise MappingMismatch(
                    f"mapping[{i}] month {month!r} disagrees with {month_list[i]}"
                )
            if isinstance(year, bool) or not isinstance(year, int):
                raise MappingMismatch(f"mapping[{i}] year {year!r}")
            cols.append((month, year))
        return cols
    raise MappingMismatch(f"mapping type {type(mapping).__name__}")


def _updated_pairs(updated: Sequence[Mapping[str, Any]]) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for item in updated:
        year = item.get("year")
        raw = item.get("months")
        if isinstance(year, bool) or not isinstance(year, int) or not isinstance(raw, list):
            continue
        for month in raw:
            if isinstance(month, bool) or not isinstance(month, int):
                continue
            pairs.append((year, month))
    return pairs


def _as_month_filter(months: object) -> frozenset[tuple[int, int]] | None:
    if months is None:
        return None
    if isinstance(months, frozenset):
        return months
    if isinstance(months, str):
        return parse_month_filter(months)
    if isinstance(months, (set, list, tuple)):
        seq = list(months)
        if seq and all(isinstance(item, tuple) for item in seq):
            return frozenset(seq)
        return parse_month_filter(seq)
    raise ValueError(f"invalid month token: {months!r}")


def _run_suffix(*, force: bool, months: frozenset[tuple[int, int]] | None) -> str:
    extra = ""
    if force:
        extra += " force=1"
    if months:
        extra += " months=" + _fmt_ym(months)
    return extra


def _with_force(
    row: dict[str, Any],
    *,
    force: bool,
    months: frozenset[tuple[int, int]] | None,
) -> dict[str, Any]:
    if not force:
        return row
    out = dict(row)
    out["force"] = True
    if months:
        out["months"] = [f"{year:04d}-{month:02d}" for year, month in sorted(months)]
    return out


def month_end(day: date) -> date:
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def assert_as_of_usable(as_of: date, today: date) -> None:
    """Reject NEED_FY_END-class years and dates past the current JST month.

    High bound is month-end(today), not today: 7416 as_of=2026-09-30 on 15 Sep is real.
    """
    if as_of.year < 2015:
        raise AsOfUnusable(f"as_of {as_of.isoformat()} year < 2015")
    hi = month_end(today)
    if as_of > hi:
        raise AsOfUnusable(f"as_of {as_of.isoformat()} after {hi.isoformat()}")


def parse_sidecar(path: Path, code_dir: str) -> Sidecar:
    want = display_code(code_dir)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not an object")
    json_code = raw.get("code")
    if not isinstance(json_code, str) or not json_code:
        raise ValueError(f"{path} missing code")
    got = display_code(json_code)
    if got != want:
        raise ValueError(f"{path} code {got} != dir {want}")
    months_raw = raw.get("months")
    if not isinstance(months_raw, list) or not months_raw:
        raise ValueError(f"{path} missing months")
    months: list[int] = []
    for m in months_raw:
        if not isinstance(m, int) or isinstance(m, bool) or m < 1 or m > 12:
            raise ValueError(f"{path} invalid months")
        months.append(m)
    rows_raw = raw.get("rows")
    if not isinstance(rows_raw, list):
        raise ValueError(f"{path} missing rows")
    rows = tuple(item for item in rows_raw if isinstance(item, dict))
    if len(rows) != len(rows_raw):
        raise ValueError(f"{path} rows must be objects")
    as_of_raw = raw.get("as_of")
    if not isinstance(as_of_raw, str) or not as_of_raw:
        raise ValueError(f"{path} missing as_of")
    try:
        as_of = date.fromisoformat(as_of_raw)
    except ValueError as e:
        raise ValueError(f"{path} as_of is not YYYY-MM-DD") from e
    if as_of.isoformat() != as_of_raw:
        raise ValueError(f"{path} as_of is not YYYY-MM-DD")
    ymd = filename_ymd(path) or date.min
    digest = sha256_bytes(path.read_bytes())
    return Sidecar(
        code=want,
        path=path,
        filename_date=ymd,
        as_of=as_of,
        months=tuple(months),
        rows=rows,
        sha256=digest,
    )


def discover_sidecars(
    dest: Path,
    *,
    only: frozenset[str] | None,
) -> tuple[list[Sidecar], int]:
    found: list[Sidecar] = []
    failed = 0
    if only is not None:
        paths: list[Path] = []
        for code in sorted(only):
            d = dest / code
            if d.is_dir():
                paths.extend(sorted(d.glob("*.ingest.json")))
    else:
        paths = sorted(dest.glob("**/*.ingest.json"))
    for path in paths:
        if not path.is_file():
            continue
        code_dir = path.parent.name
        if not _CODE_DIR_RE.fullmatch(code_dir):
            print(f"warn skip {path}: parent is not a code dir", flush=True)
            continue
        if only is not None and display_code(code_dir) not in only:
            continue
        try:
            found.append(parse_sidecar(path, code_dir))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            failed += 1
            print(f"fail sidecar {path}: {e}", flush=True)
    return found, failed


def parse_true_codes(config_path: Path) -> list[str]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    items = raw.get("html") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"{config_path} has no html issuers")
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("parse", True) is False:
            continue
        code = display_code(str(item.get("code") or "").strip().upper())
        if not _CODE_DIR_RE.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def dest_is_monthly_root(dest: Path) -> bool:
    return dest.name == "monthly"


def assert_loopback_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in _LOOPBACK:
        raise ValueError(f"base_url must be loopback, got {base_url!r}")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not include credentials")
    return base_url.rstrip("/")


def written_years(updated: Sequence[Mapping[str, Any]]) -> set[int]:
    """Years the API actually wrote. Mapping still names empty future months."""
    out: set[int] = set()
    for item in updated:
        y = item.get("year")
        if isinstance(y, int) and not isinstance(y, bool):
            out.add(y)
    return out


def confirm_years(code: str, years: Sequence[int], updated: Sequence[Mapping[str, Any]]) -> None:
    have = {int(y) for y in years}
    missing = sorted(written_years(updated) - have)
    if missing:
        raise MappingMismatch(f"{code} GET years missing {missing} have={sorted(have)}")


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("code"), str):
            return err["code"]
        if isinstance(payload.get("code"), str):
            return payload["code"]
    return "HTTP_ERROR"


def post_body(client: HttpClient, origin: str, code: str, body: Mapping[str, Any]) -> PostOk | PostErr:
    status, payload = client.post_json(f"{origin}/api/v1/tables/{code}/ingest", body)
    if status == 200:
        ok = _parse_post_ok(code, payload)
        if ok is not None:
            return ok
    return PostErr(code=code, status=status, error=_error_code(payload), body=_body_text(payload))


def post_ingest(client: HttpClient, origin: str, s: Sidecar, *, force: bool = False) -> PostOk | PostErr:
    return post_body(client, origin, s.code, ingest_body(s, force=force))


def post_months_filtered(
    client: HttpClient,
    origin: str,
    s: Sidecar,
    keep: frozenset[tuple[int, int]],
) -> PostOk | PostErr | None:
    """None は失敗をログ済み（値の POST はしていない、または probe 前に落ちた）。"""
    want_nums = {month for _year, month in keep}
    if want_nums.isdisjoint(s.months):
        print(
            f"fail months {s.code}: requested {_fmt_ym(keep)} not in sidecar months {list(s.months)}",
            flush=True,
        )
        return None
    probe = post_body(client, origin, s.code, {
        "months": list(s.months),
        "rows": [],
        "as_of": s.as_of.isoformat(),
    })
    if isinstance(probe, PostErr):
        return probe
    cols = columns_from_mapping(probe.mapping, s.months)
    keep_cols = {i for i, (month, year) in enumerate(cols) if (year, month) in keep}
    found = {(year, month) for i, (month, year) in enumerate(cols) if i in keep_cols}
    missing = keep - found
    if missing:
        print(f"warn months {s.code}: missing {_fmt_ym(missing)}", flush=True)
    if not keep_cols:
        print(f"fail months {s.code}: no column for {_fmt_ym(keep)}", flush=True)
        return None
    print(
        f"months {s.code} keep={_fmt_ym(found)} cols={sorted(keep_cols)}",
        flush=True,
    )
    try:
        body = filter_body(ingest_body(s, force=True), keep_cols)
    except ValueError as e:
        print(f"fail months {s.code}: {e}", flush=True)
        return None
    result = post_body(client, origin, s.code, body)
    if isinstance(result, PostErr):
        return result
    if result.mapping != probe.mapping:
        raise MappingMismatch(
            f"{s.code} filter mapping {result.mapping!r} != probe {probe.mapping!r}"
        )
    outside = [pair for pair in _updated_pairs(result.updated) if pair not in keep]
    if outside:
        raise MappingMismatch(f"{s.code} updated outside --months {outside}")
    return result


def _body_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _parse_post_ok(code: str, payload: Any) -> PostOk | None:
    if not isinstance(payload, dict):
        return None
    fy = payload.get("fy_end_month")
    layout = payload.get("layout")
    as_of_raw = payload.get("as_of")
    mapping = payload.get("mapping")
    updated = payload.get("updated")
    if not isinstance(fy, int) or isinstance(fy, bool) or not isinstance(layout, str):
        return None
    if not isinstance(as_of_raw, str):
        return None
    try:
        as_of = date.fromisoformat(as_of_raw)
    except ValueError:
        return None
    if isinstance(mapping, dict):
        map_out: dict[str, int] | list[dict[str, int]] = {str(k): int(v) for k, v in mapping.items()}
    elif isinstance(mapping, list) and all(isinstance(x, dict) for x in mapping):
        map_out = [{"month": int(x["month"]), "year": int(x["year"])} for x in mapping]
    else:
        return None
    if not isinstance(updated, list):
        updated = []
    return PostOk(
        code=str(payload.get("code") or code),
        fy_end_month=fy,
        layout=layout,
        as_of=as_of,
        mapping=map_out,
        updated=[x for x in updated if isinstance(x, dict)],
    )


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(newurl, code, "redirect not allowed", headers, fp)


class UrllibClient:
    def __init__(self, timeout: float = HTTP_TIMEOUT) -> None:
        self._timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectRedirect(),
        )

    def post_json(self, url: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "stock-analyzer-html-ingest/1.0",
            },
        )
        return self._open(req)

    def get_json(self, url: str) -> tuple[int, Any]:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-analyzer-html-ingest/1.0",
            },
        )
        return self._open(req)

    def _open(self, req: urllib.request.Request) -> tuple[int, Any]:
        try:
            with self._opener.open(req, timeout=self._timeout) as resp:
                return resp.status, _decode_json(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp is not None else b""
            return e.code, _decode_json(body)
        except urllib.error.URLError as e:
            return 0, {"error": {"code": "CLIENT_ERROR", "message": str(e.reason)}}


def _decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _append_report(path: Path | None, row: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(
    dest: Path,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    ledger_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    client: HttpClient | None = None,
    today: date | None = None,
    report_path: Path | None = None,
    config_path: Path | None = None,
    force: bool = False,
    months: frozenset[tuple[int, int]] | Sequence[str] | None = None,
) -> int:
    if force and not only:
        print("error: --force requires --only", flush=True)
        return 2
    if months and not force:
        print("error: --months requires --force", flush=True)
        return 2
    try:
        month_filter = _as_month_filter(months)
    except ValueError as e:
        print(f"error: {e}", flush=True)
        return 2
    if not month_filter:
        month_filter = None
    if not dest.is_dir():
        print(f"ir_monthly_html_ingest dest missing {dest}", flush=True)
        return 2
    want = None
    if only:
        want = frozenset(display_code(c.strip().upper()) for c in only if c.strip())
    sidecars, parse_failed = discover_sidecars(dest, only=want)
    chosen = select_latest_per_code(sidecars)
    if want is not None:
        missing_only = sorted(want - set(chosen))
        if missing_only:
            print(f"warn no sidecar for --only {missing_only}", flush=True)
    if want is None and dest_is_monthly_root(dest):
        cfg = config_path or DEFAULT_CONFIG
        if cfg.is_file():
            try:
                required = parse_true_codes(cfg)
            except (OSError, ValueError, json.JSONDecodeError) as e:
                print(f"fail registry {cfg}: {e}", flush=True)
                return 1
            missing_reg = [c for c in required if c not in chosen]
            if missing_reg:
                print(f"fail missing sidecar for parse:true {missing_reg}", flush=True)
                return 1
    if not chosen:
        print("ir_monthly_html_ingest no sidecars", flush=True)
        return 2
    day = today or jst_today()
    print(
        f"ir_monthly_html_ingest start dest={dest} selected={len(chosen)} "
        f"day={day.isoformat()} dry_run={int(dry_run)}"
        + _run_suffix(force=force, months=month_filter),
        flush=True,
    )
    as_of_failed = 0
    ordered = [chosen[c] for c in sorted(chosen)]
    for s in ordered:
        try:
            assert_as_of_usable(s.as_of, day)
        except AsOfUnusable as e:
            as_of_failed += 1
            print(f"fail as_of {s.code} {s.path.name}: {e}", flush=True)
    if as_of_failed:
        print(
            f"ir_monthly_html_ingest done posted=0 skipped=0 failed={as_of_failed + parse_failed}",
            flush=True,
        )
        return 1
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("", encoding="utf-8")
    if dry_run:
        for s in ordered:
            print(f"plan {s.code} {s.path} as_of={s.as_of.isoformat()} sha256={s.sha256}", flush=True)
            if month_filter:
                print(f"plan-months {s.code} {_fmt_ym(month_filter)}", flush=True)
            _append_report(report_path, _with_force({
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "dry-run",
                "sha256": s.sha256,
                "path": str(s.path),
            }, force=force, months=month_filter))
        print(
            f"ir_monthly_html_ingest dry-run selected={len(ordered)} failed={parse_failed}",
            flush=True,
        )
        return 1 if parse_failed else 0
    try:
        origin = assert_loopback_base(base_url)
    except ValueError as e:
        print(f"fail {e}", flush=True)
        return 1
    http = client if client is not None else UrllibClient()
    ledger = PostedLedger.load(ledger_path or DEFAULT_LEDGER)
    posted = skipped = failed = 0
    failed += parse_failed
    for s in ordered:
        if not force and ledger.has(s.sha256):
            skipped += 1
            print(f"skip {s.code} {s.path.name} ledger", flush=True)
            _append_report(report_path, _with_force({
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "skipped",
                "sha256": s.sha256,
            }, force=force, months=month_filter))
            continue
        if force and ledger.has(s.sha256):
            print(f"force {s.code} {s.path.name} (ledger has sha)", flush=True)
        try:
            if month_filter is not None:
                result = post_months_filtered(http, origin, s, month_filter)
                if result is None:
                    failed += 1
                    _append_report(report_path, _with_force({
                        "code": s.code,
                        "as_of": s.as_of.isoformat(),
                        "status": "failed",
                        "error": "MONTHS",
                    }, force=force, months=month_filter))
                    continue
            else:
                result = post_ingest(http, origin, s, force=force)
        except MappingMismatch as e:
            failed += 1
            print(f"fail mapping {s.code}: {e}", flush=True)
            _append_report(report_path, _with_force({
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "failed",
                "error": str(e),
            }, force=force, months=month_filter))
            continue
        if isinstance(result, PostErr):
            failed += 1
            print(f"fail POST {s.code} status={result.status} {result.error}", flush=True)
            if result.error in _FORCE_HINT_ERRORS:
                print(f"  {result.body} (確かめたら --force --only {s.code})", flush=True)
            _append_report(report_path, _with_force({
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "failed",
                "http": result.status,
                "error": result.error,
            }, force=force, months=month_filter))
            continue
        ok = result
        status = 200
        try:
            years_url = f"{origin}/api/v1/tables/{s.code}"
            g_status, g_payload = http.get_json(years_url)
            if g_status != 200 or not isinstance(g_payload, dict):
                raise MappingMismatch(f"{s.code} GET years status={g_status}")
            years = g_payload.get("years")
            if not isinstance(years, list):
                raise MappingMismatch(f"{s.code} GET years missing")
            confirm_years(s.code, years, ok.updated)
        except MappingMismatch as e:
            failed += 1
            print(f"fail mapping {s.code}: {e}", flush=True)
            _append_report(report_path, _with_force({
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "failed",
                "http": status,
                "mapping": ok.mapping,
                "error": str(e),
            }, force=force, months=month_filter))
            continue
        partial = month_filter is not None
        if not partial:
            rel = str(s.path.relative_to(dest)) if s.path.is_relative_to(dest) else s.path.name
            ledger.record(LedgerEntry(
                sha256=s.sha256,
                code=s.code,
                rel=rel,
                as_of=s.as_of.isoformat(),
                posted_at=_now_iso(),
            ))
            ledger.save()
        posted += 1
        line = f"ok {s.code} as_of={s.as_of.isoformat()} layout={ok.layout} fy={ok.fy_end_month}"
        if partial:
            line += " partial (ledger not updated)"
        print(line, flush=True)
        _append_report(report_path, _with_force({
            "code": s.code,
            "as_of": s.as_of.isoformat(),
            "status": "posted",
            "http": status,
            "mapping": ok.mapping,
            "layout": ok.layout,
            "fy_end_month": ok.fy_end_month,
        }, force=force, months=month_filter))
    print(
        f"ir_monthly_html_ingest done posted={posted} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="POST latest IR HTML ingest sidecars to loopback FastAPI")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="monthly dest directory")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="limit to these codes (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print plan, do not POST")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="sha256 skip ledger (not under dest)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="loopback origin")
    ap.add_argument("--report", default="", help="optional JSONL report path")
    ap.add_argument(
        "--force",
        action="store_true",
        help="POST even if the sidecar sha256 is in the ledger, with row force; requires --only",
    )
    ap.add_argument(
        "--months",
        action="append",
        default=[],
        help="comma separated calendar months YYYY-MM (e.g. 2026-07,2026-08); requires --force",
    )
    args = ap.parse_args(argv)
    report = Path(args.report) if args.report else None
    return run(
        Path(args.dest),
        only=args.only or None,
        dry_run=args.dry_run,
        ledger_path=Path(args.ledger),
        base_url=args.base_url,
        report_path=report,
        force=args.force,
        months=args.months or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())

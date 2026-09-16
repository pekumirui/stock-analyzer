"""IR HTML の `.ingest.json` を loopback FastAPI へ POST する。producer は触らない。

使い方:
  python scripts/ir_monthly_html_ingest.py --dest /tmp/r2/monthly
  python scripts/ir_monthly_html_ingest.py --dest /tmp/r2/monthly --only 2664
  python scripts/ir_monthly_html_ingest.py --dest /tmp/r2/monthly --dry-run
  python scripts/ir_monthly_html_ingest.py --dest /tmp/r2/monthly --verify-mapping

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ir_monthly_r2 import DEFAULT_CONFIG, DEFAULT_DEST, atomic_write, jst_today, sha256_bytes  # noqa: E402
from tdnet_monthly_r2 import display_code  # noqa: E402

DEFAULT_LEDGER = Path("/tmp/html_ingest_ledger.json")
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
HTTP_TIMEOUT = 30.0
_STEM_DATE_RE = re.compile(r"^(\d{8})_")
_CODE_DIR_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_LOOPBACK = frozenset({"127.0.0.1", "localhost"})


class AsOfUnusable(ValueError):
    """Selected sidecar as_of is still a known-bad class. Fail the run, do not skip."""


class MappingMismatch(ValueError):
    """POST mapping / fy_end_month / layout disagreed with MappingCase."""


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
class MappingCase:
    """Hand-checked calendar years for one frozen (code, as_of, months)."""

    code: str
    as_of: date
    months: tuple[int, ...]
    fy_end_month: int
    layout: str
    mapping: dict[str, int] | tuple[dict[str, int], ...]


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


MARCH_FY_MONTHS = (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)
HALOWS_MONTHS = (3, 4, 5, 6, 7, 8)

MAPPING_CASES: tuple[MappingCase, ...] = (
    MappingCase(
        code="2664",
        as_of=date(2026, 8, 31),
        months=(4, 5, 6, 7, 8),
        fy_end_month=3,
        layout="fiscal",
        mapping={
            "4": 2026, "5": 2026, "6": 2026, "7": 2026, "8": 2026,
        },
    ),
    MappingCase(
        code="7512",
        as_of=date(2026, 8, 31),
        months=(3, 4, 5, 6, 7, 8),
        fy_end_month=2,
        layout="fiscal",
        mapping={
            "3": 2026, "4": 2026, "5": 2026, "6": 2026, "7": 2026, "8": 2026,
        },
    ),
    MappingCase(
        code="2742",
        as_of=date(2026, 8, 31),
        months=HALOWS_MONTHS,
        fy_end_month=2,
        layout="fiscal",
        mapping={"3": 2026, "4": 2026, "5": 2026, "6": 2026, "7": 2026, "8": 2026},
    ),
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


def ingest_body(s: Sidecar) -> dict[str, Any]:
    # HTML reparse で unit 訂正（伸び率→yoy_pct 等）を入れられるよう force
    rows = []
    for r in s.rows:
        row = dict(r)
        row["force"] = True
        rows.append(row)
    return {
        "months": list(s.months),
        "rows": rows,
        "as_of": s.as_of.isoformat(),
    }


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


def case_for(s: Sidecar, cases: Sequence[MappingCase]) -> MappingCase | None:
    for case in cases:
        if case.code == s.code and case.as_of == s.as_of and case.months == s.months:
            return case
    return None


def _mapping_equal(
    got: dict[str, int] | list[dict[str, int]],
    expect: dict[str, int] | tuple[dict[str, int], ...],
) -> bool:
    if isinstance(expect, tuple):
        if not isinstance(got, list) or len(got) != len(expect):
            return False
        return all(a == b for a, b in zip(got, expect))
    return got == expect


def assert_mapping(ok: PostOk, case: MappingCase) -> None:
    if ok.fy_end_month != case.fy_end_month or ok.layout != case.layout or not _mapping_equal(ok.mapping, case.mapping):
        raise MappingMismatch(
            f"{case.code} mapping want fy={case.fy_end_month} layout={case.layout} "
            f"{case.mapping!r} got fy={ok.fy_end_month} layout={ok.layout} {ok.mapping!r}"
        )


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


def post_ingest(client: HttpClient, origin: str, s: Sidecar) -> PostOk | PostErr:
    status, payload = client.post_json(f"{origin}/api/v1/tables/{s.code}/ingest", ingest_body(s))
    if status == 200:
        ok = _parse_post_ok(s.code, payload)
        if ok is not None:
            return ok
    return PostErr(code=s.code, status=status, error=_error_code(payload), body=_body_text(payload))


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


def _cases_by_code(cases: Sequence[MappingCase]) -> dict[str, list[MappingCase]]:
    out: dict[str, list[MappingCase]] = {}
    for case in cases:
        out.setdefault(case.code, []).append(case)
    return out


def run(
    dest: Path,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    verify_mapping: bool = False,
    ledger_path: Path | None = None,
    base_url: str = DEFAULT_BASE_URL,
    client: HttpClient | None = None,
    today: date | None = None,
    report_path: Path | None = None,
    config_path: Path | None = None,
    cases: Sequence[MappingCase] | None = None,
) -> int:
    if dry_run and verify_mapping:
        print("error: --dry-run and --verify-mapping cannot be combined", flush=True)
        return 2
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
    mapping_cases = tuple(cases) if cases is not None else MAPPING_CASES
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
        f"day={day.isoformat()} dry_run={int(dry_run)} verify_mapping={int(verify_mapping)}",
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
            _append_report(report_path, {
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "dry-run",
                "sha256": s.sha256,
                "path": str(s.path),
            })
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
    by_code = _cases_by_code(mapping_cases)
    posted = skipped = failed = 0
    failed += parse_failed
    for s in ordered:
        if ledger.has(s.sha256):
            skipped += 1
            print(f"skip {s.code} {s.path.name} ledger", flush=True)
            _append_report(report_path, {
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "skipped",
                "sha256": s.sha256,
            })
            continue
        result = post_ingest(http, origin, s)
        if isinstance(result, PostErr):
            failed += 1
            print(f"fail POST {s.code} status={result.status} {result.error}", flush=True)
            _append_report(report_path, {
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "failed",
                "http": result.status,
                "error": result.error,
            })
            continue
        ok = result
        status = 200
        case = case_for(s, mapping_cases)
        try:
            if case is not None:
                assert_mapping(ok, case)
            elif verify_mapping and s.code in by_code:
                raise MappingMismatch(
                    f"{s.code} STALE_EXPECTED as_of={s.as_of.isoformat()} months={list(s.months)}"
                )
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
            _append_report(report_path, {
                "code": s.code,
                "as_of": s.as_of.isoformat(),
                "status": "failed",
                "http": status,
                "mapping": ok.mapping,
                "error": str(e),
            })
            continue
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
        print(
            f"ok {s.code} as_of={s.as_of.isoformat()} layout={ok.layout} fy={ok.fy_end_month}",
            flush=True,
        )
        _append_report(report_path, {
            "code": s.code,
            "as_of": s.as_of.isoformat(),
            "status": "posted",
            "http": status,
            "mapping": ok.mapping,
            "layout": ok.layout,
            "fy_end_month": ok.fy_end_month,
        })
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
    ap.add_argument("--verify-mapping", action="store_true", help="require MappingCase hits for covered codes")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER), help="sha256 skip ledger (not under dest)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="loopback origin")
    ap.add_argument("--report", default="", help="optional JSONL report path")
    args = ap.parse_args(argv)
    report = Path(args.report) if args.report else None
    return run(
        Path(args.dest),
        only=args.only or None,
        dry_run=args.dry_run,
        verify_mapping=args.verify_mapping,
        ledger_path=Path(args.ledger),
        base_url=args.base_url,
        report_path=report,
    )


if __name__ == "__main__":
    raise SystemExit(main())

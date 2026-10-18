"""ir_monthly_json_ingest の純関数。ネットワークには出ない。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.monthly.ingest import (  # noqa: E402
    apply_issuer_aliases,
    canonicalize_element_label,
    plan_column_years,
)
from app.api.monthly.ingest import INGEST_VIAS  # noqa: E402
from app.monthly.store import SOURCE_VIAS  # noqa: E402
from jobs.monthly.ir_monthly_json_ingest import (  # noqa: E402
    calendar_year,
    fy_start_year,
    ingest_body,
    ingest_from_json,
    ingest_from_payload,
    round_digits,
    spec_for,
)

FIXTURE = ROOT / "tests" / "monthly" / "testdata" / "7649_monthlyreport.json"
CODE5 = "76490"
PROD_2026_SALES = {
    "スギ薬局 全店 売上高(前年比)",
    "スギ薬局 既存 売上高(前年比)",
    "ドラッグストア事業 全店 売上高(前年比)",
    "ドラッグストア事業 既存 売上高(前年比)",
    "調剤事業 全店 売上高(前年比)",
    "調剤事業 既存 売上高(前年比)",
}


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    for row in payload["rows"]:
        if row["element"] == name:
            return row
    _fail(f"missing {name}")
    raise AssertionError


def _canon(element: str, unit: str = "yoy_index") -> str:
    name = canonicalize_element_label(element, unit)
    name = apply_issuer_aliases(CODE5, name, as_of=date(2026, 10, 15))
    return canonicalize_element_label(name, unit)


def test_calendar_year_is_html_data_year() -> None:
    if calendar_year(2026, 3) != 2026:
        _fail("2027年2月期の3月 is JSON year=2026 month=3 (calendar), not 2025")
    if calendar_year(2027, 2) != 2027:
        _fail("FY-end February stays calendar 2027")
    if fy_start_year(2026, 3, 2) != 2026:
        _fail("March 2026 starts FY ending Feb 2027")
    if fy_start_year(2027, 2, 2) != 2026:
        _fail("Feb 2027 belongs to FY that started Mar 2026")
    if fy_start_year(2026, 2, 2) != 2025:
        _fail("Feb 2026 is previous FY")


def test_round_yoy_digits() -> None:
    if round_digits(8.5 + 100.0, 1) != 108.5:
        _fail("8.5 -> 108.5")
    if round_digits(-1.5 + 100.0, 1) != 98.5:
        _fail("-1.5 -> 98.5")
    if round_digits(109.1885, 1) != 109.2:
        _fail(f"109.1885 -> {round_digits(109.1885, 1)}")
    spec = spec_for("7649")
    if spec.digits != 1 or spec.add != 100 or spec.unit != "yoy_index" or spec.fy_end_month != 2:
        _fail(f"7649 spec meta {spec}")


def test_7649_fixture() -> None:
    if not FIXTURE.is_file():
        _fail(f"missing fixture {FIXTURE}")
    payload = ingest_from_json(FIXTURE, code="7649")
    if payload["as_of"] != "2026-10-15":
        _fail(f"as_of {payload['as_of']}")
    if "year" in payload:
        _fail("year must not be sent")
    if payload["months"] != [3, 4, 5, 6, 7, 8, 9]:
        _fail(f"current FY months {payload['months']}")
    names = [r["element"] for r in payload["rows"]]
    if "pdf_path" in names or any("グループ合計" in n for n in names):
        _fail(f"leaked rows {names}")
    if names != [
        "スギ薬局 全店",
        "スギ薬局 既存店",
        "ドラッグストア事業 全店",
        "ドラッグストア事業 既存店",
        "調剤事業 全店",
        "調剤事業 既存店",
    ]:
        _fail(f"elements {names}")
    canon = {_canon(n) for n in names}
    if canon != PROD_2026_SALES:
        _fail(f"canonicalize drifted {sorted(canon)}")
    extra = canon - PROD_2026_SALES
    if extra:
        _fail(f"new rows {extra}")
    group_all = _named(payload, "スギ薬局 全店")
    if group_all["unit"] != "yoy_index" or group_all.get("force"):
        _fail(f"meta {group_all}")
    if group_all["values"][0] != 108.5:
        _fail(f"2026-03 スギ薬局 全店 {group_all['values'][0]} (must be 8.5+100, not 19.4+100)")
    if group_all["values"][-1] != 105.5:
        _fail(f"2026-09 スギ薬局 全店 {group_all['values'][-1]}")
    if 107.8 in group_all["values"]:
        _fail("previous FY 2026-02 (7.8) must not be included")
    existing = _named(payload, "ドラッグストア事業 既存店")
    if existing["values"][3] != 98.5:
        _fail(f"2026-06 ドラッグ既存 {existing['values'][3]}")
    chozai = _named(payload, "調剤事業 全店")
    if chozai["values"][0] != 113.1:
        _fail(f"2026-03 調剤 全店 {chozai['values'][0]}")
    for row in payload["rows"]:
        if any(v != round(v, 1) for v in row["values"] if v is not None):
            _fail(f"not 1 decimal {row['element']} {row['values']}")
        if len(row["values"]) != len(payload["months"]):
            _fail(f"values length {row['element']}")
    layout, mapping = plan_column_years(payload["months"], 2, date(2026, 10, 15))
    if layout != "fiscal":
        _fail(f"layout {layout}")
    if mapping[0] != (3, 2026) or mapping[-1] != (9, 2026):
        _fail(f"calendar assignment {mapping}")
    if any(y != 2026 for _m, y in mapping):
        _fail(f"all current-FY months are calendar 2026 {mapping}")


def test_payload_rounding_and_as_of_override() -> None:
    spec = spec_for("7649")
    raw = {
        "last_updated": "2026-09-15 12:00:00",
        "data": [
            {
                "year": 2026,
                "month": 3,
                    "group_total_all": 8.54,
                    "group_total_existing": 5.56,
                "sugi_total_all": 6.7,
                "sugi_total_existing": 3.2,
                "chozai_total_all": 13.1,
                "chozai_total_existing": 12.0,
            }
        ],
    }
    payload = ingest_from_payload(raw, as_of=date(2026, 4, 15), spec=spec)
    if payload["as_of"] != "2026-04-15":
        _fail(f"explicit as_of {payload['as_of']}")
    got = _named(payload, "スギ薬局 全店")["values"][0]
    if got != 108.5:
        _fail(f"8.54+100 round1 {got}")
    existing = _named(payload, "スギ薬局 既存店")["values"][0]
    if existing != 105.6:
        _fail(f"5.56+100 round1 {existing}")


def _stdout_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        _fail(f"no JSON in {text!r}")
    return json.loads(text[start : end + 1])


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        _fail("empty stdout")
    return lines[-1]


class _Client:
    def __init__(self, status: int, body: Any | None = None) -> None:
        self.status = status
        self.body = {"ok": status == 200} if body is None else body
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posts.append((url, body))
        return self.status, self.body


def test_cli_dry_run() -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_json_ingest import main as cli

    old = sys.stdout
    buf = StringIO()
    sys.stdout = buf
    try:
        rc = cli(["--json", str(FIXTURE), "--code", "7649", "--dry-run"])
        dry_text = buf.getvalue()
        buf.truncate(0)
        buf.seek(0)
        rc_unknown = cli(["--code", "9999", "--dry-run"])
        rc_missing = cli(["--json", str(FIXTURE.parent / "missing.json"), "--dry-run"])
    finally:
        sys.stdout = old
    if rc != 0:
        _fail(f"dry-run rc {rc}")
    if rc_unknown != 2:
        _fail(f"unknown code rc {rc_unknown}")
    if rc_missing != 2:
        _fail(f"missing json rc {rc_missing}")
    if '"as_of": "2026-10-15"' not in dry_text or "スギ薬局 全店" not in dry_text:
        _fail("dry-run JSON")
    if "107.8" in dry_text or "119.4" in dry_text:
        _fail("past-year values leaked into dry-run")
    if "failed=0" not in _last_line(dry_text):
        _fail(f"dry-run summary {_last_line(dry_text)}")


def test_run_uses_injected_getter() -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_json_ingest import run

    called: list[str] = []

    def getter(url: str) -> bytes:
        called.append(url)
        return FIXTURE.read_bytes()

    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = run(json_path=None, as_of=None, code="7649", getter=getter, dry_run=True)
    finally:
        sys.stdout = old
    if rc != 0 or len(called) != 1:
        _fail(f"getter rc={rc} called={called}")
    if "monthlyreport" not in called[0]:
        _fail(f"url {called[0]}")
    parsed = _stdout_json(buf.getvalue())
    if parsed["months"] != [3, 4, 5, 6, 7, 8, 9] or parsed["as_of"] != "2026-10-15":
        _fail(f"injected GET payload {parsed}")


def test_ingest_body_omits_empty_months_and_year() -> None:
    if "ir_json" not in INGEST_VIAS or "ir_json" not in SOURCE_VIAS:
        _fail("ir_json must be an ingest/source via")
    payload = ingest_from_json(FIXTURE, code="7649")
    body = ingest_body(payload)
    if set(body) != {"months", "rows", "as_of", "via"} or body["via"] != "ir_json":
        _fail(f"POST keys {sorted(body)} via={body.get('via')}")
    if "year" in body:
        _fail("year must not be sent")
    if body["as_of"] != "2026-10-15" or body["months"] != [3, 4, 5, 6, 7, 8, 9]:
        _fail(f"body months/as_of {body['months']} {body['as_of']}")
    for row in body["rows"]:
        values = row["values"]
        if not isinstance(values, dict) or not values:
            _fail(f"values must be a non-empty month dict {row}")
        if any(v is None for v in values.values()):
            _fail(f"null month sent {row['element']} {values}")
        if set(values) - {str(m) for m in body["months"]}:
            _fail(f"month outside months {row['element']} {values}")
        if any(r.get("force") for r in (row,)):
            _fail(f"cron rows must not force {row}")
    sparse = {
        "months": [3, 4, 5],
        "as_of": "2026-04-15",
        "rows": [
            {"element": "スギ薬局 全店", "unit": "yoy_index", "values": [108.5, None, 109.3]},
            {"element": "empty", "unit": "yoy_index", "values": [None, None, None]},
        ],
    }
    sparse_body = ingest_body(sparse)
    if "year" in sparse_body:
        _fail("sparse year leaked")
    names = [r["element"] for r in sparse_body["rows"]]
    if names != ["スギ薬局 全店"]:
        _fail(f"empty row must not be sent {names}")
    got = sparse_body["rows"][0]["values"]
    if got != {"3": 108.5, "5": 109.3}:
        _fail(f"null months must be omitted {got}")
    if "4" in got or None in got.values():
        _fail(f"April null leaked {got}")


def test_post_sends_ingest_body() -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_json_ingest import run

    for status, want_rc in ((200, 0), (409, 1)):
        client = _Client(status)
        buf = StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = run(
                json_path=FIXTURE, as_of=None, code="7649",
                dry_run=False, base_url="http://127.0.0.1:8000/", client=client,
            )
        finally:
            sys.stdout = old
        text = buf.getvalue()
        if rc != want_rc:
            _fail(f"POST status {status} rc {rc}")
        if len(client.posts) != 1:
            _fail(f"posts {client.posts}")
        url, body = client.posts[0]
        if url != "http://127.0.0.1:8000/api/v1/tables/7649/ingest":
            _fail(f"POST url {url}")
        if (set(body) != {"months", "rows", "as_of", "via"} or body["via"] != "ir_json"
                or "year" in body or any(v is None for r in body["rows"] for v in r["values"].values())):
            _fail(f"POST body {body}")
        last = _last_line(text)
        if status == 200 and "failed=0" not in last:
            _fail(f"ok summary {last}")
        if status == 409:
            if rc != 1 or "failed=1" not in last:
                _fail(f"fail summary rc={rc} {last}")
            if last.strip().split()[-1] != "failed=1" and "failed=1" not in last:
                _fail(f"failed=K must be on last line {last}")
    client = _Client(200)
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = run(
            json_path=FIXTURE, as_of=None, code="7649",
            dry_run=False, base_url="https://example.test", client=client,
        )
    finally:
        sys.stdout = old
    last = _last_line(buf.getvalue())
    if rc != 1 or client.posts:
        _fail(f"remote base_url must not POST rc={rc}")
    if "failed=1" not in last:
        _fail(f"loopback fail summary {last}")


def test_fail_exit_prints_failed() -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_json_ingest import run

    def getter(_url: str) -> bytes:
        raise OSError("cloudfront down")

    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = run(json_path=None, as_of=None, code="7649", getter=getter)
    finally:
        sys.stdout = old
    last = _last_line(buf.getvalue())
    if rc != 1:
        _fail(f"fetch fail rc {rc}")
    if "failed=1" not in last:
        _fail(f"fetch fail summary {last}")
    if not last.endswith("failed=1"):
        _fail(f"failed=K must be last token {last}")


def main() -> None:
    test_calendar_year_is_html_data_year()
    test_round_yoy_digits()
    test_7649_fixture()
    test_payload_rounding_and_as_of_override()
    test_cli_dry_run()
    test_run_uses_injected_getter()
    test_ingest_body_omits_empty_months_and_year()
    test_post_sends_ingest_body()
    test_fail_exit_prints_failed()
    print("ok")


if __name__ == "__main__":
    main()

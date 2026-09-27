"""ir_monthly_html_ingest の純関数。ネットワークには出ない。"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))

from html_monthly import decode_html_bytes, dumps_ingest  # noqa: E402
from ir_monthly_html_ingest import (  # noqa: E402
    DEFAULT_LEDGER,
    LedgerEntry,
    PostedLedger,
    Sidecar,
    assert_as_of_usable,
    assert_loopback_base,
    column_values,
    filter_body,
    ingest_body,
    main,
    parse_month_filter,
    parse_sidecar,
    run,
    select_latest_per_code,
)
from ir_monthly_html_r2 import ingest_payload, load_html_registry  # noqa: E402

MARCH_FY_MONTHS = (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3)


@dataclass(frozen=True)
class MappingCase:
    """FakeClient が返す POST 応答の元。年の割当そのものは test_monthly_ingest で見る。"""

    code: str
    as_of: date
    months: tuple[int, ...]
    fy_end_month: int
    layout: str
    mapping: dict[str, int]


CAWACHI = MappingCase(
    code="2664",
    as_of=date(2026, 8, 31),
    months=(4, 5, 6, 7, 8),
    fy_end_month=3,
    layout="fiscal",
    mapping={str(m): 2026 for m in (4, 5, 6, 7, 8)},
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


@dataclass
class FakeClient:
    post_status: int = 200
    post_payload: Any = None
    get_status: int = 200
    get_payload: Any = None
    probe_payload: Any = None
    posts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)

    def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posts.append((url, body))
        if self.probe_payload is not None and body.get("rows") == []:
            return self.post_status, self.probe_payload
        return self.post_status, self.post_payload

    def get_json(self, url: str) -> tuple[int, Any]:
        self.gets.append(url)
        return self.get_status, self.get_payload


def _capture(fn: Any) -> tuple[Any, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn()
    return rc, buf.getvalue()


def _sidecar(
    code: str,
    path: Path,
    *,
    filename_date: date,
    as_of: date,
    months: tuple[int, ...] = MARCH_FY_MONTHS,
    sha256: str = "x",
) -> Sidecar:
    return Sidecar(
        code=code,
        path=path,
        filename_date=filename_date,
        as_of=as_of,
        months=months,
        rows=(),
        sha256=sha256,
    )


def _write_sidecar(dest: Path, code: str, name: str, *, as_of: str, months: list[int]) -> Path:
    return _write_rows(
        dest,
        code,
        name,
        as_of=as_of,
        months=months,
        rows=[{"element": "全店", "unit": "yoy_pct", "values": {"8": -1.9}}],
    )


def _write_rows(
    dest: Path,
    code: str,
    name: str,
    *,
    as_of: str,
    months: list[int],
    rows: list[dict[str, Any]],
) -> Path:
    d = dest / code
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    payload = {
        "code": code,
        "months": months,
        "rows": rows,
        "as_of": as_of,
        "year": 2099,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _seed_ledger(path: Path, digest: str, code: str, *, posted_at: str = "2000-01-01T00:00:00+00:00") -> None:
    led = PostedLedger(path)
    led.record(LedgerEntry(
        sha256=digest,
        code=code,
        rel=f"{code}/x.ingest.json",
        as_of="2026-08-31",
        posted_at=posted_at,
    ))
    led.save()


def _ok_payload(case: MappingCase) -> dict[str, Any]:
    return {
        "code": case.code + "0",
        "fy_end_month": case.fy_end_month,
        "layout": case.layout,
        "as_of": case.as_of.isoformat(),
        "mapping": dict(case.mapping) if isinstance(case.mapping, dict) else list(case.mapping),
        "updated": [{"year": 2026, "element": "全店", "months": [8]}],
    }


def _years_payload(case: MappingCase, years: list[int] | None = None) -> dict[str, Any]:
    if years is None:
        years = [2026]
    return {"code": case.code + "0", "years": years}


def test_select_latest_filename_beats_future_as_of() -> None:
    sidecars = [
        _sidecar(
            "9948",
            Path("20260914_9948_アークス_月次.ingest.json"),
            filename_date=date(2026, 9, 14),
            as_of=date(2027, 2, 28),
            months=MARCH_FY_MONTHS,
            sha256="aaa",
        ),
        _sidecar(
            "9948",
            Path("20260915_9948_アークス_月次.ingest.json"),
            filename_date=date(2026, 9, 15),
            as_of=date(2026, 8, 31),
            months=MARCH_FY_MONTHS,
            sha256="bbb",
        ),
    ]
    chosen = select_latest_per_code(sidecars)
    if chosen["9948"].path.name.startswith("20260915_") is False:
        _fail(f"filename date must win: {chosen['9948'].path}")
    if chosen["9948"].sha256 != "bbb":
        _fail(f"kept old hash {chosen['9948'].sha256}")


def test_select_latest_as_of_tiebreak() -> None:
    a = _sidecar("2664", Path("20260915_a.ingest.json"), filename_date=date(2026, 9, 15), as_of=date(2026, 8, 15), sha256="a")
    b = _sidecar("2664", Path("20260915_b.ingest.json"), filename_date=date(2026, 9, 15), as_of=date(2026, 8, 31), sha256="b")
    chosen = select_latest_per_code([a, b])
    if chosen["2664"].sha256 != "b":
        _fail(f"later as_of must win same filename_date: {chosen['2664'].sha256}")


def test_ingest_body_keys_only() -> None:
    s = Sidecar(
        code="2664",
        path=Path("x.ingest.json"),
        filename_date=date(2026, 9, 15),
        as_of=date(2026, 8, 31),
        months=(4, 5, 8),
        rows=({"element": "全店", "unit": "yoy_pct", "values": {"8": -1.9}},),
        sha256="x",
    )
    body = ingest_body(s)
    if set(body) != {"months", "rows", "as_of"}:
        _fail(f"body keys {body.keys()}")
    if "year" in body or "code" in body:
        _fail(f"body leaked {body}")
    if body["as_of"] != "2026-08-31" or body["months"] != [4, 5, 8]:
        _fail(f"body {body}")
    if not body["rows"] or any("force" in r for r in body["rows"]):
        _fail(f"cron rows must not force past UNIT_CONFLICT {body['rows']}")
    forced = ingest_body(s, force=True)
    if not all(r.get("force") is True for r in forced["rows"]):
        _fail(f"--force rows must force unit/label fixes {forced['rows']}")
    if "force" in s.rows[0]:
        _fail("ingest_body mutated the sidecar row")


def test_as_of_gate_month_end_hi() -> None:
    assert_as_of_usable(date(2026, 9, 30), date(2026, 9, 15))
    try:
        assert_as_of_usable(date(2026, 10, 1), date(2026, 9, 15))
    except Exception as e:
        if "2026-10-01" not in str(e):
            _fail(f"month-end hi {e}")
    else:
        _fail("as_of after month-end must fail")
    try:
        assert_as_of_usable(date(2004, 9, 30), date(2026, 9, 15))
    except Exception as e:
        if "2015" not in str(e):
            _fail(f"year floor {e}")
    else:
        _fail("2004 as_of must fail")
    try:
        assert_as_of_usable(date(2027, 5, 31), date(2026, 9, 15))
    except Exception:
        pass
    else:
        _fail("future as_of must fail")


def test_ledger_skip_does_not_post(tmp: Path) -> None:
    dest = tmp / "ledger-skip"
    path = _write_sidecar(
        dest,
        "2664",
        "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31",
        months=list(CAWACHI.months),
    )
    digest = parse_sidecar(path, "2664").sha256
    ledger_path = tmp / "ledger-skip.json"
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    rc = run(
        dest,
        ledger_path=ledger_path,
        client=client,
        today=date(2026, 9, 15),
    )
    if rc != 0:
        _fail(f"first run rc {rc}")
    if len(client.posts) != 1:
        _fail(f"first run posts {client.posts}")
    if client.posts[0][1].get("year") is not None:
        _fail(f"POST body has year {client.posts[0][1]}")
    if any("force" in r for r in client.posts[0][1]["rows"]):
        _fail(f"cron POST must not force rows {client.posts[0][1]}")
    if not PostedLedger.load(ledger_path).has(digest):
        _fail("ledger missing hash")
    client2 = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    rc = run(
        dest,
        ledger_path=ledger_path,
        client=client2,
        today=date(2026, 9, 15),
    )
    if rc != 0:
        _fail(f"second run rc {rc}")
    if client2.posts:
        _fail(f"ledger skip still POSTed {client2.posts}")
    if client2.gets:
        _fail(f"ledger skip still GET {client2.gets}")


def test_confirm_years_uses_updated_not_empty_months(tmp: Path) -> None:
    dest = tmp / "confirm-years"
    _write_sidecar(
        dest,
        "2664",
        "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31",
        months=list(CAWACHI.months),
    )
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload={"code": "26640", "years": [2026]})
    rc = run(
        dest,
        ledger_path=tmp / "confirm-ledger.json",
        client=client,
        today=date(2026, 9, 15),
    )
    if rc != 0:
        _fail(f"empty 2027 month must not fail GET confirm rc={rc}")
    if not client.gets:
        _fail("confirm must GET years")


def test_unit_conflict_fails_without_ledger(tmp: Path) -> None:
    dest = tmp / "unit-conflict"
    path = _write_sidecar(
        dest, "2664", "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31", months=list(CAWACHI.months),
    )
    ledger_path = tmp / "unit-conflict.json"
    client = FakeClient(
        post_status=409,
        post_payload={"error": {
            "code": "UNIT_CONFLICT", "message": "既存の unit と異なります。",
            "existing": "number", "incoming": "yoy_index",
        }},
    )
    rc, out = _capture(lambda: run(
        dest, ledger_path=ledger_path, client=client, today=date(2026, 9, 15),
    ))
    if rc != 1:
        _fail(out or str(rc))
    if "fail POST 2664 status=409 UNIT_CONFLICT" not in out or "--force --only 2664" not in out:
        _fail(out)
    if '"existing": "number"' not in out:
        _fail(out)
    if client.gets:
        _fail("409 must not GET years")
    if PostedLedger.load(ledger_path).has(parse_sidecar(path, "2664").sha256):
        _fail("409 must not ledger")


def test_yoy_number_conflict_fails_without_ledger(tmp: Path) -> None:
    dest = tmp / "yoy-number-conflict"
    path = _write_sidecar(
        dest, "2664", "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31", months=list(CAWACHI.months),
    )
    ledger_path = tmp / "yoy-number-conflict.json"
    client = FakeClient(
        post_status=409,
        post_payload={"error": {
            "code": "YOY_NUMBER_CONFLICT",
            "message": "同じ指標の number と前年比が別行になります。",
            "existing": ["全店 売上高(前年比)"],
            "incoming": "全店 売上高",
        }},
    )
    rc, out = _capture(lambda: run(
        dest, ledger_path=ledger_path, client=client, today=date(2026, 9, 15),
    ))
    if rc != 1:
        _fail(out or str(rc))
    if "fail POST 2664 status=409 YOY_NUMBER_CONFLICT" not in out or "--force --only 2664" not in out:
        _fail(out)
    if '"existing": ["全店 売上高(前年比)"]' not in out:
        _fail(out)
    if client.gets:
        _fail("409 must not GET years")
    if PostedLedger.load(ledger_path).has(parse_sidecar(path, "2664").sha256):
        _fail("409 must not ledger")


def test_as_of_gate_blocks_post(tmp: Path) -> None:
    dest = tmp / "asof-gate"
    _write_sidecar(
        dest,
        "2664",
        "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-10-01",
        months=list(CAWACHI.months),
    )
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    rc = run(
        dest,
        ledger_path=tmp / "asof-ledger.json",
        client=client,
        today=date(2026, 9, 15),
        dry_run=True,
    )
    if rc != 1:
        _fail(f"dry-run as_of gate rc {rc}")
    if client.posts:
        _fail("as_of gate POSTed")
    rc = run(
        dest,
        ledger_path=tmp / "asof-ledger.json",
        client=client,
        today=date(2026, 9, 15),
    )
    if rc != 1 or client.posts:
        _fail(f"as_of gate still posted rc={rc} posts={client.posts}")


def test_loopback_and_default_ledger() -> None:
    if assert_loopback_base("http://127.0.0.1:8000") != "http://127.0.0.1:8000":
        _fail("loopback strip")
    try:
        assert_loopback_base("https://example.test/api")
    except ValueError:
        pass
    else:
        _fail("remote base_url must fail")
    if DEFAULT_LEDGER.as_posix() != "/tmp/html_ingest_ledger.json":
        _fail(f"ledger default {DEFAULT_LEDGER}")


def test_cli_repeatable_only(tmp: Path) -> None:
    dest = tmp / "cli-only"
    _write_sidecar(dest, "2664", "20260915_2664_x.ingest.json", as_of="2026-08-31", months=[4, 5, 8])
    rc = main(["--dest", str(dest), "--only", "2664", "--only", "7512", "--dry-run", "--ledger", str(tmp / "cli-ledger.json")])
    if rc != 0:
        _fail(f"cli dry-run rc {rc}")


def test_parse_month_filter_and_usage(tmp: Path) -> None:
    got = parse_month_filter("2026-07,2026-08")
    if got != {(2026, 7), (2026, 8)}:
        _fail(f"parse {got}")
    if parse_month_filter(["2026-07,2026-08", "2026-09"]) != {(2026, 7), (2026, 8), (2026, 9)}:
        _fail("repeatable tokens")
    for bad in ("2026-13", "2026-7", "2026-00", "July", ""):
        try:
            parse_month_filter(bad)
        except ValueError:
            pass
        else:
            _fail(f"accepted {bad!r}")
    rc, out = _capture(lambda: run(tmp, force=True, ledger_path=tmp / "force-only.json"))
    if rc != 2 or "error: --force requires --only" not in out:
        _fail(out or str(rc))
    rc, out = _capture(lambda: main([
        "--dest", str(tmp), "--force", "--ledger", str(tmp / "force-only-cli.json"),
    ]))
    if rc != 2 or "error: --force requires --only" not in out:
        _fail(out or str(rc))
    rc, out = _capture(lambda: run(
        tmp, only=["2664"], months=frozenset({(2026, 7)}), ledger_path=tmp / "months-force.json",
    ))
    if rc != 2 or "error: --months requires --force" not in out:
        _fail(out or str(rc))
    rc, out = _capture(lambda: main([
        "--dest", str(tmp), "--only", "2664", "--months", "2026-07",
        "--ledger", str(tmp / "months-force-cli.json"),
    ]))
    if rc != 2 or "error: --months requires --force" not in out:
        _fail(out or str(rc))
    rc, out = _capture(lambda: main([
        "--dest", str(tmp), "--force", "--only", "2664", "--months", "2026-13",
        "--ledger", str(tmp / "bad-month-cli.json"),
    ]))
    if rc != 2 or "invalid month" not in out:
        _fail(out or str(rc))
    rc, out = _capture(lambda: run(
        tmp, force=True, only=["2664"], months=["2026-13"], ledger_path=tmp / "bad-month-run.json",
    ))
    if rc != 2 or "invalid month" not in out:
        _fail(out or str(rc))


def test_default_ledger_skip_is_unchanged(tmp: Path) -> None:
    dest = tmp / "default-skip"
    path = _write_sidecar(
        dest, "2664", "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31", months=list(CAWACHI.months),
    )
    digest = parse_sidecar(path, "2664").sha256
    ledger_path = tmp / "default-skip.json"
    _seed_ledger(ledger_path, digest, "2664")
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    report = tmp / "default-skip.jsonl"
    rc, out = _capture(lambda: run(
        dest, ledger_path=ledger_path, client=client, today=date(2026, 9, 15), report_path=report,
    ))
    if rc != 0:
        _fail(out or str(rc))
    if client.posts or client.gets:
        _fail(f"default skip posted {client.posts}")
    if "skip 2664 20260915_2664_カワチ薬品_月次.ingest.json ledger" not in out:
        _fail(out)
    start = next(line for line in out.splitlines() if line.startswith("ir_monthly_html_ingest start "))
    if "force=" in start or "months=" in start:
        _fail(start)
    row = json.loads(report.read_text(encoding="utf-8").splitlines()[-1])
    if row.get("status") != "skipped" or "force" in row or "months" in row:
        _fail(row)


def test_force_bypasses_ledger(tmp: Path) -> None:
    dest = tmp / "force-ledger"
    path = _write_sidecar(
        dest, "2664", "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31", months=list(CAWACHI.months),
    )
    digest = parse_sidecar(path, "2664").sha256
    ledger_path = tmp / "force-ledger.json"
    _seed_ledger(ledger_path, digest, "2664")
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    report = tmp / "force-ledger.jsonl"
    rc, out = _capture(lambda: run(
        dest, only=["26640"], force=True, ledger_path=ledger_path, client=client,
        today=date(2026, 9, 15), report_path=report,
    ))
    if rc != 0:
        _fail(out or str(rc))
    if len(client.posts) != 1 or not client.posts[0][1]["rows"]:
        _fail(f"force posts {client.posts}")
    if not all(r.get("force") is True for r in client.posts[0][1]["rows"]):
        _fail(f"--force must force rows {client.posts[0][1]}")
    if not PostedLedger.load(ledger_path).has(digest):
        _fail("force full post dropped ledger")
    if "force 2664 20260915_2664_カワチ薬品_月次.ingest.json (ledger has sha)" not in out:
        _fail(out)
    start = next(line for line in out.splitlines() if line.startswith("ir_monthly_html_ingest start "))
    if not start.endswith(" force=1") or "months=" in start:
        _fail(start)
    row = json.loads(report.read_text(encoding="utf-8").splitlines()[-1])
    if row.get("force") is not True or "months" in row or row.get("status") != "posted":
        _fail(row)


def test_month_filter_posts_kept_columns_only(tmp: Path) -> None:
    dest = tmp / "months-filter"
    months = [4, 5, 6, 7, 8]
    rows = [
        {"element": "全店", "unit": "yoy_pct", "values": {str(m): float(m) for m in months}},
        {"element": "既存店", "unit": "yoy_pct", "values": {str(m): float(m) + 0.5 for m in months}},
    ]
    path = _write_rows(
        dest, "2664", "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31", months=months, rows=rows,
    )
    digest = parse_sidecar(path, "2664").sha256
    ledger_path = tmp / "months-filter.json"
    mapping = {str(m): 2026 for m in months}
    client = FakeClient(
        post_payload={
            "code": "26640",
            "fy_end_month": CAWACHI.fy_end_month,
            "layout": CAWACHI.layout,
            "as_of": "2026-08-31",
            "mapping": mapping,
            "updated": [{"year": 2026, "element": "全店", "months": [7, 8]}],
        },
        get_payload=_years_payload(CAWACHI),
    )
    report = tmp / "months-filter.jsonl"
    rc, out = _capture(lambda: run(
        dest, only=["2664"], force=True, months=parse_month_filter("2026-07,2026-08"),
        ledger_path=ledger_path, client=client, today=date(2026, 9, 15), report_path=report,
    ))
    if rc != 0:
        _fail(out or str(rc))
    if len(client.posts) != 2:
        _fail(f"posts {len(client.posts)} {out}")
    probe, real = client.posts
    if probe[1]["rows"] != [] or probe[1]["months"] != months or probe[1]["as_of"] != "2026-08-31":
        _fail(probe[1])
    if real[1]["months"] != months or real[1]["as_of"] != "2026-08-31":
        _fail(real[1])
    if len(real[1]["rows"]) != 2:
        _fail(real[1]["rows"])
    for row in real[1]["rows"]:
        if set(row["values"]) != {"7", "8"} or row.get("force") is not True:
            _fail(row)
        if None in row["values"].values():
            _fail(row)
    if PostedLedger.load(ledger_path).has(digest):
        _fail("partial post updated ledger")
    if "partial (ledger not updated)" not in out:
        _fail(out)
    if "months 2664 keep=2026-07,2026-08" not in out:
        _fail(out)
    row = json.loads(report.read_text(encoding="utf-8").splitlines()[-1])
    if row.get("force") is not True or row.get("months") != ["2026-07", "2026-08"]:
        _fail(row)
    start = next(line for line in out.splitlines() if line.startswith("ir_monthly_html_ingest start "))
    if " force=1 " not in start or not start.endswith("months=2026-07,2026-08"):
        _fail(start)


def test_month_filter_uses_server_years(tmp: Path) -> None:
    dest = tmp / "server-years"
    months = [11, 12, 1, 2]
    _write_rows(
        dest, "7416", "20260915_7416_月次.ingest.json",
        as_of="2026-08-31", months=months,
        rows=[{"element": "全店", "unit": "yoy_pct", "values": {str(m): float(m) for m in months}}],
    )
    mapping = {"11": 2025, "12": 2025, "1": 2026, "2": 2026}
    payload = {
        "code": "74160",
        "fy_end_month": 2,
        "layout": "fiscal",
        "as_of": "2026-08-31",
        "mapping": mapping,
        "updated": [{"year": 2026, "element": "全店", "months": [1]}],
    }
    client = FakeClient(post_payload=payload, get_payload={"code": "74160", "years": [2025, 2026]})
    rc, out = _capture(lambda: run(
        dest, only=["7416"], force=True, months=frozenset({(2026, 1)}),
        ledger_path=tmp / "server-years.json", client=client, today=date(2026, 9, 15),
    ))
    if rc != 0:
        _fail(out or str(rc))
    if len(client.posts) != 2:
        _fail(f"2026-01 posts {client.posts} {out}")
    if client.posts[0][1]["rows"] != []:
        _fail(client.posts[0][1])
    if client.posts[1][1]["rows"][0]["values"] != {"1": 1.0}:
        _fail(client.posts[1][1])
    client2 = FakeClient(post_payload=payload, get_payload={"code": "74160", "years": [2025, 2026]})
    rc, out = _capture(lambda: run(
        dest, only=["7416"], force=True, months=frozenset({(2025, 1)}),
        ledger_path=tmp / "server-years-miss.json", client=client2, today=date(2026, 9, 15),
    ))
    if rc != 1:
        _fail(out or str(rc))
    if len(client2.posts) != 1 or client2.posts[0][1]["rows"] != []:
        _fail(f"2025-01 should probe only {client2.posts} {out}")
    if "no column" not in out:
        _fail(out)


def test_duplicate_months_column_index_or_refuse(tmp: Path) -> None:
    dest = tmp / "dup-months"
    months = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    years = [2026] * 10 + [2027, 2027, 2027]
    mapping = [{"month": m, "year": y} for m, y in zip(months, years, strict=True)]
    values = [float(i) for i in range(13)]
    _write_rows(
        dest, "1301", "20260915_1301_月次.ingest.json",
        as_of="2026-08-31", months=months,
        rows=[{"element": "全店", "unit": "yoy_pct", "values": values}],
    )
    payload = {
        "code": "13010",
        "fy_end_month": 2,
        "layout": "fiscal",
        "as_of": "2026-08-31",
        "mapping": mapping,
        "updated": [{"year": 2026, "element": "全店", "months": [3]}],
    }
    client = FakeClient(post_payload=payload, get_payload={"years": [2026, 2027]})
    rc, out = _capture(lambda: run(
        dest, only=["1301"], force=True, months=frozenset({(2026, 3)}),
        ledger_path=tmp / "dup-ok.json", client=client, today=date(2026, 9, 15),
    ))
    if rc != 0:
        _fail(out or str(rc))
    if len(client.posts) != 2:
        _fail(f"dup posts {len(client.posts)} {out}")
    if client.posts[1][1]["rows"][0]["values"] != {"0": 0.0}:
        _fail(client.posts[1][1]["rows"])
    if client.posts[1][1]["months"] != months:
        _fail("months changed")
    client2 = FakeClient(post_payload=payload, get_payload={"years": [2026]})
    rc, out = _capture(lambda: run(
        dest, only=["1301"], force=True, months=frozenset({(2026, 6)}),
        ledger_path=tmp / "dup-bad.json", client=client2, today=date(2026, 9, 15),
    ))
    if rc != 1:
        _fail(out or str(rc))
    if len(client2.posts) != 1 or client2.posts[0][1]["rows"] != []:
        _fail(f"ambiguous selection must not real-POST {client2.posts} {out}")


def test_filter_body_drops_nulls_and_empty_rows() -> None:
    indexed = column_values({"7": 1, "8": None}, [4, 5, 6, 7, 8])
    if indexed != {3: 1, 4: None}:
        _fail(indexed)
    if column_values([1, 2, 3, 4, 5], [4, 5, 6, 7, 8])[3] != 4:
        _fail("list values")
    for bad in ({"9": 1}, [1, 2], "nope", {"x": 1}):
        try:
            column_values(bad, [4, 5, 6, 7, 8])
        except ValueError:
            pass
        else:
            _fail(f"accepted {bad!r}")
    body = {
        "months": [4, 5, 6, 7, 8],
        "as_of": "2026-08-31",
        "rows": [
            {
                "element": "全店",
                "unit": "yoy_pct",
                "force": True,
                "values": {"4": 1.0, "5": None, "6": 3.0, "7": 4.0, "8": None},
            },
            {
                "element": "客数",
                "unit": "yoy_pct",
                "force": True,
                "values": {"4": None, "5": 1.0},
            },
            {
                "element": "客単価",
                "unit": "number",
                "force": True,
                "note": "keep-me",
                "values": [None, None, 3.0, 9.0, None],
            },
        ],
    }
    out = filter_body(body, {2, 3})
    if out["months"] != [4, 5, 6, 7, 8] or out["as_of"] != "2026-08-31":
        _fail(out)
    if [r["element"] for r in out["rows"]] != ["全店", "客単価"]:
        _fail(out["rows"])
    if out["rows"][0]["values"] != {"6": 3.0, "7": 4.0}:
        _fail(out["rows"][0])
    if out["rows"][0]["force"] is not True or out["rows"][0]["unit"] != "yoy_pct":
        _fail(out["rows"][0])
    price = out["rows"][1]
    if price["values"] != {"6": 3.0, "7": 9.0} or price.get("note") != "keep-me" or price["force"] is not True:
        _fail(price)
    if any(v is None for row in out["rows"] for v in row["values"].values()):
        _fail(out)
    dup_months = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    dup = {
        "months": dup_months,
        "as_of": "2026-08-31",
        "rows": [{"element": "全店", "unit": "yoy_pct", "force": True, "values": list(range(13))}],
    }
    kept0 = filter_body(dup, {0})
    if kept0["rows"][0]["values"] != {"0": 0}:
        _fail(kept0)
    try:
        filter_body(dup, {3})
    except ValueError:
        pass
    else:
        _fail("ambiguous column keys must raise")


def test_dry_run_force_months_does_not_post(tmp: Path) -> None:
    dest = tmp / "dry-force"
    _write_sidecar(
        dest, "2664", "20260915_2664_x.ingest.json",
        as_of="2026-08-31", months=[4, 5, 6, 7, 8],
    )
    client = FakeClient()
    rc, out = _capture(lambda: run(
        dest, only=["2664"], dry_run=True, force=True,
        months=parse_month_filter("2026-07,2026-08"),
        ledger_path=tmp / "dry-force.json", client=client, today=date(2026, 9, 15),
    ))
    if rc != 0:
        _fail(out or str(rc))
    if client.posts or client.gets:
        _fail("dry-run posted")
    if "plan 2664 " not in out or "plan-months 2664 2026-07,2026-08" not in out:
        _fail(out)
    if " force=1 " not in out or "months=2026-07,2026-08" not in out:
        _fail(out)


def test_months_missing_from_sidecar_does_not_post(tmp: Path) -> None:
    dest = tmp / "missing-month"
    _write_sidecar(
        dest, "2664", "20260915_2664_x.ingest.json",
        as_of="2026-08-31", months=[4, 5, 6, 7, 8],
    )
    client = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    rc, out = _capture(lambda: run(
        dest, only=["2664"], force=True, months=frozenset({(2026, 9)}),
        ledger_path=tmp / "missing-month.json", client=client, today=date(2026, 9, 15),
    ))
    if rc != 1 or client.posts:
        _fail(f"rc={rc} posts={client.posts} {out}")
    if "not in sidecar months" not in out:
        _fail(out)


def test_fixture_3082_force_months(tmp: Path) -> None:
    iss = next(i for i in load_html_registry(ROOT / "config" / "ir_monthly.json") if i.code == "3082")
    html = decode_html_bytes((ROOT / "tests" / "testdata" / "html_issue248" / "3082.html").read_bytes())
    payload = ingest_payload(html, iss)
    text = dumps_ingest(payload)
    dest = tmp / "fix-3082"
    path = dest / "3082" / "20260925_3082_きちりホールディングス_月次.ingest.json"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    digest = parse_sidecar(path, "3082").sha256
    ledger_path = tmp / "fix-3082.json"
    _seed_ledger(ledger_path, digest, "3082")
    before = ledger_path.read_bytes()
    posted = {
        "code": "30820",
        "fy_end_month": 6,
        "layout": "fiscal",
        "as_of": "2026-08-31",
        "mapping": {"7": 2026, "8": 2026},
        "updated": [{"year": 2026, "element": "全店 売上高(％)", "months": [7, 8]}],
    }
    client = FakeClient(post_payload=posted, get_payload={"code": "30820", "years": [2026]})
    rc, out = _capture(lambda: run(
        dest, ledger_path=ledger_path, client=client, today=date(2026, 9, 25),
    ))
    if rc != 0 or client.posts:
        _fail(f"ledger skip rc={rc} posts={client.posts} {out}")
    if "skip 3082" not in out or "ledger" not in out:
        _fail(out)
    client2 = FakeClient(post_payload=posted, get_payload={"code": "30820", "years": [2026]})
    rc, out = _capture(lambda: run(
        dest, only=["30820"], force=True, months=frozenset({(2026, 7), (2026, 8)}),
        ledger_path=ledger_path, client=client2, today=date(2026, 9, 25),
    ))
    if rc != 0:
        _fail(out or str(rc))
    if len(client2.posts) != 2 or client2.posts[0][1]["rows"] != []:
        _fail(f"3082 posts {client2.posts} {out}")
    body = client2.posts[1][1]
    if body["months"] != [7, 8] or body["as_of"] != "2026-08-31":
        _fail(body)
    by_el = {r["element"]: r["values"] for r in body["rows"]}
    if by_el.get("全店 売上高(％)") != {"7": 109.5, "8": 105.7}:
        _fail(by_el.get("全店 売上高(％)"))
    if by_el.get("全店 客数(％)") != {"7": 106.3, "8": 103.1}:
        _fail(by_el.get("全店 客数(％)"))
    if ledger_path.read_bytes() != before:
        _fail("partial 3082 post rewrote the ledger")


def main_tests() -> None:
    from tempfile import TemporaryDirectory

    test_select_latest_filename_beats_future_as_of()
    test_select_latest_as_of_tiebreak()
    test_ingest_body_keys_only()
    test_as_of_gate_month_end_hi()
    test_loopback_and_default_ledger()
    with TemporaryDirectory() as d:
        p = Path(d)
        test_ledger_skip_does_not_post(p)
        test_confirm_years_uses_updated_not_empty_months(p)
        test_unit_conflict_fails_without_ledger(p)
        test_yoy_number_conflict_fails_without_ledger(p)
        test_as_of_gate_blocks_post(p)
        test_cli_repeatable_only(p)
        test_parse_month_filter_and_usage(p)
        test_default_ledger_skip_is_unchanged(p)
        test_force_bypasses_ledger(p)
        test_month_filter_posts_kept_columns_only(p)
        test_month_filter_uses_server_years(p)
        test_duplicate_months_column_index_or_refuse(p)
        test_dry_run_force_months_does_not_post(p)
        test_months_missing_from_sidecar_does_not_post(p)
        test_fixture_3082_force_months(p)
    test_filter_body_drops_nulls_and_empty_rows()
    print("ok: ir monthly html ingest")


if __name__ == "__main__":
    main_tests()

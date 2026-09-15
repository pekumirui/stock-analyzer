"""ir_monthly_html_ingest の純関数。ネットワークには出ない。"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ir_monthly_html_ingest import (  # noqa: E402
    DEFAULT_LEDGER,
    MARCH_FY_MONTHS,
    MAPPING_CASES,
    MappingCase,
    PostedLedger,
    Sidecar,
    assert_as_of_usable,
    assert_loopback_base,
    ingest_body,
    main,
    parse_sidecar,
    run,
    select_latest_per_code,
)

CAWACHI = next(c for c in MAPPING_CASES if c.code == "2664")


def _fail(msg: str) -> None:
    raise SystemExit(msg)


@dataclass
class FakeClient:
    post_status: int = 200
    post_payload: Any = None
    get_status: int = 200
    get_payload: Any = None
    posts: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    gets: list[str] = field(default_factory=list)

    def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posts.append((url, body))
        return self.post_status, self.post_payload

    def get_json(self, url: str) -> tuple[int, Any]:
        self.gets.append(url)
        return self.get_status, self.get_payload


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
    d = dest / code
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    payload = {
        "code": code,
        "months": months,
        "rows": [{"element": "全店", "unit": "yoy_pct", "values": {"8": -1.9}}],
        "as_of": as_of,
        "year": 2099,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


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
    s = _sidecar(
        "2664",
        Path("x.ingest.json"),
        filename_date=date(2026, 9, 15),
        as_of=date(2026, 8, 31),
        months=(4, 5, 8),
    )
    body = ingest_body(s)
    if set(body) != {"months", "rows", "as_of"}:
        _fail(f"body keys {body.keys()}")
    if "year" in body or "code" in body:
        _fail(f"body leaked {body}")
    if body["as_of"] != "2026-08-31" or body["months"] != [4, 5, 8]:
        _fail(f"body {body}")


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
        cases=(CAWACHI,),
    )
    if rc != 0:
        _fail(f"first run rc {rc}")
    if len(client.posts) != 1:
        _fail(f"first run posts {client.posts}")
    if client.posts[0][1].get("year") is not None:
        _fail(f"POST body has year {client.posts[0][1]}")
    if not PostedLedger.load(ledger_path).has(digest):
        _fail("ledger missing hash")
    client2 = FakeClient(post_payload=_ok_payload(CAWACHI), get_payload=_years_payload(CAWACHI))
    rc = run(
        dest,
        ledger_path=ledger_path,
        client=client2,
        today=date(2026, 9, 15),
        cases=(CAWACHI,),
    )
    if rc != 0:
        _fail(f"second run rc {rc}")
    if client2.posts:
        _fail(f"ledger skip still POSTed {client2.posts}")
    if client2.gets:
        _fail(f"ledger skip still GET {client2.gets}")


def test_mapping_case_mismatch_fails(tmp: Path) -> None:
    dest = tmp / "mismatch"
    _write_sidecar(
        dest,
        "2664",
        "20260915_2664_カワチ薬品_月次.ingest.json",
        as_of="2026-08-31",
        months=list(CAWACHI.months),
    )
    wrong = _ok_payload(CAWACHI)
    wrong["mapping"] = {str(m): 1999 for m in CAWACHI.months}
    client = FakeClient(post_payload=wrong, get_payload=_years_payload(CAWACHI))
    ledger_path = tmp / "mismatch-ledger.json"
    rc = run(
        dest,
        ledger_path=ledger_path,
        client=client,
        today=date(2026, 9, 15),
        cases=(CAWACHI,),
        verify_mapping=True,
    )
    if rc != 1:
        _fail(f"mismatch rc {rc}")
    if not client.posts:
        _fail("mismatch must still POST")
    if PostedLedger.load(ledger_path).has(parse_sidecar(next(dest.glob("**/*.ingest.json")), "2664").sha256):
        _fail("mismatch must not ledger")
    if client.gets:
        _fail("mismatch must not GET years")


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
        cases=(CAWACHI,),
        verify_mapping=True,
    )
    if rc != 0:
        _fail(f"empty 2027 month must not fail GET confirm rc={rc}")
    if not client.gets:
        _fail("confirm must GET years")


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
        cases=(CAWACHI,),
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
        cases=(CAWACHI,),
    )
    if rc != 1 or client.posts:
        _fail(f"as_of gate still posted rc={rc} posts={client.posts}")


def test_dry_run_verify_mapping_usage_error(tmp: Path) -> None:
    dest = tmp / "usage"
    dest.mkdir()
    rc = run(dest, dry_run=True, verify_mapping=True, ledger_path=tmp / "usage-ledger.json")
    if rc != 2:
        _fail(f"usage rc {rc}")


def test_loopback_and_default_ledger() -> None:
    if assert_loopback_base("http://127.0.0.1:8000") != "http://127.0.0.1:8000":
        _fail("loopback strip")
    try:
        assert_loopback_base("https://example.test/api")
    except ValueError:
        pass
    else:
        _fail("remote base_url must fail")
    if DEFAULT_LEDGER != Path("/tmp/html_ingest_ledger.json"):
        _fail(f"ledger default {DEFAULT_LEDGER}")
    if DEFAULT_LEDGER.parts[:2] != ("/", "tmp"):
        _fail(f"ledger must not sit under dest {DEFAULT_LEDGER}")


def test_cli_repeatable_only(tmp: Path) -> None:
    dest = tmp / "cli-only"
    _write_sidecar(dest, "2664", "20260915_2664_x.ingest.json", as_of="2026-08-31", months=[4, 5, 8])
    rc = main(["--dest", str(dest), "--only", "2664", "--only", "7512", "--dry-run", "--ledger", str(tmp / "cli-ledger.json")])
    if rc != 0:
        _fail(f"cli dry-run rc {rc}")


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
        test_mapping_case_mismatch_fails(p)
        test_confirm_years_uses_updated_not_empty_months(p)
        test_as_of_gate_blocks_post(p)
        test_dry_run_verify_mapping_usage_error(p)
        test_cli_repeatable_only(p)
    print("ok: ir monthly html ingest")


if __name__ == "__main__":
    main_tests()

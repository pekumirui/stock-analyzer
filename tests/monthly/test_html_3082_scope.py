"""きちりHD HTML: 全店と既存店全店が別キー。業態は既存へ。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.html_monthly import decode_html_bytes
from jobs.monthly.html_issuers import parse_issuer

from app.monthly.ingest import (
    apply_issuer_aliases,
    canonicalize_element_label,
)

FIXTURE = ROOT / "tests" / "monthly" / "testdata" / "html_issue248" / "3082.html"
AS_OF = date(2026, 8, 31)
TOL = 1e-9

MALL = "モール・郊外型 レストラン業態"
URBAN = "都市型ダイニング業態"
EXPECT = {
    "全店 売上高(前年比)": (109.5, 105.7),
    "全店 客数(前年比)": (106.3, 103.1),
    "全店 店舗数": (129.0, 129.0),
    "既存 売上高(前年比)": (101.6, 97.2),
    "既存 客数(前年比)": (97.0, 93.6),
    "既存 客単価(前年比)": (104.7, 103.9),
    "既存 店舗数": (118.0, 119.0),
    f"{URBAN} 既存 売上高(前年比)": (105.3, 97.7),
    f"{URBAN} 既存 客数(前年比)": (103.5, 96.0),
    f"{URBAN} 既存 客単価(前年比)": (101.7, 101.8),
    f"{URBAN} 店舗数": (46.0, 46.0),
    f"{MALL} 既存 売上高(前年比)": (99.1, 97.5),
    f"{MALL} 既存 客数(前年比)": (94.6, 93.0),
    f"{MALL} 既存 客単価(前年比)": (104.8, 104.8),
    f"{MALL} 店舗数": (55.0, 56.0),
}


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _month(vals: dict, month: int) -> float | None:
    if month in vals:
        raw = vals[month]
    elif str(month) in vals:
        raw = vals[str(month)]
    else:
        return None
    if raw is None:
        return None
    return float(raw)


def _stored_name(element: str, unit: str) -> str:
    s = apply_issuer_aliases(
        "30820", canonicalize_element_label(element, unit), as_of=AS_OF,
    )
    return canonicalize_element_label(s, unit)


def test_3082_scope() -> None:
    if not FIXTURE.is_file():
        _fail(f"missing fixture {FIXTURE}")
    parsed = parse_issuer(decode_html_bytes(FIXTURE.read_bytes()), "3082")
    rows = parsed.get("rows") or []
    by_name: dict[str, dict] = {}
    for row in rows:
        unit = str(row.get("unit") or "number")
        el = str(row.get("element") or "")
        stored = _stored_name(el, unit)
        if stored in by_name:
            _fail(f"collision {stored!r} from {by_name[stored].get('element')!r} and {el!r}")
        by_name[stored] = row

    got_names = set(by_name)
    want_names = set(EXPECT)
    if got_names != want_names:
        extra = sorted(got_names - want_names)
        missing = sorted(want_names - got_names)
        _fail(f"stored names extra={extra!r} missing={missing!r}")

    for name, (v7, v8) in EXPECT.items():
        vals = by_name[name].get("values") or {}
        g7 = _month(vals, 7)
        g8 = _month(vals, 8)
        if g7 is None or abs(g7 - v7) > TOL:
            _fail(f"{name} month 7: {g7!r}, want {v7}")
        if g8 is None or abs(g8 - v8) > TOL:
            _fail(f"{name} month 8: {g8!r}, want {v8}")
    print("ok: html 3082 scope")


if __name__ == "__main__":
    test_3082_scope()

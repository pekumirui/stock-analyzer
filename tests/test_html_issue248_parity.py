"""Issue 248 §A: parser output vs production monthly cells."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT))

from html_monthly import decode_html_bytes  # noqa: E402
from ir_monthly_html_r2 import (  # noqa: E402
    html_to_ingest,
    load_html_registry,
)

from app.monthly_ingest import (  # noqa: E402
    apply_issuer_aliases,
    canonicalize_element_label,
    coerce_monthly_storage,
)

FIXTURE_DIR = ROOT / "tests" / "testdata" / "html_issue248"
PROD_PATH = FIXTURE_DIR / "prod_monthly.json"
TOL = 0.05
ISSUE248_CODES = [
    "141A",
    "2685",
    "2698",
    "2702",
    "3038",
    "3082",
    "3542",
    "3549",
    "3733",
    "6045",
    "7564",
    "7679",
    "8016",
    "8255",
    "9759",
]


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _calendar_year(month: int, as_of: date) -> int:
    if month <= as_of.month:
        return as_of.year
    return as_of.year - 1


def _transform_rows(code: str, as_of: date, rows: list[dict]) -> list[dict]:
    series: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        unit = str(row.get("unit") or "number")
        element = canonicalize_element_label(str(row.get("element") or ""), unit)
        element = apply_issuer_aliases(f"{code}0", element, as_of=as_of)
        stored_unit = unit
        for mk, mv in (row.get("values") or {}).items():
            if mv is None:
                continue
            stored_unit, coerced = coerce_monthly_storage(unit, float(mv))
            if coerced is None:
                continue
            vals = series.setdefault((element, stored_unit), {})
            key = str(int(mk))
            if key not in vals:
                vals[key] = coerced
    return [{"element": el, "unit": unit, "values": vals} for (el, unit), vals in series.items() if vals]


def _prod_index(years: dict) -> dict[tuple[str, str, int, str], float]:
    idx: dict[tuple[str, str, int, str], float] = {}
    for year, payload in years.items():
        if not isinstance(payload, dict) or not payload.get("ok"):
            continue
        for el in payload.get("elements") or []:
            unit = str(el.get("unit") or "")
            name = str(el.get("element") or "")
            for mk, mv in (el.get("values") or {}).items():
                if mv is None:
                    continue
                idx[(name, unit, int(year), str(int(mk)))] = float(mv)
    return idx


def _fixture_html(code: str) -> Path | None:
    p = FIXTURE_DIR / f"{code}.html"
    return p if p.is_file() else None


def test_issue248_html_parity() -> dict[str, str]:
    if not PROD_PATH.is_file():
        print("skip: testdata/html_issue248/prod_monthly.json missing")
        return {}
    prod = json.loads(PROD_PATH.read_text(encoding="utf-8"))
    live = {i.code: i for i in load_html_registry(ROOT / "config" / "ir_monthly.json")}
    results: dict[str, str] = {}
    mismatches: list[str] = []
    for code in ISSUE248_CODES:
        iss = live.get(code)
        if iss is None:
            mismatches.append(f"{code} not in html registry")
            continue
        if not iss.parse:
            results[code] = "skip-parse-false"
            continue
        path = _fixture_html(code)
        if path is None:
            results[code] = "skip-no-fixture"
            continue
        html = decode_html_bytes(path.read_bytes())
        try:
            got = html_to_ingest(
                html, code, table=iss.table, force_unit=iss.force_unit
            )
        except Exception as e:
            mismatches.append(f"{code} parse failed: {e}")
            results[code] = f"parse-fail:{e}"
            continue
        as_of = date.fromisoformat(str(got["as_of"]))
        rows = _transform_rows(code, as_of, got.get("rows") or [])
        pidx = _prod_index(prod.get(code) or {})
        overlap = 0
        n_mismatch = 0
        for row in rows:
            for mk, mv in row["values"].items():
                year = _calendar_year(int(mk), as_of)
                key = (row["element"], row["unit"], year, mk)
                if key not in pidx:
                    hit = None
                    years = sorted({k[2] for k in pidx})
                    for y in years:
                        alt = (row["element"], row["unit"], y, mk)
                        if alt in pidx and abs(mv - pidx[alt]) <= TOL:
                            hit = alt
                            break
                    if hit is None:
                        continue
                    key = hit
                    year = key[2]
                overlap += 1
                pv = pidx[key]
                if abs(mv - pv) > TOL:
                    n_mismatch += 1
                    mismatches.append(
                        f"{code} {row['element']!r} {row['unit']} {year}-{mk}: "
                        f"parser={mv} prod={pv}"
                    )
        if overlap == 0:
            sample = [(r["element"], r["unit"], sorted(r["values"])[:4]) for r in rows[:6]]
            mismatches.append(f"{code} no overlapping cells as_of={as_of} sample={sample}")
            results[code] = "no-overlap"
        elif n_mismatch:
            results[code] = f"overlap={overlap} mismatch={n_mismatch}"
        else:
            results[code] = f"overlap={overlap}"
    hard = [m for m in mismatches if "no overlapping" in m or "parse failed" in m or "not in html" in m]
    if hard:
        _fail("issue248 parity:\n" + "\n".join((hard + mismatches)[:40]))
    results["_gaps"] = mismatches
    return results


def main() -> None:
    results = test_issue248_html_parity()
    gaps = results.pop("_gaps", [])
    print("ok: html issue248 parity", results)
    if gaps:
        print("remaining gaps:")
        for g in gaps[:30]:
            print(" ", g)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Validate (or rewrite) IRBANK golden JSON under data/xbrl_golden/.

Golden refresh uses IRBANK Connect *externally*. This CLI does not call the
network. Default action validates existing files. Optional --from-payload
rewrites one JSON from a pasted raw payload (already-golden schema or
IRBANK field names).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "xbrl_golden"

REQUIRED = (
    "code", "name", "period_type", "fiscal_year", "metrics", "unit",
)
METRICS = (
    "net_sales", "operating_income", "ordinary_income", "net_income",
    "total_assets", "net_assets", "total_liabilities",
    "operating_cf", "investing_cf",
)
# IRBANK Connect get_financials → golden metrics
FIELD_MAP = {
    "sales": "net_sales",
    "net_sales": "net_sales",
    "operating_profit": "operating_income",
    "operating_income": "operating_income",
    "ordinary_income": "ordinary_income",
    "net_income": "net_income",
    "total_assets": "total_assets",
    "net_assets": "net_assets",
    "equity": "net_assets",
    "total_liabilities": "total_liabilities",
    "liabilities": "total_liabilities",
    "operating_cf": "operating_cf",
    "investing_cf": "investing_cf",
}


def golden_files(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.glob("*.json")
        if p.name != "INDEX.json" and p.is_file()
    )


def validate_one(path: Path) -> list[str]:
    errs: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path.name}: {exc}"]
    if not isinstance(data, dict):
        return [f"{path.name}: not a JSON object"]
    for key in REQUIRED:
        if key not in data:
            errs.append(f"{path.name}: missing {key}")
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        errs.append(f"{path.name}: metrics must be an object")
        return errs
    for key in METRICS:
        if key not in metrics:
            errs.append(f"{path.name}: metrics missing {key}")
    if data.get("unit") not in (None, "million_jpy"):
        errs.append(f"{path.name}: unit should be million_jpy")
    pt = data.get("period_type")
    if pt not in ("annual", "quarterly", "full_year"):
        errs.append(f"{path.name}: period_type={pt!r}")
    return errs


def _as_code(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if len(text) == 4:
        return text + "0"
    return text


def _metrics_from(blob: dict[str, Any]) -> dict[str, Any]:
    src = blob.get("metrics") if isinstance(blob.get("metrics"), dict) else blob
    out: dict[str, Any] = {k: None for k in METRICS}
    for src_key, dst in FIELD_MAP.items():
        if src_key in src and src[src_key] is not None:
            out[dst] = src[src_key]
    return out


def payload_to_golden(raw: dict[str, Any]) -> dict[str, Any]:
    inner = raw
    for key in ("data", "financials", "result"):
        if isinstance(raw.get(key), dict):
            inner = raw[key]
            break
    code = _as_code(inner.get("code") or raw.get("code"))
    pt = inner.get("period_type") or raw.get("period_type") or "annual"
    if str(pt).lower() in ("fy", "full_year", "annual"):
        pt = "annual"
    elif str(pt).lower() in ("q1", "q2", "q3", "quarterly"):
        q = str(inner.get("quarter") or raw.get("quarter") or pt).upper()
        pt = "quarterly"
        inner = dict(inner)
        inner.setdefault("quarter", q if q in ("Q1", "Q2", "Q3") else None)
    return {
        "code": code,
        "name": inner.get("name") or raw.get("name") or "",
        "edinet_code": inner.get("edinet_code") or raw.get("edinet_code"),
        "period_type": pt,
        "fiscal_year": inner.get("fiscal_year") or raw.get("fiscal_year"),
        "quarter": inner.get("quarter") if pt == "quarterly" else None,
        "accounting_standard": inner.get("accounting_standard") or raw.get("accounting_standard"),
        "is_consolidated": bool(inner.get("is_consolidated", raw.get("is_consolidated", True))),
        "unit": "million_jpy",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": inner.get("source") or raw.get("source") or "IRBANK Connect get_financials",
        "metrics": _metrics_from(inner),
    }


def write_from_payload(payload_path: Path, out_dir: Path) -> Path:
    raw = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("payload must be a JSON object")
    golden = payload_to_golden(raw)
    if not golden.get("fiscal_year") or not golden.get("code"):
        raise SystemExit("payload needs code and fiscal_year")
    fy = str(golden["fiscal_year"]).replace("/", "")
    q = golden.get("quarter")
    suffix = f"_{q}" if q else ""
    kind = "quarterly" if golden["period_type"] == "quarterly" else "annual"
    dest = out_dir / f"{golden['code']}_{kind}_{fy}{suffix}.json"
    dest.write_text(json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden-dir", default=str(DEFAULT_DIR))
    ap.add_argument(
        "--from-payload",
        help="pasted IRBANK/raw JSON file; rewrite one golden JSON (no network)",
    )
    args = ap.parse_args()
    directory = Path(args.golden_dir)
    if not directory.is_dir():
        print(f"golden dir not found: {directory}", file=sys.stderr)
        return 1
    if args.from_payload:
        dest = write_from_payload(Path(args.from_payload), directory)
        print(f"wrote {dest}")
        return 0
    files = golden_files(directory)
    errs: list[str] = []
    for path in files:
        errs.extend(validate_one(path))
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    print(f"golden ok: {len(files)} files in {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

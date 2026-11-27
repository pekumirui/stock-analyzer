"""TDnet 月次 PDF の `.pdf-ingest.json` を loopback FastAPI へ POST する。producer は触らない。

sidecar は jobs/monthly/tdnet_monthly_pdf_sidecar.py が書く。POST の流れ（latest-per-code・sha256
ledger・--force --only・--months）は jobs/monthly/ir_monthly_html_ingest.py と同じ。違いは次だけ:

- 対象は `*.pdf-ingest.json`。ledger は HTML と別のファイル
- POST の前に dry_run し、サーバの mapping（列ごとの暦年）が sidecar の `columns` と
  一致しなければ POST しない（PDF の読み取りは推定なので二重に確かめる）
- sidecar が一つも無い日は正常終了

使い方:
  python jobs/monthly/tdnet_monthly_pdf_ingest.py --dest /tmp/r2/monthly --dry-run
  python jobs/monthly/tdnet_monthly_pdf_ingest.py --ledger /var/log/app/pdf_ingest_ledger.json
  python jobs/monthly/tdnet_monthly_pdf_ingest.py --ledger /var/log/app/pdf_ingest_ledger.json --force --only 428A

VPS cron (JST, after tdnet_monthly_pdf_sidecar.py 18:20 / 21:20): 18:30 / 21:30 with
  --ledger /var/log/app/pdf_ingest_ledger.json
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from jobs.monthly.ir_monthly_html_ingest import (
    DEFAULT_BASE_URL,
    HttpClient,
    PostErr,
    Sidecar,
    ingest_body,
    post_body,
    run,
)
from jobs.monthly.tdnet_monthly_pdf_sidecar import SIDECAR_SUFFIX
from jobs.monthly.tdnet_monthly_r2 import DEFAULT_DEST

DEFAULT_LEDGER = Path("/tmp/pdf_ingest_ledger.json")
LABEL = "tdnet_monthly_pdf_ingest"


def mapping_columns(months: tuple[int, ...], mapping: Mapping[str, int] | list[dict[str, int]]) -> list[str]:
    """応答の mapping（一意月は {月: 年}、重複時は列順の配列）→ 列ごとの YYYY-MM。"""
    if isinstance(mapping, list):
        return [f"{int(x['year']):04d}-{int(x['month']):02d}" for x in mapping]
    return [f"{int(mapping[str(m)]):04d}-{m:02d}" for m in months]


def check_columns(client: HttpClient, origin: str, s: Sidecar) -> str | None:
    """dry_run の mapping が sidecar の columns と同じか。違えば理由を返す。"""
    if not s.columns or len(s.columns) != len(s.months):
        return "sidecar の columns が months と合わない"
    body: dict[str, Any] = ingest_body(s)
    body["dry_run"] = True
    result = post_body(client, origin, s.code, body)
    if isinstance(result, PostErr):
        return f"dry_run status={result.status} {result.error} {result.body}"
    try:
        got = mapping_columns(s.months, result.mapping)
    except (KeyError, TypeError, ValueError) as e:
        return f"dry_run mapping を読めない: {e}"
    if got != list(s.columns):
        return f"mapping {got} != columns {list(s.columns)}"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="POST latest TDnet monthly PDF sidecars to loopback FastAPI")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="monthly dest directory")
    ap.add_argument("--only", action="append", default=[], help="limit to these codes (repeatable)")
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
        help="calendar YYYY-MM sent as only_months (e.g. 2026-07,2026-08); requires --force",
    )
    args = ap.parse_args(argv)
    return run(
        Path(args.dest),
        only=args.only or None,
        dry_run=args.dry_run,
        ledger_path=Path(args.ledger),
        base_url=args.base_url,
        report_path=Path(args.report) if args.report else None,
        force=args.force,
        months=args.months or None,
        pattern=f"*{SIDECAR_SUFFIX}",
        label=LABEL,
        registry=False,
        empty_ok=True,
        precheck=check_columns,
        source="tdnet",
    )


if __name__ == "__main__":
    raise SystemExit(main())

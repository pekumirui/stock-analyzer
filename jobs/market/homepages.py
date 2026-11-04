"""上場会社のホームページ URL を homepages へ（KAB-319）。

使い方:
  python jobs/market/homepages.py --code 4543 --code 9501 --dry-run
  python jobs/market/homepages.py --code 4543 --code 9501
  python jobs/market/homepages.py --all
  python jobs/market/homepages.py --set 7203 https://global.toyota/jp/
  python jobs/market/homepages.py --set-file config/market/homepage_manual.json --dry-run
  python jobs/market/homepages.py --all --compare-hosts

表 homepages はデプロイの jobs/migrate.py が作る。ここでは作らない。
本番の一括投入は KPI 承認後。この PR では --all を本番に走らせない。
manual は --refresh でも上書きしない。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.market.homepages import (  # noqa: E402
    compare_hosts,
    ordinary_code,
    set_manual,
    sync_homepages,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Save each listed company's homepage URL")
    ap.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    ap.add_argument("--all", action="store_true", help="every ordinary stocks.code")
    ap.add_argument("--dry-run", action="store_true", help="print plan, do not write")
    ap.add_argument(
        "--refresh", action="store_true",
        help="re-pick auto sources; never overwrites manual",
    )
    ap.add_argument("--set", nargs=2, metavar=("CODE", "URL"), help="store a manual URL")
    ap.add_argument(
        "--set-file", metavar="PATH",
        help="store manual URLs from a JSON file (entries: code, url)",
    )
    ap.add_argument(
        "--compare-hosts", action="store_true",
        help="XBRL vs 短信 host match (read only)",
    )
    args = ap.parse_args(argv)
    if args.set is not None or args.set_file is not None:
        if args.all or args.code or args.compare_hosts or (
            args.set is not None and args.set_file is not None
        ):
            ap.error("--set / --set-file cannot be combined with each other or --code / --all / --compare-hosts")
        return args
    if bool(args.all) == bool(args.code):
        ap.error("specify --code or --all")
    return args


def load_manual_entries(path: Path) -> list[tuple[str, str]]:
    """`{"entries": [{"code", "url", "reason"?}, ...]}` を (code, url) にする。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["entries"] if isinstance(data, dict) else data
    return [(str(item["code"]), str(item["url"])) for item in entries]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    try:
        if args.set is not None or args.set_file is not None:
            pairs = (
                [tuple(args.set)] if args.set is not None
                else load_manual_entries(Path(args.set_file))
            )
            lines = []
            put = unchanged = 0
            for code, url in pairs:
                report = set_manual(db, code, url, dry_run=args.dry_run)
                lines.extend(report.lines)
                put += report.put
                unchanged += report.unchanged
            summary = (
                f"homepages {'dry_run ' if args.dry_run else ''}"
                f"set codes={len(pairs)} put={put} unchanged={unchanged}"
            )
        elif args.compare_hosts:
            codes = None if args.all else [ordinary_code(c) for c in args.code]
            cmp = compare_hosts(db, codes)
            lines = cmp.diffs[:30]
            summary = cmp.summary()
        else:
            codes = None if args.all else [ordinary_code(c) for c in args.code]
            report = sync_homepages(
                db,
                codes,
                refresh=args.refresh,
                dry_run=args.dry_run,
            )
            lines = report.lines
            summary = report.summary(dry_run=args.dry_run)
    finally:
        db.close()
    for line in lines:
        print(line, flush=True)
    print(summary, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

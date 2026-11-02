"""最新短信の表紙を tanshin_covers に書く（KAB-325）。

使い方:
  python jobs/market/tanshin_covers.py --code 7203 --dry-run
  python jobs/market/tanshin_covers.py --code 7203
  python jobs/market/tanshin_covers.py --limit 20
  python jobs/market/tanshin_covers.py --sample-tdnet --days 14 --limit 40
  python jobs/market/tanshin_covers.py --probe-homepage --probe-limit 8

表はデプロイの jobs/migrate.py が作る。ここでは作らない。
--sample-tdnet は開示一覧と短信 PDF を限られた件数だけ取る。全件は取らない。
--probe-homepage は、説明資料が有で TDnet に無い会社だけ。ブラウザの User-Agent と間隔。
本番への一括投入はしない。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.market.briefing_docs import (  # noqa: E402
    PROBE_INTERVAL_SEC,
    Pacer,
    browser_open,
)
from app.market.homepages import ordinary_code  # noqa: E402
from app.market.tanshin_cover import (  # noqa: E402
    PROBE_LIMIT_MAX,
    SAMPLE_DAYS_MAX,
    SAMPLE_LIMIT_MAX,
    looks_like_pdf,
    probe_saved,
    sample_from_tdnet,
    sync_covers,
)
from jobs._r2common import http_get  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
TDNET_UA = "stock-analyzer-tanshin-cover/1.0"
PDF_CONFIRM_BYTES = 16


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Record cover flags from the latest earnings flash")
    ap.add_argument("--code", action="append", default=[], help="4 or 5 digit code (repeatable)")
    ap.add_argument("--limit", type=int, help="how many companies (DB order, or sample cap)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sample-tdnet", action="store_true", help="read a small live TDnet sample")
    ap.add_argument("--days", type=int, default=14, help="calendar days of TDnet lists, including today")
    ap.add_argument(
        "--probe-homepage", action="store_true",
        help="try homepages only where material is yes and TDnet is absent",
    )
    ap.add_argument("--probe-limit", type=int, default=8)
    args = ap.parse_args(argv)
    if args.sample_tdnet:
        if args.code:
            ap.error("--sample-tdnet cannot be combined with --code")
        if args.limit is None:
            args.limit = 40
        if args.limit < 1 or args.limit > SAMPLE_LIMIT_MAX:
            ap.error(f"--limit must be 1..{SAMPLE_LIMIT_MAX}")
        if args.days < 1 or args.days > SAMPLE_DAYS_MAX:
            ap.error(f"--days must be 1..{SAMPLE_DAYS_MAX}")
    elif args.probe_homepage and not args.code and args.limit is None:
        pass
    elif bool(args.code) == bool(args.limit):
        ap.error("specify --code or --limit (or --sample-tdnet / --probe-homepage)")
    if args.probe_homepage and (args.probe_limit < 1 or args.probe_limit > PROBE_LIMIT_MAX):
        ap.error(f"--probe-limit must be 1..{PROBE_LIMIT_MAX}")
    return args


def pdf_page1_text(pdf: bytes) -> str:
    """pdftotext で1ページ目。ファイルは一時ディレクトリに置いて消す。"""
    if not looks_like_pdf(pdf):
        raise ValueError("not a pdf")
    with tempfile.TemporaryDirectory(prefix="tanshin-cover-") as tmp:
        path = Path(tmp) / "tanshin.pdf"
        path.write_bytes(pdf)
        proc = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", "-enc", "UTF-8", str(path), "-"],
            check=False,
            capture_output=True,
        )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[:200]
        raise RuntimeError(err or f"pdftotext exit {proc.returncode}")
    return proc.stdout.decode("utf-8", "replace")


def _tdnet_get(url: str) -> bytes:
    return http_get(url, user_agent=TDNET_UA)


def _read_head(url: str, user_agent: str) -> bytes:
    from urllib.request import Request, urlopen

    req = Request(url, headers={"User-Agent": user_agent, "Accept": "application/pdf,*/*"})
    with urlopen(req, timeout=30) as resp:  # nosec B310
        return resp.read(PDF_CONFIRM_BYTES)


def _confirm(url: str) -> bool:
    from urllib.error import HTTPError

    from jobs._r2common import BROWSER_UA

    try:
        return looks_like_pdf(_read_head(url, TDNET_UA))
    except HTTPError as exc:
        if exc.code != 403:
            raise
        return looks_like_pdf(_read_head(url, BROWSER_UA))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    try:
        if args.sample_tdnet:
            report = sample_from_tdnet(
                db,
                days=args.days,
                limit=args.limit,
                today=datetime.now(JST).date(),
                get=_tdnet_get,
                pdf_text=pdf_page1_text,
                confirm=_confirm,
                dry_run=args.dry_run,
            )
            lines = report.lines
            summary = report.summary()
        elif args.code or args.limit:
            codes = [ordinary_code(code) for code in args.code] if args.code else None
            stored = sync_covers(db, codes, limit=args.limit, dry_run=args.dry_run)
            lines = stored.lines
            summary = stored.summary(dry_run=args.dry_run)
        else:
            lines = []
            summary = ""
        if args.probe_homepage:
            probed = probe_saved(
                db,
                browser_open,
                Pacer(PROBE_INTERVAL_SEC),
                limit=args.probe_limit,
                dry_run=args.dry_run,
            )
            lines = [*lines, *probed.lines]
            summary = (summary + " " + probed.summary()).strip()
    finally:
        db.close()
    for line in lines:
        print(line, flush=True)
    if summary:
        print(summary, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

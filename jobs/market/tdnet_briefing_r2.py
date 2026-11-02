"""TDnet の決算説明資料 PDF を data/briefing へ保存する（KAB-327）。

使い方:
  python jobs/market/tdnet_briefing_r2.py 30
  python jobs/market/tdnet_briefing_r2.py 3 --dry-run
  python jobs/market/tdnet_briefing_r2.py 7 --dest /tmp/briefing

N は今日(JST)から遡る日数（当日を含む）。日次 cron は 7（取りこぼしの拾い直し）。
初回の約 30 日は手で一度。デプロイでは走らない。
一覧は jobs/_tdnet.py の fetch_days。表題は app.market.briefing_title.is_material_title。
パスは月次 PDF と同じ {code}/YYYYMMDD_CODE_名前_表題.pdf。既存の PDF は取り直さない。
PDF があるのに索引が無いときだけ行を足し、その件数は reindexed（saved とは別）。
索引は briefing_documents（source=tdnet）。ホームページ分は source=homepage で同じ表。
ネットワークはテストから差し替える。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from app.market.briefing_store import (  # noqa: E402
    SOURCE_TDNET,
    has_saved,
    record_briefing,
)
from app.market.briefing_title import (  # noqa: E402
    compact_title,
    is_material_title,
    spaced_title,
    title_decision,
)
from jobs._r2common import (  # noqa: E402
    abort_if_disconnected,
    date_window,
    fail_if_mount_down,
    http_get as _http_get,
)
from jobs import _tdnet as tdnet_list  # noqa: E402
from jobs.monthly.tdnet_monthly_r2 import (  # noqa: E402
    dest_path,
    jq_lookup_code,
    tdnet_pdf_id,
)
from jobs.share_paths import share_path  # noqa: E402

DEFAULT_DEST = str(share_path("briefing"))
USER_AGENT = "stock-analyzer-tdnet-briefing/1.0"
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 30.0
EXAMPLE_LIMIT = 20


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    """このジョブの識別 UA。403 と AIA は _r2common.http_get。"""
    return _http_get(url, timeout=timeout, user_agent=USER_AGENT)


def read_pdf(path: Path) -> bytes | None:
    """存在する PDF。無い・空・PDF でないときは None。"""
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError as exc:
        from jobs._r2common import is_mount_disconnect_error

        if is_mount_disconnect_error(exc):
            raise
        return None
    if data.startswith(b"%PDF"):
        return data
    return None


def download_pdf(document_url: str, dest: Path) -> bytes:
    data = http_get(document_url)
    if not data or not data.startswith(b"%PDF"):
        raise ValueError(f"not a PDF ({len(data)} bytes)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_saved(rec: dict, pdf_id: str, path: Path, dest: Path, data: bytes) -> None:
    record_briefing(
        source=SOURCE_TDNET,
        document_id=pdf_id,
        code=jq_lookup_code(str(rec.get("company_code") or "")),
        company_name=str(rec.get("company_name") or "") or None,
        pubdate=str(rec.get("pubdate") or ""),
        title=str(rec.get("title") or ""),
        rel_path=path.relative_to(dest).as_posix(),
        url=str(rec.get("document_url") or "") or None,
        sha256=_sha256(data),
        size=len(data),
    )


def _near_miss(title: str) -> bool:
    """説明資料に似た表題。dry-run で落とした例を出すときだけ使う。"""
    text = compact_title(title)
    folded = spaced_title(title).casefold()
    return (
        any(word in text for word in ("説明", "補足", "プレゼン"))
        or "presentation" in folded
        or "briefing" in folded
    )


def select_materials(
    start: date,
    end: date,
    get: Callable[[str], bytes] | None = None,
) -> tuple[list[dict], Counter[str], dict[str, list[dict]]]:
    """開示一覧から説明資料を PDF ID で一意にする。件数は表題の判定ごと。"""
    getter = http_get if get is None else get
    counts: Counter[str] = Counter()
    samples: dict[str, list[dict]] = {"correction": [], "notice": [], "other": []}
    by_pdf: dict[str, dict] = {}
    for rec in tdnet_list.fetch_days(start, end, getter, SLEEP_SEC):
        title = str(rec.get("title") or "")
        decision = title_decision(title)
        counts[decision] += 1
        if not is_material_title(title):
            if _near_miss(title):
                counts[f"near_{decision}"] += 1
                bucket = samples.get(decision)
                if bucket is not None and len(bucket) < EXAMPLE_LIMIT:
                    bucket.append(rec)
            continue
        pdf_id = tdnet_pdf_id(str(rec.get("document_url") or ""))
        if not pdf_id:
            print(f"skip no-pdf-id {rec.get('company_code')} {title}", flush=True)
            counts["no-pdf-id"] += 1
            continue
        by_pdf.setdefault(pdf_id, rec)
    rows = sorted(by_pdf.items(), key=lambda kv: str(kv[1].get("pubdate") or ""))
    return [{"_pdf_id": pdf_id, **rec} for pdf_id, rec in rows], counts, samples


def _print_examples(label: str, records: list[dict], limit: int) -> None:
    for rec in records[:limit]:
        print(
            f"example {label} {rec.get('company_code')} {rec.get('pubdate')} {rec.get('title')}",
            flush=True,
        )


def run(days: int, dest: Path, *, dry_run: bool = False) -> int:
    if not dry_run:
        rc = fail_if_mount_down("tdnet_briefing_r2", dest, require_existing=False)
        if rc is not None:
            return rc
        from app.db import init_db

        init_db()
        dest.mkdir(parents=True, exist_ok=True)
    start, end = date_window(days)
    print(
        f"tdnet_briefing_r2 start days={days} dry_run={int(dry_run)} dest={dest} "
        f"range={start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        flush=True,
    )
    records, counts, samples = select_materials(start, end)
    print(
        "tdnet_briefing_r2 titles "
        f"rows={sum(counts[key] for key in ('accept', 'correction', 'notice', 'other'))} "
        f"accept={counts['accept']}",
        flush=True,
    )
    if dry_run:
        print(
            "tdnet_briefing_r2 near "
            + " ".join(f"{key}={counts[f'near_{key}']}" for key in ("correction", "notice", "other")),
            flush=True,
        )
        _print_examples("accept", records, len(records))
        for label in ("correction", "notice", "other"):
            _print_examples(label, samples[label], EXAMPLE_LIMIT)
        print(
            f"tdnet_briefing_r2 dry-run listed={len(records)} "
            f"near_correction={counts['near_correction']} "
            f"near_notice={counts['near_notice']} near_other={counts['near_other']}",
            flush=True,
        )
        return 0
    saved = skipped = reindexed = failed = 0
    for rec in records:
        pdf_id = rec["_pdf_id"]
        company_code = str(rec.get("company_code") or "")
        path = dest_path(
            dest,
            str(rec.get("pubdate") or ""),
            company_code,
            str(rec.get("company_name") or ""),
            str(rec.get("title") or ""),
        )
        rel = path.relative_to(dest)
        try:
            existing = read_pdf(path)
            if existing is not None:
                if has_saved(SOURCE_TDNET, pdf_id):
                    skipped += 1
                    print(f"skip exists {rel}", flush=True)
                else:
                    index_saved(rec, pdf_id, path, dest, existing)
                    reindexed += 1
                    print(f"reindexed {rel}", flush=True)
                continue
            data = download_pdf(str(rec.get("document_url") or ""), path)
            index_saved(rec, pdf_id, path, dest, data)
            saved += 1
            print(f"saved {rel} ({len(data)} bytes)", flush=True)
        except Exception as exc:
            down = abort_if_disconnected("tdnet_briefing_r2", dest, exc)
            if down is not None:
                return down
            failed += 1
            print(f"fail {rel} id={pdf_id}: {exc}", flush=True)
        time.sleep(SLEEP_SEC)
    print(
        f"tdnet_briefing_r2 done listed={len(records)} "
        f"saved={saved} skipped={skipped} reindexed={reindexed} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch TDnet briefing PDFs into data/briefing")
    ap.add_argument("days", type=int, help="days back from today JST (inclusive)")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--dry-run", action="store_true", help="list matches, do not download")
    args = ap.parse_args(argv)
    if args.days < 1:
        print("days must be a positive integer", file=sys.stderr)
        return 2
    return run(args.days, Path(args.dest), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

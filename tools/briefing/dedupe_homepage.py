"""会社ページの決算説明資料のうち、中身（sha256）が重複している分を一度だけ消す（KAB-440）。

使い方:
  python tools/briefing/dedupe_homepage.py --dry-run
  python tools/briefing/dedupe_homepage.py --ledger /var/log/app/homepage_briefing_ledger.json

KAB-439 より前に入れた分の後始末。本番で流したらこのファイルとテストは消す。
同じ会社・同じ中身の資料が TDnet / IR BANK にあれば会社ページの行を消す。会社ページどうしなら
公開日がいちばん古い 1 本（同じなら番号の小さい方）を残す。読み取り済み（briefing_extracts に行がある）は消さない。
消す順は PDF（手元は data/briefing、OneDrive は rclone deletefile）→ 索引の行。PDF を消せなければ行は残す。
--ledger を付けると、消した番号を homepage_briefing の ledger の dup に入れ、次からは取りに行かない。
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db, run_sqlite_write  # noqa: E402
from app.market.briefing_files import briefing_dir, local_file, remote_path  # noqa: E402
from app.market.briefing_store import (  # noqa: E402
    SOURCE_HOMEPAGE,
    STATUS_SAVED,
    STORAGE_ONEDRIVE,
)
from app.models import BriefingDocument, BriefingExtract  # noqa: E402
from jobs.market.homepage_briefing import load_ledger, save_ledger  # noqa: E402
from jobs.ops.backup_db import (  # noqa: E402
    RCLONE_LIST_TIMEOUT_S,
    RcloneRunFn,
    make_rclone_runner,
    offsite_settings_from_env,
    rclone_flags,
)


@dataclass(frozen=True)
class Drop:
    document_id: str
    code: str
    rel_path: str
    storage: str
    title: str
    keep: str


def find_drops() -> tuple[list[Drop], set[str]]:
    """消す行と、読み取り済みで消さない番号。"""
    db = SessionLocal()
    try:
        others = {
            (code, sha): doc
            for code, sha, doc in db.execute(
                select(BriefingDocument.code, BriefingDocument.sha256, BriefingDocument.document_id).where(
                    BriefingDocument.source != SOURCE_HOMEPAGE,
                    BriefingDocument.status == STATUS_SAVED,
                    BriefingDocument.sha256.is_not(None),
                )
            )
        }
        rows = db.execute(
            select(BriefingDocument)
            .where(
                BriefingDocument.source == SOURCE_HOMEPAGE,
                BriefingDocument.status == STATUS_SAVED,
                BriefingDocument.sha256.is_not(None),
            )
            .order_by(BriefingDocument.pubdate, BriefingDocument.document_id)
        ).scalars().all()
        extracted = set(db.execute(select(BriefingExtract.document_id)).scalars())
    finally:
        db.close()
    first: dict[tuple[str, str], str] = {}
    drops: list[Drop] = []
    held: set[str] = set()
    for row in rows:
        key = (row.code, row.sha256)
        keep = others.get(key) or first.get(key)
        if keep is None:
            first[key] = row.document_id
            continue
        if row.document_id in extracted:
            held.add(row.document_id)
            continue
        drops.append(Drop(row.document_id, row.code, row.rel_path, row.storage or "", row.title, keep))
    return drops, held


def delete_pdf(drop: Drop, root: Path, rclone: RcloneRunFn, environ: Mapping[str, str] | None) -> str | None:
    """消せなければ理由。"""
    if drop.storage == STORAGE_ONEDRIVE:
        result = rclone(["deletefile", remote_path(drop.rel_path, environ=environ), *rclone_flags()], RCLONE_LIST_TIMEOUT_S)
        if result.returncode != 0 and "not found" not in (result.stderr or "").lower():
            return f"rclone deletefile exit={result.returncode} {(result.stderr or '').strip()[:200]}"
        return None
    local_file(drop.rel_path, root).unlink(missing_ok=True)
    return None


def delete_row(document_id: str) -> None:
    db = SessionLocal()
    try:
        def write() -> None:
            row = db.get(BriefingDocument, (SOURCE_HOMEPAGE, document_id))
            if row is not None:
                db.delete(row)

        run_sqlite_write(db, write)
    finally:
        db.close()


def run(
    *,
    dry_run: bool,
    ledger: Path | None = None,
    root: Path | None = None,
    rclone: RcloneRunFn | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    init_db()
    drops, held = find_drops()
    for doc in sorted(held):
        print(f"held extracted {doc}", flush=True)
    base = briefing_dir(environ, root)
    runner = rclone or make_rclone_runner(offsite_settings_from_env(environ).rclone_bin)
    book = load_ledger(ledger) if ledger is not None else None
    deleted = failed = 0
    for drop in drops:
        line = f"{drop.code} {drop.document_id} keep={drop.keep} storage={drop.storage} {drop.title}"
        if dry_run:
            print(f"would delete {line}", flush=True)
            continue
        reason = delete_pdf(drop, base, runner, environ)
        if reason is not None:
            failed += 1
            print(f"fail {line}: {reason}", flush=True)
            continue
        delete_row(drop.document_id)
        if book is not None:
            book.dup[drop.document_id] = drop.keep
        deleted += 1
        print(f"deleted {line}", flush=True)
    if book is not None and ledger is not None and deleted:
        save_ledger(ledger, book)
    onedrive = sum(1 for drop in drops if drop.storage == STORAGE_ONEDRIVE)
    print(
        f"dedupe_homepage dry_run={int(dry_run)} found={len(drops)} onedrive={onedrive} "
        f"deleted={deleted} failed={failed} held={len(held)}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Delete homepage briefing PDFs whose content is already indexed")
    ap.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    ap.add_argument("--ledger", help="homepage_briefing ledger to record deleted ids as dup")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run, ledger=Path(args.ledger) if args.ledger else None)


if __name__ == "__main__":
    raise SystemExit(main())

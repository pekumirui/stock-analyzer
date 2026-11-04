"""会社ごとに最新の決算説明資料から 1 年以上前の PDF を OneDrive へ移す（KAB-345）。

使い方:
  python jobs/market/briefing_offsite.py --dry-run
  python jobs/market/briefing_offsite.py --limit 20
  deploy/cron-run.sh jobs/market/briefing_offsite.py --dry-run

順はコピー、サイズ確認、索引を onedrive に更新、手元の削除。
失敗したファイルは索引も手元も変えない。行は消さない。
cron には入れない。本番の移動は承認後に手で行う。
rclone はテストから差し替える。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from app.db import SessionLocal, init_db, migrate_add_columns  # noqa: E402
from app.market.briefing_files import local_file, remote_path  # noqa: E402
from app.market.briefing_store import (  # noqa: E402
    STORAGE_LOCAL,
    STORAGE_ONEDRIVE,
    fill_blank_storage,
    mark_storage,
)
from app.models import BriefingDocument  # noqa: E402
from jobs.ops.backup_db import (  # noqa: E402
    RCLONE_COPY_TIMEOUT_S,
    RCLONE_LIST_TIMEOUT_S,
    BackupError,
    OffsiteSkip,
    RcloneRunFn,
    WhichFn,
    clip_cmd_output,
    make_rclone_runner,
    offsite_settings_from_env,
    probe_offsite,
    rclone_flags,
)  # noqa: E402
from jobs.share_paths import share_path  # noqa: E402

_FILE_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:_|\.|$)")


class OffsiteError(Exception):
    """このファイルは移せない。索引と手元は変えない。"""


@dataclass(frozen=True)
class Doc:
    source: str
    document_id: str
    code: str
    rel_path: str
    pubdate: str
    storage: str
    size: int | None
    status: str


def document_day(rel_path: str, pubdate: str) -> date | None:
    """ファイル名先頭の YYYYMMDD。無ければ索引の pubdate。"""
    name = Path(rel_path.replace("\\", "/")).name
    match = _FILE_DAY.match(name)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass
    text = (pubdate or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def year_before(day: date) -> date:
    """暦の 1 年前。2 月 29 日は 2 月 28 日。"""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _size_bytes(stdout: str) -> int | None:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    n = data.get("bytes")
    if isinstance(n, bool) or not isinstance(n, int):
        return None
    return n


def load_docs() -> list[Doc]:
    db = SessionLocal()
    try:
        rows = db.query(BriefingDocument).all()
        return [
            Doc(
                source=row.source,
                document_id=row.document_id,
                code=row.code or "",
                rel_path=row.rel_path or "",
                pubdate=row.pubdate or "",
                storage=row.storage or STORAGE_LOCAL,
                size=row.size,
                status=row.status or "",
            )
            for row in rows
        ]
    finally:
        db.close()


def candidates(docs: Sequence[Doc], root: Path) -> list[tuple[Doc, date]]:
    """会社の最新日から 1 年以上前の PDF。最新そのものは残る。"""
    dated: dict[str, list[tuple[Doc, date]]] = defaultdict(list)
    for doc in docs:
        if doc.status != "saved" or not doc.rel_path.lower().endswith(".pdf"):
            continue
        day = document_day(doc.rel_path, doc.pubdate)
        if day is None:
            continue
        dated[doc.code].append((doc, day))
    picked: list[tuple[Doc, date]] = []
    for code in sorted(dated):
        rows = dated[code]
        latest = max(day for _, day in rows)
        cutoff = year_before(latest)
        for doc, day in rows:
            if day > cutoff:
                continue
            if doc.storage == STORAGE_ONEDRIVE:
                try:
                    path = local_file(doc.rel_path, root)
                except ValueError:
                    continue
                if not path.is_file():
                    continue
            elif doc.storage != STORAGE_LOCAL:
                continue
            picked.append((doc, day))
    picked.sort(key=lambda item: (item[0].code, item[1], item[0].rel_path, item[0].source, item[0].document_id))
    return picked


def _remote_size(remote: str, run: RcloneRunFn) -> int:
    listed = run(["size", remote, "--json", *rclone_flags()], RCLONE_LIST_TIMEOUT_S)
    size = _size_bytes(listed.stdout) if listed.returncode == 0 else None
    if size is None:
        raise OffsiteError(
            f"rclone size exit={listed.returncode} {clip_cmd_output(listed.stderr)}"
        )
    return size


def move_one(doc: Doc, root: Path, run: RcloneRunFn, *, environ: Mapping[str, str] | None) -> None:
    """コピー → サイズ → 索引 → 削除。途中の失敗では削除しない。"""
    path = local_file(doc.rel_path, root)
    if not path.is_file():
        raise OffsiteError(f"missing {doc.rel_path}")
    remote = remote_path(doc.rel_path, environ=environ)
    copied = run(["copyto", str(path), remote, *rclone_flags()], RCLONE_COPY_TIMEOUT_S)
    if copied.returncode != 0:
        raise OffsiteError(
            f"rclone copyto exit={copied.returncode} {clip_cmd_output(copied.stderr)}"
        )
    local_size = path.stat().st_size
    remote_size = _remote_size(remote, run)
    if remote_size != local_size:
        raise OffsiteError(f"size mismatch local={local_size} remote={remote_size}")
    mark_storage(doc.source, doc.document_id, STORAGE_ONEDRIVE)
    path.unlink()


def run(
    dest: Path | None = None,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    environ: Mapping[str, str] | None = None,
    which: WhichFn = shutil.which,
    rclone: RcloneRunFn | None = None,
) -> int:
    root = dest if dest is not None else share_path("briefing", environ=environ)
    if not dry_run:
        init_db()
        migrate_add_columns(verbose=False)
        fill_blank_storage()
    docs = load_docs()
    eligible = candidates(docs, root)
    batch = eligible if limit is None else eligible[:limit]
    print(
        f"briefing_offsite start dry_run={int(dry_run)} eligible={len(eligible)} "
        f"batch={len(batch)} dest={root}",
        flush=True,
    )
    if dry_run:
        for doc, day in batch:
            print(f"dry-run {doc.code} {day.isoformat()} {doc.rel_path}", flush=True)
        print(f"briefing_offsite dry-run listed={len(batch)}", flush=True)
        return 0
    if not batch:
        print("briefing_offsite done moved=0 failed=0", flush=True)
        return 0
    settings = offsite_settings_from_env(environ)
    runner = rclone or make_rclone_runner(settings.rclone_bin)
    if rclone is None:
        try:
            probe_offsite(settings, which=which, run=runner)
        except OffsiteSkip as exc:
            print(f"fail briefing_offsite: {exc}", flush=True)
            return 1
        except BackupError as exc:
            print(f"fail briefing_offsite: {exc}", flush=True)
            return 1
    moved = failed = 0
    for doc, day in batch:
        try:
            move_one(doc, root, runner, environ=environ)
        except (OffsiteError, OSError, LookupError, BackupError, ValueError) as exc:
            failed += 1
            print(f"fail {doc.rel_path}: {exc}", flush=True)
            continue
        moved += 1
        print(f"moved {doc.code} {day.isoformat()} {doc.rel_path}", flush=True)
    print(f"briefing_offsite done moved={moved} failed={failed}", flush=True)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Move briefing PDFs older than one year to OneDrive")
    ap.add_argument("--dest", default=None, help="data/briefing の代わり")
    ap.add_argument("--dry-run", action="store_true", help="対象を出すだけ。コピーも索引も削除もしない")
    ap.add_argument("--limit", type=int, default=None, help="この実行で手を付ける最大件数")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        ap.error("--limit は 1 以上")
    dest = Path(args.dest) if args.dest else None
    try:
        return run(dest, dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:
        print(f"fail briefing_offsite: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

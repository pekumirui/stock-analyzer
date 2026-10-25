"""Parse / normalize batch jobs."""
from __future__ import annotations

import json
import multiprocessing
import multiprocessing.context
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import (
    XBRL_CACHE_DIR,
    XBRL_DEST,
    XBRL_DOCUMENTS_FILL_LIMIT,
    XBRL_PARSE_WORKERS,
    XBRL_TEXT_MAX_CHARS,
)
from app.models import Stock, XbrlElement, XbrlElementMapping, XbrlFact, XbrlFiling, XbrlFinancial, XbrlJobRun
from app.xbrl.documents import fill_missing_documents, store_documents
from app.xbrl.normalize import normalize_filing, rebuild_all
from app.xbrl.parser import ParsedPackage
from app.xbrl.store import mark_failed, parse_zip, parse_zip_into_filing

from jobs.xbrl.tdnet_xbrl_r2 import jq_lookup_code

SKIP_PREFIXES = ("_unmatched/", "_nocode/", "_cache/")


def _now() -> datetime:
    return datetime.utcnow()


def start_run(db: Session, job_name: str) -> XbrlJobRun:
    run = XbrlJobRun(job_name=job_name, status="running", started_at=_now(), processed_count=0, error_count=0)
    db.add(run)
    db.commit()
    return run


def finish_run(db: Session, run: XbrlJobRun, status: str, processed: int, errors: int, message: str | None = None) -> None:
    run.status = status
    run.processed_count = processed
    run.error_count = errors
    run.message = message
    run.finished_at = _now()
    db.commit()


def iter_manifest_rows(dest: Path, start: date | None, end: date | None) -> list[dict]:
    man_dir = dest / "manifest"
    if not man_dir.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(man_dir.glob("*.jsonl")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("xbrl_id"):
                rows.append(obj)
    return rows


@dataclass
class _ParsePlan:
    doc_id: str
    code5: str
    rel: str
    sha: str | None
    zip_path: Path


def _plan_row(
    db: Session,
    dest: Path,
    row: dict,
    *,
    retry_failed: bool,
    reparse: bool,
    log: bool,
) -> _ParsePlan | str:
    """Skip / missing checks of parse_manifest_row. A status string, or what to parse."""
    doc_id = str(row["xbrl_id"])
    rel = str(row.get("path") or "")
    if any(rel.startswith(p) or f"/{p}" in f"/{rel}" for p in SKIP_PREFIXES):
        if log:
            print(f"skip unmatched doc_id={doc_id} path={rel}", flush=True)
        return "skipped"
    code5 = jq_lookup_code(str(row.get("code") or row.get("code4") or ""))
    if not code5 or db.get(Stock, code5) is None:
        if log:
            print(f"skip not-in-master doc_id={doc_id} code={code5}", flush=True)
        return "skipped"
    existing = db.query(XbrlFiling).filter_by(source="tdnet", doc_id=doc_id).one_or_none()
    sha = str(row.get("sha256") or "") or None
    if (
        existing
        and existing.parse_status in ("parsed", "skipped")
        and existing.raw_sha256 == sha
        and not reparse
    ):
        return "skipped"
    if existing and existing.parse_status == "failed" and not retry_failed:
        return "skipped"
    zip_path = dest / rel
    if not zip_path.is_file():
        if log:
            print(f"error missing-zip doc_id={doc_id} path={rel}", flush=True)
        return "error"
    return _ParsePlan(doc_id=doc_id, code5=code5, rel=rel, sha=sha, zip_path=zip_path)


def _parse_in_worker(zip_path: Path, title: str, submitted_at: str, cache_dir: Path) -> ParsedPackage | BaseException:
    """Pool target. Exceptions come back as values so the parent records them like a serial parse."""
    try:
        return parse_zip(
            zip_path,
            title=title,
            submitted_at=submitted_at,
            cache_dir=cache_dir,
            text_max=XBRL_TEXT_MAX_CHARS,
        )
    except Exception as exc:
        return exc


def parse_manifest_row(
    db: Session,
    dest: Path,
    row: dict,
    *,
    cache_dir: Path,
    retry_failed: bool = False,
    reparse: bool = False,
    package: ParsedPackage | BaseException | None = None,
) -> str:
    plan = _plan_row(db, dest, row, retry_failed=retry_failed, reparse=reparse, log=True)
    if isinstance(plan, str):
        return plan
    doc_id, code5 = plan.doc_id, plan.code5
    try:
        filing = parse_zip_into_filing(
            db,
            zip_path=plan.zip_path,
            code=code5,
            doc_id=doc_id,
            title=str(row.get("title") or ""),
            raw_path=plan.rel,
            raw_sha256=plan.sha,
            submitted_at=str(row.get("pubdate") or ""),
            cache_dir=cache_dir,
            text_max=XBRL_TEXT_MAX_CHARS,
            package=package,
        )
        print(
            f"parsed doc_id={doc_id} code={code5} status={filing.parse_status}",
            flush=True,
        )
        _store_documents_logged(db, filing, plan.zip_path)
        if filing.parse_status == "parsed":
            return "parsed"
        if filing.parse_status == "skipped":
            return "skipped"
        return "error"
    except Exception as exc:
        filing = db.query(XbrlFiling).filter_by(source="tdnet", doc_id=doc_id).one_or_none()
        if filing is not None:
            mark_failed(db, filing, exc)
        print(f"failed doc_id={doc_id} code={code5} err={exc}", flush=True)
        return "error"


def _store_documents_logged(db: Session, filing: XbrlFiling, zip_path: Path) -> None:
    """原文の HTML（KAB-161）。失敗してもパースの結果は変えない。"""
    try:
        if not store_documents(db, filing, zip_path):
            print(f"documents empty doc_id={filing.doc_id} code={filing.code}", flush=True)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"documents failed doc_id={filing.doc_id} code={filing.code} err={exc}", flush=True)


def _pool_context() -> multiprocessing.context.BaseContext:
    # fork はスレッド・SQLite 接続ごと複製するので避ける。Windows は spawn しかない
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")


def _iter_packages(
    db: Session,
    root: Path,
    rows: list[dict],
    *,
    cache: Path,
    retry_failed: bool,
    reparse: bool,
    workers: int,
) -> Iterator[ParsedPackage | BaseException | None]:
    """One item per row, in row order. ZIPs are parsed ahead in ``workers`` processes.

    Writes stay in the parent and in manifest order, so prior-filing lookups see the same
    DB as a serial run. parse_manifest_row re-checks each row when its turn comes; a row
    that was not parsed ahead (or whose worker died) yields None and is parsed inline.
    """
    if workers <= 1 or len(rows) < 2:
        yield from (None for _ in rows)
        return
    ahead = workers * 2
    with ProcessPoolExecutor(max_workers=workers, mp_context=_pool_context()) as pool:
        futures: dict[int, Future] = {}
        submitted = 0

        def submit_until(limit: int) -> None:
            nonlocal submitted
            while submitted < min(limit, len(rows)):
                row = rows[submitted]
                plan = _plan_row(db, root, row, retry_failed=retry_failed, reparse=reparse, log=False)
                if isinstance(plan, _ParsePlan):
                    try:
                        futures[submitted] = pool.submit(
                            _parse_in_worker,
                            plan.zip_path,
                            str(row.get("title") or ""),
                            str(row.get("pubdate") or ""),
                            cache,
                        )
                    except BrokenProcessPool:
                        pass
                submitted += 1

        for i in range(len(rows)):
            submit_until(i + 1 + ahead)
            fut = futures.pop(i, None)
            package = None
            if fut is not None:
                try:
                    package = fut.result()
                except Exception as exc:  # worker died / result not picklable → parse inline
                    print(f"parse worker failed doc_id={rows[i].get('xbrl_id')} err={exc!r}", flush=True)
            yield package


def run_parse(
    db: Session,
    *,
    dest: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    doc_id: str | None = None,
    retry_failed: bool = False,
    reparse: bool = False,
    cache_dir: Path | None = None,
    workers: int | None = None,
) -> dict[str, int]:
    root = Path(dest or XBRL_DEST)
    cache = Path(cache_dir or XBRL_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    run = start_run(db, "parse")
    rows = iter_manifest_rows(root, start, end)
    if doc_id:
        rows = [r for r in rows if str(r.get("xbrl_id")) == doc_id]
    processed = errors = skipped = 0
    packages = _iter_packages(
        db, root, rows,
        cache=cache, retry_failed=retry_failed, reparse=reparse,
        workers=XBRL_PARSE_WORKERS if workers is None else workers,
    )
    for row, package in zip(rows, packages):
        status = parse_manifest_row(
            db, root, row, cache_dir=cache, retry_failed=retry_failed, reparse=reparse,
            package=package,
        )
        if status == "parsed":
            processed += 1
        elif status == "error":
            errors += 1
        else:
            skipped += 1
    # パース済みで原文が無い filing（デプロイ前の分）を少しずつ埋める
    docs = fill_missing_documents(db, root, limit=XBRL_DOCUMENTS_FILL_LIMIT)
    print(
        f"documents stored={docs['stored']} empty={docs['empty']} missing_zip={docs['missing_zip']} "
        f"errors={docs['errors']}",
        flush=True,
    )
    finish_run(db, run, "success" if errors == 0 else "failed", processed, errors, f"skipped={skipped}")
    return {"processed": processed, "errors": errors, "skipped": skipped}


def filings_pending_normalize(db: Session) -> list[XbrlFiling]:
    """parsed のうち、未正規化か再パース後の filing だけ。"""
    latest = (
        db.query(
            XbrlFinancial.filing_id,
            func.max(XbrlFinancial.generated_at).label("generated_at"),
        )
        .group_by(XbrlFinancial.filing_id)
        .subquery()
    )
    return (
        db.query(XbrlFiling)
        .outerjoin(latest, latest.c.filing_id == XbrlFiling.id)
        .filter(XbrlFiling.parse_status == "parsed")
        .filter(
            (latest.c.generated_at.is_(None))
            | (
                XbrlFiling.parsed_at.isnot(None)
                & (XbrlFiling.parsed_at > latest.c.generated_at)
            )
        )
        .all()
    )


def run_normalize(db: Session, *, rebuild: bool = False, code: str | None = None) -> dict:
    run = start_run(db, "normalize")
    if rebuild:
        stats = rebuild_all(db, code=code)
        finish_run(db, run, "success" if stats["errors"] == 0 else "failed", stats["processed"], stats["errors"])
        return stats
    processed = errors = 0
    pending = filings_pending_normalize(db)
    if code:
        pending = [f for f in pending if f.code == code]
    for filing in pending:
        try:
            stats = normalize_filing(db, filing)
            db.commit()
            processed += 1
            missing = stats.get("missing_required") or []
            if missing:
                print(
                    f"normalize missing-required doc_id={filing.doc_id} code={filing.code} items={missing}",
                    flush=True,
                )
        except Exception as exc:
            db.rollback()
            errors += 1
            print(f"normalize failed doc_id={filing.doc_id} code={filing.code} err={exc}", flush=True)
    skipped = db.query(XbrlFiling).filter_by(parse_status="parsed").count() - processed - errors
    if skipped < 0:
        skipped = 0
    finish_run(db, run, "success" if errors == 0 else "failed", processed, errors, f"skipped={skipped}")
    return {"processed": processed, "errors": errors, "skipped": skipped}


def report_unmapped(db: Session, limit: int = 50) -> list[dict]:
    mapped = db.query(XbrlElementMapping.namespace_key, XbrlElementMapping.local_name).distinct().all()
    mapped_set = {(ns, name) for ns, name in mapped}
    rows = (
        db.query(XbrlElement, func.count(XbrlFact.id))
        .join(XbrlFact, XbrlFact.element_id == XbrlElement.id)
        .filter(XbrlElement.item_type == "monetary")
        .group_by(XbrlElement.id)
        .order_by(func.count(XbrlFact.id).desc())
        .all()
    )
    out = []
    for el, count in rows:
        if (el.namespace_key, el.local_name) in mapped_set:
            continue
        out.append({
            "id": el.id,
            "namespace_key": el.namespace_key,
            "local_name": el.local_name,
            "label_ja": el.label_ja,
            "fact_count": int(count),
        })
        if len(out) >= limit:
            break
    return out

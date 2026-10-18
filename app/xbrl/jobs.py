"""Parse / normalize batch jobs."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import XBRL_CACHE_DIR, XBRL_DEST, XBRL_TEXT_MAX_CHARS
from app.models import Stock, XbrlElement, XbrlElementMapping, XbrlFact, XbrlFiling, XbrlFinancial, XbrlJobRun
from app.xbrl.normalize import normalize_filing, rebuild_all
from app.xbrl.store import mark_failed, parse_zip_into_filing

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


def parse_manifest_row(
    db: Session,
    dest: Path,
    row: dict,
    *,
    cache_dir: Path,
    retry_failed: bool = False,
) -> str:
    doc_id = str(row["xbrl_id"])
    rel = str(row.get("path") or "")
    if any(rel.startswith(p) or f"/{p}" in f"/{rel}" for p in SKIP_PREFIXES):
        print(f"skip unmatched doc_id={doc_id} path={rel}", flush=True)
        return "skipped"
    code5 = jq_lookup_code(str(row.get("code") or row.get("code4") or ""))
    if not code5 or db.get(Stock, code5) is None:
        print(f"skip not-in-master doc_id={doc_id} code={code5}", flush=True)
        return "skipped"
    existing = db.query(XbrlFiling).filter_by(source="tdnet", doc_id=doc_id).one_or_none()
    sha = str(row.get("sha256") or "") or None
    if existing and existing.parse_status in ("parsed", "skipped") and existing.raw_sha256 == sha:
        return "skipped"
    if existing and existing.parse_status == "failed" and not retry_failed:
        return "skipped"
    zip_path = dest / rel
    if not zip_path.is_file():
        print(f"error missing-zip doc_id={doc_id} path={rel}", flush=True)
        return "error"
    try:
        filing = parse_zip_into_filing(
            db,
            zip_path=zip_path,
            code=code5,
            doc_id=doc_id,
            title=str(row.get("title") or ""),
            raw_path=rel,
            raw_sha256=sha,
            submitted_at=str(row.get("pubdate") or ""),
            cache_dir=cache_dir,
            text_max=XBRL_TEXT_MAX_CHARS,
        )
        print(
            f"parsed doc_id={doc_id} code={code5} status={filing.parse_status}",
            flush=True,
        )
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


def run_parse(
    db: Session,
    *,
    dest: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    doc_id: str | None = None,
    retry_failed: bool = False,
    cache_dir: Path | None = None,
) -> dict[str, int]:
    root = Path(dest or XBRL_DEST)
    cache = Path(cache_dir or XBRL_CACHE_DIR)
    cache.mkdir(parents=True, exist_ok=True)
    run = start_run(db, "parse")
    rows = iter_manifest_rows(root, start, end)
    if doc_id:
        rows = [r for r in rows if str(r.get("xbrl_id")) == doc_id]
    processed = errors = skipped = 0
    for row in rows:
        status = parse_manifest_row(db, root, row, cache_dir=cache, retry_failed=retry_failed)
        if status == "parsed":
            processed += 1
        elif status == "error":
            errors += 1
        else:
            skipped += 1
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

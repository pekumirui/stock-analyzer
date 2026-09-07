"""Persist a parsed XBRL package into the DB (filing-scoped, idempotent)."""
from __future__ import annotations

import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.orm import Session

from app.config import XBRL_CACHE_DIR, XBRL_TEXT_MAX_CHARS
from app.models import Stock, XbrlContext, XbrlElement, XbrlFact, XbrlFiling
from app.xbrl.context import classify_title
from app.xbrl.parser import extract_zip, namespace_is_standard, parse_package


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_submitted(raw: str) -> datetime:
    text = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if "T" in text or " " in text else text[:10], fmt)
        except ValueError:
            continue
    return _now()


def delete_filing_payload(db: Session, filing_id: int) -> None:
    facts = db.query(XbrlFact).filter_by(filing_id=filing_id).all()
    for fact in facts:
        db.delete(fact)
    ctxs = db.query(XbrlContext).filter_by(filing_id=filing_id).all()
    for ctx in ctxs:
        db.delete(ctx)
    db.flush()


def upsert_element(db: Session, *, uri: str, key: str, local: str, labels: dict, is_custom: bool, numeric: bool) -> XbrlElement:
    row = (
        db.query(XbrlElement)
        .filter_by(namespace_uri=uri, local_name=local)
        .one_or_none()
    )
    if row is None:
        row = XbrlElement(
            namespace_uri=uri,
            namespace_key=key,
            local_name=local,
            first_seen_at=_now(),
        )
        db.add(row)
        db.flush()
    row.namespace_key = key
    if labels.get("label_ja"):
        row.label_ja = labels["label_ja"]
    if labels.get("label_en"):
        row.label_en = labels["label_en"]
    if row.item_type is None:
        row.item_type = "monetary" if numeric else "string"
    row.is_custom = is_custom
    return row


def parse_zip_into_filing(
    db: Session,
    *,
    zip_path: Path,
    code: str,
    doc_id: str,
    title: str,
    raw_path: str,
    raw_sha256: str | None,
    submitted_at: str,
    cache_dir: Path | None = None,
    text_max: int | None = None,
) -> XbrlFiling:
    stock = db.get(Stock, code)
    if stock is None:
        raise ValueError(f"NOT_IN_MASTER {code}")
    meta = classify_title(title)
    filing = (
        db.query(XbrlFiling)
        .filter_by(source="tdnet", doc_id=doc_id)
        .one_or_none()
    )
    if filing is None:
        filing = XbrlFiling(
            code=code,
            source="tdnet",
            doc_id=doc_id,
            raw_path=raw_path,
            submitted_at=_parse_submitted(submitted_at),
            created_at=_now(),
        )
        db.add(filing)
        db.flush()
    filing.code = code
    filing.title = title
    filing.raw_path = raw_path
    filing.raw_sha256 = raw_sha256
    filing.submitted_at = _parse_submitted(submitted_at)
    filing.doc_type = meta["doc_type"]
    filing.period_type = meta["period_type"]
    filing.is_amendment = meta["is_amendment"]
    filing.accounting_standard = meta["accounting_standard"]
    filing.parse_status = "pending"
    filing.parse_error = None
    db.flush()

    delete_filing_payload(db, filing.id)
    cache = Path(cache_dir or XBRL_CACHE_DIR)
    limit = XBRL_TEXT_MAX_CHARS if text_max is None else text_max
    try:
        with TemporaryDirectory(prefix="xbrl-parse-") as tmp:
            unpacked = extract_zip(zip_path, Path(tmp) / "unpacked")
            exclude: set[date] = set()
            if filing.submitted_at is not None:
                exclude.add(filing.submitted_at.date())
            # Always resolve 本表の期末 from package contents (ignore stale column).
            pkg = parse_package(
                unpacked,
                cache_dir=cache,
                text_max=limit,
                fiscal_year_end=None,
                title=title,
                exclude_dates=exclude or None,
            )
    except ValueError as exc:
        if str(exc) == "no-ixbrl":
            filing.parse_status = "skipped"
            filing.parse_error = "no-ixbrl"
            filing.parsed_at = _now()
            db.commit()
            return filing
        raise
    if not pkg.facts:
        filing.parse_status = "failed"
        filing.parse_error = "no-facts"
        filing.parsed_at = _now()
        db.commit()
        return filing

    # Resolved 本表の期末 (never submitted_at/pubdate; see resolve_fiscal_year_end).
    filing.fiscal_year_end = pkg.fiscal_year_end

    ctx_rows: dict[str, XbrlContext] = {}
    for ctx in pkg.contexts:
        row = XbrlContext(
            filing_id=filing.id,
            context_ref=ctx.context_ref,
            period_type=ctx.period_type,
            start_date=ctx.start_date,
            end_date=ctx.end_date,
            is_consolidated=ctx.is_consolidated,
            relative_period=ctx.relative_period,
            dimensions_json=ctx.dimensions_json,
            has_dimensions=ctx.has_dimensions,
        )
        db.add(row)
        db.flush()
        ctx_rows[ctx.context_ref] = row

    for fact in pkg.facts:
        ctx = ctx_rows.get(fact.context_ref)
        if ctx is None:
            continue
        labels = pkg.labels.get((fact.namespace_key, fact.local_name), {})
        el = upsert_element(
            db,
            uri=fact.namespace_uri,
            key=fact.namespace_key,
            local=fact.local_name,
            labels=labels,
            is_custom=not namespace_is_standard(fact.namespace_key, fact.namespace_uri),
            numeric=fact.is_numeric,
        )
        db.add(XbrlFact(
            filing_id=filing.id,
            element_id=el.id,
            context_id=ctx.id,
            unit=fact.unit,
            scale=fact.scale,
            value_num=fact.value_num,
            value_text=fact.value_text,
            is_nil=fact.is_nil,
        ))
    filing.parse_status = "parsed"
    filing.parse_error = None
    filing.parsed_at = _now()
    db.commit()
    return filing


def mark_failed(db: Session, filing: XbrlFiling, exc: BaseException) -> None:
    delete_filing_payload(db, filing.id)
    filing.parse_status = "failed"
    filing.parse_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000]
    filing.parsed_at = _now()
    db.commit()

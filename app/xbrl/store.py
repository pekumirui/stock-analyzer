"""Persist a parsed XBRL package into the DB (filing-scoped, idempotent)."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import bindparam, delete, insert, select
from sqlalchemy.orm import Session

from app.config import XBRL_CACHE_DIR, XBRL_TEXT_MAX_CHARS
from app.models import (
    Stock,
    XbrlContext,
    XbrlElement,
    XbrlFact,
    XbrlFiling,
    XbrlFinancial,
    XbrlMajorShareholder,
    XbrlPdfMatch,
    XbrlSegment,
    XbrlShareholderFiling,
    XbrlShareholdingByCategory,
)
from app.xbrl.context import classify_title, infer_accounting_standard_from_namespaces
from app.xbrl.parser import ParsedPackage, extract_zip, namespace_is_standard, parse_package


def inherit_accounting_standard_from_prior_filings(
    db: Session,
    *,
    code: str,
    doc_id: str,
    submitted_at: datetime | None,
) -> str | None:
    """Title/facts が基準を決められないとき、同じ銘柄の直近の parsed filing から引き継ぐ。"""
    q = db.query(XbrlFiling).filter(
        XbrlFiling.code == code,
        XbrlFiling.doc_id != doc_id,
        XbrlFiling.parse_status == "parsed",
        XbrlFiling.accounting_standard.isnot(None),
        XbrlFiling.accounting_standard != "",
    )
    if submitted_at is not None:
        q = q.filter(XbrlFiling.submitted_at <= submitted_at)
    prior = q.order_by(XbrlFiling.submitted_at.desc(), XbrlFiling.id.desc()).first()
    if prior is None:
        return None
    return prior.accounting_standard


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
    db.execute(delete(XbrlPdfMatch).where(XbrlPdfMatch.filing_id == filing_id))
    db.execute(delete(XbrlFact).where(XbrlFact.filing_id == filing_id))
    db.execute(delete(XbrlContext).where(XbrlContext.filing_id == filing_id))


_IN_CHUNK = 500


def member_labels_json(ctx, labels: dict) -> str | None:
    """連結/単体以外の次元メンバー → その書類の日本語ラベル。無ければ None（KAB-77）。"""
    out: dict[str, str] = {}
    for member in ctx.dimensions.values():
        prefix, sep, local = member.partition(":")
        if not sep or local in ("ConsolidatedMember", "NonConsolidatedMember"):
            continue
        label = (labels.get((prefix, local)) or {}).get("label_ja")
        if label:
            out[member] = label
    return json.dumps(out, ensure_ascii=False, sort_keys=True) if out else None


def upsert_elements(db: Session, wanted: dict[tuple[str, str], dict]) -> dict[tuple[str, str], int]:
    """(namespace_uri, local_name) → element id. One SELECT per chunk, one INSERT for new rows.

    ``wanted`` values: key / labels / is_custom / numeric (the first fact of the element).
    Existing rows get the same overwrite as before: namespace_key, non-empty labels,
    item_type only when NULL, is_custom.
    """
    table = XbrlElement.__table__
    cols = table.c
    ids: dict[tuple[str, str], int] = {}
    changed: list[dict] = []
    locals_ = sorted({local for _uri, local in wanted})
    for i in range(0, len(locals_), _IN_CHUNK):
        rows = db.execute(
            select(
                cols.id, cols.namespace_uri, cols.local_name, cols.namespace_key,
                cols.label_ja, cols.label_en, cols.item_type, cols.is_custom,
            ).where(cols.local_name.in_(locals_[i:i + _IN_CHUNK]))
        )
        for row in rows:
            el_key = (row.namespace_uri, row.local_name)
            spec = wanted.get(el_key)
            if spec is None:
                continue
            ids[el_key] = row.id
            labels = spec["labels"]
            new = {
                "namespace_key": spec["key"],
                "label_ja": labels.get("label_ja") or row.label_ja,
                "label_en": labels.get("label_en") or row.label_en,
                "item_type": row.item_type or ("monetary" if spec["numeric"] else "string"),
                "is_custom": spec["is_custom"],
            }
            if any(getattr(row, k) != v for k, v in new.items()):
                changed.append({"b_id": row.id, **new})
    if changed:
        db.execute(table.update().where(cols.id == bindparam("b_id")), changed)
    new_rows = []
    now = _now()
    for (uri, local), spec in wanted.items():
        if (uri, local) in ids:
            continue
        labels = spec["labels"]
        new_rows.append({
            "namespace_uri": uri,
            "namespace_key": spec["key"],
            "local_name": local,
            "label_ja": labels.get("label_ja") or None,
            "label_en": labels.get("label_en") or None,
            "item_type": "monetary" if spec["numeric"] else "string",
            "is_custom": spec["is_custom"],
            "first_seen_at": now,
        })
    if new_rows:
        result = db.execute(
            insert(table).returning(
                XbrlElement.id, XbrlElement.namespace_uri, XbrlElement.local_name,
            ),
            new_rows,
        )
        for el_id, uri, local in result:
            ids[(uri, local)] = el_id
    return ids


def parse_zip(
    zip_path: Path,
    *,
    title: str,
    submitted_at: str,
    cache_dir: Path | None = None,
    text_max: int | None = None,
) -> ParsedPackage:
    """ZIP → ParsedPackage. No DB, so it also runs in parse workers (KAB-63)."""
    cache = Path(cache_dir or XBRL_CACHE_DIR)
    limit = XBRL_TEXT_MAX_CHARS if text_max is None else text_max
    with TemporaryDirectory(prefix="xbrl-parse-") as tmp:
        unpacked = extract_zip(zip_path, Path(tmp) / "unpacked")
        # Always resolve 本表の期末 from package contents (ignore stale column).
        return parse_package(
            unpacked,
            cache_dir=cache,
            text_max=limit,
            fiscal_year_end=None,
            title=title,
            exclude_dates={_parse_submitted(submitted_at).date()},
        )


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
    package: ParsedPackage | BaseException | None = None,
    source: str = "tdnet",
    title_meta: dict | None = None,
    keep_fact=None,
) -> XbrlFiling:
    """``package``: a parse_zip() result (or its exception) from a worker. None parses here.

    ``title_meta``: classify_title の代わり（EDINET は書類種別コードから決める）。
    ``keep_fact(fact, ctx) -> bool``: 入れる fact を絞る（EDINET は正規化に使わない次元を落とす）。
    """
    stock = db.get(Stock, code)
    if stock is None:
        raise ValueError(f"NOT_IN_MASTER {code}")
    meta = title_meta or classify_title(title)
    filing = (
        db.query(XbrlFiling)
        .filter_by(source=source, doc_id=doc_id)
        .one_or_none()
    )
    if filing is None:
        filing = XbrlFiling(
            code=code,
            source=source,
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
    try:
        if isinstance(package, BaseException):
            raise package
        if package is None:
            package = parse_zip(
                zip_path,
                title=title,
                submitted_at=submitted_at,
                cache_dir=cache_dir,
                text_max=text_max,
            )
        pkg = package
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

    # Prefer title; if ambiguous, use fact namespaces / DEI / *IFRS, then prior filings.
    filing.accounting_standard = infer_accounting_standard_from_namespaces(
        pkg.facts,
        title_standard=filing.accounting_standard,
    )
    if filing.accounting_standard is None:
        filing.accounting_standard = inherit_accounting_standard_from_prior_filings(
            db,
            code=filing.code,
            doc_id=filing.doc_id,
            submitted_at=filing.submitted_at,
        )

    if keep_fact is not None:
        by_ref = {ctx.context_ref: ctx for ctx in pkg.contexts}
        facts = [f for f in pkg.facts if keep_fact(f, by_ref.get(f.context_ref))]
        used = {f.context_ref for f in facts}
        contexts = [c for c in pkg.contexts if c.context_ref in used]
    else:
        facts, contexts = pkg.facts, pkg.contexts
    store_package(
        db,
        filing,
        ParsedPackage(
            contexts=contexts,
            facts=facts,
            labels=pkg.labels,
            ixbrl_files=pkg.ixbrl_files,
            fiscal_year_end=pkg.fiscal_year_end,
        ),
    )
    db.commit()
    return filing


def store_package(db: Session, filing: XbrlFiling, pkg: ParsedPackage) -> None:
    """Write elements, contexts, and facts for a filing whose payload is already deleted.

    No commit. An empty fact list still marks the filing parsed. The ZIP path rejects
    a package that arrived with no facts (``no-facts``) before it calls this.
    """
    facts, contexts = pkg.facts, pkg.contexts
    wanted: dict[tuple[str, str], dict] = {}
    for fact in facts:
        el_key = (fact.namespace_uri, fact.local_name)
        if el_key in wanted:
            continue
        wanted[el_key] = {
            "key": fact.namespace_key,
            "labels": pkg.labels.get((fact.namespace_key, fact.local_name), {}),
            "is_custom": not namespace_is_standard(fact.namespace_key, fact.namespace_uri),
            "numeric": fact.is_numeric,
        }
    element_ids = upsert_elements(db, wanted)

    ctx_ids: dict[str, int] = {}
    if contexts:
        result = db.execute(
            insert(XbrlContext.__table__).returning(XbrlContext.id, XbrlContext.context_ref),
            [
                {
                    "filing_id": filing.id,
                    "context_ref": ctx.context_ref,
                    "period_type": ctx.period_type,
                    "start_date": ctx.start_date,
                    "end_date": ctx.end_date,
                    "is_consolidated": ctx.is_consolidated,
                    "relative_period": ctx.relative_period,
                    "dimensions_json": ctx.dimensions_json,
                    "has_dimensions": ctx.has_dimensions,
                    "member_labels_json": member_labels_json(ctx, pkg.labels),
                }
                for ctx in contexts
            ],
        )
        ctx_ids = {ref: ctx_id for ctx_id, ref in result}

    fact_rows: list[dict] = []
    for fact in facts:
        ctx_id = ctx_ids.get(fact.context_ref)
        if ctx_id is None:
            continue
        el_id = element_ids.get((fact.namespace_uri, fact.local_name))
        if el_id is None:
            continue
        fact_rows.append({
            "filing_id": filing.id,
            "element_id": el_id,
            "context_id": ctx_id,
            "unit": fact.unit,
            "scale": fact.scale,
            "value_num": fact.value_num,
            "value_text": fact.value_text,
            "is_nil": fact.is_nil,
        })
    if fact_rows:
        db.execute(insert(XbrlFact.__table__), fact_rows)
    filing.parse_status = "parsed"
    filing.parse_error = None
    filing.parsed_at = _now()


def delete_filing(db: Session, filing: XbrlFiling) -> None:
    """Drop financials, segments, shareholders, facts, contexts, then the filing row. No commit."""
    filing_id = filing.id
    db.query(XbrlFinancial).filter_by(filing_id=filing_id).delete(synchronize_session="fetch")
    db.query(XbrlSegment).filter_by(filing_id=filing_id).delete(synchronize_session="fetch")
    db.query(XbrlMajorShareholder).filter_by(filing_id=filing_id).delete(synchronize_session="fetch")
    db.query(XbrlShareholdingByCategory).filter_by(filing_id=filing_id).delete(
        synchronize_session="fetch")
    db.query(XbrlShareholderFiling).filter_by(filing_id=filing_id).delete(
        synchronize_session="fetch")
    delete_filing_payload(db, filing_id)
    db.delete(filing)


def mark_failed(db: Session, filing: XbrlFiling, exc: BaseException) -> None:
    delete_filing_payload(db, filing.id)
    filing.parse_status = "failed"
    filing.parse_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:2000]
    filing.parsed_at = _now()
    db.commit()

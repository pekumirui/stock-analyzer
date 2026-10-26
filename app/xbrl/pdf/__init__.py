"""TDnet 短信 PDF（tdnet_pdf_documents の HTML）を XBRL の生の層に入れる（KAB-173）。

ZIP と同じ ParsedPackage を作り、同じ store_package / normalize_filing を通す。
印字の行名は pdf-* 名前空間の要素名。標準科目への対応は config/xbrl/mappings/tdnet_pdf.yaml。
"""
from __future__ import annotations

import gzip
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Statement, TdnetPdfDocument, XbrlFiling, XbrlFinancial
from app.xbrl.normalize import is_normalizable_context, normalize_filing
from app.xbrl.parser import ParsedPackage
from app.xbrl.pdf._layout import PdfLayoutError, read_pages
from app.xbrl.pdf._tanshin import (
    DocKind,
    PdfDisclosure,
    Unsupported,
    label_key,
    package,
    parse_disclosure_no,
)
from app.xbrl.store import (
    delete_filing,
    delete_filing_payload,
    inherit_accounting_standard_from_prior_filings,
    store_package,
)

__all__ = [
    "READER_VERSION",
    "DocKind",
    "PdfDisclosure",
    "PdfLayoutError",
    "SyncOutcome",
    "Unsupported",
    "label_key",
    "read_tdnet_pdf",
    "sync_pdf_filing",
    "sync_pdf_filings",
]

SOURCE = "tdnet_pdf"
# レイアウト・行名・label_key の結果が変わったら上げる。上げると全 PDF filing の指紋がずれる。
READER_VERSION = 2


class SyncOutcome(str, Enum):
    PARSED = "parsed"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    RETIRED = "retired"
    ABSENT = "absent"


def fingerprint(html_sha256: str) -> str:
    """xbrl_filings.raw_sha256。HTML と READER_VERSION を 1 つの値にする。"""
    return hashlib.sha256(f"{html_sha256}:{READER_VERSION}".encode()).hexdigest()


def read_tdnet_pdf(html: str, disclosure: PdfDisclosure) -> ParsedPackage:
    """HTML（svg1 でも svg2 でも）→ TSE サマリーと同じメンバーの contexts と facts。

    金額は円（百万円は 10^6 倍）。印字の符号のまま（△ は負）。百分率は pure。
    """
    return package(read_pages(html), disclosure)


@dataclass(frozen=True)
class _Build:
    disclosure: PdfDisclosure
    doc: TdnetPdfDocument
    fingerprint: str


@dataclass(frozen=True)
class _Absent:
    reason: Literal["zip", "no-html", "no-statement", "doc-type", "reit", "irregular-period", "no-dates"]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _zip_parsed(db: Session, disclosure_no: str) -> bool:
    rows = db.query(XbrlFiling.doc_id).filter_by(source="tdnet", parse_status="parsed")
    return any(doc_id and doc_id[-14:] == disclosure_no for (doc_id,) in rows)


def _plan(db: Session, disclosure_no: str, zip_parsed: bool) -> _Build | _Absent:
    if zip_parsed:
        return _Absent("zip")
    doc = db.get(TdnetPdfDocument, disclosure_no)
    if doc is None or doc.status != "ok" or not doc.html_gz or not doc.sha256:
        return _Absent("no-html")
    statement = db.get(Statement, disclosure_no)
    if statement is None:
        return _Absent("no-statement")
    parsed = PdfDisclosure.parse(statement)
    if isinstance(parsed, Unsupported):
        return _Absent(parsed.reason)
    return _Build(parsed, doc, fingerprint(doc.sha256))


def _filing(db: Session, disclosure_no: str) -> XbrlFiling | None:
    return db.query(XbrlFiling).filter_by(source=SOURCE, doc_id=disclosure_no).one_or_none()


def _upsert(db: Session, filing: XbrlFiling | None, disclosure: PdfDisclosure, fp: str) -> XbrlFiling:
    meta = disclosure.filing_meta()
    if filing is None:
        filing = XbrlFiling(
            code=disclosure.code,
            source=SOURCE,
            doc_id=disclosure.disclosure_no,
            raw_path=f"tdnet_pdf:{disclosure.disclosure_no}",
            submitted_at=disclosure.submitted_at,
            created_at=_now(),
            doc_type=meta["doc_type"],
            period_type=meta["period_type"],
            is_amendment=False,
        )
        db.add(filing)
    filing.code = disclosure.code
    filing.title = disclosure.title
    filing.raw_path = f"tdnet_pdf:{disclosure.disclosure_no}"
    filing.raw_sha256 = fp
    filing.submitted_at = disclosure.submitted_at
    filing.doc_type = meta["doc_type"]
    filing.period_type = meta["period_type"]
    filing.is_amendment = False
    filing.accounting_standard = meta["accounting_standard"]
    filing.fiscal_year_end = disclosure.anchor_end
    filing.parse_status = "pending"
    filing.parse_error = None
    db.flush()
    return filing


def _kept(pkg: ParsedPackage) -> ParsedPackage:
    by_ref = {ctx.context_ref: ctx for ctx in pkg.contexts}
    facts = [
        fact for fact in pkg.facts
        if (ctx := by_ref.get(fact.context_ref)) is not None and is_normalizable_context(ctx)
    ]
    used = {fact.context_ref for fact in facts}
    contexts = [ctx for ctx in pkg.contexts if ctx.context_ref in used]
    return ParsedPackage(
        contexts=contexts,
        facts=facts,
        labels=pkg.labels,
        ixbrl_files=pkg.ixbrl_files,
        fiscal_year_end=pkg.fiscal_year_end,
    )


def sync_pdf_filing(
    db: Session,
    disclosure_no: str,
    *,
    reparse: bool = False,
    ignore_zip: bool = False,
) -> SyncOutcome:
    """1 件を今あるべき状態へ寄せる。commit は 1 回。繰り返しても、途中で落ちても同じ終わりになる。

    ``ignore_zip`` は ZIP があっても PDF 側を残す。ZIP との突き合わせ用。本番ジョブは渡さない。
    """
    number = parse_disclosure_no(disclosure_no)
    zip_parsed = False if ignore_zip else _zip_parsed(db, number)
    return _sync(db, number, zip_parsed, reparse=reparse)


def _sync(db: Session, disclosure_no: str, zip_parsed: bool, *, reparse: bool) -> SyncOutcome:
    plan = _plan(db, disclosure_no, zip_parsed)
    filing = _filing(db, disclosure_no)
    if isinstance(plan, _Absent):
        if filing is None:
            return SyncOutcome.ABSENT
        delete_filing(db, filing)
        db.commit()
        return SyncOutcome.RETIRED
    fp = plan.fingerprint
    if (
        filing is not None
        and filing.raw_sha256 == fp
        and filing.parse_status in ("parsed", "failed")
        and not reparse
    ):
        return SyncOutcome.UNCHANGED
    filing = _upsert(db, filing, plan.disclosure, fp)
    delete_filing_payload(db, filing.id)
    try:
        html = gzip.decompress(plan.doc.html_gz).decode("utf-8")
        pkg = _kept(read_tdnet_pdf(html, plan.disclosure))
    except PdfLayoutError as exc:
        filing.parse_status = "failed"
        filing.parse_error = str(exc)[:2000]
        filing.parsed_at = _now()
        db.query(XbrlFinancial).filter_by(filing_id=filing.id).delete(synchronize_session="fetch")
        db.commit()
        return SyncOutcome.FAILED
    except Exception:
        db.rollback()
        raise
    if filing.accounting_standard is None:
        filing.accounting_standard = inherit_accounting_standard_from_prior_filings(
            db,
            code=filing.code,
            doc_id=filing.doc_id,
            submitted_at=filing.submitted_at,
        )
    store_package(db, filing, pkg)
    normalize_filing(db, filing)
    db.commit()
    return SyncOutcome.PARSED


def sync_pdf_filings(
    db: Session,
    *,
    codes: list[str] | None = None,
    reparse: bool = False,
    limit: int = 2000,
) -> Counter:
    """ok の HTML か、既存の PDF filing がある開示。新しい順。remaining は今回見なかった件数。"""
    docs = db.query(TdnetPdfDocument).filter(TdnetPdfDocument.status == "ok")
    filings = db.query(XbrlFiling).filter_by(source=SOURCE)
    if codes:
        docs = docs.filter(TdnetPdfDocument.code.in_(codes))
        filings = filings.filter(XbrlFiling.code.in_(codes))
    rank: dict[str, date] = {}
    for doc in docs:
        rank[doc.disclosure_no] = doc.disclosed_date or date.min
    for filing in filings:
        rank.setdefault(
            filing.doc_id,
            filing.submitted_at.date() if filing.submitted_at else date.min,
        )
    ordered = sorted(rank, key=lambda number: (rank[number], number), reverse=True)
    zip_rows = db.query(XbrlFiling.doc_id).filter_by(source="tdnet", parse_status="parsed")
    zip_set = {doc_id[-14:] for (doc_id,) in zip_rows if doc_id and len(doc_id) >= 14}
    counts: Counter = Counter()
    for number in ordered[:limit]:
        try:
            counts[_sync(db, number, number in zip_set, reparse=reparse)] += 1
        except Exception as exc:
            db.rollback()
            counts[SyncOutcome.FAILED] += 1
            print(f"pdf failed disclosure_no={number} err={exc}", flush=True)
    counts["remaining"] = max(0, len(ordered) - limit)
    return counts

"""briefing_documents への 1 行。TDnet・IR BANK の過去分・ホームページ取得が同じ関数を使う。"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal, upsert
from app.models import BriefingDocument

JST = ZoneInfo("Asia/Tokyo")
SOURCE_TDNET = "tdnet"
SOURCE_HOMEPAGE = "homepage"
SOURCE_IRBANK = "irbank"
SOURCES = frozenset({SOURCE_TDNET, SOURCE_HOMEPAGE, SOURCE_IRBANK})
STATUS_SAVED = "saved"
_ID_CHUNK = 400


def record_briefing(
    *,
    source: str,
    document_id: str,
    code: str,
    company_name: str | None,
    pubdate: str,
    title: str,
    rel_path: str,
    url: str | None,
    sha256: str,
    size: int,
    saved_at: datetime | None = None,
) -> None:
    """保存済み PDF を索引する。同じ (source, document_id) は上書きする。"""
    if source not in SOURCES:
        names = ", ".join(sorted(SOURCES))
        raise ValueError(f"briefing source must be one of {names}: {source!r}")
    if not document_id or not code or not rel_path:
        raise ValueError("document_id, code, and rel_path are required")
    when = saved_at or datetime.now(JST).replace(tzinfo=None)
    row = {
        "source": source,
        "document_id": document_id,
        "code": code,
        "company_name": company_name or None,
        "pubdate": pubdate,
        "title": title,
        "rel_path": rel_path,
        "url": url or None,
        "sha256": sha256,
        "size": size,
        "status": STATUS_SAVED,
        "saved_at": when,
    }
    db = SessionLocal()
    try:
        upsert(db, BriefingDocument, [row])
    finally:
        db.close()


def has_saved(source: str, document_id: str) -> bool:
    db = SessionLocal()
    try:
        row = db.get(BriefingDocument, (source, document_id))
        return row is not None and row.status == STATUS_SAVED and bool(row.rel_path)
    finally:
        db.close()


def saved_document_ids(document_ids: Iterable[str]) -> set[str]:
    """保存済みの document_id。出所は問わない。TDnet 済みを IR BANK から取り直さないときに使う。"""
    ids = list(dict.fromkeys(i for i in document_ids if i))
    if not ids:
        return set()
    found: set[str] = set()
    db = SessionLocal()
    try:
        for start in range(0, len(ids), _ID_CHUNK):
            chunk = ids[start:start + _ID_CHUNK]
            rows = (
                db.query(BriefingDocument.document_id)
                .filter(
                    BriefingDocument.document_id.in_(chunk),
                    BriefingDocument.status == STATUS_SAVED,
                    BriefingDocument.rel_path != "",
                )
                .all()
            )
            found.update(row[0] for row in rows)
    finally:
        db.close()
    return found

"""briefing_documents への 1 行。TDnet の保存と、将来のホームページ取得が同じ関数を使う。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.db import SessionLocal, upsert
from app.models import BriefingDocument

JST = ZoneInfo("Asia/Tokyo")
SOURCE_TDNET = "tdnet"
SOURCE_HOMEPAGE = "homepage"
SOURCES = frozenset({SOURCE_TDNET, SOURCE_HOMEPAGE})
STATUS_SAVED = "saved"


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
        raise ValueError(f"briefing source must be tdnet or homepage: {source!r}")
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

"""briefing_documents への 1 行。TDnet・IR BANK の過去分・ホームページ取得が同じ関数を使う。"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import SessionLocal, run_sqlite_write, upsert
from app.models import BriefingDocument

JST = ZoneInfo("Asia/Tokyo")
SOURCE_TDNET = "tdnet"
SOURCE_HOMEPAGE = "homepage"
SOURCE_IRBANK = "irbank"
SOURCES = frozenset({SOURCE_TDNET, SOURCE_HOMEPAGE, SOURCE_IRBANK})
STATUS_SAVED = "saved"
STORAGE_LOCAL = "local"
STORAGE_ONEDRIVE = "onedrive"
STORAGES = frozenset({STORAGE_LOCAL, STORAGE_ONEDRIVE})
_ID_CHUNK = 400
_DOC_KEY = re.compile(r"^[0-9A-Za-z]{8,}$")


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
    """保存済み PDF を索引する。同じ (source, document_id) は上書きする。

    locked は run_sqlite_write が数回、待ちを伸ばして再試行する（KAB-305 と同じ）。
    """
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
        "storage": STORAGE_LOCAL,
        "saved_at": when,
    }
    db = SessionLocal()
    try:
        run_sqlite_write(db, lambda: upsert(db, BriefingDocument, [row], commit=False))
    finally:
        db.close()


@dataclass(frozen=True)
class SavedBriefing:
    source: str
    document_id: str
    code: str
    rel_path: str
    storage: str
    pubdate: str
    size: int | None
    status: str


def _saved_from(row: BriefingDocument) -> SavedBriefing | None:
    if row.status != STATUS_SAVED or not row.rel_path:
        return None
    return SavedBriefing(
        source=row.source,
        document_id=row.document_id,
        code=row.code,
        rel_path=row.rel_path,
        storage=row.storage or STORAGE_LOCAL,
        pubdate=row.pubdate or "",
        size=row.size,
        status=row.status,
    )


def has_saved(source: str, document_id: str) -> bool:
    """保存済みか。OneDrive へ移した行も saved のまま（storage は見ない）。"""
    return get_saved(source, document_id) is not None


def get_saved(source: str, document_id: str) -> SavedBriefing | None:
    db = SessionLocal()
    try:
        row = db.get(BriefingDocument, (source, document_id))
        if row is None:
            return None
        return _saved_from(row)
    finally:
        db.close()


def find_saved(document_id: str) -> SavedBriefing | None:
    """開示番号で保存済みを探す。14 桁の PDF ID は 18 桁の末尾とも合う。出所は問わない。"""
    key = (document_id or "").strip()
    if not key:
        return None
    db = SessionLocal()
    try:
        query = db.query(BriefingDocument).filter(
            BriefingDocument.status == STATUS_SAVED,
            BriefingDocument.rel_path != "",
        )
        rows = query.filter(BriefingDocument.document_id == key).all()
        if not rows and _DOC_KEY.fullmatch(key):
            rows = query.filter(BriefingDocument.document_id.endswith(key)).all()
        saved = [item for item in (_saved_from(row) for row in rows) if item is not None]
        if not saved:
            return None
        saved.sort(key=lambda item: item.pubdate, reverse=True)
        return saved[0]
    finally:
        db.close()


def mark_storage(source: str, document_id: str, storage: str) -> None:
    """保存場所だけ変える。行・status・rel_path は残す。"""
    if storage not in STORAGES:
        raise ValueError(f"briefing storage must be one of {', '.join(sorted(STORAGES))}: {storage!r}")
    db = SessionLocal()
    try:
        def write() -> None:
            row = db.get(BriefingDocument, (source, document_id))
            if row is None:
                raise LookupError(document_id)
            row.storage = storage

        run_sqlite_write(db, write)
    finally:
        db.close()


def fill_blank_storage() -> int:
    """列を足した直後の空を local にする。onedrive の行は触らない。"""
    db = SessionLocal()
    try:
        def write() -> int:
            result = db.execute(
                text(
                    "UPDATE briefing_documents SET storage = :local "
                    "WHERE storage IS NULL OR storage = ''"
                ),
                {"local": STORAGE_LOCAL},
            )
            return int(result.rowcount or 0)

        return int(run_sqlite_write(db, write) or 0)
    finally:
        db.close()


def saved_document_ids(document_ids: Iterable[str]) -> set[str]:
    """保存済みの document_id。出所と保存場所は問わない。TDnet 済みを IR BANK から取り直さないときに使う。"""
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

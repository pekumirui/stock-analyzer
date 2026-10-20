from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Protocol

from .errors import EdinetNotFoundError
from .models import JST, DocumentEntry, DocumentListMetadata


class _MetadataClient(Protocol):
    def get_metadata(self, file_date: date | str) -> DocumentListMetadata: ...


def today_jst() -> date:
    return datetime.now(JST).date()


def date_range(start: date, end: date) -> Iterator[date]:
    if end < start:
        raise ValueError("end must be on or after start")
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def normalize_sec_code(code: str) -> str:
    stripped = code.strip()
    if len(stripped) == 4:
        return stripped + "0"
    return stripped


def keep_list_entry(
    entry: DocumentEntry,
    *,
    doc_types: set[str] | None = None,
    edinet_codes: set[str] | None = None,
    sec_codes: set[str] | None = None,
    include_events: bool = False,
    only_available: bool = True,
) -> bool:
    """Return True when a list row should be yielded from iter_documents."""
    if doc_types is not None and (entry.doc_type_code or "") not in doc_types:
        return False
    if edinet_codes is not None and entry.edinet_code not in edinet_codes:
        return False
    if sec_codes is not None:
        if entry.sec_code is None:
            return False
        if normalize_sec_code(entry.sec_code) not in sec_codes:
            return False
    if entry.is_event_record:
        return include_events
    return entry.is_available or not only_available


def is_previous_days_finalized(client: _MetadataClient) -> bool:
    try:
        client.get_metadata(today_jst())
    except EdinetNotFoundError:
        return False
    return True

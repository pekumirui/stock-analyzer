from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Iterable
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Self

import httpx

from ._core import (
    DownloadPayload,
    ExpectedKind,
    ParsedJSON,
    classify,
    coerce_file_type,
    document_url,
    documents_list_url,
    parse_document_list,
    parse_file_date,
    parse_metadata,
    parse_retry_after,
    validate_doc_id,
    validate_file_date,
)
from ._ratelimit import AsyncRateLimiter
from ._redact import install_httpx_redaction, redact_text, redact_url
from ._retry import should_retry, sleep_seconds
from .client import default_timeout, default_user_agent, resolve_api_key
from .config import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MIN_INTERVAL,
)
from .enums import DocType, DocumentFileType, ListType
from .errors import (
    EdinetError,
    EdinetFileNotAvailableError,
    EdinetTransportError,
)
from .files import DownloadedFile
from .models import DocumentEntry, DocumentList, DocumentListMetadata
from .utils import date_range, keep_list_entry, normalize_sec_code

logger = logging.getLogger("edinet_wrapper")


class AsyncEdinetClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout | None = None,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        max_retries: int = DEFAULT_MAX_RETRIES,
        user_agent: str | None = None,
        validate_dates: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = resolve_api_key(api_key)
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._validate_dates = validate_dates
        self._limiter = AsyncRateLimiter(min_interval)
        self._owns_client = http_client is None
        install_httpx_redaction()
        headers = {"User-Agent": user_agent or default_user_agent()}
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout if timeout is not None else default_timeout(),
            headers=headers,
            follow_redirects=False,
        )
        if http_client is not None and user_agent:
            self._http.headers["User-Agent"] = user_agent

    def __repr__(self) -> str:
        return f"AsyncEdinetClient(base_url={self._base_url!r})"

    async def close(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def get_metadata(self, file_date: date | str) -> DocumentListMetadata:
        """Fetch list metadata (type=1) for a file date."""
        day = self._checked_date(file_date)
        url = documents_list_url(self._base_url, self._api_key, day, ListType.METADATA)
        parsed = await self._request(url, "list")
        if not isinstance(parsed, ParsedJSON):
            raise TypeError("list request expected JSON")
        return parse_metadata(parsed.data)

    async def list_documents(self, file_date: date | str) -> DocumentList:
        """Fetch the full document list (type=2) for a file date."""
        day = self._checked_date(file_date)
        url = documents_list_url(self._base_url, self._api_key, day, ListType.FULL)
        parsed = await self._request(url, "list")
        if not isinstance(parsed, ParsedJSON):
            raise TypeError("list request expected JSON")
        return parse_document_list(parsed.data)

    async def iter_documents(
        self,
        start: date | str,
        end: date | str | None = None,
        *,
        doc_types: Iterable[DocType | str] | None = None,
        edinet_codes: Iterable[str] | None = None,
        sec_codes: Iterable[str] | None = None,
        include_events: bool = False,
        only_available: bool = True,
        skip_empty_days: bool = True,
    ) -> AsyncIterator[DocumentEntry]:
        """Yield list rows over an inclusive calendar range."""
        start_day = self._checked_date(start)
        end_day = self._checked_date(end) if end is not None else start_day
        type_filter = {str(item) for item in doc_types} if doc_types is not None else None
        edinet_filter = set(edinet_codes) if edinet_codes is not None else None
        sec_filter = {normalize_sec_code(code) for code in sec_codes} if sec_codes is not None else None
        for day in date_range(start_day, end_day):
            if skip_empty_days and (await self.get_metadata(day)).count == 0:
                continue
            listing = await self.list_documents(day)
            for entry in listing.results:
                if keep_list_entry(
                    entry,
                    doc_types=type_filter,
                    edinet_codes=edinet_filter,
                    sec_codes=sec_filter,
                    include_events=include_events,
                    only_available=only_available,
                ):
                    yield entry

    async def fetch_new(self, file_date: date | str, *, after_seq: int = 0) -> list[DocumentEntry]:
        """Return rows with seqNumber greater than after_seq."""
        listing = await self.list_documents(file_date)
        return [entry for entry in listing.results if entry.seq_number > after_seq]

    async def download(self, doc_id: str, file_type: DocumentFileType | int) -> DownloadedFile:
        """Download a document into memory."""
        doc_id = validate_doc_id(doc_id)
        kind = coerce_file_type(file_type)
        url = document_url(self._base_url, self._api_key, doc_id, kind)
        payload = await self._request(url, "download")
        if not isinstance(payload, DownloadPayload):
            raise TypeError("download expected binary payload")
        return DownloadedFile(doc_id, kind, payload.content, payload.content_type)

    async def download_entry(self, entry: DocumentEntry, file_type: DocumentFileType | int) -> DownloadedFile:
        """Download after checking list flags. Sends no request when the file is absent."""
        kind = coerce_file_type(file_type)
        if not entry.has_file(kind):
            raise EdinetFileNotAvailableError(f"{entry.doc_id} has no file type {int(kind)}")
        return await self.download(entry.doc_id, kind)

    async def download_to(
        self,
        doc_id: str,
        file_type: DocumentFileType | int,
        dest: str | os.PathLike[str],
        *,
        overwrite: bool = False,
    ) -> Path:
        """Stream a document to dest. Directories get `{docID}_{type}.{ext}`."""
        doc_id = validate_doc_id(doc_id)
        kind = coerce_file_type(file_type)
        path = Path(dest)
        if path.exists() and path.is_dir():
            path = path / f"{doc_id}_{int(kind)}{kind.suffix}"
        if path.exists() and not overwrite:
            raise FileExistsError(str(path))
        url = document_url(self._base_url, self._api_key, doc_id, kind)
        return await self._download_to_path(url, path)

    async def download_many(
        self,
        items: Iterable[tuple[str, DocumentFileType]],
        dest: Path,
        *,
        concurrency: int = 2,
    ) -> list[Path | EdinetError]:
        """Download many files, sharing this client's rate limiter."""
        dest.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(concurrency)
        sequenced = list(items)

        async def one(doc_id: str, file_type: DocumentFileType) -> Path | EdinetError:
            async with semaphore:
                try:
                    return await self.download_to(doc_id, file_type, dest)
                except EdinetError as exc:
                    return exc

        return list(await asyncio.gather(*[one(doc_id, file_type) for doc_id, file_type in sequenced]))

    def _checked_date(self, file_date: date | str) -> date:
        day = parse_file_date(file_date)
        if self._validate_dates:
            validate_file_date(day)
        return day

    async def _request(self, url: str, expected: ExpectedKind) -> ParsedJSON | DownloadPayload:
        last_error: EdinetError | None = None
        for attempt in range(self._max_retries + 1):
            await self._limiter.wait()
            started = time.monotonic()
            try:
                response = await self._http.get(url, follow_redirects=False)
            except httpx.RequestError as exc:
                error = EdinetTransportError(redact_text(str(exc)), original_message=redact_text(str(exc)))
                if should_retry(error, attempt, self._max_retries):
                    await asyncio.sleep(sleep_seconds(error, attempt))
                    last_error = error
                    continue
                raise error from None
            elapsed = time.monotonic() - started
            endpoint = str(response.request.url)
            logger.debug(
                "GET %s %s %s %.3fs",
                redact_url(endpoint),
                response.status_code,
                response.headers.get("content-type"),
                elapsed,
            )
            try:
                result = classify(
                    response.status_code,
                    response.headers.get("content-type"),
                    response.content,
                    expected,
                    endpoint=endpoint,
                )
            except EdinetError as exc:
                if should_retry(exc, attempt, self._max_retries):
                    await asyncio.sleep(
                        sleep_seconds(
                            exc,
                            attempt,
                            retry_after=parse_retry_after(response.headers.get("Retry-After")),
                        )
                    )
                    last_error = exc
                    continue
                raise
            return result
        assert last_error is not None
        raise last_error

    async def _download_to_path(self, url: str, dest: Path) -> Path:
        part = dest.with_name(dest.name + ".part")
        last_error: EdinetError | None = None
        for attempt in range(self._max_retries + 1):
            await self._limiter.wait()
            if part.exists():
                part.unlink()
            try:
                async with self._http.stream("GET", url, follow_redirects=False) as response:
                    endpoint = str(response.request.url)
                    content_type = response.headers.get("content-type")
                    mime = (content_type or "").split(";", 1)[0].strip().lower()
                    if mime in {"application/json", "text/html"}:
                        body = await response.aread()
                        classify(
                            response.status_code,
                            content_type,
                            body,
                            "download",
                            endpoint=endpoint,
                        )
                        raise AssertionError("classify should have raised for JSON/HTML download")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        first = b""
                        with part.open("wb") as handle:
                            async for chunk in response.aiter_bytes():
                                if not first:
                                    first = chunk[:4]
                                handle.write(chunk)
                        classify(
                            response.status_code,
                            content_type,
                            first + b"\0" * max(0, 4 - len(first)),
                            "download",
                            endpoint=endpoint,
                        )
                        os.replace(part, dest)
                        return dest
                    except Exception:
                        if part.exists():
                            part.unlink()
                        raise
            except httpx.RequestError as exc:
                error = EdinetTransportError(redact_text(str(exc)), original_message=redact_text(str(exc)))
                if part.exists():
                    part.unlink()
                if should_retry(error, attempt, self._max_retries):
                    await asyncio.sleep(sleep_seconds(error, attempt))
                    last_error = error
                    continue
                raise error from None
            except EdinetError as exc:
                if part.exists():
                    part.unlink()
                if should_retry(exc, attempt, self._max_retries):
                    await asyncio.sleep(sleep_seconds(exc, attempt))
                    last_error = exc
                    continue
                raise
        assert last_error is not None
        raise last_error

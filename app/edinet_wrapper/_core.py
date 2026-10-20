from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

from ._redact import redact_url
from .config import DATE_MAX_LOOKBACK_DAYS, DOC_ID_PATTERN
from .enums import DocumentFileType, ListType
from .errors import (
    EdinetAPIError,
    EdinetAuthError,
    EdinetBadRequestError,
    EdinetMaintenanceError,
    EdinetNotFoundError,
    EdinetRateLimitError,
    EdinetServerError,
    EdinetUnexpectedResponseError,
)
from .models import DocumentList, DocumentListMetadata
from .utils import today_jst

ExpectedKind = Literal["list", "download"]
DOC_ID_RE = re.compile(DOC_ID_PATTERN)


@dataclass(frozen=True)
class ParsedJSON:
    data: dict[str, Any]


@dataclass(frozen=True)
class DownloadPayload:
    content: bytes
    content_type: str
    kind: Literal["zip", "pdf"]


def mime_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def parse_file_date(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ValueError("file_date must be a date or YYYY-MM-DD string")
    if isinstance(value, date):
        return value
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc
    if value != parsed.isoformat():
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    return parsed


def validate_file_date(file_date: date, *, today: date | None = None) -> None:
    current = today if today is not None else today_jst()
    if file_date > current:
        raise ValueError(f"file_date {file_date.isoformat()} is in the future")
    # NOTE(spec-gap): the spec cuts off at the latest FSA business day 24:00 ten
    # years ago. We only reject dates older than 10 years + 7 days.
    oldest = current - timedelta(days=DATE_MAX_LOOKBACK_DAYS)
    if file_date < oldest:
        raise ValueError(f"file_date {file_date.isoformat()} is older than 10 years")


def validate_doc_id(doc_id: str) -> str:
    if not DOC_ID_RE.fullmatch(doc_id):
        raise ValueError(f"doc_id must match {DOC_ID_PATTERN}, got {doc_id!r}")
    return doc_id


def coerce_file_type(file_type: DocumentFileType | int) -> DocumentFileType:
    if isinstance(file_type, DocumentFileType):
        return file_type
    try:
        return DocumentFileType(file_type)
    except ValueError as exc:
        raise ValueError(f"file_type must be 1..5, got {file_type!r}") from exc


def coerce_list_type(list_type: ListType | int) -> ListType:
    if isinstance(list_type, ListType):
        return list_type
    return ListType(list_type)


def documents_list_url(
    base_url: str,
    api_key: str,
    file_date: date,
    list_type: ListType,
) -> str:
    params = {
        "date": file_date.isoformat(),
        "type": str(int(list_type)),
        "Subscription-Key": api_key,
    }
    return f"{base_url.rstrip('/')}/documents.json?{urlencode(params)}"


def document_url(
    base_url: str,
    api_key: str,
    doc_id: str,
    file_type: DocumentFileType,
) -> str:
    params = {
        "type": str(int(file_type)),
        "Subscription-Key": api_key,
    }
    return f"{base_url.rstrip('/')}/documents/{doc_id}?{urlencode(params)}"


def parse_retry_after(value: str | None) -> float | None:
    # NOTE(spec-gap): Retry-After is not mentioned in the official spec.
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def raise_for(
    status: int,
    message: str | None,
    *,
    endpoint: str,
    raw: dict[str, Any] | None = None,
) -> None:
    text = message or ""
    masked = redact_url(endpoint)
    cls: type[EdinetAPIError]
    if status == 400:
        cls = EdinetBadRequestError
    elif status == 401:
        cls = EdinetAuthError
    elif status == 404:
        cls = EdinetNotFoundError
    elif status == 429:
        cls = EdinetRateLimitError
    elif status >= 500:
        cls = EdinetServerError
    else:
        cls = EdinetAPIError
    raise cls(text, status=status, endpoint=masked, raw=raw)


def classify(
    status_code: int,
    content_type: str | None,
    body: bytes,
    expected: ExpectedKind,
    *,
    endpoint: str = "",
) -> ParsedJSON | DownloadPayload:
    mime = mime_type(content_type)
    masked = redact_url(endpoint) if endpoint else ""

    if mime == "text/html":
        raise EdinetMaintenanceError(
            "EDINET returned HTML instead of API data",
            content_type=mime,
            endpoint=masked or None,
        )

    if mime == "application/json":
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EdinetUnexpectedResponseError(
                "response declared JSON but the body was not valid JSON",
                content_type=mime,
                endpoint=masked or None,
            ) from exc
        if not isinstance(data, dict):
            raise EdinetUnexpectedResponseError(
                "JSON response was not an object",
                content_type=mime,
                endpoint=masked or None,
            )
        if "StatusCode" in data:
            raise_for(
                int(data["StatusCode"]),
                data.get("message"),
                endpoint=masked,
                raw=data,
            )
        meta = data.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("status") is not None:
            status = str(meta["status"])
            if status != "200":
                raise_for(int(status), meta.get("message"), endpoint=masked, raw=data)
        if expected == "download":
            raise EdinetUnexpectedResponseError(
                "document download returned JSON; this is never a success body",
                content_type=mime,
                endpoint=masked or None,
            )
        return ParsedJSON(data)

    if expected == "download":
        if mime == "application/octet-stream":
            if body[:2] != b"PK":
                raise EdinetUnexpectedResponseError(
                    "octet-stream body was not a ZIP (missing PK magic)",
                    content_type=mime,
                    endpoint=masked or None,
                )
            return DownloadPayload(body, mime, "zip")
        if mime == "application/pdf":
            if body[:4] != b"%PDF":
                raise EdinetUnexpectedResponseError(
                    "PDF body did not start with %PDF",
                    content_type=mime,
                    endpoint=masked or None,
                )
            return DownloadPayload(body, mime, "pdf")
        # NOTE(spec-gap): DESIGN.md classifies unknown download MIME before the
        # non-2xx branch, which would hide 503/302 from raise_for and skip retries.
        if status_code < 200 or status_code >= 300:
            _raise_http_status(status_code, mime, masked)
        raise EdinetUnexpectedResponseError(
            f"unexpected download Content-Type {mime!r}",
            content_type=mime,
            endpoint=masked or None,
        )

    # NOTE(spec-gap): the spec says API errors use HTTP 200. Non-2xx is defensive.
    if status_code < 200 or status_code >= 300:
        _raise_http_status(status_code, mime, masked)

    raise EdinetUnexpectedResponseError(
        f"unexpected Content-Type {mime!r} for {expected} request",
        content_type=mime,
        endpoint=masked or None,
    )


def _raise_http_status(status_code: int, mime: str, endpoint: str) -> None:
    if 300 <= status_code < 400:
        raise EdinetMaintenanceError(
            f"HTTP {status_code} redirect; treating as a maintenance page",
            content_type=mime,
            endpoint=endpoint or None,
        )
    raise_for(status_code, f"HTTP {status_code}", endpoint=endpoint, raw=None)


def parse_document_list(data: dict[str, Any]) -> DocumentList:
    return DocumentList.model_validate(data)


def parse_metadata(data: dict[str, Any]) -> DocumentListMetadata:
    metadata = data.get("metadata", data)
    return DocumentListMetadata.model_validate(metadata)

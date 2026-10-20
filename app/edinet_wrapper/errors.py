from __future__ import annotations

from typing import Any


class EdinetError(Exception):
    pass


class EdinetConfigError(EdinetError):
    pass


class EdinetTransportError(EdinetError):
    """Connection failure or timeout, wrapping a redacted httpx error."""

    def __init__(self, message: str, *, original_message: str | None = None) -> None:
        super().__init__(message)
        self.original_message = original_message or message


class EdinetUnexpectedResponseError(EdinetError):
    """HTML, unknown Content-Type, or a body that failed magic-byte checks."""

    def __init__(
        self,
        message: str,
        *,
        content_type: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.content_type = content_type
        self.endpoint = endpoint


class EdinetMaintenanceError(EdinetUnexpectedResponseError):
    pass


class EdinetAPIError(EdinetError):
    def __init__(
        self,
        message: str,
        *,
        status: int,
        endpoint: str,
        raw: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{status} {message} ({endpoint})")
        self.status = status
        self.message = message
        self.endpoint = endpoint
        self.raw = raw


class EdinetBadRequestError(EdinetAPIError):
    """HTTP/API status 400."""


class EdinetAuthError(EdinetAPIError):
    """HTTP/API status 401."""


class EdinetNotFoundError(EdinetAPIError):
    """HTTP/API status 404."""


class EdinetRateLimitError(EdinetAPIError):
    """HTTP/API status 429."""


class EdinetServerError(EdinetAPIError):
    """HTTP/API status 500 or other 5xx."""


class EdinetFileNotAvailableError(EdinetError):
    """The list flags say this file type is not present; no request was sent."""

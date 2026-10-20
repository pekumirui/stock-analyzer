from __future__ import annotations

import random

from .config import (
    DEFAULT_BACKOFF_BASE,
    DEFAULT_BACKOFF_MAX,
    DEFAULT_JITTER,
    DEFAULT_MAX_RETRIES,
    MAINTENANCE_RETRY_CAP,
    RATE_LIMIT_FLOOR,
)
from .errors import (
    EdinetMaintenanceError,
    EdinetRateLimitError,
    EdinetServerError,
    EdinetTransportError,
)

Retryable = (
    EdinetRateLimitError,
    EdinetServerError,
    EdinetTransportError,
    EdinetMaintenanceError,
)


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, Retryable)


def should_retry(exc: BaseException, attempt: int, max_retries: int = DEFAULT_MAX_RETRIES) -> bool:
    if not is_retryable(exc):
        return False
    limit = max_retries
    if isinstance(exc, EdinetMaintenanceError):
        limit = min(max_retries, MAINTENANCE_RETRY_CAP)
    return attempt < limit


def sleep_seconds(
    exc: BaseException,
    attempt: int,
    *,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    jitter: float = DEFAULT_JITTER,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    if isinstance(exc, EdinetRateLimitError) and retry_after is not None:
        return max(float(retry_after), 0.0)
    delay = min(backoff_max, backoff_base * (2**attempt))
    jitter_amt = (rng or random).uniform(0, jitter)
    delay = delay + jitter_amt
    if isinstance(exc, EdinetRateLimitError):
        delay = max(delay, RATE_LIMIT_FLOOR)
    return float(delay)

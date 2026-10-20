from __future__ import annotations

import logging
import re
from collections.abc import Mapping

_KEY_QUERY = re.compile(r"(Subscription-Key=)([^&]*)", re.IGNORECASE)
_KEY_JSON = re.compile(r'("Subscription-Key"\s*:\s*")([^"]*)(")', re.IGNORECASE)


def redact_url(url: str) -> str:
    return _KEY_QUERY.sub(r"\1***", url)


def redact_text(text: str) -> str:
    masked = redact_url(text)
    return _KEY_JSON.sub(r"\1***\3", masked)


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    # httpx logs request.url as an httpx.URL; LogRecord %s then calls str(url).
    text = str(value)
    lowered = text.lower()
    if "subscription-key=" in lowered or '"subscription-key"' in lowered:
        return redact_text(text)
    return value


class RedactLogFilter(logging.Filter):
    """Mask Subscription-Key values in httpx/httpcore log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_value(record.msg)
        if record.args:
            if isinstance(record.args, Mapping):
                record.args = {key: _redact_value(value) for key, value in record.args.items()}
            else:
                record.args = tuple(_redact_value(value) for value in record.args)
        cached = record.__dict__.get("message")
        if isinstance(cached, str):
            record.message = redact_text(cached)

        original_get_message = record.getMessage

        def get_message() -> str:
            return redact_text(original_get_message())

        record.getMessage = get_message  # type: ignore[method-assign]
        return True


def install_httpx_redaction() -> None:
    """Attach a redacting filter to loggers that print request URLs."""
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if any(isinstance(item, RedactLogFilter) for item in logger.filters):
            continue
        logger.addFilter(RedactLogFilter())

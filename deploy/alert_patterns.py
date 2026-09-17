"""Alert line registry. fluent-bit.conf Grep filters must contain each regex."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AlertPattern:
    source: str  # app | batch | nginx | mcp | systemd
    kind: str  # asgi | access_5xx | startup | fail | summary | traceback | jquants | xbrl | cron_exit | nginx | unit
    regex: str  # applied to the log line (or MESSAGE for systemd)


# failed=0 / errors=0 must not match; 10+ must (0* then a non-zero digit).
ALERT_PATTERNS: tuple[AlertPattern, ...] = (
    AlertPattern("app", "asgi", r"ERROR: Exception in ASGI application"),
    AlertPattern("app", "access_5xx", r" 5[0-9][0-9] "),
    AlertPattern("app", "startup", r"ERROR: Error loading ASGI app"),
    AlertPattern("app", "fail", r"SchemaMismatch"),
    AlertPattern("batch", "fail", r"^fail "),
    AlertPattern("batch", "cron_exit", r"^fail cron "),
    AlertPattern("batch", "summary", r"failed=0*[1-9][0-9]*"),
    AlertPattern("batch", "summary", r"errors=0*[1-9][0-9]*"),
    AlertPattern("batch", "traceback", r"Traceback"),
    AlertPattern("batch", "jquants", r"リトライ上限"),
    AlertPattern("batch", "jquants", r"JQUANTS_API_KEY が未設定"),
    AlertPattern("batch", "xbrl", r"list truncated"),
    AlertPattern("batch", "xbrl", r"error missing-zip"),
    AlertPattern("batch", "xbrl", r"failed doc_id="),
    AlertPattern("batch", "xbrl", r"normalize failed"),
    AlertPattern("nginx", "nginx", r"\[error\]"),
    AlertPattern("nginx", "nginx", r"\[crit\]"),
    AlertPattern("nginx", "nginx", r"\[emerg\]"),
    AlertPattern("nginx", "nginx", r"\[alert\]"),
    AlertPattern("mcp", "fail", r"ERROR"),
    AlertPattern("mcp", "traceback", r"Traceback"),
    AlertPattern("mcp", "fail", r"MCP_TOKEN is unset"),
    AlertPattern("systemd", "unit", r"Failed with result"),
    AlertPattern("systemd", "unit", r"Main process exited"),
)

SECRET_LINE_MARKERS: tuple[str, ...] = (
    "hooks.slack.com",
    "?token=",
    "JQUANTS_API_KEY=",
    "MCP_TOKEN=",
    "SLACK_WEBHOOK",
)

GREP_FIELD: dict[str, str] = {
    "app": "log",
    "batch": "log",
    "nginx": "log",
    "mcp": "log",
    "systemd": "MESSAGE",
}


def should_page(source: str, line: str) -> bool:
    if any(marker in line for marker in SECRET_LINE_MARKERS):
        return False
    for pattern in ALERT_PATTERNS:
        if pattern.source != source:
            continue
        if re.search(pattern.regex, line):
            return True
    return False

"""Alert line registry. fluent-bit.conf Grep filters must contain each regex."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class AlertPattern:
    source: str
    kind: str
    regex: str


ALERT_PATTERNS: tuple[AlertPattern, ...] = (
    AlertPattern("app", "asgi", r"ERROR:\s+Exception in ASGI application"),
    AlertPattern("app", "access_5xx", r" 5[0-9][0-9] "),
    AlertPattern("app", "startup", r"ERROR:\s+Error loading ASGI app"),
    AlertPattern("app", "fail", r"SchemaMismatch"),
    AlertPattern(
        "batch",
        "cron_exit",
        r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} fail cron ",
    ),
    AlertPattern("batch", "summary", r"failed=0*[1-9][0-9]*"),
    AlertPattern("batch", "summary", r"errors=0*[1-9][0-9]*"),
    AlertPattern("batch", "summary", r"stale_new=0*[1-9][0-9]*"),
    AlertPattern("batch", "jquants", r"リトライ上限"),
    AlertPattern("batch", "jquants", r"JQUANTS_API_KEY が未設定"),
    AlertPattern("batch", "xbrl", r"list truncated"),
    AlertPattern("batch", "xbrl", r"error missing-zip"),
    AlertPattern("batch", "xbrl", r"(^|[^-])failed doc_id="),
    AlertPattern("batch", "xbrl", r"normalize failed"),
    AlertPattern("batch", "edinet", r"WARNING edinet lvh obligation-date"),
    AlertPattern("batch", "jquants", r"WARNING jquants statements period-"),
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

# rewrite_tag Rule splits on spaces, so these must stay space-free.
# Tag `quiet` (#alerts-quiet). Do not prefix with nginx — Match nginx is prefix.
QUIET_NGINX_REGEXES: tuple[str, ...] = (
    r"limiting.connections.by.zone",
    r"connect\(\).failed.\(111:.Connection.refused\)",
)
QUIET_OUTPUT_TAG = "quiet"

# Designed denials: nginx-site.sh `limit_except GET { deny all; }` on the public
# /monthly and /api/v1/tables locations. Dropped (not paged) by a grep Exclude
# on the nginx tag. GET/HEAD, other paths/methods/deny reasons still page.
EXPECTED_NGINX_DENY_REGEXES: tuple[str, ...] = (
    (
        r'\[error\] [0-9]+.[0-9]+: \*[0-9]+ access forbidden by rule, client: [^ ,]+, server: [^ ,]+, '
        r'request: "(POST|PUT|PATCH|DELETE|OPTIONS) '
        r'(/monthly([/?][^ "]*)?|/api/v1/tables(\?[^ "]*)?|/api/v1/tables/[0-9][0-9A-Z]{3}[0-9](/[0-9]{4})?(\?[^ "]*)?) '
        r'HTTP/[0-9.]+"'
    ),
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

JST = timezone(timedelta(hours=9), name="JST")
# Named "danger" is ignored on Block Kit attachments (bar falls back to app blue).
SLACK_COLOR_DANGER = "#E01E5A"
SLACK_HEADER_MAX = 150
SLACK_SECTION_MAX = 2900
SLACK_FALLBACK_MAX = 2000


def format_jst(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(JST).strftime(
        "%Y-%m-%d %H:%M:%S JST"
    )


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def slack_payload(source: str, line: str, epoch: float) -> dict[str, object]:
    """Incoming Webhook body: JST clock + hex-red attachment bar.

    Attachment may only have color + blocks. Mixing fallback/ts/title with
    blocks makes Slack return HTTP 400 invalid_attachments.
    """
    src = (source or "log").strip() or "log"
    text = (line or "").replace("\r\n", "\n").rstrip("\n")
    jst = format_jst(epoch)
    header = _clip(f"error · {src}", SLACK_HEADER_MAX)
    fenced = text.replace("```", "'''")
    body = _clip(f"*{jst}*\n```\n{fenced}\n```", SLACK_SECTION_MAX)
    fallback = _clip(f"[{src}] {jst}: {text}", SLACK_FALLBACK_MAX)
    return {
        "text": fallback,
        "attachments": [
            {
                "color": SLACK_COLOR_DANGER,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": header, "emoji": True},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": body},
                    },
                ],
            }
        ],
    }


def slack_payload_json(source: str, line: str, epoch: float) -> str:
    return json.dumps(slack_payload(source, line, epoch), ensure_ascii=False, separators=(",", ":"))


def is_quiet_alert(source: str, line: str) -> bool:
    if source != "nginx":
        return False
    return any(re.search(regex, line) for regex in QUIET_NGINX_REGEXES)


def alert_output_tag(source: str, line: str) -> str:
    """fluent-bit OUTPUT Match after quiet rewrite_tag."""
    if is_quiet_alert(source, line):
        return QUIET_OUTPUT_TAG
    return source


def should_page(source: str, line: str) -> bool:
    if any(marker in line for marker in SECRET_LINE_MARKERS):
        return False
    if source == "nginx" and any(re.search(regex, line) for regex in EXPECTED_NGINX_DENY_REGEXES):
        return False
    for pattern in ALERT_PATTERNS:
        if pattern.source != source:
            continue
        if re.search(pattern.regex, line):
            return True
    return False

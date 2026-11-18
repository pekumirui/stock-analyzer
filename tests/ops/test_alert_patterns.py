"""AlertPattern fixtures: page / do not page. fluent-bit.conf must list each regex."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))

from alert_patterns import (
    ALERT_PATTERNS,
    EXPECTED_NGINX_DENY_REGEXES,
    GREP_FIELD,
    QUIET_NGINX_REGEXES,
    QUIET_OUTPUT_TAG,
    SECRET_LINE_MARKERS,
    SLACK_COLOR_DANGER,
    alert_output_tag,
    format_jst,
    is_quiet_alert,
    should_page,
    slack_payload,
    slack_payload_json,
)

FORBIDDEN_WEBHOOK_PREFIX = "hooks.slack.com" + "/services/"

PAGE: tuple[tuple[str, str], ...] = (
    ("app", "ERROR: Exception in ASGI application"),
    ("app", "ERROR:    Exception in ASGI application"),
    ("app", 'INFO:     127.0.0.1:43721 - "GET /api/screener HTTP/1.1" 500 Internal Server Error'),
    ("app", "ERROR: Error loading ASGI app. Could not import module \"app.main\"."),
    ("app", "ERROR:    Error loading ASGI app. Could not import module \"app.main\"."),
    ("app", "SchemaMismatch: stocks missing column"),
    ("batch", "2026-09-30 18:00:01 fail cron jobs/market/run_batch.py exit=1"),
    ("batch", "2026-09-30 18:00:01 fail cron cwd exit=1"),
    ("batch", "2026-10-02 10:20:01 fail cron jobs/monthly/ir_monthly_html_r2.py exit=127"),
    ("batch", "2026-09-30 18:00:01 tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=3"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=3"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=10"),
    ("batch", "material_stats done written=0 failed=1"),
    ("batch", "2026-10-17 07:15:01 material_stats done written=0 failed=2"),
    ("batch", "parse processed=3 errors=2 skipped=0"),
    ("batch", "parse processed=3 errors=10 skipped=0"),
    ("batch", "2026-12-13 22:27:10 homepage_briefing stale_new=1 stale_known=2"),
    ("batch", "リトライ上限に達しました: /v2/equities/bars/daily"),
    ("batch", "JQUANTS_API_KEY が未設定です。"),
    ("batch", "warn list truncated for 2026-08-05: next_flag=1"),
    ("batch", "error missing-zip doc_id=S100ABC path=xbrl/7203/foo.zip"),
    ("batch", "failed doc_id=S100ABC code=72030 err=boom"),
    ("batch", "2026-10-22 12:00:00 failed doc_id=S100ABC code=72030 err=boom"),
    ("batch", "normalize failed doc_id=S100ABC code=72030 err=boom"),
    ("batch", "WARNING edinet lvh obligation-date-mismatch doc_id=S100QKKW recorded=2024-04-03 parsed=2025-04-03"),
    (
        "batch",
        (
            "2026-10-29 09:05:01 WARNING edinet lvh obligation-date-future "
            "doc_id=S100LATE filer=大量保有証券株式会社 code=190A0 "
            "obligation_date=2026-12-01 submit_date=2026-10-15"
        ),
    ),
    ("batch", "edinet lvh done parsed=0 failed=0 missing=0 errors=3 refreshed=0"),
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 connect() failed (111: Connection refused)"),
    (
        "nginx",
        (
            '2026/10/02 20:30:06 [error] 123#123: *456 limiting connections by zone "addr", '
            'client: 88.216.183.179, server: kabukpi.com, request: "GET /monthly/3542 HTTP/1.1"'
        ),
    ),
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 upstream timed out (110: Connection timed out)"),
    ("nginx", "2026/08/05 12:00:00 [crit] 12#12: *1 emerg-adjacent"),
    ("nginx", "2026/08/05 12:00:00 [emerg] 12#12: bind() failed"),
    ("nginx", "2026/08/05 12:00:00 [alert] 12#12: 768 worker_connections are not enough"),
    ("mcp", "ERROR: something broke"),
    ("mcp", "Traceback (most recent call last):"),
    ("mcp", "MCP_TOKEN is unset; all requests will be 401"),
    ("systemd", "Failed with result 'exit-code'."),
    ("systemd", "Main process exited, code=exited, status=1/FAILURE"),
)

NOT_PAGE: tuple[tuple[str, str], ...] = (
    ("batch", "skip exists monthly/9279/foo.pdf"),
    ("batch", "skip hash monthly/9279/foo.pdf == monthly/9279/old.pdf"),
    ("batch", "skip seen X123"),
    ("batch", "skip unknown-stock 3032"),
    ("batch", "skip parse monthly/9279/foo.html"),
    ("batch", "warn stocks db missing /home/stock/stock-analyzer/stock.db"),
    ("batch", "warn stocks map empty; unknown-stock skip disabled"),
    ("batch", "  429 Too Many Requests → 5秒待機して再試行 (/v2/equities/bars/daily)"),
    ("batch", "retry 429 wait=5.0s https://srsholdings.com/pages/ir-library-monthly/"),
    ("batch", "2026-08-05: 銘柄10 / 株価0 / TOPIX0 / 決算0"),
    ("batch", "完了"),
    ("batch", "normalize missing-required doc_id=S100ABC code=72030 items=NetSales"),
    ("batch", "saved xbrl/3032/foo.zip (12 bytes) unknown-stock"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=9 failed=0"),
    ("batch", "2026-12-13 22:27:10 homepage_briefing stale_new=0 stale_known=2"),
    ("batch", "2026-12-13 22:27:10 stale 80020 2026-11-04 15:00:00 known 2027年3月期 第2四半期決算短信"),
    ("batch", "edinet lvh download-failed doc_id=S100MMVL err=404 Not Found"),
    (
        "batch",
        (
            "2026-10-22 16:30:14 edinet lvh download-failed doc_id=S100MMVL "
            "err=404 Not Found (https://api.edinet-fsa.go.jp/api/v2/documents/S100MMVL?type=5)"
        ),
    ),
    ("batch", "edinet lvh missing n=164 sample=S100MMVL,S100MMWV"),
    ("batch", "edinet lvh outside-window n=12"),
    ("batch", "edinet lvh parse-error n=1 sample=S100BRKN"),
    ("batch", "edinet lvh download-error n=2 sample=S100SRV1,S100TMO1"),
    ("batch", "edinet lvh reparse-fixes n=23"),
    ("batch", "WARN edinet maintenance skip job=edinet_xbrl err=EDINET returned HTML instead of API data"),
    (
        "batch",
        (
            "2026-10-24 10:00:01 WARN edinet maintenance skip job=edinet_documents "
            "err=HTTP 302 redirect; treating as a maintenance page"
        ),
    ),
    ("batch", "edinet buyback parse-error n=1 sample=S100Z1HS"),
    (
        "batch",
        (
            "edinet buyback parse-error doc_id=S100Z1HS type=ValueError "
            "msg=取得状況のテキストブロックが無い"
        ),
    ),
    ("batch", "edinet buyback parse-error   app/edinet/buybacks.py:392 parse_buyback"),
    ("batch", "edinet lvh done parsed=73161 failed=0 missing=164 errors=0 refreshed=363"),
    ("batch", "material_stats done written=3 failed=0"),
    ("batch", "fail meti seidou list: WAF challenge: https://x. stop and retry next run"),
    ("batch", "parse processed=3 errors=0 skipped=1"),
    ("batch", "140120141028066531 (68570): done facts=348 verified=348 sum_mismatch=0/20 unparsed=1/8"),
    ("batch", "  140120141028066531 page 4: JSON が読めない: Extra data: line 24 column 4 (char 1381)"),
    ("batch", "  140120141028066531 page 5: errored: api_error"),
    ("batch", "fail cron jobs/market/run_batch.py exit=1"),
    ("batch", "2026-10-02 10:20:01 missing interpreter /home/stock/stock-analyzer/.venv/bin/python3 /home/stock/stock-analyzer/.venv/bin/python"),
    ("batch", "fail POST 7203 status=500 boom"),
    ("batch", "fail issuer 1878 fetch: HTTP Error 403: Forbidden"),
    ("batch", "skip issuer 8163 rate-limited: HTTP Error 429: Too Many Requests"),
    ("batch", "skip issuer 8164 host-cooled srsholdings.com"),
    ("batch", "ir_monthly_html_r2 done listed=55 saved=11 skipped=44 failed=0"),
    ("batch", "Traceback (most recent call last):"),
    ("app", 'INFO:     127.0.0.1:43721 - "GET / HTTP/1.1" 200 OK'),
    ("app", 'INFO:     127.0.0.1:43721 - "GET /api/v1/tables/7203/2024 HTTP/1.1" 404 Not Found'),
    ("app", "INFO:     Started server process [12345]"),
    ("app", "ERROR: Exception in ASGI application see hooks.slack.com"),
    ("app", "SchemaMismatch ?token=abc"),
    ("batch", "JQUANTS_API_KEY=not-a-real-key Traceback (most recent call last):"),
    ("mcp", "ERROR MCP_TOKEN=not-a-real-token"),
    ("batch", "failed=3 SLACK_WEBHOOK_URL=not-a-real-webhook"),
)

QUIET_PAGE: tuple[tuple[str, str], ...] = (
    (
        "nginx",
        (
            "2026/08/05 12:00:00 [error] 12#12: *1 connect() failed (111: Connection refused) "
            "while connecting to upstream"
        ),
    ),
    (
        "nginx",
        (
            '2026/10/02 20:30:06 [error] 123#123: *456 limiting connections by zone "addr", '
            'client: 88.216.183.179, server: kabukpi.com, request: "GET /monthly/3542 HTTP/1.1"'
        ),
    ),
)

INFRA_NGINX_PAGE: tuple[tuple[str, str], ...] = (
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 upstream timed out (110: Connection timed out)"),
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 connect() failed (113: No route to host)"),
    ("nginx", "2026/08/05 12:00:00 [emerg] 12#12: bind() failed"),
    ("nginx", "2026/08/05 12:00:00 [crit] 12#12: *1 emerg-adjacent"),
    ("nginx", "2026/08/05 12:00:00 [alert] 12#12: 768 worker_connections are not enough"),
)


# nginx-site.sh limit_except GET { deny all; } (real error.log lines 2026-10/11). Not paged.
EXPECTED_NGINX_DENY: tuple[tuple[str, str], ...] = (
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 59941#59941: *4841 access forbidden by rule, client: 34.32.224.183, '
            'server: kabukpi.com, request: "POST /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/10/28 00:39:53 [error] 731395#731395: *4245 access forbidden by rule, client: 144.217.135.164, '
            'server: kabukpi.com, request: "OPTIONS /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 2400:cb00::1, '
            'server: kabukpi.com, request: "POST /monthly/7203?x=1 HTTP/2.0", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "DELETE /api/v1/tables HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "PUT /api/v1/tables/130A0/2025 HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
)

# Must keep paging on #alerts-infra (KAB-401): auth failures, real 5xx, upstream, crit/emerg,
# and any deny that is not the designed limit_except one.
INFRA_NGINX_KEEP: tuple[tuple[str, str], ...] = (
    (
        "nginx",
        (
            "2026/11/09 09:42:06 [error] 97863#97863: *7449 auth request unexpected status: 404 while sending to client, "
            'client: 74.235.120.220, server: kabukpi.com, request: "GET /app HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            "2026/11/09 09:42:22 [error] 97860#97860: *7467 auth request unexpected status: 404 while sending to client, "
            'client: 74.235.120.220, server: kabukpi.com, request: "GET /api/screener HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "GET /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "HEAD /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "POST /app HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "POST /monthlyx HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "PROPFIND /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "POST /api/v1/tables/7203/extra HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 directory index of "/x/" is forbidden, client: 1.2.3.4, '
            'server: kabukpi.com, request: "POST /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [error] 1#1: *2 upstream prematurely closed connection while reading response header '
            'from upstream, client: 1.2.3.4, server: kabukpi.com, request: "POST /monthly HTTP/1.1", '
            'upstream: "http://127.0.0.1:8000/monthly", host: "kabukpi.com"'
        ),
    ),
    (
        "nginx",
        (
            '2026/11/08 14:07:21 [crit] 1#1: *2 access forbidden by rule, client: 1.2.3.4, '
            'server: kabukpi.com, request: "POST /monthly HTTP/1.1", host: "kabukpi.com"'
        ),
    ),
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 upstream timed out (110: Connection timed out)"),
    ("nginx", "2026/08/05 12:00:00 [emerg] 12#12: bind() failed"),
)

# Real app access lines from the same 2026-11-09 incident must stay on #alerts-app.
APP_KEEP: tuple[tuple[str, str], ...] = (
    ("app", 'INFO:     127.0.0.1:43721 - "GET /app HTTP/1.1" 500 Internal Server Error'),
    ("app", 'INFO:     127.0.0.1:43721 - "GET /api/screener HTTP/1.1" 500 Internal Server Error'),
)

def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_fixtures() -> None:
    for source, line in PAGE:
        if not should_page(source, line):
            _fail(f"should page {source}: {line!r}")
    for source, line in NOT_PAGE:
        if should_page(source, line):
            _fail(f"must not page {source}: {line!r}")
    for source, line in QUIET_PAGE:
        if not should_page(source, line):
            _fail(f"quiet line must still page {source}: {line!r}")
        if alert_output_tag(source, line) != QUIET_OUTPUT_TAG:
            _fail(f"quiet line must route to {QUIET_OUTPUT_TAG}: {line!r}")
        if not is_quiet_alert(source, line):
            _fail(f"is_quiet_alert missed {line!r}")
    for source, line in INFRA_NGINX_PAGE:
        if not should_page(source, line):
            _fail(f"infra nginx must page: {line!r}")
        if alert_output_tag(source, line) != "nginx":
            _fail(f"real nginx error must stay on nginx tag: {line!r}")
        if is_quiet_alert(source, line):
            _fail(f"real nginx error must not be quiet: {line!r}")
    if QUIET_OUTPUT_TAG.startswith("nginx"):
        _fail("quiet tag must not start with nginx (Match nginx is prefix)")
    for source, line in EXPECTED_NGINX_DENY:
        if should_page(source, line):
            _fail(f"designed nginx deny must not page: {line!r}")
    for source, line in (*INFRA_NGINX_KEEP, *APP_KEEP):
        if not should_page(source, line):
            _fail(f"must keep paging {source}: {line!r}")
        if alert_output_tag(source, line) != source:
            _fail(f"must stay on {source} tag: {line!r}")


def test_expected_nginx_deny_exclude_in_conf() -> None:
    """Exclude lines on Match nginx equal EXPECTED_NGINX_DENY_REGEXES; nowhere else."""
    conf = (ROOT / "deploy" / "fluent-bit.conf").read_text(encoding="utf-8")
    excludes: list[tuple[str, str]] = []
    for block in _grep_blocks(conf):
        lines = block["lines"]
        assert isinstance(lines, list)
        for line in lines:
            assert isinstance(line, str)
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].lower() == "exclude":
                excludes.append((str(block["match"]), f"{parts[1]} {parts[2]}"))
    nginx_excludes = sorted(val for match, val in excludes if match == "nginx")
    want = sorted(f"log {regex}" for regex in EXPECTED_NGINX_DENY_REGEXES)
    if nginx_excludes != want:
        _fail(f"fluent-bit.conf nginx Exclude must equal EXPECTED_NGINX_DENY_REGEXES: {nginx_excludes!r}")
    for regex in EXPECTED_NGINX_DENY_REGEXES:
        if "#" in regex:
            _fail("nginx Exclude regex must not contain # (config comment)")
        for _match, val in excludes:
            if val == f"log {regex}" and _match != "nginx":
                _fail(f"designed deny Exclude must only Match nginx, got {_match}")
    compiled = [re.compile(r) for r in EXPECTED_NGINX_DENY_REGEXES]
    for _source, line in EXPECTED_NGINX_DENY:
        if not any(rx.search(line) for rx in compiled):
            _fail(f"Exclude must drop designed deny: {line!r}")
    for _source, line in (*INFRA_NGINX_KEEP, *INFRA_NGINX_PAGE, *QUIET_PAGE):
        if any(rx.search(line) for rx in compiled):
            _fail(f"Exclude must not drop: {line!r}")


def test_expected_nginx_deny_follows_limit_except() -> None:
    """The suppressed paths are exactly the nginx-site.sh locations with limit_except GET deny all."""
    site = (ROOT / "deploy" / "nginx-site.sh").read_text(encoding="utf-8")
    denied: list[str] = []
    location = ""
    for raw in site.splitlines():
        stripped = raw.strip()
        if stripped.startswith("location "):
            location = stripped
        elif stripped == "limit_except GET { deny all; }":
            denied.append(location)
    want = [
        "location ^~ /monthly {",
        "location = /api/v1/tables {",
        'location ~ "^/api/v1/tables/[0-9][0-9A-Z]{3}[0-9](?:/[0-9]{4})?\\$" {',
    ]
    if denied != want:
        _fail(
            "nginx-site.sh limit_except locations changed; update EXPECTED_NGINX_DENY_REGEXES "
            f"and this test together: {denied!r}"
        )
    if "deny all;" in site.replace("limit_except GET { deny all; }", ""):
        _fail("nginx-site.sh has another deny all; decide whether it should page before excluding it")


def _section_body(conf: str, header: str, needle: str) -> str:
    for part in conf.split(header)[1:]:
        body = part.split("[", 1)[0]
        if needle in body:
            return body
    return ""


OUTPUT_URI_BY_MATCH = {
    "app": "${SLACK_WEBHOOK_URI_APP}",
    "batch": "${SLACK_WEBHOOK_URI_BATCH}",
    "nginx": "${SLACK_WEBHOOK_URI_INFRA}",
    "mcp": "${SLACK_WEBHOOK_URI_INFRA}",
    "systemd": "${SLACK_WEBHOOK_URI_INFRA}",
    "quiet": "${SLACK_WEBHOOK_URI_QUIET}",
}
DEFAULT_SLACK_URI = "${SLACK_WEBHOOK_URI}"
CATCHALL_TAG_RE = r"^(?!(app|batch|nginx|mcp|systemd|quiet)$).+$"
CHANNEL_WEBHOOK_SECRETS = (
    "SLACK_WEBHOOK_URL_APP",
    "SLACK_WEBHOOK_URL_BATCH",
    "SLACK_WEBHOOK_URL_INFRA",
    "SLACK_WEBHOOK_URL_QUIET",
)


def _grep_blocks(conf: str) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw in conf.splitlines():
        stripped = raw.strip()
        keep_ws = raw.rstrip("\n").lstrip()
        if stripped == "[FILTER]":
            if current is not None:
                blocks.append(current)
            current = {"name": "", "match": "", "lines": []}
            continue
        if stripped.startswith("[") and current is not None:
            blocks.append(current)
            current = None
        if current is None:
            continue
        lines = current["lines"]
        assert isinstance(lines, list)
        lines.append(keep_ws)
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            key, val = parts[0].lower(), parts[1]
            if key == "name":
                current["name"] = val
            elif key == "match":
                current["match"] = val
    if current is not None:
        blocks.append(current)
    return [b for b in blocks if b["name"] == "grep"]


def _rewrite_tag_rules(conf: str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    in_block = False
    match = ""
    name = ""
    for raw in conf.splitlines():
        stripped = raw.strip()
        if stripped == "[FILTER]":
            in_block = True
            match = ""
            name = ""
            continue
        if stripped.startswith("[") and in_block:
            in_block = False
            continue
        if not in_block:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        key, val = parts[0].lower(), parts[1]
        if key == "name":
            name = val
        elif key == "match":
            match = val
        elif key == "rule" and name == "rewrite_tag":
            if len(parts) != 5:
                _fail(f"rewrite_tag Rule must be field regex tag keep (no spaces in regex): {stripped!r}")
            rules.append(
                {
                    "match": match,
                    "field": parts[1],
                    "regex": parts[2],
                    "tag": parts[3],
                    "keep": parts[4],
                }
            )
    return rules


def _output_blocks(conf: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in conf.splitlines():
        stripped = raw.strip()
        if stripped == "[OUTPUT]":
            if current is not None:
                blocks.append(current)
            current = {"name": "", "match": "", "match_regex": "", "uri": "", "host": ""}
            continue
        if stripped.startswith("[") and current is not None:
            blocks.append(current)
            current = None
        if current is None:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts[0].lower(), parts[1]
        if key in current:
            current[key] = val
    if current is not None:
        blocks.append(current)
    return blocks


def test_conf_has_registry_regexes() -> None:
    path = ROOT / "deploy" / "fluent-bit.conf"
    if not path.is_file():
        _fail("deploy/fluent-bit.conf is missing")
    conf = path.read_text(encoding="utf-8")
    greps = _grep_blocks(conf)
    for pattern in ALERT_PATTERNS:
        field = GREP_FIELD[pattern.source]
        found = False
        for block in greps:
            if block["match"] != pattern.source:
                continue
            lines = block["lines"]
            assert isinstance(lines, list)
            for line in lines:
                assert isinstance(line, str)
                if not line.lower().startswith("regex"):
                    continue
                if field in line and pattern.regex in line:
                    found = True
                    break
            if found:
                break
        if not found:
            _fail(f"fluent-bit.conf missing Grep regex for {pattern.source}: {pattern.regex!r}")
    for block in greps:
        match = block["match"]
        if match not in GREP_FIELD:
            continue
        field = GREP_FIELD[match]
        lines = block["lines"]
        assert isinstance(lines, list)
        for line in lines:
            assert isinstance(line, str)
            if not line.lower().startswith("regex"):
                continue
            owned = False
            for pattern in ALERT_PATTERNS:
                if pattern.source != match:
                    continue
                if field in line and pattern.regex in line:
                    owned = True
                    break
            if not owned:
                _fail(f"fluent-bit.conf extra Grep regex for {match}: {line!r}")
    if "Webhook ${SLACK_WEBHOOK_URL}" in conf:
        _fail("fluent-bit.conf must not use the slack output plugin")
    if "Name         http" not in conf:
        _fail("fluent-bit.conf must POST via http")
    if "Host         hooks.slack.com" not in conf:
        _fail("fluent-bit.conf must POST to hooks.slack.com")
    outputs = _output_blocks(conf)
    if not outputs:
        _fail("fluent-bit.conf must have [OUTPUT] blocks")
    matched: dict[str, str] = {}
    catchall: dict[str, str] | None = None
    for block in outputs:
        if block["name"] != "http":
            _fail(f"fluent-bit.conf OUTPUT must be http: {block}")
        if block["host"] != "hooks.slack.com":
            _fail(f"fluent-bit.conf OUTPUT must POST to hooks.slack.com: {block}")
        if block["match"] == "*":
            _fail("fluent-bit.conf OUTPUT must not Match * (would duplicate tagged alerts)")
        if block["match"] and block["match_regex"]:
            _fail(f"fluent-bit.conf OUTPUT must not set both Match and Match_Regex: {block}")
        if block["match"]:
            if block["match"] in matched:
                _fail(f"fluent-bit.conf duplicate OUTPUT Match {block['match']}")
            expected = OUTPUT_URI_BY_MATCH.get(block["match"])
            if expected is None:
                _fail(f"fluent-bit.conf OUTPUT Match {block['match']!r} is not a known tag")
            if block["uri"] != expected:
                _fail(
                    f"fluent-bit.conf OUTPUT Match {block['match']} must use URI {expected}, "
                    f"got {block['uri']!r}"
                )
            matched[block["match"]] = block["uri"]
            continue
        if block["match_regex"] != CATCHALL_TAG_RE:
            _fail(
                "fluent-bit.conf catch-all OUTPUT Match_Regex must exclude "
                f"app/batch/nginx/mcp/systemd/quiet, got {block['match_regex']!r}"
            )
        if block["uri"] != DEFAULT_SLACK_URI:
            _fail(f"fluent-bit.conf catch-all OUTPUT must use URI {DEFAULT_SLACK_URI}")
        if catchall is not None:
            _fail("fluent-bit.conf duplicate catch-all OUTPUT")
        catchall = block
    missing = sorted(set(OUTPUT_URI_BY_MATCH) - set(matched))
    if missing:
        _fail(f"fluent-bit.conf missing tagged OUTPUT Match: {missing}")
    if catchall is None:
        _fail("fluent-bit.conf must have a catch-all OUTPUT for unclassified tags")
    catch_re = re.compile(CATCHALL_TAG_RE)
    for tag in OUTPUT_URI_BY_MATCH:
        if catch_re.search(tag):
            _fail(f"catch-all Match_Regex must not match tagged source {tag}")
    if catch_re.search("other") is None:
        _fail("catch-all Match_Regex must match unclassified tags")
    if catch_re.search(QUIET_OUTPUT_TAG):
        _fail("catch-all Match_Regex must not match quiet (would duplicate #alerts-quiet)")
    rewrite_rules = _rewrite_tag_rules(conf)
    if not rewrite_rules:
        _fail("fluent-bit.conf must rewrite_tag quiet nginx lines")
    listed = [r["regex"] for r in rewrite_rules]
    for regex in QUIET_NGINX_REGEXES:
        if regex not in listed:
            _fail(f"fluent-bit.conf rewrite_tag missing quiet regex {regex!r}")
    for rule in rewrite_rules:
        if rule["match"] != "nginx":
            _fail(f"quiet rewrite_tag must Match nginx: {rule}")
        if rule["field"] != "$log":
            _fail(f"quiet rewrite_tag must use $log: {rule}")
        if rule["tag"] != QUIET_OUTPUT_TAG:
            _fail(f"quiet rewrite_tag must emit {QUIET_OUTPUT_TAG}: {rule}")
        if rule["keep"] != "false":
            _fail(f"quiet rewrite_tag must drop the nginx copy (keep false): {rule}")
        if rule["tag"].startswith("nginx"):
            _fail("quiet tag must not start with nginx (Match nginx is prefix)")
    compiled = [re.compile(rule["regex"]) for rule in rewrite_rules]
    for _source, line in QUIET_PAGE:
        if not any(rx.search(line) for rx in compiled):
            _fail(f"rewrite_tag regexes must match quiet line: {line!r}")
    for _source, line in INFRA_NGINX_PAGE:
        if any(rx.search(line) for rx in compiled):
            _fail(f"rewrite_tag must not steal real nginx errors: {line!r}")
    if "body_key     $body" not in conf or "headers_key  $headers" not in conf:
        _fail("fluent-bit.conf must send the Lua webhook body as-is")
    if "call    format_slack" not in conf:
        _fail("fluent-bit.conf must run format_slack")
    if "/etc/fluent-bit/slack.lua" not in conf:
        _fail("fluent-bit.conf must load /etc/fluent-bit/slack.lua")
    if "HTTP_Server    Off" not in conf and "HTTP_Server  Off" not in conf:
        _fail("fluent-bit.conf must set HTTP_Server Off")
    for marker in SECRET_LINE_MARKERS:
        if marker == "?token=":
            needle = r"\?token="
        else:
            needle = marker
        if needle not in conf:
            _fail(f"fluent-bit.conf missing Exclude {needle!r}")
    for raw in conf.splitlines():
        stripped = raw.strip()
        if not stripped.lower().startswith("path") or stripped.lower().startswith("path_key"):
            continue
        path_val = stripped.split(None, 1)[1] if " " in stripped else ""
        if "access.log" in path_val:
            _fail("fluent-bit.conf must not tail nginx access.log")
        if "html_ingest_ledger" in path_val:
            _fail("fluent-bit.conf must not tail html_ingest_ledger.json")
        if path_val.endswith(".1") or path_val.endswith(".gz"):
            _fail(f"fluent-bit.conf must tail live files only: {path_val}")
        if "*" in path_val and path_val != "/var/log/app/*.log":
            _fail(f"fluent-bit.conf must tail live files only: {path_val}")
    if "/home/stock/stock-analyzer/.env" in conf:
        _fail("fluent-bit.conf must not EnvironmentFile the app .env")
    batch_in = _section_body(conf, "[INPUT]", "/var/log/app/*.log")
    if "Path_Key" not in batch_in:
        _fail("batch tail must record the file path")
    if "/var/log/app/stock-analyzer.log" not in batch_in or "/var/log/app/mcp.log" not in batch_in:
        _fail("batch tail must exclude stock-analyzer.log and mcp.log")
    if "/var/log/app/batch.log" not in batch_in:
        _fail("batch tail must exclude leftover batch.log")
    if "call    source_from_file" not in conf:
        _fail("fluent-bit.conf must set batch source from the log file name")
    lua = (ROOT / "deploy" / "fluent-bit_slack.lua").read_text(encoding="utf-8")
    if "function source_from_file" not in lua or 'record["file"]' not in lua:
        _fail("fluent-bit_slack.lua must set source from the log file name")


def test_no_committed_webhook_url() -> None:
    roots = (
        ROOT / "deploy",
        ROOT / "mcp",
        ROOT / "jobs",
        ROOT / "tests",
        ROOT / "tools",
        ROOT / ".github",
        ROOT / "docs",
        ROOT / "README.md",
        ROOT / "AGENTS.md",
    )
    for root in roots:
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".png", ".jpg", ".pdf", ".db", ".gz", ".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if FORBIDDEN_WEBHOOK_PREFIX in text:
                _fail(f"{path} contains a Slack webhook path")


def _sudo_preserve_calls(script: str) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    lines = script.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("sudo ") and "--preserve-env=" in stripped:
            buf = stripped
            while buf.endswith("\\"):
                i += 1
                if i >= len(lines):
                    _fail("update.sh --preserve-env line continuation is truncated")
                buf = buf[:-1].rstrip() + " " + lines[i].strip()
            after = buf.split("--preserve-env=", 1)[1]
            env, _, rest = after.partition(" ")
            calls.append({"env": env, "cmd": rest})
        i += 1
    return calls


def test_secret_contract() -> None:
    """Webhook の名前が cicd.yml・update.sh の --preserve-env・sudoers の env_keep で揃っていること。

    受け渡しの動きは test_stub_sudo_channel_env_after_sudoers_refresh が sudo を差し替えて見る。
    """
    cicd = (ROOT / ".github" / "workflows" / "cicd.yml").read_text(encoding="utf-8")
    webhook_secrets = ("SLACK_WEBHOOK_URL", *CHANNEL_WEBHOOK_SECRETS)
    for name in webhook_secrets:
        if f"{name}: ${{{{ secrets.{name} }}}}" not in cicd:
            _fail(f"cicd.yml deploy env must include {name}")
        if f'"{name}"' not in cicd:
            _fail(f"cicd.yml export keys must include {name}")
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    if "upsert_env SLACK_WEBHOOK_URL" in update:
        _fail("update.sh must not upsert SLACK_WEBHOOK_URL into the app .env")
    if "SLACK_WEBHOOK_URL" not in update or "--preserve-env=" not in update:
        _fail("update.sh must pass SLACK_WEBHOOK_URL via sudo --preserve-env")
    calls = _sudo_preserve_calls(update)
    if len(calls) < 2:
        _fail("update.sh must sudo --sudoers-only before the full preserve-env provision")
    refresh = calls[0]
    if "--sudoers-only" not in refresh["cmd"] or "provision.sh" not in refresh["cmd"]:
        _fail("first sudo --preserve-env must run provision.sh --sudoers-only")
    refresh_env = [p for p in refresh["env"].split(",") if p]
    if refresh_env != ["APP_DIR"]:
        _fail(
            "sudoers-only --preserve-env must list only APP_DIR "
            f"(live sudoers may not env_keep new names yet): {refresh['env']!r}"
        )
    for name in CHANNEL_WEBHOOK_SECRETS:
        if name in refresh["env"].split(","):
            _fail(f"sudoers-only --preserve-env must not list {name}")
    full = next((c for c in calls if "--sudoers-only" not in c["cmd"] and "provision.sh" in c["cmd"]), None)
    if full is None:
        _fail("update.sh must sudo provision.sh with Slack --preserve-env after --sudoers-only")
    for name in webhook_secrets:
        if name not in full["env"].split(","):
            _fail(f"update.sh full --preserve-env must list {name}")
    # Pre-#373 live sudoers. First preserve-env must work before /etc/sudoers.d is replaced.
    old_keep = {
        "APP_DIR",
        "VPS_DOMAIN",
        "BASIC_AUTH_USER",
        "BASIC_AUTH_PASSWORD",
        "CERTBOT_EMAIL",
        "SLACK_WEBHOOK_URL",
    }
    rejected = [n for n in refresh_env if n not in old_keep]
    if rejected:
        _fail(f"sudoers-only --preserve-env would fail on pre-#373 sudoers: {rejected}")
    sudoers = (ROOT / "deploy" / "sudoers").read_text(encoding="utf-8")
    if "env_keep" not in sudoers:
        _fail("deploy/sudoers must env_keep Slack webhook URLs")
    for name in webhook_secrets:
        if name not in sudoers:
            _fail(f"deploy/sudoers must env_keep {name}")
    visudo_bin = shutil.which("visudo", path=os.pathsep.join(
        [os.environ.get("PATH", ""), "/usr/sbin", "/sbin"]))
    if visudo_bin is None:
        _fail("visudo not found (apt install sudo)")
    visudo = subprocess.run(
        [visudo_bin, "-cf", str(ROOT / "deploy" / "sudoers")],
        capture_output=True,
        text=True,
        check=False,
    )
    if visudo.returncode != 0:
        _fail(f"deploy/sudoers failed visudo -cf: {visudo.stderr or visudo.stdout}")
    for rel in ("deploy/update.sh", "deploy/provision.sh"):
        chk = subprocess.run(
            ["bash", "-n", str(ROOT / rel)],
            capture_output=True,
            text=True,
            check=False,
        )
        if chk.returncode != 0:
            _fail(f"bash -n {rel} failed: {chk.stderr or chk.stdout}")
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    for line in crontab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ".venv/bin/python" in stripped and "cron-run.sh" not in stripped:
            _fail(f"crontab python job must use cron-run.sh: {stripped}")
    wrapper = ROOT / "deploy" / "cron-run.sh"
    if not wrapper.is_file():
        _fail("deploy/cron-run.sh missing")
    listed = subprocess.check_output(
        ["git", "ls-files", "-s", "--", "deploy/cron-run.sh"],
        cwd=ROOT,
        text=True,
    )
    if not listed.startswith("100755"):
        _fail(f"deploy/cron-run.sh must be executable in git: {listed!r}")
    for line in crontab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ">>" in stripped or "batch.log" in stripped:
            _fail(f"crontab must not redirect logs: {stripped}")


def test_stub_sudo_channel_env_after_sudoers_refresh(tmp_path: Path) -> None:
    """Live sudoers rejects new --preserve-env names until --sudoers-only installs them."""
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    calls = _sudo_preserve_calls(update)
    if len(calls) < 2:
        _fail("update.sh missing sudoers refresh + full provision")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "sudo.log"
    keep = tmp_path / "keep.txt"
    old_keep = (
        "APP_DIR VPS_DOMAIN BASIC_AUTH_USER BASIC_AUTH_PASSWORD "
        "CERTBOT_EMAIL SLACK_WEBHOOK_URL"
    )
    keep.write_text(old_keep + "\n", encoding="utf-8")
    sudo = fake_bin / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$*\" >> {log.as_posix()!r}\n"
        f"keep=$(tr '\\n' ' ' < {keep.as_posix()!r})\n"
        "preserve=\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    --preserve-env=*) preserve="${arg#--preserve-env=}" ;;\n'
        "  esac\n"
        "done\n"
        "bad=\n"
        "IFS=',' read -r -a names <<< \"$preserve\"\n"
        'for n in "${names[@]}"; do\n'
        '  [[ -z "$n" ]] && continue\n'
        '  if [[ " $keep " != *" $n "* ]]; then\n'
        '    bad="${bad:+$bad, }$n"\n'
        "  fi\n"
        "done\n"
        'if [[ -n "$bad" ]]; then\n'
        '  echo "sudo: sorry, you are not allowed to set the following environment variables: $bad" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [[ " $* " == *" --sudoers-only "* ]]; then\n'
        f"  printf '%s %s\\n' \"$keep\" "
        f"{' '.join(CHANNEL_WEBHOOK_SECRETS)!r} "
        f"> {keep.as_posix()!r}\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    sudo.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    old = subprocess.run(
        [
            "sudo",
            (
                "--preserve-env=APP_DIR,VPS_DOMAIN,BASIC_AUTH_USER,BASIC_AUTH_PASSWORD,"
                "CERTBOT_EMAIL,SLACK_WEBHOOK_URL,SLACK_WEBHOOK_URL_APP,"
                "SLACK_WEBHOOK_URL_BATCH,SLACK_WEBHOOK_URL_INFRA"
            ),
            "provision.sh",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if old.returncode == 0:
        _fail("pre-#373 sudoers must reject channel webhook --preserve-env")
    if "SLACK_WEBHOOK_URL_APP" not in old.stderr or "not allowed" not in old.stderr:
        _fail(f"expected sudo env rejection, got {old.stderr!r}")
    keep.write_text(
        old_keep + " SLACK_WEBHOOK_URL_APP SLACK_WEBHOOK_URL_BATCH SLACK_WEBHOOK_URL_INFRA\n",
        encoding="utf-8",
    )
    post374 = subprocess.run(
        [
            "sudo",
            (
                "--preserve-env=APP_DIR,VPS_DOMAIN,BASIC_AUTH_USER,BASIC_AUTH_PASSWORD,"
                "CERTBOT_EMAIL,SLACK_WEBHOOK_URL,SLACK_WEBHOOK_URL_APP,"
                "SLACK_WEBHOOK_URL_BATCH,SLACK_WEBHOOK_URL_INFRA,SLACK_WEBHOOK_URL_QUIET"
            ),
            "provision.sh",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if post374.returncode == 0:
        _fail("post-#374 sudoers must reject SLACK_WEBHOOK_URL_QUIET until sudoers-only")
    if "SLACK_WEBHOOK_URL_QUIET" not in post374.stderr or "not allowed" not in post374.stderr:
        _fail(f"expected QUIET sudo env rejection, got {post374.stderr!r}")
    keep.write_text(old_keep + "\n", encoding="utf-8")
    for call in calls:
        cmd = call["cmd"].replace('"', "").split()
        proc = subprocess.run(
            ["sudo", f"--preserve-env={call['env']}", *cmd],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _fail(f"stub sudo {call} failed: {proc.stderr!r}")
    if "--sudoers-only" not in log.read_text(encoding="utf-8"):
        _fail("stub sudo did not run --sudoers-only")


def test_slack_payload_jst_danger() -> None:
    epoch = 1789689761
    if format_jst(epoch) != "2026-09-18 09:02:41 JST":
        _fail(f"JST clock: {format_jst(epoch)!r}")
    payload = slack_payload("batch", "fail cron slack-probe exit=1\n", epoch)
    if not isinstance(payload.get("attachments"), list) or not payload["attachments"]:
        _fail("slack payload needs attachments")
    att = payload["attachments"][0]
    assert isinstance(att, dict)
    if att.get("color") != SLACK_COLOR_DANGER:
        _fail(f"slack color: {att.get('color')!r}")
    legacy = {"fallback", "ts", "title", "text", "pretext", "mrkdwn_in", "fields"}
    extra = legacy.intersection(att)
    if extra:
        _fail(f"attachment mixed legacy keys with blocks: {sorted(extra)}")
    text = str(payload.get("text") or "")
    if "2026-09-18 09:02:41 JST" not in text:
        _fail(f"fallback missing JST: {text!r}")
    if "1789689761" in text or "timestamp" in text.lower():
        _fail(f"fallback leaked raw timestamp: {text!r}")
    blocks = att.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != 2:
        _fail("slack attachment needs header+section")
    header = blocks[0]
    section = blocks[1]
    assert isinstance(header, dict) and isinstance(section, dict)
    if header.get("type") != "header":
        _fail("first block must be header")
    header_text = header.get("text")
    assert isinstance(header_text, dict)
    if header_text.get("text") != "error · batch":
        _fail(f"header: {header_text.get('text')!r}")
    section_text = section.get("text")
    assert isinstance(section_text, dict)
    body = str(section_text.get("text") or "")
    if "*2026-09-18 09:02:41 JST*" not in body or "fail cron slack-probe exit=1" not in body:
        _fail(f"section missing JST/line: {body!r}")
    raw = slack_payload_json("batch", "fail cron slack-probe exit=1", epoch)
    if SLACK_COLOR_DANGER not in raw or "09:02:41 JST" not in raw:
        _fail("slack json missing hex red/JST")
    if SLACK_COLOR_DANGER != "#E01E5A":
        _fail(f"SLACK_COLOR_DANGER must be Slack danger hex: {SLACK_COLOR_DANGER!r}")
    lua = (ROOT / "deploy" / "fluent-bit_slack.lua").read_text(encoding="utf-8")
    if 'color":"#E01E5A"' not in lua or "JST" not in lua or "format_slack" not in lua:
        _fail("fluent-bit_slack.lua must emit #E01E5A + JST")
    if 'color":"danger"' in lua:
        _fail("named danger is ignored on Block Kit attachments")
    if '"fallback"' in lua.split("local json", 1)[-1]:
        _fail("lua attachment must not mix fallback with blocks")


def test_cron_run_splits_and_stamps(tmp_path: Path) -> None:
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    py = bindir / "python"
    py.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "script = next(a for a in sys.argv[1:] if a.endswith('.py'))\n"
        "print('out ' + script, flush=True)\n"
        "print('err ' + script, file=sys.stderr, flush=True)\n"
        "raise SystemExit(3 if '--fail' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    env = os.environ.copy()
    env["CRON_APP_DIR"] = str(app)
    env["CRON_LOG_DIR"] = str(logs)
    env["TZ"] = "UTC"
    wrapper = str(ROOT / "deploy" / "cron-run.sh")
    first = subprocess.Popen(
        ["bash", wrapper, "jobs/monthly/tdnet_monthly_r2.py", "1"],
        cwd=app,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        ["bash", wrapper, "jobs/xbrl/tdnet_xbrl_r2.py", "1", "--fail"],
        cwd=app,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out1, err1 = first.communicate(timeout=30)
    out2, err2 = second.communicate(timeout=30)
    if first.returncode != 0:
        _fail(f"monthly cron-run exit {first.returncode}: {out1!r} {err1!r}")
    if second.returncode != 3:
        _fail(f"xbrl cron-run exit {second.returncode}: {out2!r} {err2!r}")
    if out1 or err1 or out2 or err2:
        _fail(f"cron-run leaked stdio: {out1!r} {err1!r} {out2!r} {err2!r}")
    monthly = (logs / "tdnet_monthly_r2.log").read_text(encoding="utf-8")
    xbrl = (logs / "tdnet_xbrl_r2.log").read_text(encoding="utf-8")
    if "tdnet_xbrl_r2.py" in monthly or "tdnet_monthly_r2.py" in xbrl:
        _fail(f"job logs mixed: {monthly!r} {xbrl!r}")
    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")
    for text, needle in (
        (monthly, "out jobs/monthly/tdnet_monthly_r2.py"),
        (monthly, "err jobs/monthly/tdnet_monthly_r2.py"),
        (xbrl, "out jobs/xbrl/tdnet_xbrl_r2.py"),
        (xbrl, "err jobs/xbrl/tdnet_xbrl_r2.py"),
        (xbrl, "fail cron jobs/xbrl/tdnet_xbrl_r2.py 1 --fail exit=3"),
    ):
        if needle not in text:
            _fail(f"missing {needle!r} in {text!r}")
    if "fail cron" in monthly:
        _fail(f"success log has fail cron: {monthly!r}")
    for text in (monthly, xbrl):
        for line in text.splitlines():
            if stamp.match(line) is None:
                _fail(f"line missing JST stamp: {line!r}")
            logged = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=jst)
            if abs((now - logged).total_seconds()) > 120:
                _fail(f"stamp is not JST now: {line!r} now={now.isoformat()}")
    missing = tmp_path / "missing-app"
    env["CRON_APP_DIR"] = str(missing)
    bad = subprocess.run(
        ["bash", wrapper, "jobs/market/run_batch.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if bad.returncode != 1:
        _fail(f"missing cwd should exit 1, got {bad.returncode}")
    cwd_log = (logs / "run_batch.log").read_text(encoding="utf-8")
    if "fail cron cwd exit=1" not in cwd_log:
        _fail(f"cwd failure not logged: {cwd_log!r}")


_TDNET_XBRL_JOBS = (
    "jobs/xbrl/tdnet_xbrl_r2.py",
    "jobs/xbrl/xbrl_parse.py",
    "jobs/xbrl/xbrl_normalize.py",
    "jobs/xbrl/xbrl_statements.py",
    "jobs/market/tanshin_covers.py",
    "jobs/market/tdnet_briefing_r2.py",
    "jobs/market/homepage_briefing.py",
)


def _tdnet_xbrl_cron_line() -> str:
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    lines = [
        raw.strip()
        for raw in crontab.splitlines()
        if raw.strip()
        and not raw.strip().startswith("#")
        and _TDNET_XBRL_JOBS[0] in raw
    ]
    if len(lines) != 1:
        _fail(f"expected 1 TDnet XBRL cron line, got {len(lines)}: {lines!r}")
    return lines[0]


def test_crontab_chains_tdnet_xbrl() -> None:
    line = _tdnet_xbrl_cron_line()
    if not line.startswith("0 12-23 * * * "):
        _fail(f"TDnet XBRL cron must be hourly :00 at 12-23: {line!r}")
    if "deploy/cron-run.sh jobs/xbrl/tdnet_xbrl_r2.py 2" not in line:
        _fail(f"fetch must stay cron-run.sh days=2: {line!r}")
    if "deploy/cron-run.sh jobs/xbrl/xbrl_parse.py" not in line:
        _fail(f"parse must stay cron-run.sh: {line!r}")
    if "deploy/cron-run.sh jobs/xbrl/xbrl_normalize.py" not in line:
        _fail(f"normalize must stay cron-run.sh: {line!r}")
    if "tdnet_xbrl_r2.py 2; deploy/cron-run.sh jobs/xbrl/xbrl_parse.py;" not in line:
        _fail(f"fetch failure must still run parse (; not &&): {line!r}")
    if "xbrl_parse.py; deploy/cron-run.sh jobs/xbrl/xbrl_normalize.py;" not in line:
        _fail(f"parse failure must still run normalize (; not &&): {line!r}")
    if "xbrl_normalize.py; deploy/cron-run.sh jobs/xbrl/xbrl_statements.py;" not in line:
        _fail(f"normalize failure must still run statements (; not &&): {line!r}")
    i_fetch = line.index("tdnet_xbrl_r2.py")
    i_parse = line.index("xbrl_parse.py")
    i_norm = line.index("xbrl_normalize.py")
    i_stmt = line.index("xbrl_statements.py")
    if not (i_fetch < i_parse < i_norm < i_stmt):
        _fail(f"order must be fetch → parse → normalize → statements: {line!r}")
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    for raw in crontab.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^(10|20)\s", stripped) and (
            "xbrl_parse.py" in stripped or "xbrl_normalize.py" in stripped
        ):
            _fail(f":10/:20 standalone XBRL cron must be removed: {stripped!r}")


def test_cron_xbrl_chain_continues_after_fetch_fail(tmp_path: Path) -> None:
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    order = tmp_path / "order.txt"
    py = bindir / "python"
    py.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "script = next(a for a in sys.argv[1:] if a.endswith('.py'))\n"
        "order = Path(os.environ['CHAIN_ORDER'])\n"
        "prev = order.read_text(encoding='utf-8') if order.exists() else ''\n"
        "order.write_text(prev + script + '\\n', encoding='utf-8')\n"
        "print('out ' + script, flush=True)\n"
        "raise SystemExit(3 if 'tdnet_xbrl_r2.py' in script else 0)\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    env = os.environ.copy()
    env["CRON_APP_DIR"] = str(app)
    env["CRON_LOG_DIR"] = str(logs)
    env["CHAIN_ORDER"] = str(order)
    line = _tdnet_xbrl_cron_line()
    fields = line.split(None, 5)
    if len(fields) != 6:
        _fail(f"crontab command missing: {line!r}")
    cmd = fields[5]
    prefix = "cd /home/stock/stock-analyzer && "
    if not cmd.startswith(prefix):
        _fail(f"crontab must cd then chain: {cmd!r}")
    body = cmd[len(prefix) :]
    wrapper = str(ROOT / "deploy" / "cron-run.sh")
    if body.count("deploy/cron-run.sh") != len(_TDNET_XBRL_JOBS):
        _fail(f"chain must call cron-run.sh {len(_TDNET_XBRL_JOBS)} times: {body!r}")
    body = body.replace("deploy/cron-run.sh", shlex.quote(wrapper))
    full = f"cd {shlex.quote(str(app))} && {body}"
    proc = subprocess.run(
        ["bash", "-c", full],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(
            f"chain exit should be statements (0) after fetch fail, got {proc.returncode}: "
            f"{proc.stdout!r} {proc.stderr!r}"
        )
    if proc.stdout or proc.stderr:
        _fail(f"chain leaked stdio: {proc.stdout!r} {proc.stderr!r}")
    got = order.read_text(encoding="utf-8").splitlines()
    if got != list(_TDNET_XBRL_JOBS):
        _fail(f"chain order after fetch fail: {got!r}")
    fetch_log = (logs / "tdnet_xbrl_r2.log").read_text(encoding="utf-8")
    parse_log = (logs / "xbrl_parse.log").read_text(encoding="utf-8")
    norm_log = (logs / "xbrl_normalize.log").read_text(encoding="utf-8")
    if "fail cron jobs/xbrl/tdnet_xbrl_r2.py 2 exit=3" not in fetch_log:
        _fail(f"fetch fail cron missing: {fetch_log!r}")
    if "out jobs/xbrl/xbrl_parse.py" not in parse_log:
        _fail(f"parse log missing: {parse_log!r}")
    if "out jobs/xbrl/xbrl_normalize.py" not in norm_log:
        _fail(f"normalize log missing: {norm_log!r}")
    stmt_log = (logs / "xbrl_statements.log").read_text(encoding="utf-8")
    if "out jobs/xbrl/xbrl_statements.py" not in stmt_log or "fail cron" in stmt_log:
        _fail(f"statements log: {stmt_log!r}")
    if "fail cron" in parse_log or "fail cron" in norm_log:
        _fail(f"later stages must not page: {parse_log!r} {norm_log!r}")
    if "xbrl_parse.py" in fetch_log or "tdnet_xbrl_r2.py" in parse_log:
        _fail(f"job logs mixed: {fetch_log!r} {parse_log!r}")


def _write_cron_python(path: Path, marker: str) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"print({marker!r}, flush=True)\n"
        "script = next(a for a in sys.argv[1:] if a.endswith('.py'))\n"
        "print('out ' + script, flush=True)\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_cron_run_picks_venv_python(tmp_path: Path) -> None:
    wrapper = str(ROOT / "deploy" / "cron-run.sh")

    def run_job(app: Path, logs: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CRON_APP_DIR"] = str(app)
        env["CRON_LOG_DIR"] = str(logs)
        env["TZ"] = "UTC"
        return subprocess.run(
            ["bash", wrapper, "jobs/monthly/ir_monthly_html_r2.py"],
            cwd=app,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    py3_only = tmp_path / "py3-only"
    py3_logs = tmp_path / "py3-only-logs"
    py3_bin = py3_only / ".venv" / "bin"
    py3_bin.mkdir(parents=True)
    _write_cron_python(py3_bin / "python3", "interp-py3")
    py3 = run_job(py3_only, py3_logs)
    if py3.returncode != 0:
        _fail(f"python3-only cron-run exit {py3.returncode}: {py3.stdout!r} {py3.stderr!r}")
    if py3.stdout or py3.stderr:
        _fail(f"python3-only cron-run leaked stdio: {py3.stdout!r} {py3.stderr!r}")
    py3_log = (py3_logs / "ir_monthly_html_r2.log").read_text(encoding="utf-8")
    if "interp-py3" not in py3_log or "out jobs/monthly/ir_monthly_html_r2.py" not in py3_log:
        _fail(f"python3-only log missing job output: {py3_log!r}")
    if "fail cron" in py3_log:
        _fail(f"python3-only success log has fail cron: {py3_log!r}")

    both = tmp_path / "both"
    both_logs = tmp_path / "both-logs"
    both_bin = both / ".venv" / "bin"
    both_bin.mkdir(parents=True)
    _write_cron_python(both_bin / "python3", "interp-py3")
    _write_cron_python(both_bin / "python", "interp-py")
    both_run = run_job(both, both_logs)
    if both_run.returncode != 0:
        _fail(
            f"both-interpreters cron-run exit {both_run.returncode}: "
            f"{both_run.stdout!r} {both_run.stderr!r}"
        )
    both_log = (both_logs / "ir_monthly_html_r2.log").read_text(encoding="utf-8")
    if "interp-py3" not in both_log:
        _fail(f"cron-run must prefer python3: {both_log!r}")
    if re.search(r"interp-py(?:\s|$)", both_log):
        _fail(f"cron-run used python while python3 exists: {both_log!r}")

    missing = tmp_path / "missing-venv"
    missing_logs = tmp_path / "missing-venv-logs"
    (missing / ".venv" / "bin").mkdir(parents=True)
    missing_run = run_job(missing, missing_logs)
    if missing_run.returncode != 127:
        _fail(f"missing interpreter should exit 127, got {missing_run.returncode}")
    if missing_run.stdout or missing_run.stderr:
        _fail(
            "missing interpreter leaked stdio: "
            f"{missing_run.stdout!r} {missing_run.stderr!r}"
        )
    missing_log = (missing_logs / "ir_monthly_html_r2.log").read_text(encoding="utf-8")
    if "missing interpreter" not in missing_log:
        _fail(f"missing interpreter not logged: {missing_log!r}")
    if "fail cron jobs/monthly/ir_monthly_html_r2.py exit=127" not in missing_log:
        _fail(f"missing interpreter must page fail cron: {missing_log!r}")


def test_cron_run_nice_ionice_source() -> None:
    chk = subprocess.run(
        ["bash", "-n", str(ROOT / "deploy" / "cron-run.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n deploy/cron-run.sh failed: {chk.stderr or chk.stdout}")
    src = (ROOT / "deploy" / "cron-run.sh").read_text(encoding="utf-8")
    active = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    if "-c3" in active:
        _fail("cron-run.sh must not use ionice idle class -c3")
    if "-n 10" not in active:
        _fail("cron-run.sh must nice -n 10")
    if "-c2" not in active or "-n7" not in active:
        _fail("cron-run.sh must ionice -c2 -n7")
    for rel in (
        "deploy/stock-analyzer.service",
        "deploy/stock-analyzer-mcp.service",
        "deploy/uvicorn-start.sh",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in ("Nice=", "IOSchedulingClass=", "IOSchedulingPriority=", "ionice", "nice -n"):
            if needle in text:
                _fail(f"{rel} must not lower service priority ({needle!r})")


def _cron_run_env(app: Path, logs: Path, path: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CRON_APP_DIR"] = str(app)
    env["CRON_LOG_DIR"] = str(logs)
    env["TZ"] = "UTC"
    if path is not None:
        env["PATH"] = path
    return env


def _write_prio_python(path: Path, exit_code: int = 0) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import shutil\n"
        "import subprocess\n"
        "pid = os.getpid()\n"
        "print('ni=%d' % os.nice(0), flush=True)\n"
        "print('pid=%d' % pid, flush=True)\n"
        "ps = shutil.which('ps')\n"
        "if ps:\n"
        "    print('ps_ni=%d' % int(subprocess.check_output(\n"
        "        [ps, '-o', 'ni=', '-p', str(pid)], text=True).strip()), flush=True)\n"
        "    print(subprocess.check_output([ps, '-o', 'ni,cmd', '-p', str(pid)], text=True), flush=True)\n"
        "ionice = shutil.which('ionice')\n"
        "if ionice:\n"
        "    print(\n"
        "        'ioprio=' + subprocess.check_output(\n"
        "            [ionice, '-p', str(pid)], text=True\n"
        "        ).strip(),\n"
        "        flush=True,\n"
        "    )\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_exec_recorder(path: Path, tag: str, log: Path, real: str) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s %s\\n' {tag!r} \"$*\" >> {log.as_posix()!r}\n"
        f"exec {real!r} \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _path_without(names: tuple[str, ...], extra: Path | None = None) -> str:
    kept: list[str] = []
    if extra is not None:
        extra.mkdir(parents=True, exist_ok=True)
        for name in ("date", "mkdir", "touch", "ps", "bash"):
            dest = extra / name
            if not dest.exists():
                src = shutil.which(name)
                if src:
                    dest.symlink_to(src)
        kept.append(str(extra))
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        if any((Path(directory) / name).exists() for name in names):
            continue
        kept.append(directory)
    return os.pathsep.join(kept) if kept else str(extra or "/usr/bin")


def test_cron_run_nices_python(tmp_path: Path) -> None:
    wrapper = str(ROOT / "deploy" / "cron-run.sh")
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    _write_prio_python(bindir / "python3", exit_code=0)
    env = _cron_run_env(app, logs)
    proc = subprocess.run(
        ["bash", wrapper, "jobs/edinet/edinet_xbrl.py", "--limit", "300"],
        cwd=app,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"niced cron-run exit {proc.returncode}: {proc.stdout!r} {proc.stderr!r}")
    if proc.stdout or proc.stderr:
        _fail(f"niced cron-run leaked stdio: {proc.stdout!r} {proc.stderr!r}")
    text = (logs / "edinet_xbrl.log").read_text(encoding="utf-8")
    if "fail cron" in text:
        _fail(f"success log has fail cron: {text!r}")
    if "ni=10" not in text:
        _fail(f"python niceness is not 10: {text!r}")
    if "ps_ni=10" not in text:
        _fail(f"ps -o ni did not show 10: {text!r}")
    if shutil.which("ionice") and "ioprio=" in text:
        if "idle" in text or "ioprio=idle" in text:
            _fail(f"ionice idle class is forbidden: {text!r}")
        if "best-effort: prio 7" not in text and "best-effort: prio 07" not in text:
            _fail(f"ionice class is not best-effort 7: {text!r}")

    fail_app = tmp_path / "fail-app"
    fail_logs = tmp_path / "fail-logs"
    fail_bin = fail_app / ".venv" / "bin"
    fail_bin.mkdir(parents=True)
    _write_prio_python(fail_bin / "python3", exit_code=3)
    fail = subprocess.run(
        ["bash", wrapper, "jobs/market/run_batch.py", "--fail"],
        cwd=fail_app,
        env=_cron_run_env(fail_app, fail_logs),
        capture_output=True,
        text=True,
        check=False,
    )
    if fail.returncode != 3:
        _fail(f"niced fail exit {fail.returncode}: {fail.stdout!r} {fail.stderr!r}")
    fail_log = (fail_logs / "run_batch.log").read_text(encoding="utf-8")
    if "fail cron jobs/market/run_batch.py --fail exit=3" not in fail_log:
        _fail(f"niced fail must page: {fail_log!r}")
    if "ni=10" not in fail_log:
        _fail(f"failed job niceness is not 10: {fail_log!r}")


def test_cron_run_skips_missing_nice_ionice(tmp_path: Path) -> None:
    wrapper = str(ROOT / "deploy" / "cron-run.sh")
    rec = tmp_path / "rec.log"
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    nice_real = shutil.which("nice")
    ionice_real = shutil.which("ionice")
    if nice_real:
        _write_exec_recorder(fake / "nice", "nice-argv", rec, nice_real)
    if ionice_real:
        _write_exec_recorder(fake / "ionice", "ionice-argv", rec, ionice_real)

    app = tmp_path / "with-wrap"
    logs = tmp_path / "with-wrap-logs"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    _write_prio_python(bindir / "python3")
    wrapped = subprocess.run(
        ["bash", wrapper, "jobs/edinet/edinet_xbrl.py"],
        cwd=app,
        env=_cron_run_env(app, logs, path=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}"),
        capture_output=True,
        text=True,
        check=False,
    )
    if wrapped.returncode != 0:
        _fail(f"recorder cron-run exit {wrapped.returncode}: {wrapped.stdout!r} {wrapped.stderr!r}")
    recorded = rec.read_text(encoding="utf-8") if rec.exists() else ""
    if nice_real:
        if "nice-argv -n 10 " not in recorded:
            _fail(f"nice was not invoked as nice -n 10: {recorded!r}")
        if " -c3" in recorded.split("nice-argv", 1)[-1]:
            _fail(f"nice argv must not pass -c3: {recorded!r}")
    if ionice_real:
        if "ionice-argv -c2 -n7 " not in recorded:
            _fail(f"ionice was not invoked as ionice -c2 -n7: {recorded!r}")
        if "ionice-argv -c3" in recorded or " -c3 " in recorded:
            _fail(f"ionice -c3 is forbidden: {recorded!r}")

    bare = tmp_path / "bare-bin"
    bare.mkdir()
    hidden_path = _path_without(("nice", "ionice"), extra=bare)
    if shutil.which("nice", path=hidden_path) or shutil.which("ionice", path=hidden_path):
        _fail(f"hidden PATH still has nice/ionice: {hidden_path!r}")
    miss_app = tmp_path / "no-wrap"
    miss_logs = tmp_path / "no-wrap-logs"
    miss_bin = miss_app / ".venv" / "bin"
    miss_bin.mkdir(parents=True)
    _write_prio_python(miss_bin / "python3", exit_code=4)
    missing = subprocess.run(
        ["bash", wrapper, "jobs/xbrl/xbrl_parse.py"],
        cwd=miss_app,
        env=_cron_run_env(miss_app, miss_logs, path=hidden_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if missing.returncode != 4:
        _fail(
            f"missing nice/ionice should keep python exit, got {missing.returncode}: "
            f"{missing.stdout!r} {missing.stderr!r}"
        )
    if missing.stdout or missing.stderr:
        _fail(f"missing nice/ionice leaked stdio: {missing.stdout!r} {missing.stderr!r}")
    miss_log = (miss_logs / "xbrl_parse.log").read_text(encoding="utf-8")
    if "fail cron jobs/xbrl/xbrl_parse.py exit=4" not in miss_log:
        _fail(f"missing nice/ionice must still page: {miss_log!r}")
    if "ni=10" in miss_log:
        _fail(f"python was niced without nice in PATH: {miss_log!r}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_fixtures()
    test_conf_has_registry_regexes()
    test_no_committed_webhook_url()
    test_secret_contract()
    with TemporaryDirectory() as d:
        test_stub_sudo_channel_env_after_sudoers_refresh(Path(d))
    test_slack_payload_jst_danger()
    with TemporaryDirectory() as d:
        test_cron_run_splits_and_stamps(Path(d))
    test_crontab_chains_tdnet_xbrl()
    with TemporaryDirectory() as d:
        test_cron_xbrl_chain_continues_after_fetch_fail(Path(d))
    with TemporaryDirectory() as d:
        test_cron_run_picks_venv_python(Path(d))
    test_cron_run_nice_ionice_source()
    with TemporaryDirectory() as d:
        test_cron_run_nices_python(Path(d))
    with TemporaryDirectory() as d:
        test_cron_run_skips_missing_nice_ionice(Path(d))
    print("ok: alert patterns")


if __name__ == "__main__":
    main()

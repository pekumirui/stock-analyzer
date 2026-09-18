"""AlertPattern fixtures: page / do not page. fluent-bit.conf must list each regex."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))

from alert_patterns import (  # noqa: E402
    ALERT_PATTERNS,
    GREP_FIELD,
    SECRET_LINE_MARKERS,
    SLACK_COLOR_DANGER,
    format_jst,
    should_page,
    slack_payload,
    slack_payload_json,
)

FORBIDDEN_WEBHOOK_PREFIX = "hooks.slack.com" + "/services/"

PAGE: tuple[tuple[str, str], ...] = (
    ("app", "ERROR: Exception in ASGI application"),
    ("app", 'INFO:     127.0.0.1:43721 - "GET /api/screener HTTP/1.1" 500 Internal Server Error'),
    ("app", "ERROR: Error loading ASGI app. Could not import module \"app.main\"."),
    ("app", "SchemaMismatch: stocks missing column"),
    ("batch", "fail POST 7203 status=500 boom"),
    ("batch", "fail cron scripts/run_batch.py exit=1"),
    ("batch", "fail cron cwd exit=1"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=3"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=10"),
    ("batch", "parse processed=3 errors=2 skipped=0"),
    ("batch", "parse processed=3 errors=10 skipped=0"),
    ("batch", "Traceback (most recent call last):"),
    ("batch", "リトライ上限に達しました: /v2/equities/bars/daily"),
    ("batch", "JQUANTS_API_KEY が未設定です。"),
    ("batch", "warn list truncated for 2026-08-05: next_flag=1"),
    ("batch", "error missing-zip doc_id=S100ABC path=xbrl/7203/foo.zip"),
    ("batch", "failed doc_id=S100ABC code=72030 err=boom"),
    ("batch", "normalize failed doc_id=S100ABC code=72030 err=boom"),
    ("nginx", "2026/08/05 12:00:00 [error] 12#12: *1 connect() failed (111: Connection refused)"),
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
    ("batch", "2026-08-05: 銘柄10 / 株価0 / TOPIX0 / 決算0"),
    ("batch", "完了"),
    ("batch", "normalize missing-required doc_id=S100ABC code=72030 items=NetSales"),
    ("batch", "saved xbrl/3032/foo.zip (12 bytes) unknown-stock"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=9 failed=0"),
    ("batch", "parse processed=3 errors=0 skipped=1"),
    ("app", 'INFO:     127.0.0.1:43721 - "GET / HTTP/1.1" 200 OK'),
    ("app", 'INFO:     127.0.0.1:43721 - "GET /api/v1/tables/7203/2024 HTTP/1.1" 404 Not Found'),
    ("app", "INFO:     Started server process [12345]"),
    ("app", "ERROR: Exception in ASGI application see hooks.slack.com"),
    ("app", "SchemaMismatch ?token=abc"),
    ("batch", "JQUANTS_API_KEY=not-a-real-key Traceback (most recent call last):"),
    ("mcp", "ERROR MCP_TOKEN=not-a-real-token"),
    ("batch", "failed=3 SLACK_WEBHOOK_URL=not-a-real-webhook"),
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
    if "Webhook ${SLACK_WEBHOOK_URL}" in conf:
        _fail("fluent-bit.conf must not use the slack output plugin")
    if "Name         http" not in conf:
        _fail("fluent-bit.conf must POST via http")
    if "Host         hooks.slack.com" not in conf:
        _fail("fluent-bit.conf must POST to hooks.slack.com")
    if "URI          ${SLACK_WEBHOOK_URI}" not in conf:
        _fail("fluent-bit.conf must use URI ${SLACK_WEBHOOK_URI}")
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
        if path_val.endswith(".1") or path_val.endswith(".gz") or "*" in path_val:
            _fail(f"fluent-bit.conf must tail live files only: {path_val}")
    if "/home/stock/stock-analyzer/.env" in conf:
        _fail("fluent-bit.conf must not EnvironmentFile the app .env")


def test_no_committed_webhook_url() -> None:
    roots = (
        ROOT / "deploy",
        ROOT / "scripts",
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


def test_secret_contract() -> None:
    cicd = (ROOT / ".github" / "workflows" / "cicd.yml").read_text(encoding="utf-8")
    if "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}" not in cicd:
        _fail("cicd.yml deploy env must include SLACK_WEBHOOK_URL")
    if '"SLACK_WEBHOOK_URL"' not in cicd:
        _fail("cicd.yml export keys must include SLACK_WEBHOOK_URL")
    update = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    if "upsert_env SLACK_WEBHOOK_URL" in update:
        _fail("update.sh must not upsert SLACK_WEBHOOK_URL into the app .env")
    if "SLACK_WEBHOOK_URL" not in update or "--preserve-env=" not in update:
        _fail("update.sh must pass SLACK_WEBHOOK_URL via sudo --preserve-env")
    if "SLACK_WEBHOOK_URL" not in update.split("--preserve-env=", 1)[-1].split("\n", 1)[0]:
        _fail("update.sh --preserve-env must list SLACK_WEBHOOK_URL")
    sudoers = (ROOT / "deploy" / "sudoers").read_text(encoding="utf-8")
    if "SLACK_WEBHOOK_URL" not in sudoers or "env_keep" not in sudoers:
        _fail("deploy/sudoers must env_keep SLACK_WEBHOOK_URL")
    provision = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    if "/etc/fluent-bit/slack.env" not in provision:
        _fail("provision.sh must write /etc/fluent-bit/slack.env")
    if "SLACK_WEBHOOK_URI=%s" not in provision:
        _fail("provision.sh must write SLACK_WEBHOOK_URI into slack.env")
    if "fluent-bit_slack.lua" not in provision or "/etc/fluent-bit/slack.lua" not in provision:
        _fail("provision.sh must install fluent-bit_slack.lua")
    if "EnvironmentFile=-/etc/fluent-bit/slack.env" not in provision:
        _fail("provision.sh must install the fluent-bit slack.env drop-in")
    if "SLACK_WEBHOOK_URI=/services/" not in provision:
        _fail("provision.sh must start fluent-bit only when SLACK_WEBHOOK_URI is set")
    dropin = provision.split("fluent-bit.service.d", 1)[-1]
    if "stock-analyzer/.env" in dropin.split("echo \"provision ok", 1)[0]:
        _fail("fluent-bit drop-in must not EnvironmentFile the app .env")
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    for line in crontab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ".venv/bin/python" in stripped and "cron-run.sh" not in stripped:
            _fail(f"crontab python job must use cron-run.sh: {stripped}")
    wrapper = ROOT / "deploy" / "cron-run.sh"
    if not wrapper.is_file() or not wrapper.stat().st_mode & 0o111:
        _fail("deploy/cron-run.sh must be executable")


def test_slack_payload_jst_danger() -> None:
    # 2026-09-18 00:02:41 UTC == 09:02:41 JST
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
        _fail("slack json missing danger/JST")
    lua = (ROOT / "deploy" / "fluent-bit_slack.lua").read_text(encoding="utf-8")
    if 'color":"danger"' not in lua or "JST" not in lua or "format_slack" not in lua:
        _fail("fluent-bit_slack.lua must emit danger + JST")
    if '"fallback"' in lua.split("local json", 1)[-1]:
        _fail("lua attachment must not mix fallback with blocks")


def main() -> None:
    test_fixtures()
    test_conf_has_registry_regexes()
    test_no_committed_webhook_url()
    test_secret_contract()
    test_slack_payload_jst_danger()
    print("ok: alert patterns")


if __name__ == "__main__":
    main()

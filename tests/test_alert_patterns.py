"""AlertPattern fixtures: page / do not page. fluent-bit.conf must list each regex."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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
    ("batch", "2026-09-30 18:00:01 fail cron jobs/run_batch.py exit=1"),
    ("batch", "2026-09-30 18:00:01 fail cron cwd exit=1"),
    ("batch", "2026-09-30 18:00:01 tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=3"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=3"),
    ("batch", "tdnet_monthly_r2 done listed=10 saved=1 skipped=0 failed=10"),
    ("batch", "parse processed=3 errors=2 skipped=0"),
    ("batch", "parse processed=3 errors=10 skipped=0"),
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
    ("batch", "fail cron jobs/run_batch.py exit=1"),
    ("batch", "fail POST 7203 status=500 boom"),
    ("batch", "fail issuer 1878 fetch: HTTP Error 403: Forbidden"),
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


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_fixtures() -> None:
    for source, line in PAGE:
        if not should_page(source, line):
            _fail(f"should page {source}: {line!r}")
    for source, line in NOT_PAGE:
        if should_page(source, line):
            _fail(f"must not page {source}: {line!r}")


def _section_body(conf: str, header: str, needle: str) -> str:
    for part in conf.split(header)[1:]:
        body = part.split("[", 1)[0]
        if needle in body:
            return body
    return ""


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
    if "call    source_from_file" not in conf:
        _fail("fluent-bit.conf must set batch source from the log file name")
    lua = (ROOT / "deploy" / "fluent-bit_slack.lua").read_text(encoding="utf-8")
    if "function source_from_file" not in lua or 'record["file"]' not in lua:
        _fail("fluent-bit_slack.lua must set source from the log file name")


def test_no_committed_webhook_url() -> None:
    roots = (
        ROOT / "deploy",
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
        ["bash", wrapper, "jobs/tdnet_monthly_r2.py", "1"],
        cwd=app,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        ["bash", wrapper, "jobs/tdnet_xbrl_r2.py", "1", "--fail"],
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
        (monthly, "out jobs/tdnet_monthly_r2.py"),
        (monthly, "err jobs/tdnet_monthly_r2.py"),
        (xbrl, "out jobs/tdnet_xbrl_r2.py"),
        (xbrl, "err jobs/tdnet_xbrl_r2.py"),
        (xbrl, "fail cron jobs/tdnet_xbrl_r2.py 1 --fail exit=3"),
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
        ["bash", wrapper, "jobs/run_batch.py"],
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


def main() -> None:
    from tempfile import TemporaryDirectory

    test_fixtures()
    test_conf_has_registry_regexes()
    test_no_committed_webhook_url()
    test_secret_contract()
    test_slack_payload_jst_danger()
    with TemporaryDirectory() as d:
        test_cron_run_splits_and_stamps(Path(d))
    print("ok: alert patterns")


if __name__ == "__main__":
    main()

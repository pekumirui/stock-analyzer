"""MCP サーバの認証・JSON-RPC・入力バリデーション。systemd は不要。"""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")
os.environ["MCP_APP_DIR"] = str(ROOT)

from starlette.testclient import TestClient

import log_mcp as m


def _rpc(client: TestClient, method: str, params: dict | None = None, token: str = "test-token-for-ci-only", req_id: int = 1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


def test_call_tool_redacts_query_token() -> None:
    orig = m.TOOLS["fs_list"]
    m.TOOLS["fs_list"] = (
        orig[0],
        orig[1],
        lambda _a: "GET /mcp?token=dummy-consent-secret HTTP/1.0",
    )
    try:
        text, err = m.call_tool("fs_list", {})
        if err:
            raise SystemExit(f"call_tool err: {text}")
        if "dummy-consent-secret" in text:
            raise SystemExit(f"query token leaked: {text!r}")
        if "token=<redacted>" not in text:
            raise SystemExit(f"expected url redaction: {text!r}")
    finally:
        m.TOOLS["fs_list"] = orig


def test_main_disables_access_log() -> None:
    from unittest.mock import patch

    with patch.object(m, "configured_token", return_value="x"), patch("uvicorn.run") as run:
        m.main()
    if not run.called:
        raise SystemExit("uvicorn.run not called")
    if run.call_args.kwargs.get("access_log") is not False:
        raise SystemExit(f"access_log: {run.call_args.kwargs}")


def test_redact() -> None:
    out = m.redact("SLACK_WEBHOOK_URL=https://example.invalid/hook")
    if out != "SLACK_WEBHOOK_URL=<redacted>":
        raise SystemExit(f"redact slack: {out!r}")
    if "example.invalid" in out:
        raise SystemExit(f"redact leaked webhook host: {out!r}")
    out = m.redact("JQUANTS_API_KEY=secret-value")
    if out != "JQUANTS_API_KEY=<redacted>":
        raise SystemExit(f"redact jquants: {out!r}")
    if "SLACK_WEBHOOK_URL" not in m.SECRET_RE.pattern:
        raise SystemExit("SECRET_RE must include SLACK_WEBHOOK_URL")


def test_since_and_grep() -> None:
    assert m.parse_since("1h") == "1 hours ago"
    assert m.parse_since("30m") == "30 minutes ago"
    assert m.parse_since("today") == "today"
    assert m.parse_since("2026-08-24") == "2026-08-24"
    try:
        m.parse_since("--user=root")
    except ValueError:
        pass
    else:
        raise SystemExit("since must reject option-like strings")
    try:
        m.parse_grep("-n; rm -rf /")
    except ValueError:
        pass
    else:
        raise SystemExit("grep must reject dash-prefix")
    try:
        m.parse_grep("a" * 200)
    except ValueError:
        pass
    else:
        raise SystemExit("grep must cap length")


def test_tail_grep(tmp_path: Path) -> None:
    p = tmp_path / "batch.log"
    p.write_text("ok line\nERROR boom\nJQUANTS_API_KEY=secret-value\n", encoding="utf-8")
    out = m.tail_grep(p, 10, "ERROR")
    if "ERROR boom" not in out or "ok line" in out:
        raise SystemExit(f"tail_grep filter failed: {out!r}")


def test_pick_log(tmp_path: Path) -> None:
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    app.mkdir(parents=True)
    logs.mkdir(parents=True)
    (app / "batch.log").write_text("old\n", encoding="utf-8")
    prev_app = os.environ.get("MCP_APP_DIR")
    prev_log = os.environ.get("MCP_LOG_DIR")
    os.environ["MCP_APP_DIR"] = str(app)
    os.environ.pop("MCP_LOG_DIR", None)
    try:
        if m.pick_log("batch.log") != app / "batch.log":
            raise SystemExit(f"fallback app_dir failed: {m.pick_log('batch.log')}")
        (logs / "batch.log").write_text("new\n", encoding="utf-8")
        os.environ["MCP_LOG_DIR"] = str(logs)
        if m.pick_log("batch.log") != logs / "batch.log":
            raise SystemExit(f"prefer log_dir failed: {m.pick_log('batch.log')}")
    finally:
        if prev_app is None:
            os.environ.pop("MCP_APP_DIR", None)
        else:
            os.environ["MCP_APP_DIR"] = prev_app
        if prev_log is None:
            os.environ.pop("MCP_LOG_DIR", None)
        else:
            os.environ["MCP_LOG_DIR"] = prev_log


def test_batch_logs_job_file(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / "batch.log").write_text("old batch\n", encoding="utf-8")
    (logs / "tdnet_monthly_r2.log").write_text("monthly line\nfail cron hidden\n", encoding="utf-8")
    (logs / "tdnet_xbrl_r2.log").write_text("xbrl line\n", encoding="utf-8")
    (logs / "stock-analyzer.log").write_text("app secret\n", encoding="utf-8")
    (logs / "mcp.log").write_text("mcp secret\n", encoding="utf-8")
    prev_app = os.environ.get("MCP_APP_DIR")
    prev_log = os.environ.get("MCP_LOG_DIR")
    os.environ["MCP_APP_DIR"] = str(tmp_path / "app")
    os.environ["MCP_LOG_DIR"] = str(logs)
    try:
        text = m.tool_batch_logs({})
        if "monthly line" not in text or "xbrl line" not in text or "old batch" not in text:
            raise SystemExit(f"batch_logs should read job files: {text!r}")
        if "app secret" in text or "mcp secret" in text:
            raise SystemExit(f"batch_logs leaked app/mcp: {text!r}")
        one = m.tool_batch_logs({"job": "tdnet_monthly_r2"})
        if one != "monthly line\nfail cron hidden":
            raise SystemExit(f"job filter: {one!r}")
        if "xbrl line" in one:
            raise SystemExit(f"job filter leaked another file: {one!r}")
        for bad in ("../.env", "stock-analyzer", "mcp", "tdnet/monthly"):
            try:
                m.tool_batch_logs({"job": bad})
            except ValueError:
                continue
            raise SystemExit(f"job must reject {bad!r}")
    finally:
        if prev_app is None:
            os.environ.pop("MCP_APP_DIR", None)
        else:
            os.environ["MCP_APP_DIR"] = prev_app
        if prev_log is None:
            os.environ.pop("MCP_LOG_DIR", None)
        else:
            os.environ["MCP_LOG_DIR"] = prev_log


def test_batch_logs_migrate_r2_share(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    line = (
        "2026-10-19 23:27:08 mismatch path=xbrl/manifest/2026-10-19.jsonl "
        "src_size=10 dest_size=10 src_sha=aaaaaaaaaaaa dest_sha=bbbbbbbbbbbb"
    )
    (logs / "migrate_r2_share.log").write_text(line + "\n", encoding="utf-8")
    prev_app, prev_log = _with_log_dir(logs)
    try:
        if "migrate_r2_share" not in m.BATCH_JOBS:
            raise SystemExit("migrate_r2_share must be allowed for batch_logs")
        if "migrate_r2_share" in m.crontab_job_names():
            raise SystemExit("migrate_r2_share must stay off crontab")
        text = m.tool_batch_logs({"job": "migrate_r2_share"})
        if "xbrl/manifest/2026-10-19.jsonl" not in text:
            raise SystemExit(f"migrate_r2_share log: {text!r}")
        windowed, err = m.call_tool(
            "batch_logs",
            {"job": "migrate_r2_share", "since": "2026-10-19", "until": "2026-10-20"},
        )
        if err or "xbrl/manifest/2026-10-19.jsonl" not in windowed:
            raise SystemExit(f"migrate_r2_share since=today: {windowed!r} err={err}")
    finally:
        _restore_log_dir(prev_app, prev_log)


def _with_log_dir(tmp_path: Path) -> tuple[str | None, str | None]:
    prev_app = os.environ.get("MCP_APP_DIR")
    prev_log = os.environ.get("MCP_LOG_DIR")
    os.environ["MCP_APP_DIR"] = str(tmp_path / "app")
    os.environ["MCP_LOG_DIR"] = str(tmp_path)
    return prev_app, prev_log


def _restore_log_dir(prev_app: str | None, prev_log: str | None) -> None:
    if prev_app is None:
        os.environ.pop("MCP_APP_DIR", None)
    else:
        os.environ["MCP_APP_DIR"] = prev_app
    if prev_log is None:
        os.environ.pop("MCP_LOG_DIR", None)
    else:
        os.environ["MCP_LOG_DIR"] = prev_log


def test_batch_logs_window(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    yesterday_gz = (
        "2026-09-28 09:00:00 fail too old\n"
        "2026-09-29 08:00:00 fail early\n"
        "2026-09-29 08:30:00 fail JQUANTS_API_KEY=secret-value\n"
        "2026-09-29 09:00:00 ok kept\n"
    )
    (logs / "xbrl_parse.log.2.gz").write_bytes(gzip.compress(yesterday_gz.encode("utf-8")))
    (logs / "xbrl_parse.log.1").write_text(
        "2026-09-29 00:00:00 fail start\n"
        "2026-09-29 18:00:01 fail cron jobs/xbrl/xbrl_parse.py exit=1\n"
        "2026-09-30 00:00:00 fail boundary\n",
        encoding="utf-8",
    )
    (logs / "xbrl_parse.log").write_text(
        "2026-09-29 23:30:00 fail late\n"
        "2026-09-30 10:00:02 fail today\n"
        "2026-09-30 14:30:00 fail recent\n",
        encoding="utf-8",
    )
    (logs / "tdnet_xbrl_r2.log").write_text(
        "2026-09-29 12:00:00 fail other\n",
        encoding="utf-8",
    )
    (logs / "not_a_job.log").write_text("2026-09-29 12:00:00 fail stranger\n", encoding="utf-8")
    prev = _with_log_dir(logs)
    orig_now = m._jst_now

    def _frozen_now() -> datetime:
        return datetime(2026, 9, 30, 15, 0, 0, tzinfo=m.JST).replace(tzinfo=None)

    m._jst_now = _frozen_now
    try:
        desc = m.TOOLS["batch_logs"][0]
        props = m.TOOLS["batch_logs"][1]["properties"]
        for name in ("since", "until", "job", "lines", "grep"):
            if name not in props:
                raise SystemExit(f"batch_logs schema missing {name}")
        for job_name in sorted(m.BATCH_JOBS):
            if job_name not in desc:
                raise SystemExit(f"batch_logs description missing {job_name}")
        plain = m.tool_batch_logs({"job": "xbrl_parse", "grep": "fail early"})
        if "fail early" in plain:
            raise SystemExit(f"live tail must not read rotated logs: {plain!r}")
        text, is_error = m.call_tool(
            "batch_logs",
            {"job": "xbrl_parse", "since": "yesterday", "until": "today", "grep": "fail"},
        )
        if is_error:
            raise SystemExit(f"yesterday xbrl_parse: {text!r}")
        expect = [
            "2026-09-29 00:00:00 fail start",
            "2026-09-29 08:00:00 fail early",
            "2026-09-29 08:30:00 fail JQUANTS_API_KEY=<redacted>",
            "2026-09-29 18:00:01 fail cron jobs/xbrl/xbrl_parse.py exit=1",
            "2026-09-29 23:30:00 fail late",
        ]
        if text != "\n".join(expect):
            raise SystemExit(f"yesterday window: {text!r}")
        if "secret-value" in text or "fail other" in text or "fail today" in text or "ok kept" in text:
            raise SystemExit(f"window leaked: {text!r}")
        if "fail too old" in text or "fail boundary" in text or "fail stranger" in text:
            raise SystemExit(f"window leaked outside: {text!r}")
        dated, dated_err = m.call_tool(
            "batch_logs",
            {"job": "xbrl_parse", "since": "2026-09-29", "until": "2026-09-30", "grep": "fail"},
        )
        if dated_err or dated != text:
            raise SystemExit(f"date window != yesterday: {dated!r}")
        recent, recent_err = m.call_tool("batch_logs", {"job": "xbrl_parse", "since": "1h", "grep": "fail"})
        if recent_err or recent != "2026-09-30 14:30:00 fail recent":
            raise SystemExit(f"since 1h: {recent!r}")
        older, older_err = m.call_tool("batch_logs", {"job": "xbrl_parse", "until": "1h", "grep": "fail today"})
        if older_err or older != "2026-09-30 10:00:02 fail today":
            raise SystemExit(f"until 1h: {older!r}")
        short, short_err = m.call_tool(
            "batch_logs",
            {"job": "xbrl_parse", "since": "yesterday", "until": "today", "grep": "fail", "lines": 1},
        )
        if short_err or short != "(truncated; narrow since/until)\n2026-09-29 23:30:00 fail late":
            raise SystemExit(f"truncated: {short!r}")
        for bad in ("../.env", "xbrl_parse.log", "/var/log/app/xbrl_parse", "batch", "not_a_job"):
            rejected, bad_err = m.call_tool("batch_logs", {"job": bad})
            if not bad_err:
                raise SystemExit(f"job must reject {bad!r}: {rejected!r}")
        empty, empty_err = m.call_tool(
            "batch_logs",
            {"job": "xbrl_parse", "since": "yesterday", "until": "yesterday"},
        )
        if not empty_err or "until must be after since" not in empty:
            raise SystemExit(f"empty window: {empty!r} err={empty_err}")
        bad_since, since_err = m.call_tool("batch_logs", {"job": "xbrl_parse", "since": "../etc"})
        if not since_err:
            raise SystemExit(f"since must reject path: {bad_since!r}")
    finally:
        m._jst_now = orig_now
        _restore_log_dir(*prev)


def test_http_auth_and_tools() -> None:
    token = "test-token-for-ci-only"
    client = TestClient(m.create_app(token))

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    if r.status_code != 401:
        raise SystemExit(f"missing token should 401, got {r.status_code}")

    r = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    if r.status_code != 200:
        raise SystemExit(f"initialize: {r.status_code} {r.text}")
    result = r.json()["result"]
    if result["serverInfo"]["name"] != "stock-analyzer-logs":
        raise SystemExit(f"bad initialize: {result}")

    r = _rpc(client, "tools/list", req_id=2)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    expect = {
        "fs_list",
        "fs_read",
        "fs_grep",
        "service_status",
        "app_logs",
        "batch_logs",
        "nginx_logs",
        "db_stats",
        "db_lookup",
        "db_query",
        "db_schema",
        "xbrl_compare",
        "xbrl_diff_summary",
        "financial_statements",
        "metrics_query",
        "fx_rate",
        "fx_rates",
        "monthly_list",
        "monthly_get",
        "monthly_add_element",
        "monthly_put_values",
        "monthly_delete_element",
        "monthly_get_doc",
        "monthly_put_doc",
        "monthly_set_industry",
        "monthly_ingest",
        "earnings_day",
        "earnings_detail",
        "earnings_docs",
        "earnings_doc_read",
        "stock_overview",
        "market_context",
    }
    if names != expect:
        raise SystemExit(f"tools {names} != {expect}")

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        Path(d, "batch.log").write_text("hello batch\nERROR failed ingest\n", encoding="utf-8")
        r = _rpc(client, "tools/call", {"name": "batch_logs", "arguments": {"grep": "ERROR", "lines": 50}}, req_id=3)
        text = r.json()["result"]["content"][0]["text"]
        if "ERROR failed ingest" not in text or "hello batch" in text:
            raise SystemExit(f"batch_logs: {text!r}")
        if r.json()["result"]["isError"]:
            raise SystemExit("batch_logs should not be isError")

    r = _rpc(client, "tools/call", {"name": "app_logs", "arguments": {"since": "; reboot"}}, req_id=4)
    if not r.json()["result"]["isError"]:
        raise SystemExit("bad since should set isError")

    r = client.get("/mcp", params={"token": token})
    if r.status_code != 401:
        raise SystemExit(f"query token must 401, got {r.status_code} {r.text}")

    r = client.get("/mcp", headers={"X-MCP-Token": token})
    if r.status_code != 200 or "stock-analyzer-logs" not in r.text:
        raise SystemExit(f"GET /mcp X-MCP-Token: {r.status_code} {r.text}")

    r = _rpc(client, "initialize", token="wrong-token")
    if r.status_code != 401:
        raise SystemExit("wrong token should 401")

    _rpc(client, "tools/call", {"name": "batch_logs", "arguments": {}}, req_id=5)
    sse = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "ping"},
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
    )
    if "text/event-stream" not in sse.headers.get("content-type", ""):
        raise SystemExit(f"SSE content-type: {sse.headers.get('content-type')}")
    if "event: message" not in sse.text:
        raise SystemExit(f"SSE body: {sse.text!r}")


def _pkce() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def test_oauth() -> None:
    from tempfile import TemporaryDirectory

    token = "test-token-for-ci-only"
    prev = os.environ.get("MCP_APP_DIR")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        try:
            _test_oauth_inner(token)
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev


def _test_oauth_inner(token: str) -> None:
    client = TestClient(m.create_app(token))

    r = client.get("/.well-known/oauth-authorization-server")
    if r.status_code != 200:
        raise SystemExit(f"as metadata: {r.status_code} {r.text}")
    meta = r.json()
    if meta["token_endpoint_auth_methods_supported"] != ["none"]:
        raise SystemExit(f"auth methods: {meta}")
    if "S256" not in meta["code_challenge_methods_supported"]:
        raise SystemExit("S256 missing")
    if not meta["authorization_endpoint"].endswith("/oauth/authorize"):
        raise SystemExit(f"authorize: {meta['authorization_endpoint']}")

    r = client.get("/.well-known/openid-configuration")
    if r.status_code != 200:
        raise SystemExit(f"openid-configuration: {r.status_code} {r.text}")
    oidc = r.json()
    if oidc["authorization_endpoint"] != meta["authorization_endpoint"]:
        raise SystemExit(f"openid authorize: {oidc['authorization_endpoint']}")

    r = client.get("/.well-known/openid-configuration/mcp")
    if r.status_code != 200 or r.json()["authorization_endpoint"] != meta["authorization_endpoint"]:
        raise SystemExit(f"openid-configuration/mcp: {r.status_code} {r.text}")

    r = client.get("/.well-known/oauth-protected-resource")
    if r.status_code != 200 or "/mcp" not in r.json().get("resource", ""):
        raise SystemExit(f"pr metadata: {r.status_code} {r.text}")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    www = r.headers.get("www-authenticate", "")
    if r.status_code != 401 or "resource_metadata=" not in www:
        raise SystemExit(f"401 www: {r.status_code} {www}")

    verifier, challenge = _pkce()
    q = {
        "response_type": "code",
        "client_id": "stock-analyzer",
        "redirect_uri": "https://grok.com/oauth/callback",
        "state": "st1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    r = client.get("/oauth/authorize", params=q)
    if r.status_code != 200 or "MCP_TOKEN" not in r.text:
        raise SystemExit(f"authorize get: {r.status_code} {r.text[:200]}")

    r = client.post(
        "/oauth/authorize",
        data={**q, "redirect_uri": "https://evil.example/cb", "token": token},
        follow_redirects=False,
    )
    if r.status_code != 400:
        raise SystemExit(f"evil redirect should 400, got {r.status_code}")

    r = client.post("/oauth/authorize", data={**q, "token": "wrong"}, follow_redirects=False)
    if r.status_code != 401:
        raise SystemExit(f"wrong token should 401, got {r.status_code}")

    r = client.post("/oauth/authorize", data={**q, "token": token}, follow_redirects=False)
    if r.status_code != 302:
        raise SystemExit(f"authorize post: {r.status_code} {r.text[:200]}")
    loc = r.headers.get("location") or ""
    if "code=" not in loc or "state=st1" not in loc:
        raise SystemExit(f"redirect: {loc}")
    from urllib.parse import parse_qs, urlparse

    code = parse_qs(urlparse(loc).query)["code"][0]

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": q["redirect_uri"],
            "client_id": "stock-analyzer",
            "code_verifier": verifier,
        },
    )
    if r.status_code != 200:
        raise SystemExit(f"token: {r.status_code} {r.text}")
    body = r.json()
    access = body["access_token"]
    refresh = body["refresh_token"]
    if body["token_type"] != "Bearer":
        raise SystemExit(body)

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": q["redirect_uri"],
            "client_id": "stock-analyzer",
            "code_verifier": verifier,
        },
    )
    if r.status_code != 400:
        raise SystemExit(f"replayed code should 400, got {r.status_code} {r.text}")

    r = _rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        token=access,
    )
    if r.status_code != 200:
        raise SystemExit(f"mcp with access token: {r.status_code} {r.text}")

    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh})
    if r.status_code != 200 or "access_token" not in r.json():
        raise SystemExit(f"refresh: {r.status_code} {r.text}")

    r = client.post(
        "/oauth/register",
        json={"client_name": "Grok", "redirect_uris": ["https://grok.com/oauth/callback"], "token_endpoint_auth_method": "none"},
    )
    if r.status_code != 201 or not r.json().get("client_id"):
        raise SystemExit(f"dcr: {r.status_code} {r.text}")

    r = client.post(
        "/oauth/register",
        json={"client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "token_endpoint_auth_method": "none"},
    )
    if r.status_code != 201 or not r.json().get("client_id"):
        raise SystemExit(f"dcr claude.ai: {r.status_code} {r.text}")
    r = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://claude.com/api/mcp/auth_callback"]},
    )
    if r.status_code != 201 or not r.json().get("client_id"):
        raise SystemExit(f"dcr claude.com: {r.status_code} {r.text}")
    r = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://evil.example/cb"]},
    )
    if r.status_code != 400 or r.json().get("error") != "invalid_redirect_uri":
        raise SystemExit(f"dcr evil: {r.status_code} {r.text}")

    if m.redirect_ok("https://evil.example/cb"):
        raise SystemExit("redirect_ok allowed evil host")
    if not m.redirect_ok("https://grok.com/oauth/callback"):
        raise SystemExit("redirect_ok rejected grok.com")
    if not m.redirect_ok("https://www.cursor.com/agents/mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected www.cursor.com")
    if not m.redirect_ok("https://cursor.com/agents/mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected cursor.com")
    if not m.redirect_ok("cursor://anysphere.cursor-mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected cursor:// callback")
    if m.redirect_ok("https://cursor.evil.example/cb"):
        raise SystemExit("redirect_ok allowed cursor lookalike host")
    if not m.redirect_ok("https://claude.ai/api/mcp/auth_callback"):
        raise SystemExit("redirect_ok rejected claude.ai")
    if not m.redirect_ok("https://claude.com/api/mcp/auth_callback"):
        raise SystemExit("redirect_ok rejected claude.com")
    if not m.redirect_ok("https://www.claude.ai/api/mcp/auth_callback"):
        raise SystemExit("redirect_ok rejected www.claude.ai")
    if m.redirect_ok("https://claude.ai.evil.example/cb"):
        raise SystemExit("redirect_ok allowed claude lookalike host")
    if m.redirect_ok("https://notclaude.ai/cb"):
        raise SystemExit("redirect_ok allowed notclaude.ai")


def test_db_tools() -> None:
    from tempfile import TemporaryDirectory

    token = "test-token-for-ci-only"
    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        import sqlite3

        conn = sqlite3.connect(dbp)
        conn.executescript(
            """
            CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT, sector33 TEXT, scale_category TEXT);
            CREATE TABLE daily_prices (code TEXT, date TEXT, close REAL, adj_close REAL, adj_volume REAL, PRIMARY KEY(code, date));
            CREATE TABLE topix (date TEXT PRIMARY KEY, close REAL);
            CREATE TABLE statements (disclosure_no TEXT PRIMARY KEY, code TEXT, disclosed_date TEXT, type_of_period TEXT, type_of_document TEXT);
            CREATE TABLE indicators (code TEXT, date TEXT, stage INTEGER, rs_rank INTEGER, ma50 REAL, ma150 REAL, ma200 REAL, PRIMARY KEY(code, date));
            INSERT INTO stocks VALUES ('07203','デモ','', '', '');
            INSERT INTO stocks VALUES ('72030','トヨタ','プライム','輸送用機器','TOPIX Large70');
            INSERT INTO daily_prices VALUES ('72030','2026-08-20',3000,3000,1000);
            INSERT INTO daily_prices VALUES ('72030','2026-08-21',3010,3010,1100);
            INSERT INTO topix VALUES ('2026-08-21', 2800);
            INSERT INTO statements VALUES ('1','72030','2026-05-08','FY','FinancialStatements');
            INSERT INTO indicators VALUES ('72030','2026-08-21',2,85,2900,2800,2700);
            CREATE TABLE xbrl_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                period_end TEXT
            );
            INSERT INTO xbrl_segments (code, period_end) VALUES ('72030', '2026-03-31');
            INSERT INTO xbrl_segments (code, period_end) VALUES ('72030', '2025-03-31');
            """
        )
        conn.commit()
        conn.close()
        try:
            if m.parse_code("7203") != "72030":
                raise SystemExit("parse_code 7203")
            if m.parse_code("9279") != "92790":
                raise SystemExit("parse_code 9279")
            if m.parse_code("262A") != "262A0":
                raise SystemExit("parse_code 262A")
            if m.parse_code("262A0") != "262A0":
                raise SystemExit("parse_code 262A0")
            if m.parse_code("262a") != "262A0":
                raise SystemExit("parse_code 262a")
            try:
                m.parse_code("drop table")
            except ValueError:
                pass
            else:
                raise SystemExit("parse_code must reject junk")
            for bad in ("abc", "12", "262A/../x", "262A.0"):
                try:
                    m.parse_code(bad)
                except ValueError as exc:
                    if "4-5" not in str(exc):
                        raise SystemExit(f"parse_code {bad!r}: {exc}")
                else:
                    raise SystemExit(f"parse_code must reject {bad!r}")
            text = m.tool_db_stats({})
            if "72030" in text or "トヨタ" in text:
                pass
            if "counts: approx" not in text:
                raise SystemExit(f"db_stats header: {text}")
            if "stocks: 2" not in text or "demo=1" not in text:
                raise SystemExit(f"db_stats: {text}")
            if "daily_prices: 2" not in text or "2026-08-20..2026-08-21" not in text:
                raise SystemExit(f"db_stats prices: {text}")
            if "codes=" in text or "days=" in text:
                raise SystemExit(f"db_stats default must omit DISTINCT: {text}")
            sqls: list[str] = []
            real_fetch = m._timed_fetch

            def record_fetch(conn, budget, sql, params=()):
                sqls.append(sql)
                return real_fetch(conn, budget, sql, params)

            m._timed_fetch = record_fetch
            try:
                m.tool_db_stats({"tables": ["daily_prices"]})
            finally:
                m._timed_fetch = real_fetch
            if any("MIN(" in s and "MAX(" in s for s in sqls):
                raise SystemExit(f"MIN and MAX must be separate queries: {sqls}")
            if not any("MIN(" in s for s in sqls) or not any("MAX(" in s for s in sqls):
                raise SystemExit(f"expected separate MIN and MAX: {sqls}")
            if "xbrl_segments: 2" not in text:
                raise SystemExit(f"db_stats extra table: {text}")
            last_fixed = text.rfind("edinet_list_days:")
            extra_row = text.find("xbrl_segments:")
            if last_fixed < 0 or extra_row < 0 or extra_row < last_fixed:
                raise SystemExit(f"db_stats extra table order: {text}")
            if "sqlite_sequence:" in text:
                raise SystemExit(f"db_stats leaked sqlite_*: {text}")
            exact = m.tool_db_stats({"exact": True})
            if "counts: exact" not in exact or "codes=1" not in exact or "days=2" not in exact:
                raise SystemExit(f"db_stats exact: {exact}")
            only = m.tool_db_stats({"tables": ["stocks", "xbrl_segments"]})
            if "stocks: 2" not in only or "xbrl_segments: 2" not in only:
                raise SystemExit(f"db_stats tables: {only}")
            if "daily_prices:" in only:
                raise SystemExit(f"db_stats tables leaked: {only}")
            missing_tbl = m.tool_db_stats({"tables": ["no_such_table"]})
            if "no_such_table: (no table)" not in missing_tbl:
                raise SystemExit(f"db_stats unknown table: {missing_tbl}")
            bad_exact, bad_exact_err = m.call_tool("db_stats", {"exact": "yes"})
            if not bad_exact_err or "exact must be a boolean" not in bad_exact:
                raise SystemExit(f"db_stats bad exact: {bad_exact}")
            looked = m.tool_db_lookup({"code": "7203", "lines": 5})
            if "72030" not in looked or "トヨタ" not in looked or "adj=3010" not in looked:
                raise SystemExit(f"db_lookup: {looked}")
            missing = m.tool_db_lookup({"code": "99990"})
            if "not in stocks" not in missing:
                raise SystemExit(f"missing: {missing}")
            client = TestClient(m.create_app(token))
            r = _rpc(client, "tools/call", {"name": "db_stats", "arguments": {}}, req_id=90)
            if r.status_code != 200:
                raise SystemExit(f"rpc db_stats: {r.text}")
            rpc_text = r.json()["result"]["content"][0]["text"]
            if "stocks: 2" not in rpc_text or "xbrl_segments: 2" not in rpc_text:
                raise SystemExit(f"rpc db_stats: {r.text}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def test_db_stats_approx_timeout_and_stat1() -> None:
    from tempfile import TemporaryDirectory

    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        import sqlite3

        conn = sqlite3.connect(dbp)
        conn.executescript(
            """
            CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT);
            INSERT INTO stocks VALUES ('72030', 'トヨタ');
            CREATE TABLE gap (id INTEGER PRIMARY KEY, x TEXT);
            INSERT INTO gap VALUES (1, 'a'), (2, 'b'), (3, 'c');
            DELETE FROM gap WHERE id = 1;
            CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
            INSERT INTO kv VALUES ('a', '1'), ('b', '2');
            ANALYZE;
            """
        )
        conn.close()
        try:
            text = m.tool_db_stats({})
            if "gap: 3" not in text:
                raise SystemExit(f"approx MAX(rowid) after delete: {text}")
            if "kv: 2" not in text:
                raise SystemExit(f"WITHOUT ROWID sqlite_stat1: {text}")
            exact = m.tool_db_stats({"exact": True, "tables": ["gap", "kv"]})
            if "gap: 2" not in exact or "kv: 2" not in exact:
                raise SystemExit(f"exact after delete: {exact}")
            if "stocks:" in exact:
                raise SystemExit(f"tables filter leaked stocks: {exact}")

            real_fetch = m._timed_fetch

            def timeout_stocks(conn, budget, sql, params=()):
                if "MAX(rowid)" in sql and "stocks" in sql:
                    return m._TIMEOUT
                return real_fetch(conn, budget, sql, params)

            m._timed_fetch = timeout_stocks
            try:
                partial = m.tool_db_stats({})
            finally:
                m._timed_fetch = real_fetch
            if "stocks: (timeout)" not in partial:
                raise SystemExit(f"timeout stocks: {partial}")
            if "gap: 3" not in partial or "kv: 2" not in partial:
                raise SystemExit(f"timeout must keep later tables: {partial}")

            orig_budget = m._StatsBudget

            def zero_query_budget(*_a, **_k):
                return orig_budget(total_s=8, query_s=0)

            m._StatsBudget = zero_query_budget
            try:
                all_to = m.tool_db_stats({"tables": ["stocks", "gap"]})
            finally:
                m._StatsBudget = orig_budget
            if "stocks: (timeout)" not in all_to or "gap: (timeout)" not in all_to:
                raise SystemExit(f"query_s=0: {all_to}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def test_timed_fetch_progress_interrupt() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(8000)])
    conn.commit()
    clock = {"t": 0.0}

    def now() -> float:
        clock["t"] += 1.0
        return clock["t"]

    budget = m._StatsBudget(total_s=10, query_s=2, now=now)
    conn.set_progress_handler(budget.handler, 1)
    try:
        row = m._timed_fetch(conn, budget, "SELECT COUNT(*) FROM t")
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()
    if row is not m._TIMEOUT:
        raise SystemExit(f"progress_handler should interrupt, got {row!r}")


def test_db_query_and_schema() -> None:
    from tempfile import TemporaryDirectory
    import sqlite3

    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        setup = sqlite3.connect(dbp)
        setup.executescript(
            """
            CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO stocks VALUES ('72030', 'トヨタ'), ('99840', 'ソフバン');
            CREATE TABLE api_tokens (token TEXT);
            INSERT INTO api_tokens VALUES ('secret-token');
            """
        )
        setup.commit()
        setup.close()
        audits: list[tuple] = []
        orig_audit = m._audit_db_query

        def capture_audit(sql, elapsed_s, row_count, truncated, result="ok", error=None):
            audits.append((sql, elapsed_s, row_count, truncated, result, error))
            orig_audit(sql, elapsed_s, row_count, truncated, result, error)

        m._audit_db_query = capture_audit
        try:
            if m._query_authorizer(sqlite3.SQLITE_ATTACH, ":memory:", None, None, None) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny ATTACH")
            if m._query_authorizer(sqlite3.SQLITE_INSERT, "stocks", None, "main", None) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny INSERT")
            if m._query_authorizer(sqlite3.SQLITE_PRAGMA, "query_only", "OFF", None, None) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny PRAGMA write")
            if m._query_authorizer(sqlite3.SQLITE_CREATE_TEMP_TABLE, "x", None, "temp", None) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny TEMP TABLE")
            if m._query_authorizer(sqlite3.SQLITE_PRAGMA, "table_info", "stocks", None, None) != sqlite3.SQLITE_OK:
                raise SystemExit("authorizer must allow table_info")
            if m._query_authorizer(sqlite3.SQLITE_READ, "api_tokens", "token", "main", None) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny secret table")
            if m._query_authorizer(sqlite3.SQLITE_READ, "stocks", "code", "main", None) != sqlite3.SQLITE_OK:
                raise SystemExit("authorizer must allow stocks")

            text, err = m.call_tool(
                "db_query",
                {"sql": "SELECT code, name FROM stocks WHERE code = ? ORDER BY code", "params": ["72030"]},
            )
            if err:
                raise SystemExit(f"select: {text}")
            body = json.loads(text)
            if body["columns"] != ["code", "name"] or body["row_count"] != 1 or body["truncated"]:
                raise SystemExit(f"select body: {body}")
            if body["rows"] != [["72030", "トヨタ"]]:
                raise SystemExit(f"select rows: {body}")
            if not audits or "SELECT code, name FROM stocks" not in audits[-1][0] or audits[-1][2] != 1:
                raise SystemExit(f"audit: {audits}")
            if audits[-1][4] != "ok" or audits[-1][5] is not None:
                raise SystemExit(f"audit result: {audits[-1]}")

            cte, cte_err = m.call_tool(
                "db_query",
                {"sql": "WITH x AS (SELECT code FROM stocks) SELECT COUNT(*) AS n FROM x"},
            )
            if cte_err or json.loads(cte)["rows"] != [[2]]:
                raise SystemExit(f"cte select: {cte}")

            named, named_err = m.call_tool(
                "db_query",
                {"sql": "SELECT name FROM stocks WHERE code = :code", "params": {"code": "99840"}},
            )
            if named_err or json.loads(named)["rows"] != [["ソフバン"]]:
                raise SystemExit(f"named params: {named}")

            semi, semi_err = m.call_tool("db_query", {"sql": "SELECT 1 AS n;"})
            if semi_err or json.loads(semi)["rows"] != [[1]]:
                raise SystemExit(f"trailing semicolon: {semi}")

            denied = [
                "INSERT INTO stocks VALUES ('00000', 'x')",
                "UPDATE stocks SET name = 'x'",
                "DELETE FROM stocks",
                "DROP TABLE stocks",
                "ATTACH DATABASE ':memory:' AS other",
                "PRAGMA query_only = OFF",
                "PRAGMA writable_schema = ON",
                "SELECT 1; SELECT 2",
                "SELECT 1; DROP TABLE stocks",
                "WITH x AS (SELECT '00000' AS code, 'x' AS name) INSERT INTO stocks SELECT * FROM x",
                "/* SELECT 1 */ INSERT INTO stocks VALUES ('00000', 'x')",
            ]
            for sql in denied:
                text, is_error = m.call_tool("db_query", {"sql": sql})
                if not is_error:
                    raise SystemExit(f"must reject {sql!r}: {text}")
            still = json.loads(m.tool_db_query({"sql": "SELECT COUNT(*) AS n FROM stocks"}))
            if still["rows"] != [[2]]:
                raise SystemExit(f"writes leaked: {still}")
            bad, bad_err = m.call_tool("db_query", {"sql": "SELECT nope FROM stocks"})
            if not bad_err or audits[-1][4] != "error" or "nope" not in (audits[-1][5] or ""):
                raise SystemExit(f"audit error: {bad!r} {audits[-1]}")

            secret, secret_err = m.call_tool("db_query", {"sql": "SELECT token FROM api_tokens"})
            if not secret_err or "secret-token" in secret:
                raise SystemExit(f"secret table: {secret!r}")

            limited = json.loads(m.tool_db_query({"sql": "SELECT code FROM stocks ORDER BY code", "limit": 1}))
            if limited["row_count"] != 1 or not limited["truncated"] or limited["rows"] != [["72030"]]:
                raise SystemExit(f"row limit: {limited}")

            orig_bytes = m.DB_QUERY_MAX_BYTES
            m.DB_QUERY_MAX_BYTES = 30
            try:
                sized = json.loads(m.tool_db_query({"sql": "SELECT code, name FROM stocks ORDER BY code"}))
            finally:
                m.DB_QUERY_MAX_BYTES = orig_bytes
            if not sized["truncated"] or sized["row_count"] != 1:
                raise SystemExit(f"byte limit: {sized}")

            orig_ops = m.DB_QUERY_PROGRESS_OPS
            orig_budget = m._QueryBudget

            def short_budget(*_args, **kwargs):
                kwargs["timeout_s"] = 0.0
                return orig_budget(**kwargs)

            m.DB_QUERY_PROGRESS_OPS = 1
            m._QueryBudget = short_budget
            try:
                to_text, to_err = m.call_tool(
                    "db_query",
                    {
                        "sql": (
                            "WITH RECURSIVE n(x) AS ("
                            "SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x < 1000000"
                            ") SELECT COUNT(*) AS n FROM n"
                        )
                    },
                )
            finally:
                m.DB_QUERY_PROGRESS_OPS = orig_ops
                m._QueryBudget = orig_budget
            if not to_err or "query timeout after 180s" not in to_text:
                raise SystemExit(f"timeout: {to_text!r} err={to_err}")
            if audits[-1][4] != "timeout" or "WITH RECURSIVE" not in audits[-1][0]:
                raise SystemExit(f"audit timeout: {audits[-1]}")

            schema = json.loads(m.tool_db_schema({}))
            names = [t["name"] for t in schema["tables"]]
            if "stocks" not in names or "api_tokens" in names or any(n.startswith("sqlite_") for n in names):
                raise SystemExit(f"schema names: {schema}")
            stocks = next(t for t in schema["tables"] if t["name"] == "stocks")
            cols = {c["name"]: c for c in stocks["columns"]}
            if "code" not in cols or cols["code"]["pk"] != 1 or not cols["name"]["notnull"]:
                raise SystemExit(f"schema columns: {stocks}")
            only = json.loads(m.tool_db_schema({"tables": ["stocks"]}))
            if [t["name"] for t in only["tables"]] != ["stocks"]:
                raise SystemExit(f"schema filter: {only}")
            denied_schema, denied_schema_err = m.call_tool("db_schema", {"tables": ["api_tokens"]})
            if not denied_schema_err or "not readable" not in denied_schema:
                raise SystemExit(f"schema secret: {denied_schema}")

            client = TestClient(m.create_app("test-token-for-ci-only"))
            r = _rpc(client, "tools/call", {"name": "db_query", "arguments": {"sql": "SELECT 1 AS n"}}, req_id=91)
            rpc_body = json.loads(r.json()["result"]["content"][0]["text"])
            if r.json()["result"]["isError"] or rpc_body["rows"] != [[1]]:
                raise SystemExit(f"rpc db_query: {r.text}")
        finally:
            m._audit_db_query = orig_audit
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def test_db_query_timeout_value_and_description() -> None:
    """上限は 180 秒。nginx の /mcp（300 秒）より内側で、ツール説明も同じ値。"""
    if m.DB_QUERY_TIMEOUT_S != 180.0:
        raise SystemExit(f"DB_QUERY_TIMEOUT_S {m.DB_QUERY_TIMEOUT_S} != 180")
    nginx = (ROOT / "deploy" / "nginx-site.sh").read_text(encoding="utf-8")
    block = nginx[nginx.index("location ~ ^/mcp/?$"):]
    block = block[: block.index("\n    }")]
    limits = [int(n) for n in re.findall(r"proxy_(?:read|send)_timeout (\d+)s;", block)]
    if len(limits) != 2 or min(limits) <= m.DB_QUERY_TIMEOUT_S:
        raise SystemExit(f"nginx /mcp timeouts {limits} must exceed {m.DB_QUERY_TIMEOUT_S}")
    desc = m.TOOLS["db_query"][0]
    if "180 秒で打ち切り" not in desc or "約2分" in desc:
        raise SystemExit(f"db_query description: {desc}")


def test_db_query_audit_line_format() -> None:
    """監査行: 行頭に JST の日時、result、所要時間、SQL は 1 行で全文。秘密は伏せる。"""

    def line_of(*args) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m._audit_db_query(*args)
        out = buf.getvalue()
        if len(out.splitlines()) != 1 or not out.endswith("\n"):
            raise SystemExit(f"audit must be one line: {out!r}")
        return out[:-1]

    tail = "OR code = '72030' " * 60
    sql = "SELECT code,\r\n  name\nFROM stocks WHERE name = 'x\u2028y' " + tail
    ok = line_of(sql, 1.23456, 3, False)
    stamp = m._cron_stamp(ok)
    if stamp is None or abs((m._jst_now() - stamp).total_seconds()) > 5:
        raise SystemExit(f"audit stamp: {ok[:40]!r}")
    flat = ("SELECT code,   name FROM stocks WHERE name = 'x y' " + tail).strip()
    want = f" db_query result=ok rows=3 elapsed=1.235s truncated=false sql_chars={len(sql)} sql={flat}"
    if ok[19:] != want or len(flat) <= 400:
        raise SystemExit(f"audit ok line: {ok!r}")

    timeout = line_of("SELECT 1", 180.004, 0, False, "timeout")
    if " db_query result=timeout rows=0 elapsed=180.004s " not in timeout or "error=" in timeout:
        raise SystemExit(f"audit timeout line: {timeout!r}")

    err = line_of("SELECT nope", 0.001, 0, False, "error", "no such column: nope\npassword=hunter2")
    want_err = ' result=error rows=0 elapsed=0.001s truncated=false error="no such column: nope password=<redacted>" sql_chars=11 sql=SELECT nope'
    if want_err not in err:
        raise SystemExit(f"audit error line: {err!r}")
    # fluent-bit は mcp.log の ERROR / Traceback を Slack に流す。監査行の語は小文字のまま。
    if "ERROR" in err or "Traceback" in err:
        raise SystemExit(f"audit line must not page: {err!r}")

    secret = line_of("SELECT 'api_key=abc123' AS k", 0.0, 1, False)
    if "abc123" in secret or "api_key=<redacted>" not in secret:
        raise SystemExit(f"audit redact: {secret!r}")

    huge = "SELECT '" + "あ" * (m.DB_QUERY_SQL_LOG_CHARS + 50) + "'"
    cut = line_of(huge, 0.0, 1, False)
    logged = cut.split(" sql=", 1)[1]
    if len(logged) != m.DB_QUERY_SQL_LOG_CHARS + 1 or not logged.endswith("…") or f"sql_chars={len(huge)} " not in cut:
        raise SystemExit(f"audit cut: len={len(logged)}")


def test_db_query_budget_interrupts_after_timeout_s() -> None:
    """progress handler は渡した timeout_s を過ぎたときだけクエリを切る。"""
    import sqlite3

    sql = (
        "WITH RECURSIVE n(x) AS ("
        "SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x < 100000"
        ") SELECT COUNT(*) AS c FROM n"
    )

    def run(timeout_s: float, now) -> str | None:
        conn = sqlite3.connect(":memory:")
        budget = m._QueryBudget(timeout_s=timeout_s, now=now)
        conn.set_progress_handler(budget.handler, 1)
        try:
            conn.execute(sql).fetchone()
        except sqlite3.OperationalError as exc:
            if m._is_interrupt(exc):
                return "timeout"
            raise
        else:
            return None
        finally:
            conn.set_progress_handler(None, 0)
            conn.close()

    held = {"t": 50.0}
    if run(1.0, lambda: held["t"]) is not None:
        raise SystemExit("query was cut while the clock stayed inside timeout_s")

    moved = {"t": 0.0}

    def step() -> float:
        moved["t"] += 1.0
        return moved["t"]

    if run(0.25, step) != "timeout":
        raise SystemExit("query was not cut after timeout_s elapsed")


def test_db_query_explain() -> None:
    from tempfile import TemporaryDirectory
    import sqlite3

    desc, schema, _fn = m.TOOLS["db_query"]
    if "EXPLAIN" not in desc or "EXPLAIN QUERY PLAN" not in desc:
        raise SystemExit(f"tool description missing EXPLAIN: {desc}")
    sql_desc = schema["properties"]["sql"]["description"]
    if "EXPLAIN" not in sql_desc:
        raise SystemExit(f"sql property missing EXPLAIN: {sql_desc}")

    allowed = [
        "EXPLAIN SELECT 1",
        "EXPLAIN QUERY PLAN SELECT 1",
        "explain query plan select 1",
        "EXPLAIN QUERY PLAN SELECT 1;",
        "EXPLAIN\nQUERY PLAN\nSELECT 1",
        "EXPLAIN QUERY PLAN WITH x AS (SELECT 1 AS n) SELECT n FROM x",
        "EXPLAIN (SELECT 1)",
    ]
    for sql in allowed:
        got = m.parse_db_sql(sql)
        if m._LEAD_SQL_RE.match(got) is None:
            raise SystemExit(f"parse allowed failed: {sql!r} -> {got!r}")

    denied = [
        "EXPLAIN DELETE FROM stocks",
        "EXPLAIN INSERT INTO stocks VALUES ('00000', 'x')",
        "EXPLAIN UPDATE stocks SET name = 'x'",
        "EXPLAIN QUERY PLAN PRAGMA table_info(stocks)",
        "EXPLAIN QUERY PLAN INSERT INTO stocks VALUES ('00000', 'x')",
        "EXPLAIN PRAGMA query_only = OFF",
        "EXPLAIN SELECT 1; SELECT 2",
        "EXPLAIN QUERY PLAN SELECT 1; EXPLAIN QUERY PLAN SELECT 2",
        "EXPLAIN SELECT 1; DROP TABLE stocks",
        "EXPLAIN",
        "QUERY PLAN SELECT 1",
    ]
    for sql in denied:
        try:
            m.parse_db_sql(sql)
        except ValueError:
            continue
        raise SystemExit(f"parse must reject {sql!r}")

    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        setup = sqlite3.connect(dbp)
        setup.executescript(
            """
            CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT NOT NULL);
            INSERT INTO stocks VALUES ('72030', 'トヨタ'), ('99840', 'ソフバン');
            CREATE TABLE api_tokens (token TEXT);
            INSERT INTO api_tokens VALUES ('secret-token');
            """
        )
        setup.commit()
        setup.close()
        try:
            if m._query_authorizer(
                sqlite3.SQLITE_READ, "sqlite_dbstat", "name", "main", None,
            ) != sqlite3.SQLITE_DENY:
                raise SystemExit("authorizer must deny dbstat")

            plan, plan_err = m.call_tool(
                "db_query",
                {"sql": "EXPLAIN QUERY PLAN SELECT code, name FROM stocks WHERE code = '72030'"},
            )
            if plan_err:
                raise SystemExit(f"explain query plan: {plan}")
            plan_body = json.loads(plan)
            if plan_body["row_count"] < 1 or plan_body["truncated"]:
                raise SystemExit(f"explain query plan body: {plan_body}")
            cols = [c.lower() for c in plan_body["columns"]]
            if "detail" not in cols:
                raise SystemExit(f"explain query plan columns: {plan_body['columns']}")
            detail = " ".join(
                str(cell) for row in plan_body["rows"] for cell in row if cell is not None
            ).lower()
            if "stocks" not in detail and "scan" not in detail and "search" not in detail:
                raise SystemExit(f"explain query plan detail: {plan_body}")

            bytecode, bytecode_err = m.call_tool(
                "db_query",
                {"sql": "EXPLAIN SELECT code FROM stocks ORDER BY code"},
            )
            if bytecode_err:
                raise SystemExit(f"explain: {bytecode}")
            bytecode_body = json.loads(bytecode)
            opcode_cols = [c.lower() for c in bytecode_body["columns"]]
            if bytecode_body["row_count"] < 1 or "opcode" not in opcode_cols:
                raise SystemExit(f"explain bytecode: {bytecode_body}")

            cte, cte_err = m.call_tool(
                "db_query",
                {
                    "sql": (
                        "EXPLAIN QUERY PLAN WITH x AS (SELECT code FROM stocks) "
                        "SELECT code FROM x"
                    )
                },
            )
            if cte_err or json.loads(cte)["row_count"] < 1:
                raise SystemExit(f"explain cte: {cte}")

            rejected = [
                "EXPLAIN DELETE FROM stocks",
                "EXPLAIN INSERT INTO stocks VALUES ('00000', 'x')",
                "EXPLAIN UPDATE stocks SET name = 'x'",
                "EXPLAIN QUERY PLAN PRAGMA table_info(stocks)",
                "EXPLAIN QUERY PLAN INSERT INTO stocks VALUES ('00000', 'x')",
                "EXPLAIN SELECT 1; SELECT 2",
                "EXPLAIN QUERY PLAN SELECT 1; DROP TABLE stocks",
                "EXPLAIN QUERY PLAN SELECT * FROM sqlite_dbstat",
                "EXPLAIN SELECT token FROM api_tokens",
            ]
            for sql in rejected:
                text, is_error = m.call_tool("db_query", {"sql": sql})
                if not is_error:
                    raise SystemExit(f"must reject {sql!r}: {text}")
                if "secret-token" in text:
                    raise SystemExit(f"secret leaked for {sql!r}: {text}")

            still = json.loads(m.tool_db_query({"sql": "SELECT COUNT(*) AS n FROM stocks"}))
            if still["rows"] != [[2]]:
                raise SystemExit(f"writes leaked after explain: {still}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def test_fx_tools() -> None:
    from tempfile import TemporaryDirectory

    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        import sqlite3

        conn = sqlite3.connect(dbp)
        conn.executescript(
            """
            CREATE TABLE fx_rates (
                date TEXT NOT NULL,
                quote TEXT NOT NULL,
                rate REAL NOT NULL,
                PRIMARY KEY (date, quote)
            );
            INSERT INTO fx_rates VALUES ('2024-01-04', 'USD', 1.10);
            INSERT INTO fx_rates VALUES ('2024-01-04', 'JPY', 165.0);
            INSERT INTO fx_rates VALUES ('2024-01-05', 'USD', 1.09);
            INSERT INTO fx_rates VALUES ('2024-01-05', 'JPY', 159.14);
            INSERT INTO fx_rates VALUES ('2024-01-31', 'USD', 1.08);
            INSERT INTO fx_rates VALUES ('2024-01-31', 'JPY', 159.3);
            INSERT INTO fx_rates VALUES ('2024-02-01', 'USD', 1.08);
            INSERT INTO fx_rates VALUES ('2024-02-01', 'JPY', 160.38);
            INSERT INTO fx_rates VALUES ('2024-02-29', 'USD', 1.08);
            INSERT INTO fx_rates VALUES ('2024-02-29', 'JPY', 162.0);
            """
        )
        conn.commit()
        conn.close()
        try:
            text, is_error = m.call_tool(
                "fx_rate", {"date": "2024-01-05", "pair": "USD/JPY"},
            )
            if is_error:
                raise SystemExit(f"fx_rate friday: {text}")
            body = json.loads(text)
            expect = 159.14 / 1.09
            if abs(body["rate"] - expect) > 1e-12:
                raise SystemExit(f"fx_rate rate: {body}")
            if body["holiday_fallback"] or body["as_of"] != "2024-01-05":
                raise SystemExit(f"friday fallback: {body}")
            sat, sat_err = m.call_tool(
                "fx_rate", {"date": "2024-01-06", "base": "USD", "quote": "JPY"},
            )
            if sat_err:
                raise SystemExit(f"fx_rate saturday: {sat}")
            sat_body = json.loads(sat)
            if not sat_body["holiday_fallback"] or sat_body["as_of"] != "2024-01-05":
                raise SystemExit(f"saturday must use friday: {sat_body}")
            if "直近営業日" not in sat_body["holiday_policy"]:
                raise SystemExit(f"holiday policy missing: {sat_body}")
            series, series_err = m.call_tool(
                "fx_rates",
                {"from": "2024-01-04", "to": "2024-01-07", "pair": "USD/JPY"},
            )
            if series_err:
                raise SystemExit(f"fx_rates: {series}")
            head, _, table = series.partition("\ndate\trate\n")
            if table.splitlines() != ["2024-01-04\t150", "2024-01-05\t146"]:
                raise SystemExit(f"series rows: {series}")
            if "2件" not in head or "-2.7%" not in head or "高値 150（2024-01-04）" not in head:
                raise SystemExit(f"series summary: {series}")
            if "休日は含めない" not in head:
                raise SystemExit(f"series policy: {series}")
            eur, _ = m.call_tool("fx_rates", {"from": "2024-01-04", "to": "2024-01-05", "pair": "EUR/USD"})
            if "2024-01-05\t1.09" not in eur:
                raise SystemExit(f"5 significant digits: {eur}")
            month, month_err = m.call_tool(
                "fx_rates", {"from": "2024-01-01", "to": "2024-02-29", "pair": "USD/JPY", "interval": "month"},
            )
            if month_err or month.split("\ndate\trate\n")[1].splitlines() != ["2024-01-31\t147.5", "2024-02-29\t150"]:
                raise SystemExit(f"fx_rates month: {month}")
            if "安値 146（2024-01-05）" not in month:
                raise SystemExit(f"month summary uses daily: {month}")
            week, _ = m.call_tool(
                "fx_rates", {"from": "2024-01-01", "to": "2024-02-29", "pair": "USD/JPY", "interval": "week"},
            )
            if [r.split("\t")[0] for r in week.split("\ndate\trate\n")[1].splitlines()] != [
                "2024-01-05", "2024-02-01", "2024-02-29",
            ]:
                raise SystemExit(f"fx_rates week: {week}")
            cut, _ = m.call_tool("fx_rates", {"from": "2024-01-01", "to": "2024-02-29", "pair": "USD/JPY", "lines": 1})
            if "全 5 件のうち先頭 1 件" not in cut or len(cut.split("\ndate\trate\n")[1].splitlines()) != 1:
                raise SystemExit(f"fx_rates lines: {cut}")
            empty, empty_err = m.call_tool("fx_rates", {"from": "2024-03-02", "to": "2024-03-03", "pair": "USD/JPY"})
            if empty_err or "0件" not in empty:
                raise SystemExit(f"fx_rates empty: {empty}")
            bad_iv, bad_iv_err = m.call_tool(
                "fx_rates", {"from": "2024-01-04", "to": "2024-01-05", "pair": "USD/JPY", "interval": "year"},
            )
            if not bad_iv_err or "interval" not in bad_iv:
                raise SystemExit(f"bad interval: {bad_iv!r}")
            bad, bad_err = m.call_tool("fx_rate", {"date": "2024-01-05"})
            if not bad_err or "pair" not in bad:
                raise SystemExit(f"missing pair: {bad!r}")
            early, early_err = m.call_tool(
                "fx_rate", {"date": "1999-01-03", "pair": "USD/JPY"},
            )
            if not early_err or "1999-01-04" not in early:
                raise SystemExit(f"before earliest: {early!r}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def _http_status_body(text: str) -> tuple[int, object]:
    line, _, rest = text.partition("\n")
    if not line.startswith("HTTP "):
        raise SystemExit(f"missing HTTP status line: {text!r}")
    status = int(line.split()[1])
    rest = rest.strip()
    if not rest:
        return status, None
    try:
        return status, json.loads(rest)
    except json.JSONDecodeError:
        return status, rest


def _expect_value_error(fn, *args, needle: str) -> None:
    try:
        fn(*args)
    except ValueError as exc:
        if needle not in str(exc):
            raise SystemExit(f"ValueError {exc!r} missing {needle!r}")
    else:
        raise SystemExit(f"{fn.__name__}{args} should have raised")


def _stub_handle(method: str, path: str, raw: bytes, store: dict) -> tuple[int, object]:
    import re
    from urllib.parse import unquote

    def load_body() -> dict:
        if not raw:
            return {}
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            return {}
        return body

    list_re = re.compile(r"^/api/v1/tables$")
    _code = r"[0-9][0-9A-Z]{3}[0-9]?"
    get_re = re.compile(rf"^/api/v1/tables/({_code})/(\d{{4}})$")
    el_re = re.compile(rf"^/api/v1/tables/({_code})/(\d{{4}})/elements$")
    batch_re = re.compile(rf"^/api/v1/tables/({_code})/(\d{{4}})/values/batch$")
    ingest_re = re.compile(rf"^/api/v1/tables/({_code})/ingest$")

    if method == "GET" and list_re.fullmatch(path):
        by_code: dict[str, dict] = {}
        for code, year in sorted(store):
            item = by_code.setdefault(code, {"code": code, "name": None, "years": []})
            item["years"].append(year)
        return 200, list(by_code.values())

    m = get_re.fullmatch(path)
    if method == "GET" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        row = store.get((code, year))
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        elements = []
        units = row.get("units") or {}
        for el in row["order"]:
            vals = {str(k): v for k, v in row["values"].get(el, {}).items()}
            if vals:
                vals = {str(m): vals.get(str(m)) for m in range(1, 13)}
            elements.append({"element": el, "unit": units.get(el, "number"), "values": vals})
        return 200, {"code": code, "year": year, "elements": elements}

    m = el_re.fullmatch(path)
    if method == "POST" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        force = body.get("force") is True
        unit = body.get("unit") if isinstance(body.get("unit"), str) else None
        key = (code, year)
        row = store.setdefault(key, {"order": [], "values": {}, "units": {}})
        row.setdefault("units", {})
        if element in row["order"]:
            if unit is not None:
                row["units"][element] = unit
            return 200, {
                "code": code, "year": year, "element": element,
                "unit": row["units"].get(element, "number"), "created": False,
            }

        def _norm(s: str) -> str:
            return "".join(str(s).split()).lower()

        similar = [e for e in row["order"] if _norm(e) == _norm(element)]
        if similar and not force:
            return 409, {
                "error": {
                    "code": "ELEMENT_SIMILAR_EXISTS",
                    "message": "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。",
                    "similar": similar,
                }
            }
        row["order"].append(element)
        row["values"][element] = {}
        row["units"][element] = unit or "number"
        return 201, {
            "code": code, "year": year, "element": element,
            "unit": row["units"][element], "created": True,
        }

    if method == "PUT" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        unit = body.get("unit")
        key = (code, year)
        row = store.get(key)
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        if element not in row["order"]:
            return 404, {"error": {"code": "ELEMENT_NOT_FOUND", "message": "要素が登録されていません。"}}
        row.setdefault("units", {})[element] = unit
        return 200, {"code": code, "year": year, "element": element, "unit": unit}

    if method == "DELETE" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        key = (code, year)
        row = store.get(key)
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        if element not in row["order"]:
            return 404, {"error": {"code": "ELEMENT_NOT_FOUND", "message": "要素が登録されていません。"}}
        row["order"] = [e for e in row["order"] if e != element]
        row["values"].pop(element, None)
        row.get("units", {}).pop(element, None)
        return 200, {"code": code, "year": year, "element": element, "deleted": True}

    m = batch_re.fullmatch(path)
    if method == "PUT" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        items = body.get("values")
        key = (code, year)
        row = store.get(key)
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        if element not in row["order"]:
            return 404, {"error": {"code": "ELEMENT_NOT_FOUND", "message": "要素が登録されていません。"}}
        if not isinstance(items, list):
            return 400, {"error": {"code": "INVALID_PARAM", "message": "values は {month, value} の配列です。"}}
        ops: dict[int, object] = {}
        for item in items:
            val = item.get("value")
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
                return 400, {
                    "error": {
                        "code": "INVALID_VALUE",
                        "message": "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。",
                    }
                }
            ops[int(item["month"])] = val
        for month, val in ops.items():
            if val is None:
                row["values"][element].pop(month, None)
            else:
                row["values"][element][month] = val
        return 200, {"code": code, "year": year, "element": element, "updated": list(ops.keys())}

    m = ingest_re.fullmatch(path)
    if method == "POST" and m:
        code = unquote(m.group(1))
        body = load_body()
        if "year" in body:
            return 400, {"error": {"code": "INVALID_PARAM", "message": "year must not be sent"}}
        return 200, {
            "code": code,
            "fy_end_month": 10,
            "layout": "fiscal",
            "as_of": body.get("as_of"),
            "mapping": {},
            "updated": [],
        }

    return 404, {"error": {"code": "NOT_FOUND", "message": path}}


def _monthly_stub():
    import threading
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse as _urlparse

    @contextmanager
    def ctx():
        recorded: list[dict] = []
        store: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_PUT(self) -> None:
                self._dispatch("PUT")

            def do_DELETE(self) -> None:
                self._dispatch("DELETE")

            def _dispatch(self, method: str) -> None:
                path = _urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                recorded.append({"method": method, "path": path, "host": self.headers.get("Host", ""), "body": raw})
                status, payload = _stub_handle(method, path, raw, store)
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1], recorded
        finally:
            httpd.shutdown()
            httpd.server_close()

    return ctx()


def test_monthly_tools() -> None:
    from tempfile import TemporaryDirectory
    from urllib.parse import urlparse

    if m.APP_HTTP_TIMEOUT != 10:
        raise SystemExit(f"APP_HTTP_TIMEOUT {m.APP_HTTP_TIMEOUT} != 10")
    if m.DEFAULT_APP_HTTP != "http://127.0.0.1:8000":
        raise SystemExit(f"DEFAULT_APP_HTTP {m.DEFAULT_APP_HTTP}")

    prev_http = os.environ.get("MCP_APP_HTTP")
    os.environ.pop("MCP_APP_HTTP", None)
    try:
        if m.app_http_base() != "http://127.0.0.1:8000":
            raise SystemExit(f"default base: {m.app_http_base()}")
        url = m.monthly_api_url("7203", 2024)
        parsed = urlparse(url)
        if parsed.hostname != "127.0.0.1" or parsed.port != 8000:
            raise SystemExit(f"url host: {url}")
        if parsed.path != "/api/v1/tables/7203/2024":
            raise SystemExit(f"url path not padded? {parsed.path}")
        if m.monthly_api_url() != "http://127.0.0.1:8000/api/v1/tables":
            raise SystemExit(f"list url: {m.monthly_api_url()}")
        if m.monthly_api_url("7203", 2024, "/elements") != "http://127.0.0.1:8000/api/v1/tables/7203/2024/elements":
            raise SystemExit("elements url")
        if m.monthly_api_url("7203", 2024, "/values/batch") != "http://127.0.0.1:8000/api/v1/tables/7203/2024/values/batch":
            raise SystemExit("batch url")
        if m.monthly_ingest_url("92790") != "http://127.0.0.1:8000/api/v1/tables/92790/ingest":
            raise SystemExit(f"ingest url: {m.monthly_ingest_url('92790')}")
        if m.monthly_industry_url("7203") != "http://127.0.0.1:8000/api/v1/tables/7203/industry":
            raise SystemExit(f"industry url: {m.monthly_industry_url('7203')}")
        if m.monthly_api_url("262A0", 2024) != "http://127.0.0.1:8000/api/v1/tables/262A0/2024":
            raise SystemExit(f"alnum url: {m.monthly_api_url('262A0', 2024)}")
        if m.monthly_api_url("262A", 2024) != "http://127.0.0.1:8000/api/v1/tables/262A/2024":
            raise SystemExit(f"display url charset: {m.monthly_api_url('262A', 2024)}")
        if m.monthly_ingest_url("262A0") != "http://127.0.0.1:8000/api/v1/tables/262A0/ingest":
            raise SystemExit("alnum ingest url")
        if m.monthly_api_url("92790", tail="/ingest") != "http://127.0.0.1:8000/api/v1/tables/92790/ingest":
            raise SystemExit("ingest via monthly_api_url")
        ingest_parsed = urlparse(m.monthly_ingest_url("7203"))
        if ingest_parsed.path != "/api/v1/tables/7203/ingest":
            raise SystemExit(f"ingest path has year?: {ingest_parsed.path}")
        if ingest_parsed.hostname != "127.0.0.1":
            raise SystemExit(f"ingest host: {ingest_parsed.hostname}")
        try:
            m.monthly_api_url("7203", 2024, "/etc/passwd")
        except ValueError:
            pass
        else:
            raise SystemExit("arbitrary tail should fail")
        try:
            m.monthly_api_url("7203", 2024, "/ingest")
        except ValueError:
            pass
        else:
            raise SystemExit("year-bearing ingest URL should fail")
        try:
            m.monthly_api_url("7203", 2024, "/industry")
        except ValueError:
            pass
        else:
            raise SystemExit("year-bearing industry URL should fail")

        os.environ["MCP_APP_HTTP"] = "http://kabukpi.com/api"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "http://8.8.8.8:8000"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "https://example.com"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "http://127.0.0.1:9"
        if m.app_http_base() != "http://127.0.0.1:9":
            raise SystemExit(f"loopback override: {m.app_http_base()}")
        os.environ["MCP_APP_HTTP"] = "http://localhost:8000/ignored"
        if m.app_http_base() != "http://localhost:8000":
            raise SystemExit(f"path should be stripped: {m.app_http_base()}")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http

    _expect_value_error(m.parse_monthly_code, "abc", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "12", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "7203/../etc", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "7203.0", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "720300", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "262A/../x", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "262A.0", needle="4-5")
    if m.parse_monthly_code("7203") != "72030":
        raise SystemExit("monthly 7203 should pad")
    if m.parse_monthly_code("72030") != "72030":
        raise SystemExit("72030")
    if m.parse_monthly_code("9279") != "92790":
        raise SystemExit("monthly 9279 should pad")
    if m.parse_monthly_code("92790") != "92790":
        raise SystemExit("92790")
    if m.parse_monthly_code("262A") != "262A0":
        raise SystemExit("monthly 262A should canonicalize")
    if m.parse_monthly_code("262A0") != "262A0":
        raise SystemExit("monthly 262A0")
    if m.parse_monthly_code("262a") != "262A0":
        raise SystemExit("monthly 262a")
    if m.parse_monthly_code("7649") != "76490":
        raise SystemExit("monthly 7649 should pad")
    if m.parse_monthly_code("9279") != m.parse_code("9279"):
        raise SystemExit("parse_monthly_code must match parse_code")
    if not m.CODE_RE.fullmatch("262A0") or not m.CODE_RE.fullmatch("262A"):
        raise SystemExit("CODE_RE must allow 262A / 262A0")
    if m.CODE_RE.fullmatch("abc") or m.CODE_RE.fullmatch("12") or m.CODE_RE.fullmatch("262A/../x"):
        raise SystemExit("CODE_RE must reject junk")
    _expect_value_error(m.monthly_api_url, "262A/../x", 2024, needle="4-5")
    _expect_value_error(m.parse_monthly_year, 24, needle="4-digit")
    _expect_value_error(m.parse_monthly_year, "2024/../x", needle="4-digit")
    _expect_value_error(m.parse_monthly_year, 20240, needle="4-digit")
    if m.parse_monthly_year(2024) != 2024 or m.parse_monthly_year("2024") != 2024:
        raise SystemExit("year parse")
    _expect_value_error(m.parse_monthly_month, 0, needle="1-12")
    _expect_value_error(m.parse_monthly_month, 13, needle="1-12")
    if m.parse_monthly_unit("yoy_index") != "yoy_index":
        raise SystemExit("parse_monthly_unit yoy_index")
    if m.parse_monthly_unit("yoy_pct") != "yoy_pct":
        raise SystemExit("parse_monthly_unit yoy_pct")
    if m.parse_monthly_unit("number") != "number":
        raise SystemExit("parse_monthly_unit number")
    _expect_value_error(m.parse_monthly_unit, "percent", needle="number, yoy_index, or yoy_pct")
    _expect_value_error(m.parse_monthly_unit, 125.8, needle="number, yoy_index, or yoy_pct")
    _expect_value_error(m.parse_monthly_unit, None, needle="number, yoy_index, or yoy_pct")

    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory(ignore_cleanup_errors=True) as d:
        os.environ["DATABASE_URL"] = f"sqlite:///{d}/stock.db"
        try:
            from fastapi.testclient import TestClient as FastAPIClient

            from app.db import SessionLocal
            from app.main import app as fastapi_app
            from app.models import Stock

            db = SessionLocal()
            try:
                if db.get(Stock, "72030") is None:
                    db.add(Stock(code="72030", name="トヨタ自動車"))
                    db.commit()
            finally:
                db.close()

            api = FastAPIClient(fastapi_app)
            r = api.get("/api/v1/tables")
            if r.status_code != 200 or r.json() != []:
                raise SystemExit(f"TestClient empty tables: {r.status_code} {r.text}")
            r = api.post("/api/v1/tables/7203/2024/elements", json={"element": "売上高前年比", "unit": "number"})
            if r.status_code != 201:
                raise SystemExit(f"TestClient add: {r.status_code} {r.text}")
            r = api.get("/api/v1/tables/7203/2024")
            if r.status_code != 200 or r.json()["elements"]:
                raise SystemExit(f"TestClient get empty: {r.text}")
        finally:
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url

    prev_http = os.environ.get("MCP_APP_HTTP")
    try:
        with _monthly_stub() as (port, recorded):
            os.environ["MCP_APP_HTTP"] = f"http://127.0.0.1:{port}"
            token = "test-token-for-ci-only"
            client = TestClient(m.create_app(token))

            before = len(recorded)
            for bad in ("abc", "12", "7203/../etc", "7203.0", "262A/../x"):
                text, is_error = m.call_tool("monthly_get", {"code": bad, "year": 2024})
                if not is_error:
                    raise SystemExit(f"bad code {bad!r} should isError: {text}")
                text, is_error = m.call_tool(
                    "monthly_add_element", {"code": bad, "year": 2024, "element": "x"}
                )
                if not is_error:
                    raise SystemExit(f"add bad code {bad!r} should isError")
                text, is_error = m.call_tool(
                    "monthly_put_values",
                    {"code": bad, "year": 2024, "element": "x", "values": [{"month": 1, "value": 1}]},
                )
                if not is_error:
                    raise SystemExit(f"put bad code {bad!r} should isError")
            if len(recorded) != before:
                raise SystemExit(f"bad code must not hit HTTP: {recorded[before:]}")

            text, is_error = m.call_tool("monthly_list", {})
            if is_error:
                raise SystemExit(f"monthly_list empty: {text}")
            status, body = _http_status_body(text)
            if status != 200 or body != []:
                raise SystemExit(f"empty list shape: {text!r}")

            r = _rpc(client, "tools/call", {"name": "monthly_list", "arguments": {}}, req_id=40)
            rpc_text = r.json()["result"]["content"][0]["text"]
            if r.json()["result"]["isError"] or "HTTP 200" not in rpc_text:
                raise SystemExit(f"rpc monthly_list: {r.text}")

            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "売上高前年比", "unit": "number"},
            )
            if is_error:
                raise SystemExit(f"add: {text}")
            status, body = _http_status_body(text)
            if status != 201 or not isinstance(body, dict) or body.get("created") is not True:
                raise SystemExit(f"add created: {text}")

            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "店舗数", "unit": "number"},
            )
            if is_error:
                raise SystemExit(f"add with unit: {text}")
            status, body = _http_status_body(text)
            if status != 201 or not isinstance(body, dict) or body.get("unit") != "number":
                raise SystemExit(f"add unit body: {text}")

            before_bad_unit = len(recorded)
            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "x", "unit": "percent"},
            )
            if not is_error:
                raise SystemExit(f"invalid unit should isError: {text}")
            if "number, yoy_index, or yoy_pct" not in text:
                raise SystemExit(f"invalid unit message: {text}")
            if len(recorded) != before_bad_unit:
                raise SystemExit(f"invalid unit must not hit HTTP: {recorded[before_bad_unit:]}")

            text, is_error = m.call_tool("monthly_get", {"code": "7203", "year": 2024})
            if is_error:
                raise SystemExit(f"get after add: {text}")
            status, body = _http_status_body(text)
            if status != 200 or body["elements"][0]["element"] != "売上高前年比":
                raise SystemExit(f"get roundtrip: {text}")
            for el in body["elements"]:
                if "unit" in el and el["unit"] not in ("number", "yoy_index", "yoy_pct"):
                    raise SystemExit(f"GET JSON unit invalid: {el!r}")
            by_el = {e["element"]: e for e in body["elements"]}
            if "店舗数" in by_el and by_el["店舗数"].get("unit") not in (None, "number"):
                raise SystemExit(f"GET unit for 店舗数: {by_el['店舗数']!r}")

            before_omit_unit = len(recorded)
            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "x"},
            )
            if not is_error:
                raise SystemExit(f"omit unit should isError: {text}")
            if "unit is required" not in text:
                raise SystemExit(f"omit unit message: {text}")
            if len(recorded) != before_omit_unit:
                raise SystemExit(f"omit unit must not hit HTTP: {recorded[before_omit_unit:]}")

            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "売上高 前年比", "unit": "number"},
            )
            if not is_error:
                raise SystemExit(f"similar should set isError: {text}")
            if not text.startswith("HTTP 409"):
                raise SystemExit(f"similar should still return HTTP text: {text}")
            status, body = _http_status_body(text)
            if status != 409 or not isinstance(body, dict) or body.get("error", {}).get("code") != "ELEMENT_SIMILAR_EXISTS":
                raise SystemExit(f"expected 409 ELEMENT_SIMILAR_EXISTS: {text}")

            text, is_error = m.call_tool(
                "monthly_put_values",
                {
                    "code": "7203",
                    "year": 2024,
                    "element": "売上高前年比",
                    "values": [{"month": 4, "value": 12.5}, {"month": 5, "value": None}],
                },
            )
            if is_error:
                raise SystemExit(f"put: {text}")
            status, body = _http_status_body(text)
            if status != 200:
                raise SystemExit(f"put status: {text}")

            text, is_error = m.call_tool(
                "monthly_put_values",
                {
                    "code": "7203",
                    "year": 2024,
                    "element": "売上高前年比",
                    "values": [{"month": 4, "value": "10%"}],
                },
            )
            if not is_error:
                raise SystemExit(f"% value should set isError: {text}")
            if not text.startswith("HTTP 400"):
                raise SystemExit(f"% value should be API error text: {text}")
            status, body = _http_status_body(text)
            if status != 400 or not isinstance(body, dict) or body.get("error", {}).get("code") != "INVALID_VALUE":
                raise SystemExit(f"expected INVALID_VALUE: {text}")

            text, is_error = m.call_tool(
                "monthly_delete_element",
                {"code": "7203", "year": 2024, "element": "店舗数"},
            )
            if is_error:
                raise SystemExit(f"delete: {text}")
            status, body = _http_status_body(text)
            if status != 200 or not isinstance(body, dict) or body.get("deleted") is not True:
                raise SystemExit(f"delete body: {text}")

            r = _rpc(
                client,
                "tools/call",
                {"name": "monthly_get", "arguments": {"code": "7203/../etc", "year": 2024}},
                req_id=41,
            )
            if not r.json()["result"]["isError"]:
                raise SystemExit("rpc traversal code should isError")

            allowed = {
                ("GET", "/api/v1/tables"),
                ("GET", "/api/v1/tables/72030/2024"),
                ("POST", "/api/v1/tables/72030/2024/elements"),
                ("PUT", "/api/v1/tables/72030/2024/values/batch"),
                ("DELETE", "/api/v1/tables/72030/2024/elements"),
            }
            for rec in recorded:
                host = rec["host"].split(":")[0]
                if host != "127.0.0.1":
                    raise SystemExit(f"non-loopback host: {rec}")
                if not rec["path"].startswith("/api/v1/tables"):
                    raise SystemExit(f"path not allowlisted: {rec}")
                if ".." in rec["path"] or "/etc" in rec["path"]:
                    raise SystemExit(f"traversal path: {rec}")
                if (rec["method"], rec["path"]) not in allowed:
                    raise SystemExit(f"unexpected monthly request: {rec}")
            if not any(rec["method"] == "GET" and rec["path"] == "/api/v1/tables" for rec in recorded):
                raise SystemExit("monthly_list did not hit GET /api/v1/tables")
            if not any(rec["method"] == "POST" and rec["path"] == "/api/v1/tables/72030/2024/elements" for rec in recorded):
                raise SystemExit("add_element path missing")
            if not any(rec["method"] == "PUT" and rec["path"] == "/api/v1/tables/72030/2024/values/batch" for rec in recorded):
                raise SystemExit("put_values path missing")
            if not any(rec["method"] == "DELETE" and rec["path"] == "/api/v1/tables/72030/2024/elements" for rec in recorded):
                raise SystemExit("delete_element path missing")

        os.environ["MCP_APP_HTTP"] = "http://127.0.0.1:1"
        text, is_error = m.call_tool("monthly_list", {})
        if not is_error:
            raise SystemExit(f"down FastAPI should isError: {text}")
        if "connection refused" not in text.lower() and "unreachable" not in text.lower():
            raise SystemExit(f"expected connection refused: {text!r}")
        if "Traceback" in text:
            raise SystemExit(f"traceback leaked: {text}")

        with _monthly_stub() as (port, recorded):
            os.environ["MCP_APP_HTTP"] = f"http://127.0.0.1:{port}"
            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "262A", "year": 2024, "element": "売上高前年比", "unit": "number"},
            )
            if is_error:
                raise SystemExit(f"alnum add: {text}")
            text, is_error = m.call_tool("monthly_get", {"code": "262A0", "year": 2024})
            if is_error:
                raise SystemExit(f"alnum get: {text}")
            status, body = _http_status_body(text)
            if status != 200 or not isinstance(body, dict) or body.get("code") != "262A0":
                raise SystemExit(f"alnum get body: {text}")
            paths = [(rec["method"], rec["path"]) for rec in recorded]
            if ("POST", "/api/v1/tables/262A0/2024/elements") not in paths:
                raise SystemExit(f"262A should hit canonical path: {paths}")
            if ("GET", "/api/v1/tables/262A0/2024") not in paths:
                raise SystemExit(f"262A0 get path missing: {paths}")
            for rec in recorded:
                host = rec["host"].split(":")[0]
                if host != "127.0.0.1":
                    raise SystemExit(f"non-loopback host: {rec}")
                if not rec["path"].startswith("/api/v1/tables"):
                    raise SystemExit(f"path not allowlisted: {rec}")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http


def test_monthly_ingest_tool() -> None:
    prev_http = os.environ.get("MCP_APP_HTTP")
    try:
        with _monthly_stub() as (port, recorded):
            os.environ["MCP_APP_HTTP"] = f"http://127.0.0.1:{port}"
            token = "test-token-for-ci-only"
            client = TestClient(m.create_app(token))

            before = len(recorded)
            for bad in ("abc", "12", "7203/../etc", "7203.0", "262A/../x"):
                text, is_error = m.call_tool(
                    "monthly_ingest",
                    {"code": bad, "months": [11, 12, 1], "rows": [{"element": "x", "values": {"11": 1}}]},
                )
                if not is_error:
                    raise SystemExit(f"ingest bad code {bad!r} should isError: {text}")
            if len(recorded) != before:
                raise SystemExit(f"ingest bad code must not hit HTTP: {recorded[before:]}")

            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "year": 2025,
                    "months": [11, 12, 1, 2, 3, 4, 5, 6, 7],
                    "rows": [{
                        "element": "全店 売上高",
                        "values": {"11": 125.8, "12": 124.7, "1": 125.9},
                        "unit": "yoy_index",
                    }],
                    "as_of": "2026-08-05",
                },
            )
            if is_error:
                raise SystemExit(f"monthly_ingest: {text}")
            status, body = _http_status_body(text)
            if status != 200 or not isinstance(body, dict) or body.get("code") != "92790":
                raise SystemExit(f"ingest body: {text}")

            r = _rpc(
                client,
                "tools/call",
                {
                    "name": "monthly_ingest",
                    "arguments": {
                        "code": "7203",
                        "months": [1, 2, 3],
                        "rows": [{"element": "売上高", "values": {"1": 1.0}, "unit": "number"}],
                        "as_of": "2026-08-05",
                    },
                },
                req_id=50,
            )
            rpc_text = r.json()["result"]["content"][0]["text"]
            if r.json()["result"]["isError"] or "HTTP 200" not in rpc_text:
                raise SystemExit(f"rpc monthly_ingest: {r.text}")

            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "262A",
                    "months": [11, 12, 1],
                    "rows": [{"element": "売上高", "values": {"11": 1.0}, "unit": "number"}],
                    "as_of": "2026-08-05",
                },
            )
            if is_error:
                raise SystemExit(f"alnum ingest: {text}")

            if not recorded:
                raise SystemExit("monthly_ingest recorded nothing")
            allowed = {
                ("POST", "/api/v1/tables/92790/ingest"),
                ("POST", "/api/v1/tables/72030/ingest"),
                ("POST", "/api/v1/tables/262A0/ingest"),
            }
            for rec in recorded:
                host = rec["host"].split(":")[0]
                if host != "127.0.0.1":
                    raise SystemExit(f"non-loopback host: {rec}")
                if (rec["method"], rec["path"]) not in allowed:
                    raise SystemExit(f"unexpected ingest request: {rec}")
                if "/202" in rec["path"] or rec["path"].count("/") != 5:
                    raise SystemExit(f"ingest path must be /api/v1/tables/{{code}}/ingest: {rec}")
                payload = json.loads(rec["body"].decode("utf-8")) if rec["body"] else {}
                if "year" in payload:
                    raise SystemExit(f"ingest body must not include year: {payload}")
                if "dry_run" in payload or "only_months" in payload:
                    raise SystemExit(f"omit dry_run/only_months must not send them: {payload}")
                if "months" not in payload or "rows" not in payload:
                    raise SystemExit(f"ingest body missing months/rows: {payload}")
                if rec["path"] == "/api/v1/tables/92790/ingest":
                    row0 = payload["rows"][0]
                    if row0.get("unit") != "yoy_index":
                        raise SystemExit(f"ingest must forward unit: {payload}")
                    if row0.get("values", {}).get("11") != 125.8:
                        raise SystemExit(f"ingest must not convert 125.8: {payload}")
            if not any(rec["path"] == "/api/v1/tables/92790/ingest" for rec in recorded):
                raise SystemExit("did not POST /api/v1/tables/92790/ingest")
            if not any(rec["path"] == "/api/v1/tables/262A0/ingest" for rec in recorded):
                raise SystemExit("did not POST /api/v1/tables/262A0/ingest")

            before_omit = len(recorded)
            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "months": [11, 12, 1],
                    "rows": [{"element": "x", "values": {"11": 1}, "unit": "number"}],
                },
            )
            if not is_error or "as_of is required" not in text:
                raise SystemExit(f"omit as_of should isError: {text}")
            if len(recorded) != before_omit:
                raise SystemExit(f"omit as_of must not hit HTTP: {recorded[before_omit:]}")

            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "months": [11, 12, 1],
                    "rows": [{"element": "x", "values": {"11": 1}}],
                    "as_of": "2026-08-05",
                },
            )
            if not is_error or "unit is required" not in text:
                raise SystemExit(f"ingest omit unit should isError: {text}")
            if len(recorded) != before_omit:
                raise SystemExit(f"ingest omit unit must not hit HTTP: {recorded[before_omit:]}")

            before_bad = len(recorded)
            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "months": [11, 12, 1],
                    "rows": [{"element": "x", "values": {"11": 1}, "unit": "percent"}],
                },
            )
            if not is_error:
                raise SystemExit(f"ingest invalid unit should isError: {text}")
            if len(recorded) != before_bad:
                raise SystemExit(f"ingest invalid unit must not hit HTTP: {recorded[before_bad:]}")

            before_dry = len(recorded)
            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "months": [7, 8],
                    "rows": [{"element": "全店", "values": {"7": 1.0}, "unit": "number"}],
                    "as_of": "2026-08-05",
                    "dry_run": True,
                    "only_months": ["2026-07"],
                },
            )
            if is_error:
                raise SystemExit(f"dry_run ingest: {text}")
            if len(recorded) != before_dry + 1:
                raise SystemExit(f"dry_run posts: {recorded[before_dry:]}")
            dry_body = json.loads(recorded[-1]["body"].decode("utf-8"))
            if dry_body.get("dry_run") is not True or dry_body.get("only_months") != ["2026-07"]:
                raise SystemExit(f"dry_run body: {dry_body}")
            if "year" in dry_body:
                raise SystemExit(f"dry_run must not send year: {dry_body}")

            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "months": [7, 8],
                    "rows": [{"element": "全店", "values": {"7": 1.0}, "unit": "number"}],
                    "as_of": "2026-08-05",
                    "dry_run": "yes",
                },
            )
            if not is_error or "dry_run must be a boolean" not in text:
                raise SystemExit(f"bad dry_run: {text}")
            if len(recorded) != before_dry + 1:
                raise SystemExit("bad dry_run must not hit HTTP")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http


def test_oauth_store_persists_and_purges() -> None:
    import json
    import time
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        path = Path(d) / ".mcp-oauth.json"
        store = m.OAuthStore(path)
        now = time.time()
        store.data["access"]["live"] = {"exp": now + 3600, "client_id": "c1"}
        store.data["access"]["dead"] = {"exp": now - 10, "client_id": "c1"}
        store.data["refresh"]["r-live"] = {"exp": now + 3600, "client_id": "c1"}
        store.codes["used"] = {"exp": now - 1}
        store._save()
        if not path.is_file():
            raise SystemExit("oauth store file was not written")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw["access"]["live"]["client_id"] != "c1":
            raise SystemExit(f"saved access: {raw}")
        if oct(path.stat().st_mode & 0o777) != "0o600":
            raise SystemExit(f"oauth file mode {oct(path.stat().st_mode)}")
        reloaded = m.OAuthStore(path)
        if "live" not in reloaded.data["access"] or "r-live" not in reloaded.data["refresh"]:
            raise SystemExit(f"reload missed tokens: {reloaded.data}")
        if "dead" not in reloaded.data["access"]:
            raise SystemExit("expired rows must still load before purge")
        reloaded.purge()
        if "dead" in reloaded.data["access"] or "used" in reloaded.codes:
            raise SystemExit(f"purge left expired: {reloaded.data} {reloaded.codes}")
        if "live" not in reloaded.data["access"] or "r-live" not in reloaded.data["refresh"]:
            raise SystemExit("purge must keep unexpired tokens")
        missing = m.OAuthStore(Path(d) / "absent.json")
        if missing.data != {"access": {}, "refresh": {}, "clients": {}}:
            raise SystemExit(f"missing file: {missing.data}")
        path.write_text("{not json", encoding="utf-8")
        broken = m.OAuthStore(path)
        if broken.data["access"]:
            raise SystemExit("bad json must start empty")


def test_configured_token_strips_env() -> None:
    prev = os.environ.get("MCP_TOKEN")
    try:
        os.environ["MCP_TOKEN"] = "  abc  "
        if m.configured_token() != "abc":
            raise SystemExit(f"strip: {m.configured_token()!r}")
        os.environ["MCP_TOKEN"] = ""
        if m.configured_token() != "":
            raise SystemExit("empty token")
        del os.environ["MCP_TOKEN"]
        if m.configured_token() != "":
            raise SystemExit("unset token")
    finally:
        if prev is None:
            os.environ.pop("MCP_TOKEN", None)
        else:
            os.environ["MCP_TOKEN"] = prev


def test_parse_ingest_months_and_industry() -> None:
    if m._parse_ingest_months([1, 12, 1]) != [1, 12, 1]:
        raise SystemExit("duplicate month wrap")
    try:
        m._parse_ingest_months([])
    except ValueError as exc:
        if "non-empty" not in str(exc):
            raise SystemExit(exc)
    else:
        raise SystemExit("empty months")
    try:
        m._parse_ingest_months("1-12")
    except ValueError:
        pass
    else:
        raise SystemExit("string months")
    if m.parse_monthly_industry(" 小売 ") != "小売":
        raise SystemExit("industry trim")
    try:
        m.parse_monthly_industry("")
    except ValueError:
        pass
    else:
        raise SystemExit("empty industry")
    try:
        m.parse_monthly_industry("x" * 51)
    except ValueError:
        pass
    else:
        raise SystemExit("industry too long")
    try:
        m.parse_monthly_industry(1)
    except ValueError:
        pass
    else:
        raise SystemExit("industry type")


def _mcp_status_line(text: str) -> str:
    prefix = f"{m.PORT} "
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    raise SystemExit(f"missing {m.PORT} line: {text!r}")


def test_service_status_skips_self_mcp_curl() -> None:
    calls: list[list[str]] = []
    orig = m.run_cmd

    def fake(argv, timeout=15):
        calls.append(list(argv))
        if argv and argv[0] == "curl":
            return "200"
        return "active"

    m.run_cmd = fake
    try:
        t0 = time.perf_counter()
        text, is_error = m.call_tool("service_status", {})
        elapsed = time.perf_counter() - t0
        if is_error:
            raise SystemExit(f"service_status isError: {text!r}")
        if elapsed >= 1.0:
            raise SystemExit(f"service_status too slow: {elapsed:.3f}s")
        mcp_line = _mcp_status_line(text)
        if mcp_line.split()[1] == "000":
            raise SystemExit(f"MCP still 000: {mcp_line!r}")
        if not mcp_line.startswith(f"{m.PORT} ok"):
            raise SystemExit(f"expected ok, got {mcp_line!r}")
        curl_urls = [a for argv in calls if argv and argv[0] == "curl" for a in argv if str(a).startswith("http")]
        if any("/mcp" in url or f":{m.PORT}" in url for url in curl_urls):
            raise SystemExit(f"must not curl MCP itself: {curl_urls!r}")
        if "http://127.0.0.1/" not in curl_urls or "http://127.0.0.1:8000/" not in curl_urls:
            raise SystemExit(f"still curl 80/8000: {curl_urls!r}")
    finally:
        m.run_cmd = orig


def test_service_status_live_server_does_not_timeout() -> None:
    """uvicorn workers=1 で tools/call しても自分への curl 待ちで 3 秒止まらないこと。"""
    import socket
    import threading
    import urllib.error
    import urllib.request

    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    orig = m.run_cmd

    def fake(argv, timeout=15):
        if argv and argv[0] == "curl":
            url = next((a for a in argv if isinstance(a, str) and a.startswith("http")), "")
            if "/mcp" in url or f":{m.PORT}/mcp" in url:
                # 回帰すると同一ワーカーへ同期 GET してデッドロックする
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=3)  # nosec B310
                except Exception:
                    return "000"
                return "200"
            return "200"
        return "active"

    m.run_cmd = fake
    config = uvicorn.Config(
        m.create_app("test-token-for-ci-only"),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 8
        while not getattr(server, "started", False):
            if time.time() > deadline:
                raise SystemExit("uvicorn did not start")
            time.sleep(0.05)
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "service_status", "arguments": {}},
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/mcp",
            data=body,
            headers={
                "Authorization": "Bearer test-token-for-ci-only",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:  # nosec B310
                payload = json.loads(resp.read())
        except (TimeoutError, urllib.error.URLError) as exc:
            raise SystemExit(f"service_status blocked on self-curl: {exc}") from exc
        elapsed = time.perf_counter() - t0
        if elapsed >= 1.5:
            raise SystemExit(f"service_status still slow: {elapsed:.3f}s")
        text = payload["result"]["content"][0]["text"]
        mcp_line = _mcp_status_line(text)
        if "000" in mcp_line.split(" ", 1)[1]:
            raise SystemExit(f"MCP still 000: {mcp_line!r}")
        if not mcp_line.startswith(f"{m.PORT} ok"):
            raise SystemExit(f"expected ok, got {mcp_line!r}")
    finally:
        server.should_exit = True
        m.run_cmd = orig


def test_rpc_error_and_env_flag(tmp_path: Path) -> None:
    err = m.rpc_error(7, -32600, "invalid jsonrpc")
    if err != {"jsonrpc": "2.0", "id": 7, "error": {"code": -32600, "message": "invalid jsonrpc"}}:
        raise SystemExit(f"rpc_error: {err}")
    prev = os.environ.get("MCP_APP_DIR")
    prev_key = os.environ.get("JQUANTS_API_KEY")
    os.environ.pop("JQUANTS_API_KEY", None)
    os.environ["MCP_APP_DIR"] = str(tmp_path)
    try:
        if m.env_flag("JQUANTS_API_KEY") != "missing (.env unreadable)":
            raise SystemExit(f"missing env: {m.env_flag('JQUANTS_API_KEY')!r}")
        (tmp_path / ".env").write_text('JQUANTS_API_KEY="secret"\n', encoding="utf-8")
        if m.env_flag("JQUANTS_API_KEY") != "set":
            raise SystemExit("dotenv set")
        os.environ["JQUANTS_API_KEY"] = "live"
        if m.env_flag("JQUANTS_API_KEY") != "set":
            raise SystemExit("env set beats dotenv")
        os.environ.pop("JQUANTS_API_KEY", None)
        (tmp_path / ".env").write_text("JQUANTS_API_KEY=\n", encoding="utf-8")
        if m.env_flag("JQUANTS_API_KEY") != "empty":
            raise SystemExit("empty dotenv value")
    finally:
        if prev is None:
            os.environ.pop("MCP_APP_DIR", None)
        else:
            os.environ["MCP_APP_DIR"] = prev
        if prev_key is None:
            os.environ.pop("JQUANTS_API_KEY", None)
        else:
            os.environ["JQUANTS_API_KEY"] = prev_key


def test_tool_app_logs_joins_file_and_journal(tmp_path: Path) -> None:
    log = tmp_path / "stock-analyzer.log"
    log.write_text("keep\nERROR boom\n", encoding="utf-8")
    prev = os.environ.get("MCP_APP_DIR")
    os.environ["MCP_APP_DIR"] = str(tmp_path)
    try:
        orig_journal = m.journal
        orig_pick = m.pick_log
        m.journal = lambda unit, since, lines, grep, until=None: f"journal-{unit}-{since}-{grep}"
        m.pick_log = lambda name: log
        out = m.tool_app_logs({"lines": 20, "grep": "ERROR"})
        if "ERROR boom" not in out or "keep" in out:
            raise SystemExit(f"file filter: {out!r}")
        if "journal-stock-analyzer-1 hours ago-ERROR" not in out:
            raise SystemExit(f"journal: {out!r}")
        try:
            m.tool_app_logs({"since": "--user=root"})
        except ValueError:
            pass
        else:
            raise SystemExit("bad since")
    finally:
        m.journal = orig_journal
        m.pick_log = orig_pick
        if prev is None:
            os.environ.pop("MCP_APP_DIR", None)
        else:
            os.environ["MCP_APP_DIR"] = prev


def test_tool_app_logs_window_reads_rotated(tmp_path: Path) -> None:
    log = tmp_path / "stock-analyzer.log"
    (tmp_path / "stock-analyzer.log.2.gz").write_bytes(gzip.compress(
        b'INFO:     1.1.1.1:0 - "GET /before-log-config HTTP/1.0" 200 OK\n'
        b'2026-10-03 23:59:59 INFO:     1.1.1.1:0 - "GET /too-old HTTP/1.0" 200 OK\n'
    ))
    (tmp_path / "stock-analyzer.log.1").write_text(
        "2026-10-04 09:00:00 ERROR:    Exception in ASGI application\n"
        "Traceback (most recent call last):\n"
        '  File "app/api.py", line 1, in boom\n'
        "ValueError: boom\n"
        '2026-10-04 09:00:01 INFO:     1.1.1.1:0 - "GET /api/screener HTTP/1.0" 500 Internal Server Error\n',
        encoding="utf-8",
    )
    log.write_text('2026-10-05 00:00:00 INFO:     1.1.1.1:0 - "GET /today HTTP/1.0" 200 OK\n', encoding="utf-8")
    orig = (m.journal, m.pick_log, m._jst_now)
    calls: list[tuple[str, str | None]] = []

    def _journal(unit, since, lines, grep, until=None):
        calls.append((since, until))
        return "journal"

    try:
        m.journal = _journal
        m.pick_log = lambda name: tmp_path / name
        m._jst_now = lambda: datetime(2026, 10, 5, 12, 0, 0, tzinfo=m.JST).replace(tzinfo=None)
        if "until" not in m.TOOLS["app_logs"][1]["properties"]:
            raise SystemExit("app_logs schema missing until")
        text, is_error = m.call_tool("app_logs", {"since": "yesterday", "until": "today"})
        if is_error:
            raise SystemExit(f"yesterday app: {text!r}")
        body = text.split("===", 2)[2].split("=== journal", 1)[0]
        expect = [
            "2026-10-04 09:00:00 ERROR:    Exception in ASGI application",
            "Traceback (most recent call last):",
            '  File "app/api.py", line 1, in boom',
            "ValueError: boom",
            '2026-10-04 09:00:01 INFO:     1.1.1.1:0 - "GET /api/screener HTTP/1.0" 500 Internal Server Error',
        ]
        if body.strip() != "\n".join(expect):
            raise SystemExit(f"app window: {body!r}")
        if calls[-1] != ("2026-10-04 00:00:00", "2026-10-05 00:00:00"):
            raise SystemExit(f"journal window: {calls[-1]!r}")
        if "until 2026-10-05 00:00:00" not in text:
            raise SystemExit(f"journal label: {text!r}")
        tb, _ = m.call_tool("app_logs", {"since": "yesterday", "grep": "ValueError"})
        if "ValueError: boom" not in tb or "/today" in tb:
            raise SystemExit(f"grep on continuation: {tb!r}")
    finally:
        m.journal, m.pick_log, m._jst_now = orig


def test_tool_nginx_logs_access_timed_and_redacted(tmp_path: Path) -> None:
    access = tmp_path / "stock-analyzer.access.log"
    access.write_text(
        '1.2.3.4 [02/Oct/2026:12:00:00 +0900] "GET /api/v1/tables HTTP/1.1" 200 9 rt=4.282 urt=4.281 "ua"\n'
        '1.2.3.4 [02/Oct/2026:12:00:01 +0900] "GET /app HTTP/1.1" 200 9 rt=0.003 urt=0.002 "ua"\n'
        '1.2.3.4 [02/Oct/2026:12:00:02 +0900] "GET /oauth/callback?code=abc123&state=s1&x=1 HTTP/1.1" '
        '302 0 rt=1.500 urt=1.499 "ua"\n',
        encoding="utf-8",
    )
    orig = (m.journal, m.NGINX_ACCESS_LOG, m.NGINX_ERROR_LOG)
    try:
        m.journal = lambda unit, since, lines, grep: "journal"
        m.NGINX_ACCESS_LOG = access
        m.NGINX_ERROR_LOG = tmp_path / "missing-error.log"
        out = m.tool_nginx_logs({"grep": "rt=[1-9]"})
        if "/api/v1/tables" not in out or "GET /app " in out:
            raise SystemExit(f"slow filter: {out!r}")
        if "abc123" in out or "s1&" in out:
            raise SystemExit(f"oauth query leaked: {out!r}")
        if "code=<redacted>&state=<redacted>&x=1" not in out:
            raise SystemExit(f"redact_url: {out!r}")
    finally:
        m.journal, m.NGINX_ACCESS_LOG, m.NGINX_ERROR_LOG = orig


def test_tool_nginx_logs_window_reads_rotated(tmp_path: Path) -> None:
    nginx = tmp_path / "nginx"
    nginx.mkdir()
    access = nginx / "stock-analyzer.access.log"
    error = nginx / "error.log"
    (nginx / "stock-analyzer.access.log.2.gz").write_bytes(gzip.compress(
        b'9.9.9.1 [03/Oct/2026:23:59:59 +0900] "GET /.env HTTP/1.1" 401 0 rt=0.000 urt=- "ua"\n'
        b'9.9.9.2 [04/Oct/2026:00:00:00 +0900] "GET /.git/config HTTP/1.1" 401 0 rt=0.000 urt=- "ua"\n'
    ))
    (nginx / "stock-analyzer.access.log.1").write_text(
        '9.9.9.3 [04/Oct/2026:12:00:00 +0900] "GET /oauth/callback?code=abc123 HTTP/1.1" 302 0 rt=0.001 urt=0.001 "ua"\n'
        '9.9.9.4 [04/Oct/2026:06:00:00 +0000] "GET /wp-login.php HTTP/1.1" 401 0 rt=0.000 urt=- "ua"\n',
        encoding="utf-8",
    )
    access.write_text(
        '9.9.9.5 [05/Oct/2026:00:00:00 +0900] "GET /today HTTP/1.1" 401 0 rt=0.000 urt=- "ua"\n',
        encoding="utf-8",
    )
    (nginx / "error.log.1").write_text(
        "2026/10/04 09:00:00 [error] 1#1: *1 access forbidden by rule, client: 9.9.9.6\n"
        "  continuation without stamp\n",
        encoding="utf-8",
    )
    error.write_text("2026/10/05 01:00:00 [error] 1#1: *2 today\n", encoding="utf-8")
    (nginx / "unrelated.log.1").write_text("2026/10/04 10:00:00 [error] stranger\n", encoding="utf-8")
    orig = (m.journal, m.NGINX_ACCESS_LOG, m.NGINX_ERROR_LOG, m._jst_now)
    calls: list[tuple[str, str | None]] = []

    def _journal(unit, since, lines, grep, until=None):
        calls.append((since, until))
        return "journal"

    try:
        m.journal = _journal
        m.NGINX_ACCESS_LOG = access
        m.NGINX_ERROR_LOG = error
        m._jst_now = lambda: datetime(2026, 10, 5, 12, 0, 0, tzinfo=m.JST).replace(tzinfo=None)
        props = m.TOOLS["nginx_logs"][1]["properties"]
        if "until" not in props:
            raise SystemExit("nginx_logs schema missing until")
        live = m.tool_nginx_logs({})
        if "9.9.9.3" in live or "/today" not in live:
            raise SystemExit(f"no window reads live only: {live!r}")
        text, is_error = m.call_tool("nginx_logs", {"since": "yesterday", "until": "today"})
        if is_error:
            raise SystemExit(f"yesterday nginx: {text!r}")
        acc = text.split("stock-analyzer.access.log", 1)[1]
        for want in ("9.9.9.2", "code=<redacted>", "9.9.9.4"):
            if want not in acc:
                raise SystemExit(f"window missing {want}: {acc!r}")
        if not acc.index("9.9.9.2") < acc.index("9.9.9.3") < acc.index("9.9.9.4"):
            raise SystemExit(f"window not sorted by JST: {acc!r}")
        for leak in ("9.9.9.1", "/today", "abc123"):
            if leak in acc:
                raise SystemExit(f"window leaked {leak}: {acc!r}")
        err = text.split("error.log ===", 1)[1].split("===", 1)[0]
        if "9.9.9.6" not in err or "continuation" not in err or "today" in err or "stranger" in err:
            raise SystemExit(f"error window: {err!r}")
        if calls[-1] != ("2026-10-04 00:00:00", "2026-10-05 00:00:00"):
            raise SystemExit(f"journal window: {calls[-1]!r}")
        older = m.tool_nginx_logs({"until": "today", "grep": "wp-login"})
        if "9.9.9.4" not in older:
            raise SystemExit(f"until only: {older!r}")
        os.utime(nginx / "stock-analyzer.access.log.2.gz", (0, 0))
        skipped = m.tool_nginx_logs({"since": "2026-10-04", "grep": "git"})
        if "9.9.9.2" in skipped:
            raise SystemExit(f"stale mtime must skip file: {skipped!r}")
        bad, bad_err = m.call_tool("nginx_logs", {"since": "today", "until": "yesterday"})
        if not bad_err or "until must be after since" not in bad:
            raise SystemExit(f"bad window: {bad!r}")
    finally:
        m.journal, m.NGINX_ACCESS_LOG, m.NGINX_ERROR_LOG, m._jst_now = orig


def _period_block(text: str, period: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(period):
            start = index
            break
    if start is None:
        raise SystemExit(f"missing period {period}: {text}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if len(line) >= 10 and line[4] == "-" and line[:4].isdigit() and line[7] == "-":
            end = index
            break
    return "\n".join(lines[start:end])


def _item_cells(block: str, item: str) -> list[str]:
    for line in block.splitlines():
        parts = line.split()
        if parts and parts[0] == item:
            return parts
    raise SystemExit(f"missing {item} in {block}")


def _source_line(block: str, source: str) -> str:
    prefix = f"  {source}"
    for line in block.splitlines():
        if line.startswith(prefix):
            return line
    raise SystemExit(f"missing {source} line in {block}")


def test_xbrl_compare_tools() -> None:
    from tempfile import TemporaryDirectory

    token = "test-token-for-ci-only"
    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        import sqlite3

        conn = sqlite3.connect(dbp)
        conn.executescript(
            """
            CREATE TABLE stocks (
                code TEXT PRIMARY KEY, name TEXT, market TEXT, sector33 TEXT, scale_category TEXT
            );
            CREATE TABLE statements (
                disclosure_no TEXT PRIMARY KEY,
                code TEXT,
                disclosed_date TEXT,
                disclosed_time TEXT,
                type_of_period TEXT,
                type_of_document TEXT,
                period_end TEXT,
                net_sales REAL,
                operating_profit REAL,
                ordinary_profit REAL,
                profit REAL,
                eps REAL
            );
            CREATE TABLE xbrl_filings (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                period_type TEXT,
                submitted_at TEXT NOT NULL,
                accounting_standard TEXT,
                is_amendment INTEGER NOT NULL DEFAULT 0,
                parse_status TEXT NOT NULL
            );
            CREATE TABLE xbrl_std_items (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                statement TEXT NOT NULL,
                section TEXT,
                label_ja TEXT NOT NULL,
                display_order INTEGER NOT NULL
            );
            CREATE TABLE xbrl_financials (
                id INTEGER PRIMARY KEY,
                filing_id INTEGER NOT NULL,
                std_item_id INTEGER NOT NULL,
                is_consolidated INTEGER NOT NULL,
                period_end TEXT NOT NULL,
                value REAL NOT NULL,
                value_kind TEXT NOT NULL,
                range_side TEXT NOT NULL
            );
            INSERT INTO stocks VALUES ('72030','トヨタ','プライム','輸送用機器','TOPIX Large70');
            INSERT INTO xbrl_std_items VALUES
                (6,'total_assets','BS','assets','資産合計',40),
                (1,'net_sales','PL','operating','売上高',100),
                (2,'operating_income','PL','operating','営業利益',140),
                (3,'ordinary_income','PL','nonoperating','経常利益',150),
                (4,'net_income','PL','tax','親会社株主に帰属する当期純利益',170),
                (5,'eps_basic','PL','per_share','1株当たり当期純利益',240),
                (7,'dps','OTHER','dividend','1株当たり配当金',260);
            """
        )
        conn.executemany(
            "INSERT INTO statements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("Z-OLD", "72030", "2025-05-01", "15:00", "FY",
                 "FinancialStatements_Consolidated_FY", "2025-03-31",
                 7_777_000_000, None, None, None, None),
                ("A-NEW", "72030", "2025-05-15", "15:30", "FY",
                 "FinancialStatements_Consolidated_FY", "2025-03-31",
                 100_000_000, 1_000_000_000, 500_000_000, -500_000_000, 100.0),
                ("F-REV", "72030", "2025-06-01", "10:00", "FY",
                 "EarnForecastRevision", "2025-03-31",
                 None, None, None, None, None),
                ("B-STMT", "72030", "2024-05-10", "15:00", "FY",
                 "FinancialStatements", "2024-03-31",
                 2_222_000_000, None, None, None, None),
                ("C-STMT", "72030", "2025-02-10", "15:00", "3Q",
                 "FinancialStatements", "2024-12-31",
                 3_000_000_000, None, None, None, None),
                ("F2023", "72030", "2023-05-10", "15:00", "FY",
                 "FinancialStatements", "2023-03-31", 10, None, None, None, None),
                ("F2022", "72030", "2022-05-10", "15:00", "FY",
                 "FinancialStatements", "2022-03-31", 10, None, None, None, None),
                ("F2021", "72030", "2021-05-10", "15:00", "FY",
                 "FinancialStatements", "2021-03-31", 10, None, None, None, None),
                ("F2020", "72030", "2020-05-10", "15:00", "FY",
                 "FinancialStatements", "2020-03-31", 10, None, None, None, None),
                ("F2017", "72030", "2017-05-10", "15:00", "FY",
                 "FinancialStatements", "2017-03-31", 10, None, None, None, None),
            ],
        )
        conn.executemany(
            "INSERT INTO xbrl_filings VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (1, "72030", "tdnet", "TD-A", "FY", "2025-05-15 15:30:00", "JPGAAP", 0, "parsed"),
                (2, "72030", "edinet", "S100OLD", "FY", "2025-06-01 09:00:00", "IFRS", 0, "parsed"),
                (3, "72030", "edinet", "S100AMND", "FY", "2025-06-25 09:12:00", "IFRS", 1, "parsed"),
                (4, "72030", "edinet", "S100PEND", "FY", "2025-07-01 09:00:00", "IFRS", 0, "pending"),
                (5, "72030", "edinet", "S100SOLO", "FY", "2024-06-20 09:00:00", "JPGAAP", 0, "parsed"),
                (6, "72030", "tdnet", "TD-B", "FY", "2024-05-10 15:00:00", "JPGAAP", 0, "parsed"),
                (7, "72030", "tdnet", "TD-C", "3Q", "2025-02-10 15:00:00", "JPGAAP", 0, "parsed"),
                (8, "72030", "edinet", "S100ONLY", "FY", "2023-11-01 09:00:00", "JPGAAP", 0, "parsed"),
                (9, "72030", "tdnet", "081220260930543461", "FY", "2026-09-30 17:00:00", "JPGAAP", 1, "parsed"),
            ],
        )
        conn.executemany(
            "INSERT INTO xbrl_financials (filing_id, std_item_id, is_consolidated, period_end, value, value_kind, range_side) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (1, 1, 1, "2025-03-31", 100_400_000, "actual", "point"),
                (1, 1, 0, "2025-03-31", 7, "actual", "point"),
                (1, 2, 1, "2025-03-31", 1_000_000_000, "actual", "point"),
                (1, 3, 1, "2025-03-31", 500_000_000, "actual", "point"),
                (1, 4, 1, "2025-03-31", -500_000_000, "actual", "point"),
                (1, 5, 1, "2025-03-31", 100.00, "actual", "point"),
                (1, 6, 1, "2025-03-31", 40_000_000_000, "actual", "point"),
                (1, 7, 1, "2025-03-31", 999, "actual", "point"),
                (2, 1, 1, "2025-03-31", 42_000_000_000, "actual", "point"),
                (3, 1, 1, "2025-03-31", 100_000_000, "actual", "point"),
                (3, 2, 1, "2025-03-31", 1_010_000_000, "actual", "point"),
                (3, 4, 1, "2025-03-31", 500_000_000, "actual", "point"),
                (3, 5, 1, "2025-03-31", 100.20, "actual", "point"),
                (3, 6, 1, "2025-03-31", 40_000_000_000, "actual", "point"),
                (3, 1, 1, "2025-03-31", 123_456_789, "forecast", "point"),
                (3, 1, 1, "2025-03-31", 555_000_000, "actual", "lower"),
                (4, 1, 1, "2025-03-31", 99_000_000_000, "actual", "point"),
                (5, 1, 0, "2024-03-31", 2_222_000_000, "actual", "point"),
                (6, 1, 1, "2024-03-31", 2_222_000_000, "actual", "point"),
                (7, 1, 1, "2024-12-31", 3_000_000_000, "actual", "point"),
                (8, 1, 1, "2023-09-30", 8_000_000_000, "actual", "point"),
                (9, 1, 1, "2023-03-31", 99_000, "actual", "point"),
            ],
        )
        conn.commit()
        conn.close()
        try:
            text = m.tool_xbrl_compare({"code": "7203"})
            if "72030" not in text or "トヨタ" not in text or "periods=8" not in text:
                raise SystemExit(f"header: {text}")
            if "2017-03-31" in text or "2020-03-31" not in text:
                raise SystemExit(f"newest 8: {text}")
            if "dps" in text or "S100OLD" in text or "S100PEND" in text or "Z-OLD" in text or "F-REV" in text:
                raise SystemExit(f"dropped rows leaked: {text}")
            for absent in ("7,777,000,000", "42,000,000,000", "99,000,000,000", "123,456,789", "555,000,000"):
                if absent in text:
                    raise SystemExit(f"shadow value {absent}: {text}")

            fy = _period_block(text, "2025-03-31")
            if _item_cells(fy, "net_sales")[1:] != [
                "100,000,000", "100,400,000", "100,000,000", "OK", "OK", "DIFF",
            ]:
                raise SystemExit(f"net_sales rounding: {fy}")
            if _item_cells(fy, "operating_income")[1:] != [
                "1,000,000,000", "1,000,000,000", "1,010,000,000", "OK", "DIFF", "DIFF",
            ]:
                raise SystemExit(f"operating rel: {fy}")
            if _item_cells(fy, "ordinary_income")[1:] != [
                "500,000,000", "500,000,000", "-", "OK", "MISSING", "MISSING",
            ]:
                raise SystemExit(f"ordinary missing: {fy}")
            if _item_cells(fy, "net_income")[1:] != [
                "-500,000,000", "-500,000,000", "500,000,000", "OK", "DIFF", "DIFF",
            ]:
                raise SystemExit(f"sign flip: {fy}")
            if _item_cells(fy, "eps_basic")[1:] != [
                "100.00", "100.00", "100.20", "OK", "DIFF", "DIFF",
            ]:
                raise SystemExit(f"eps: {fy}")
            if _item_cells(fy, "total_assets")[1:] != [
                "-", "40,000,000,000", "40,000,000,000", "-", "-", "OK",
            ]:
                raise SystemExit(f"xbrl-only: {fy}")
            td_line = _source_line(fy, "tdnet")
            ed_line = _source_line(fy, "edinet")
            st_line = _source_line(fy, "statements")
            if "consolidated" not in td_line or " 7 " in td_line:
                raise SystemExit(f"tdnet consolidated: {td_line}")
            if "S100AMND" not in ed_line or "AMENDED" not in ed_line or "*" not in ed_line:
                raise SystemExit(f"amendment mark: {ed_line}")
            if "A-NEW" not in st_line or "AMENDED" not in st_line:
                raise SystemExit(f"statements correction: {st_line}")

            solo = _period_block(text, "2024-03-31")
            if "standalone" not in _source_line(solo, "edinet"):
                raise SystemExit(f"standalone filing: {solo}")
            if _item_cells(solo, "net_sales")[1:] != [
                "2,222,000,000", "2,222,000,000", "2,222,000,000", "OK", "OK", "OK",
            ]:
                raise SystemExit(f"standalone value: {solo}")

            empty_ed = _period_block(text, "2024-12-31")
            if "(no filing)" not in _source_line(empty_ed, "edinet"):
                raise SystemExit(f"no filing: {empty_ed}")
            if "MISSING" in empty_ed:
                raise SystemExit(f"no filing must not be MISSING: {empty_ed}")
            if _item_cells(empty_ed, "net_sales")[-3:] != ["OK", "-", "-"]:
                raise SystemExit(f"no filing verdicts: {empty_ed}")

            pinned = m.tool_xbrl_compare({"code": "72030", "period_end": "2017-03-31"})
            if "2017-03-31" not in pinned or "2025-03-31" in pinned or "periods=1" not in pinned:
                raise SystemExit(f"period_end pin: {pinned}")
            for key in ("operating_profit", "operating_income"):
                narrowed = m.tool_xbrl_compare({"code": "7203", "period_end": "2025-03-31", "items": [key]})
                if "operating_income" not in narrowed or "net_sales" in narrowed:
                    raise SystemExit(f"items {key}: {narrowed}")
            unknown, unknown_err = m.call_tool("xbrl_compare", {"code": "7203", "items": ["not_a_real_item"]})
            if not unknown_err or "unknown items" not in unknown:
                raise SystemExit(f"unknown item: {unknown!r}")

            summary = m.tool_xbrl_diff_summary({})
            if "paired=2" not in summary or "xbrl_periods=3" not in summary or "xbrl_only=1" not in summary:
                raise SystemExit(f"pair counts: {summary}")
            if "match=" not in summary or "accounting_standard" not in summary or "IFRS" not in summary:
                raise SystemExit(f"rates: {summary}")
            if "sector33" not in summary or "輸送用機器" not in summary:
                raise SystemExit(f"sector: {summary}")
            top = summary.split("top diffs", 1)[1].strip().splitlines()
            first = top[0]
            for need in ("net_income", "-500,000,000", "500,000,000", "A-NEW", "S100AMND", "DIFF"):
                if need not in first:
                    raise SystemExit(f"top diff missing {need}: {first}")

            td_sum, td_err = m.call_tool("xbrl_diff_summary", {"source": "tdnet"})
            if td_err or "source=tdnet" not in td_sum or "paired=" not in td_sum:
                raise SystemExit(f"tdnet summary: {td_sum!r}")
            if "restated=1" not in td_sum or "99,000" in td_sum:
                raise SystemExit(f"tdnet restatement must not count as DIFF: {td_sum!r}")
            if "restated=0" not in summary:
                raise SystemExit(f"edinet restated: {summary}")
            bad_source, bad_source_err = m.call_tool("xbrl_diff_summary", {"source": "statements"})
            if not bad_source_err or "source must be" not in bad_source:
                raise SystemExit(f"bad source: {bad_source!r}")
            bad_limit, bad_limit_err = m.call_tool("xbrl_diff_summary", {"limit": "abc"})
            if not bad_limit_err or "limit" not in bad_limit:
                raise SystemExit(f"bad limit: {bad_limit!r}")

            client = TestClient(m.create_app(token))
            listed = _rpc(client, "tools/list", req_id=70)
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            if "xbrl_compare" not in names or "xbrl_diff_summary" not in names:
                raise SystemExit(f"tools/list: {names}")
            compared = _rpc(
                client, "tools/call",
                {"name": "xbrl_compare", "arguments": {"code": "7203", "period_end": "2025-03-31"}},
                req_id=71,
            )
            body = compared.json()["result"]
            if body["isError"] or "S100AMND" not in body["content"][0]["text"]:
                raise SystemExit(f"tools/call compare: {body}")
            summed = _rpc(client, "tools/call", {"name": "xbrl_diff_summary", "arguments": {}}, req_id=72)
            sum_body = summed.json()["result"]
            if sum_body["isError"] or "paired=2" not in sum_body["content"][0]["text"]:
                raise SystemExit(f"tools/call summary: {sum_body}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def main() -> None:
    from tempfile import TemporaryDirectory

    test_redact()
    test_since_and_grep()
    with TemporaryDirectory() as d:
        test_tail_grep(Path(d))
        test_pick_log(Path(d) / "pick")
        test_batch_logs_job_file(Path(d) / "jobs")
        test_batch_logs_migrate_r2_share(Path(d) / "migrate")
        test_batch_logs_window(Path(d) / "window")
    test_http_auth_and_tools()
    test_service_status_skips_self_mcp_curl()
    test_service_status_live_server_does_not_timeout()
    test_oauth()
    test_oauth_store_persists_and_purges()
    test_configured_token_strips_env()
    test_parse_ingest_months_and_industry()
    with TemporaryDirectory() as d:
        base = Path(d)
        test_rpc_error_and_env_flag(base)
        logs = base / "applog"
        logs.mkdir()
        test_tool_app_logs_joins_file_and_journal(logs)
        app_window = base / "appwindow"
        app_window.mkdir()
        test_tool_app_logs_window_reads_rotated(app_window)
        test_tool_nginx_logs_access_timed_and_redacted(logs)
        test_tool_nginx_logs_window_reads_rotated(logs)
    test_db_tools()
    test_db_stats_approx_timeout_and_stat1()
    test_timed_fetch_progress_interrupt()
    test_db_query_and_schema()
    test_db_query_timeout_value_and_description()
    test_db_query_audit_line_format()
    test_db_query_budget_interrupts_after_timeout_s()
    test_db_query_explain()
    test_xbrl_compare_tools()
    test_fx_tools()
    test_monthly_tools()
    test_monthly_ingest_tool()
    print("ok: log mcp")


if __name__ == "__main__":
    main()

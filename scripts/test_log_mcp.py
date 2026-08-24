"""MCP サーバの認証・JSON-RPC・入力バリデーション。systemd は不要。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")
os.environ["MCP_APP_DIR"] = str(ROOT)

from starlette.testclient import TestClient  # noqa: E402

import log_mcp as m  # noqa: E402


def _rpc(client: TestClient, method: str, params: dict | None = None, token: str = "test-token-for-ci-only", req_id: int = 1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


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
    expect = {"service_status", "app_logs", "batch_logs", "nginx_logs"}
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
    if r.status_code != 200 or "stock-analyzer-logs" not in r.text:
        raise SystemExit(f"GET /mcp: {r.status_code} {r.text}")

    r = _rpc(client, "initialize", token="wrong-token")
    if r.status_code != 401:
        raise SystemExit("wrong token should 401")

    r = _rpc(client, "tools/call", {"name": "batch_logs", "arguments": {}}, req_id=5)
    sse = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "ping"},
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
    )
    if "text/event-stream" not in sse.headers.get("content-type", ""):
        raise SystemExit(f"SSE content-type: {sse.headers.get('content-type')}")
    if "event: message" not in sse.text:
        raise SystemExit(f"SSE body: {sse.text!r}")
    _ = r, json  # silence unused if ping path changes


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

    if m.redirect_ok("https://evil.example/cb"):
        raise SystemExit("redirect_ok allowed evil host")
    if not m.redirect_ok("https://grok.com/oauth/callback"):
        raise SystemExit("redirect_ok rejected grok.com")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_since_and_grep()
    with TemporaryDirectory() as d:
        test_tail_grep(Path(d))
    test_http_auth_and_tools()
    test_oauth()
    print("ok: log mcp")


if __name__ == "__main__":
    main()

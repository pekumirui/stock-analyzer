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


def main() -> None:
    from tempfile import TemporaryDirectory

    test_since_and_grep()
    with TemporaryDirectory() as d:
        test_tail_grep(Path(d))
    test_http_auth_and_tools()
    print("ok: log mcp")


if __name__ == "__main__":
    main()

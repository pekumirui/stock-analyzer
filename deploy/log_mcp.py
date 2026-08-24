"""Read-only Streamable HTTP MCP for VPS logs.

No extra deps: Starlette ships with FastAPI. Listens on 127.0.0.1:8010/mcp.
Arbitrary shell is not exposed. journalctl units and log paths are allowlisted.
"""
from __future__ import annotations

import json
import os
import re
import hmac
import secrets
import subprocess
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

def app_dir() -> Path:
    return Path(os.environ.get("MCP_APP_DIR") or Path(__file__).resolve().parent.parent)


NGINX_ERROR_LOG = Path("/var/log/nginx/error.log")
HOST = os.environ.get("MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_PORT", "8010"))
MAX_LINES = 500
DEFAULT_LINES = 200
SERVER_NAME = "stock-analyzer-logs"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
SINCE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
SECRET_RE = re.compile(
    r"(MCP_TOKEN|JQUANTS_API_KEY|BASIC_AUTH_PASSWORD|api[_-]?key|password)\s*[:=]\s*\S+",
    re.I,
)
GREP_RE = re.compile(r"^[^\n\r\x00]{1,120}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
SHORT_SINCE_RE = re.compile(r"^(\d+)([smhd])$", re.I)


def redact(text: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1) + "=<redacted>", text)


def parse_since(raw: str | None) -> str:
    s = (raw or "1h").strip()
    if not s:
        s = "1h"
    low = s.lower()
    if low in ("today", "yesterday"):
        return low
    m = SHORT_SINCE_RE.fullmatch(s)
    if m:
        n, u = m.group(1), m.group(2).lower()
        return f"{n} {SINCE_UNITS[u]} ago"
    if ISO_RE.fullmatch(s):
        return s.replace("T", " ")
    raise ValueError("invalid since. use 1h, 30m, today, yesterday, or YYYY-MM-DD")


def clamp_lines(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_LINES
    return max(1, min(n, MAX_LINES))


def parse_grep(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if not GREP_RE.fullmatch(s):
        raise ValueError("invalid grep (1-120 chars, no newlines)")
    if s.startswith("-"):
        raise ValueError("grep must not start with -")
    return s


def extract_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if auth and not auth.lower().startswith("basic "):
        return auth.strip()
    header = (request.headers.get("x-mcp-token") or "").strip()
    if header:
        return header
    return (request.query_params.get("token") or "").strip()


def token_ok(got: str, expected: str) -> bool:
    if not expected:
        return False
    a = got.encode("utf-8")
    b = expected.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def run_cmd(argv: list[str], timeout: int = 15) -> str:
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return f"(command not found: {argv[0]})"
    except subprocess.TimeoutExpired:
        return f"(timeout: {' '.join(argv[:3])})"
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or f"(exit {proc.returncode}, empty output)"


def tail_grep(path: Path, lines: int, grep: str | None) -> str:
    if not path.is_file():
        return f"(missing {path})"
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        return f"(cannot read {path}: {exc})"
    rows = text.splitlines()
    if grep:
        try:
            rx = re.compile(grep)
            rows = [ln for ln in rows if rx.search(ln)]
        except re.error:
            rows = [ln for ln in rows if grep in ln]
    rows = rows[-lines:]
    return "\n".join(rows) if rows else "(no matching lines)"


def journal(unit: str, since: str, lines: int, grep: str | None) -> str:
    if unit not in ("stock-analyzer", "nginx"):
        return f"(unit not allowed: {unit})"
    argv = [
        "journalctl",
        "-u",
        unit,
        "--since",
        since,
        "-n",
        str(lines),
        "--no-pager",
        "-o",
        "short-iso",
    ]
    if grep:
        argv.extend(["--grep", grep])
    return run_cmd(argv)


def tool_service_status(_args: dict[str, Any]) -> str:
    parts = [
        "=== services ===",
        f"stock-analyzer: {run_cmd(['systemctl', 'is-active', 'stock-analyzer'])}",
        f"nginx: {run_cmd(['systemctl', 'is-active', 'nginx'])}",
        f"stock-analyzer-mcp: {run_cmd(['systemctl', 'is-active', 'stock-analyzer-mcp'])}",
        "",
        "=== stock-analyzer status ===",
        run_cmd(["systemctl", "status", "stock-analyzer", "--no-pager", "-l", "-n", "15"]),
        "",
        "=== listeners ===",
        run_cmd(["ss", "-lnt"]),
        "",
        "=== curl local ===",
        "80 " + run_cmd(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", "http://127.0.0.1/"]),
        "8000 " + run_cmd(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", "http://127.0.0.1:8000/"]),
        "8010 " + run_cmd(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", "http://127.0.0.1:8010/mcp"]),
    ]
    return "\n".join(parts)


def tool_app_logs(args: dict[str, Any]) -> str:
    since = parse_since(args.get("since"))
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    return journal("stock-analyzer", since, lines, grep)


def tool_batch_logs(args: dict[str, Any]) -> str:
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    return tail_grep(app_dir() / "batch.log", lines, grep)


def tool_nginx_logs(args: dict[str, Any]) -> str:
    since = parse_since(args.get("since"))
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    journal_out = journal("nginx", since, lines, grep)
    file_out = tail_grep(NGINX_ERROR_LOG, lines, grep)
    return f"=== journal nginx since {since} ===\n{journal_out}\n\n=== {NGINX_ERROR_LOG} ===\n{file_out}"


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], str]]] = {
    "service_status": (
        "nginx / stock-analyzer / MCP の active 状態、待ち受けポート、localhost の HTTP コード。",
        {"type": "object", "properties": {}},
        tool_service_status,
    ),
    "app_logs": (
        "uvicorn (stock-analyzer) の journalctl。since は 1h / 30m / today / YYYY-MM-DD。",
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "1h, 30m, today, yesterday, or YYYY-MM-DD"},
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200"},
            },
        },
        tool_app_logs,
    ),
    "batch_logs": (
        "日次バッチの batch.log 末尾。",
        {
            "type": "object",
            "properties": {
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200"},
            },
        },
        tool_batch_logs,
    ),
    "nginx_logs": (
        "nginx の journal と /var/log/nginx/error.log。",
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "1h, 30m, today, yesterday, or YYYY-MM-DD"},
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200"},
            },
        },
        tool_nginx_logs,
    ),
}


def tools_list() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": desc, "inputSchema": schema}
        for name, (desc, schema, _) in TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any] | None) -> tuple[str, bool]:
    spec = TOOLS.get(name)
    if spec is None:
        return f"unknown tool: {name}", True
    try:
        text = spec[2](arguments or {})
        return redact(text), False
    except ValueError as exc:
        return str(exc), True
    except Exception as exc:  # noqa: BLE001 — surface unexpected errors to the agent
        return f"tool failed: {type(exc).__name__}: {exc}", True


def rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_rpc(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("jsonrpc") != "2.0":
        return rpc_error(payload.get("id"), -32600, "invalid jsonrpc")
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method == "notifications/initialized" or (isinstance(method, str) and method.startswith("notifications/")):
        return None
    if method == "initialize":
        wanted = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[1])
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1]
        return rpc_result(
            req_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return rpc_result(req_id, {})
    if method == "tools/list":
        return rpc_result(req_id, {"tools": tools_list()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        text, is_error = call_tool(name, arguments)
        return rpc_result(
            req_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    if method == "resources/list":
        return rpc_result(req_id, {"resources": []})
    if method == "prompts/list":
        return rpc_result(req_id, {"prompts": []})
    return rpc_error(req_id, -32601, f"method not found: {method}")


def encode_mcp(request: Request, payload: dict[str, Any], session_id: str) -> Response:
    headers = {
        "Mcp-Session-Id": session_id,
        "Cache-Control": "no-cache",
    }
    accept = (request.headers.get("accept") or "").lower()
    body = json.dumps(payload, ensure_ascii=False)
    if "text/event-stream" in accept:
        return Response(
            f"event: message\ndata: {body}\n\n",
            media_type="text/event-stream",
            headers=headers,
        )
    return Response(body, media_type="application/json", headers=headers)


def create_app(token: str) -> Starlette:
    async def mcp_endpoint(request: Request) -> Response:
        if not token_ok(extract_token(request), token):
            return PlainTextResponse("unauthorized", status_code=401)
        session_id = request.headers.get("mcp-session-id") or secrets.token_hex(8)
        if request.method == "GET":
            return PlainTextResponse(f"{SERVER_NAME} {SERVER_VERSION}\n", headers={"Mcp-Session-Id": session_id})
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)

        if isinstance(payload, list):
            replies = [handle_rpc(item) for item in payload if isinstance(item, dict)]
            replies = [r for r in replies if r is not None]
            if not replies:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return encode_mcp(request, replies if len(replies) > 1 else replies[0], session_id)

        if not isinstance(payload, dict):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}, status_code=400)
        reply = handle_rpc(payload)
        if reply is None:
            return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
        return encode_mcp(request, reply, session_id)

    routes = [
        Route("/mcp", mcp_endpoint, methods=["GET", "POST"]),
        Route("/mcp/", mcp_endpoint, methods=["GET", "POST"]),
    ]
    return Starlette(routes=routes)


def configured_token() -> str:
    return (os.environ.get("MCP_TOKEN") or "").strip()


app = create_app(configured_token())


def main() -> None:
    import uvicorn

    token = configured_token()
    if not token:
        print("MCP_TOKEN is unset; all requests will be 401", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()

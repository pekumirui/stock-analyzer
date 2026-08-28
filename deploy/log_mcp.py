"""Streamable HTTP MCP for VPS logs, SQLite stats, and monthly tables.

No extra deps: Starlette ships with FastAPI. Listens on 127.0.0.1:8010/mcp.
Arbitrary shell/SQL is not exposed. journalctl units, log paths, and SELECT shapes are allowlisted.
Monthly table tools call http://127.0.0.1:8000/api/v1/tables only (not nginx, not user URLs).
monthly_ingest POSTs /api/v1/tables/{code}/ingest and does not send year.
"""
from __future__ import annotations

import sqlite3
import base64
import hashlib
import html
import json
import os
import re
import hmac
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

def app_dir() -> Path:
    return Path(os.environ.get("MCP_APP_DIR") or Path(__file__).resolve().parent.parent)


def log_dir() -> Path:
    return Path(os.environ.get("MCP_LOG_DIR") or DEFAULT_LOG_DIR)


def pick_log(name: str) -> Path:
    preferred = log_dir() / name
    if preferred.is_file():
        return preferred
    return app_dir() / name


NGINX_ERROR_LOG = Path("/var/log/nginx/error.log")
DEFAULT_LOG_DIR = Path("/var/log/app")
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
OAUTH_CLIENT_ID = "stock-analyzer"
OAUTH_SCOPE = "mcp"
ACCESS_TTL = 30 * 24 * 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
OAUTH_STORE_NAME = ".mcp-oauth.json"
SINCE_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
SECRET_RE = re.compile(
    r"(MCP_TOKEN|JQUANTS_API_KEY|BASIC_AUTH_PASSWORD|api[_-]?key|password)\s*[:=]\s*\S+",
    re.I,
)
GREP_RE = re.compile(r"^[^\n\r\x00]{1,120}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
SHORT_SINCE_RE = re.compile(r"^(\d+)([smhd])$", re.I)
# 先頭・末尾は数字、途中は 0-9A-Z。4桁 (7203 / 262A) と 5桁 (72030 / 262A0) を許す。
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
YEAR_RE = re.compile(r"^\d{4}$")
DB_TABLES = ("stocks", "daily_prices", "topix", "statements", "indicators", "earnings_events")
DEFAULT_APP_HTTP = "http://127.0.0.1:8000"
APP_HTTP_TIMEOUT = 10
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MONTHLY_PATH_PREFIX = "/api/v1/tables"
MONTHLY_INGEST_TAIL = "/ingest"
MONTHLY_TAILS = frozenset({"", "/elements", "/values/batch", MONTHLY_INGEST_TAIL})
MONTHLY_URL_PATH_RE = re.compile(
    r"^/api/v1/tables(?:/[0-9][0-9A-Z]{3}[0-9]?(?:/\d{4}(?:/elements|/values/batch)?|/ingest))?$"
)
_CODE_ERR = "code must be 4-5 characters (e.g. 7203, 72030, 262A0)"


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


def env_flag(name: str) -> str:
    if (os.environ.get(name) or "").strip():
        return "set"
    path = app_dir() / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("export "):
                s = s[7:].strip()
            if s.startswith(f"{name}="):
                val = s.split("=", 1)[1].strip().strip("\"'")
                return "set" if val else "empty"
    except OSError:
        return "missing (.env unreadable)"
    return "missing"


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
        "",
        f"=== {log_dir()} ===",
        run_cmd(["ls", "-l", str(log_dir())]),
        "",
        "=== env flags ===",
        f"jquants_key: {env_flag('JQUANTS_API_KEY')}",
        f"mcp_token: {env_flag('MCP_TOKEN')}",
    ]
    return "\n".join(parts)


def tool_app_logs(args: dict[str, Any]) -> str:
    since = parse_since(args.get("since"))
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    path = pick_log("stock-analyzer.log")
    file_out = tail_grep(path, lines, grep)
    journal_out = journal("stock-analyzer", since, lines, grep)
    return f"=== {path} ===\n{file_out}\n\n=== journal stock-analyzer since {since} ===\n{journal_out}"


def tool_batch_logs(args: dict[str, Any]) -> str:
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    return tail_grep(pick_log("batch.log"), lines, grep)


def tool_nginx_logs(args: dict[str, Any]) -> str:
    since = parse_since(args.get("since"))
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    journal_out = journal("nginx", since, lines, grep)
    file_out = tail_grep(NGINX_ERROR_LOG, lines, grep)
    return f"=== journal nginx since {since} ===\n{journal_out}\n\n=== {NGINX_ERROR_LOG} ===\n{file_out}"


def db_file() -> Path:
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if raw.startswith("sqlite:///"):
        return Path(raw[len("sqlite:///"):])
    return app_dir() / "stock.db"


def open_db() -> sqlite3.Connection:
    path = db_file()
    if not path.is_file():
        raise ValueError(f"missing {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=8)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def parse_code(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if not CODE_RE.fullmatch(s) or "." in s or "/" in s:
        raise ValueError(_CODE_ERR)
    if len(s) == 4:
        s = s + "0"
    return s


def parse_monthly_code(raw: Any) -> str:
    """4-5 char as given for numeric; 4-char JPX display (262A) becomes 262A0.

    Do not pad 4-digit numeric; monthly API treats 7203 and 72030 as distinct.
    """
    s = str(raw or "").strip().upper()
    # charset + explicit . / : blocks path traversal in /api/v1/tables/{code}/...
    if not CODE_RE.fullmatch(s) or "." in s or "/" in s:
        raise ValueError(_CODE_ERR)
    if len(s) == 4 and not s.isdigit():
        s = s + "0"
    return s


def parse_monthly_year(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError("year must be a 4-digit integer")
    if isinstance(raw, int):
        s = str(raw)
    else:
        s = str(raw or "").strip()
    if not YEAR_RE.fullmatch(s):
        raise ValueError("year must be a 4-digit integer")
    return int(s)


def parse_monthly_month(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1 or raw > 12:
        raise ValueError("month must be an integer 1-12")
    return raw


def parse_monthly_values(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("values must be an array of {month, value}")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or "month" not in item or "value" not in item:
            raise ValueError("values must be an array of {month, value}")
        # value is passed through; % / comma strings are rejected by the API as INVALID_VALUE
        out.append({"month": parse_monthly_month(item.get("month")), "value": item.get("value")})
    return out


def _is_loopback_host(host: str | None) -> bool:
    return (host or "").lower().strip("[]") in LOOPBACK_HOSTS


def app_http_base() -> str:
    """Loopback FastAPI only. MCP_APP_HTTP may override host/port for tests."""
    raw = (os.environ.get("MCP_APP_HTTP") or DEFAULT_APP_HTTP).strip()
    if not raw:
        raw = DEFAULT_APP_HTTP
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not _is_loopback_host(parsed.hostname):
        raise ValueError("MCP_APP_HTTP host must be loopback (127.0.0.1)")
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if ":" in host:
        netloc = f"[{host}]" + (f":{port}" if port else "")
    else:
        netloc = host + (f":{port}" if port else "")
    return f"{parsed.scheme}://{netloc}"


def monthly_api_url(code: str | None = None, year: int | None = None, tail: str = "") -> str:
    if tail not in MONTHLY_TAILS:
        raise ValueError("invalid monthly API path")
    if code is None:
        if year is not None or tail:
            raise ValueError("invalid monthly API path")
        path = MONTHLY_PATH_PREFIX
    else:
        safe_code = quote(code, safe="")
        if safe_code != code or not CODE_RE.fullmatch(code) or "." in code or "/" in code:
            raise ValueError(_CODE_ERR)
        if tail == MONTHLY_INGEST_TAIL:
            if year is not None:
                raise ValueError("invalid monthly API path")
            path = f"{MONTHLY_PATH_PREFIX}/{safe_code}{MONTHLY_INGEST_TAIL}"
        else:
            if year is None:
                raise ValueError("invalid monthly API path")
            path = f"{MONTHLY_PATH_PREFIX}/{safe_code}/{year}{tail}"
    if ".." in path or not path.startswith(MONTHLY_PATH_PREFIX):
        raise ValueError("invalid monthly API path")
    if not MONTHLY_URL_PATH_RE.fullmatch(path):
        raise ValueError("invalid monthly API path")
    url = app_http_base() + path
    parsed = urlparse(url)
    if not _is_loopback_host(parsed.hostname) or not parsed.path.startswith(MONTHLY_PATH_PREFIX):
        raise ValueError("monthly API URL must be loopback /api/v1/tables")
    if parsed.path != path:
        raise ValueError("invalid monthly API path")
    return url


def monthly_ingest_url(code: str) -> str:
    """POST /api/v1/tables/{code}/ingest. No year in the path."""
    return monthly_api_url(code, tail=MONTHLY_INGEST_TAIL)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _monthly_opener() -> urllib.request.OpenerDirector:
    # No HTTP(S)_PROXY: requests must stay on 127.0.0.1. Do not follow redirects off-path.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler)


def call_monthly_api(method: str, url: str, body: dict[str, Any] | None = None) -> str:
    if method not in ("GET", "POST", "PUT"):
        raise ValueError("invalid monthly HTTP method")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not _is_loopback_host(parsed.hostname):
        raise ValueError("monthly API host must be loopback")
    if not parsed.path.startswith(MONTHLY_PATH_PREFIX):
        raise ValueError("monthly API path must be /api/v1/tables")
    if not MONTHLY_URL_PATH_RE.fullmatch(parsed.path):
        raise ValueError("monthly API path must be /api/v1/tables")
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        with _monthly_opener().open(req, timeout=APP_HTTP_TIMEOUT) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read() if exc.fp is not None else b""
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise ValueError(f"FastAPI monthly API timeout after {APP_HTTP_TIMEOUT}s at {base}") from None
        raise ValueError(f"FastAPI monthly API connection refused at {base}: {reason}") from None
    except TimeoutError:
        raise ValueError(f"FastAPI monthly API timeout after {APP_HTTP_TIMEOUT}s at {base}") from None
    except OSError as exc:
        raise ValueError(f"FastAPI monthly API unreachable at {base}: {exc}") from None
    text = raw.decode("utf-8", errors="replace") if raw else ""
    return f"HTTP {status}\n{text}"


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def tool_db_stats(_args: dict[str, Any]) -> str:
    path = db_file()
    if not path.is_file():
        return f"(missing {path})"
    st = path.stat()
    parts = [
        f"path: {path}",
        f"size_mb: {st.st_size / (1024 * 1024):.1f}",
        f"mtime: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}",
        "",
    ]
    conn = open_db()
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in DB_TABLES:
            if table not in names:
                parts.append(f"{table}: (no table)")
                continue
            n = _scalar(conn, f"SELECT COUNT(*) FROM {table}")
            extra = ""
            if table == "stocks":
                demo = _scalar(conn, "SELECT COUNT(*) FROM stocks WHERE code LIKE '0%'")
                extra = f"  demo={demo} real={int(n) - int(demo)}"
            elif table == "daily_prices":
                mn, mx, codes, days = conn.execute(
                    "SELECT MIN(date), MAX(date), COUNT(DISTINCT code), COUNT(DISTINCT date) FROM daily_prices"
                ).fetchone()
                extra = f"  {mn}..{mx}  codes={codes} days={days}"
            elif table == "topix":
                mn, mx = conn.execute("SELECT MIN(date), MAX(date) FROM topix").fetchone()
                extra = f"  {mn}..{mx}"
            elif table == "statements":
                mn, mx, codes = conn.execute(
                    "SELECT MIN(disclosed_date), MAX(disclosed_date), COUNT(DISTINCT code) FROM statements"
                ).fetchone()
                extra = f"  disclosed {mn}..{mx}  codes={codes}"
            elif table == "indicators":
                mn, mx = conn.execute("SELECT MIN(date), MAX(date) FROM indicators").fetchone()
                extra = f"  {mn}..{mx}"
            parts.append(f"{table}: {n}{extra}")
    finally:
        conn.close()
    return "\n".join(parts)


def tool_db_lookup(args: dict[str, Any]) -> str:
    code = parse_code(args.get("code"))
    lines = clamp_lines(args.get("lines") or 8)
    path = db_file()
    if not path.is_file():
        return f"(missing {path})"
    conn = open_db()
    try:
        stock = conn.execute(
            "SELECT code, name, market, sector33, scale_category FROM stocks WHERE code = ?",
            (code,),
        ).fetchone()
        if stock is None:
            return f"{code}: not in stocks"
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        parts = [
            f"{stock['code']} {stock['name'] or ''}  market={stock['market'] or '-'}  sector={stock['sector33'] or '-'}  scale={stock['scale_category'] or '-'}",
        ]
        if "daily_prices" in names:
            n_p = _scalar(conn, "SELECT COUNT(*) FROM daily_prices WHERE code = ?", (code,))
            rng = conn.execute(
                "SELECT MIN(date), MAX(date) FROM daily_prices WHERE code = ?",
                (code,),
            ).fetchone()
            parts.append(f"prices: {n_p}  {rng[0]}..{rng[1]}")
            rows = conn.execute(
                "SELECT date, adj_close, adj_volume, close FROM daily_prices WHERE code = ? ORDER BY date DESC LIMIT ?",
                (code, lines),
            ).fetchall()
            for r in rows:
                parts.append(f"  {r['date']}  adj={r['adj_close']}  vol={r['adj_volume']}  raw={r['close']}")
        if "statements" in names:
            n_s = _scalar(conn, "SELECT COUNT(*) FROM statements WHERE code = ?", (code,))
            last = conn.execute(
                "SELECT disclosed_date, type_of_period, type_of_document FROM statements WHERE code = ? ORDER BY disclosed_date DESC LIMIT 5",
                (code,),
            ).fetchall()
            parts.append(f"statements: {n_s}")
            for r in last:
                parts.append(f"  {r['disclosed_date']}  {r['type_of_period'] or ''}  {r['type_of_document'] or ''}")
        if "indicators" in names:
            ind = conn.execute(
                "SELECT date, stage, rs_rank, ma50, ma150, ma200 FROM indicators WHERE code = ? ORDER BY date DESC LIMIT 1",
                (code,),
            ).fetchone()
            if ind:
                parts.append(
                    f"indicator {ind['date']}: stage={ind['stage']} rs={ind['rs_rank']} ma50={ind['ma50']} ma150={ind['ma150']} ma200={ind['ma200']}"
                )
            else:
                parts.append("indicators: 0")
    finally:
        conn.close()
    return "\n".join(parts)


def tool_monthly_list(_args: dict[str, Any]) -> str:
    return call_monthly_api("GET", monthly_api_url())


def tool_monthly_get(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    return call_monthly_api("GET", monthly_api_url(code, year))


def tool_monthly_add_element(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    element = args.get("element")
    if not isinstance(element, str) or not element.strip():
        raise ValueError("element is required")
    body: dict[str, Any] = {"element": element}
    if "force" in args:
        if not isinstance(args["force"], bool):
            raise ValueError("force must be a boolean")
        body["force"] = args["force"]
    return call_monthly_api("POST", monthly_api_url(code, year, "/elements"), body)


def tool_monthly_put_values(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    element = args.get("element")
    if not isinstance(element, str) or not element.strip():
        raise ValueError("element is required")
    body = {"element": element, "values": parse_monthly_values(args.get("values"))}
    return call_monthly_api("PUT", monthly_api_url(code, year, "/values/batch"), body)


def _parse_ingest_months(raw: Any) -> list[int]:
    if not isinstance(raw, list) or len(raw) < 1:
        raise ValueError("months must be a non-empty array of unique integers 1-12")
    out: list[int] = []
    seen: set[int] = set()
    for item in raw:
        month = parse_monthly_month(item)
        if month in seen:
            raise ValueError("months must be a non-empty array of unique integers 1-12")
        seen.add(month)
        out.append(month)
    return out


def _parse_ingest_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError("rows must be an array of {element, values}")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("rows must be an array of {element, values}")
        element = item.get("element")
        if not isinstance(element, str) or not element.strip():
            raise ValueError("element is required")
        values = item.get("values")
        if not isinstance(values, dict):
            raise ValueError("values must be an object of month -> number|null")
        row: dict[str, Any] = {"element": element, "values": values}
        if "force" in item:
            if not isinstance(item["force"], bool):
                raise ValueError("force must be a boolean")
            row["force"] = item["force"]
        out.append(row)
    return out


def tool_monthly_ingest(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    body: dict[str, Any] = {
        "months": _parse_ingest_months(args.get("months")),
        "rows": _parse_ingest_rows(args.get("rows")),
    }
    if "as_of" in args and args["as_of"] is not None:
        as_of = args["as_of"]
        if not isinstance(as_of, str):
            raise ValueError("as_of must be YYYY-MM-DD")
        body["as_of"] = as_of
    return call_monthly_api("POST", monthly_ingest_url(code), body)


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], str]]] = {
    "service_status": (
        "nginx / stock-analyzer / MCP の active 状態、待ち受けポート、localhost の HTTP コード。",
        {"type": "object", "properties": {}},
        tool_service_status,
    ),
    "app_logs": (
        "uvicorn (stock-analyzer) の /var/log/app/stock-analyzer.log と journalctl。since は 1h / 30m / today / YYYY-MM-DD。",
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
        "日次バッチの /var/log/app/batch.log 末尾。無ければアプリディレクトリの batch.log。",
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
    "db_stats": (
        "stock.db の件数・日付範囲・デモ混在。取込が想定通りか見る用。読み取り専用。",
        {"type": "object", "properties": {}},
        tool_db_stats,
    ),
    "db_lookup": (
        "1銘柄のマスタ・直近株価・決算・指標。code は 7203 / 72030 / 262A0。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "lines": {"type": "integer", "description": "recent price rows, 1-500, default 8"},
            },
            "required": ["code"],
        },
        tool_db_lookup,
    ),
    "monthly_list": (
        "月次表がある銘柄一覧。localhost:8000 の GET /api/v1/tables（nginx Basic は使わない）。",
        {"type": "object", "properties": {}},
        tool_monthly_list,
    ),
    "monthly_get": (
        "銘柄×年度の月次表。localhost:8000 の GET /api/v1/tables/{code}/{year}。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
            },
            "required": ["code", "year"],
        },
        tool_monthly_get,
    ),
    "monthly_add_element": (
        "月次表に要素を登録。localhost:8000 の POST /api/v1/tables/{code}/{year}/elements。類似は 409。force=true で強制。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
                "element": {"type": "string", "description": "row label, 1-50 chars"},
                "force": {"type": "boolean", "description": "create even if a similar element exists"},
            },
            "required": ["code", "year", "element"],
        },
        tool_monthly_add_element,
    ),
    "monthly_put_values": (
        "月次セルを一括 upsert（訂正用）。localhost:8000 の PUT /api/v1/tables/{code}/{year}/values/batch。value の null は削除。新規登録は monthly_ingest。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
                "element": {"type": "string", "description": "existing row label"},
                "values": {
                    "type": "array",
                    "description": "list of {month, value}; value null deletes the cell",
                    "items": {
                        "type": "object",
                        "properties": {
                            "month": {"type": "integer", "description": "1-12"},
                            "value": {"type": ["number", "null"], "description": "number, or null to delete"},
                        },
                        "required": ["month", "value"],
                    },
                },
            },
            "required": ["code", "year", "element", "values"],
        },
        tool_monthly_put_values,
    ),
    "monthly_ingest": (
        "PDF月次を暦年に割り当てて登録。localhost:8000 の POST /api/v1/tables/{code}/ingest。year は渡さない（決算の事業年度末から決める）。年付き put は訂正用。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "months": {
                    "type": "array",
                    "description": "PDF column months in left-to-right order, unique integers 1-12",
                    "items": {"type": "integer"},
                },
                "rows": {
                    "type": "array",
                    "description": "list of {element, values, force?}",
                    "items": {
                        "type": "object",
                        "properties": {
                            "element": {"type": "string", "description": "row label, 1-50 chars"},
                            "values": {
                                "type": "object",
                                "description": "month (string or int key) -> number, or null to delete",
                                "additionalProperties": {"type": ["number", "null"]},
                            },
                            "force": {"type": "boolean", "description": "create even if a similar element exists"},
                        },
                        "required": ["element", "values"],
                    },
                },
                "as_of": {"type": "string", "description": "disclosure date YYYY-MM-DD; default server date"},
            },
            "required": ["code", "months", "rows"],
        },
        tool_monthly_ingest,
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


def public_base(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost").split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = "https"
    return f"{proto}://{host}"


def cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Session-Id",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Max-Age": "86400",
        "Cache-Control": "no-store",
    }


def json_cors(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=cors_headers())


async def read_form(request: Request) -> dict[str, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            body = await request.json()
        except Exception:
            return {}
        if not isinstance(body, dict):
            return {}
        return {str(k): "" if v is None else str(v) for k, v in body.items()}
    raw = (await request.body()).decode("utf-8", errors="replace")
    return dict(parse_qsl(raw, keep_blank_values=True))


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_ok(uri: str) -> bool:
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme in ("http", "https") and host in {"127.0.0.1", "localhost", "testserver"}:
        return True
    if parsed.scheme in {"grok", "x-grok", "grok-app", "cursor"}:
        return True
    if parsed.scheme != "https":
        return False
    return (
        host == "grok.com"
        or host.endswith(".grok.com")
        or host == "x.ai"
        or host.endswith(".x.ai")
        or host == "cursor.com"
        or host.endswith(".cursor.com")
    )


def oauth_path(app_dir_path: Path) -> Path:
    return app_dir_path / OAUTH_STORE_NAME


class OAuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.codes: dict[str, dict[str, Any]] = {}
        self.data: dict[str, Any] = {"access": {}, "refresh": {}, "clients": {}}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            for key in ("access", "refresh", "clients"):
                if isinstance(raw.get(key), dict):
                    self.data[key] = raw[key]

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def purge(self) -> None:
        now = time.time()
        for bucket in ("access", "refresh"):
            dead = [k for k, v in self.data[bucket].items() if float(v.get("exp", 0)) < now]
            for k in dead:
                self.data[bucket].pop(k, None)
        expired = [k for k, v in self.codes.items() if float(v.get("exp", 0)) < now]
        for k in expired:
            self.codes.pop(k, None)

    def access_ok(self, got: str) -> bool:
        if not got:
            return False
        with self.lock:
            self.purge()
            rec = self.data["access"].get(got)
            return bool(rec) and float(rec.get("exp", 0)) >= time.time()


def issue_pair(store: OAuthStore, client_id: str) -> dict[str, Any]:
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    now = time.time()
    with store.lock:
        store.purge()
        store.data["access"][access] = {"exp": now + ACCESS_TTL, "client_id": client_id}
        store.data["refresh"][refresh] = {"exp": now + REFRESH_TTL, "client_id": client_id}
        store._save()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": refresh,
        "scope": OAUTH_SCOPE,
    }


def as_server_metadata(base: str) -> dict[str, Any]:
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [OAUTH_SCOPE],
        "authorization_response_iss_parameter_supported": True,
    }


def as_resource_metadata(base: str) -> dict[str, Any]:
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [OAUTH_SCOPE],
    }


def authorize_page(error: str = "", hidden: dict[str, str] | None = None) -> str:
    fields = ""
    for k, v in (hidden or {}).items():
        fields += f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">\n'
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>stock-analyzer MCP</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:28rem;margin:12vh auto;padding:0 1.2rem;color:#111}}
h1{{font-size:1.15rem}}
p,label{{color:#444;line-height:1.5}}
input[type=password]{{width:100%;box-sizing:border-box;padding:.7rem;font-size:1rem;margin:.4rem 0 1rem}}
button{{background:#111;color:#fff;border:0;padding:.7rem 1.1rem;border-radius:6px;font-size:1rem}}
.err{{color:#b00020}}
</style></head><body>
<h1>ログ MCP の接続許可</h1>
<p>Grok が stock-analyzer の読み取り専用ログに接続しようとしています。GitHub secret と同じ <code>MCP_TOKEN</code> を入れて許可してください。</p>
{err}
<form method="post" action="/oauth/authorize" autocomplete="off">
{fields}
<label>MCP_TOKEN
<input type="password" name="token" required autofocus>
</label>
<button type="submit">許可する</button>
</form>
</body></html>"""


def unauthorized(request: Request) -> Response:
    meta = public_base(request) + "/.well-known/oauth-protected-resource"
    headers = cors_headers()
    headers["WWW-Authenticate"] = f'Bearer realm="mcp", resource_metadata="{meta}"'
    return PlainTextResponse("unauthorized", status_code=401, headers=headers)


def create_app(token: str) -> Starlette:
    store = OAuthStore(oauth_path(app_dir()))

    def bearer_ok(got: str) -> bool:
        if token_ok(got, token):
            return True
        return store.access_ok(got)

    async def mcp_endpoint(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        if not bearer_ok(extract_token(request)):
            return unauthorized(request)
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

    async def well_known_as(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        return json_cors(as_server_metadata(public_base(request)))

    async def well_known_pr(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        return json_cors(as_resource_metadata(public_base(request)))

    def authz_params(request: Request, form: dict[str, str] | None = None) -> dict[str, str]:
        src = form if form is not None else {k: v for k, v in request.query_params.items()}
        keys = (
            "response_type",
            "client_id",
            "redirect_uri",
            "state",
            "scope",
            "code_challenge",
            "code_challenge_method",
            "resource",
        )
        return {k: (src.get(k) or "").strip() for k in keys}

    async def authorize(request: Request) -> Response:
        form: dict[str, str] | None = None
        posted_token = ""
        if request.method == "POST":
            form = await read_form(request)
            posted_token = (form.get("token") or "").strip()
        p = authz_params(request, form)
        hidden = {k: v for k, v in p.items() if v}
        if p["response_type"] != "code":
            return HTMLResponse(authorize_page("response_type は code のみです。", hidden), status_code=400)
        if p["code_challenge_method"] != "S256" or len(p["code_challenge"]) < 43:
            return HTMLResponse(authorize_page("PKCE (S256) が必要です。", hidden), status_code=400)
        if not redirect_ok(p["redirect_uri"]):
            return HTMLResponse(authorize_page("redirect_uri が許可されていません。", hidden), status_code=400)
        if request.method == "GET":
            return HTMLResponse(authorize_page("", hidden))
        if not token_ok(posted_token, token):
            return HTMLResponse(authorize_page("トークンが違います。", hidden), status_code=401)
        code = secrets.token_urlsafe(24)
        with store.lock:
            store.purge()
            store.codes[code] = {
                "exp": time.time() + CODE_TTL,
                "client_id": p["client_id"] or OAUTH_CLIENT_ID,
                "redirect_uri": p["redirect_uri"],
                "challenge": p["code_challenge"],
                "resource": p["resource"],
            }
        q = dict(parse_qsl(urlparse(p["redirect_uri"]).query, keep_blank_values=True))
        q["code"] = code
        if p["state"]:
            q["state"] = p["state"]
        q["iss"] = public_base(request)
        dest = urlparse(p["redirect_uri"])
        loc = urlunparse((dest.scheme, dest.netloc, dest.path, dest.params, urlencode(q), dest.fragment))
        return RedirectResponse(loc, status_code=302)

    async def token_endpoint(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        form = await read_form(request)
        grant = (form.get("grant_type") or "").strip()
        if grant == "refresh_token":
            refresh = (form.get("refresh_token") or "").strip()
            with store.lock:
                store.purge()
                rec = store.data["refresh"].pop(refresh, None)
                if rec:
                    store._save()
            if not rec or float(rec.get("exp", 0)) < time.time():
                return json_cors({"error": "invalid_grant"}, 400)
            return json_cors(issue_pair(store, str(rec.get("client_id") or OAUTH_CLIENT_ID)))
        if grant != "authorization_code":
            return json_cors({"error": "unsupported_grant_type"}, 400)
        code = (form.get("code") or "").strip()
        verifier = (form.get("code_verifier") or "").strip()
        redirect_uri = (form.get("redirect_uri") or "").strip()
        with store.lock:
            rec = store.codes.pop(code, None)
        if not rec or float(rec.get("exp", 0)) < time.time():
            return json_cors({"error": "invalid_grant"}, 400)
        if rec.get("redirect_uri") != redirect_uri:
            return json_cors({"error": "invalid_grant"}, 400)
        if s256(verifier) != rec.get("challenge"):
            return json_cors({"error": "invalid_grant"}, 400)
        return json_cors(issue_pair(store, str(rec.get("client_id") or OAUTH_CLIENT_ID)))

    async def register(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        uris = body.get("redirect_uris") or []
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and redirect_ok(u) for u in uris):
            return json_cors({"error": "invalid_redirect_uri"}, 400)
        client_id = secrets.token_urlsafe(16)
        with store.lock:
            store.data["clients"][client_id] = {
                "redirect_uris": uris,
                "name": str(body.get("client_name") or "grok")[:80],
                "created": int(time.time()),
            }
            store._save()
        return json_cors(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "redirect_uris": uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
            201,
        )

    async def revoke(request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers())
        data = await read_form(request)
        tok = str(data.get("token") or "").strip()
        with store.lock:
            store.data["access"].pop(tok, None)
            store.data["refresh"].pop(tok, None)
            store._save()
        return Response(status_code=200, headers=cors_headers())

    routes = [
        Route("/mcp", mcp_endpoint, methods=["GET", "POST", "OPTIONS"]),
        Route("/mcp/", mcp_endpoint, methods=["GET", "POST", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server", well_known_as, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-authorization-server/mcp", well_known_as, methods=["GET", "OPTIONS"]),
        Route("/.well-known/openid-configuration", well_known_as, methods=["GET", "OPTIONS"]),
        Route("/.well-known/openid-configuration/mcp", well_known_as, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource", well_known_pr, methods=["GET", "OPTIONS"]),
        Route("/.well-known/oauth-protected-resource/mcp", well_known_pr, methods=["GET", "OPTIONS"]),
        Route("/oauth/authorize", authorize, methods=["GET", "POST"]),
        Route("/oauth/token", token_endpoint, methods=["POST", "OPTIONS"]),
        Route("/oauth/register", register, methods=["POST", "OPTIONS"]),
        Route("/oauth/revoke", revoke, methods=["POST", "OPTIONS"]),
    ]
    app = Starlette(routes=routes)
    app.state.oauth = store
    return app


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

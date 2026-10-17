"""Streamable HTTP MCP for VPS logs, SQLite stats, monthly tables, and FX rates.

No extra deps: Starlette ships with FastAPI. Listens on 127.0.0.1:8010/mcp.
Arbitrary shell/SQL is not exposed. journalctl units, log paths, and SELECT shapes are allowlisted.
Monthly table tools call http://127.0.0.1:8000/api/v1/tables only (not nginx, not user URLs).
monthly_ingest POSTs /api/v1/tables/{code}/ingest and does not send year.
FX tools read fx_rates (EUR base) and compute cross rates. They do not call Frankfurter.
"""
from __future__ import annotations

import sqlite3
import sys
import base64
import gzip
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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
NGINX_ACCESS_LOG = Path("/var/log/nginx/stock-analyzer.access.log")
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
    r"(MCP_TOKEN|JQUANTS_API_KEY|BASIC_AUTH_PASSWORD|SLACK_WEBHOOK_URL|api[_-]?key|password)\s*[:=]\s*\S+",
    re.I,
)
GREP_RE = re.compile(r"^[^\n\r\x00]{1,120}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
SHORT_SINCE_RE = re.compile(r"^(\d+)([smhd])$", re.I)
# 先頭・末尾は数字、途中は 0-9A-Z。4桁は末尾0で 5桁正規形 (9279→92790, 262A→262A0)。
CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BATCH_LOG_SKIP = frozenset({"stock-analyzer.log", "mcp.log"})
# cron-run.sh の行頭。JST の "YYYY-MM-DD HH:MM:SS "。別の形は読まない。
LINE_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ")
# nginx の行時刻。access は log_format timed の [$time_local]、error は VPS ローカル（JST）
ACCESS_STAMP_RE = re.compile(r"\[(\d{2}/[A-Z][a-z]{2}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\]")
ERROR_STAMP_RE = re.compile(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) ")
JST = timezone(timedelta(hours=9))
_BOUND_HINT = "1h, 30m, today, yesterday, or YYYY-MM-DD"


def crontab_job_names() -> tuple[str, ...]:
    """Stems of jobs/**/*.py scheduled in deploy/crontab. Comments are not jobs."""
    path = _REPO_ROOT / "deploy" / "crontab"
    found: list[str] = []
    job_re = re.compile(r"jobs/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.py")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for match in job_re.finditer(stripped):
            name = match.group(1)
            if name in found or name in {"stock-analyzer", "mcp"}:
                continue
            if JOB_NAME_RE.fullmatch(name) is None:
                continue
            found.append(name)
    return tuple(found)


BATCH_JOBS = frozenset(crontab_job_names())
YEAR_RE = re.compile(r"^\d{4}$")
DB_TABLES = (
    "stocks", "daily_prices", "topix", "statements", "indicators",
    "earnings_events", "fx_rates",
)
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FX_MAX_OBS = 500
DEFAULT_APP_HTTP = "http://127.0.0.1:8000"
APP_HTTP_TIMEOUT = 10
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MONTHLY_PATH_PREFIX = "/api/v1/tables"
MONTHLY_INGEST_TAIL = "/ingest"
MONTHLY_INDUSTRY_TAIL = "/industry"
MONTHLY_TAILS = frozenset({
    "", "/elements", "/values/batch", "/doc", MONTHLY_INGEST_TAIL, MONTHLY_INDUSTRY_TAIL,
})
MONTHLY_URL_PATH_RE = re.compile(
    r"^/api/v1/tables(?:/[0-9][0-9A-Z]{3}[0-9]?(?:/\d{4}(?:/elements|/values/batch|/doc)?|/ingest|/industry))?$"
)
_CODE_ERR = "code must be 4-5 characters (e.g. 7203, 72030, 262A0)"


# アクセスログの URL に載る OAuth の一時値。MCP の応答に出さない
URL_SECRET_RE = re.compile(
    r"([?&](?:code|state|token|access_token|refresh_token|client_secret|code_verifier)=)[^&\s\"]+",
    re.I,
)


def redact(text: str) -> str:
    return SECRET_RE.sub(lambda m: m.group(1) + "=<redacted>", text)


def redact_url(text: str) -> str:
    return URL_SECRET_RE.sub(lambda m: m.group(1) + "<redacted>", redact(text))


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


def journal(unit: str, since: str, lines: int, grep: str | None, until: str | None = None) -> str:
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
    if until:
        argv.extend(["--until", until])
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
        # uvicorn は workers=1。call_tool は同期でイベントループを塞ぐので、
        # 自分の /mcp へ curl すると --max-time 3 で 000 になり故障と誤認する。
        # この tool が応答できていること自体が MCP の生存確認。
        f"{PORT} ok (this process)",
        "",
        f"=== {log_dir()} ===",
        run_cmd(["ls", "-l", str(log_dir())]),
        "",
        "=== env flags ===",
        f"jquants_key: {env_flag('JQUANTS_API_KEY')}",
        f"mcp_token: {env_flag('MCP_TOKEN')}",
    ]
    return "\n".join(parts)


def _journal_window(start: datetime | None, end: datetime | None) -> tuple[str, str | None, str]:
    """journalctl の --since / --until と見出し。journalctl は VPS ローカル（JST）で解釈する。"""
    fmt = "%Y-%m-%d %H:%M:%S"
    since = start.strftime(fmt) if start is not None else "1970-01-01 00:00:00"
    until = end.strftime(fmt) if end is not None else None
    return since, until, f"since {since}" + (f" until {until}" if until else "")


def tool_app_logs(args: dict[str, Any]) -> str:
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    start, end = parse_batch_window(args)
    path = pick_log("stock-analyzer.log")
    if start is None and end is None:
        since = parse_since(args.get("since"))
        file_out = tail_grep(path, lines, grep)
        journal_out = journal("stock-analyzer", since, lines, grep)
        label = f"since {since}"
    else:
        since, until, label = _journal_window(start, end)
        file_out = _rotated_window(path, _cron_stamp, start, end, lines, grep)
        journal_out = journal("stock-analyzer", since, lines, grep, until)
    return f"=== {path} ===\n{file_out}\n\n=== journal stock-analyzer {label} ===\n{journal_out}"


def _job_error() -> str:
    names = ", ".join(sorted(BATCH_JOBS))
    return f"job must be a crontab script stem ({names})"


def parse_batch_job(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(_job_error())
    name = raw.strip()
    if not name:
        return None
    if JOB_NAME_RE.fullmatch(name) is None or name not in BATCH_JOBS:
        raise ValueError(_job_error())
    return name


def _jst_now() -> datetime:
    return datetime.now(JST).replace(tzinfo=None)


def parse_batch_bound(raw: str, label: str) -> datetime:
    """JST instant. today / yesterday / YYYY-MM-DD are that day's 00:00."""
    s = raw.strip()
    low = s.lower()
    now = _jst_now()
    if low == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if low == "yesterday":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=1)
    match = SHORT_SINCE_RE.fullmatch(s)
    if match:
        n = int(match.group(1))
        unit = match.group(2).lower()
        return now - timedelta(**{SINCE_UNITS[unit]: n})
    if ISO_RE.fullmatch(s):
        norm = s.replace("T", " ")
        try:
            if len(norm) == 10:
                return datetime.strptime(norm, "%Y-%m-%d")
            if len(norm) == 16:
                return datetime.strptime(norm, "%Y-%m-%d %H:%M")
            return datetime.strptime(norm, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError(f"invalid {label}. use {_BOUND_HINT}") from None
    raise ValueError(f"invalid {label}. use {_BOUND_HINT}")


def _optional_bound(raw: Any, label: str) -> datetime | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"invalid {label}. use {_BOUND_HINT}")
    if not raw.strip():
        return None
    return parse_batch_bound(raw, label)


def parse_batch_window(args: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    # until is exclusive, same tokens as journalctl. yesterday's lines: since=yesterday, until=today.
    start = _optional_bound(args.get("since"), "since")
    end = _optional_bound(args.get("until"), "until")
    if start is not None and end is not None and start >= end:
        raise ValueError("until must be after since")
    return start, end


def batch_log_files() -> list[Path]:
    """Job logs next to batch.log. App and MCP logs stay on their own tools."""
    primary = pick_log("batch.log")
    directory = primary.parent
    found: list[Path] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.log")):
            if not path.is_file() or path.name in BATCH_LOG_SKIP:
                continue
            if JOB_NAME_RE.fullmatch(path.stem) is None:
                continue
            found.append(path)
    return found or [primary]


def rotated_log_paths(directory: Path, stem: str) -> list[Path]:
    """Oldest logrotate sibling first, then the live stem.log. .1 is plain, later ones .gz."""
    if JOB_NAME_RE.fullmatch(stem) is None:
        return []
    return rotated_siblings(directory / f"{stem}.log")


def rotated_siblings(live: Path) -> list[Path]:
    """live とその logrotate 済み (.1 / .N.gz)。古い順。live は呼び出し側が固定したパスだけ。"""
    directory = live.parent
    rx = re.compile(rf"^{re.escape(live.name)}\.([1-9][0-9]*)(?:\.gz)?$")
    numbered: list[tuple[int, str, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            if not path.is_file():
                continue
            match = rx.fullmatch(path.name)
            if match is None:
                continue
            numbered.append((int(match.group(1)), path.name, path))
    numbered.sort(key=lambda item: (-item[0], item[1]))
    paths = [item[2] for item in numbered]
    if live.is_file():
        paths.append(live)
    return paths


def job_log_dir(stem: str) -> Path:
    directory = log_dir()
    if directory.is_dir() and rotated_log_paths(directory, stem):
        return directory
    return pick_log(f"{stem}.log").parent


def _read_log_text(path: Path) -> str:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as handle:
            return handle.read()
    return path.read_text(errors="replace")


def _filter_grep(rows: list[str], grep: str | None) -> list[str]:
    if not grep:
        return rows
    try:
        rx = re.compile(grep)
    except re.error:
        return [ln for ln in rows if grep in ln]
    return [ln for ln in rows if rx.search(ln)]


def _format_tail(rows: list[str], lines: int) -> str:
    truncated = len(rows) > lines
    kept = rows[-lines:]
    if not kept:
        return "(no matching lines)"
    body = "\n".join(kept)
    if truncated:
        return "(truncated; narrow since/until)\n" + body
    return body


def _windowed_text(
    directory: Path,
    stem: str,
    start: datetime | None,
    end: datetime | None,
    lines: int,
    grep: str | None,
) -> str:
    paths = rotated_log_paths(directory, stem)
    if not paths:
        return f"(missing {directory / f'{stem}.log'})"
    return _window_paths(paths, _cron_stamp, start, end, lines, grep)


def _cron_stamp(line: str) -> datetime | None:
    match = LINE_STAMP_RE.match(line)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")


def _access_stamp(line: str) -> datetime | None:
    match = ACCESS_STAMP_RE.search(line)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(match.group(1), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    return stamp.astimezone(JST).replace(tzinfo=None)


def _error_stamp(line: str) -> datetime | None:
    match = ERROR_STAMP_RE.match(line)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y/%m/%d %H:%M:%S")


def _window_paths(
    paths: list[Path],
    stamp_of: Callable[[str], datetime | None],
    start: datetime | None,
    end: datetime | None,
    lines: int,
    grep: str | None,
) -> str:
    """JST の [start, end) に入る行。"""
    collected: list[tuple[datetime, int, str]] = []
    for path in paths:
        if start is not None:
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, JST).replace(tzinfo=None)
            except OSError:
                mtime = None
            # 最後の書き込みが start より前なら中身も全部 start より前
            if mtime is not None and mtime < start:
                continue
        try:
            text = _read_log_text(path)
        except OSError as exc:
            return f"(cannot read {path}: {exc})"
        stamp: datetime | None = None
        for line in text.splitlines():
            # 時刻の無い行（Traceback の続きなど）は直前の行に属する。ファイル先頭のものは捨てる
            stamp = stamp_of(line) or stamp
            if stamp is None:
                continue
            if start is not None and stamp < start:
                continue
            if end is not None and stamp >= end:
                continue
            collected.append((stamp, len(collected), line))
    collected.sort()
    rows = _filter_grep([line for _, _, line in collected], grep)
    return _format_tail(rows, lines)


def _rotated_window(live: Path, stamp_of: Callable[[str], datetime | None], start: datetime | None,
                    end: datetime | None, lines: int, grep: str | None) -> str:
    paths = rotated_siblings(live)
    if not paths:
        return f"(missing {live})"
    return _window_paths(paths, stamp_of, start, end, lines, grep)


def _live_batch_logs(job: str | None, lines: int, grep: str | None) -> str:
    if job is not None:
        return tail_grep(pick_log(f"{job}.log"), lines, grep)
    paths = batch_log_files()
    if len(paths) == 1 and paths[0].name == "batch.log":
        return tail_grep(paths[0], lines, grep)
    return "\n\n".join(f"=== {path.name} ===\n{tail_grep(path, lines, grep)}" for path in paths)


def tool_batch_logs(args: dict[str, Any]) -> str:
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    job = parse_batch_job(args.get("job"))
    start, end = parse_batch_window(args)
    if start is None and end is None:
        return _live_batch_logs(job, lines, grep)
    if job is not None:
        return _windowed_text(job_log_dir(job), job, start, end, lines, grep)
    paths = batch_log_files()
    parts = [
        f"=== {path.name} ===\n{_windowed_text(path.parent, path.stem, start, end, lines, grep)}"
        for path in paths
    ]
    return "\n\n".join(parts)


def tool_nginx_logs(args: dict[str, Any]) -> str:
    lines = clamp_lines(args.get("lines"))
    grep = parse_grep(args.get("grep"))
    start, end = parse_batch_window(args)
    if start is None and end is None:
        since = parse_since(args.get("since"))
        journal_out = journal("nginx", since, lines, grep)
        file_out = tail_grep(NGINX_ERROR_LOG, lines, grep)
        access_out = tail_grep(NGINX_ACCESS_LOG, lines, grep)
        label = f"since {since}"
    else:
        since, until, label = _journal_window(start, end)
        journal_out = journal("nginx", since, lines, grep, until)
        file_out = _rotated_window(NGINX_ERROR_LOG, _error_stamp, start, end, lines, grep)
        access_out = _rotated_window(NGINX_ACCESS_LOG, _access_stamp, start, end, lines, grep)
    return (f"=== journal nginx {label} ===\n{journal_out}\n\n=== {NGINX_ERROR_LOG} ===\n{file_out}"
            f"\n\n=== {NGINX_ACCESS_LOG} (rt=nginx 全体 / urt=uvicorn、秒) ===\n{redact_url(access_out)}")


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
    """TDnet 4-digit (9279) and JPX display (262A) become J-Quants 5-digit.

    Same function for db_lookup, monthly fy_end lookup, and monthly save key.
    charset + explicit . / blocks path traversal in /api/v1/tables/{code}/...
    """
    s = str(raw or "").strip().upper()
    if not CODE_RE.fullmatch(s) or "." in s or "/" in s:
        raise ValueError(_CODE_ERR)
    if len(s) == 4:
        s = s + "0"
    return s


def parse_monthly_code(raw: Any) -> str:
    return parse_code(raw)


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


MONTHLY_UNITS = frozenset({"number", "yoy_index", "yoy_pct"})


def parse_monthly_unit(raw: Any) -> str:
    """PDF heading decides unit. Do not infer from numeric magnitude."""
    if not isinstance(raw, str) or raw not in MONTHLY_UNITS:
        raise ValueError("unit must be number, yoy_index, or yoy_pct")
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
        if tail in (MONTHLY_INGEST_TAIL, MONTHLY_INDUSTRY_TAIL):
            if year is not None:
                raise ValueError("invalid monthly API path")
            path = f"{MONTHLY_PATH_PREFIX}/{safe_code}{tail}"
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


def monthly_industry_url(code: str) -> str:
    """PUT /api/v1/tables/{code}/industry. No year in the path."""
    return monthly_api_url(code, tail=MONTHLY_INDUSTRY_TAIL)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _monthly_opener() -> urllib.request.OpenerDirector:
    # No HTTP(S)_PROXY: requests must stay on 127.0.0.1. Do not follow redirects off-path.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler)


def call_monthly_api(method: str, url: str, body: dict[str, Any] | None = None) -> str:
    if method not in ("GET", "POST", "PUT", "DELETE"):
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


def _http_status_is_error(text: str) -> bool:
    first = text.split("\n", 1)[0]
    if not first.startswith("HTTP "):
        return False
    parts = first.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return False
    return int(parts[1]) >= 400


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
            elif table == "fx_rates":
                mn, mx, quotes, days = conn.execute(
                    "SELECT MIN(date), MAX(date), COUNT(DISTINCT quote), COUNT(DISTINCT date) FROM fx_rates"
                ).fetchone()
                extra = f"  {mn}..{mx}  quotes={quotes} days={days}"
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


def parse_iso_date(raw: Any, label: str) -> date:
    s = str(raw or "").strip()
    if not DATE_ONLY_RE.fullmatch(s):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc


def _fx_mod():
    from app.fx import (  # noqa: PLC0415
        FX_EARLIEST,
        HOLIDAY_NOTE,
        FxLookupError,
        cross_rate,
        needed_quotes,
        parse_pair,
    )

    return FX_EARLIEST, HOLIDAY_NOTE, FxLookupError, cross_rate, needed_quotes, parse_pair


def _fx_open(conn: sqlite3.Connection) -> None:
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fx_rates" not in names:
        raise ValueError("fx_rates table is missing; run python jobs/fx_rates.py --backfill")


def _fx_as_of(conn: sqlite3.Connection, requested: str, quotes: tuple[str, ...]) -> str | None:
    if not quotes:
        row = conn.execute(
            "SELECT MAX(date) FROM fx_rates WHERE date <= ?", (requested,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    placeholders = ",".join("?" * len(quotes))
    row = conn.execute(
        "SELECT date FROM fx_rates "
        f"WHERE date <= ? AND quote IN ({placeholders}) "
        "GROUP BY date HAVING COUNT(DISTINCT quote) >= ? "
        "ORDER BY date DESC LIMIT 1",
        (requested, *quotes, len(quotes)),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _fx_eur_rates(conn: sqlite3.Connection, as_of: str, quotes: tuple[str, ...]) -> dict[str, float]:
    if not quotes:
        return {"EUR": 1.0}
    placeholders = ",".join("?" * len(quotes))
    rows = conn.execute(
        f"SELECT quote, rate FROM fx_rates WHERE date = ? AND quote IN ({placeholders})",
        (as_of, *quotes),
    ).fetchall()
    rates = {"EUR": 1.0}
    for row in rows:
        rates[str(row["quote"])] = float(row["rate"])
    return rates


def _fx_pair_args(args: dict[str, Any]) -> tuple[str, str]:
    _earliest, _note, err, _cross, _need, parse_pair = _fx_mod()
    try:
        return parse_pair(args.get("pair"), args.get("base"), args.get("quote"))
    except err as exc:
        raise ValueError(str(exc)) from exc


def tool_fx_rate(args: dict[str, Any]) -> str:
    fx = _fx_mod()
    earliest, note, err, cross_rate, needed_quotes, _parse = fx
    requested = parse_iso_date(args.get("date"), "date")
    if requested < earliest:
        raise ValueError(f"date must be on or after {earliest.isoformat()}")
    base, quote = _fx_pair_args(args)
    conn = open_db()
    try:
        _fx_open(conn)
        need = needed_quotes(base, quote)
        as_of = _fx_as_of(conn, requested.isoformat(), need)
        if as_of is None:
            raise ValueError(
                f"no FX rate on or before {requested.isoformat()} for {base}/{quote}"
            )
        rates = _fx_eur_rates(conn, as_of, need)
        try:
            rate = cross_rate(rates, base, quote)
        except err as exc:
            raise ValueError(str(exc)) from exc
        body = {
            "pair": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "requested_date": requested.isoformat(),
            "as_of": as_of,
            "holiday_fallback": as_of != requested.isoformat(),
            "rate": rate,
            "holiday_policy": note,
        }
    finally:
        conn.close()
    return json.dumps(body, ensure_ascii=False, indent=2)


def tool_fx_rates(args: dict[str, Any]) -> str:
    fx = _fx_mod()
    earliest, note, err, cross_rate, needed_quotes, _parse = fx
    start = parse_iso_date(args.get("from") or args.get("start"), "from")
    end = parse_iso_date(args.get("to") or args.get("end"), "to")
    if end < start:
        raise ValueError("to must be on or after from")
    if start < earliest:
        raise ValueError(f"from must be on or after {earliest.isoformat()}")
    base, quote = _fx_pair_args(args)
    limit = clamp_lines(args.get("lines") or FX_MAX_OBS)
    conn = open_db()
    try:
        _fx_open(conn)
        need = needed_quotes(base, quote)
        observations: list[dict[str, Any]] = []
        truncated = False
        if not need:
            rows = conn.execute(
                "SELECT DISTINCT date FROM fx_rates "
                "WHERE date >= ? AND date <= ? ORDER BY date LIMIT ?",
                (start.isoformat(), end.isoformat(), limit + 1),
            ).fetchall()
            truncated = len(rows) > limit
            for row in rows[:limit]:
                day = str(row["date"])
                observations.append({
                    "date": day, "as_of": day, "holiday_fallback": False, "rate": 1.0,
                })
        else:
            placeholders = ",".join("?" * len(need))
            rows = conn.execute(
                "SELECT date, quote, rate FROM fx_rates "
                f"WHERE date >= ? AND date <= ? AND quote IN ({placeholders}) "
                "ORDER BY date, quote",
                (start.isoformat(), end.isoformat(), *need),
            ).fetchall()
            by_day: dict[str, dict[str, float]] = {}
            for row in rows:
                by_day.setdefault(str(row["date"]), {})[str(row["quote"])] = float(row["rate"])
            for day in sorted(by_day):
                rates = dict(by_day[day])
                if any(c not in rates for c in need):
                    continue
                rates["EUR"] = 1.0
                try:
                    rate = cross_rate(rates, base, quote)
                except err as exc:
                    raise ValueError(str(exc)) from exc
                observations.append({
                    "date": day, "as_of": day, "holiday_fallback": False, "rate": rate,
                })
                if len(observations) >= limit:
                    truncated = True
                    break
        body = {
            "pair": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "count": len(observations),
            "truncated": truncated,
            "holiday_policy": (
                note
                + " 期間指定では公表日だけを返す。休日を直近営業日で埋めたいときは fx_rate にその日を渡す。"
            ),
            "observations": observations,
        }
    finally:
        conn.close()
    return json.dumps(body, ensure_ascii=False, indent=2)


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
    if "unit" not in args or args["unit"] is None:
        raise ValueError("unit is required")
    body: dict[str, Any] = {
        "element": element,
        "unit": parse_monthly_unit(args["unit"]),
    }
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


def tool_monthly_delete_element(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    element = args.get("element")
    if not isinstance(element, str) or not element.strip():
        raise ValueError("element is required")
    body = {"element": element}
    return call_monthly_api("DELETE", monthly_api_url(code, year, "/elements"), body)


def tool_monthly_get_doc(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    return call_monthly_api("GET", monthly_api_url(code, year, "/doc"))


def tool_monthly_put_doc(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    year = parse_monthly_year(args.get("year"))
    version = args.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("version is required (from monthly_get_doc)")
    elements = args.get("elements")
    if not isinstance(elements, list):
        raise ValueError("elements must be an array")
    body: dict[str, Any] = {"version": version, "elements": elements}
    for flag in ("allow_structure_change", "dry_run"):
        if flag in args:
            if not isinstance(args[flag], bool):
                raise ValueError(f"{flag} must be a boolean")
            body[flag] = args[flag]
    return call_monthly_api("PUT", monthly_api_url(code, year, "/doc"), body)


def _parse_ingest_months(raw: Any) -> list[int]:
    # Genky-style year-wrap may repeat a month (e.g. 13 cols with June twice).
    if not isinstance(raw, list) or len(raw) < 1:
        raise ValueError("months must be a non-empty array of integers 1-12")
    return [parse_monthly_month(item) for item in raw]


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
        if not isinstance(values, (dict, list)):
            raise ValueError(
                "values must be an object of month -> number|null, "
                "or a column-aligned array when months has duplicates"
            )
        row: dict[str, Any] = {"element": element, "values": values}
        if "force" in item:
            if not isinstance(item["force"], bool):
                raise ValueError("force must be a boolean")
            row["force"] = item["force"]
        if "unit" not in item or item["unit"] is None:
            raise ValueError("unit is required")
        row["unit"] = parse_monthly_unit(item["unit"])
        out.append(row)
    return out


def parse_monthly_industry(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("industry must be 1-50 characters")
    s = raw.strip()
    if not s or len(s) > 50:
        raise ValueError("industry must be 1-50 characters")
    return s


def tool_monthly_set_industry(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    body = {"industry": parse_monthly_industry(args.get("industry"))}
    return call_monthly_api("PUT", monthly_industry_url(code), body)


_ONLY_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _parse_only_months(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("only_months must be a non-empty array of YYYY-MM")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or _ONLY_MONTH_RE.fullmatch(item) is None:
            raise ValueError("only_months must be YYYY-MM")
        out.append(item)
    return out


def tool_monthly_ingest(args: dict[str, Any]) -> str:
    code = parse_monthly_code(args.get("code"))
    body: dict[str, Any] = {
        "months": _parse_ingest_months(args.get("months")),
        "rows": _parse_ingest_rows(args.get("rows")),
    }
    as_of = args.get("as_of")
    if as_of is None:
        raise ValueError("as_of is required")
    if not isinstance(as_of, str):
        raise ValueError("as_of must be YYYY-MM-DD")
    body["as_of"] = as_of
    if "dry_run" in args:
        if not isinstance(args["dry_run"], bool):
            raise ValueError("dry_run must be a boolean")
        body["dry_run"] = args["dry_run"]
    if "only_months" in args:
        body["only_months"] = _parse_only_months(args["only_months"])
    return call_monthly_api("POST", monthly_ingest_url(code), body)


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], str]]] = {
    "service_status": (
        "nginx / stock-analyzer / MCP の active 状態、待ち受けポート、localhost:80 / :8000 の HTTP コード。MCP 自身はこの応答自体を ok とし、自分へは curl しない。",
        {"type": "object", "properties": {}},
        tool_service_status,
    ),
    "app_logs": (
        ("uvicorn (stock-analyzer) の /var/log/app/stock-analyzer.log と journalctl。"
         "since / until 省略時は journal が直近 1h、ファイルは live の末尾だけ。"
         "どちらかを指定すると .1 と .gz も読み、行頭の JST YYYY-MM-DD HH:MM:SS で絞る（時刻の無い行は直前の行に付く）。"
         "until はその時刻を含まない。昨日は since=yesterday と until=today。lines は 1-500。超えた分は末尾だけ返す。"),
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive."},
                "until": {
                    "type": "string",
                    "description": "Same forms as since. Exclusive. Yesterday is since=yesterday until=today.",
                },
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200"},
            },
        },
        tool_app_logs,
    ),
    "batch_logs": (
        (
            "cron の /var/log/app/<ジョブ>.log。"
            f"job は crontab のスクリプト名（{', '.join(sorted(BATCH_JOBS))}）。"
            "一覧に無い名前とパスは拒否。"
            "省略時は stock-analyzer.log と mcp.log 以外の live ファイル（古い batch.log を含む）。"
            "since / until は 1h / 30m / today / yesterday / YYYY-MM-DD。"
            "until はその時刻を含まない。昨日は since=yesterday と until=today。"
            "指定時は .1 と .gz も読み、行頭の JST YYYY-MM-DD HH:MM:SS で絞る。"
            "lines は 1-500。超えた分は末尾だけ返すので since / until を狭める。"
        ),
        {
            "type": "object",
            "properties": {
                "job": {"type": "string", "description": "crontab script stem without .py, e.g. xbrl_parse"},
                "since": {"type": "string", "description": "1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive."},
                "until": {
                    "type": "string",
                    "description": "Same forms as since. Exclusive. Yesterday is since=yesterday until=today.",
                },
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200. Narrow since/until past 500."},
            },
        },
        tool_batch_logs,
    ),
    "nginx_logs": (
        ("nginx の journal・/var/log/nginx/error.log・アクセスログ（rt/urt に処理秒）。"
         "遅いリクエストは grep に 'rt=[1-9]' などを渡す。"
         "since / until 省略時は journal が直近 1h、ファイルは live の末尾だけ。"
         "どちらかを指定すると .1 と .gz も読み、行の時刻（JST）で絞る。until はその時刻を含まない。"
         "昨日は since=yesterday と until=today。lines は 1-500。超えた分は末尾だけ返すので範囲を狭める。"),
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "1h, 30m, today, yesterday, or YYYY-MM-DD. Inclusive."},
                "until": {
                    "type": "string",
                    "description": "Same forms as since. Exclusive. Yesterday is since=yesterday until=today.",
                },
                "grep": {"type": "string", "description": "regex or substring, max 120 chars"},
                "lines": {"type": "integer", "description": "1-500, default 200. Narrow since/until past 500."},
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
    "fx_rate": (
        (
            "指定日の為替レート。保存は EUR ベース（ECB / Frankfurter）。"
            "任意ペアは EUR クロス（例: USD/JPY = EURJPY / EURUSD）。"
            "休日・ECB非公表日は指定日以前の直近営業日（as_of）を使う。"
            "requested_date と as_of が違うときは holiday_fallback=true。"
            "pair（USD/JPY）または base と quote。読み取り専用。"
        ),
        {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Weekends/holidays use the previous ECB business day."},
                "pair": {"type": "string", "description": "USD/JPY. Alternative to base+quote."},
                "base": {"type": "string", "description": "ISO 4217, e.g. USD. Ignored if pair is set."},
                "quote": {"type": "string", "description": "ISO 4217, e.g. JPY. Ignored if pair is set."},
            },
            "required": ["date"],
        },
        tool_fx_rate,
    ),
    "fx_rates": (
        (
            "期間の為替レート（公表日のみ）。保存は EUR ベースのクロス計算。"
            "休日は系列に含めない。特定日を直近営業日で埋めたいときは fx_rate。"
            "pair（USD/JPY）または base と quote。lines は 1-500。"
            "読み取り専用。"
        ),
        {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "YYYY-MM-DD inclusive"},
                "to": {"type": "string", "description": "YYYY-MM-DD inclusive"},
                "pair": {"type": "string", "description": "USD/JPY. Alternative to base+quote."},
                "base": {"type": "string", "description": "ISO 4217, e.g. USD. Ignored if pair is set."},
                "quote": {"type": "string", "description": "ISO 4217, e.g. JPY. Ignored if pair is set."},
                "lines": {"type": "integer", "description": "max observations, 1-500, default 500"},
            },
            "required": ["from", "to"],
        },
        tool_fx_rates,
    ),
    "monthly_list": (
        "月次表がある銘柄一覧。localhost:8000 の GET /api/v1/tables（nginx Basic は使わない）。",
        {"type": "object", "properties": {}},
        tool_monthly_list,
    ),
    "monthly_get": (
        "銘柄×年度の月次表。localhost:8000 の GET /api/v1/tables/{code}/{year}。values は 1〜12。無い月は null。0 は実ゼロ。",
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
        "月次表に要素を登録。localhost:8000 の POST /api/v1/tables/{code}/{year}/elements。unit は PDF 見出しから (number / yoy_index / yoy_pct)。yoy_pct の POST/PUT は保存 unit を yoy_index にする。類似は 409。force=true で強制。既存行の unit 変更は同じパスへ PUT {element, unit}。PUT unit は値を書き換えない。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
                "element": {"type": "string", "description": "row label, 1-50 chars"},
                "unit": {
                    "type": "string",
                    "enum": ["number", "yoy_index", "yoy_pct"],
                    "description": "From PDF heading, never from magnitude. number=count. yoy_index=100 is flat, store as-is. yoy_pct=signed change (e.g. -7.1); POST stores yoy_index. Value +100 is ingest, not this tool.",
                },
                "force": {"type": "boolean", "description": "create even if a similar element exists"},
            },
            "required": ["code", "year", "element", "unit"],
        },
        tool_monthly_add_element,
    ),
    "monthly_put_values": (
        "月次セルを一括 upsert（訂正用）。localhost:8000 の PUT /api/v1/tables/{code}/{year}/values/batch。保存形（指数）を送る。unit は無い。+100 しない。value の null は削除。新規登録は monthly_ingest。",
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
    "monthly_delete_element": (
        "月次の空行を消す。localhost:8000 の DELETE /api/v1/tables/{code}/{year}/elements。値が残っていれば 409。表は残す。新規登録は monthly_ingest。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
                "element": {"type": "string", "description": "existing row label"},
            },
            "required": ["code", "year", "element"],
        },
        tool_monthly_delete_element,
    ),
    "monthly_get_doc": (
        "銘柄×年度の月次表を 1 枚の文書で返す。localhost:8000 の GET /api/v1/tables/{code}/{year}/doc。空の行も含む。行ごとに element / unit（保存形）/ values（\"1\"〜\"12\"）/ tree（木の位置）。version は monthly_put_doc にそのまま渡す。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
            },
            "required": ["code", "year"],
        },
        tool_monthly_get_doc,
    ),
    "monthly_put_doc": (
        "monthly_get_doc の文書を直して丸ごと返す。localhost:8000 の PUT /api/v1/tables/{code}/{year}/doc。1 トランザクション。行名は渡したまま（正規化・エイリアスを掛けない）。行の改名は was に元の名前、element に新しい名前（値はサーバーが移す）。文書に無い行は削除。was の無い新しい名前は作成（unit 必須）。values を省いた行は値をそのまま、渡した行は 12 か月を丸ごと置き換え（無い月・null は削除）。values は保存形（+100 しない）。version が GET 後に変わっていれば 409 VERSION_MISMATCH（GET からやり直す）。改名・作成・削除・unit 変更は allow_structure_change=true のときだけ（無ければ 409 STRUCTURE_CHANGED）。dry_run=true は書かずに差分（changes）を返す。木に入らない行は warnings。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
                "version": {"type": "string", "description": "version from monthly_get_doc"},
                "elements": {
                    "type": "array",
                    "description": "all rows of the table after the edit; rows left out are deleted",
                    "items": {
                        "type": "object",
                        "properties": {
                            "element": {"type": "string", "description": "row label after the edit, 1-50 chars, stored as given"},
                            "was": {"type": "string", "description": "current label when renaming; omit when unchanged"},
                            "unit": {"type": "string", "enum": ["number", "yoy_index"], "description": "stored unit; required for new rows"},
                            "values": {
                                "type": "object",
                                "description": "{\"1\": value, ..., \"12\": value}; replaces all 12 months; null or missing deletes; omit to keep values",
                            },
                        },
                        "required": ["element"],
                    },
                },
                "allow_structure_change": {"type": "boolean", "description": "allow rename / create / delete / unit change"},
                "dry_run": {"type": "boolean", "description": "return changes without writing"},
            },
            "required": ["code", "year", "version", "elements"],
        },
        tool_monthly_put_doc,
    ),
    "monthly_set_industry": (
        "月次業種を付ける。localhost:8000 の PUT /api/v1/tables/{code}/industry。銘柄マスタの業種とは別。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "industry": {"type": "string", "description": "月次業種, 1-50 chars. Default その他 until set."},
            },
            "required": ["code", "industry"],
        },
        tool_monthly_set_industry,
    ),
    "monthly_ingest": (
        "PDF月次を暦年に割り当てて登録。localhost:8000 の POST /api/v1/tables/{code}/ingest。year は渡さない（決算の事業年度末から決める）。dry_run=true は fy_end_month / layout / mapping と書くはずのセルを返し、DB には書かない。only_months は YYYY-MM で、mapping のあと該当列だけ書く。年付き put は訂正用で unit も +100 もしない。unit は PDF 見出しから number / yoy_index / yoy_pct（大きさから推測しない）。yoy_pct は符号付き変化率。ingest は yoy_index と value+100 で書く。yoy_index と number はそのまま。新規行で、strip 後の名前が同じ number と yoy の既存行が rows に無いときは 409 YOY_NUMBER_CONFLICT。同じ POST に両方あれば通る。force=true で作る。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (7203, 72030, 262A0)"},
                "months": {
                    "type": "array",
                    "description": "PDF column months in left-to-right order, integers 1-12. Duplicates allowed for year-wrap (e.g. Genky 13-col Jun…Jun).",
                    "items": {"type": "integer"},
                },
                "rows": {
                    "type": "array",
                    "description": "list of {element, values, unit, force?}. yoy_index and number store as-is. yoy_pct input is signed; ingest writes yoy_index and value+100.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "element": {"type": "string", "description": "row label, 1-50 chars"},
                            "values": {
                                "description": "Unique months: object month -> number|null. Duplicate months: JSON array aligned to months (or object keyed by column index \"0\"..). yoy_index/number as-is (125.8 stays 125.8). yoy_pct signed rate; ingest stores value+100 as yoy_index.",
                            },
                            "unit": {
                                "type": "string",
                                "enum": ["number", "yoy_index", "yoy_pct"],
                                "description": "From PDF heading, never from magnitude. 前年比 100基準 → yoy_index (as-is). ▲付き変化率 → yoy_pct input, ingest stores yoy_index and value+100. 店舗数 → number.",
                            },
                            "force": {"type": "boolean", "description": "create even if a similar element or a yoy/number twin exists"},
                        },
                        "required": ["element", "values", "unit"],
                    },
                },
                "as_of": {"type": "string", "description": "disclosure date YYYY-MM-DD"},
                "dry_run": {
                    "type": "boolean",
                    "description": "true: return fy_end_month, layout, mapping, and cells that would be written. Do not write the DB.",
                },
                "only_months": {
                    "type": "array",
                    "description": "Calendar YYYY-MM. After mapping, write only those columns.",
                    "items": {"type": "string"},
                },
            },
            "required": ["code", "months", "rows", "as_of"],
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
        return redact(text), _http_status_is_error(text)
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


# Claude.ai custom connectors POST https://claude.ai/api/mcp/auth_callback (and claude.com).
REDIRECT_HTTPS_HOSTS = frozenset({
    "grok.com",
    "x.ai",
    "cursor.com",
    "claude.ai",
    "claude.com",
    "anthropic.com",
})
REDIRECT_CUSTOM_SCHEMES = frozenset({"grok", "x-grok", "grok-app", "cursor"})


def _host_allowed(host: str, allowed: frozenset[str]) -> bool:
    return any(host == name or host.endswith("." + name) for name in allowed)


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
    if parsed.scheme in REDIRECT_CUSTOM_SCHEMES:
        return True
    if parsed.scheme != "https":
        return False
    return _host_allowed(host, REDIRECT_HTTPS_HOSTS)


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
<p>クライアントが stock-analyzer のログ MCP に接続しようとしています。GitHub secret と同じ <code>MCP_TOKEN</code> を入れて許可してください。</p>
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
                "name": str(body.get("client_name") or "mcp")[:80],
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

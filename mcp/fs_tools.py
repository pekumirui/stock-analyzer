"""Read-only file tools for the stock-analyzer MCP (data/ and /var/log/app).

No write/delete/exec. Roots are allowlisted aliases. Paths are realpath'd and
must stay inside the resolved root. A denylist is applied on top.
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

JST = timezone(timedelta(hours=9))

DEFAULT_LOG_DIR = Path("/var/log/app")
ROOT_ALIASES = frozenset({"data", "logs"})
DENY_PREFIXES: tuple[Path, ...] = (
    Path("/home/stock/backups"),
    Path("/home/stock/r2-share"),
)
DENY_NAME_PATTERNS: tuple[str, ...] = (
    ".env*",
    "*.db",
    "*.sqlite*",
    "stock.db",
    "*.pem",
    "id_*",
    "*.key",
    "rclone.conf",
)
DEFAULT_MAX_READ = 1024 * 1024
MAX_READ_CEILING = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 8.0
MAX_TIMEOUT = 30.0
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 2000
DEFAULT_GREP_MATCHES = 50
MAX_GREP_MATCHES = 500
GREP_RE = re.compile(r"^[^\n\r\x00]{1,120}$")
ENCODINGS = frozenset({"utf-8", "base64"})


def data_dir() -> Path:
    raw = (os.environ.get("MCP_DATA_DIR") or "").strip()
    if raw:
        return Path(raw)
    app = Path(os.environ.get("MCP_APP_DIR") or Path(__file__).resolve().parent.parent)
    return app / "data"


def logs_dir() -> Path:
    raw = (os.environ.get("MCP_LOG_DIR") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_LOG_DIR


def root_dir(alias: str) -> Path:
    if alias == "data":
        return data_dir()
    if alias == "logs":
        return logs_dir()
    raise ValueError("root must be data or logs")


def max_read_bytes() -> int:
    raw = (os.environ.get("MCP_FS_MAX_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_READ
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_READ
    return max(1, min(n, MAX_READ_CEILING))


def fs_timeout() -> float:
    raw = (os.environ.get("MCP_FS_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        n = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT
    return max(0.5, min(n, MAX_TIMEOUT))


class _Deadline:
    def __init__(self) -> None:
        self.end = time.monotonic() + fs_timeout()

    def check(self) -> None:
        if time.monotonic() >= self.end:
            raise ValueError("timeout")


def parse_root(raw: Any) -> str:
    if not isinstance(raw, str) or raw not in ROOT_ALIASES:
        raise ValueError("root must be data or logs")
    return raw


def _rel_parts(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, str):
        raise ValueError("path must be a relative path")
    s = raw.strip()
    if not s or s in {".", "./"}:
        return ()
    if "\x00" in s:
        raise ValueError("invalid path")
    if s.startswith("/") or s.startswith("\\"):
        raise ValueError("path must be relative")
    # Windows drive / UNC — never a relative path under the allowlisted root
    if len(s) >= 2 and s[1] == ":":
        raise ValueError("path must be relative")
    parts: list[str] = []
    for part in s.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError("path is outside allowed root")
        parts.append(part)
    return tuple(parts)


def _realpath(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _is_under(child: Path, parent: Path) -> bool:
    try:
        return child == parent or child.is_relative_to(parent)
    except ValueError:
        return False


def _name_denied(name: str) -> bool:
    lowered = name.lower()
    for pat in DENY_NAME_PATTERNS:
        if fnmatch.fnmatch(lowered, pat.lower()):
            return True
    return False


def _path_denied(path: Path) -> bool:
    for part in path.parts:
        if _name_denied(part):
            return True
    return _name_denied(path.name)


def _prefix_denied(path: Path) -> bool:
    real = _realpath(path)
    for raw in DENY_PREFIXES:
        prefix = _realpath(raw)
        if _is_under(real, prefix):
            return True
    return False


def resolve_fs_path(alias: str, rel: Any) -> Path:
    """Resolve a user path against an allowlisted root. Rejects escapes."""
    alias = parse_root(alias)
    parts = _rel_parts(rel)
    root = root_dir(alias)
    try:
        root_real = _realpath(root)
    except OSError as exc:
        raise ValueError(f"root {alias} is unavailable") from exc
    if not root_real.is_dir():
        raise ValueError(f"root {alias} is unavailable")
    if _prefix_denied(root_real) or _path_denied(root_real):
        raise ValueError("path is denied")
    joined = root_real.joinpath(*parts) if parts else root_real
    try:
        target = _realpath(joined)
    except OSError as exc:
        raise ValueError("cannot resolve path") from exc
    if not _is_under(target, root_real):
        raise ValueError("path is outside allowed root")
    if _prefix_denied(target) or _path_denied(target):
        raise ValueError("path is denied")
    return target


def clamp_int(raw: Any, default: int, lo: int, hi: int, label: str) -> int:
    if raw is None:
        n = default
    else:
        if isinstance(raw, bool) or not isinstance(raw, int):
            try:
                n = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be an integer {lo}-{hi}") from None
        else:
            n = raw
    if n < lo:
        raise ValueError(f"{label} must be an integer {lo}-{hi}")
    return min(n, hi)


def _parse_bool(raw: Any, default: bool, label: str) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ValueError(f"{label} must be a boolean")
    return raw


def _parse_encoding(raw: Any) -> str:
    if raw is None or raw == "":
        return "utf-8"
    if not isinstance(raw, str) or raw not in ENCODINGS:
        raise ValueError('encoding must be "utf-8" or "base64"')
    return raw


def parse_grep_pattern(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("pattern is required")
    s = raw.strip()
    if not s:
        raise ValueError("pattern is required")
    if not GREP_RE.fullmatch(s):
        raise ValueError("invalid pattern (1-120 chars, no newlines)")
    if s.startswith("-"):
        raise ValueError("pattern must not start with -")
    return s


def _mtime_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, JST).isoformat(timespec="seconds")


def _entry_type(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _safe_lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError:
        return None


def _visible(root_real: Path, path: Path) -> bool:
    try:
        real = _realpath(path)
    except OSError:
        return False
    if not _is_under(real, root_real):
        return False
    if _prefix_denied(real) or _path_denied(path) or _path_denied(real):
        return False
    return True


def _rel_display(root_real: Path, path: Path) -> str:
    try:
        return path.relative_to(root_real).as_posix()
    except ValueError:
        return path.name


def _list_entry(root_real: Path, path: Path, st: os.stat_result) -> dict[str, Any]:
    return {
        "name": path.name,
        "path": _rel_display(root_real, path),
        "type": _entry_type(st.st_mode),
        "size": int(st.st_size),
        "mtime": _mtime_iso(st.st_mtime),
    }


def _iter_dir(directory: Path) -> Iterator[Path]:
    try:
        names = sorted(os.listdir(directory), key=lambda n: n.lower())
    except OSError as exc:
        raise ValueError(f"cannot list {directory.name}: {exc}") from None
    for name in names:
        yield directory / name


def _walk_entries(root_real: Path, start: Path, recursive: bool, limit: int, deadline: _Deadline) -> tuple[list[dict[str, Any]], bool]:
    entries: list[dict[str, Any]] = []
    truncated = False
    stack = [start]
    seen: set[str] = set()
    while stack:
        deadline.check()
        current = stack.pop(0)
        key = str(current)
        if key in seen:
            continue
        seen.add(key)
        if current != start:
            st = _safe_lstat(current)
            if st is None or not _visible(root_real, current):
                continue
            if len(entries) >= limit:
                truncated = True
                break
            entries.append(_list_entry(root_real, current, st))
            if not recursive or not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
                continue
        elif not current.is_dir():
            continue
        if current == start or (recursive and current.is_dir() and not current.is_symlink()):
            try:
                children = list(_iter_dir(current))
            except ValueError:
                if current == start:
                    raise
                continue
            if current == start and not recursive:
                for child in children:
                    deadline.check()
                    st = _safe_lstat(child)
                    if st is None or not _visible(root_real, child):
                        continue
                    if len(entries) >= limit:
                        truncated = True
                        break
                    entries.append(_list_entry(root_real, child, st))
                break
            stack.extend(children)
    return entries, truncated


def _dump(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2)


def tool_fs_list(args: dict[str, Any]) -> str:
    alias = parse_root(args.get("root"))
    recursive = _parse_bool(args.get("recursive"), False, "recursive")
    limit = clamp_int(args.get("limit"), DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT, "limit")
    target = resolve_fs_path(alias, args.get("path"))
    deadline = _Deadline()
    if not target.exists():
        raise ValueError("path not found")
    st = _safe_lstat(target)
    if st is None:
        raise ValueError("path not found")
    root_real = _realpath(root_dir(alias))
    if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
        if not _visible(root_real, target):
            raise ValueError("path is denied")
        entries = [_list_entry(root_real, target, st)]
        truncated = False
    elif stat.S_ISDIR(st.st_mode):
        entries, truncated = _walk_entries(root_real, target, recursive, limit, deadline)
    else:
        raise ValueError("path is not a file or directory")
    rel = args.get("path")
    rel_s = rel.strip() if isinstance(rel, str) and rel.strip() not in {".", "./"} else ""
    return _dump({
        "root": alias,
        "path": rel_s,
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
    })


def _open_readonly(path: Path):
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    # Open the realpath'd file; O_NOFOLLOW still helps if the last component
    # was swapped for a symlink between resolve and open.
    if nofollow:
        flags |= nofollow
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot read path: {exc}") from None
    return os.fdopen(fd, "rb")


def tool_fs_read(args: dict[str, Any]) -> str:
    alias = parse_root(args.get("root"))
    if args.get("path") is None or (isinstance(args.get("path"), str) and not str(args.get("path")).strip()):
        raise ValueError("path is required")
    target = resolve_fs_path(alias, args.get("path"))
    encoding = _parse_encoding(args.get("encoding"))
    offset = clamp_int(args.get("offset"), 0, 0, 2**63 - 1, "offset")
    cap = max_read_bytes()
    length = clamp_int(args.get("length"), cap, 1, cap, "length")
    deadline = _Deadline()
    if not target.exists():
        raise ValueError("path not found")
    if target.is_dir():
        raise ValueError("path is a directory")
    if not target.is_file():
        raise ValueError("path is not a regular file")
    deadline.check()
    try:
        total = target.stat().st_size
    except OSError as exc:
        raise ValueError(f"cannot read path: {exc}") from None
    if offset > total:
        chunk = b""
    else:
        deadline.check()
        with _open_readonly(target) as handle:
            deadline.check()
            handle.seek(offset)
            chunk = handle.read(length)
    truncated = offset + len(chunk) < total
    if encoding == "base64":
        content: str = base64.b64encode(chunk).decode("ascii")
    else:
        content = chunk.decode("utf-8", errors="replace")
    rel = str(args.get("path") or "").strip()
    return _dump({
        "root": alias,
        "path": rel,
        "size": int(total),
        "offset": offset,
        "length": len(chunk),
        "encoding": encoding,
        "truncated": truncated,
        "content": content,
    })


def _compile_grep(pattern: str) -> re.Pattern[str] | str:
    try:
        return re.compile(pattern)
    except re.error:
        return pattern


def _match_line(rx: re.Pattern[str] | str, line: str) -> bool:
    if isinstance(rx, str):
        return rx in line
    return rx.search(line) is not None


def _iter_grep_files(root_real: Path, start: Path, recursive: bool, deadline: _Deadline) -> Iterator[Path]:
    if start.is_file():
        if _visible(root_real, start):
            yield start
        return
    if not start.is_dir():
        raise ValueError("path is not a file or directory")
    stack = [start]
    while stack:
        deadline.check()
        current = stack.pop(0)
        try:
            children = list(_iter_dir(current))
        except ValueError:
            continue
        for child in children:
            deadline.check()
            st = _safe_lstat(child)
            if st is None or not _visible(root_real, child):
                continue
            if stat.S_ISREG(st.st_mode):
                yield child
            elif recursive and stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
                stack.append(child)


def tool_fs_grep(args: dict[str, Any]) -> str:
    alias = parse_root(args.get("root"))
    if args.get("path") is None or (isinstance(args.get("path"), str) and not str(args.get("path")).strip()):
        raise ValueError("path is required")
    pattern = parse_grep_pattern(args.get("pattern"))
    max_matches = clamp_int(
        args.get("max_matches"), DEFAULT_GREP_MATCHES, 1, MAX_GREP_MATCHES, "max_matches",
    )
    target = resolve_fs_path(alias, args.get("path"))
    deadline = _Deadline()
    if not target.exists():
        raise ValueError("path not found")
    root_real = _realpath(root_dir(alias))
    rx = _compile_grep(pattern)
    matches: list[dict[str, Any]] = []
    truncated = False
    for file_path in _iter_grep_files(root_real, target, recursive=True, deadline=deadline):
        deadline.check()
        try:
            with _open_readonly(file_path) as handle:
                lineno = 0
                while True:
                    deadline.check()
                    raw = handle.readline()
                    if not raw:
                        break
                    lineno += 1
                    line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
                    if not _match_line(rx, line):
                        continue
                    matches.append({
                        "path": _rel_display(root_real, file_path),
                        "line": lineno,
                        "text": line,
                    })
                    if len(matches) >= max_matches:
                        truncated = True
                        break
        except ValueError:
            continue
        except OSError:
            continue
        if truncated:
            break
    rel = str(args.get("path") or "").strip()
    return _dump({
        "root": alias,
        "path": rel,
        "pattern": pattern,
        "count": len(matches),
        "truncated": truncated,
        "matches": matches,
    })

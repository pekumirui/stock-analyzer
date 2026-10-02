"""R2 バッチの共通処理。ネットワークと app/ はテストから分離する。app は import しない。

http_get は IR 側の実装（識別 UA が 403 ならブラウザ UA に一度だけ切替、
429 / 408 / 5xx は指数バックオフと Retry-After（上限あり）で再試行、
中間証明書の欠落は AIA で補う）。各ジョブの識別 UA は user_agent で渡す。
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import sqlite3
import ssl
import time
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parent.parent
HTTP_TIMEOUT = 30.0
HTTP_RETRY_ATTEMPTS = 5
HTTP_NETWORK_RETRY_ATTEMPTS = 2
HTTP_RETRY_AFTER_CAP = 60.0
HTTP_RETRY_BASE_SEC = 2.0
USER_AGENT = "stock-analyzer-ir-monthly/1.0"
# AWS ELB が識別 UA を 403 する発行体（大東建託）向け。通常は USER_AGENT を先に使う。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_AIA_OID = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x01, 0x01))
_CA_ISSUERS_OID = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x02))
_AIA_MAX_CERT = 64 * 1024
_AIA_CTX: dict[str, ssl.SSLContext | None] = {}
_BAD_FILENAME_RE = re.compile(r'[\x00-\x1f\\/:*?"<>|]')
_NON_SQLITE_SKIP = "warn stocks db not sqlite; infra skip uses company_name only"


def jst_today(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    return now.date()


def date_window(days: int, today: date | None = None) -> tuple[date, date]:
    if days < 1:
        raise ValueError("days must be a positive integer")
    end = today or jst_today()
    start = end - timedelta(days=days - 1)
    return start, end


def _dotenv_setdefault(text: str, environ: dict[str, str]) -> None:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        environ.setdefault(key, val)


def resolve_database_url(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """DATABASE_URL env -> repo .env (setdefault) -> <repo>/stock.db. env is not overwritten."""
    env = os.environ if environ is None else environ
    root = REPO_ROOT if repo_root is None else repo_root
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        return url
    merged = dict(env)
    src = root / ".env" if dotenv_path is None else dotenv_path
    try:
        _dotenv_setdefault(src.read_text(encoding="utf-8"), merged)
    except OSError:
        pass
    url = (merged.get("DATABASE_URL") or "").strip()
    if url:
        return url
    return f"sqlite:///{root / 'stock.db'}"


def sqlite_path_from_url(url: str, repo_root: Path | None = None) -> Path | None:
    raw = (url or "").strip()
    if not raw.startswith("sqlite:///"):
        return None
    rest = raw[len("sqlite:///") :].split("?", 1)[0]
    if not rest or rest == ":memory:":
        return None
    pth = Path(rest)
    if not pth.is_absolute():
        pth = (repo_root or REPO_ROOT) / pth
    return pth


def load_stock_product_map(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    repo_root: Path | None = None,
    db_path: Path | None = None,
    non_sqlite_warn: str = _NON_SQLITE_SKIP,
) -> dict[str, tuple[str | None, str | None]]:
    """code -> (product_category, name). empty dict on missing/non-sqlite/failure."""
    try:
        root = REPO_ROOT if repo_root is None else repo_root
        if db_path is None:
            url = resolve_database_url(
                environ=environ, dotenv_path=dotenv_path, repo_root=root
            )
            db_path = sqlite_path_from_url(url, repo_root=root)
            if db_path is None:
                print(non_sqlite_warn, flush=True)
                return {}
        if not db_path.is_file():
            print(f"warn stocks db missing {db_path}", flush=True)
            return {}
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=8)
        try:
            rows = conn.execute(
                "SELECT code, product_category, name FROM stocks"
            ).fetchall()
        finally:
            conn.close()
        out: dict[str, tuple[str | None, str | None]] = {}
        for code, cat, name in rows:
            if not code:
                continue
            out[str(code)] = (
                None if cat is None else str(cat),
                None if name is None else str(name),
            )
        return out
    except Exception as e:
        print(f"warn stocks db unreadable: {e}", flush=True)
        return {}


def sanitize_filename_part(s: str) -> str:
    # NFKC: 全角数字・英字を半角に（既存 R2 オブジェクト名と揃える）
    s = unicodedata.normalize("NFKC", s or "")
    s = _BAD_FILENAME_RE.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "unknown"


def yyyymmdd_from_pubdate(pubdate: str) -> str:
    s = (pubdate or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[0:4]}{s[5:7]}{s[8:10]}"
    digits = re.sub(r"\D", "", s)[:8]
    if len(digits) == 8:
        return digits
    raise ValueError(f"unusable pubdate: {pubdate!r}")


def parse_list_body(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    if text[0] not in "{[":
        left = text.find("(")
        right = text.rfind(")")
        if left != -1 and right > left:
            text = text[left + 1 : right]
    data = json.loads(text)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = None
        for key in ("Tdnet", "TDnet", "tdnet"):
            cand = item.get(key)
            if isinstance(cand, dict):
                inner = cand
                break
        if inner is None and ("title" in item or "document_url" in item):
            inner = item
        if inner:
            out.append(inner)
    return out


def _der_len(buf: bytes, i: int) -> tuple[int, int]:
    if i >= len(buf):
        raise ValueError("der truncated")
    b = buf[i]
    if b < 0x80:
        return b, i + 1
    n = b & 0x7F
    if n == 0 or n > 4 or i + 1 + n > len(buf):
        raise ValueError("der length")
    return int.from_bytes(buf[i + 1 : i + 1 + n], "big"), i + 1 + n


def _der_children(buf: bytes, start: int, end: int):
    i = start
    while i < end:
        if i + 1 >= len(buf):
            break
        tag = buf[i]
        try:
            length, v = _der_len(buf, i + 1)
        except ValueError:
            break
        e = v + length
        if e > end or e > len(buf):
            break
        yield tag, v, e
        i = e


def aia_ca_issuer_uris(der: bytes) -> list[str]:
    """Leaf DER から AIA CA Issuers URI を取る。無ければ空。"""
    needle = bytes((0x06, len(_AIA_OID))) + _AIA_OID
    idx = der.find(needle)
    if idx < 0:
        return []
    i = idx + len(needle)
    if i >= len(der):
        return []
    try:
        if der[i] == 0x01:
            length, v = _der_len(der, i + 1)
            i = v + length
        if i >= len(der) or der[i] != 0x04:
            return []
        length, v = _der_len(der, i + 1)
        octet = der[v : v + length]
        if not octet or octet[0] != 0x30:
            return []
        seq_len, seq_v = _der_len(octet, 1)
        out: list[str] = []
        for tag, vs, es in _der_children(octet, seq_v, seq_v + seq_len):
            if tag != 0x30:
                continue
            kids = list(_der_children(octet, vs, es))
            if len(kids) < 2:
                continue
            t0, a, b = kids[0]
            t1, c, d = kids[1]
            if t0 != 0x06 or octet[a:b] != _CA_ISSUERS_OID:
                continue
            if (t1 & 0x1F) != 6:
                continue
            try:
                uri = octet[c:d].decode("ascii")
            except UnicodeDecodeError:
                continue
            if uri.startswith(("http://", "https://")):
                out.append(uri)
        return out
    except ValueError:
        return []


def _is_incomplete_chain(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    for _ in range(6):
        if cur is None:
            return False
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        if "certificate verify failed" in str(cur).lower():
            return True
        nxt = cur.__cause__ or getattr(cur, "reason", None)
        if not isinstance(nxt, BaseException):
            nxt = cur.__context__
        cur = nxt if isinstance(nxt, BaseException) else None
    return False


def _peer_leaf_der(host: str, timeout: float) -> bytes:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
    if not der:
        raise OSError(f"no peer cert for {host}")
    return der


def _aia_cadata(blob: bytes) -> bytes | str:
    if blob.lstrip().startswith(b"-----BEGIN"):
        return blob.decode("ascii")
    return blob


def ssl_context_with_aia(
    url: str,
    timeout: float = HTTP_TIMEOUT,
    user_agent: str | None = None,
) -> ssl.SSLContext | None:
    """サーバーが中間を付けない／付け違えるとき、AIA の CA Issuers を足して検証する。"""
    from urllib.request import Request, urlopen

    ua = USER_AGENT if user_agent is None else user_agent
    host = urlparse(url).hostname
    if not host:
        return None
    if host in _AIA_CTX:
        return _AIA_CTX[host]
    ctx: ssl.SSLContext | None = None
    try:
        der = _peer_leaf_der(host, timeout)
        extras: list[bytes | str] = []
        for uri in aia_ca_issuer_uris(der)[:3]:
            if urlparse(uri).scheme.lower() not in ("http", "https"):
                continue
            try:
                req = Request(uri, headers={"User-Agent": ua, "Accept": "*/*"})
                with urlopen(req, timeout=timeout) as resp:  # nosec B310
                    blob = resp.read(_AIA_MAX_CERT + 1)
                if not blob or len(blob) > _AIA_MAX_CERT:
                    continue
                extras.append(_aia_cadata(blob))
            except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, ssl.SSLError):
                continue
        if extras:
            ctx = ssl.create_default_context()
            loaded = False
            for cadata in extras:
                try:
                    ctx.load_verify_locations(cadata=cadata)
                    loaded = True
                except ssl.SSLError:
                    continue
            if not loaded:
                ctx = None
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, ssl.SSLError):
        ctx = None
    _AIA_CTX[host] = ctx
    return ctx


def _is_retryable_http(code: int) -> bool:
    return code in (408, 429) or code >= 500


def retry_after_seconds(
    headers: object | None,
    *,
    now: datetime | None = None,
    cap: float = HTTP_RETRY_AFTER_CAP,
) -> float | None:
    """Retry-After を秒に直す。無い・不正なら None。待ちは cap で切る。"""
    if headers is None or cap < 0:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    raw = getter("Retry-After")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        n = float(text)
    except ValueError:
        n = None
    if n is not None:
        if not math.isfinite(n) or n < 0:
            return None
        return min(n, cap)
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    wait = (dt - ref.astimezone(dt.tzinfo)).total_seconds()
    if wait < 0:
        return 0.0
    return min(wait, cap)


def retry_sleep_seconds(attempt: int, err: BaseException | None = None) -> float:
    """attempt は失敗した試行の 0 始まり。Retry-After があればそれを優先。"""
    exp = min(HTTP_RETRY_AFTER_CAP, HTTP_RETRY_BASE_SEC * (2 ** attempt))
    if isinstance(err, HTTPError):
        ra = retry_after_seconds(err.headers)
        if ra is not None:
            return ra
    return exp


def http_get(
    url: str,
    timeout: float = HTTP_TIMEOUT,
    *,
    user_agent: str | None = None,
) -> bytes:
    """識別 UA で取り、403 のときだけブラウザ UA に切り替える。検証は切らない。

    429 / 408 / 5xx は同じ UA で指数バックオフ再試行する。Retry-After があれば
    それを優先（上限 HTTP_RETRY_AFTER_CAP）。レート制限に UA 切替は使わない。
    """
    from urllib.request import Request, urlopen

    primary = USER_AGENT if user_agent is None else user_agent
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {scheme or 'missing'}")
    last: Exception | None = None
    aia_tried = False
    for ua in (primary, BROWSER_UA):
        req = Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        for attempt in range(HTTP_RETRY_ATTEMPTS):
            try:
                with urlopen(req, timeout=timeout) as resp:  # nosec B310
                    return resp.read()
            except HTTPError as e:
                last = e
                if e.code == 403 and ua == primary:
                    break
                if not _is_retryable_http(e.code):
                    raise
                if attempt >= HTTP_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(retry_sleep_seconds(attempt, e))
            except (URLError, TimeoutError, OSError) as e:
                last = e
                if not aia_tried and _is_incomplete_chain(e):
                    aia_tried = True
                    ctx = ssl_context_with_aia(url, timeout, user_agent=primary)
                    if ctx is not None:
                        try:
                            with urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
                                return resp.read()
                        except (HTTPError, URLError, TimeoutError, OSError) as e2:
                            last = e2
                    raise last
                if attempt >= HTTP_NETWORK_RETRY_ATTEMPTS - 1:
                    break
                time.sleep(retry_sleep_seconds(attempt, e))
    assert last is not None
    raise last

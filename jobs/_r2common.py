"""バッチ共通処理。ネットワークはテストから差し替えられるよう引数で受ける。

http_get は IR 側の実装（識別 UA が 403 ならブラウザ UA に一度だけ切替、
中間証明書の欠落は AIA で補う）。各ジョブの識別 UA は user_agent で渡す。
既定 dest は data/。dest が FUSE `/home/stock/r2-share` 上のときの切断プロトコルもここ。
"""
from __future__ import annotations

import base64
import errno
import os
import random
import re
import socket
import ssl
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO_ROOT = Path(__file__).resolve().parent.parent
HTTP_TIMEOUT = 30.0
USER_AGENT = "stock-analyzer-ir-monthly/1.0"
HTTP_RETRY_ATTEMPTS = 6
HTTP_RETRY_BASE_SEC = 5.0
HTTP_RETRY_CAP_SEC = 60.0
HTTP_RETRYABLE = frozenset({408, 429})
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
R2_SHARE_ROOT_DEFAULT = "/home/stock/r2-share"
R2_SHARE_ROOT_ENV = "R2_SHARE_ROOT"
PROC_MOUNTS = Path("/proc/mounts")
# deploy/r2-share.service RestartSec. Two waits after the first probe.
MOUNT_RETRY_WAIT_SEC = 5.0
MOUNT_RETRIES = 2
_DISCONNECT_ERRNOS = frozenset(
    n
    for n in (
        errno.ECONNABORTED,
        errno.ENOTCONN,
        getattr(errno, "ESTALE", None),
    )
    if isinstance(n, int)
)
T = TypeVar("T")


class MountDisconnected(Exception):
    """R2 FUSE mount is down after retries. Jobs abort with this one line."""

    def __init__(self, detail: str = "") -> None:
        self.detail = (detail or "").strip()
        super().__init__(
            f"R2 マウント切断: {self.detail}" if self.detail else "R2 マウント切断"
        )


def r2_share_root(root: Path | str | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = (os.environ.get(R2_SHARE_ROOT_ENV) or "").strip()
    return Path(env or R2_SHARE_ROOT_DEFAULT)


def path_on_r2_share(path: Path | str, root: Path | str | None = None) -> bool:
    path_s = os.path.normpath(os.fspath(path))
    root_s = os.path.normpath(os.fspath(r2_share_root(root)))
    return path_s == root_s or path_s.startswith(root_s + os.sep)


def is_mount_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, MountDisconnected):
        return True
    err = getattr(exc, "errno", None)
    return isinstance(err, int) and err in _DISCONNECT_ERRNOS


def r2_mount_in_proc(root: Path | str | None = None, text: str | None = None) -> bool | None:
    """True if root is in /proc/mounts. None if /proc/mounts is unreadable."""
    if text is None:
        try:
            text = PROC_MOUNTS.read_text(encoding="utf-8")
        except OSError:
            return None
    needle = f" {os.path.normpath(os.fspath(r2_share_root(root)))} "
    return needle in text


def check_r2_mount(
    path: Path | str,
    *,
    require_existing: bool = True,
    retries: int = MOUNT_RETRIES,
    wait_sec: float = MOUNT_RETRY_WAIT_SEC,
    sleep: Callable[[float], None] = time.sleep,
    listdir: Callable[[str], list[str]] | None = None,
    proc_mounts: Callable[[], str] | None = None,
    r2_root: Path | str | None = None,
) -> None:
    """List path. On FUSE disconnect or R2-share dest gone, wait and retry.

    Raise MountDisconnected after retries. Other OSError (local dest missing)
    propagate. require_existing=False lets writers mkdir dest on a live mount.
    """
    dest_s = os.fspath(path)
    list_fn = os.listdir if listdir is None else listdir
    last_detail = ""
    attempts = max(0, retries) + 1
    for i in range(attempts):
        if path_on_r2_share(path, r2_root):
            mounted: bool | None
            if proc_mounts is None:
                mounted = r2_mount_in_proc(r2_root)
            else:
                try:
                    mounted = r2_mount_in_proc(r2_root, proc_mounts())
                except OSError:
                    mounted = None
            if mounted is False:
                last_detail = f"{r2_share_root(r2_root)} is not mounted"
                if i < attempts - 1:
                    sleep(wait_sec)
                    continue
                raise MountDisconnected(last_detail)
        try:
            list_fn(dest_s)
            return
        except OSError as e:
            last_detail = str(e)
            missing = e.errno in (errno.ENOENT, errno.ENOTDIR)
            if missing and not require_existing and not is_mount_disconnect_error(e):
                return
            disconnected = is_mount_disconnect_error(e) or (
                missing and path_on_r2_share(path, r2_root)
            )
            if not disconnected:
                raise
            if i < attempts - 1:
                sleep(wait_sec)
                continue
            raise MountDisconnected(last_detail) from e
    raise MountDisconnected(last_detail)


def with_mount_retry(
    fn: Callable[[], T],
    dest: Path | str,
    *,
    require_existing: bool = True,
    retries: int = MOUNT_RETRIES,
    wait_sec: float = MOUNT_RETRY_WAIT_SEC,
    sleep: Callable[[float], None] = time.sleep,
    listdir: Callable[[str], list[str]] | None = None,
    proc_mounts: Callable[[], str] | None = None,
    r2_root: Path | str | None = None,
) -> T:
    """Call fn. On FUSE abort or dest ENOENT, re-probe dest then call fn once more."""
    kwargs = {
        "require_existing": require_existing,
        "retries": retries,
        "wait_sec": wait_sec,
        "sleep": sleep,
        "listdir": listdir,
        "proc_mounts": proc_mounts,
        "r2_root": r2_root,
    }
    try:
        return fn()
    except OSError as e:
        if not (is_mount_disconnect_error(e) or e.errno == errno.ENOENT):
            raise
        try:
            check_r2_mount(dest, **kwargs)
        except OSError as probe:
            raise MountDisconnected(str(probe)) from e
        return fn()


def fail_if_mount_down(
    label: str,
    dest: Path | str,
    *,
    require_existing: bool = True,
    retries: int = MOUNT_RETRIES,
    wait_sec: float = MOUNT_RETRY_WAIT_SEC,
    sleep: Callable[[float], None] = time.sleep,
    listdir: Callable[[str], list[str]] | None = None,
    proc_mounts: Callable[[], str] | None = None,
    r2_root: Path | str | None = None,
) -> int | None:
    """Probe dest. Print `{label} R2 マウント切断…` and return 1 if down."""
    try:
        check_r2_mount(
            dest,
            require_existing=require_existing,
            retries=retries,
            wait_sec=wait_sec,
            sleep=sleep,
            listdir=listdir,
            proc_mounts=proc_mounts,
            r2_root=r2_root,
        )
    except MountDisconnected as e:
        print(f"{label} {e}", flush=True)
        return 1
    except OSError:
        return None
    return None


def abort_if_disconnected(
    label: str,
    dest: Path | str,
    exc: BaseException,
    *,
    require_existing: bool = False,
    retries: int = MOUNT_RETRIES,
    wait_sec: float = MOUNT_RETRY_WAIT_SEC,
    sleep: Callable[[float], None] = time.sleep,
    listdir: Callable[[str], list[str]] | None = None,
    proc_mounts: Callable[[], str] | None = None,
    r2_root: Path | str | None = None,
) -> int | None:
    """If exc is a FUSE disconnect, re-probe dest and return 1 when still down."""
    if isinstance(exc, MountDisconnected):
        print(f"{label} {exc}", flush=True)
        return 1
    if not is_mount_disconnect_error(exc):
        return None
    return fail_if_mount_down(
        label,
        dest,
        require_existing=require_existing,
        retries=retries,
        wait_sec=wait_sec,
        sleep=sleep,
        listdir=listdir,
        proc_mounts=proc_mounts,
        r2_root=r2_root,
    )


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
    from app.config import load_dotenv

    merged = dict(env)
    load_dotenv(root / ".env" if dotenv_path is None else dotenv_path, merged)
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
        # app.db は import 時に DATABASE_URL のエンジンを作る。sqlite でないときに読み込まないよう、ここで import
        from sqlalchemy import select

        from app.db import sqlite_readonly_engine
        from app.models import Stock

        engine = sqlite_readonly_engine(db_path)
        try:
            with engine.connect() as conn:
                rows = conn.execute(select(Stock.code, Stock.product_category, Stock.name)).all()
        finally:
            engine.dispose()
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


_INCOMPLETE_CHAIN_MARKERS = (
    "unable to get local issuer",
    "unable to get issuer certificate",
    "incomplete certificate chain",
)
# OpenSSL X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT / _LOCALLY
_INCOMPLETE_VERIFY_CODES = frozenset({2, 20})


def _is_incomplete_chain(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    for _ in range(6):
        if cur is None:
            return False
        code = getattr(cur, "verify_code", None)
        if isinstance(code, int) and code in _INCOMPLETE_VERIFY_CODES:
            return True
        msg = str(cur).lower()
        if any(marker in msg for marker in _INCOMPLETE_CHAIN_MARKERS):
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


def _aia_as_pem(cadata: bytes | str) -> bytes:
    if isinstance(cadata, str):
        text = cadata.strip()
        if not text.startswith("-----BEGIN"):
            raise ValueError("cadata str is not pem")
        return text.encode("ascii")
    blob = cadata
    if blob.lstrip().startswith(b"-----BEGIN"):
        return blob
    wrapped = base64.encodebytes(blob).decode("ascii")
    return ("-----BEGIN CERTIFICATE-----\n" + wrapped + "-----END CERTIFICATE-----\n").encode("ascii")


def _default_cafile() -> str | None:
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return paths.cafile
    try:
        import certifi

        where = certifi.where()
    except ImportError:
        return None
    if where and os.path.isfile(where):
        return where
    return None


def _aia_chains_to_default_trust(cadata: bytes | str) -> bool:
    """AIA の中身が既定トラストストアまで繋がるときだけ True。自己署名の差し替え CA は落とす。"""
    cafile = _default_cafile()
    if not cafile:
        return False
    try:
        pem = _aia_as_pem(cadata)
    except (UnicodeDecodeError, ValueError):
        return False
    fd, path = tempfile.mkstemp(prefix="stock-aia-", suffix=".pem")
    try:
        os.write(fd, pem)
        os.close(fd)
        fd = -1
        proc = subprocess.run(
            ["openssl", "verify", "-CAfile", cafile, path],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass


def ssl_context_with_aia(
    url: str,
    timeout: float = HTTP_TIMEOUT,
    user_agent: str | None = None,
) -> ssl.SSLContext | None:
    """サーバーが中間を付けないとき、AIA の CA Issuers を既定ルートに繋がる場合だけ足す。"""
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
                if not _aia_chains_to_default_trust(cadata):
                    continue
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


def is_retryable_http(code: int) -> bool:
    return code >= 500 or code in HTTP_RETRYABLE


def retry_after_seconds(err: BaseException, now: float | None = None) -> float | None:
    """HTTPError の Retry-After（秒または HTTP-date）。無ければ None。"""
    headers = getattr(err, "headers", None) or getattr(err, "hdrs", None)
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    base = time.time() if now is None else now
    return max(0.0, dt.timestamp() - base)


def backoff_delay(
    attempt: int,
    retry_after: float | None = None,
    *,
    rng: random.Random | None = None,
) -> float:
    """attempt は 0始まりの失敗回数。Retry-After があればそれを優先（上限あり）。"""
    if retry_after is not None:
        return min(HTTP_RETRY_CAP_SEC, max(0.0, retry_after))
    n = max(0, attempt)
    base = min(HTTP_RETRY_CAP_SEC, HTTP_RETRY_BASE_SEC * (2 ** n))
    roll = rng.random() if rng is not None else random.random()  # nosec B311
    return base * (0.75 + 0.5 * roll)


def _http_final_url(resp: object, requested: str) -> str:
    """urlopen の geturl()。モックが str 以外を返すときは要求 URL のまま。"""
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):
        return requested
    try:
        final = geturl()
    except Exception:
        return requested
    if not isinstance(final, str):
        return requested
    final = final.strip()
    if urlparse(final).scheme not in ("http", "https"):
        return requested
    return final


def http_get_result(
    url: str,
    timeout: float = HTTP_TIMEOUT,
    *,
    user_agent: str | None = None,
) -> tuple[bytes, str]:
    """識別 UA で取り、403 のときだけブラウザ UA に切り替える。検証は切らない。

    戻りは (本文, リダイレクト後の URL)。429 / 408 / 5xx は Retry-After があればそれに従い、
    無ければ指数バックオフ＋jitter。同一 UA で最大 HTTP_RETRY_ATTEMPTS 回。
    打ち切ったら例外を上げる（UA は変えない）。
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
        http_tries = 0
        net_tries = 0
        while True:
            try:
                with urlopen(req, timeout=timeout) as resp:  # nosec B310
                    return resp.read(), _http_final_url(resp, url)
            except HTTPError as e:
                last = e
                if e.code == 403 and ua == primary:
                    break
                if not is_retryable_http(e.code):
                    raise
                http_tries += 1
                if http_tries >= HTTP_RETRY_ATTEMPTS:
                    raise
                wait = backoff_delay(http_tries - 1, retry_after_seconds(e))
                print(f"retry {e.code} wait={wait:.1f}s {url}", flush=True)
                time.sleep(wait)
            except (URLError, TimeoutError, OSError) as e:
                last = e
                if not aia_tried and _is_incomplete_chain(e):
                    aia_tried = True
                    ctx = ssl_context_with_aia(url, timeout, user_agent=primary)
                    if ctx is not None:
                        try:
                            with urlopen(req, timeout=timeout, context=ctx) as resp:  # nosec B310
                                return resp.read(), _http_final_url(resp, url)
                        except (HTTPError, URLError, TimeoutError, OSError) as e2:
                            last = e2
                    raise last
                net_tries += 1
                if net_tries >= 2:
                    break
                time.sleep(1.5)
    assert last is not None
    raise last


def http_get(
    url: str,
    timeout: float = HTTP_TIMEOUT,
    *,
    user_agent: str | None = None,
) -> bytes:
    """識別 UA で取り、403 のときだけブラウザ UA に切り替える。検証は切らない。

    429 / 408 / 5xx は Retry-After があればそれに従い、無ければ指数バックオフ＋jitter。
    同一 UA で最大 HTTP_RETRY_ATTEMPTS 回。打ち切ったら例外を上げる（UA は変えない）。
    """
    body, _final = http_get_result(url, timeout, user_agent=user_agent)
    return body

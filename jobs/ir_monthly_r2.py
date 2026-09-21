"""IR サイトの月次 PDF/xlsx/xls を Cloudflare R2 mount へ保存する。

使い方:
  python jobs/ir_monthly_r2.py
  python jobs/ir_monthly_r2.py --dest /tmp/monthly --only 3382
  python jobs/ir_monthly_r2.py --dry-run

対象は config/ir_monthly.json。表計算と pdf が両方あるページは表計算だけ取る。
xlsx があれば xlsx、なければ xls。ingest / パーサーはまだ。
ネットワークと app/ はテストから分離する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import ssl
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tdnet_monthly_r2 import (  # noqa: E402
    display_code,
    download_skip_reason,
    load_stock_product_map,
    sanitize_filename_part,
    yyyymmdd_from_pubdate,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_DEST = "/home/stock/r2-share/monthly"
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "ir_monthly.json"
USER_AGENT = "stock-analyzer-ir-monthly/1.0"
# AWS ELB が識別 UA を 403 する発行体（大東建託）向け。通常は USER_AGENT を先に使う。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 30.0
_AIA_OID = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x01, 0x01))
_CA_ISSUERS_OID = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x02))
_AIA_MAX_CERT = 64 * 1024
_AIA_CTX: dict[str, ssl.SSLContext | None] = {}
_EXT_RE = re.compile(r"\.(pdf|xlsx|xls)(?:$|[?#])", re.I)
_ALLOWED_EXT = frozenset({"pdf", "xlsx", "xls", "html"})
_XLS_MAGIC = b"\xd0\xcf\x11\xe0"
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_SKIP_HREF_RE = re.compile(r"^(?:javascript:|mailto:|#)", re.I)
_HASH_STEM_RE = re.compile(r"^[0-9a-f]{32,}$", re.I)


@dataclass(frozen=True)
class Issuer:
    code: str
    name: str
    index_url: str
    link_re: str
    title: str


@dataclass(frozen=True)
class FileLink:
    url: str
    text: str
    ext: str
    stem: str


class _AParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = None
        for key, val in attrs:
            if key.lower() == "href":
                href = val
                break
        self._href = href
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        self.links.append((self._href, "".join(self._text).strip()))
        self._href = None


def jst_today(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    return now.date()


def normalize_ext(ext: str) -> str:
    s = (ext or "").lstrip(".").lower()
    if s not in _ALLOWED_EXT:
        raise ValueError(f"unsupported ext: {ext!r}")
    return s


def url_stem(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name
    stem = Path(name).stem
    return sanitize_filename_part(stem)


def parse_file_links(html: str, base_url: str) -> list[FileLink]:
    """<a href> の pdf/xlsx/xls だけ。コメント内のリンクは HTMLParser が無視する。"""
    parser = _AParser()
    parser.feed(html or "")
    parser.close()
    out: list[FileLink] = []
    seen: set[str] = set()
    for href, text in parser.links:
        raw = (href or "").strip()
        if not raw or _SKIP_HREF_RE.match(raw):
            continue
        abs_url = urljoin(base_url, raw)
        parsed = urlparse(abs_url)
        if parsed.scheme not in ("http", "https"):
            continue
        m = _EXT_RE.search(parsed.path)
        if not m:
            m = _EXT_RE.search(abs_url)
        if not m:
            continue
        key = abs_url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        ext = m.group(1).lower()
        stem = url_stem(key)
        if not stem:
            continue
        out.append(FileLink(url=key, text=re.sub(r"\s+", " ", text or ""), ext=ext, stem=stem))
    return out


def filter_links(links: list[FileLink], link_re: str) -> list[FileLink]:
    try:
        rx = re.compile(link_re)
    except re.error as e:
        raise ValueError(f"invalid link_re: {link_re!r} ({e})") from e
    matched = [ln for ln in links if rx.search(ln.url)]
    xlsx = [ln for ln in matched if ln.ext == "xlsx"]
    if xlsx:
        return xlsx
    xls = [ln for ln in matched if ln.ext == "xls"]
    if xls:
        return xls
    return [ln for ln in matched if ln.ext == "pdf"]


def dest_title(link: FileLink, issuer_title: str, text_counts: Mapping[str, int]) -> str:
    """URL stem を基本にする。CMS ハッシュ名だけ、一意なリンクテキストを使う。"""
    if _HASH_STEM_RE.fullmatch(link.stem):
        text = re.sub(r"\s+", " ", link.text or "").strip()
        if text and text_counts.get(text, 0) == 1:
            return text
    return link.stem or issuer_title


def dest_filename(
    pubdate: str,
    company_code: str,
    company_name: str,
    title: str,
    ext: str = "pdf",
) -> str:
    ymd = yyyymmdd_from_pubdate(pubdate)
    code = display_code(company_code)
    name = sanitize_filename_part(company_name)
    t = sanitize_filename_part(title)
    stem = f"{ymd}_{code}_{name}_{t}"
    if len(stem) > 180:
        stem = stem[:180].rstrip(" ._")
    return f"{stem}.{normalize_ext(ext)}"


def dest_path(
    dest: Path,
    pubdate: str,
    company_code: str,
    company_name: str,
    title: str,
    ext: str,
) -> Path:
    code = display_code(company_code)
    return dest / code / dest_filename(pubdate, company_code, company_name, title, ext)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def existing_hashes(code_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not code_dir.is_dir():
        return out
    for p in code_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in (".pdf", ".xlsx", ".xls"):
            continue
        try:
            if p.stat().st_size <= 0:
                continue
            out[sha256_bytes(p.read_bytes())] = p
        except OSError:
            continue
    return out


def looks_like_file(data: bytes, ext: str) -> bool:
    if not data:
        return False
    if ext == "pdf":
        return data.startswith(b"%PDF")
    if ext == "xlsx":
        return data.startswith(b"PK")
    if ext == "xls":
        return data.startswith(_XLS_MAGIC)
    if ext == "html":
        head = data.lstrip()[:200].lower()
        return head.startswith(b"<") or head.startswith(b"<!doctype")
    return False


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


def ssl_context_with_aia(url: str, timeout: float = HTTP_TIMEOUT) -> ssl.SSLContext | None:
    """サーバーが中間を付けない／付け違えるとき、AIA の CA Issuers を足して検証する。"""
    from urllib.request import Request, urlopen

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
            try:
                req = Request(uri, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
                with urlopen(req, timeout=timeout) as resp:
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


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    from urllib.request import Request, urlopen

    last: Exception | None = None
    aia_tried = False
    for ua in (USER_AGENT, BROWSER_UA):
        req = Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        for attempt in range(2):
            try:
                with urlopen(req, timeout=timeout) as resp:
                    return resp.read()
            except HTTPError as e:
                last = e
                if e.code == 403 and ua == USER_AGENT:
                    break
                if e.code < 500 and e.code not in (408, 429):
                    raise
            except (URLError, TimeoutError, OSError) as e:
                last = e
                if not aia_tried and _is_incomplete_chain(e):
                    aia_tried = True
                    ctx = ssl_context_with_aia(url, timeout)
                    if ctx is not None:
                        try:
                            with urlopen(req, timeout=timeout, context=ctx) as resp:
                                return resp.read()
                        except (HTTPError, URLError, TimeoutError, OSError) as e2:
                            last = e2
                    raise last
            if attempt == 0:
                time.sleep(1.5)
    assert last is not None
    raise last


def _parse_issuer(raw: object) -> Issuer:
    if not isinstance(raw, dict):
        raise ValueError("issuer must be an object")
    raw_code = str(raw.get("code") or "").strip().upper()
    if not _CODE_RE.fullmatch(raw_code):
        raise ValueError(f"invalid issuer code: {raw.get('code')!r}")
    code = display_code(raw_code)
    name = str(raw.get("name") or "").strip()
    index_url = str(raw.get("index_url") or "").strip()
    link_re = str(raw.get("link_re") or "").strip()
    title = str(raw.get("title") or "").strip() or "月次"
    parsed = urlparse(index_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid index_url for {code}: {index_url!r}")
    if not name:
        raise ValueError(f"issuer {code} missing name")
    if not link_re:
        raise ValueError(f"issuer {code} missing link_re")
    try:
        re.compile(link_re)
    except re.error as e:
        raise ValueError(f"issuer {code} invalid link_re: {e}") from e
    return Issuer(code=code, name=name, index_url=index_url, link_re=link_re, title=title)


def load_registry(path: Path) -> list[Issuer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("issuers")
    else:
        items = raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path} has no issuers")
    out: list[Issuer] = []
    seen: set[str] = set()
    for item in items:
        iss = _parse_issuer(item)
        if iss.code in seen:
            raise ValueError(f"duplicate issuer code: {iss.code}")
        seen.add(iss.code)
        out.append(iss)
    return out


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_issuer(
    iss: Issuer,
    dest: Path,
    stock_map: Mapping[str, tuple[str | None, str | None]],
    today: date,
    *,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """returns listed, saved, skipped, failed."""
    reason = download_skip_reason(iss.code, iss.name, stock_map)
    if reason:
        print(f"skip {reason}", flush=True)
        return 0, 0, 1, 0
    body = http_get(iss.index_url).decode("utf-8", errors="replace")
    time.sleep(SLEEP_SEC)
    links = filter_links(parse_file_links(body, iss.index_url), iss.link_re)
    listed = len(links)
    if dry_run:
        for ln in links:
            print(f"dry-run {iss.code} {ln.ext} {ln.stem} {ln.url}", flush=True)
        return listed, 0, listed, 0
    code_dir = dest / display_code(iss.code)
    hashes = existing_hashes(code_dir)
    saved = skipped = failed = 0
    pubdate = today.isoformat()
    text_counts: dict[str, int] = {}
    for ln in links:
        t = re.sub(r"\s+", " ", ln.text or "").strip()
        if t:
            text_counts[t] = text_counts.get(t, 0) + 1
    for ln in links:
        title = dest_title(ln, iss.title, text_counts)
        path = dest_path(dest, pubdate, iss.code, iss.name, title, ln.ext)
        rel = path.relative_to(dest)
        try:
            data = http_get(ln.url)
            time.sleep(SLEEP_SEC)
            if not looks_like_file(data, ln.ext):
                raise ValueError(f"not a {ln.ext} ({len(data)} bytes)")
            digest = sha256_bytes(data)
            prev = hashes.get(digest)
            if prev is not None:
                skipped += 1
                print(f"skip hash {rel} == {prev.relative_to(dest)}", flush=True)
                continue
            if path.is_file() and path.stat().st_size > 0:
                old = sha256_bytes(path.read_bytes())
                hashes.pop(old, None)
            atomic_write(path, data)
            hashes[digest] = path
            saved += 1
            print(f"saved {rel} ({len(data)} bytes)", flush=True)
        except Exception as e:
            failed += 1
            print(f"fail {rel} url={ln.url}: {e}", flush=True)
    return listed, saved, skipped, failed


def run(
    dest: Path,
    config_path: Path,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    today: date | None = None,
) -> int:
    issuers = load_registry(config_path)
    if only:
        want = {display_code(c.strip().upper()) for c in only if c.strip()}
        issuers = [i for i in issuers if i.code in want]
        missing = want - {i.code for i in issuers}
        if missing:
            print(f"warn unknown --only {sorted(missing)}", flush=True)
    if not issuers:
        print("ir_monthly_r2 no issuers", flush=True)
        return 2
    day = today or jst_today()
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"ir_monthly_r2 start dest={dest} issuers={len(issuers)} "
        f"day={day.isoformat()} dry_run={int(dry_run)}",
        flush=True,
    )
    stock_map = load_stock_product_map()
    if not stock_map:
        print("warn stocks map empty; unknown-stock skip disabled", flush=True)
    listed = saved = skipped = failed = 0
    for iss in issuers:
        print(f"issuer {iss.code} {iss.name}", flush=True)
        try:
            n, s, k, f = fetch_issuer(iss, dest, stock_map, day, dry_run=dry_run)
        except Exception as e:
            failed += 1
            print(f"fail issuer {iss.code} {iss.index_url}: {e}", flush=True)
            continue
        listed += n
        saved += s
        skipped += k
        failed += f
    print(
        f"ir_monthly_r2 done listed={listed} saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch IR monthly PDF/xlsx/xls into R2 mount")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="issuer registry JSON")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="limit to these codes (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", help="list matched URLs, do not download")
    args = ap.parse_args(argv)
    return run(
        Path(args.dest),
        Path(args.config),
        only=args.only or None,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

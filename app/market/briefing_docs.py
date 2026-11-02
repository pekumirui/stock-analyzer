"""決算説明資料が TDnet と会社ホームページのどちらから取れるか（KAB-325）。

TDnet は開示一覧の表題と、PDF の先頭が %PDF- か。一覧は約1か月で消える。
ホームページは homepages の URL から、説明資料の PDF リンクを少数ページだけ辿る。
ボット対策があるので、こちらは最初からブラウザの User-Agent で、間隔をあける。
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from jobs._r2common import BROWSER_UA

from .homepages import ordinary_code

# 短信の日からこの日数までを、同じ回の説明資料とみなす
MATERIAL_WINDOW_DAYS = 7
# TDnet が一覧を残すおおよその日数。これより新しい日の 404 は開示ゼロとして扱う
LIST_RETENTION_DAYS = 32
PROBE_INTERVAL_SEC = 2.5
PROBE_MAX_PAGES = 4
PROBE_HTML_BYTES = 1_500_000
PROBE_PDF_BYTES = 8192
# 会社サイトから1段だけ迎える IR 配信
IR_HOSTS = ("eir-parts.net", "xj-storage.jp", "irpocket.com", "pronexus.co.jp")

_WS = re.compile(r"\s+")
_MATERIAL_HINTS = ("決算", "四半期", "中間", "業績", "通期")
_SKIP_TITLE = ("月次", "有価証券報告書", "招集通知", "コーポレート・ガバナンス", "コーポレートガバナンス")
_BOT_MARKERS = (
    "cf-browser-verification",
    "just a moment",
    "access denied",
    "attention required",
    "captcha",
    "/cdn-cgi/",
)


@dataclass(frozen=True)
class TdnetJudgement:
    status: str  # found / listed / absent / unknown
    title: str | None = None
    url: str | None = None
    pubdate: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class ProbeResult:
    status: str  # found / not_found / blocked / error
    url: str | None = None
    detail: str = ""
    pages: int = 0


@dataclass
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes


class Pacer:
    """リクエストの間隔。最初の1回は待たない。"""

    def __init__(self, interval: float = PROBE_INTERVAL_SEC, sleep=time.sleep, clock=time.monotonic):
        self.interval = interval
        self.sleep = sleep
        self.clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._last is not None:
            gap = self.interval - (now - self._last)
            if gap > 0:
                self.sleep(gap)
        self._last = self.clock()


def _compact(text: str | None) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def is_material_title(title: str | None) -> bool:
    """決算説明資料・説明会資料・決算の補足資料。短信そのものは違う。"""
    text = _compact(title)
    if not text or "決算短信" in text:
        return False
    if any(bad in text for bad in _SKIP_TITLE):
        return False
    folded = text.casefold()
    if "説明会資料" in text or "説明会プレゼン" in text:
        return True
    if "説明資料" in text and any(hint in text for hint in _MATERIAL_HINTS):
        return True
    if "補足資料" in text and any(hint in text for hint in ("決算", "財務", "業績", "四半期")):
        return True
    if "presentation" in folded and any(hint in folded for hint in ("earning", "financial", "result")):
        return True
    return False


def pub_date(pubdate: str | None) -> date | None:
    if not pubdate:
        return None
    head = pubdate.strip()[:10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def judge_tdnet_rows(
    rows: list[dict],
    code: str,
    disclosed_on: date,
    *,
    missing_days: set[date] | None = None,
    today: date | None = None,
    window_days: int = MATERIAL_WINDOW_DAYS,
) -> TdnetJudgement:
    """同じ会社の、短信当日を含む window 日以内の説明資料。PDF の中身は見ない。

    保持期間外の 404 が窓に掛かっていて該当が無ければ unknown。
    新しい日の 404 は開示ゼロ（absent になり得る）。
    """
    try:
        want = ordinary_code(code)
    except ValueError:
        return TdnetJudgement("unknown", detail="bad code")
    end = disclosed_on + timedelta(days=window_days)
    hit: dict | None = None
    for row in rows:
        try:
            row_code = ordinary_code(str(row.get("company_code") or ""))
        except ValueError:
            continue
        if row_code != want:
            continue
        on = pub_date(row.get("pubdate"))
        if on is None or on < disclosed_on or on > end:
            continue
        if not is_material_title(row.get("title")):
            continue
        if hit is None or str(row.get("pubdate") or "") < str(hit.get("pubdate") or ""):
            hit = row
    if hit is not None:
        return TdnetJudgement(
            "listed",
            title=hit.get("title"),
            url=hit.get("document_url") or None,
            pubdate=hit.get("pubdate"),
        )
    anchor = today or date.today()
    if end > anchor:
        return TdnetJudgement("unknown", detail="window open")
    expired = {
        day for day in (missing_days or set())
        if disclosed_on <= day <= end and (anchor - day).days > LIST_RETENTION_DAYS
    }
    if expired:
        return TdnetJudgement("unknown", detail="list expired")
    return TdnetJudgement("absent")


def fetch_tdnet_range(start: date, end: date, get, sleep_sec: float = 0.4) -> tuple[list[dict], set[date]]:
    """start〜end の開示一覧。404 の日は missing に入れて飛ばす。"""
    from jobs import _tdnet as tdnet_list

    missing: set[date] = set()
    rows: list[dict] = []
    cur = start
    while cur <= end:
        try:
            rows.extend(tdnet_list.fetch_day(cur, get, sleep_sec))
        except HTTPError as exc:
            if exc.code != 404:
                raise
            missing.add(cur)
            print(f"warn list gone {cur.isoformat()}", flush=True)
        cur += timedelta(days=1)
    return rows, missing


def browser_headers() -> dict[str, str]:
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.7",
    }


def ascii_url(url: str) -> str:
    """urlopen は IRI を送れない。ホストは IDNA、パスとクエリの非 ASCII は百分率符号化する。"""
    parts = urlsplit(url.strip())
    host = parts.hostname or ""
    if host:
        host = host.encode("idna").decode("ascii")
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&%+;:@,")
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _final_url(resp: object, requested: str) -> str:
    geturl = getattr(resp, "geturl", None)
    if not callable(geturl):
        return requested
    try:
        final = geturl()
    except Exception:
        return requested
    if isinstance(final, str) and urlparse(final).scheme in ("http", "https"):
        return final
    return requested


def browser_open(req: Request, *, limit: int) -> HttpResponse:
    """ブラウザ UA の req を開き、本文は limit バイトまで。403 等も応答として返す。"""
    try:
        target = ascii_url(req.full_url)
    except UnicodeError:
        target = req.full_url
    req = Request(target, headers=browser_headers())
    try:
        with urlopen(req, timeout=20) as resp:  # nosec B310
            return HttpResponse(
                status=getattr(resp, "status", 200) or 200,
                url=_final_url(resp, req.full_url),
                headers={k: v for k, v in resp.headers.items()},
                body=resp.read(limit),
            )
    except HTTPError as exc:
        body = exc.read(limit) if exc.fp is not None else b""
        return HttpResponse(
            status=exc.code,
            url=req.full_url,
            headers={k: v for k, v in (exc.headers.items() if exc.headers else [])},
            body=body,
        )


def _registrable(host: str) -> str:
    host = host.lower().rstrip(".").removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    if len(parts) >= 3 and parts[-1] == "jp" and parts[-2] in {"co", "ne", "or", "ac", "go", "ed", "lg"}:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def same_site(home: str, url: str) -> bool:
    left = urlparse(home).hostname or ""
    right = urlparse(url).hostname or ""
    if not left or not right:
        return False
    return _registrable(left) == _registrable(right)


def _ir_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in IR_HOSTS)


def _allowed(home: str, url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return same_site(home, url) or _ir_host(url)


def _is_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf" in path


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip or tag != "a":
            return
        found = {key.lower(): value or "" for key, value in attrs}
        self._href = found.get("href") or None
        self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._buf)))
            self._href = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._skip or self._href is None:
            return
        self._buf.append(data)


def _decode_html(body: bytes, content_type: str | None) -> str:
    charset = None
    if content_type:
        match = re.search(r"charset=([\w.-]+)", content_type, re.I)
        if match:
            charset = match.group(1)
    if charset is None:
        head = body[:2500].decode("ascii", "ignore")
        match = re.search(r"charset=[\"']?([\w.-]+)", head, re.I)
        if match:
            charset = match.group(1)
    for enc in (charset, "utf-8", "cp932"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _is_tanshin_link(href: str, text: str) -> bool:
    """決算短信そのものの PDF。説明資料のリンクに短信という語が混ざる場合は残す。"""
    blob = unicodedata.normalize("NFKC", f"{text} {unquote(href)}")
    compact = _WS.sub("", blob)
    if "決算短信" not in compact:
        return False
    return not any(key in compact for key in ("説明資料", "説明会", "補足資料", "補足説明"))


_OTHER_FILING = ("有価証券報告書", "半期報告書", "四半期報告書", "招集通知", "招集ご通知")
# この点未満のリンクは IR の目次として辿るが、PDF でも説明資料とはしない
_MATERIAL_SCORE = 90


def _link_score(href: str, text: str) -> int:
    blob = unicodedata.normalize("NFKC", f"{text} {unquote(href)}")
    compact = _WS.sub("", blob)
    folded = compact.casefold()
    if _is_tanshin_link(href, text) or any(key in compact for key in _OTHER_FILING):
        return 0
    if any(key in compact for key in ("決算説明", "説明会資料", "補足説明", "説明資料")):
        return 100
    if _is_pdf_url(href) and any(key in folded for key in ("決算", "presentation", "briefing", "kessan")):
        return 90
    if any(key in folded for key in ("/ir", "investor", "ir-library", "ir_library", "/library")):
        return 50
    if "決算" in compact:
        return 40
    return 0


def _bot_page(text: str) -> bool:
    head = text[:4000].casefold()
    return any(marker in head for marker in _BOT_MARKERS)


def _content_type(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return ""


def _is_pdf_body(resp: HttpResponse) -> bool:
    ctype = _content_type(resp.headers).lower()
    if resp.body.startswith(b"%PDF-"):
        return True
    return "application/pdf" in ctype and resp.status == 200 and len(resp.body) > 0


def probe_homepage(
    url: str,
    opener,
    pacer: Pacer,
    *,
    max_pages: int = PROBE_MAX_PAGES,
) -> ProbeResult:
    """url から説明資料の PDF に届くか。opener(req, limit=) は HttpResponse を返す。"""
    if not _allowed(url, url):
        return ProbeResult("error", detail="bad homepage url")
    seen: set[str] = set()
    pending: list[tuple[int, str]] = []
    current: str | None = url
    current_score = 0
    pages = 0
    blocked_detail = ""
    while current and pages < max_pages:
        try:
            current = ascii_url(current)
        except UnicodeError as exc:
            if pages == 0:
                return ProbeResult("error", detail=str(exc)[:200], pages=1)
            current, current_score = _next_link(pending, seen)
            continue
        if current in seen:
            current, current_score = _next_link(pending, seen)
            continue
        seen.add(current)
        limit = PROBE_PDF_BYTES if _is_pdf_url(current) else PROBE_HTML_BYTES
        pacer.wait()
        req = Request(current, headers=browser_headers())
        try:
            resp = opener(req, limit=limit)
        except (URLError, TimeoutError, OSError, UnicodeError) as exc:
            if pages == 0:
                return ProbeResult("error", detail=str(exc)[:200], pages=1)
            pages += 1
            current, current_score = _next_link(pending, seen)
            continue
        pages += 1
        if resp.status in {401, 403, 429, 503}:
            blocked_detail = f"http {resp.status}"
            if pages == 1:
                return ProbeResult("blocked", detail=blocked_detail, pages=pages)
            current, current_score = _next_link(pending, seen)
            continue
        landed = resp.url or current
        ctype = _content_type(resp.headers)
        if _is_pdf_body(resp) and (_is_pdf_url(landed) or resp.body.startswith(b"%PDF-")):
            if current_score >= _MATERIAL_SCORE and not _is_tanshin_link(landed, ""):
                return ProbeResult("found", url=landed, detail="pdf", pages=pages)
            current, current_score = _next_link(pending, seen)
            continue
        html = _decode_html(resp.body, ctype)
        if _bot_page(html):
            if pages == 1:
                return ProbeResult("blocked", detail="bot page", pages=pages)
            current, current_score = _next_link(pending, seen)
            continue
        parser = _LinkParser()
        parser.feed(html)
        parser.close()
        base = resp.url or current
        for href, text in parser.links:
            try:
                absolute = ascii_url(urljoin(base, href.strip()))
            except UnicodeError:
                continue
            if absolute in seen or not _allowed(url, absolute):
                continue
            score = _link_score(absolute, text)
            if score <= 0:
                continue
            pending.append((score, absolute))
        pending.sort(key=lambda item: item[0], reverse=True)
        current, current_score = _next_link(pending, seen)
    if blocked_detail and pages == 1:
        return ProbeResult("blocked", detail=blocked_detail, pages=pages)
    return ProbeResult("not_found", detail="no pdf", pages=pages)


def _next_link(pending: list[tuple[int, str]], seen: set[str]) -> tuple[str | None, int]:
    while pending:
        score, link = pending.pop(0)
        if link not in seen:
            return link, score
    return None, 0

"""TDnet に出ない決算説明資料を会社の IR ページから data/briefing へ保存する（KAB-413）。

使い方:
  python jobs/market/homepage_briefing.py --dry-run
  python jobs/market/homepage_briefing.py --only 6728
  python jobs/market/homepage_briefing.py --only 6728 --limit 4 --dest /tmp/briefing

対象は config/market/briefing_pages.json（code / name / index_url、任意で link_re）。
index_url は 1 本か、過去分が別ページの会社は配列。
TDnet に資料を出す会社は載せない（同じ資料が別の番号で 2 行になる）。
index_url が転送や meta refresh で別のページへ飛ばすときは、その先を読む。
表題は「h2 以下の見出し + リンクの文字」。採否は app.market.briefing_title.is_material_title を
リンクの文字に当てる。決算の資料置き場なので決算の語は補い、資料の種類はリンクの文字に要る
（決算短信のリンクは採らない）。リンクの文字に種類の語が無い会社は link_re（リンクの文字に当てる
正規表現）で採る。リンクの文字からは容量や「PDF」の注記を落とす。リンクの文字が「PDF」だけのときは
直前の文字（隣の欄の資料名）を使う。リンクの中の aria-hidden と、svg / style / script の文字は読まない。
公開日は一覧に無いので PDF の更新日時（無ければ作成日時、どちらも無ければ URL の中の
YYYYMMDD、それも無ければ取得した時刻）。
番号は表題と PDF のファイル名から作る。索引にある PDF は取りに行かない。
--limit は 1 社で新しく保存する件数（ページの上から）。
一覧を eir-parts（プロネクサスの IR サービス）で出す会社は、index_url に会社のページでなく
フィード（https://ssl4.eir-parts.net/V4Public/EIR/<code>/ja/announcement/announcement_<N>.js）を書く。
フィードは公開日を持つのでそれを使い、番号は PDF の URL から作る。
索引は briefing_documents（source=homepage）。ネットワークはテストから差し替える。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from app.market.briefing_store import (  # noqa: E402
    JST,
    SOURCE_HOMEPAGE,
    has_saved,
    record_briefing,
)
from app.market.briefing_title import is_material_title, spaced_title  # noqa: E402
from jobs._r2common import (  # noqa: E402
    abort_if_disconnected,
    fail_if_mount_down,
    http_get as _http_get,
    http_get_result as _http_get_result,
)
from jobs.monthly.tdnet_monthly_r2 import dest_path, jq_lookup_code  # noqa: E402
from jobs.share_paths import share_path  # noqa: E402

DEFAULT_DEST = str(share_path("briefing"))
DEFAULT_CONFIG = SCRIPT_DIR.parents[1] / "config" / "market" / "briefing_pages.json"
USER_AGENT = "stock-analyzer-homepage-briefing/1.0"
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 60.0
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_PDF_RE = re.compile(r"\.pdf$", re.I)
_HEADING = re.compile(r"^h([1-6])$")
_TEXT_NOTE = re.compile(r"\s*[(\[][^()\[\]]*(?:\d\s*[KMG]B|PDF|新しいウィンドウ)[^()\[\]]*[)\]]", re.I)
_TEXT_TAIL = re.compile(r"\s*PDF$", re.I)
_META_REFRESH = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]*content=[\"']?\s*\d+\s*;\s*url=([^\"'>\s]+)",
    re.I,
)
MAX_REFRESH = 2
_VOID_TAGS = frozenset({"img", "br", "hr", "input", "meta", "link", "source", "wbr", "area", "col", "embed"})
_SKIP_TAGS = frozenset({"svg", "style", "script"})
_FORMAT_ONLY = re.compile(r"^PDF$", re.I)
MAX_LABEL = 60
_EIR_FEED = re.compile(r"^https://[a-z0-9]+\.eir-parts\.net/V4Public/EIR/[^/]+/[a-z]+/announcement/announcement_\d+\.js$")
_EIR_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
_EIR_FISCAL = re.compile(r"^(\d{4})(\d{2})$")
_URL_DAY = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)

Getter = Callable[[str], bytes]
PageGetter = Callable[[str], tuple[bytes, str]]
PdfDate = Callable[[bytes], datetime | None]


@dataclass(frozen=True)
class Issuer:
    code: str
    name: str
    index_urls: tuple[str, ...]
    link_re: str = ""


@dataclass(frozen=True)
class DeckLink:
    url: str
    title: str
    document_id: str
    pubdate: str = ""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str, str]] = []
        self._path: dict[int, str] = {}
        self._level: int | None = None
        self._heading_text: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._before: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        hidden = self._href is not None and dict(attrs).get("aria-hidden") == "true"
        if tag in _SKIP_TAGS or (hidden and tag not in _VOID_TAGS):
            self._skip_tag = tag
            self._skip_depth = 1
            return
        heading = _HEADING.match(tag)
        if heading:
            self._level = int(heading.group(1))
            self._heading_text = []
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._skip_tag is not None:
            return
        if self._level is not None:
            self._heading_text.append(data)
        if self._href is not None:
            self._text.append(data)
        elif self._level is None:
            self._before.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag is not None:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if self._level is not None and tag == f"h{self._level}":
            level, self._level = self._level, None
            self._path = {n: text for n, text in self._path.items() if n < level}
            self._path[level] = spaced_title("".join(self._heading_text))
            self._before = []
        elif tag == "a" and self._href is not None:
            heading = " ".join(text for n, text in sorted(self._path.items()) if n >= 2 and text)
            before = spaced_title("".join(self._before))
            self.links.append((self._href, spaced_title("".join(self._text)), heading, before))
            self._href = None
            self._before = []


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    return _http_get(url, timeout=timeout, user_agent=USER_AGENT)


def http_get_page(url: str, timeout: float = HTTP_TIMEOUT) -> tuple[bytes, str]:
    return _http_get_result(url, timeout, user_agent=USER_AGENT)


def document_id(code: str, title: str, url: str) -> str:
    """パスの途中の番号は資料が足されるとずれるので、表題とファイル名だけで決める。"""
    name = Path(unquote(urlparse(url).path)).name
    digest = hashlib.sha1(f"{code}\n{title}\n{name}".encode(), usedforsecurity=False).hexdigest()
    return f"hp{digest[:24]}"


def clean_link_text(text: str) -> str:
    """容量・形式の注記を落とす。容量が変わっても同じ番号になる。"""
    return _TEXT_TAIL.sub("", _TEXT_NOTE.sub("", text)).strip()


def parse_deck_links(html: str, base_url: str, code: str, link_re: str = "") -> list[DeckLink]:
    """ページの上から。文字の無いアイコンのリンクは飛ばし、同じ番号は最初の 1 本。"""
    extra = re.compile(link_re) if link_re else None
    parser = _LinkParser()
    parser.feed(html or "")
    parser.close()
    out: list[DeckLink] = []
    seen: set[str] = set()
    for href, label, heading, before in parser.links:
        raw = (href or "").strip()
        text = clean_link_text(label)
        if not text and _FORMAT_ONLY.match(label) and len(before) <= MAX_LABEL:
            text = clean_link_text(before)
        if not raw or not text:
            continue
        url = urljoin(base_url, raw).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _PDF_RE.search(parsed.path):
            continue
        if not is_material_title(f"決算 {text}") and not (extra and extra.search(text)):
            continue
        title = f"{heading} {text}".strip()
        doc_id = document_id(code, title, url)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(DeckLink(url=url, title=title, document_id=doc_id))
    return out


def is_eir_feed(url: str) -> bool:
    return bool(_EIR_FEED.match(url))


def parse_eir_feed(text: str, code: str, link_re: str = "") -> list[DeckLink]:
    """eir-parts のフィード（JSONP）から PDF の IR 資料を、並びのまま。

    会社のページと違って決算以外の IR 資料も並ぶので、決算の語は補わない。表題そのものが
    is_material_title を通るか、link_re に合うものだけ。短信・適時開示は採らない。
    """
    extra = re.compile(link_re) if link_re else None
    body = text.lstrip("\ufeff")
    data = json.loads(body[body.index("(") + 1:body.rindex(")")])
    out: list[DeckLink] = []
    seen: set[str] = set()
    for item in data.get("item") or []:
        kind = str(item.get("news_type") or "")
        url = str(item.get("link") or "").strip()
        parsed = urlparse(url)
        if item.get("type") != "pdf" or not kind.startswith("ir_material"):
            continue
        if parsed.scheme not in ("http", "https") or not _PDF_RE.search(parsed.path):
            continue
        text_ = spaced_title(str(item.get("title") or ""))
        fiscal = kind.startswith("ir_material_for_fiscal_ym")
        if not is_material_title(text_) and not (extra and extra.search(text_)):
            continue
        year = _EIR_FISCAL.match(str(item.get("fiscal_ym") or ""))
        title = text_
        if fiscal and year and f"{year.group(1)}年" not in text_:
            title = f"{year.group(1)}年{int(year.group(2))}月期 {text_}"
        digest = hashlib.sha1(f"{code}\n{parsed.path}".encode(), usedforsecurity=False).hexdigest()
        doc_id = f"hp{digest[:24]}"
        if doc_id in seen:
            continue
        seen.add(doc_id)
        when = _EIR_DATE.match(str(item.get("date") or ""))
        pubdate = "{}-{}-{} {}:{}:{}".format(*when.groups()) if when else ""
        out.append(DeckLink(url=url, title=title, document_id=doc_id, pubdate=pubdate))
    return out


def parse_pdf_date(value: str | None) -> datetime | None:
    """PDF の日時（D:YYYYMMDDHHmmSS+09'00'）を JST の naive に。時差が無ければそのまま。"""
    match = _PDF_DATE.match((value or "").strip())
    if not match:
        return None
    year, month, day, hour, minute, second, zulu, sign, off_h, off_m = match.groups()
    try:
        when = datetime(  # noqa: DTZ001
            int(year), int(month or 1), int(day or 1),
            int(hour or 0), int(minute or 0), int(second or 0),
        )
    except ValueError:
        return None
    if zulu or sign:
        offset = timedelta(hours=int(off_h or 0), minutes=int(off_m or 0))
        tz = timezone.utc if zulu else timezone(offset if sign == "+" else -offset)
        when = when.replace(tzinfo=tz).astimezone(JST).replace(tzinfo=None)
    return when


def pdf_date(data: bytes) -> datetime | None:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        return None
    try:
        meta = pdf.get_metadata_dict(skip_empty=True)
    finally:
        pdf.close()
    return parse_pdf_date(meta.get("ModDate")) or parse_pdf_date(meta.get("CreationDate"))


def url_date(url: str) -> datetime | None:
    for year, month, day in _URL_DAY.findall(unquote(urlparse(url).path)):
        try:
            return datetime(int(year), int(month), int(day))  # noqa: DTZ001
        except ValueError:
            continue
    return None


def load_registry(path: Path) -> list[Issuer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("issuers") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError(f"{path}: issuers must be a list")
    out: list[Issuer] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: issuer must be an object")
        code = str(item.get("code") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        raw_urls = item.get("index_url")
        index_urls = tuple(str(u).strip() for u in (raw_urls if isinstance(raw_urls, list) else [raw_urls or ""]))
        link_re = str(item.get("link_re") or "")
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"{path}: bad code {code!r}")
        if not name or not index_urls or any(urlparse(u).scheme not in ("http", "https") for u in index_urls):
            raise ValueError(f"{path}: {code} needs name and http(s) index_url")
        if code in seen:
            raise ValueError(f"{path}: duplicate code {code}")
        try:
            re.compile(link_re)
        except re.error as exc:
            raise ValueError(f"{path}: {code} bad link_re: {exc}") from None
        seen.add(code)
        out.append(Issuer(code=code, name=name, index_urls=index_urls, link_re=link_re))
    return out


def fetch_index(url: str, get_page: PageGetter) -> tuple[str, str]:
    """一覧ページの HTML と、転送と meta refresh をたどった先の URL。"""
    for _ in range(MAX_REFRESH):
        body, url = get_page(url)
        html = body.decode("utf-8", "replace")
        match = _META_REFRESH.search(html)
        if not match:
            return html, url
        url = urljoin(url, match.group(1))
    body, url = get_page(url)
    return body.decode("utf-8", "replace"), url


def save_pdf(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch_issuer(
    issuer: Issuer,
    dest: Path,
    *,
    get: Getter,
    get_page: PageGetter,
    read_date: PdfDate,
    now: datetime,
    limit: int | None,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """戻りは (listed, saved, skipped, failed)。一覧が取れなければ例外。"""
    code5 = jq_lookup_code(issuer.code)
    links: list[DeckLink] = []
    listed: set[str] = set()
    for page_url in issuer.index_urls:
        if is_eir_feed(page_url):
            found = parse_eir_feed(get(page_url).decode("utf-8", "replace"), code5, issuer.link_re)
        else:
            html, index_url = fetch_index(page_url, get_page)
            found = parse_deck_links(html, index_url, code5, issuer.link_re)
        for link in found:
            if link.document_id not in listed:
                listed.add(link.document_id)
                links.append(link)
    saved = skipped = failed = 0
    for link in links:
        if has_saved(SOURCE_HOMEPAGE, link.document_id):
            skipped += 1
            continue
        if limit is not None and saved >= limit:
            break
        if dry_run:
            saved += 1
            print(f"would save {issuer.code} {link.title} {link.url}", flush=True)
            continue
        try:
            data = get(link.url)
            if not data.startswith(b"%PDF"):
                raise ValueError(f"not a PDF ({len(data)} bytes)")
            pubdate = link.pubdate
            if not pubdate:
                when = read_date(data) or url_date(link.url)
                if when is None or when > now:
                    when = now
                pubdate = when.strftime("%Y-%m-%d %H:%M:%S")
            path = dest_path(dest, pubdate, issuer.code, issuer.name, link.title)
            if path.exists():
                path = path.with_name(f"{path.stem}_{link.document_id[-6:]}.pdf")
            save_pdf(path, data)
            record_briefing(
                source=SOURCE_HOMEPAGE,
                document_id=link.document_id,
                code=code5,
                company_name=issuer.name,
                pubdate=pubdate,
                title=link.title,
                rel_path=path.relative_to(dest).as_posix(),
                url=link.url,
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
            saved += 1
            print(f"saved {path.relative_to(dest)} ({len(data)} bytes)", flush=True)
        except Exception as exc:
            if abort_if_disconnected("homepage_briefing", dest, exc) is not None:
                raise
            failed += 1
            print(f"fail {issuer.code} {link.title} {link.url}: {exc}", flush=True)
        time.sleep(SLEEP_SEC)
    return len(links), saved, skipped, failed


def run(
    dest: Path,
    config: Path = DEFAULT_CONFIG,
    *,
    only: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    get: Getter | None = None,
    get_page: PageGetter | None = None,
    read_date: PdfDate | None = None,
    now: datetime | None = None,
) -> int:
    issuers = load_registry(config)
    if only:
        wanted = {code.strip().upper() for code in only}
        unknown = wanted - {issuer.code for issuer in issuers}
        if unknown:
            print(f"not in {config.name}: {' '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        issuers = [issuer for issuer in issuers if issuer.code in wanted]
    if not dry_run:
        rc = fail_if_mount_down("homepage_briefing", dest, require_existing=False)
        if rc is not None:
            return rc
        dest.mkdir(parents=True, exist_ok=True)
    from app.db import init_db

    init_db()
    getter = http_get if get is None else get
    if get_page is not None:
        pager = get_page
    elif get is not None:
        def pager(url: str) -> tuple[bytes, str]:
            return get(url), url
    else:
        pager = http_get_page
    reader = pdf_date if read_date is None else read_date
    when = now or datetime.now(JST).replace(tzinfo=None)
    print(
        f"homepage_briefing start issuers={len(issuers)} dry_run={int(dry_run)} dest={dest}",
        flush=True,
    )
    listed = saved = skipped = failed = 0
    for issuer in issuers:
        try:
            counts = fetch_issuer(
                issuer, dest, get=getter, get_page=pager, read_date=reader, now=when, limit=limit, dry_run=dry_run,
            )
        except Exception as exc:
            down = abort_if_disconnected("homepage_briefing", dest, exc)
            if down is not None:
                return down
            failed += 1
            print(f"fail {issuer.code} {' '.join(issuer.index_urls)}: {exc}", flush=True)
            continue
        print(
            f"homepage_briefing {issuer.code} listed={counts[0]} saved={counts[1]} "
            f"skipped={counts[2]} failed={counts[3]}",
            flush=True,
        )
        listed += counts[0]
        saved += counts[1]
        skipped += counts[2]
        failed += counts[3]
    print(
        f"homepage_briefing done listed={listed} saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch briefing PDFs from company IR pages into data/briefing")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="issuer list JSON")
    ap.add_argument("--only", action="append", help="stock code in the config (repeatable)")
    ap.add_argument("--limit", type=int, help="new PDFs per issuer in this run, from the top of the page")
    ap.add_argument("--dry-run", action="store_true", help="list new PDFs, do not download")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print("--limit must be a positive integer", file=sys.stderr)
        return 2
    return run(
        Path(args.dest), Path(args.config), only=args.only, limit=args.limit, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

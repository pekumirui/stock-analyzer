"""TDnet に出ない決算説明資料を会社の IR ページから data/briefing へ保存する（KAB-413）。

使い方:
  python jobs/market/homepage_briefing.py --dry-run
  python jobs/market/homepage_briefing.py --only 6728
  python jobs/market/homepage_briefing.py --only 6728 --limit 4 --dest /tmp/briefing

対象は config/market/briefing_pages.json（code / name / index_url）。TDnet に資料を出す会社は
載せない（同じ資料が別の番号で 2 行になる）。
表題は「h2 以下の見出し + リンクの文字」。採否は app.market.briefing_title.is_material_title を
リンクの文字に当てる。決算の資料置き場なので決算の語は補い、資料の種類はリンクの文字に要る
（決算短信のリンクは採らない）。
公開日は一覧に無いので PDF の作成日時（無ければ更新日時、どちらも無ければ取得した時刻）。
番号は表題と PDF のファイル名から作る。索引にある PDF は取りに行かない。
--limit は 1 社で新しく保存する件数（ページの上から）。
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
_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)

Getter = Callable[[str], bytes]
PdfDate = Callable[[bytes], datetime | None]


@dataclass(frozen=True)
class Issuer:
    code: str
    name: str
    index_url: str


@dataclass(frozen=True)
class DeckLink:
    url: str
    title: str
    document_id: str


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []
        self._path: dict[int, str] = {}
        self._level: int | None = None
        self._heading_text: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        heading = _HEADING.match(tag)
        if heading:
            self._level = int(heading.group(1))
            self._heading_text = []
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._level is not None:
            self._heading_text.append(data)
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._level is not None and tag == f"h{self._level}":
            level, self._level = self._level, None
            self._path = {n: text for n, text in self._path.items() if n < level}
            self._path[level] = spaced_title("".join(self._heading_text))
        elif tag == "a" and self._href is not None:
            heading = " ".join(text for n, text in sorted(self._path.items()) if n >= 2 and text)
            self.links.append((self._href, spaced_title("".join(self._text)), heading))
            self._href = None


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    return _http_get(url, timeout=timeout, user_agent=USER_AGENT)


def document_id(code: str, title: str, url: str) -> str:
    """パスの途中の番号は資料が足されるとずれるので、表題とファイル名だけで決める。"""
    name = Path(unquote(urlparse(url).path)).name
    digest = hashlib.sha1(f"{code}\n{title}\n{name}".encode(), usedforsecurity=False).hexdigest()
    return f"hp{digest[:24]}"


def parse_deck_links(html: str, base_url: str, code: str) -> list[DeckLink]:
    """ページの上から。文字の無いアイコンのリンクは飛ばし、同じ番号は最初の 1 本。"""
    parser = _LinkParser()
    parser.feed(html or "")
    parser.close()
    out: list[DeckLink] = []
    seen: set[str] = set()
    for href, text, heading in parser.links:
        raw = (href or "").strip()
        if not raw or not text:
            continue
        url = urljoin(base_url, raw).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not _PDF_RE.search(parsed.path):
            continue
        if not is_material_title(f"決算 {text}"):
            continue
        title = f"{heading} {text}".strip()
        doc_id = document_id(code, title, url)
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(DeckLink(url=url, title=title, document_id=doc_id))
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
    return parse_pdf_date(meta.get("CreationDate")) or parse_pdf_date(meta.get("ModDate"))


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
        index_url = str(item.get("index_url") or "").strip()
        if not _CODE_RE.fullmatch(code):
            raise ValueError(f"{path}: bad code {code!r}")
        if not name or urlparse(index_url).scheme not in ("http", "https"):
            raise ValueError(f"{path}: {code} needs name and http(s) index_url")
        if code in seen:
            raise ValueError(f"{path}: duplicate code {code}")
        seen.add(code)
        out.append(Issuer(code=code, name=name, index_url=index_url))
    return out


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
    read_date: PdfDate,
    now: datetime,
    limit: int | None,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """戻りは (listed, saved, skipped, failed)。一覧が取れなければ例外。"""
    code5 = jq_lookup_code(issuer.code)
    html = get(issuer.index_url).decode("utf-8", "replace")
    links = parse_deck_links(html, issuer.index_url, code5)
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
            when = read_date(data)
            if when is None or when > now:
                when = now
            pubdate = when.strftime("%Y-%m-%d %H:%M:%S")
            path = dest_path(dest, pubdate, issuer.code, issuer.name, link.title)
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
                issuer, dest, get=getter, read_date=reader, now=when, limit=limit, dry_run=dry_run,
            )
        except Exception as exc:
            down = abort_if_disconnected("homepage_briefing", dest, exc)
            if down is not None:
                return down
            failed += 1
            print(f"fail {issuer.code} {issuer.index_url}: {exc}", flush=True)
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

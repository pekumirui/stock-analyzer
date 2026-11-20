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
表題に英文版と書いてある資料（is_english_version）は取らない。
--years N は今日から N 年前の日より前の資料を保存しない。eir-parts はフィードの日付で決める。
会社のページは表題か URL にある年（和暦を含む）が N 年前の年より古ければ取りに行かず、
年が読めないものは取ってから公開日で決める。--ledger PATH を付けると、取ってから古いと
分かった資料の番号と公開日と、--pending で会社を見た時刻を JSON に残す（dest には置かない）。
--pending N は、直近 N 日に「補足説明資料 有」（読めないときも含む）の短信を出し、そのあとの資料を
まだ会社ページから取れていない会社だけを見る。短信の直後に 1 回、見つからなければ RECHECK ごとに 1 回。
毎時の短信の取り込みのあとに cron が動かす。--pending を付けない回（週 1 回）は全社を見て、
直近 MISSING_DAYS 日に資料ありの短信を出したのに索引に資料が無く、設定にも無い会社を missing に出す。
同じ回で、設定にあるのに資料ありの短信から STALE_DAYS 日たってもその決算の資料がどの出所からも
入っていない会社を stale に出す（フィードの番号や載せる場所が変わった疑い）。初めて出た会社の数が
stale_new で、1 以上ならアラートになる（deploy/alert_patterns.py）。出した短信は ledger に残し、毎週は鳴らさない。
取った PDF と同じ中身（sha256）の資料が同じ会社の索引（出所を問わない）にあれば保存せず dup に数え、
--ledger の dup に残して次からは取りに行かない（同じ PDF を表題を変えて複数の分類に載せる会社がある）。
保存の直前に --dest の空きを見て、MIN_FREE_BYTES 未満なら保存せずに止める（終了コード 0）。
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
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from app.market.briefing_store import (
    JST,
    SOURCE_HOMEPAGE,
    has_saved,
    record_briefing,
)
from app.market.briefing_title import is_english_version, is_material_title, spaced_title
from jobs._r2common import (
    abort_if_disconnected,
    fail_if_mount_down,
    http_get as _http_get,
    http_get_result as _http_get_result,
)
from jobs.monthly.tdnet_monthly_r2 import dest_path, jq_lookup_code
from app.share_paths import share_path

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
MIN_FREE_BYTES = 25 * 1024 * 1024 * 1024
RECHECK = timedelta(hours=20)
FOUND_SLACK = timedelta(days=2)
MISSING_DAYS = 7
STALE_DAYS = 30
STALE_LOOKBACK_DAYS = 120
_TITLE_YEAR = re.compile(r"(?<!\d)(19[89]\d|20\d{2})(?!\d)")
_WAREKI_YEAR = re.compile(r"(平成|令和)\s*(元|\d{1,2})\s*年")
_WAREKI_BASE = {"平成": 1988, "令和": 2018}
_VOID_TAGS = frozenset({"img", "br", "hr", "input", "meta", "link", "source", "wbr", "area", "col", "embed"})
_SKIP_TAGS = frozenset({"svg", "style", "script"})
_FORMAT_ONLY = re.compile(r"^PDF$", re.I)
MAX_LABEL = 60
_EIR_FEED = re.compile(r"^https://[a-z0-9]+\.eir-parts\.net/V4Public/EIR/[^/]+/[a-z]+/announcement/announcement_\d+\.js$")
_EIR_DATE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})$")
_JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠々]")
_EIR_FISCAL = re.compile(r"^(\d{4})(\d{2})$")
_URL_DAY = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")
_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:(Z)|([+-])(\d{2})'?(\d{2})?'?)?"
)

Getter = Callable[[str], bytes]
DiskUsage = Callable[[Path], object]
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
        if is_english_version(text):
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
    同じ決算期・四半期に日本語の表題の資料があれば、表題に日本語の無い資料は英文版として採らない。
    同じ表題・同じ日の資料が複数の分類に載っていれば最初の 1 本。
    """
    extra = re.compile(link_re) if link_re else None
    body = text.lstrip("\ufeff")
    data = json.loads(body[body.index("(") + 1:body.rindex(")")])
    picked: list[tuple[dict, str]] = []
    japanese: set[tuple[str, str]] = set()
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
        if is_english_version(text_):
            continue
        picked.append((item, text_))
        if _JAPANESE.search(text_):
            japanese.add((str(item.get("fiscal_ym") or ""), str(item.get("quarter") or "")))
    out: list[DeckLink] = []
    seen: set[object] = set()
    for item, text_ in picked:
        group = (str(item.get("fiscal_ym") or ""), str(item.get("quarter") or ""))
        if group[0] and group in japanese and not _JAPANESE.search(text_):
            continue
        kind = str(item.get("news_type") or "")
        fiscal = kind.startswith("ir_material_for_fiscal_ym")
        url = str(item.get("link") or "").strip()
        parsed = urlparse(url)
        year = _EIR_FISCAL.match(str(item.get("fiscal_ym") or ""))
        title = text_
        if fiscal and year and f"{year.group(1)}年" not in text_:
            title = f"{year.group(1)}年{int(year.group(2))}月期 {text_}"
        digest = hashlib.sha1(f"{code}\n{parsed.path}".encode(), usedforsecurity=False).hexdigest()
        doc_id = f"hp{digest[:24]}"
        when = _EIR_DATE.match(str(item.get("date") or ""))
        pubdate = "{}-{}-{} {}:{}:{}".format(*when.groups()) if when else ""
        if doc_id in seen or (title, pubdate) in seen:
            continue
        seen.update({doc_id, (title, pubdate)})
        out.append(DeckLink(url=url, title=title, document_id=doc_id, pubdate=pubdate))
    return out


def year_hint(title: str, url: str) -> int | None:
    """表題と URL から読める一番新しい年。資料の年はこれより後にはならない。"""
    years = [int(y) for y in _TITLE_YEAR.findall(title)]
    years += [_WAREKI_BASE[era] + (1 if n == "元" else int(n)) for era, n in _WAREKI_YEAR.findall(title)]
    day = url_date(url)
    if day is not None:
        years.append(day.year)
    return max(years) if years else None


def years_ago(now: datetime, years: int) -> datetime:
    day = now.date()
    try:
        start = day.replace(year=day.year - years)
    except ValueError:
        start = day.replace(year=day.year - years, day=28)
    return datetime(start.year, start.month, start.day)


def disk_is_low(dest: Path, disk_usage: DiskUsage) -> bool:
    """--dest の空きがしきい値未満、または空きを読めない。"""
    try:
        target = dest if dest.exists() else dest.parent
        free = getattr(disk_usage(target), "free", None)
    except OSError:
        return True
    if isinstance(free, bool) or not isinstance(free, int):
        return True
    return free < MIN_FREE_BYTES


class DiskLow(Exception):
    """空きが足りないので、この実行ではもう保存しない。"""


@dataclass
class Counts:
    listed: int = 0
    saved: int = 0
    skipped: int = 0
    old: int = 0
    failed: int = 0
    dup: int = 0

    def add(self, other: Counts) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def text(self) -> str:
        return " ".join(f"{f.name}={getattr(self, f.name)}" for f in fields(self))


def parse_pdf_date(value: str | None) -> datetime | None:
    """PDF の日時（D:YYYYMMDDHHmmSS+09'00'）を JST の naive に。時差が無ければそのまま。"""
    match = _PDF_DATE.match((value or "").strip())
    if not match:
        return None
    year, month, day, hour, minute, second, zulu, sign, off_h, off_m = match.groups()
    try:
        when = datetime(
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
            return datetime(int(year), int(month), int(day))
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


@dataclass
class Ledger:
    old: dict[str, str] = field(default_factory=dict)       # 取ってから古いと分かった資料の番号 → 公開日
    checked: dict[str, str] = field(default_factory=dict)   # --pending で見た会社（5 桁）→ 時刻
    dup: dict[str, str] = field(default_factory=dict)       # 中身が索引の資料と同じだった番号 → その資料の番号
    stale: dict[str, str] = field(default_factory=dict)     # stale に出した会社（5 桁）→ その短信の時刻

    def as_json(self) -> dict[str, dict[str, str]]:
        return {
            "old": dict(sorted(self.old.items())),
            "checked": dict(sorted(self.checked.items())),
            "dup": dict(sorted(self.dup.items())),
            "stale": dict(sorted(self.stale.items())),
        }


def _str_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str)}


def load_ledger(path: Path) -> Ledger:
    """無い・壊れていれば空。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Ledger()
    if not isinstance(raw, dict):
        return Ledger()
    return Ledger(
        old=_str_map(raw.get("old")), checked=_str_map(raw.get("checked")),
        dup=_str_map(raw.get("dup")), stale=_str_map(raw.get("stale")),
    )


def same_content(code: str, sha256: str) -> str | None:
    """同じ会社の索引にある、中身が同じ資料の番号。出所は問わない。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import BriefingDocument

    db = SessionLocal()
    try:
        row = db.execute(
            select(BriefingDocument.document_id).where(
                BriefingDocument.code == code,
                BriefingDocument.sha256 == sha256,
                BriefingDocument.status == "saved",
            ).limit(1)
        ).first()
    finally:
        db.close()
    return row[0] if row else None


def save_ledger(path: Path, data: Ledger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data.as_json(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _ts(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H:%M:%S")


def _latest_covers(codes: set[str] | None, start: datetime) -> dict[str, tuple[datetime, bool | None, str]]:
    """直近の短信の表紙（code 5 桁 → 開示時刻・資料の有無・表題）。codes が None なら全社。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import TanshinCover

    q = select(TanshinCover.code, TanshinCover.disclosed_at, TanshinCover.has_material, TanshinCover.title).where(
        TanshinCover.disclosed_at >= start
    )
    if codes is not None:
        q = q.where(TanshinCover.code.in_(sorted(codes)))
    latest: dict[str, tuple[datetime, bool | None, str]] = {}
    db = SessionLocal()
    try:
        for code, at, has, title in db.execute(q):
            if at is not None and (code not in latest or at > latest[code][0]):
                latest[code] = (at, has, title or "")
    finally:
        db.close()
    return latest


def _has_deck_since(code: str, at: datetime, source: str | None) -> bool:
    """短信のころ以降の資料が索引にあるか。会社ページの公開日は PDF の日付なので少し前にずれうる。"""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import BriefingDocument

    q = select(BriefingDocument.document_id).where(
        BriefingDocument.code == code,
        BriefingDocument.status == "saved",
        BriefingDocument.pubdate >= _ts(at - FOUND_SLACK),
    )
    if source is not None:
        q = q.where(BriefingDocument.source == source)
    db = SessionLocal()
    try:
        return db.execute(q.limit(1)).first() is not None
    finally:
        db.close()


def pending_issuers(issuers: list[Issuer], now: datetime, days: int, checked: dict[str, str]) -> list[Issuer]:
    """短信のあとの資料をまだ会社ページから取れていない会社。見たばかりの会社は外す。"""
    by_code = {jq_lookup_code(issuer.code): issuer for issuer in issuers}
    covers = _latest_covers(set(by_code), now - timedelta(days=days))
    out: list[Issuer] = []
    for code, (at, has, _title) in sorted(covers.items()):
        if has is False or _has_deck_since(code, at, SOURCE_HOMEPAGE):
            continue
        last = checked.get(code, "")
        if last >= _ts(at) and last > _ts(now - RECHECK):
            continue
        out.append(by_code[code])
    return out


def missing_companies(issuers: list[Issuer], now: datetime) -> list[tuple[str, datetime, str]]:
    """資料ありの短信を出したのに、どの出所の資料も索引に無く、設定にも無い会社。"""
    configured = {jq_lookup_code(issuer.code) for issuer in issuers}
    covers = _latest_covers(None, now - timedelta(days=MISSING_DAYS))
    return [
        (code, at, title)
        for code, (at, has, title) in sorted(covers.items())
        if has is True and code not in configured and not _has_deck_since(code, at, None)
    ]


def stale_companies(issuers: list[Issuer], now: datetime) -> list[tuple[str, datetime, str]]:
    """設定にあるのに、資料ありの短信から STALE_DAYS 日たってもその決算の資料がどの出所からも無い会社。"""
    configured = {jq_lookup_code(issuer.code) for issuer in issuers}
    covers = _latest_covers(configured, now - timedelta(days=STALE_LOOKBACK_DAYS))
    limit = now - timedelta(days=STALE_DAYS)
    return [
        (code, at, title)
        for code, (at, has, title) in sorted(covers.items())
        if has is True and at <= limit and not _has_deck_since(code, at, None)
    ]


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
    since: datetime | None = None,
    disk_usage: DiskUsage = shutil.disk_usage,
    counts: Counts | None = None,
    known_old: dict[str, str] | None = None,
    known_dup: dict[str, str] | None = None,
) -> Counts:
    """一覧が取れなければ例外。空きが足りなければ DiskLow（counts にそこまでの件数）。"""
    counts = Counts() if counts is None else counts
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
    counts.listed = len(links)
    cutoff = since.strftime("%Y-%m-%d %H:%M:%S") if since is not None else ""
    for link in links:
        if has_saved(SOURCE_HOMEPAGE, link.document_id):
            counts.skipped += 1
            continue
        if known_dup is not None and link.document_id in known_dup:
            counts.dup += 1
            continue
        if since is not None:
            hint = year_hint(link.title, link.url)
            if (link.pubdate and link.pubdate < cutoff) or (not link.pubdate and hint is not None and hint < since.year):
                counts.old += 1
                continue
            if known_old is not None and known_old.get(link.document_id, cutoff) < cutoff:
                counts.old += 1
                continue
        if limit is not None and counts.saved >= limit:
            break
        if dry_run:
            counts.saved += 1
            print(f"would save {issuer.code} {link.title} {link.url}", flush=True)
            continue
        if disk_is_low(dest, disk_usage):
            raise DiskLow(dest)
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
            if cutoff and pubdate < cutoff:
                counts.old += 1
                if known_old is not None:
                    known_old[link.document_id] = pubdate
                print(f"old {issuer.code} {pubdate} {link.title}", flush=True)
                continue
            digest = hashlib.sha256(data).hexdigest()
            existing = same_content(code5, digest)
            if existing is not None:
                counts.dup += 1
                if known_dup is not None:
                    known_dup[link.document_id] = existing
                print(f"dup {issuer.code} {link.title} same={existing}", flush=True)
                continue
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
                sha256=digest,
                size=len(data),
            )
            counts.saved += 1
            print(f"saved {path.relative_to(dest)} ({len(data)} bytes)", flush=True)
        except Exception as exc:
            if abort_if_disconnected("homepage_briefing", dest, exc) is not None:
                raise
            counts.failed += 1
            print(f"fail {issuer.code} {link.title} {link.url}: {exc}", flush=True)
        finally:
            time.sleep(SLEEP_SEC)
    return counts


def run(
    dest: Path,
    config: Path = DEFAULT_CONFIG,
    *,
    only: list[str] | None = None,
    limit: int | None = None,
    years: int | None = None,
    dry_run: bool = False,
    get: Getter | None = None,
    get_page: PageGetter | None = None,
    read_date: PdfDate | None = None,
    now: datetime | None = None,
    disk_usage: DiskUsage = shutil.disk_usage,
    ledger: Path | None = None,
    pending: int | None = None,
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
    since = years_ago(when, years) if years is not None else None
    print(
        f"homepage_briefing start issuers={len(issuers)} dry_run={int(dry_run)} dest={dest}"
        + (f" since={since.date()}" if since is not None else ""),
        flush=True,
    )
    book = load_ledger(ledger) if ledger is not None else Ledger()
    before = book.as_json()
    known_old = book.old if ledger is not None else None
    known_dup = book.dup
    all_issuers = issuers
    if pending is not None:
        issuers = pending_issuers(issuers, when, pending, book.checked)
        print(f"homepage_briefing pending={len(issuers)} {' '.join(i.code for i in issuers)}".rstrip(), flush=True)

    def keep() -> None:
        if ledger is not None and not dry_run and book.as_json() != before:
            save_ledger(ledger, book)

    total = Counts()
    stopped = "done"
    for issuer in issuers:
        counts = Counts()
        try:
            fetch_issuer(
                issuer, dest, get=getter, get_page=pager, read_date=reader, now=when, limit=limit,
                dry_run=dry_run, since=since, disk_usage=disk_usage, counts=counts, known_old=known_old,
                known_dup=known_dup,
            )
        except DiskLow:
            stopped = "disk_low"
        except Exception as exc:
            down = abort_if_disconnected("homepage_briefing", dest, exc)
            if down is not None:
                keep()
                return down
            counts.failed += 1
            print(f"fail {issuer.code} {' '.join(issuer.index_urls)}: {exc}", flush=True)
        else:
            if pending is not None:
                book.checked[jq_lookup_code(issuer.code)] = _ts(when)
        print(f"homepage_briefing {issuer.code} {counts.text()}", flush=True)
        total.add(counts)
        if stopped != "done":
            break
    keep()
    if pending is None and not only:
        missing = missing_companies(all_issuers, when)
        for code, at, title in missing:
            print(f"missing {code} {_ts(at)} {title}", flush=True)
        print(f"homepage_briefing missing={len(missing)}", flush=True)
        stale = stale_companies(all_issuers, when)
        fresh = 0
        for code, at, title in stale:
            known = book.stale.get(code) == _ts(at)
            fresh += 0 if known else 1
            print(f"stale {code} {_ts(at)} {'known' if known else 'new'} {title}", flush=True)
        book.stale = {code: _ts(at) for code, at, _title in stale}
        keep()
        print(f"homepage_briefing stale_new={fresh} stale_known={len(stale) - fresh}", flush=True)
    print(f"homepage_briefing stopped={stopped} {total.text()}", flush=True)
    return 1 if total.failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch briefing PDFs from company IR pages into data/briefing")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="issuer list JSON")
    ap.add_argument("--only", action="append", help="stock code in the config (repeatable)")
    ap.add_argument("--limit", type=int, help="new PDFs per issuer in this run, from the top of the page")
    ap.add_argument("--years", type=int, help="skip documents older than N years from today")
    ap.add_argument("--ledger", help="JSON of old documents and --pending checks (not under dest)")
    ap.add_argument("--pending", type=int, help="only issuers with a material-flagged tanshin in the last N days")
    ap.add_argument("--dry-run", action="store_true", help="list new PDFs, do not download")
    args = ap.parse_args(argv)
    for name in ("limit", "years", "pending"):
        value = getattr(args, name)
        if value is not None and value < 1:
            print(f"--{name} must be a positive integer", file=sys.stderr)
            return 2
    return run(
        Path(args.dest), Path(args.config), only=args.only, limit=args.limit, years=args.years,
        dry_run=args.dry_run, ledger=Path(args.ledger) if args.ledger else None, pending=args.pending,
    )


if __name__ == "__main__":
    raise SystemExit(main())

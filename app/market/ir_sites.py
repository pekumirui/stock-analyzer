"""上場会社の自社 IR サイト URL を ir_sites に残す（KAB-319）。

候補は強い順: 月次設定の index_url → ホームページから刈った IR リンク →
短信カバーの tse-ed-t:URL / tse-re-t:URL → 最新の決算短信 HTML。
普通株コード（末尾 0）に畳む。HTTP は fetch を注入する。ジョブは init_db しない。
"""
from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urljoin, urlparse, urlunparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import IR_MONTHLY_PATH
from ..db import upsert
from ..models import IrSite, Stock, TdnetPdfDocument, XbrlElement, XbrlFact, XbrlFiling

Fetch = Callable[[str], tuple[bytes, str]]

JST = timezone(timedelta(hours=9))

SOURCE_IR_MONTHLY = "ir_monthly"
SOURCE_HOMEPAGE_NAV = "homepage_nav"
SOURCE_XBRL_URL = "xbrl_url"
SOURCE_TANSHIN_HTML = "tanshin_html"
SOURCE_ORDER = (
    SOURCE_IR_MONTHLY,
    SOURCE_HOMEPAGE_NAV,
    SOURCE_XBRL_URL,
    SOURCE_TANSHIN_HTML,
)

KIND_IR = "ir"
KIND_HOME = "home"

CONF_VERIFIED = "verified"
CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"

XBRL_URL_KEYS = ("tse-ed-t", "tse-re-t")
XBRL_URL_NAME = "URL"

_CODE4_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_URL_RE = re.compile(r"https?://[A-Za-z0-9][A-Za-z0-9._~:/\?#@!$&'()*+,;=%\-]*", re.I)
_SCHEMELESS_RE = re.compile(r"^(?:www\.)[A-Za-z0-9.-]+(?:[/:].*)?$", re.I)
_IR_PATH_RE = re.compile(r"(?:^|/)(?:ir|investor|investors)(?:/|$|\.)", re.I)
_IR_TEXT_RE = re.compile(r"(?<![A-Za-z])IR(?![A-Za-z])|ＩＲ|投資家|investor", re.I)
_IR_TOP_RE = re.compile(r"/(?:ir|investors?)/?$", re.I)
_HARVEST_PENALTY_RE = re.compile(
    r"/(?:events?|contact|newsletter|notify|announc|shareholders?|stockholders?|interview)(?:/|$)",
    re.I,
)
_HARVEST_BOOST_RE = re.compile(r"/(?:library|financial-results?|overview)(?:/|$)", re.I)
_HARVEST_BAD_TEXT_RE = re.compile(r"電子公告|公告|IRTV|IR動画")
_FILE_RE = re.compile(
    r"\.(?:pdf|xlsx?|csv|zip|docx?|pptx?|exe|wmv|mp4)(?:$|[?#])", re.I)
_SKIP_HREF_RE = re.compile(r"^(?:javascript:|mailto:|#)", re.I)
_FW = str.maketrans({"：": ":", "／": "/", "．": "."})
_TRAIL_PUNCT = ".,);]）｣」'\"、。"

_SKIP_HOST_NEEDLES = (
    "w3.org",
    "tdnet.info",
    "edinet-fsa.go.jp",
    "jpx.co.jp",
    "sec.gov",
    "twitter.com",
    "x.com",
    "facebook.com",
    "youtube.com",
    "linkedin.com",
    "instagram.com",
    "prtimes.jp",
    "line.me",
    "irbank.net",
)

_RANK = {
    (SOURCE_IR_MONTHLY, KIND_IR): 100,
    (SOURCE_HOMEPAGE_NAV, KIND_IR): 80,
    (SOURCE_TANSHIN_HTML, KIND_IR): 60,
    (SOURCE_XBRL_URL, KIND_IR): 50,
    (SOURCE_XBRL_URL, KIND_HOME): 40,
    (SOURCE_TANSHIN_HTML, KIND_HOME): 20,
}


def now_jst() -> datetime:
    return datetime.now(JST).replace(tzinfo=None)


def ordinary_code(code: str) -> str:
    """4 桁・優先株を含む 5 桁を、普通株の 5 桁（末尾 0）にする。"""
    s = re.sub(r"\s+", "", code or "").upper()
    if len(s) >= 5:
        s = s[:4]
    if not _CODE4_RE.fullmatch(s):
        raise ValueError(f"invalid stock code: {code!r}")
    return s + "0"


def source_rank(source: str, url_kind: str) -> int:
    return _RANK.get((source, url_kind), 0)


def is_ir_url(url: str) -> bool:
    path = unquote(urlparse(url).path or "")
    return bool(_IR_PATH_RE.search(path) or "投資家" in path)


def normalize_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().translate(_FW)
    if not s:
        return None
    s = s.rstrip(_TRAIL_PUNCT)
    if "://" not in s and _SCHEMELESS_RE.match(s):
        s = "https://" + s
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or any(n in host for n in _SKIP_HOST_NEEDLES):
        return None
    if "/wp-content/" in (parsed.path or "").lower():
        return None
    if _FILE_RE.search(parsed.path or "") or _FILE_RE.search(parsed.query or ""):
        return None
    return parsed.geturl()


def extract_urls_from_html(html: str) -> list[str]:
    """タグを剥がした本文から会社サイトの URL を出現順で返す。"""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = text.translate(_FW)
    out: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = normalize_url(match.group(0))
        if url is None or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def pick_tanshin_url(urls: list[str]) -> tuple[str, str] | None:
    """短信本文の URL から IR ページを優先し、無ければ先頭のホームページ。"""
    pages = [u for u in urls if normalize_url(u)]
    for url in pages:
        if is_ir_url(url):
            return url, KIND_IR
    if pages:
        return pages[0], KIND_HOME
    return None


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


def harvest_ir_href(html: str, base_url: str) -> str | None:
    """ホームページの <a> から IR / 投資家 / investors のリンクを 1 本選ぶ。推測で /ir/ は付けない。"""
    parser = _AParser()
    parser.feed(html or "")
    parser.close()
    home_host = (urlparse(base_url).hostname or "").lower()
    best: tuple[int, str] | None = None
    seen: set[str] = set()
    for href, text in parser.links:
        raw = (href or "").strip()
        if not raw or _SKIP_HREF_RE.match(raw):
            continue
        abs_url = normalize_url(urljoin(base_url, raw))
        if abs_url is None:
            continue
        parsed = urlparse(abs_url)
        abs_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        if abs_url in seen:
            continue
        seen.add(abs_url)
        path = urlparse(abs_url).path or "/"
        path_ir = is_ir_url(abs_url)
        text_ir = bool(_IR_TEXT_RE.search(text or ""))
        bad_text = bool(_HARVEST_BAD_TEXT_RE.search(text or ""))
        if not path_ir and not text_ir:
            continue
        host = (urlparse(abs_url).hostname or "").lower()
        score = 0
        if host == home_host:
            score += 50
        if path_ir:
            score += 100
        if text_ir and not bad_text:
            score += 40
        if _IR_TOP_RE.search(path):
            score += 50
        if _HARVEST_BOOST_RE.search(path):
            score += 25
        if _HARVEST_PENALTY_RE.search(path) or bad_text:
            score -= 50
        score -= path.strip("/").count("/") * 12
        score -= min(len(path), 80)
        if best is None or score > best[0]:
            best = (score, abs_url)
    return None if best is None else best[1]


def load_ir_monthly_urls(path: Path | None = None) -> dict[str, str]:
    """ir_monthly.json の index_url。E-IR も会社側の index_url を使う。JSON API は載せない。"""
    src = IR_MONTHLY_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    blocks: list = []
    if isinstance(raw, dict):
        for key in ("issuers", "html"):
            block = raw.get(key)
            if isinstance(block, list):
                blocks.extend(block)
    elif isinstance(raw, list):
        blocks = list(raw)
    out: dict[str, str] = {}
    for item in blocks:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        if len(code) >= 5:
            code = code[:4]
        if not _CODE4_RE.fullmatch(code):
            continue
        url = normalize_url(str(item.get("index_url") or ""))
        if url is None:
            continue
        out.setdefault(code + "0", url)
    return out


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    confidence: str
    url_kind: str
    origin: str

    @property
    def rank(self) -> int:
        return source_rank(self.source, self.url_kind)


@dataclass
class SyncReport:
    codes: int = 0
    put: int = 0
    unchanged: int = 0
    unreachable: int = 0
    fetched: int = 0
    chosen: dict[str, int] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    def summary(self, *, dry_run: bool = False) -> str:
        prefix = "ir_sites dry_run" if dry_run else "ir_sites"
        keys = SOURCE_ORDER + ("none",)
        chosen = " ".join(f"{k}={self.chosen.get(k, 0)}" for k in keys)
        return (
            f"{prefix} codes={self.codes} put={self.put} unchanged={self.unchanged} "
            f"unreachable={self.unreachable} fetched={self.fetched} chosen {chosen}"
        )


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def _confidence(source: str, url_kind: str) -> str:
    if source == SOURCE_IR_MONTHLY:
        return CONF_VERIFIED
    if source == SOURCE_HOMEPAGE_NAV:
        return CONF_HIGH
    if source == SOURCE_XBRL_URL:
        return CONF_MEDIUM
    if url_kind == KIND_IR:
        return CONF_MEDIUM
    return CONF_LOW


def _candidate(url: str, source: str, origin: str, url_kind: str | None = None) -> Candidate:
    kind = url_kind or (KIND_IR if source == SOURCE_IR_MONTHLY or is_ir_url(url) else KIND_HOME)
    if source in (SOURCE_IR_MONTHLY, SOURCE_HOMEPAGE_NAV):
        kind = KIND_IR
    return Candidate(
        url=url,
        source=source,
        confidence=_confidence(source, kind),
        url_kind=kind,
        origin=origin,
    )


def _target_codes(db: Session, codes: list[str] | None) -> list[str]:
    if codes is not None:
        return sorted({ordinary_code(c) for c in codes})
    rows = db.execute(select(Stock.code)).scalars().all()
    return sorted({ordinary_code(c) for c in rows})


def _load_xbrl_urls(db: Session, codes: list[str]) -> dict[str, tuple[str, str]]:
    if not codes:
        return {}
    stmt = (
        select(
            XbrlFiling.code,
            XbrlFact.value_text,
            XbrlFiling.doc_id,
            XbrlFiling.submitted_at,
        )
        .join(XbrlFact, XbrlFact.filing_id == XbrlFiling.id)
        .join(XbrlElement, XbrlElement.id == XbrlFact.element_id)
        .where(
            XbrlFiling.code.in_(codes),
            XbrlElement.namespace_key.in_(XBRL_URL_KEYS),
            XbrlElement.local_name == XBRL_URL_NAME,
            XbrlFact.is_nil.is_(False),
            XbrlFact.value_text.isnot(None),
        )
        .order_by(XbrlFiling.submitted_at.desc(), XbrlFiling.id.desc())
    )
    out: dict[str, tuple[str, str]] = {}
    for code, value, doc_id, _submitted in db.execute(stmt):
        key = ordinary_code(code)
        if key in out:
            continue
        url = normalize_url(value)
        if url is None:
            continue
        out[key] = (url, f"xbrl:{doc_id}")
    return out


def _tanshin_home(urls: list[str]) -> str | None:
    for url in urls:
        if not is_ir_url(url):
            return url
    return None


def _load_tanshin(db: Session, codes: list[str]) -> dict[str, tuple[str, str, str, str | None]]:
    """code -> (url, url_kind, disclosure_no, harvest_home)。最新の決算短信 HTML 1 本。"""
    if not codes:
        return {}
    ranked = (
        select(
            TdnetPdfDocument.disclosure_no,
            TdnetPdfDocument.code,
            func.row_number().over(
                partition_by=TdnetPdfDocument.code,
                order_by=(
                    TdnetPdfDocument.disclosed_date.desc(),
                    TdnetPdfDocument.disclosure_no.desc(),
                ),
            ).label("rn"),
        )
        .where(
            TdnetPdfDocument.code.in_(codes),
            TdnetPdfDocument.status == "ok",
            TdnetPdfDocument.html_gz.isnot(None),
            TdnetPdfDocument.title.contains("決算短信"),
        )
        .subquery()
    )
    stmt = (
        select(TdnetPdfDocument)
        .join(ranked, TdnetPdfDocument.disclosure_no == ranked.c.disclosure_no)
        .where(ranked.c.rn == 1)
    )
    out: dict[str, tuple[str, str, str]] = {}
    for doc in db.execute(stmt).scalars():
        key = ordinary_code(doc.code)
        if key in out:
            continue
        html = gzip.decompress(doc.html_gz).decode("utf-8", "replace")
        urls = extract_urls_from_html(html)
        picked = pick_tanshin_url(urls)
        if picked is None:
            continue
        url, kind = picked
        out[key] = (url, kind, doc.disclosure_no, _tanshin_home(urls))
    return out


def _homepage_for_harvest(
    xbrl: tuple[str, str] | None,
    tanshin: tuple[str, str, str, str | None] | None,
) -> str | None:
    if xbrl is not None:
        return xbrl[0]
    if tanshin is None:
        return None
    url, kind, _origin, home = tanshin
    if home:
        return home
    if kind == KIND_HOME:
        return url
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


def _pick(cands: list[Candidate]) -> Candidate | None:
    if not cands:
        return None
    return max(cands, key=lambda c: (c.rank, c.url_kind == KIND_IR, c.url))


def _should_fetch(existing: IrSite | None, local_best: Candidate | None, refresh: bool) -> bool:
    if refresh:
        return True
    if existing is not None and existing.source in (SOURCE_IR_MONTHLY, SOURCE_HOMEPAGE_NAV):
        return False
    if local_best is not None and local_best.source == SOURCE_IR_MONTHLY:
        return False
    return True


def _keep_existing(existing: IrSite | None, chosen: Candidate | None, refresh: bool) -> bool:
    if existing is None or chosen is None:
        return False
    if refresh:
        return False
    return source_rank(existing.source, existing.url_kind) > chosen.rank


def _same_row(existing: IrSite, chosen: Candidate) -> bool:
    return (
        existing.url == chosen.url
        and existing.source == chosen.source
        and existing.confidence == chosen.confidence
        and existing.url_kind == chosen.url_kind
        and (existing.origin or "") == chosen.origin
    )


def sync_ir_sites(
    db: Session,
    codes: list[str] | None = None,
    *,
    fetch: Fetch | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    ir_monthly: Path | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """codes が None なら stocks の普通株すべて。fetch が None なら HTTP しない。"""
    fetched_at = now if now is not None else now_jst()
    targets = _target_codes(db, codes)
    monthly = load_ir_monthly_urls(ir_monthly)
    xbrl_map = _load_xbrl_urls(db, targets)
    tanshin_map = _load_tanshin(db, targets)
    existing_rows = {
        row.code: row
        for row in db.execute(select(IrSite).where(IrSite.code.in_(targets))).scalars()
    } if targets else {}

    report = SyncReport(codes=len(targets))
    writes: list[dict] = []
    for code in targets:
        existing = existing_rows.get(code)
        local: list[Candidate] = []
        if code in monthly:
            local.append(_candidate(monthly[code], SOURCE_IR_MONTHLY, "ir_monthly.json"))
        if code in xbrl_map:
            url, origin = xbrl_map[code]
            local.append(_candidate(url, SOURCE_XBRL_URL, origin))
        if code in tanshin_map:
            url, kind, disc, _home = tanshin_map[code]
            local.append(_candidate(url, SOURCE_TANSHIN_HTML, disc, url_kind=kind))
        local_best = _pick(local)

        harvested: Candidate | None = None
        home = _homepage_for_harvest(xbrl_map.get(code), tanshin_map.get(code))
        if fetch is not None and home and _should_fetch(existing, local_best, refresh):
            try:
                body, final_url = fetch(home)
                report.fetched += 1
                ir_url = harvest_ir_href(_decode(body), final_url or home)
                if ir_url:
                    harvested = _candidate(ir_url, SOURCE_HOMEPAGE_NAV, final_url or home)
            except Exception as exc:
                report.unreachable += 1
                report.lines.append(f"unreachable {code} {SOURCE_HOMEPAGE_NAV} {type(exc).__name__}({exc})")

        chosen = _pick(([harvested] if harvested else []) + local)
        if chosen is None:
            report.chosen["none"] = report.chosen.get("none", 0) + 1
            continue
        if _keep_existing(existing, chosen, refresh):
            report.unchanged += 1
            report.chosen[existing.source] = report.chosen.get(existing.source, 0) + 1
            continue
        report.chosen[chosen.source] = report.chosen.get(chosen.source, 0) + 1
        if existing is not None and _same_row(existing, chosen):
            report.unchanged += 1
            continue
        report.put += 1
        report.lines.append(f"put {code} {chosen.source} {chosen.url} origin={chosen.origin}")
        writes.append({
            "code": code,
            "url": chosen.url,
            "source": chosen.source,
            "confidence": chosen.confidence,
            "url_kind": chosen.url_kind,
            "origin": chosen.origin,
            "fetched_at": fetched_at,
        })

    if writes and not dry_run:
        upsert(db, IrSite, writes)
    return report

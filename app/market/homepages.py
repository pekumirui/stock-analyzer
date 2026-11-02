"""上場会社のホームページ URL を homepages に残す（KAB-319）。

候補は強い順: 短信カバーの tse-ed-t:URL / tse-re-t:URL → 最新の決算短信 HTML の先頭の会社 URL。
普通株コード（末尾 0）に畳む。HTTP はしない。ジョブは init_db しない。
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import Homepage, Stock, TdnetPdfDocument, XbrlElement, XbrlFact, XbrlFiling

JST = timezone(timedelta(hours=9))

SOURCE_XBRL_URL = "xbrl_url"
SOURCE_TANSHIN_HTML = "tanshin_html"
SOURCE_ORDER = (SOURCE_XBRL_URL, SOURCE_TANSHIN_HTML)

CONF_HIGH = "high"
CONF_MEDIUM = "medium"

XBRL_URL_KEYS = ("tse-ed-t", "tse-re-t")
XBRL_URL_NAME = "URL"

_CODE4_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_URL_RE = re.compile(r"https?://[A-Za-z0-9][A-Za-z0-9._~:/\?#@!$&'()*+,;=%\-]*", re.I)
_SCHEMELESS_RE = re.compile(r"^(?:www\.)[A-Za-z0-9.-]+(?:[/:].*)?$", re.I)
_FILE_RE = re.compile(
    r"\.(?:pdf|xlsx?|csv|zip|docx?|pptx?|exe|wmv|mp4)(?:$|[?#])", re.I)
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
    SOURCE_XBRL_URL: 80,
    SOURCE_TANSHIN_HTML: 40,
}
_CONF = {
    SOURCE_XBRL_URL: CONF_HIGH,
    SOURCE_TANSHIN_HTML: CONF_MEDIUM,
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


def source_rank(source: str) -> int:
    return _RANK.get(source, 0)


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


def pick_homepage_url(urls: list[str]) -> str | None:
    """短信本文の先頭の会社 URL（カバーのホームページ欄）。"""
    for url in urls:
        if normalize_url(url):
            return url
    return None


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    confidence: str
    origin: str

    @property
    def rank(self) -> int:
        return source_rank(self.source)


@dataclass
class SyncReport:
    codes: int = 0
    put: int = 0
    unchanged: int = 0
    chosen: dict[str, int] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    def summary(self, *, dry_run: bool = False) -> str:
        prefix = "homepages dry_run" if dry_run else "homepages"
        keys = SOURCE_ORDER + ("none",)
        chosen = " ".join(f"{k}={self.chosen.get(k, 0)}" for k in keys)
        return (
            f"{prefix} codes={self.codes} put={self.put} unchanged={self.unchanged} "
            f"chosen {chosen}"
        )


def _candidate(url: str, source: str, origin: str) -> Candidate:
    return Candidate(
        url=url,
        source=source,
        confidence=_CONF[source],
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


def _load_tanshin(db: Session, codes: list[str]) -> dict[str, tuple[str, str]]:
    """code -> (url, disclosure_no)。最新の決算短信 HTML 1 本。"""
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
    out: dict[str, tuple[str, str]] = {}
    for doc in db.execute(stmt).scalars():
        key = ordinary_code(doc.code)
        if key in out:
            continue
        html = gzip.decompress(doc.html_gz).decode("utf-8", "replace")
        url = pick_homepage_url(extract_urls_from_html(html))
        if url is None:
            continue
        out[key] = (url, doc.disclosure_no)
    return out


def _pick(cands: list[Candidate]) -> Candidate | None:
    if not cands:
        return None
    return max(cands, key=lambda c: (c.rank, c.url))


def _keep_existing(existing: Homepage | None, chosen: Candidate | None, refresh: bool) -> bool:
    if existing is None or chosen is None:
        return False
    if refresh:
        return False
    return source_rank(existing.source) > chosen.rank


def _same_row(existing: Homepage, chosen: Candidate) -> bool:
    return (
        existing.url == chosen.url
        and existing.source == chosen.source
        and existing.confidence == chosen.confidence
        and (existing.origin or "") == chosen.origin
    )


def sync_homepages(
    db: Session,
    codes: list[str] | None = None,
    *,
    refresh: bool = False,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SyncReport:
    """codes が None なら stocks の普通株すべて。"""
    fetched_at = now if now is not None else now_jst()
    targets = _target_codes(db, codes)
    xbrl_map = _load_xbrl_urls(db, targets)
    tanshin_map = _load_tanshin(db, targets)
    existing_rows = {
        row.code: row
        for row in db.execute(select(Homepage).where(Homepage.code.in_(targets))).scalars()
    } if targets else {}

    report = SyncReport(codes=len(targets))
    writes: list[dict] = []
    for code in targets:
        existing = existing_rows.get(code)
        local: list[Candidate] = []
        if code in xbrl_map:
            url, origin = xbrl_map[code]
            local.append(_candidate(url, SOURCE_XBRL_URL, origin))
        if code in tanshin_map:
            url, disc = tanshin_map[code]
            local.append(_candidate(url, SOURCE_TANSHIN_HTML, disc))
        chosen = _pick(local)
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
            "origin": chosen.origin,
            "fetched_at": fetched_at,
        })

    if writes and not dry_run:
        upsert(db, Homepage, writes)
    return report

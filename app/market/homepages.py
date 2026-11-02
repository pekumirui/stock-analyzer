"""上場会社のホームページ URL を homepages に残す（KAB-319、取りこぼしは KAB-322）。

候補は新しい提出を優先。同じ日なら manual > xbrl_url > tanshin_html。
XBRL は code ごとに最新 1 件だけ。短信は『URL』『ホームページ』ラベル近くの URL。
スキーム無しのドメインはラベルの近くだけ候補にし、https:// を補う。
訂正の題名と本文が 5KB 未満の短信は飛ばし、新しい順に最大 5 本見て最初に取れた URL を採る。
題名の「決算短信」「FinancialStatements」、または statements の種類が FinancialStatements。
jpx.co.jp は 86970 だけ残す。持株会社化の旧コードは PREDECESSOR_CODES。
普通株コード（末尾 0）に畳む。HTTP はしない。ジョブは init_db しない。
"""
from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import upsert
from ..models import (
    Homepage,
    Statement,
    Stock,
    TdnetPdfDocument,
    XbrlElement,
    XbrlFact,
    XbrlFiling,
)

JST = timezone(timedelta(hours=9))

SOURCE_MANUAL = "manual"
SOURCE_XBRL_URL = "xbrl_url"
SOURCE_TANSHIN_HTML = "tanshin_html"
SOURCE_ORDER = (SOURCE_MANUAL, SOURCE_XBRL_URL, SOURCE_TANSHIN_HTML)

ORIGIN_MANUAL = "manual"

XBRL_URL_KEYS = ("tse-ed-t", "tse-re-t")
XBRL_URL_NAME = "URL"

_CODE4_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_URL_RE = re.compile(r"https?://[A-Za-z0-9][A-Za-z0-9._~:/\?#@!$&'()*+,;=%\-]*", re.I)
# 値全体がドメイン（XBRL の wize-inc.jp、www. 無しも）。パスが付いてもよい
_SCHEMELESS_DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}"
    r"(?:[/:?#].*)?$",
    re.I,
)
# 本文中。メールと、すでにスキームがある URL の途中には当たらない
_SCHEMELESS_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9@./])"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,}"
    r"(?:/[A-Za-z0-9._~:/?#@!$&'()*+,;=%\-]*)?",
    re.I,
)
_FILE_RE = re.compile(
    r"\.(?:pdf|xlsx?|csv|zip|docx?|pptx?|exe|wmv|mp4)(?:$|[?#])", re.I)
_BARE_FILE_TLDS = frozenset({
    "pdf", "xls", "xlsx", "csv", "zip", "doc", "docx", "ppt", "pptx",
    "exe", "wmv", "mp4", "html", "htm", "txt", "xml", "json",
})
_FW = str.maketrans({"：": ":", "／": "/", "．": "."})
_TRAIL_PUNCT = ".,);]）｣」'\"、。"
_LABEL_RE = re.compile(
    r"(?:ホームページ|ＨＰ|(?<![A-Za-z0-9])URL(?![A-Za-z0-9])|ＵＲＬ)")
_LABEL_WINDOW = 400
# pdfium が ToUnicode のない欧文を (cid:N) にする。N + 29 が印字 ASCII（日本トリムの表紙）
_CID_RE = re.compile(r"\(cid:(\d+)\)")
_CID_ASCII_DELTA = 29

JPX_CODE = "86970"
TANSHIN_SCAN = 5
SHORT_HTML_BYTES = 5_000
# 持株会社化で書類が旧コードに残っている。新しいコードに書類が無いときだけ旧を見る
PREDECESSOR_CODES = {
    "641A0": "72400",  # ＮＯＫ Group ← ＮＯＫ
    "642A0": "95080",  # キューデンホールディングス ← 九州電力
    "590A0": "44490",  # ギフティグループ ← ギフティ
}

# 完全一致、またはドット区切りの末尾一致（examplex.com は x.com に当たらない）
_SKIP_HOSTS = (
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
    SOURCE_MANUAL: 100,
    SOURCE_XBRL_URL: 80,
    SOURCE_TANSHIN_HTML: 40,
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


def origin_xbrl(doc_id: str) -> str:
    return f"xbrl:{doc_id}"


def origin_tanshin(disclosure_no: str) -> str:
    return f"tanshin:{disclosure_no}"


def _allows_jpx(code: str | None) -> bool:
    if not code:
        return False
    try:
        return ordinary_code(code) == JPX_CODE
    except ValueError:
        return False


def host_is_skipped(host: str, *, allow_jpx: bool = False) -> bool:
    h = (host or "").lower().rstrip(".")
    if not h:
        return True
    for needle in _SKIP_HOSTS:
        if allow_jpx and needle == "jpx.co.jp":
            continue
        if h == needle or h.endswith("." + needle):
            return True
    return False


def url_host(url: str | None) -> str | None:
    """正規化したホスト。小文字、末尾ドット無し、先頭の www. を除く。"""
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _bare_file_host(host: str) -> bool:
    return host.rsplit(".", 1)[-1] in _BARE_FILE_TLDS


def normalize_url(raw: str | None, *, allow_jpx: bool = False) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().translate(_FW)
    if not s:
        return None
    s = s.rstrip(_TRAIL_PUNCT)
    bare = False
    if "://" not in s and _SCHEMELESS_DOMAIN_RE.fullmatch(s):
        s = "https://" + s
        bare = True
    parsed = urlparse(s)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if bare and _bare_file_host(host):
        return None
    if host_is_skipped(host, allow_jpx=allow_jpx):
        return None
    if "/wp-content/" in (parsed.path or "").lower():
        return None
    if _FILE_RE.search(parsed.path or "") or _FILE_RE.search(parsed.query or ""):
        return None
    return parsed.geturl()


def _decode_cid_ascii(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        code = int(match.group(1)) + _CID_ASCII_DELTA
        if 32 <= code <= 126:
            return chr(code)
        return " "
    return _CID_RE.sub(repl, html)


def _plain_text(html: str) -> str:
    text = _decode_cid_ascii(html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return text.translate(_FW)


def _url_matches(text: str, *, schemeless: bool) -> list[re.Match[str]]:
    http_matches = list(_URL_RE.finditer(text))
    spans = [(match.start(), match.end()) for match in http_matches]
    found = list(http_matches)
    if schemeless:
        for match in _SCHEMELESS_IN_TEXT_RE.finditer(text):
            if any(start <= match.start() < end for start, end in spans):
                continue
            found.append(match)
    found.sort(key=lambda match: match.start())
    return found


def _urls_from_text(
    text: str, *, allow_jpx: bool = False, schemeless: bool = False,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in _url_matches(text, schemeless=schemeless):
        url = normalize_url(match.group(0), allow_jpx=allow_jpx)
        if url is None or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def extract_urls_from_html(html: str) -> list[str]:
    """タグを剥がした本文から会社サイトの URL を出現順で返す。スキーム無しは含めない。"""
    return _urls_from_text(_plain_text(html))


def pick_homepage_url(html: str, *, allow_jpx: bool = False) -> str | None:
    """『URL』『ホームページ』ラベルの直後を優先。無ければ先頭の会社 URL。

    スキーム無しのドメインはラベルの近くだけ。本文のどこにでも拾うと誤検出になる。
    """
    text = _plain_text(html)
    for match in _LABEL_RE.finditer(text):
        window = text[match.end(): match.end() + _LABEL_WINDOW]
        urls = _urls_from_text(window, allow_jpx=allow_jpx, schemeless=True)
        if urls:
            return urls[0]
    urls = _urls_from_text(text, allow_jpx=allow_jpx)
    return urls[0] if urls else None


_ASOF_MIN = datetime(1, 1, 1)  # noqa: DTZ001


def _as_of_dt(value: datetime | date | None) -> datetime:
    if value is None:
        return _ASOF_MIN
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    return datetime(value.year, value.month, value.day)  # noqa: DTZ001


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    origin: str
    as_of: datetime | date | None = None

    @property
    def rank(self) -> int:
        return source_rank(self.source)

    @property
    def host(self) -> str:
        return url_host(self.url) or ""

    @property
    def sort_key(self) -> tuple:
        return (_as_of_dt(self.as_of), self.rank, self.url)


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


@dataclass
class HostCompareReport:
    both: int = 0
    same_host: int = 0
    diff_host: int = 0
    diffs: list[str] = field(default_factory=list)

    def summary(self) -> str:
        rate = (self.same_host / self.both) if self.both else 0.0
        return (
            f"homepages compare_hosts both={self.both} same={self.same_host} "
            f"diff={self.diff_host} rate={rate:.3f}"
        )


def _candidate(url: str, source: str, origin: str, as_of: datetime | date | None) -> Candidate:
    return Candidate(url=url, source=source, origin=origin, as_of=as_of)


def _target_codes(db: Session, codes: list[str] | None) -> list[str]:
    if codes is not None:
        return sorted({ordinary_code(c) for c in codes})
    rows = db.execute(select(Stock.code)).scalars().all()
    return sorted({ordinary_code(c) for c in rows})


def _codes_with_predecessors(codes: list[str]) -> list[str]:
    extra = [PREDECESSOR_CODES[code] for code in codes if code in PREDECESSOR_CODES]
    return sorted(set(codes) | set(extra))


def _fill_predecessors(found: dict, requested: list[str]) -> dict:
    """新しいコードに候補が無いときだけ、手で持つ旧コードの候補をコピーする。"""
    wanted = set(requested)
    for new, old in PREDECESSOR_CODES.items():
        if new in wanted and new not in found and old in found:
            found[new] = found[old]
    return {code: row for code, row in found.items() if code in wanted}


def _load_xbrl_urls(db: Session, codes: list[str]) -> dict[str, tuple[str, str, datetime]]:
    """code -> (url, origin, submitted_at)。最新 1 件だけ。無効なら候補なし。"""
    if not codes:
        return {}
    query_codes = _codes_with_predecessors(codes)
    ranked = (
        select(
            XbrlFiling.code,
            XbrlFact.value_text,
            XbrlFiling.doc_id,
            XbrlFiling.submitted_at,
            func.row_number().over(
                partition_by=XbrlFiling.code,
                order_by=(XbrlFiling.submitted_at.desc(), XbrlFiling.id.desc()),
            ).label("rn"),
        )
        .join(XbrlFact, XbrlFact.filing_id == XbrlFiling.id)
        .join(XbrlElement, XbrlElement.id == XbrlFact.element_id)
        .where(
            XbrlFiling.code.in_(query_codes),
            XbrlElement.namespace_key.in_(XBRL_URL_KEYS),
            XbrlElement.local_name == XBRL_URL_NAME,
            XbrlFact.is_nil.is_(False),
            XbrlFact.value_text.isnot(None),
        )
        .subquery()
    )
    stmt = select(
        ranked.c.code, ranked.c.value_text, ranked.c.doc_id, ranked.c.submitted_at,
    ).where(ranked.c.rn == 1)
    out: dict[str, tuple[str, str, datetime]] = {}
    for code, value, doc_id, submitted in db.execute(stmt):
        key = ordinary_code(code)
        url = normalize_url(value, allow_jpx=_allows_jpx(code))
        if url is None:
            continue
        prev = out.get(key)
        if prev is not None and _as_of_dt(prev[2]) >= _as_of_dt(submitted):
            continue
        out[key] = (url, origin_xbrl(doc_id), submitted)
    return _fill_predecessors(out, codes)


def _tanshin_rejected(title: str | None, nbytes: int | None) -> bool:
    """訂正短信と、変換後 HTML が 5KB 未満の短信は候補にしない。"""
    if title and "訂正" in title:
        return True
    return nbytes is not None and nbytes < SHORT_HTML_BYTES


def _load_tanshin(db: Session, codes: list[str]) -> dict[str, tuple[str, str, date]]:
    """code -> (url, origin, disclosed_date)。新しい順に最大 TANSHIN_SCAN 本。"""
    if not codes:
        return {}
    query_codes = _codes_with_predecessors(codes)
    ranked = (
        select(
            TdnetPdfDocument.disclosure_no,
            TdnetPdfDocument.code,
            TdnetPdfDocument.disclosed_date,
            TdnetPdfDocument.title,
            TdnetPdfDocument.bytes,
            func.row_number().over(
                partition_by=TdnetPdfDocument.code,
                order_by=(
                    TdnetPdfDocument.disclosed_date.desc(),
                    TdnetPdfDocument.disclosure_no.desc(),
                ),
            ).label("rn"),
        )
        .outerjoin(Statement, Statement.disclosure_no == TdnetPdfDocument.disclosure_no)
        .where(
            TdnetPdfDocument.code.in_(query_codes),
            TdnetPdfDocument.status == "ok",
            TdnetPdfDocument.html_gz.isnot(None),
            or_(
                TdnetPdfDocument.title.contains("決算短信"),
                TdnetPdfDocument.title.contains("FinancialStatements"),
                Statement.type_of_document.contains("FinancialStatements"),
            ),
        )
        .subquery()
    )
    stmt = (
        select(
            ranked.c.disclosure_no,
            ranked.c.code,
            ranked.c.disclosed_date,
            ranked.c.title,
            ranked.c.bytes,
        )
        .where(ranked.c.rn <= TANSHIN_SCAN)
        .order_by(ranked.c.code, ranked.c.rn)
    )
    grouped: dict[str, list[tuple]] = {}
    for disclosure_no, code, disclosed, title, nbytes in db.execute(stmt):
        grouped.setdefault(ordinary_code(code), []).append(
            (disclosure_no, disclosed, title, nbytes))
    out: dict[str, tuple[str, str, date]] = {}
    wanted = set(codes) | {
        old for new, old in PREDECESSOR_CODES.items() if new in codes
    }
    for code in wanted:
        allow_jpx = _allows_jpx(code)
        for disclosure_no, disclosed, title, nbytes in grouped.get(code, ()):
            if _tanshin_rejected(title, nbytes):
                continue
            doc = db.get(TdnetPdfDocument, disclosure_no)
            if doc is None or not doc.html_gz:
                continue
            html = gzip.decompress(doc.html_gz).decode("utf-8", "replace")
            url = pick_homepage_url(html, allow_jpx=allow_jpx)
            if url is None:
                continue
            out[code] = (url, origin_tanshin(disclosure_no), disclosed or date.min)
            break
    return _fill_predecessors(out, codes)


def _pick(cands: list[Candidate]) -> Candidate | None:
    if not cands:
        return None
    return max(cands, key=lambda c: c.sort_key)


def _same_row(existing: Homepage, chosen: Candidate) -> bool:
    return (
        existing.url == chosen.url
        and existing.host == chosen.host
        and existing.source == chosen.source
        and existing.origin == chosen.origin
    )


def _touch_dict(existing: Homepage, checked_at: datetime) -> dict:
    """INSERT は NOT NULL を全部載せる。ON CONFLICT で checked_at だけ進めたいときも同じ。"""
    return {
        "code": existing.code,
        "url": existing.url,
        "host": existing.host,
        "source": existing.source,
        "origin": existing.origin,
        "changed_at": existing.changed_at,
        "checked_at": checked_at,
    }


def _row_dict(code: str, chosen: Candidate, *, changed_at: datetime, checked_at: datetime) -> dict:
    return {
        "code": code,
        "url": chosen.url,
        "host": chosen.host,
        "source": chosen.source,
        "origin": chosen.origin,
        "changed_at": changed_at,
        "checked_at": checked_at,
    }


def set_manual(
    db: Session,
    code: str,
    url: str,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> SyncReport:
    """手入力。source=manual。ジョブの自動更新では上書きしない。"""
    fetched_at = now if now is not None else now_jst()
    key = ordinary_code(code)
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError(f"invalid homepage url: {url!r}")
    chosen = _candidate(normalized, SOURCE_MANUAL, ORIGIN_MANUAL, fetched_at)
    existing = db.get(Homepage, key)
    report = SyncReport(codes=1)
    report.chosen[SOURCE_MANUAL] = 1
    if existing is not None and _same_row(existing, chosen):
        report.unchanged = 1
        if not dry_run:
            upsert(db, Homepage, [_touch_dict(existing, fetched_at)])
        return report
    report.put = 1
    report.lines.append(f"put {key} {chosen.source} {chosen.url} origin={chosen.origin}")
    if not dry_run:
        upsert(db, Homepage, [_row_dict(key, chosen, changed_at=fetched_at, checked_at=fetched_at)])
    return report


def sync_homepages(
    db: Session,
    codes: list[str] | None = None,
    *,
    refresh: bool = False,  # noqa: ARG001  manual 以外は毎回新しさで選び直す
    dry_run: bool = False,
    now: datetime | None = None,
) -> SyncReport:
    """codes が None なら stocks の普通株すべて。manual は refresh でも残す。"""
    fetched_at = now if now is not None else now_jst()
    targets = _target_codes(db, codes)
    xbrl_map = _load_xbrl_urls(db, targets)
    tanshin_map = _load_tanshin(db, targets)
    existing_rows = {
        row.code: row
        for row in db.execute(select(Homepage).where(Homepage.code.in_(targets))).scalars()
    } if targets else {}

    report = SyncReport(codes=len(targets))
    value_writes: list[dict] = []
    check_writes: list[dict] = []
    for code in targets:
        existing = existing_rows.get(code)
        local: list[Candidate] = []
        if code in xbrl_map:
            url, origin, submitted = xbrl_map[code]
            local.append(_candidate(url, SOURCE_XBRL_URL, origin, submitted))
        if code in tanshin_map:
            url, origin, disclosed = tanshin_map[code]
            local.append(_candidate(url, SOURCE_TANSHIN_HTML, origin, disclosed))
        chosen = _pick(local)
        if existing is not None and existing.source == SOURCE_MANUAL:
            report.unchanged += 1
            report.chosen[SOURCE_MANUAL] = report.chosen.get(SOURCE_MANUAL, 0) + 1
            if not dry_run:
                check_writes.append(_touch_dict(existing, fetched_at))
            continue
        if chosen is None:
            report.chosen["none"] = report.chosen.get("none", 0) + 1
            continue
        report.chosen[chosen.source] = report.chosen.get(chosen.source, 0) + 1
        if existing is not None and _same_row(existing, chosen):
            report.unchanged += 1
            check_writes.append(_touch_dict(existing, fetched_at))
            continue
        report.put += 1
        report.lines.append(f"put {code} {chosen.source} {chosen.url} origin={chosen.origin}")
        value_writes.append(_row_dict(
            code, chosen, changed_at=fetched_at, checked_at=fetched_at))

    if not dry_run:
        if value_writes:
            upsert(db, Homepage, value_writes)
        if check_writes:
            upsert(db, Homepage, check_writes)
    return report


def compare_hosts(db: Session, codes: list[str] | None = None) -> HostCompareReport:
    """XBRL と短信の両方がある会社のホスト一致。書き込みしない。"""
    targets = _target_codes(db, codes)
    xbrl_map = _load_xbrl_urls(db, targets)
    tanshin_map = _load_tanshin(db, targets)
    report = HostCompareReport()
    for code in sorted(set(xbrl_map) & set(tanshin_map)):
        report.both += 1
        x_url, x_origin, _ = xbrl_map[code]
        t_url, t_origin, _ = tanshin_map[code]
        x_host = url_host(x_url)
        t_host = url_host(t_url)
        if x_host == t_host:
            report.same_host += 1
        else:
            report.diff_host += 1
            report.diffs.append(
                f"{code} xbrl={x_url} ({x_origin}) tanshin={t_url} ({t_origin})")
    return report

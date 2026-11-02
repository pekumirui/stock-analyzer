"""最新の決算短信の表紙から、説明資料・説明会の有無を tanshin_covers に残す（KAB-325）。

PDF は変換 HTML の1ページ目だけ。XBRL はサマリー iXBRL（表紙）。
訂正と「決算短信の修正について」は飛ばす。ジョブは init_db しない。
"""
from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.xbrl.documents import load_html
from app.xbrl.pdf import read_pages

from ..models import Homepage, TanshinCover, TdnetPdfDocument, XbrlDocument, XbrlFiling
from .briefing_docs import (
    MATERIAL_WINDOW_DAYS,
    ProbeResult,
    TdnetJudgement,
    fetch_tdnet_range,
    judge_tdnet_rows,
    probe_homepage,
    pub_date,
)
from .homepages import now_jst, ordinary_code

SOURCE_XBRL = "xbrl_summary"
SOURCE_PDF = "tdnet_pdf"
SOURCE_LIVE = "tdnet_live"
_NO_TIME = datetime(1, 1, 1)  # noqa: DTZ001
SAMPLE_LIMIT_MAX = 80
PROBE_LIMIT_MAX = 12
SAMPLE_DAYS_MAX = 21

_WS = re.compile(r"\s+")
_AMENDMENT = re.compile(r"決算短信.*(?:の修正について|の一部修正)")
# pdftotext はラベルと「：有」を改行で割ることがある
_FLAG = r"\s*[:：]\s*(有|無)(?!・)(?:\s*[（(]([^）)]{0,80})[）)])?"
_MATERIAL_RE = re.compile(r"(?:中間期|中間|四半期)?決算補足説明資料(?:の)?作成の有無" + _FLAG)
_BRIEFING_RE = re.compile(r"(?:中間期|中間|四半期)?決算説明会開催の有無" + _FLAG)


@dataclass(frozen=True)
class CoverFlags:
    has_material: bool | None
    material_note: str | None
    has_briefing: bool | None
    briefing_note: str | None

    @property
    def found_any(self) -> bool:
        return self.has_material is not None or self.has_briefing is not None


@dataclass
class CoverReport:
    lines: list[str] = field(default_factory=list)
    written: int = 0
    skipped: int = 0

    def summary(self, *, dry_run: bool = False) -> str:
        head = "tanshin_covers dry_run" if dry_run else "tanshin_covers"
        return f"{head} written={self.written} skipped={self.skipped}"


@dataclass
class SampleReport:
    lines: list[str] = field(default_factory=list)
    n: int = 0
    errors: int = 0
    material_yes: int = 0
    material_no: int = 0
    material_unknown: int = 0
    briefing_yes: int = 0
    briefing_no: int = 0
    briefing_unknown: int = 0
    tdnet_found: int = 0
    tdnet_listed: int = 0
    tdnet_absent: int = 0
    tdnet_unknown: int = 0

    def summary(self) -> str:
        yes = self.material_yes
        found_pct = _pct(self.tdnet_found, yes)
        mat_pct = _pct(self.material_yes, self.n)
        return (
            f"sample n={self.n} errors={self.errors} "
            f"material_yes={self.material_yes} ({mat_pct}) "
            f"material_no={self.material_no} unknown={self.material_unknown} "
            f"briefing_yes={self.briefing_yes} no={self.briefing_no} unknown={self.briefing_unknown} "
            f"tdnet_found={self.tdnet_found}/{yes} ({found_pct}) "
            f"listed={self.tdnet_listed} absent={self.tdnet_absent} unknown={self.tdnet_unknown}"
        )


@dataclass
class ProbeReport:
    lines: list[str] = field(default_factory=list)
    probed: int = 0
    found: int = 0
    not_found: int = 0
    blocked: int = 0
    error: int = 0
    no_homepage: int = 0

    def summary(self) -> str:
        return (
            f"homepage probed={self.probed} found={self.found} ({_pct(self.found, self.probed)}) "
            f"not_found={self.not_found} blocked={self.blocked} error={self.error} "
            f"no_homepage={self.no_homepage}"
        )


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "-"
    return f"{100.0 * part / whole:.0f}%"


def _compact(text: str | None) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def _cover_body(text: str | None) -> str:
    """空白は詰める。改行は残す（次の行の注記を括弧に巻き込まない）。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[^\S\n]+", "", normalized)


_UNIT_NOTE = re.compile(r"百万円|千円|億円|切捨て|切り捨て|四捨五入|未満")
# 列順で隣の「（氏名）」などを括弧ごと拾う。対象者の注記だけ残す
_AUDIENCE_NOTE = re.compile(r"向け|投資家|アナリスト|機関|証券|電話会議|動画|配信")


def is_cover_tanshin_title(title: str | None) -> bool:
    """表紙を読む決算短信。訂正と修正のお知らせは除く。"""
    text = _compact(title)
    if "決算短信" not in text:
        return False
    return not ("訂正" in text or _AMENDMENT.search(text))


def parse_cover_text(text: str) -> CoverFlags:
    """表紙の見える文字列から有無を取る。『有・無』の併記は読まない。"""
    body = _cover_body(text)
    material = _MATERIAL_RE.search(body)
    briefing = _BRIEFING_RE.search(body)
    return CoverFlags(
        has_material=None if material is None else material.group(1) == "有",
        material_note=_note(material.group(2) if material else None),
        has_briefing=None if briefing is None else briefing.group(1) == "有",
        briefing_note=_note(briefing.group(2) if briefing else None),
    )


def _note(raw: str | None) -> str | None:
    text = _WS.sub("", raw or "")
    if not text or _UNIT_NOTE.search(text) or _AUDIENCE_NOTE.search(text) is None:
        return None
    return text


class _Visible(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            return
        self.parts.append(data.strip())


def _visible_text(html: str) -> str:
    parser = _Visible()
    parser.feed(html)
    parser.close()
    return " ".join(parser.parts)


def _is_pdf_html(html: str) -> bool:
    head = html[:8000].lower()
    return "<svg" in head and "ix:nonnumeric" not in head


def cover_text_from_html(html: str) -> str:
    """PDF の変換 HTML は1ページ目だけ。iXBRL サマリーは見える文字全部。"""
    if not html:
        return ""
    if _is_pdf_html(html):
        pages = read_pages(html)
        if not pages:
            return ""
        return "\n".join(line.text for line in pages[0].lines)
    return _visible_text(html)


def _pdf_at(doc: TdnetPdfDocument) -> datetime:
    when = datetime.combine(doc.disclosed_date, _NO_TIME.time()) if doc.disclosed_date else _NO_TIME
    clock = (doc.disclosed_time or "").strip()
    if len(clock) >= 5 and clock[2] == ":":
        try:
            hh, mm = int(clock[:2]), int(clock[3:5])
            return when.replace(hour=hh, minute=mm)
        except ValueError:
            return when
    return when


@dataclass(frozen=True)
class _Candidate:
    at: datetime
    disclosure_no: str
    title: str
    source: str
    html: str


def _latest_xbrl(db: Session, code: str) -> _Candidate | None:
    filings = db.execute(
        select(XbrlFiling)
        .where(XbrlFiling.code == code, XbrlFiling.source == "tdnet")
        .order_by(XbrlFiling.submitted_at.desc())
        .limit(40)
    ).scalars()
    for filing in filings:
        if filing.is_amendment or not is_cover_tanshin_title(filing.title):
            continue
        doc = db.execute(
            select(XbrlDocument)
            .where(XbrlDocument.filing_id == filing.id, XbrlDocument.part == "summary")
            .order_by(XbrlDocument.seq)
            .limit(1)
        ).scalar_one_or_none()
        if doc is None:
            continue
        return _Candidate(
            at=filing.submitted_at,
            disclosure_no=filing.doc_id,
            title=filing.title or "",
            source=SOURCE_XBRL,
            html=load_html(doc),
        )
    return None


def _latest_pdf(db: Session, code: str) -> _Candidate | None:
    docs = db.execute(
        select(TdnetPdfDocument)
        .where(TdnetPdfDocument.code == code, TdnetPdfDocument.status == "ok")
        .order_by(TdnetPdfDocument.disclosed_date.desc(), TdnetPdfDocument.disclosed_time.desc())
        .limit(40)
    ).scalars()
    for doc in docs:
        if not doc.html_gz or not is_cover_tanshin_title(doc.title):
            continue
        return _Candidate(
            at=_pdf_at(doc),
            disclosure_no=doc.disclosure_no,
            title=doc.title or "",
            source=SOURCE_PDF,
            html=load_html_pdf(doc),
        )
    return None


def load_html_pdf(doc: TdnetPdfDocument) -> str:
    raw = gzip.decompress(doc.html_gz or b"")
    return raw.decode("utf-8", "replace")


def _pick(candidates: list[_Candidate]) -> tuple[_Candidate, CoverFlags] | None:
    """提出が新しい日の表紙。その日に複数あれば、有無が読めたものを優先する。"""
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: item.at, reverse=True)
    day = ordered[0].at.date()
    same_day = [item for item in ordered if item.at.date() == day]
    parsed = [(item, parse_cover_text(cover_text_from_html(item.html))) for item in same_day]
    for item, flags in parsed:
        if flags.found_any:
            return item, flags
    return parsed[0]


def _line(code: str, item: _Candidate, flags: CoverFlags) -> str:
    material = _yn(flags.has_material)
    briefing = _yn(flags.has_briefing)
    note = f" ({flags.briefing_note})" if flags.briefing_note else ""
    return f"{code} {item.source} {item.disclosure_no} material={material} briefing={briefing}{note}"


def _yn(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def save_cover(
    db: Session,
    code: str,
    item: _Candidate,
    flags: CoverFlags,
    *,
    dry_run: bool = False,
) -> None:
    """表紙を書く。書類が変わったら TDnet / HP の判定は消す。"""
    if dry_run:
        return
    row = db.get(TanshinCover, code)
    changed = row is None or row.disclosure_no != item.disclosure_no
    if row is None:
        row = TanshinCover(code=code, disclosure_no=item.disclosure_no, source=item.source, checked_at=now_jst())
        db.add(row)
    row.disclosure_no = item.disclosure_no
    row.disclosed_at = item.at if item.at != _NO_TIME else None
    row.title = item.title
    row.source = item.source
    row.has_material = flags.has_material
    row.material_note = flags.material_note
    row.has_briefing = flags.has_briefing
    row.briefing_note = flags.briefing_note
    row.checked_at = now_jst()
    if changed:
        row.tdnet_status = None
        row.tdnet_title = None
        row.tdnet_url = None
        row.tdnet_pubdate = None
        row.hp_status = None
        row.hp_url = None
        row.hp_detail = None
    db.commit()


def save_tdnet(db: Session, code: str, judgement: TdnetJudgement, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    row = db.get(TanshinCover, code)
    if row is None:
        return
    row.tdnet_status = judgement.status
    row.tdnet_title = judgement.title
    row.tdnet_url = judgement.url
    row.tdnet_pubdate = judgement.pubdate
    row.checked_at = now_jst()
    db.commit()


def save_homepage(db: Session, code: str, result: ProbeResult, *, dry_run: bool = False) -> None:
    if dry_run:
        return
    row = db.get(TanshinCover, code)
    if row is None:
        return
    row.hp_status = result.status
    row.hp_url = result.url
    row.hp_detail = result.detail[:500] if result.detail else None
    row.checked_at = now_jst()
    db.commit()


def sync_covers(
    db: Session,
    codes: list[str] | None,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> CoverReport:
    """DB にある最新短信の表紙を読む。codes が無しなら新しい順に limit 社。"""
    report = CoverReport()
    chosen = list(codes) if codes is not None else _recent_codes(db, limit or 0)
    for code in chosen:
        try:
            ordinary = ordinary_code(code)
        except ValueError:
            report.skipped += 1
            report.lines.append(f"skip {code} bad code")
            continue
        picked = _pick([item for item in (_latest_xbrl(db, ordinary), _latest_pdf(db, ordinary)) if item])
        if picked is None:
            report.skipped += 1
            report.lines.append(f"skip {ordinary} no tanshin")
            continue
        item, flags = picked
        report.lines.append(_line(ordinary, item, flags))
        save_cover(db, ordinary, item, flags, dry_run=dry_run)
        report.written += 1
    return report


def _recent_codes(db: Session, limit: int) -> list[str]:
    if limit < 1:
        return []
    found: list[str] = []
    seen: set[str] = set()
    filings = db.execute(
        select(XbrlFiling.code, XbrlFiling.title, XbrlFiling.is_amendment)
        .where(XbrlFiling.source == "tdnet")
        .order_by(XbrlFiling.submitted_at.desc())
        .limit(limit * 8)
    ).all()
    for code, title, amended in filings:
        if amended or not is_cover_tanshin_title(title) or code in seen:
            continue
        seen.add(code)
        found.append(code)
        if len(found) >= limit:
            return found
    docs = db.execute(
        select(TdnetPdfDocument.code, TdnetPdfDocument.title)
        .where(TdnetPdfDocument.status == "ok")
        .order_by(TdnetPdfDocument.disclosed_date.desc())
        .limit(limit * 8)
    ).all()
    for code, title in docs:
        if not is_cover_tanshin_title(title) or code in seen:
            continue
        seen.add(code)
        found.append(code)
        if len(found) >= limit:
            break
    return found


def select_sample_rows(rows: list[dict], *, limit: int, today: date) -> list[dict]:
    """会社ごとに最新の短信。説明資料の窓（7日）が閉じた会社を先に取る。"""
    best: dict[str, dict] = {}
    for row in rows:
        if not is_cover_tanshin_title(row.get("title")):
            continue
        try:
            code = ordinary_code(str(row.get("company_code") or ""))
        except ValueError:
            continue
        current = best.get(code)
        pub = str(row.get("pubdate") or "")
        if current is None or pub > str(current.get("pubdate") or ""):
            best[code] = {**row, "code": code}
    items = sorted(best.values(), key=lambda item: str(item.get("pubdate") or ""), reverse=True)
    closed: list[dict] = []
    still_open: list[dict] = []
    for item in items:
        on = pub_date(item.get("pubdate"))
        if on is not None and on + timedelta(days=MATERIAL_WINDOW_DAYS) <= today:
            closed.append(item)
        else:
            still_open.append(item)
    chosen = closed[:limit]
    if len(chosen) < limit:
        have = {item["code"] for item in chosen}
        for item in still_open:
            if item["code"] in have:
                continue
            chosen.append(item)
            if len(chosen) >= limit:
                break
    return chosen


def _count_flags(report: SampleReport, flags: CoverFlags) -> None:
    if flags.has_material is True:
        report.material_yes += 1
    elif flags.has_material is False:
        report.material_no += 1
    else:
        report.material_unknown += 1
    if flags.has_briefing is True:
        report.briefing_yes += 1
    elif flags.has_briefing is False:
        report.briefing_no += 1
    else:
        report.briefing_unknown += 1


def _count_tdnet(report: SampleReport, status: str) -> None:
    if status == "found":
        report.tdnet_found += 1
    elif status == "listed":
        report.tdnet_listed += 1
    elif status == "unknown":
        report.tdnet_unknown += 1
    else:
        report.tdnet_absent += 1


def sample_from_tdnet(
    db: Session,
    *,
    days: int,
    limit: int,
    today: date,
    get,
    pdf_text,
    confirm,
    sleep_sec: float = 0.4,
    dry_run: bool = False,
) -> SampleReport:
    """直近 days 日の開示一覧から、最大 limit 社の短信 PDF 1ページ目を読む。

    get(url)->bytes は一覧用。pdf_text(bytes)->str は1ページ目。
    confirm(url)->bool は説明資料 PDF の先頭が %PDF- か。失敗は listed のまま。
    """
    if days < 1 or days > SAMPLE_DAYS_MAX:
        raise ValueError(f"days must be 1..{SAMPLE_DAYS_MAX}")
    if limit < 1 or limit > SAMPLE_LIMIT_MAX:
        raise ValueError(f"limit must be 1..{SAMPLE_LIMIT_MAX}")
    report = SampleReport()
    start = today - timedelta(days=days - 1)
    rows, missing = fetch_tdnet_range(start, today, get, sleep_sec)
    chosen = select_sample_rows(rows, limit=limit, today=today)
    for row in chosen:
        code = row["code"]
        url = row.get("document_url") or ""
        print(f"fetch {code} {url}", flush=True)
        try:
            text = pdf_text(get(url))
        except (OSError, ValueError, RuntimeError) as exc:
            report.errors += 1
            report.lines.append(f"error {code} pdf {exc}")
            continue
        flags = parse_cover_text(text)
        on = pub_date(row.get("pubdate")) or today
        item = _Candidate(
            at=_pub_datetime(row.get("pubdate"), on),
            disclosure_no=_stem(url) or code,
            title=row.get("title") or "",
            source=SOURCE_LIVE,
            html="",
        )
        report.lines.append(_line(code, item, flags))
        save_cover(db, code, item, flags, dry_run=dry_run)
        report.n += 1
        _count_flags(report, flags)
        if flags.has_material is not True:
            continue
        judgement = judge_tdnet_rows(rows, code, on, missing_days=missing, today=today)
        if judgement.status == "listed" and judgement.url:
            try:
                ok = bool(confirm(judgement.url))
            except (OSError, ValueError, RuntimeError) as exc:
                ok = False
                judgement = TdnetJudgement(
                    "listed", judgement.title, judgement.url, judgement.pubdate, str(exc)[:200],
                )
            if ok:
                judgement = TdnetJudgement("found", judgement.title, judgement.url, judgement.pubdate)
        report.lines.append(
            f"  tdnet {code} {judgement.status} {judgement.title or ''} {judgement.url or ''}".rstrip()
        )
        save_tdnet(db, code, judgement, dry_run=dry_run)
        _count_tdnet(report, judgement.status)
    return report


def _pub_datetime(pubdate: str | None, on: date) -> datetime:
    text = (pubdate or "").strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.combine(on, datetime.min.time())


def _stem(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    if "." in name:
        name = name.split(".", 1)[0]
    return name[:32]


def homepage_targets(db: Session, limit: int) -> list[tuple[str, str | None]]:
    """説明資料が有で、TDnet に無い会社。(code, homepage url or None)。"""
    if limit < 1:
        return []
    rows = db.execute(
        select(TanshinCover)
        .where(TanshinCover.has_material.is_(True), TanshinCover.tdnet_status == "absent")
        .order_by(TanshinCover.disclosed_at.desc())
        .limit(limit)
    ).scalars()
    out: list[tuple[str, str | None]] = []
    for row in rows:
        page = db.get(Homepage, row.code)
        out.append((row.code, page.url if page is not None else None))
    return out


def apply_probe(db: Session, code: str, result: ProbeResult, *, dry_run: bool = False) -> None:
    if result.status == "no_homepage":
        if dry_run:
            return
        row = db.get(TanshinCover, code)
        if row is None:
            return
        row.hp_status = "no_homepage"
        row.hp_url = None
        row.hp_detail = "no homepages row"
        row.checked_at = now_jst()
        db.commit()
        return
    save_homepage(db, code, result, dry_run=dry_run)


def probe_saved(
    db: Session,
    opener,
    pacer,
    *,
    limit: int,
    dry_run: bool = False,
) -> ProbeReport:
    """TDnet で説明資料が取れなかった会社を、最大 limit 社だけホームページから試す。"""
    if limit < 1 or limit > PROBE_LIMIT_MAX:
        raise ValueError(f"probe limit must be 1..{PROBE_LIMIT_MAX}")
    report = ProbeReport()
    for code, url in homepage_targets(db, limit):
        if not url:
            report.no_homepage += 1
            report.lines.append(f"{code} no_homepage")
            apply_probe(db, code, ProbeResult("no_homepage"), dry_run=dry_run)
            continue
        if dry_run:
            report.lines.append(f"{code} dry_run {url}")
            report.probed += 1
            continue
        result = probe_homepage(url, opener, pacer)
        report.lines.append(f"{code} {result.status} pages={result.pages} {result.url or ''} {result.detail}".rstrip())
        apply_probe(db, code, result, dry_run=False)
        report.probed += 1
        if result.status == "found":
            report.found += 1
        elif result.status == "blocked":
            report.blocked += 1
        elif result.status == "error":
            report.error += 1
        else:
            report.not_found += 1
    return report


def looks_like_pdf(body: bytes) -> bool:
    return body[:5] == b"%PDF-"

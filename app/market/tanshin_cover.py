"""決算短信の表紙から、説明資料・説明会の有無を tanshin_covers に残す（KAB-325）。

PDF は変換 HTML の1ページ目だけ。XBRL はサマリー iXBRL（表紙）。
訂正と「決算短信の修正について」は飛ばす。1短信1行。ジョブは init_db しない。
"""
from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.xbrl.documents import load_html
from app.xbrl.pdf import read_pages

from ..models import Stock, TanshinCover, TdnetPdfDocument, XbrlDocument, XbrlFiling
from .homepages import now_jst, ordinary_code

SOURCE_XBRL = "xbrl_summary"
SOURCE_PDF = "tdnet_pdf"
_NO_TIME = datetime(1, 1, 1)  # noqa: DTZ001
# J-Quants ProdCat。011 内国株券、021 外国株券、013 REIT、014 ETF、023 外国ETF/ETN
_ORDINARY = frozenset({"011", "021"})
_REIT = "013"
_ETF = frozenset({"014", "023"})

_WS = re.compile(r"\s+")
_AMENDMENT = re.compile(r"決算短信.*(?:の修正について|の一部修正)")
# pdftotext はラベルと「：有」を改行で割ることがある
_FLAG = r"\s*[:：]\s*(有|無)(?!・)(?:\s*[（(]([^）)]{0,80})[）)])?"
_MATERIAL_RE = re.compile(r"(?:中間期|中間|四半期)?決算補足説明資料(?:の)?作成の有無" + _FLAG)
_BRIEFING_RE = re.compile(r"(?:中間期|中間|四半期)?決算説明会開催の有無" + _FLAG)
_UNIT_NOTE = re.compile(r"百万円|千円|億円|切捨て|切り捨て|四捨五入|未満")
# 列順で隣の「（氏名）」などを括弧ごと拾う。対象者の注記だけ残す
_AUDIENCE_NOTE = re.compile(r"向け|投資家|アナリスト|機関|証券|電話会議|動画|配信")
_FUND_WORD = ("ファンド", "投資信託", "ＥＴＦ", "ＥＴＮ", "ETF", "ETN")


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


def _compact(text: str | None) -> str:
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def _cover_body(text: str | None) -> str:
    """空白は詰める。改行は残す（次の行の注記を括弧に巻き込まない）。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[^\S\n]+", "", normalized)


def _fundish(name: str | None, title: str | None = None) -> bool:
    text = unicodedata.normalize("NFKC", f"{name or ''} {title or ''}")
    return any(word in text for word in _FUND_WORD)


def is_cover_target(category: str | None, name: str | None, title: str | None = None) -> bool:
    """普通株と REIT。ETF・ETN・ファンドは除く。マスタが無いときは題名で判断する。"""
    cat = (category or "").strip()
    if cat in _ETF:
        return False
    if cat == _REIT:
        folded = unicodedata.normalize("NFKC", name or "")
        return "ファンド" not in folded and "インフラ" not in folded
    if cat in _ORDINARY:
        return True
    if cat:
        return False
    return not _fundish(name, title)


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
    heads = db.execute(
        select(TdnetPdfDocument.disclosure_no, TdnetPdfDocument.title)
        .where(
            TdnetPdfDocument.code == code,
            TdnetPdfDocument.status == "ok",
            TdnetPdfDocument.html_gz.is_not(None),
        )
        .order_by(TdnetPdfDocument.disclosed_date.desc(), TdnetPdfDocument.disclosed_time.desc())
        .limit(40)
    ).all()
    for disclosure_no, title in heads:
        if not is_cover_tanshin_title(title):
            continue
        doc = db.get(TdnetPdfDocument, disclosure_no)
        if doc is None or not doc.html_gz:
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


def _drop_same_day(db: Session, code: str, item: _Candidate) -> None:
    """同じ発表日の別書類は残さない。別の日の行は履歴として残す。"""
    if item.at == _NO_TIME:
        return
    day = item.at.date()
    rows = db.execute(select(TanshinCover).where(TanshinCover.code == code)).scalars()
    for row in rows:
        if row.disclosure_no == item.disclosure_no or row.disclosed_at is None:
            continue
        if row.disclosed_at.date() == day:
            db.delete(row)


def save_cover(
    db: Session,
    code: str,
    item: _Candidate,
    flags: CoverFlags,
    *,
    dry_run: bool = False,
) -> None:
    """この短信の行を書く。別の短信の行は消さない。"""
    if dry_run:
        return
    row = db.get(TanshinCover, item.disclosure_no)
    if row is None:
        row = TanshinCover(
            disclosure_no=item.disclosure_no,
            code=code,
            source=item.source,
            checked_at=now_jst(),
        )
        db.add(row)
    row.code = code
    row.disclosed_at = item.at if item.at != _NO_TIME else None
    row.title = item.title
    row.source = item.source
    row.has_material = flags.has_material
    row.material_note = flags.material_note
    row.has_briefing = flags.has_briefing
    row.briefing_note = flags.briefing_note
    row.checked_at = now_jst()
    _drop_same_day(db, code, item)
    db.commit()


def _target(db: Session, code: str, title: str | None) -> bool:
    stock = db.get(Stock, code)
    if stock is None:
        return is_cover_target(None, None, title)
    return is_cover_target(stock.product_category, stock.name, title)


def sync_covers(
    db: Session,
    codes: list[str] | None,
    *,
    limit: int | None = None,
    since: datetime | None = None,
    dry_run: bool = False,
) -> CoverReport:
    """DB にある最新短信の表紙を読む。codes が無しなら since 以降に短信を出した会社、since も無しなら新しい順に limit 社。"""
    report = CoverReport()
    if codes is not None:
        chosen = list(codes)
    elif since is not None:
        chosen = _codes_since(db, since)
    else:
        chosen = _recent_codes(db, limit or 0)
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
        if not _target(db, ordinary, item.title):
            report.skipped += 1
            report.lines.append(f"skip {ordinary} not ordinary")
            continue
        report.lines.append(_line(ordinary, item, flags))
        save_cover(db, ordinary, item, flags, dry_run=dry_run)
        report.written += 1
    return report


def _codes_since(db: Session, since: datetime) -> list[str]:
    seen: set[str] = set()
    filings = db.execute(
        select(XbrlFiling.code, XbrlFiling.title, XbrlFiling.is_amendment)
        .where(XbrlFiling.source == "tdnet", XbrlFiling.submitted_at >= since)
    ).all()
    for code, title, amended in filings:
        if not amended and is_cover_tanshin_title(title):
            seen.add(code)
    docs = db.execute(
        select(TdnetPdfDocument.code, TdnetPdfDocument.title)
        .where(TdnetPdfDocument.status == "ok", TdnetPdfDocument.disclosed_date >= since.date())
    ).all()
    for code, title in docs:
        if is_cover_tanshin_title(title):
            seen.add(code)
    return sorted(seen)


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
        if amended or not is_cover_tanshin_title(title) or code in seen or not _target(db, code, title):
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
        if not is_cover_tanshin_title(title) or code in seen or not _target(db, code, title):
            continue
        seen.add(code)
        found.append(code)
        if len(found) >= limit:
            break
    return found

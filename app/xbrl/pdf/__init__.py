"""TDnet 短信 PDF（tdnet_pdf_documents の HTML）を XBRL の生の層に入れる（KAB-173）。

ZIP と同じ ParsedPackage を作り、同じ store_package / normalize_filing を通す。
印字の行名は pdf-* 名前空間の要素名。標準科目への対応は config/xbrl/mappings/tdnet_pdf.yaml。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.config import PDF_OTHER_STANDARD_PATH, PDF_STANDARD_FIXES_PATH
from app.models import Statement, Stock, TdnetPdfDocument, XbrlFiling, XbrlFinancial
from app.xbrl.context import classify_title
from app.xbrl.normalize import is_normalizable_context, normalize_filing
from app.xbrl.parser import ParsedPackage
from app.xbrl.pdf._layout import (
    Page,
    PdfLayoutError,
    format_garbled_pages,
    format_textless_pages,
    garbled_page_indexes,
    outlined_page,
    read_pages,
    textless_page_indexes,
)
from app.xbrl.pdf._tanshin import (
    DocKind,
    PdfDisclosure,
    Unsupported,
    drop_pre_correction,
    is_amendment_notice,
    label_key,
    package,
    parse_disclosure_no,
)
from app.xbrl.store import (
    delete_filing,
    delete_filing_payload,
    inherit_accounting_standard_from_prior_filings,
    store_package,
)

__all__ = [
    "READER_VERSION",
    "DocKind",
    "PdfDisclosure",
    "PdfLayoutError",
    "SyncOutcome",
    "Unsupported",
    "format_garbled_pages",
    "format_textless_pages",
    "garbled_page_indexes",
    "label_key",
    "outlined_page",
    "read_pages",
    "read_tdnet_pdf",
    "sync_pdf_filing",
    "sync_pdf_filings",
    "textless_page_indexes",
]

SOURCE = "tdnet_pdf"
# レイアウト・行名・label_key の結果が変わったら上げる。上げると全 PDF filing の指紋がずれる。
READER_VERSION = 27
_STD_DOC = re.compile(
    r"^(?:1Q|2Q|3Q|FY)FinancialStatements_(Consolidated|NonConsolidated)_(IFRS|JP)$"
)


# database is locked の回を区切りでやり直すまでの待ち（秒）。KAB-435
LOCK_RETRY_WAIT = 5.0


class SyncOutcome(str, Enum):
    PARSED = "parsed"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    RETIRED = "retired"
    ABSENT = "absent"


def fingerprint(html_sha256: str, standard: str | None = None) -> str:
    """xbrl_filings.raw_sha256。HTML と READER_VERSION と会計基準を 1 つの値にする。"""
    token = standard or ""
    return hashlib.sha256(f"{html_sha256}:{READER_VERSION}:{token}".encode()).hexdigest()


def inherit_accounting_standard_from_statements(
    db: Session,
    *,
    code: str,
    disclosed_date: date | None,
) -> str | None:
    """修正開示など type_of_document に基準が無いとき、近い通常の短信から取る（KAB-213）。"""
    if disclosed_date is None:
        return None
    best: str | None = None
    best_dist: int | None = None
    for st in db.query(Statement).filter(Statement.code == code):
        matched = _STD_DOC.fullmatch(st.type_of_document or "")
        if matched is None or st.disclosed_date is None:
            continue
        dist = abs((st.disclosed_date - disclosed_date).days)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = fixed_standard(st.disclosure_no, st.code, "IFRS" if matched.group(2) == "IFRS" else "JPGAAP")
    return best


def inherit_consolidated_from_statements(
    db: Session,
    *,
    code: str,
    disclosed_date: date | None,
) -> bool | None:
    """修正開示など type_of_document に連結/非連結が無いとき、近い通常の短信から取る（KAB-247）。

    会計基準と同じく日付が近い 1 件。連結の短信がある会社を、古い非連結だけで単体にしない。
    通常の短信が無ければ None（呼び出し側は従来の既定＝連結のまま）。
    """
    if disclosed_date is None:
        return None
    best: bool | None = None
    best_dist: int | None = None
    for st in db.query(Statement).filter(Statement.code == code):
        matched = _STD_DOC.fullmatch(st.type_of_document or "")
        if matched is None or st.disclosed_date is None:
            continue
        dist = abs((st.disclosed_date - disclosed_date).days)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = matched.group(1) == "Consolidated"
    return best


def read_tdnet_pdf(html: str, disclosure: PdfDisclosure) -> ParsedPackage:
    """HTML（svg1 でも svg2 でも）→ TSE サマリーと同じメンバーの contexts と facts。

    金額は円（百万円は 10^6 倍）。印字の符号のまま（△ は負）。百分率は pure。
    """
    return _package(read_pages(html), disclosure)


def _package(pages: list[Page], disclosure: PdfDisclosure) -> ParsedPackage:
    return package(drop_pre_correction(pages), disclosure, bs_pages=drop_pre_correction(pages, bs=True))


@dataclass(frozen=True)
class _Build:
    disclosure: PdfDisclosure
    doc: TdnetPdfDocument
    fingerprint: str


@dataclass(frozen=True)
class _Absent:
    reason: Literal[
        "zip", "no-html", "no-statement", "doc-type", "reit", "irregular-period", "no-dates", "flash-results",
        "other-standard",
    ]


@lru_cache(maxsize=1)
def other_standard_disclosures() -> frozenset[str]:
    """元の短信と別の会計基準の財務諸表を追加したお知らせ（config の一覧。規則では決めない）。"""
    raw = json.loads(PDF_OTHER_STANDARD_PATH.read_text(encoding="utf-8"))
    return frozenset(str(item["disclosure_no"]).strip() for item in raw.get("disclosures") or [])


def load_standard_fixes(path: Path | None = None) -> dict[str, tuple[str, str]]:
    src = PDF_STANDARD_FIXES_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    out: dict[str, tuple[str, str]] = {}
    for item in raw.get("fixes") or []:
        no = str(item["disclosure_no"]).strip()
        if no in out:
            raise ValueError(f"duplicate disclosure_no in pdf standard fixes: {no}")
        out[no] = (str(item["jquants"]), str(item["standard"]))
    return out


@lru_cache(maxsize=1)
def standard_fixes() -> dict[str, tuple[str, str]]:
    return load_standard_fixes()


def fixed_standard(disclosure_no: str | None, code: str, standard: str | None) -> str | None:
    recorded = standard_fixes().get(str(disclosure_no or ""))
    if recorded is None or standard == recorded[1]:
        return standard
    if standard != recorded[0]:
        print(
            f"WARNING tdnet_pdf standard-fix-mismatch disclosure_no={disclosure_no} code={code} "
            f"recorded={recorded[0]} jquants={standard or '-'}",
            flush=True,
        )
        return standard
    return recorded[1]


def _warn_standard_candidate(disclosure: PdfDisclosure, title: str | None) -> None:
    printed = classify_title(title or "").get("accounting_standard")
    if printed is None or disclosure.standard is None or printed == disclosure.standard:
        return
    print(
        f"WARNING tdnet_pdf standard-candidate disclosure_no={disclosure.disclosure_no} code={disclosure.code} "
        f"jquants={disclosure.standard} title={printed}",
        flush=True,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _zip_parsed(db: Session, disclosure_no: str) -> bool:
    rows = db.query(XbrlFiling.doc_id).filter_by(source="tdnet", parse_status="parsed")
    return any(doc_id and doc_id[-14:] == disclosure_no for (doc_id,) in rows)


def _plan(db: Session, disclosure_no: str, zip_parsed: bool) -> _Build | _Absent:
    if zip_parsed:
        return _Absent("zip")
    doc = db.get(TdnetPdfDocument, disclosure_no)
    if doc is None or doc.status != "ok" or not doc.html_gz or not doc.sha256:
        return _Absent("no-html")
    statement = db.get(Statement, disclosure_no)
    if statement is None:
        return _Absent("no-statement")
    if doc.title and "速報値" in doc.title:
        return _Absent("flash-results")
    if disclosure_no in other_standard_disclosures():
        return _Absent("other-standard")
    parsed = PdfDisclosure.parse(statement)
    if isinstance(parsed, Unsupported):
        return _Absent(parsed.reason)
    if parsed.kind is DocKind.EARNINGS:
        parsed = replace(parsed, standard=fixed_standard(disclosure_no, parsed.code, parsed.standard))
        _warn_standard_candidate(parsed, doc.title)
    if parsed.standard is None:
        inherited = inherit_accounting_standard_from_statements(
            db, code=parsed.code, disclosed_date=statement.disclosed_date,
        )
        if inherited:
            parsed = replace(parsed, standard=inherited)
    if parsed.kind is DocKind.FORECAST_REVISION:
        inherited_cons = inherit_consolidated_from_statements(
            db, code=parsed.code, disclosed_date=statement.disclosed_date,
        )
        if inherited_cons is not None:
            parsed = replace(parsed, consolidated=inherited_cons)
    return _Build(parsed, doc, fingerprint(doc.sha256, parsed.standard))


def _filing(db: Session, disclosure_no: str) -> XbrlFiling | None:
    return db.query(XbrlFiling).filter_by(source=SOURCE, doc_id=disclosure_no).one_or_none()


def _upsert(db: Session, filing: XbrlFiling | None, disclosure: PdfDisclosure, fp: str) -> XbrlFiling:
    meta = disclosure.filing_meta()
    if filing is None:
        filing = XbrlFiling(
            code=disclosure.code,
            source=SOURCE,
            doc_id=disclosure.disclosure_no,
            raw_path=f"tdnet_pdf:{disclosure.disclosure_no}",
            submitted_at=disclosure.submitted_at,
            created_at=_now(),
            doc_type=meta["doc_type"],
            period_type=meta["period_type"],
            is_amendment=False,
        )
        db.add(filing)
    filing.code = disclosure.code
    filing.title = disclosure.title
    filing.raw_path = f"tdnet_pdf:{disclosure.disclosure_no}"
    filing.raw_sha256 = fp
    filing.submitted_at = disclosure.submitted_at
    filing.doc_type = meta["doc_type"]
    filing.period_type = meta["period_type"]
    filing.is_amendment = False
    filing.accounting_standard = meta["accounting_standard"]
    filing.fiscal_year_end = disclosure.anchor_end
    filing.parse_status = "pending"
    filing.parse_error = None
    db.flush()
    return filing


def _kept(pkg: ParsedPackage) -> ParsedPackage:
    by_ref = {ctx.context_ref: ctx for ctx in pkg.contexts}
    facts = [
        fact for fact in pkg.facts
        if (ctx := by_ref.get(fact.context_ref)) is not None and is_normalizable_context(ctx)
    ]
    used = {fact.context_ref for fact in facts}
    contexts = [ctx for ctx in pkg.contexts if ctx.context_ref in used]
    return ParsedPackage(
        contexts=contexts,
        facts=facts,
        labels=pkg.labels,
        ixbrl_files=pkg.ixbrl_files,
        fiscal_year_end=pkg.fiscal_year_end,
    )


def _is_locked(exc: Exception) -> bool:
    return "database is locked" in str(exc)


def sync_pdf_filing(
    db: Session,
    disclosure_no: str,
    *,
    reparse: bool = False,
    ignore_zip: bool = False,
) -> SyncOutcome:
    """1 件を今あるべき状態へ寄せる。commit は 1 回。繰り返しても、途中で落ちても同じ終わりになる。

    ``ignore_zip`` は ZIP があっても PDF 側を残す。ZIP との突き合わせ用。本番ジョブは渡さない。
    """
    number = parse_disclosure_no(disclosure_no)
    zip_parsed = False if ignore_zip else _zip_parsed(db, number)
    return _sync(db, number, zip_parsed, reparse=reparse)


def _sync(db: Session, disclosure_no: str, zip_parsed: bool, *, reparse: bool) -> SyncOutcome:
    plan = _plan(db, disclosure_no, zip_parsed)
    filing = _filing(db, disclosure_no)
    if isinstance(plan, _Absent):
        if filing is None:
            return SyncOutcome.ABSENT
        delete_filing(db, filing)
        db.commit()
        return SyncOutcome.RETIRED
    if filing is None and db.get(Stock, plan.disclosure.code) is None:
        return SyncOutcome.ABSENT
    fp = plan.fingerprint
    if (
        filing is not None
        and filing.raw_sha256 == fp
        and filing.parse_status in ("parsed", "failed")
        and not reparse
    ):
        return SyncOutcome.UNCHANGED
    filing = _upsert(db, filing, plan.disclosure, fp)
    delete_filing_payload(db, filing.id)
    try:
        html = gzip.decompress(plan.doc.html_gz).decode("utf-8")
        pages = read_pages(html)
        garbled_idx = garbled_page_indexes(pages)
        garbled_note = format_garbled_pages(garbled_idx)
        textless_note = format_textless_pages(textless_page_indexes(pages, exclude=garbled_idx))
        amendment = is_amendment_notice(plan.doc.title, pages)
        pkg = _kept(_package(pages, plan.disclosure))
    except PdfLayoutError as exc:
        filing.parse_status = "failed"
        filing.parse_error = str(exc)[:2000]
        filing.parsed_at = _now()
        db.query(XbrlFinancial).filter_by(filing_id=filing.id).delete(synchronize_session="fetch")
        db.commit()
        return SyncOutcome.FAILED
    except Exception:
        db.rollback()
        raise
    if filing.accounting_standard is None:
        filing.accounting_standard = inherit_accounting_standard_from_prior_filings(
            db,
            code=filing.code,
            doc_id=filing.doc_id,
            submitted_at=filing.submitted_at,
        )
    store_package(db, filing, pkg)
    normalize_filing(db, filing)
    filing.is_amendment = amendment
    notes = [note for note in (garbled_note, textless_note) if note]
    if notes:
        filing.parse_error = "; ".join(notes)
    db.commit()
    return SyncOutcome.PARSED


def _sync_counted(db: Session, number: str, zip_parsed: bool, counts: Counter, *, reparse: bool) -> SyncOutcome:
    outcome = _sync(db, number, zip_parsed, reparse=reparse)
    counts[outcome] += 1
    if outcome is SyncOutcome.PARSED:
        filing = _filing(db, number)
        err = filing.parse_error or "" if filing is not None else ""
        if err.startswith("garbled"):
            counts["garbled"] += 1
        if "textless" in err:
            counts["textless"] += 1
    return outcome


def _sync_failed(db: Session, number: str, counts: Counter, exc: Exception) -> None:
    db.rollback()
    counts[SyncOutcome.FAILED] += 1
    print(f"pdf failed disclosure_no={number} err={exc}", flush=True)


def sync_pdf_filings(
    db: Session,
    *,
    codes: list[str] | None = None,
    reparse: bool = False,
    limit: int = 2000,
    batch: int | None = None,
    retry_wait: float = LOCK_RETRY_WAIT,
    changed: set[str] | None = None,
) -> Counter:
    """ok の HTML か、既存の PDF filing がある開示。新しい順。remaining は今回見なかった件数。

    ``batch`` を渡すと N 件ごとに区切り、区切りごとに累計を 1 行出す（KAB-435）。database is locked で
    落ちた回は区切りの終わりに ``retry_wait`` 秒待って 1 回やり直し、それでも落ちた回だけ failed に数える。
    """
    docs = db.query(
        TdnetPdfDocument.disclosure_no, TdnetPdfDocument.disclosed_date, TdnetPdfDocument.code,
    ).filter(TdnetPdfDocument.status == "ok")
    filings = db.query(XbrlFiling.doc_id, XbrlFiling.submitted_at, XbrlFiling.code).filter_by(source=SOURCE)
    if codes:
        docs = docs.filter(TdnetPdfDocument.code.in_(codes))
        filings = filings.filter(XbrlFiling.code.in_(codes))
    rank: dict[str, date] = {}
    code_of: dict[str, str] = {}
    for disclosure_no, disclosed_date, code in docs:
        rank[disclosure_no] = disclosed_date or date.min
        code_of[disclosure_no] = code
    for doc_id, submitted_at, code in filings:
        rank.setdefault(
            doc_id,
            submitted_at.date() if submitted_at else date.min,
        )
        code_of.setdefault(doc_id, code)
    ordered = sorted(rank, key=lambda number: (rank[number], number), reverse=True)
    zip_rows = db.query(XbrlFiling.doc_id).filter_by(source="tdnet", parse_status="parsed")
    zip_set = {doc_id[-14:] for (doc_id,) in zip_rows if doc_id and len(doc_id) >= 14}
    counts: Counter = Counter()

    def run(number: str) -> None:
        outcome = _sync_counted(db, number, number in zip_set, counts, reparse=reparse)
        if changed is not None and outcome not in (SyncOutcome.UNCHANGED, SyncOutcome.ABSENT) and number in code_of:
            changed.add(code_of[number])

    targets = ordered[:limit]
    step = batch if batch and batch > 0 else len(targets) or 1
    for start in range(0, len(targets), step):
        chunk = targets[start:start + step]
        locked: list[str] = []
        for number in chunk:
            try:
                run(number)
            except Exception as exc:
                if batch and _is_locked(exc):
                    db.rollback()
                    locked.append(number)
                    continue
                _sync_failed(db, number, counts, exc)
        if locked:
            print(f"pdf locked retry={len(locked)} wait={retry_wait:g}s", flush=True)
            time.sleep(retry_wait)
            for number in locked:
                try:
                    run(number)
                except Exception as exc:
                    _sync_failed(db, number, counts, exc)
        if batch:
            done = start + len(chunk)
            parts = [f"{outcome.value}={counts[outcome]}" for outcome in SyncOutcome]
            parts.append(f"garbled={counts['garbled']}")
            parts.append(f"textless={counts['textless']}")
            parts.append(f"left={len(targets) - done}")
            print(f"tdnet_pdf_financials batch done={done}/{len(targets)} " + " ".join(parts), flush=True)
    counts["remaining"] = max(0, len(ordered) - limit)
    return counts

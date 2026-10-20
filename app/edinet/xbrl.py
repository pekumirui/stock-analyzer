"""EDINET 有報・半期報の XBRL（type=1）→ xbrl_filings(source='edinet') → xbrl_financials。

設計は docs/DESIGN_edinet.md §4。パース・正規化は TDnet と同じ app/xbrl を使う。
ZIP は保存しない（一時ディレクトリで読んで捨てる。取り直しは EDINET から）。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import XBRL_CACHE_DIR
from app.models import EdinetDocument, XbrlFiling, XbrlFinancial, XbrlSegment
from app.xbrl.context import ParsedContext, classify_title
from app.xbrl.normalize import normalize_filing
from app.xbrl.parser import ParsedFact
from app.xbrl.segments import is_segment_context
from app.xbrl.store import mark_failed, parse_zip_into_filing

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient

SOURCE = "edinet"
ANNUAL = ("120", "130")         # 有価証券報告書 / 訂正
SEMIANNUAL = ("160", "170")     # 半期報告書 / 訂正
QUARTERLY = ("140", "150")      # 四半期報告書 / 訂正（2024-03 まで）
DEFAULT_DOC_TYPES = ANNUAL + SEMIANNUAL + QUARTERLY
AMENDMENTS = ("130", "150", "170")
DEI_KEY = "jpdei_cor"
# 「第138期第2四半期」。classify_title は「第1」を先に探すので 第1xx期 を Q1 にしてしまう
_QUARTER_RE = re.compile(r"第([123])四半期")


def title_meta(doc_type_code: str, description: str | None) -> dict:
    """xbrl_filings の doc_type / period_type / is_amendment。書類種別コードを優先する。

    会計基準はタイトルに無いので None（パース後に DEI の AccountingStandardsDEI から決まる）。
    """
    meta = classify_title(description or "")
    if doc_type_code in ANNUAL:
        meta.update(doc_type="annual", period_type="FY")
    elif doc_type_code in SEMIANNUAL:
        meta.update(doc_type="semiannual", period_type="Q2")
    elif doc_type_code in QUARTERLY:
        m = _QUARTER_RE.search(unicodedata.normalize("NFKC", description or ""))
        meta.update(doc_type="quarterly", period_type=f"Q{m[1]}" if m else None)
    meta["is_amendment"] = doc_type_code in AMENDMENTS
    meta["accounting_standard"] = None
    return meta


def keep_fact(fact: ParsedFact, ctx: ParsedContext | None) -> bool:
    """DB に残す fact。正規化が使う当期・次元なし（連結/単体の軸だけは可）と DEI、
    当期のセグメント軸だけの fact（KAB-77。連結/単体の軸は付いてよい）。

    有報は 1 件 1,000〜2,000 fact あり、全部入れると年 800MB を超える。これで 4 分の 1 ほど。
    """
    if ctx is None:
        return False
    if fact.namespace_key == DEI_KEY:
        return True
    if ctx.relative_period != "current":
        return False
    return ctx.only_consol_dimensions() or is_segment_context(ctx)


def pending_documents(
    db: Session,
    *,
    doc_types: tuple[str, ...] = DEFAULT_DOC_TYPES,
    start: date | None = None,
    end: date | None = None,
    code: str | None = None,
    retry_failed: bool = False,
    reparse: bool = False,
    limit: int | None = None,
) -> list[EdinetDocument]:
    """取り込む書類。上場銘柄（code あり）・XBRL あり・取下げ/不開示でない、元の行だけ。

    新しい日から。cron の --limit で新着を先に取り、残りの過去分は次の回に回す。

    既に parsed / skipped の docID は reparse でなければ飛ばす。failed は retry_failed のときだけ。
    """
    T = EdinetDocument
    q = (select(T)
         .where(T.code.is_not(None), T.doc_type_code.in_(doc_types), T.xbrl_flag.is_(True),
                T.withdrawal_status != "2", T.disclosure_status.in_(("0",)),
                T.doc_info_edit_status != "1")
         .order_by(T.file_date.desc(), T.seq_number))
    if start:
        q = q.where(T.file_date >= start)
    if end:
        q = q.where(T.file_date <= end)
    if code:
        q = q.where(T.code == code)
    docs = db.scalars(q).all()
    status = dict(db.execute(
        select(XbrlFiling.doc_id, XbrlFiling.parse_status).where(XbrlFiling.source == SOURCE)
    ).all())
    out, seen = [], set()
    for d in docs:
        if d.doc_id in seen:
            continue
        seen.add(d.doc_id)
        st = status.get(d.doc_id)
        if st in ("parsed", "skipped") and not reparse:
            continue
        if st == "failed" and not (retry_failed or reparse):
            continue
        out.append(d)
        if limit and len(out) >= limit:
            break
    return out


def withdrawn_filings(db: Session) -> list[XbrlFiling]:
    """取り込み済みで、後から取下げ・不開示になった filing。"""
    T = EdinetDocument
    gone = set(db.scalars(
        select(T.doc_id).where((T.withdrawal_status == "2") | (T.disclosure_status == "2"))
    ))
    if not gone:
        return []
    return db.scalars(
        select(XbrlFiling).where(XbrlFiling.source == SOURCE, XbrlFiling.doc_id.in_(gone),
                                 XbrlFiling.parse_status == "parsed")
    ).all()


def retract(db: Session, filing: XbrlFiling) -> None:
    """取下げ・不開示の filing は正規化層から外し、skipped にする（生 fact も消す）。"""
    from app.xbrl.store import delete_filing_payload

    db.query(XbrlFinancial).filter_by(filing_id=filing.id).delete()
    db.query(XbrlSegment).filter_by(filing_id=filing.id).delete()
    delete_filing_payload(db, filing.id)
    filing.parse_status = "skipped"
    filing.parse_error = "withdrawn"
    db.commit()


def _jst_naive(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def ingest_document(db: Session, client: EdinetClient, doc: EdinetDocument,
                    cache_dir: Path | None = None) -> str:
    """1 書類: ダウンロード → パース → 正規化。戻り値は parse_status か 'error'。"""
    from app.edinet_wrapper import DocumentFileType

    with TemporaryDirectory(prefix="edinet-xbrl-") as tmp:
        zip_path = Path(tmp) / f"{doc.doc_id}.zip"
        try:
            got = client.download(doc.doc_id, DocumentFileType.MAIN)
            zip_path.write_bytes(got.content)
        except Exception as exc:
            print(f"edinet xbrl download-failed doc_id={doc.doc_id} code={doc.code} err={exc}", flush=True)
            return "error"
        sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        try:
            filing = parse_zip_into_filing(
                db,
                zip_path=zip_path,
                code=doc.code,
                doc_id=doc.doc_id,
                title=doc.doc_description or "",
                raw_path=f"edinet:{doc.doc_id}",
                raw_sha256=sha,
                submitted_at=_jst_naive(doc.submit_datetime),
                cache_dir=Path(cache_dir or XBRL_CACHE_DIR),
                source=SOURCE,
                title_meta=title_meta(doc.doc_type_code or "", doc.doc_description),
                keep_fact=keep_fact,
            )
        except Exception as exc:
            # TDnet の parse_manifest_row と同じ。flush 済みの filing を failed で残す
            filing = db.query(XbrlFiling).filter_by(source=SOURCE, doc_id=doc.doc_id).one_or_none()
            if filing is not None:
                mark_failed(db, filing, exc)
            print(f"edinet xbrl failed doc_id={doc.doc_id} code={doc.code} err={exc}", flush=True)
            return "error"
    if filing.parse_status != "parsed":
        print(f"edinet xbrl {filing.parse_status} doc_id={doc.doc_id} code={doc.code} "
              f"err={filing.parse_error}", flush=True)
        return filing.parse_status
    try:
        stats = normalize_filing(db, filing)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"edinet xbrl normalize-failed doc_id={doc.doc_id} code={doc.code} err={exc}", flush=True)
        return "error"
    missing = stats.get("missing_required") or []
    print(f"edinet xbrl parsed doc_id={doc.doc_id} code={doc.code} type={doc.doc_type_code} "
          f"std={filing.accounting_standard} items={stats.get('upserted')} segs={stats.get('segments')}"
          + (f" missing={missing}" if missing else ""), flush=True)
    return "parsed"


def run(db: Session, client: EdinetClient, docs: list[EdinetDocument]) -> dict:
    out = {"parsed": 0, "skipped": 0, "failed": 0, "errors": 0, "retracted": 0}
    for filing in withdrawn_filings(db):
        retract(db, filing)
        print(f"edinet xbrl retracted doc_id={filing.doc_id} code={filing.code}", flush=True)
        out["retracted"] += 1
    for doc in docs:
        st = ingest_document(db, client, doc)
        if st == "parsed":
            out["parsed"] += 1
        elif st == "skipped":
            out["skipped"] += 1
        elif st == "failed":
            out["failed"] += 1
        else:
            out["errors"] += 1
    return out

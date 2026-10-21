"""EDINET 大量保有報告書・変更報告書・訂正（350 / 360）→ large_holding_reports / large_holdings。

設計は docs/DESIGN_edinet.md §5（KAB-74）。中身は書類取得 API type=5（XBRL→CSV）。
入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。ZIP は保存しない。
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.edinet.documents import CodeLookup, now_jst
from app.models import EdinetDocument, LargeHolding, LargeHoldingReport

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient

DOC_TYPES = ("350", "360")       # 大量保有報告書・変更報告書 / 訂正報告書
AMENDMENT = "360"
_HOLDER_RE = re.compile(r"FilerLargeVolumeHolder(\d+)Member$")
# 「該当事項なし」「該当事項はありません。」「第３【共同保有者に関する事項】該当事項なし」
_NONE_TEXT_RE = re.compile(r"(第3【共同保有者に関する事項】)?\s*該当(事項)?(は)?(なし|無し|ありません)[。.]?")
_NIL = ("", "－", "-")


@dataclass
class ParsedReport:
    """CSV 1 枚。facts は (要素のローカル名, 保有者番号) → 値（書類レベルは 0）。最初の値を使う。"""
    facts: dict[tuple[str, int], str] = field(default_factory=dict)
    holders: list[int] = field(default_factory=list)

    def text(self, name: str, holder: int = 0) -> str | None:
        v = self.facts.get((name, holder))
        return None if v is None or v.strip() in _NIL else v.strip()

    def num(self, name: str, holder: int = 0) -> Decimal | None:
        v = self.text(name, holder)
        if v is None:
            return None
        try:
            return Decimal(unicodedata.normalize("NFKC", v).replace(",", ""))
        except InvalidOperation:
            return None

    def int_(self, name: str, holder: int = 0) -> int | None:
        v = self.num(name, holder)
        return None if v is None else int(v)

    def ratio(self, name: str, holder: int = 0) -> float | None:
        v = self.num(name, holder)
        return None if v is None else float(v)

    def day(self, name: str, holder: int = 0) -> date | None:
        v = self.text(name, holder)
        try:
            return date.fromisoformat(unicodedata.normalize("NFKC", v)) if v else None
        except ValueError:
            return None

    def note(self, name: str, holder: int = 0) -> str | None:
        """文章の欄。「該当事項なし」は NULL。"""
        v = self.text(name, holder)
        if v is None or _NONE_TEXT_RE.fullmatch(unicodedata.normalize("NFKC", v).strip()):
            return None
        return v


def parse_csv_zip(content: bytes) -> ParsedReport:
    """type=5 の ZIP（XBRL_TO_CSV/jplvh*.csv。UTF-16・タブ区切り）を読む。"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv") and "jplvh" in n.rsplit("/", 1)[-1]]
        if not names:
            raise ValueError(f"jplvh の CSV が無い: {zf.namelist()}")
        raw = zf.read(names[0])
    out = ParsedReport()
    rows = csv.reader(io.StringIO(raw.decode("utf-16")), delimiter="\t")
    header = next(rows, None)
    if not header or header[0] != "要素ID":
        raise ValueError(f"CSV の見出しが違う: {header}")
    holders = set()
    for r in rows:
        if len(r) < 9:
            continue
        local = r[0].split(":", 1)[-1]
        m = _HOLDER_RE.search(r[2])
        no = int(m[1]) if m else 0
        if no:
            holders.add(no)
        out.facts.setdefault((local, no), r[8])
    out.holders = sorted(holders)
    return out


def sec_code5(raw: str | None) -> str | None:
    """CSV の発行者の証券コード（4 桁。190A など）→ stocks.code と同じ 5 桁。"""
    if not raw:
        return None
    s = unicodedata.normalize("NFKC", raw).strip().upper()
    if len(s) == 4:
        return s + "0"
    return s if len(s) == 5 else None


def report_row(doc: EdinetDocument, rep: ParsedReport, lookup: CodeLookup,
               fetched_at: datetime) -> dict:
    """large_holding_reports の 1 行。合計の欄は保有者が 1 人だと書類レベルに無いので、保有者 1 の値を使う。"""
    def total(name: str, conv):
        v = conv(name)
        if v is None and len(rep.holders) == 1:
            v = conv(name, rep.holders[0])
        return v

    issuer_sec = sec_code5(rep.text("SecurityCodeOfIssuer"))
    amends = rep.text("IdentificationOfDocumentSubjectToAmendmentDEI")
    is_amendment = doc.doc_type_code == AMENDMENT
    if is_amendment and not amends:
        amends = doc.parent_doc_id
    return {
        "doc_id": doc.doc_id,
        "file_date": doc.file_date,
        "submit_datetime": doc.submit_datetime,
        "doc_type_code": doc.doc_type_code,
        "form_code": doc.form_code,
        "title": rep.text("DocumentTitleCoverPage") or doc.doc_description,
        "filer_edinet_code": rep.text("EDINETCodeDEI") or doc.edinet_code,
        "filer_name": rep.text("FilerNameInJapaneseDEI") or doc.filer_name,
        "issuer_edinet_code": doc.issuer_edinet_code,
        "issuer_sec_code": issuer_sec,
        "issuer_name": rep.text("NameOfIssuer"),
        "code": lookup.code_for(issuer_sec, doc.issuer_edinet_code),
        "obligation_date": rep.day("DateWhenFilingRequirementAroseCoverPage"),
        "filing_date": rep.day("FilingDateCoverPage"),
        "reason": rep.note("ReasonForFilingChangeReportCoverPage"),
        "arrangement": rep.text("ArrangementOfFilingCoverPage"),
        "holder_count": rep.int_("TotalNumberOfFilersAndJointHoldersCoverPage"),
        "shares_held": total("TotalNumberOfStocksEtcHeld", rep.int_),
        "outstanding_shares": total("TotalNumberOfOutstandingStocksEtc", rep.int_),
        "holding_ratio": total("HoldingRatioOfShareCertificatesEtc", rep.ratio),
        "holding_ratio_prev": total("HoldingRatioOfShareCertificatesEtcPerLastReport", rep.ratio),
        "joint_holders_text": rep.note("InformationAboutJointHoldersTextBlock"),
        "is_amendment": is_amendment,
        "amends_doc_id": amends if is_amendment else None,
        "original_doc_id": doc.doc_id,   # refresh_versions が付け直す
        "parse_status": "parsed",
        "parse_error": None,
        "fetched_at": fetched_at,
    }


def holder_rows(doc_id: str, rep: ParsedReport) -> list[dict]:
    out = []
    for no in rep.holders:
        out.append({
            "doc_id": doc_id,
            "holder_no": no,
            "edinet_code": rep.text("EDINETCodeDEI", no),
            "name": rep.text("Name", no) or rep.text("FilerNameInJapaneseDEI", no),
            "holder_type": rep.text("IndividualOrCorporation", no),
            "shares_held": rep.int_("TotalNumberOfStocksEtcHeld", no),
            "outstanding_shares": rep.int_("TotalNumberOfOutstandingStocksEtc", no),
            "holding_ratio": rep.ratio("HoldingRatioOfShareCertificatesEtc", no),
            "holding_ratio_prev": rep.ratio("HoldingRatioOfShareCertificatesEtcPerLastReport", no),
            "base_date": rep.day("BaseDate", no),
            "purpose": rep.text("PurposeOfHolding", no),
            "important_proposal": rep.note("ActOfMakingImportantProposalEtc", no),
            "own_funds": rep.int_("AmountOfOwnFund", no),
            "borrowings": rep.int_("TotalAmountOfBorrowings", no),
            "other_funds": rep.int_("TotalAmountFromOtherSources", no),
            "total_funds": rep.int_("TotalAmountOfFundingForAcquisition", no),
        })
    return out


def pending_documents(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    retry_failed: bool = False,
    reparse: bool = False,
    limit: int | None = None,
) -> list[EdinetDocument]:
    """取り込む書類。350 / 360・CSV あり・取下げ/不開示でない元の行。新しい日から。

    発行者が上場していない書類も取る（code は NULL）。parsed は reparse でなければ飛ばす。
    failed は retry_failed のときだけ。
    """
    T = EdinetDocument
    q = (select(T)
         .where(T.doc_type_code.in_(DOC_TYPES), T.csv_flag.is_(True),
                T.withdrawal_status != "2", T.disclosure_status == "0",
                T.doc_info_edit_status != "1")
         .order_by(T.file_date.desc(), T.seq_number))
    if start:
        q = q.where(T.file_date >= start)
    if end:
        q = q.where(T.file_date <= end)
    docs = db.scalars(q).all()
    status = dict(db.execute(select(LargeHoldingReport.doc_id, LargeHoldingReport.parse_status)).all())
    out, seen = [], set()
    for d in docs:
        if d.doc_id in seen:
            continue
        seen.add(d.doc_id)
        st = status.get(d.doc_id)
        if st == "parsed" and not reparse:
            continue
        if st == "failed" and not (retry_failed or reparse):
            continue
        out.append(d)
        if limit and len(out) >= limit:
            break
    return out


def store_report(db: Session, row: dict, holders: list[dict]) -> None:
    def write():
        upsert(db, LargeHoldingReport, [row], commit=False)
        db.execute(delete(LargeHolding).where(LargeHolding.doc_id == row["doc_id"]))
        upsert(db, LargeHolding, holders, commit=False)

    run_sqlite_write(db, write)


def store_failed(db: Session, doc: EdinetDocument, exc: Exception, fetched_at: datetime) -> None:
    """パースできなかった書類。前に parsed だった値が残らないよう、全列を埋め直す。"""
    row = {c.name: None for c in LargeHoldingReport.__table__.columns}
    row.update({
        "doc_id": doc.doc_id, "file_date": doc.file_date, "submit_datetime": doc.submit_datetime,
        "doc_type_code": doc.doc_type_code, "form_code": doc.form_code,
        "title": doc.doc_description, "filer_edinet_code": doc.edinet_code,
        "filer_name": doc.filer_name, "issuer_edinet_code": doc.issuer_edinet_code,
        "is_amendment": doc.doc_type_code == AMENDMENT,
        "amends_doc_id": doc.parent_doc_id if doc.doc_type_code == AMENDMENT else None,
        "original_doc_id": doc.doc_id, "parse_status": "failed",
        "parse_error": f"{type(exc).__name__}: {exc}"[:2000], "fetched_at": fetched_at,
        "is_latest": False, "withdrawn": False,
    })
    store_report(db, row, [])


def ingest_document(db: Session, client: EdinetClient, doc: EdinetDocument,
                    lookup: CodeLookup) -> str:
    """1 書類: CSV をダウンロード → パース → 保存。戻り値は parsed / failed / error（ダウンロード失敗）。"""
    from app.edinet_wrapper import DocumentFileType

    try:
        got = client.download(doc.doc_id, DocumentFileType.CSV)
    except Exception as exc:
        print(f"edinet lvh download-failed doc_id={doc.doc_id} err={exc}", flush=True)
        return "error"
    fetched_at = now_jst()
    try:
        rep = parse_csv_zip(got.content)
        row = report_row(doc, rep, lookup, fetched_at)
        holders = holder_rows(doc.doc_id, rep)
    except Exception as exc:
        store_failed(db, doc, exc, fetched_at)
        print(f"edinet lvh failed doc_id={doc.doc_id} err={exc}", flush=True)
        return "failed"
    store_report(db, row, holders)
    ratio = "" if row["holding_ratio"] is None else f"{row['holding_ratio'] * 100:.2f}%"
    print(f"edinet lvh parsed doc_id={doc.doc_id} type={doc.doc_type_code} code={row['code']} "
          f"holders={len(holders)} ratio={ratio}", flush=True)
    return "parsed"


def _root(doc_id: str, amends: dict[str, str | None]) -> str:
    seen = {doc_id}
    cur = doc_id
    while amends.get(cur) and amends[cur] not in seen:
        cur = amends[cur]
        seen.add(cur)
    return cur


def refresh_versions(db: Session) -> int:
    """訂正の大元（original_doc_id）・取下げ（withdrawn）・最新（is_latest）を全件から決め直す。

    大元ごとに、parsed で取下げでない書類のうち提出日時が新しい 1 件を最新にする。
    訂正の対象が DB に無い（取り込む前や 5 年より前）ときも、対象の docID を大元にする。
    戻り値は変えた行数。
    """
    R = LargeHoldingReport
    reports = db.execute(select(
        R.doc_id, R.amends_doc_id, R.original_doc_id, R.is_latest, R.withdrawn,
        R.parse_status, R.submit_datetime,
    )).all()
    if not reports:
        return 0
    T = EdinetDocument
    gone = set(db.scalars(
        select(T.doc_id).where(T.doc_type_code.in_(DOC_TYPES),
                               (T.withdrawal_status == "2") | (T.disclosure_status == "2"))
    ))
    amends = {r.doc_id: r.amends_doc_id for r in reports}
    root = {r.doc_id: _root(r.doc_id, amends) for r in reports}
    best: dict[str, tuple] = {}
    for r in reports:
        if r.parse_status != "parsed" or r.doc_id in gone:
            continue
        key = (r.submit_datetime or datetime.min, r.doc_id)  # noqa: DTZ901  JST naive で比べる
        g = root[r.doc_id]
        if g not in best or key > best[g]:
            best[g] = key
    latest = {k[1] for k in best.values()}

    def write():
        n = 0
        for r in reports:
            want = {"original_doc_id": root[r.doc_id], "withdrawn": r.doc_id in gone,
                    "is_latest": r.doc_id in latest}
            if (r.original_doc_id, r.withdrawn, r.is_latest) != tuple(want.values()):
                db.execute(update(R).where(R.doc_id == r.doc_id).values(**want))
                n += 1
        return n

    return run_sqlite_write(db, write)


def run(db: Session, client: EdinetClient, docs: list[EdinetDocument]) -> dict:
    out = {"parsed": 0, "failed": 0, "errors": 0}
    lookup = CodeLookup.load(db)
    for doc in docs:
        st = ingest_document(db, client, doc, lookup)
        out[{"parsed": "parsed", "failed": "failed"}.get(st, "errors")] += 1
    out["refreshed"] = refresh_versions(db)
    return out

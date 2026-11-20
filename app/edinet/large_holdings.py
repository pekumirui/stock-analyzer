"""EDINET 大量保有報告書・変更報告書・訂正（350 / 360）→ large_holding_reports / large_holdings。

設計は docs/jobs/edinet.md §5（KAB-74）。中身は書類取得 API type=5（XBRL→CSV）。
入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。ZIP は data/edinet に残し、毎朝 OneDrive へ退避する（app/edinet/archive.py。KAB-281）。
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import LARGE_HOLDING_DATE_FIXES_PATH
from app.db import run_sqlite_write, upsert
from app.edinet.archive import archived, download
from app.edinet.client import is_not_found, reraise
from app.edinet.documents import CodeLookup, now_jst
from app.edinet.textblocks import edinet_doc_id
from app.models import EdinetDocument, LargeHolding, LargeHoldingReport

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient

DOC_TYPES = ("350", "360")       # 大量保有報告書・変更報告書 / 訂正報告書
AMENDMENT = "360"
KEEP_YEARS = 5                   # EDINET が大量保有 CSV（type=5）を持つ期間
FUTURE_DAYS = 30                 # 義務発生日が提出日よりこれ以上先なら WARNING（KAB-269）
_SAMPLE_IDS = 8


@dataclass(frozen=True)
class ObligationDateFix:
    recorded: date
    correct: date


def load_obligation_date_fixes(path: Path | None = None) -> dict[str, ObligationDateFix]:
    """config/large_holding_date_fixes.json。キーは doc_id。重複は ValueError。"""
    src = LARGE_HOLDING_DATE_FIXES_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    out: dict[str, ObligationDateFix] = {}
    for item in raw.get("fixes") or []:
        doc_id = str(item["doc_id"]).strip()
        if doc_id in out:
            raise ValueError(f"duplicate doc_id in date fixes: {doc_id}")
        out[doc_id] = ObligationDateFix(
            recorded=date.fromisoformat(item["recorded"]),
            correct=date.fromisoformat(item["correct"]),
        )
    return out


def apply_obligation_date_fix(
    doc_id: str,
    parsed: date | None,
    *,
    fixes: dict[str, ObligationDateFix] | None = None,
) -> date | None:
    """原本の値が recorded と一致したときだけ correct に差し替える。不一致は WARNING。冪等。"""
    if parsed is None:
        return None
    table = load_obligation_date_fixes() if fixes is None else fixes
    fix = table.get(doc_id)
    if fix is None:
        return parsed
    if parsed != fix.recorded:
        print(
            f"WARNING edinet lvh obligation-date-mismatch doc_id={doc_id} "
            f"recorded={fix.recorded.isoformat()} parsed={parsed.isoformat()}",
            flush=True,
        )
        return parsed
    return fix.correct


def submit_day(submit_datetime: datetime | None, filing_date: date | None = None) -> date | None:
    if submit_datetime is not None:
        return submit_datetime.date()
    return filing_date


def is_late_obligation(obligation: date | None, submitted: date | None,
                       *, days: int = FUTURE_DAYS) -> bool:
    if obligation is None or submitted is None:
        return False
    return (obligation - submitted).days >= days


def warn_late_obligation_dates(
    db: Session,
    *,
    fixes: dict[str, ObligationDateFix] | None = None,
) -> int:
    """差し替え後も義務発生日が提出日より FUTURE_DAYS 日以上先の、設定に無い書類を WARNING。"""
    table = load_obligation_date_fixes() if fixes is None else fixes
    R = LargeHoldingReport
    rows = db.execute(
        select(R.doc_id, R.filer_name, R.code, R.obligation_date, R.submit_datetime, R.filing_date)
        .where(R.parse_status == "parsed", R.withdrawn.is_(False), R.obligation_date.is_not(None))
        .order_by(R.doc_id)
    ).all()
    n = 0
    for r in rows:
        if r.doc_id in table:
            continue
        submitted = submit_day(r.submit_datetime, r.filing_date)
        if not is_late_obligation(r.obligation_date, submitted):
            continue
        print(
            f"WARNING edinet lvh obligation-date-future doc_id={r.doc_id} "
            f"filer={r.filer_name or '-'} code={r.code or '-'} "
            f"obligation_date={r.obligation_date.isoformat()} "
            f"submit_date={submitted.isoformat()}",
            flush=True,
        )
        n += 1
    return n


class OutsideEdinetWindow(Exception):
    """大量保有 CSV が EDINET の取得可能期間（KEEP_YEARS 年）より前。"""
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
               fetched_at: datetime, *,
               fixes: dict[str, ObligationDateFix] | None = None) -> dict:
    """large_holding_reports の 1 行。合計の欄は保有者が 1 人だと書類レベルに無いので、保有者 1 の値を使う。"""
    def total(name: str, conv):
        v = conv(name)
        if v is None and len(rep.holders) == 1:
            v = conv(name, rep.holders[0])
        return v

    issuer_sec = sec_code5(rep.text("SecurityCodeOfIssuer"))
    amends = edinet_doc_id(rep.text("IdentificationOfDocumentSubjectToAmendmentDEI"))
    is_amendment = doc.doc_type_code == AMENDMENT
    if is_amendment and not amends:
        amends = edinet_doc_id(doc.parent_doc_id)
    obligation = apply_obligation_date_fix(
        doc.doc_id, rep.day("DateWhenFilingRequirementAroseCoverPage"), fixes=fixes)
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
        "obligation_date": obligation,
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
        "original_doc_id": doc.doc_id,
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


def keep_after(today: date | None = None) -> date:
    """大量保有 CSV を取る file_date の下限（今日の KEEP_YEARS 年前）。"""
    day = today or now_jst().date()
    try:
        return day.replace(year=day.year - KEEP_YEARS)
    except ValueError:
        return day.replace(month=2, day=28, year=day.year - KEEP_YEARS)


def _sample_ids(ids: list[str]) -> str:
    if len(ids) <= _SAMPLE_IDS:
        return ",".join(ids)
    return ",".join(ids[:_SAMPLE_IDS]) + f",...+{len(ids) - _SAMPLE_IDS}"


def pending_documents(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    retry_failed: bool = False,
    reparse: bool = False,
    limit: int | None = None,
    today: date | None = None,
    doc_ids: list[str] | None = None,
) -> list[EdinetDocument]:
    """取り込む書類。350 / 360・CSV あり・取下げ/不開示でない元の行。新しい日から。

    発行者が上場していない書類も取る（code は NULL）。parsed は reparse でなければ飛ばす。
    failed は retry_failed のときだけ。KEEP_YEARS 年より前は一覧に出ても CSV が無いので除く
    （先に mark_outside_window で failed に残す）。
    """
    T = EdinetDocument
    floor = keep_after(today)
    if start and start > floor:
        floor = start
    q = (select(T)
         .where(T.doc_type_code.in_(DOC_TYPES), T.csv_flag.is_(True),
                T.withdrawal_status != "2", T.disclosure_status == "0",
                T.doc_info_edit_status != "1", T.file_date >= floor)
         .order_by(T.file_date.desc(), T.seq_number))
    if end:
        q = q.where(T.file_date <= end)
    if doc_ids:
        q = q.where(T.doc_id.in_(doc_ids))
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
        "amends_doc_id": edinet_doc_id(doc.parent_doc_id) if doc.doc_type_code == AMENDMENT else None,
        "original_doc_id": doc.doc_id, "parse_status": "failed",
        "parse_error": f"{type(exc).__name__}: {exc}"[:2000], "fetched_at": fetched_at,
        "is_latest": False, "withdrawn": False,
    })
    store_report(db, row, [])


def mark_outside_window(db: Session, *, today: date | None = None) -> int:
    """KEEP_YEARS 年より前で report が無い 350/360 を failed で残す。ダウンロードしない。"""
    cutoff = keep_after(today)
    have = set(db.scalars(select(LargeHoldingReport.doc_id)))
    T = EdinetDocument
    docs = db.scalars(
        select(T).where(
            T.doc_type_code.in_(DOC_TYPES), T.csv_flag.is_(True),
            T.file_date < cutoff, T.withdrawal_status != "2",
            T.disclosure_status == "0", T.doc_info_edit_status != "1",
        )
    ).all()
    seen: set[str] = set()
    n = 0
    fetched_at = now_jst()
    for doc in docs:
        if doc.doc_id in seen or doc.doc_id in have:
            continue
        seen.add(doc.doc_id)
        store_failed(
            db, doc,
            OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
            fetched_at,
        )
        n += 1
    return n


def ingest_document(db: Session, client: EdinetClient, doc: EdinetDocument,
                    lookup: CodeLookup, *, today: date | None = None,
                    fixes: dict[str, ObligationDateFix] | None = None) -> str:
    """1 書類: CSV をダウンロード → パース → 保存。

    戻り値は parsed / failed / missing / error。
    missing は CSV が 404 か取得可能期間外。failed で保存し、次の回は飛ばす。
    error は 5xx・タイムアウトなど一時的な失敗。行を作らない。
    1 件ごとの失敗ログは出さない（run が集約する。Slack の failed doc_id= に引っかかるため）。
    """
    from app.edinet_wrapper import DocumentFileType

    expired = doc.file_date < keep_after(today)
    if expired and not archived(db, doc.doc_id, DocumentFileType.CSV):
        store_failed(
            db, doc,
            OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
            now_jst(),
        )
        return "missing"
    try:
        got = download(db, client, doc, DocumentFileType.CSV, expired=expired)
    except Exception as exc:
        reraise(exc)
        if is_not_found(exc):
            store_failed(db, doc, exc, now_jst())
            return "missing"
        return "error"
    fetched_at = now_jst()
    try:
        rep = parse_csv_zip(got.content)
        row = report_row(doc, rep, lookup, fetched_at, fixes=fixes)
        holders = holder_rows(doc.doc_id, rep)
    except Exception as exc:
        store_failed(db, doc, exc, fetched_at)
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
        key = (r.submit_datetime or datetime.min, r.doc_id)
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


def run(db: Session, client: EdinetClient, docs: list[EdinetDocument],
        *, today: date | None = None,
        fixes: dict[str, ObligationDateFix] | None = None) -> dict:
    out = {"parsed": 0, "failed": 0, "missing": 0, "errors": 0}
    lookup = CodeLookup.load(db)
    table = load_obligation_date_fixes() if fixes is None else fixes
    missing_ids: list[str] = []
    failed_ids: list[str] = []
    error_ids: list[str] = []
    for doc in docs:
        st = ingest_document(db, client, doc, lookup, today=today, fixes=table)
        out[{"parsed": "parsed", "failed": "failed", "missing": "missing"}.get(st, "errors")] += 1
        if st == "missing":
            missing_ids.append(doc.doc_id)
        elif st == "failed":
            failed_ids.append(doc.doc_id)
        elif st != "parsed":
            error_ids.append(doc.doc_id)
    if missing_ids:
        print(f"edinet lvh missing n={len(missing_ids)} sample={_sample_ids(missing_ids)}",
              flush=True)
    if failed_ids:
        print(f"edinet lvh parse-error n={len(failed_ids)} sample={_sample_ids(failed_ids)}",
              flush=True)
    if error_ids:
        print(f"edinet lvh download-error n={len(error_ids)} sample={_sample_ids(error_ids)}",
              flush=True)
    out["refreshed"] = refresh_versions(db)
    out["late"] = warn_late_obligation_dates(db, fixes=table)
    return out


def exit_status(st: dict) -> int:
    """cron の終了コード。パース失敗と一時的な download 失敗だけ 1。404 の missing は 0。"""
    return 0 if st["failed"] + st["errors"] == 0 else 1

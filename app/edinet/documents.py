"""EDINET 書類一覧（type=2）→ edinet_documents / edinet_list_days。

設計は docs/DESIGN_edinet.md §3。一意キーは (file_date, seq_number)。docID では引かない。
ラッパーは Python 3.11+ なので型以外は関数の中で import する。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.models import EdinetDocument, EdinetFiler, EdinetListDay, Stock

if TYPE_CHECKING:
    from app.edinet_wrapper import DocumentEntry, DocumentList, EdinetClient

JST = ZoneInfo("Asia/Tokyo")
# EDINET の一覧は過去 10 年分まで（ラッパーの DATE_MAX_LOOKBACK_DAYS と同じ）
MAX_LOOKBACK_DAYS = 10 * 365 + 7
DEFAULT_WINDOW_DAYS = 7
# 取下げで EDINET が元の行のメタデータを空にして返す。空で上書きしない列
_KEEP_ON_NULL = (
    "code", "edinet_code", "sec_code", "jcn", "filer_name", "fund_code", "ordinance_code",
    "form_code", "doc_type_code", "period_start", "period_end", "submit_datetime",
    "doc_description", "issuer_edinet_code", "subject_edinet_code",
    "subsidiary_edinet_codes", "current_report_reasons", "parent_doc_id", "ope_datetime",
)


def now_jst() -> datetime:
    """JST の naive datetime（DB は JST naive で持つ）。"""
    return datetime.now(JST).replace(tzinfo=None)


def _naive_jst(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST).replace(tzinfo=None)
    return dt


class CodeLookup:
    """書類の提出者 → stocks.code。secCode を先に、無ければ EDINET コード経由。"""

    def __init__(self, stock_codes: set[str], edinet_to_sec: dict[str, str]):
        self.stock_codes = stock_codes
        self.edinet_to_sec = edinet_to_sec

    @classmethod
    def load(cls, db: Session) -> CodeLookup:
        codes = set(db.scalars(select(Stock.code)))
        e2s = dict(db.execute(
            select(EdinetFiler.edinet_code, EdinetFiler.sec_code)
            .where(EdinetFiler.sec_code.is_not(None))
        ).all())
        return cls(codes, e2s)

    def code_for(self, sec_code: str | None, edinet_code: str | None) -> str | None:
        if sec_code and sec_code in self.stock_codes:
            return sec_code
        sec = self.edinet_to_sec.get(edinet_code or "")
        if sec and sec in self.stock_codes:
            return sec
        return None


def entry_row(entry: DocumentEntry, lookup: CodeLookup, fetched_at: datetime) -> dict:
    return {
        "file_date": entry.file_date,
        "seq_number": entry.seq_number,
        "doc_id": entry.doc_id,
        "code": lookup.code_for(entry.sec_code, entry.edinet_code),
        "edinet_code": entry.edinet_code,
        "sec_code": entry.sec_code,
        "jcn": entry.jcn,
        "filer_name": entry.filer_name,
        "fund_code": entry.fund_code,
        "ordinance_code": entry.ordinance_code,
        "form_code": entry.form_code,
        "doc_type_code": entry.doc_type_code,
        "period_start": entry.period_start,
        "period_end": entry.period_end,
        "submit_datetime": _naive_jst(entry.submit_datetime),
        "doc_description": entry.doc_description,
        "issuer_edinet_code": entry.issuer_edinet_code,
        "subject_edinet_code": entry.subject_edinet_code,
        "subsidiary_edinet_codes": ",".join(entry.subsidiary_edinet_codes) or None,
        "current_report_reasons": ",".join(entry.current_report_reasons) or None,
        "parent_doc_id": entry.parent_doc_id,
        "ope_datetime": _naive_jst(entry.ope_datetime),
        "withdrawal_status": str(entry.withdrawal_status),
        "doc_info_edit_status": str(entry.doc_info_edit_status),
        "disclosure_status": str(entry.disclosure_status),
        "xbrl_flag": entry.has_xbrl,
        "pdf_flag": entry.has_pdf,
        "attach_doc_flag": entry.has_attachment,
        "english_doc_flag": entry.has_english,
        "csv_flag": entry.has_csv,
        "legal_status": str(entry.legal_status),
        "fetched_at": fetched_at,
    }


def upsert_documents(db: Session, rows: list[dict]) -> None:
    """UPSERT。_KEEP_ON_NULL の列は新しい値が NULL なら前の値を残す。"""
    if not rows:
        return
    table = EdinetDocument.__table__
    ins = sqlite_insert(table)
    keys = {"file_date", "seq_number"}
    set_ = {}
    for col in table.columns:
        if col.name in keys:
            continue
        if col.name in _KEEP_ON_NULL:
            set_[col.name] = func.coalesce(ins.excluded[col.name], table.c[col.name])
        else:
            set_[col.name] = ins.excluded[col.name]
    stmt = ins.on_conflict_do_update(index_elements=sorted(keys), set_=set_)
    for i in range(0, len(rows), 1000):
        db.execute(stmt, rows[i:i + 1000])


def _is_event(row) -> bool:
    return (row.withdrawal_status == "1" or row.doc_info_edit_status == "1"
            or row.disclosure_status in ("1", "3"))


def apply_events(db: Session, doc_ids: set[str]) -> int:
    """イベント行（取下げ・書類情報修正・不開示の開始/解除）を元の行の状態に反映する。

    DB にある行から毎回決め直す（取り直しの順番に依らない）。
    - 取下書（withdrawal=1, parentDocID=D）がある → D の元の行を withdrawal=2
    - D の修正通知（edit=1）がある → D の元の行を edit=2
    - D の不開示の最後のイベントが開始（1）→ 元の行を 2、解除（3）→ 0
    NOTE(spec-gap): EDINET も元の行の状態を書き換えて返すが、元の日を取り直さないと見えない。
    """
    if not doc_ids:
        return 0
    T = EdinetDocument
    ids = sorted(doc_ids)
    changed = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.execute(
            select(T).where(or_(T.doc_id.in_(chunk), T.parent_doc_id.in_(chunk)))
            .order_by(T.file_date, T.seq_number)
        ).scalars().all()
        for d in chunk:
            mine = [r for r in rows if r.doc_id == d]
            base = [r for r in mine if not _is_event(r)]
            if not base:
                continue
            withdrawn = any(r.withdrawal_status == "1" and r.parent_doc_id == d for r in rows)
            edited = any(r.doc_info_edit_status == "1" for r in mine)
            disc = [r.disclosure_status for r in mine if r.disclosure_status in ("1", "3")]
            for r in base:
                vals = {}
                if withdrawn and r.withdrawal_status != "2":
                    vals["withdrawal_status"] = "2"
                if edited and r.doc_info_edit_status != "2":
                    vals["doc_info_edit_status"] = "2"
                if disc:
                    want = "2" if disc[-1] == "1" else "0"
                    if r.disclosure_status != want:
                        vals["disclosure_status"] = want
                if vals:
                    db.execute(update(T).where(
                        T.file_date == r.file_date, T.seq_number == r.seq_number,
                    ).values(**vals))
                    changed += 1
    return changed


def event_targets(entries) -> set[str]:
    """状態を決め直す docID。一覧の全書類と取下書の親。

    元の日を取り直すと状態列は EDINET の値で上書きされるので、イベント行だけでなく全部見る。
    """
    out = set()
    for e in entries:
        out.add(e.doc_id)
        if e.parent_doc_id:
            out.add(e.parent_doc_id)
    return out


def is_finalized(day: date, process_datetime: datetime | None, fetched_at: datetime) -> bool:
    """その日の一覧が確定か。EDINET の一覧作成時刻（無ければ取得時刻）が翌日以降なら確定。"""
    ref = _naive_jst(process_datetime) or fetched_at
    return ref.date() > day


def store_listing(db: Session, day: date, listing: DocumentList, lookup: CodeLookup,
                  fetched_at: datetime) -> dict:
    entries = list(listing.results)
    rows = [entry_row(e, lookup, fetched_at) for e in entries]
    meta = listing.metadata

    def write():
        upsert_documents(db, rows)
        db.flush()
        n_events = apply_events(db, event_targets(entries))
        upsert(db, EdinetListDay, [{
            "file_date": day,
            "count": meta.count,
            "max_seq": max((e.seq_number for e in entries), default=None),
            "process_datetime": _naive_jst(meta.process_datetime),
            "finalized": is_finalized(day, meta.process_datetime, fetched_at),
            "fetched_at": fetched_at,
        }], commit=False)
        return n_events

    n_events = run_sqlite_write(db, write)
    return {"rows": len(rows), "events": n_events,
            "linked": sum(1 for r in rows if r["code"])}


def days_to_fetch(db: Session, today: date, start: date | None = None,
                  end: date | None = None, force: bool = False) -> list[date]:
    """取る日。start 省略時は直近 DEFAULT_WINDOW_DAYS 日と、それより古い未確定の日。

    手で取った過去の日に引っ張られないよう、既定は「最後に取った日の翌日から」にしない。
    cron が 1 週間より長く止まったら --from で埋める。
    force でなければ確定済みの日は飛ばす。未来日と 10 年より前は取らない。
    """
    earliest = today - timedelta(days=MAX_LOOKBACK_DAYS)
    done = dict(db.execute(select(EdinetListDay.file_date, EdinetListDay.finalized)).all())
    if start is None:
        start = today - timedelta(days=DEFAULT_WINDOW_DAYS)
        opens = [d for d, f in done.items() if not f]
        if opens:
            start = min(start, min(opens))
    end = min(end or today, today)
    start = max(start, earliest)
    out = []
    d = start
    while d <= end:
        if force or not done.get(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def ingest_days(db: Session, client: EdinetClient, days: list[date], log=print) -> dict:
    lookup = CodeLookup.load(db)
    total = {"days": 0, "rows": 0, "events": 0, "linked": 0}
    for day in days:
        listing = client.list_documents(day)
        st = store_listing(db, day, listing, lookup, now_jst())
        log(f"edinet list {day.isoformat()} count={listing.metadata.count} "
            f"rows={st['rows']} linked={st['linked']} events={st['events']}")
        total["days"] += 1
        for k in ("rows", "events", "linked"):
            total[k] += st[k]
    return total


def relink_codes(db: Session) -> int:
    """code が空の行を、今の stocks / edinet_filers で付け直す（コードリスト更新の後）。"""
    n = db.execute(text(
        "UPDATE edinet_documents SET code = sec_code "
        "WHERE code IS NULL AND sec_code IN (SELECT code FROM stocks)"
    )).rowcount or 0
    n += db.execute(text(
        "UPDATE edinet_documents SET code = ("
        "  SELECT f.sec_code FROM edinet_filers f"
        "  WHERE f.edinet_code = edinet_documents.edinet_code) "
        "WHERE code IS NULL AND edinet_code IN ("
        "  SELECT f.edinet_code FROM edinet_filers f"
        "  JOIN stocks s ON s.code = f.sec_code)"
    )).rowcount or 0
    db.commit()
    return n

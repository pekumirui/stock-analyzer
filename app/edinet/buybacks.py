"""EDINET 自己株券買付状況報告書・訂正（220 / 230）→ edinet_buybacks / edinet_buyback_resolutions。

設計は docs/DESIGN_edinet.md §6（KAB-79）。中身は書類取得 API type=1（XBRL）のテキストブロックの表。
入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。ZIP は保存しない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.edinet.documents import CodeLookup, now_jst
from app.edinet.large_holdings import is_not_found
from app.edinet.textblocks import (
    Block,
    Instance,
    chain_root,
    doc_metas,
    honbun_html,
    integer,
    is_dash,
    jp_dates,
    number,
    parse_html,
    read_instance,
    sample_ids,
    squash,
    years_ago,
)
from app.models import EdinetBuyback, EdinetBuybackResolution, EdinetDocument

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient

DOC_TYPES = ("220", "230")      # 自己株券買付状況報告書 / 訂正
AMENDMENT = "230"
# 縦覧期間は 1 年。過ぎると EDINET の一覧からも消え、書類も取れない
KEEP_YEARS = 1
# (テキストブロック, kind)。投資法人（jpsps）は役員会の決議 1 つのブロック
_ACQ_BLOCKS = (
    ("AcquisitionsByResolutionOfShareholdersMeetingTextBlock", "shareholders"),
    ("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock", "board"),
    ("AcquisitionsOfTreasurySharesTextBlock", "board"),
)
_BODY_RE = re.compile(r"^(.*?)[(（]")
_ACQ_HEAD_RE = re.compile(r"[1１]\s*[．.、]?\s*【\s*取得状況\s*】")
_DISP_HEAD_RE = re.compile(r"[2２]\s*[．.、]?\s*【\s*処理状況\s*】")
_SCALED_UNIT_RE = re.compile(r"(千|万|百万|億)(株|円)")
_PROGRESS_TOL = 0.005   # 進捗率と累計÷枠の差（0.5 ポイント）
_LOG = "edinet buyback"


class OutsideEdinetWindow(Exception):
    """縦覧期間（KEEP_YEARS 年）を過ぎた書類。EDINET から取れない。"""


@dataclass
class Resolution:
    kind: str
    label: str
    body: str | None = None
    resolution_date: date | None = None
    acquire_from: date | None = None
    acquire_to: date | None = None
    limit_shares: int | None = None
    limit_amount: int | None = None
    month_shares: int | None = None
    month_amount: int | None = None
    cumulative_shares: int | None = None
    cumulative_amount: int | None = None
    progress_shares: float | None = None
    progress_amount: float | None = None
    _month_row: bool = False


@dataclass
class ParsedBuyback:
    period_start: date | None = None
    period_end: date | None = None
    issued_shares: int | None = None
    treasury_shares: int | None = None
    amends: str | None = None
    resolutions: list[Resolution] = field(default_factory=list)


def _values(row: list[str]) -> list[str]:
    """見出しのセルを除いた、空でないセル。"""
    return [c for c in row[1:] if c.strip()]


def _last2(row: list[str], conv, *, dash_zero: bool = False):
    vals = _values(row)[-2:]
    vals = [""] * (2 - len(vals)) + vals
    out = []
    for v in vals:
        if dash_zero and is_dash(v):
            out.append(0)
        else:
            out.append(conv(v))
    return out


def _pct(cell: str) -> float | None:
    v = number(cell)
    return None if v is None else round(float(v) / 100, 6)


def _start(label_cells: list[str], kind: str, row: list[str]) -> Resolution:
    raw = label_cells[0]
    head = squash(raw)
    res = Resolution(kind=kind, label=raw)
    m = _BODY_RE.match(head)
    res.body = (m[1] if m else head.split("での決議状況")[0]) or None
    dates = jp_dates(" ".join(label_cells))
    if dates:
        res.resolution_date = dates[0]
    _period(res, " ".join(label_cells))
    res.limit_shares, res.limit_amount = _last2(row, integer)
    return res


def _period(res: Resolution, text: str) -> bool:
    """「取得期間 A～B」「取得日 A」→ acquire_from / acquire_to。"""
    text = squash(text)
    for key in ("取得期間", "取得日"):
        at = text.find(key)
        if at < 0:
            continue
        after = jp_dates(text[at:])
        if len(after) >= 2 and key == "取得期間":
            res.acquire_from, res.acquire_to = after[0], after[1]
        elif after:
            res.acquire_from = res.acquire_to = after[0]
        return bool(after)
    return False


def parse_acquisitions(block: Block, kind: str) -> list[Resolution]:
    """取得状況の表 → 決議ごと。「決議状況」の行で新しい決議を始める。"""
    out: list[Resolution] = []
    cur: Resolution | None = None
    for row in block.rows():
        cells = [c for c in row if c.strip()]
        if not cells:
            continue
        label = squash(row[0]) or squash(cells[0])
        if "決議状況" in label:
            cur = _start([row[0] or cells[0]], kind, row)
            out.append(cur)
        elif cur is None:
            continue
        elif cur.acquire_to is None and label.startswith(("(取得期間", "取得期間", "(取得日")):
            _period(cur, label)   # 期間が決議の行の下の行にある書類
        elif "報告月における取得" in label:
            cur._month_row = True
        elif label == "計" and cur.month_shares is None:
            cur.month_shares, cur.month_amount = _last2(row, integer, dash_zero=True)
        elif "累計" in label or "累積" in label:
            cur.cumulative_shares, cur.cumulative_amount = _last2(row, integer, dash_zero=True)
        elif "進捗" in label:
            cur.progress_shares, cur.progress_amount = _last2(row, _pct)
    for r in out:
        if r.month_shares is None and r._month_row:
            r.month_shares, r.month_amount = 0, 0
    return out


def _holding(block: Block | None) -> tuple[int | None, int | None]:
    issued = treasury = None
    if block is None:
        return issued, treasury
    for row in block.rows():
        label = squash(row[0]) if row else ""
        vals = _values(row)
        if not vals:
            continue
        if label.startswith("発行済") and issued is None:
            issued = integer(vals[-1])
        elif label.startswith("保有自己") and treasury is None:
            treasury = integer(vals[-1])
    return issued, treasury


def _acq_kind(text: str) -> str | None:
    s = squash(text)
    if "株主総会決議" in s:
        return "shareholders"
    if "取締役会決議" in s or "役員会" in s:
        return "board"
    return None


def _acq_section(html: str) -> str | None:
    """１【取得状況】から２【処理状況】の手前まで。無ければ None。"""
    m = _ACQ_HEAD_RE.search(html)
    if not m:
        return None
    rest = html[m.start():]
    end = _DISP_HEAD_RE.search(rest)
    return rest[:end.start()] if end else rest


def _pick_acq_honbun(honbun: str | list[str] | None) -> str | None:
    """取得状況の章がある本文を1つ。複数なら ValueError。該当なしはファイル1件のときだけそれを返す。"""
    if honbun is None:
        return None
    files = [honbun] if isinstance(honbun, str) else [h for h in honbun if h]
    if not files:
        return None
    hits = [h for h in files if _ACQ_HEAD_RE.search(h)]
    if len(hits) > 1:
        raise ValueError(f"取得状況の本文 iXBRL が複数ある: {len(hits)} 件")
    if len(hits) == 1:
        return hits[0]
    return files[0] if len(files) == 1 else None


def _check_acq_units(rows: list[list[str]]) -> None:
    header = None
    for row in rows:
        joined = squash("".join(row))
        if "株式数" in joined or "価額" in joined:
            header = joined
            break
    if header is None:
        raise ValueError("取得状況の表に株式数/価額の見出しが無い")
    if _SCALED_UNIT_RE.search(header) or "(株)" not in header or "(円)" not in header:
        raise ValueError("取得状況の単位が株・円ではない")


def _check_honbun_resolution(r: Resolution) -> None:
    for unit, month, cum, lim, prog in (
        ("株", r.month_shares, r.cumulative_shares, r.limit_shares, r.progress_shares),
        ("円", r.month_amount, r.cumulative_amount, r.limit_amount, r.progress_amount),
    ):
        if cum is not None and lim is not None and cum > lim:
            raise ValueError(f"累計が枠を超えている({unit}): {cum} > {lim}")
        if month is not None and cum is not None and month > cum:
            raise ValueError(f"報告月の取得が累計を超えている({unit}): {month} > {cum}")
        if prog is not None and cum is not None and lim:
            expected = cum / lim
            if abs(prog - expected) > _PROGRESS_TOL:
                raise ValueError(f"進捗率が累計÷枠と合わない({unit}): {prog} vs {expected}")


def parse_acquisitions_honbun(html: str) -> list[Resolution]:
    """本文 iXBRL の未タグ表（１【取得状況】のみ）。見出し必須。怪しければ ValueError。"""
    section = _acq_section(html)
    if section is None:
        raise ValueError("取得状況の章が無い")
    out: list[Resolution] = []
    kind: str | None = None
    for k, v in parse_html(section).items:
        if k == "X":
            got = _acq_kind(v)
            if got:
                kind = got
            continue
        if kind is None:
            raise ValueError("取得状況の表に株主総会/取締役会の見出しが無い")
        _check_acq_units(v)
        out += parse_acquisitions(Block(items=[("T", v)]), kind)
    if not out:
        if "該当事項はありません" in squash(section):
            return []
        raise ValueError("取得状況の決議が読めない")
    seen: set[tuple[str, date | None]] = set()
    for r in out:
        key = (r.kind, r.resolution_date)
        if key in seen:
            raise ValueError(f"同じ区分・同じ決議日の決議が重複: {r.kind} {r.resolution_date}")
        seen.add(key)
        _check_honbun_resolution(r)
    return out


def parse_buyback(inst: Instance, honbun: str | list[str] | None = None) -> ParsedBuyback:
    out = ParsedBuyback()
    period = jp_dates(inst.value("ReportingPeriodCoverPage"))
    if len(period) >= 2:
        out.period_start, out.period_end = period[0], period[1]
    out.issued_shares, out.treasury_shares = _holding(inst.block("HoldingOfTreasurySharesTextBlock"))
    out.amends = inst.value("IdentificationOfDocumentSubjectToAmendmentDEI")
    seen = False
    for local, kind in _ACQ_BLOCKS:
        block = inst.block(local)
        if block is None:
            continue
        seen = True
        out.resolutions += parse_acquisitions(block, kind)
    if not seen:
        html = _pick_acq_honbun(honbun)
        if html is None:
            raise ValueError(f"取得状況のテキストブロックが無い: {inst.name}")
        out.resolutions += parse_acquisitions_honbun(html)
    return out


def report_row(doc: EdinetDocument, rep: ParsedBuyback, lookup: CodeLookup,
               fetched_at: datetime) -> dict:
    is_amendment = doc.doc_type_code == AMENDMENT
    amends = (rep.amends or doc.parent_doc_id) if is_amendment else None
    month = [r for r in rep.resolutions if r.month_shares is not None]
    month_amounts = [r.month_amount for r in month if r.month_amount is not None]
    return {
        "doc_id": doc.doc_id,
        "file_date": doc.file_date,
        "submit_datetime": doc.submit_datetime,
        "doc_type_code": doc.doc_type_code,
        "form_code": doc.form_code,
        "code": doc.code or lookup.code_for(doc.sec_code, doc.edinet_code),
        "edinet_code": doc.edinet_code,
        "filer_name": doc.filer_name,
        "report_month": rep.period_end.strftime("%Y-%m") if rep.period_end else None,
        "period_start": rep.period_start,
        "period_end": rep.period_end,
        "issued_shares": rep.issued_shares,
        "treasury_shares": rep.treasury_shares,
        "resolution_count": len(rep.resolutions),
        "month_shares": sum(r.month_shares for r in month) if month else None,
        "month_amount": sum(month_amounts) if month_amounts else None,
        "is_amendment": is_amendment,
        "amends_doc_id": amends,
        "original_doc_id": doc.doc_id,   # refresh_versions が付け直す
        "parse_status": "parsed",
        "parse_error": None,
        "fetched_at": fetched_at,
    }


def resolution_rows(doc_id: str, rep: ParsedBuyback) -> list[dict]:
    return [{
        "doc_id": doc_id,
        "resolution_no": i,
        "kind": r.kind,
        "body": r.body,
        "resolution_date": r.resolution_date,
        "acquire_from": r.acquire_from,
        "acquire_to": r.acquire_to,
        "limit_shares": r.limit_shares,
        "limit_amount": r.limit_amount,
        "month_shares": r.month_shares,
        "month_amount": r.month_amount,
        "cumulative_shares": r.cumulative_shares,
        "cumulative_amount": r.cumulative_amount,
        "progress_shares": r.progress_shares,
        "progress_amount": r.progress_amount,
        "label": r.label,
    } for i, r in enumerate(rep.resolutions, start=1)]


def keep_after(today: date | None = None) -> date:
    return years_ago(today or now_jst().date(), KEEP_YEARS)


def _candidates(db: Session):
    T = EdinetDocument
    return (select(T)
            .where(T.doc_type_code.in_(DOC_TYPES), T.xbrl_flag.is_(True),
                   T.withdrawal_status != "2", T.disclosure_status == "0",
                   T.doc_info_edit_status != "1"))


def pending_documents(
    db: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    retry_failed: bool = False,
    reparse: bool = False,
    limit: int | None = None,
    today: date | None = None,
) -> list[EdinetDocument]:
    """取り込む書類。220 / 230・XBRL あり・取下げ/不開示でない元の行。新しい日から。"""
    T = EdinetDocument
    floor = keep_after(today)
    if start and start > floor:
        floor = start
    q = _candidates(db).where(T.file_date >= floor).order_by(T.file_date.desc(), T.seq_number)
    if end:
        q = q.where(T.file_date <= end)
    status = dict(db.execute(select(EdinetBuyback.doc_id, EdinetBuyback.parse_status)).all())
    out, seen = [], set()
    for d in db.scalars(q):
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


def store_report(db: Session, row: dict, resolutions: list[dict]) -> None:
    def write():
        upsert(db, EdinetBuyback, [row], commit=False)
        db.execute(delete(EdinetBuybackResolution).where(EdinetBuybackResolution.doc_id == row["doc_id"]))
        upsert(db, EdinetBuybackResolution, resolutions, commit=False)

    run_sqlite_write(db, write)


def store_failed(db: Session, doc: EdinetDocument, exc: Exception, fetched_at: datetime) -> None:
    row = {c.name: None for c in EdinetBuyback.__table__.columns}
    is_amendment = doc.doc_type_code == AMENDMENT
    row.update({
        "doc_id": doc.doc_id, "file_date": doc.file_date, "submit_datetime": doc.submit_datetime,
        "doc_type_code": doc.doc_type_code, "form_code": doc.form_code, "code": doc.code,
        "edinet_code": doc.edinet_code, "filer_name": doc.filer_name,
        "is_amendment": is_amendment, "amends_doc_id": doc.parent_doc_id if is_amendment else None,
        "original_doc_id": doc.doc_id, "is_latest": False, "withdrawn": False,
        "parse_status": "failed", "parse_error": f"{type(exc).__name__}: {exc}"[:2000],
        "fetched_at": fetched_at,
    })
    store_report(db, row, [])


def mark_outside_window(db: Session, *, today: date | None = None) -> int:
    """縦覧期間より前で、まだ行の無い 220/230 を failed で残す。ダウンロードしない。"""
    cutoff = keep_after(today)
    have = set(db.scalars(select(EdinetBuyback.doc_id)))
    seen: set[str] = set()
    n = 0
    fetched_at = now_jst()
    for doc in db.scalars(_candidates(db).where(EdinetDocument.file_date < cutoff)):
        if doc.doc_id in seen or doc.doc_id in have:
            continue
        seen.add(doc.doc_id)
        store_failed(db, doc, OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
                     fetched_at)
        n += 1
    return n


def ingest_document(db: Session, client: EdinetClient, doc: EdinetDocument,
                    lookup: CodeLookup, *, today: date | None = None) -> str:
    """1 書類: XBRL をダウンロード → パース → 保存。戻り値は parsed / failed / missing / error。"""
    from app.edinet_wrapper import DocumentFileType

    if doc.file_date < keep_after(today):
        store_failed(db, doc, OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
                     now_jst())
        return "missing"
    try:
        got = client.download(doc.doc_id, DocumentFileType.MAIN)
    except Exception as exc:
        if is_not_found(exc):
            store_failed(db, doc, exc, now_jst())
            return "missing"
        return "error"
    fetched_at = now_jst()
    try:
        rep = parse_buyback(read_instance(got.content), honbun_html(got.content))
        row = report_row(doc, rep, lookup, fetched_at)
        res = resolution_rows(doc.doc_id, rep)
    except Exception as exc:
        store_failed(db, doc, exc, fetched_at)
        return "failed"
    store_report(db, row, res)
    print(f"{_LOG} parsed doc_id={doc.doc_id} type={doc.doc_type_code} code={row['code']} "
          f"month={row['report_month']} resolutions={len(res)} shares={row['month_shares']}", flush=True)
    return "parsed"


def refresh_versions(db: Session) -> int:
    """訂正の大元・取下げ・最新を全件から決め直す（large_holdings.refresh_versions と同じ考え方）。"""
    R = EdinetBuyback
    reports = db.execute(select(
        R.doc_id, R.amends_doc_id, R.original_doc_id, R.is_latest, R.withdrawn,
        R.parse_status, R.submit_datetime,
    )).all()
    if not reports:
        return 0
    gone = {k for k, m in doc_metas(db, DOC_TYPES).items() if m.gone}
    amends = {r.doc_id: r.amends_doc_id for r in reports}
    root = {r.doc_id: chain_root(r.doc_id, amends) for r in reports}
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


def run(db: Session, client: EdinetClient, docs: list[EdinetDocument],
        *, today: date | None = None) -> dict:
    out = {"parsed": 0, "failed": 0, "missing": 0, "errors": 0}
    lookup = CodeLookup.load(db)
    ids: dict[str, list[str]] = {"missing": [], "failed": [], "error": []}
    for doc in docs:
        st = ingest_document(db, client, doc, lookup, today=today)
        out[{"parsed": "parsed", "failed": "failed", "missing": "missing"}.get(st, "errors")] += 1
        if st != "parsed":
            ids["error" if st not in ids else st].append(doc.doc_id)
    for key, tag in (("missing", "missing"), ("failed", "parse-error"), ("error", "download-error")):
        if ids[key]:
            print(f"{_LOG} {tag} n={len(ids[key])} sample={sample_ids(ids[key])}", flush=True)
    out["refreshed"] = refresh_versions(db)
    return out


def exit_status(st: dict) -> int:
    """cron の終了コード。パース失敗と一時的な download 失敗だけ 1。404 の missing は 0。"""
    return 0 if st["failed"] + st["errors"] == 0 else 1

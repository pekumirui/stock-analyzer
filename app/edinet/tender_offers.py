"""EDINET 公開買付けの書類（240〜340）→ edinet_tender_offer_docs / edinet_tender_offers。

設計は docs/DESIGN_edinet.md §7（KAB-79）。中身は書類取得 API type=1（XBRL）のテキストブロック。
書類は parent_doc_id をたどって大元（公開買付届出書）に束ね、案件の行は取込のたびに全件から決め直す。
入口は edinet_documents（先に jobs/edinet/edinet_documents.py）。ZIP は保存しない。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.edinet.client import reraise
from app.edinet.documents import CodeLookup, now_jst
from app.edinet.large_holdings import is_not_found
from app.edinet.textblocks import (
    Block,
    DocMeta,
    Instance,
    chain_root,
    doc_metas,
    integer,
    jp_dates,
    nfkc,
    number,
    read_instance,
    sample_ids,
    squash,
    years_ago,
)
from app.models import EdinetDocument, EdinetTenderOffer, EdinetTenderOfferDoc

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient

NOTIFICATION = ("240", "250")        # 公開買付届出書 / 訂正届出書
WITHDRAWAL = ("260",)                # 公開買付撤回届出書
REPORT = ("270", "280")              # 公開買付報告書 / 訂正報告書
OPINION = ("290", "300")             # 意見表明報告書 / 訂正
OTHER = ("310", "320", "330", "340")  # 対質問回答報告書・別途買付け禁止の特例を受けるための申出書（と訂正）
DOC_TYPES = NOTIFICATION + WITHDRAWAL + REPORT + OPINION + OTHER
# 訂正（S100Y50I など）は別のローカル名。見出しに【】が無いこともある
OPINION_BLOCKS = (
    "OpinionRegardingSaidTenderOfferAndBasisAndReasonsEtcTextBlock",
    "OpinionAndBasisAndReasonOfOpinionRegardingSaidTenderOfferTextBlock",
)
_OPINION_HEADING_RE = re.compile(r"[\(（][1１]")
# 中身を読む書類。ほかは一覧のメタデータだけ持つ（parse_status='meta'）
PARSED_TYPES = NOTIFICATION + REPORT + OPINION
ISSUER_ORDINANCE = "050"             # 発行者による上場株券等の公開買付けの開示に関する内閣府令
KEEP_YEARS = 5                       # 縦覧期間。過ぎると EDINET の一覧からも消え、書類も取れない
_LOG = "edinet tob"
_PERIOD_RE = re.compile(r"から.*?まで")
_NOT_SHARES = ("新株予約権", "新投資口予約権", "社債", "信託受益", "預託", "算定")
_PRICE_RE = re.compile(r"金\s*([\d,]+(?:\.\d+)?)\s*円")


class OutsideEdinetWindow(Exception):
    """縦覧期間（KEEP_YEARS 年）を過ぎた書類。EDINET から取れない。"""


# ---- パース ---------------------------------------------------------------

def _label(row: list[str]) -> str:
    return squash(row[0]) if row else ""


def parse_period(block: Block | None) -> tuple[date | None, date | None]:
    """買付け等の期間。表の「買付け等の期間」の行、無ければ文の最初の「A から B まで」。"""
    if block is None:
        return None, None
    for row in block.rows():
        if "期間" in _label(row) and len(row) > 1:
            ds = jp_dates(" ".join(row[1:]))
            if len(ds) >= 2:
                return ds[0], ds[1]
    for text in [*block.texts, block.text()]:
        if "から" in text:
            ds = jp_dates(text)
            if len(ds) >= 2:
                return ds[0], ds[1]
    return None, None


def parse_price(block: Block | None) -> float | None:
    """普通株式 1 株（投資法人は 1 口）の価格（円）。株券・普通株式・投資証券の行の最初の「金 X 円」。

    新株予約権・社債・信託受益証券・預託証券の行は見ない。無ければ文の「１株につき金 X 円」。
    """
    if block is None:
        return None
    for row in block.rows():
        label = _label(row)
        if not label or any(w in label for w in _NOT_SHARES):
            continue
        m = _PRICE_RE.search(nfkc(" ".join(row[1:])))
        if m:
            return float(m[1].replace(",", ""))
    for text in block.texts:
        t = nfkc(text)
        if "1株につき" in t.replace(" ", ""):
            m = _PRICE_RE.search(t)
            if m:
                return float(m[1].replace(",", ""))
    return None


def _cell_int(row: list[str], i: int | None) -> int | None:
    return integer(row[i]) if i is not None and i < len(row) else None


def _header_index(header: list[str]) -> dict[str, int]:
    return {squash(h): i for i, h in enumerate(header)}


def parse_planned(block: Block | None, *, issuer: bool) -> tuple[int | None, int | None, int | None]:
    """(買付予定数, 下限, 上限)。合計の行（無ければ最初の行）。発行者の TOB は上限 = 計（予定数＋超過予定数）。"""
    if block is None:
        return None, None, None
    for table in block.tables:
        head = next((i for i, r in enumerate(table) if any("買付予定数" == squash(c) for c in r)), None)
        if head is None:
            continue
        idx = _header_index(table[head])
        body = [r for r in table[head + 1:] if r and any(c.strip() for c in r)]
        if not body:
            continue
        row = next((r for r in body if _label(r) == "合計"), body[0])
        planned, lower, upper, total = (_cell_int(row, idx.get(k)) for k in
                                        ("買付予定数", "買付予定数の下限", "買付予定数の上限", "計"))
        if issuer:
            return planned, None, total or planned
        return planned, lower, upper
    return None, None, None


def parse_result(block: Block | None) -> tuple[int | None, int | None]:
    """(応募数, 買付数)。列の見出しに応募数・買付数がある表（合計の行）か、行の見出しにある表（発行者）。"""
    if block is None:
        return None, None
    for table in block.tables:
        for i, r in enumerate(table):
            cols = [squash(c) for c in r]
            ten = next((j for j, c in enumerate(cols) if "応募数" in c), None)
            buy = next((j for j, c in enumerate(cols) if "買付数" in c), None)
            if ten is None or buy is None or ten == 0:
                continue
            body = [x for x in table[i + 1:] if x]
            row = next((x for x in body if _label(x) == "合計"), body[0] if body else None)
            if row is None:
                continue
            return _cell_int(row, ten), _cell_int(row, buy)
        tendered = purchased = None
        for r in table:
            label = _label(r)
            vals = [c for c in r[1:] if c.strip()]
            if not vals:
                continue
            if label.startswith("応募数") and tendered is None:
                tendered = integer(vals[0])
            elif label.startswith("買付数") and purchased is None:
                purchased = integer(vals[0])
        if tendered is not None or purchased is not None:
            return tendered, purchased
    return None, None


def parse_success(block: Block | None) -> bool | None:
    """公開買付けの成否。不成立・「全部の買付け等を行いません」を先に見る。"""
    if block is None:
        return None
    text = squash(" ".join(t for t in block.texts if "成否" not in t) or block.text())
    if "不成立" in text or "買付け等を行いません" in text or "買付けを行いません" in text:
        return False
    if "成立" in text or "買付け等を行います" in text or "買付けを行います" in text:
        return True
    return None


def opinion_text(block: Block | None) -> str | None:
    """意見の内容（「（１）意見の内容」と「（２）意見の根拠」の間の文）。"""
    if block is None:
        return None
    out: list[str] = []
    on = False
    for t in block.texts:
        s = squash(t)
        if "意見の内容" in s and ("【" in s or _OPINION_HEADING_RE.match(s)):
            on = True
            continue
        if on and ("意見の根拠" in s or s.startswith("(2)")):
            break
        if on:
            out.append(t)
    if not out:                      # 見出しの形が違う書類は最初の段落
        out = [t for t in block.texts[:3] if "【" not in t]
    text = " ".join(out).strip()
    return text[:2000] or None


def classify_opinion(text: str | None) -> tuple[str | None, str | None]:
    """意見の内容 → (opinion, recommendation)。"""
    if not text:
        return None, None
    s = squash(text)
    # 「中立」はふつう応募についての立場（賛同しつつ応募は株主の判断に委ねる）。賛同を先に見る
    agree = s.replace("賛同するか否か", "")
    if re.search(r"賛同(しない|できない|いたしかね)|反対", agree):
        opinion = "oppose"
    elif "賛同" in agree:
        opinion = "support"
    elif "留保" in s:
        opinion = "reserved"
    elif "中立" in s:
        opinion = "neutral"
    else:
        opinion = None
    if re.search(r"応募(しない|を見送る)(こと|よう)を?(推奨|勧め)|応募を推奨(しない|いたしません|しません)", s):
        rec = "not_recommend"
    elif re.search(r"応募(すること|するよう)を?(推奨|勧め)|応募を(推奨|勧め)", s):
        rec = "recommend"
    elif "判断に委ね" in s:
        rec = "leave"
    else:
        rec = None
    return opinion, rec


def _target_name(block: Block | None) -> str | None:
    if block is None:
        return None
    names = [t for t in block.texts if "【" not in t]
    return names[-1].strip() if names else None


def _ratio(inst: Instance) -> float | None:
    v = number(inst.value("HoldingRatioOfShareCertificatesEtcAfterPurchaseEtc"))
    return None if v is None else float(v)


def parse_document(doc_type: str, inst: Instance, *, issuer: bool) -> dict:
    """書類種別ごとに読む値。読めない欄は NULL。"""
    out: dict = {}
    if doc_type in NOTIFICATION:
        out["period_start"], out["period_end"] = parse_period(inst.block("PeriodOfPurchaseEtcTextBlock"))
        out["price"] = parse_price(inst.block("PriceOfPurchaseEtcTextBlock"))
        out["planned_shares"], out["min_shares"], out["max_shares"] = parse_planned(
            inst.block("NumberOfShareCertificatesEtcIntendedToPurchaseTextBlock",
                       "NumberOfListedShareCertificatesEtcIntendedToPurchaseTextBlock"),
            issuer=issuer)
        out["holding_ratio_after"] = _ratio(inst)
        out["target_name"] = _target_name(inst.block("NameOfSubjectCompanyTextBlock"))
        if out["period_end"] is None and out["price"] is None and out["planned_shares"] is None:
            raise ValueError(f"届出書の期間・価格・予定数が読めない: {inst.name}")
    elif doc_type in REPORT:
        out["period_start"], out["period_end"] = parse_period(inst.block("TenderOfferPeriodTextBlock"))
        out["tendered_shares"], out["purchased_shares"] = parse_result(
            inst.block("NumberOfShareCertificatesEtcAcquiredByPurchaseEtcTextBlock",
                       "NumberOfListedShareCertificatesEtcAcquiredByPurchaseEtcTextBlock"))
        out["success"] = parse_success(inst.block("SuccessOrFailureOfTenderOfferTextBlock"))
        if out["success"] is False and out["purchased_shares"] is None:
            out["purchased_shares"] = 0                    # 不成立の買付数は「―」
        if out["success"] is None and issuer and out["purchased_shares"] is not None:
            out["success"] = out["purchased_shares"] > 0   # 発行者の TOB は下限が無い
        out["holding_ratio_after"] = _ratio(inst)
        out["target_name"] = _target_name(inst.block("NameOfSubjectCompanyTextBlock"))
        if out["tendered_shares"] is None and out["success"] is None:
            raise ValueError(f"報告書の応募数・成否が読めない: {inst.name}")
    elif doc_type in OPINION:
        text = opinion_text(inst.block(*OPINION_BLOCKS))
        out["opinion_text"] = text
        out["opinion"], out["recommendation"] = classify_opinion(text)
        if text is None:
            raise ValueError(f"意見の内容が読めない: {inst.name}")
    return out


# ---- 取込 -----------------------------------------------------------------

def keep_after(today: date | None = None) -> date:
    return years_ago(today or now_jst().date(), KEEP_YEARS)


def _is_issuer(doc: EdinetDocument) -> bool:
    return doc.ordinance_code == ISSUER_ORDINANCE


def base_row(doc: EdinetDocument, fetched_at: datetime) -> dict:
    row = {c.name: None for c in EdinetTenderOfferDoc.__table__.columns}
    row.update({
        "doc_id": doc.doc_id, "tender_offer_id": doc.doc_id,   # refresh_offers が付け直す
        "file_date": doc.file_date, "submit_datetime": doc.submit_datetime,
        "doc_type_code": doc.doc_type_code, "ordinance_code": doc.ordinance_code,
        "form_code": doc.form_code, "title": doc.doc_description,
        "filer_edinet_code": doc.edinet_code, "filer_name": doc.filer_name,
        "subject_edinet_code": doc.subject_edinet_code, "parent_doc_id": doc.parent_doc_id,
        "withdrawn": False, "fetched_at": fetched_at,
    })
    return row


def _candidates(db: Session):
    T = EdinetDocument
    return select(T).where(T.doc_type_code.in_(DOC_TYPES), T.withdrawal_status != "2",
                           T.disclosure_status == "0", T.doc_info_edit_status != "1")


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
    """取り込む書類（240〜340）。取下げ/不開示でない元の行。新しい日から。"""
    T = EdinetDocument
    floor = keep_after(today)
    if start and start > floor:
        floor = start
    q = _candidates(db).where(T.file_date >= floor).order_by(T.file_date.desc(), T.seq_number)
    if end:
        q = q.where(T.file_date <= end)
    status = dict(db.execute(select(EdinetTenderOfferDoc.doc_id, EdinetTenderOfferDoc.parse_status)).all())
    out, seen = [], set()
    for d in db.scalars(q):
        if d.doc_id in seen:
            continue
        seen.add(d.doc_id)
        st = status.get(d.doc_id)
        if st in ("parsed", "meta") and not reparse:
            continue
        if st == "failed" and not (retry_failed or reparse):
            continue
        out.append(d)
        if limit and len(out) >= limit:
            break
    return out


def _store(db: Session, row: dict) -> None:
    run_sqlite_write(db, lambda: upsert(db, EdinetTenderOfferDoc, [row], commit=False))


def store_failed(db: Session, doc: EdinetDocument, exc: Exception, fetched_at: datetime) -> None:
    row = base_row(doc, fetched_at)
    row.update({"parse_status": "failed", "parse_error": f"{type(exc).__name__}: {exc}"[:2000]})
    _store(db, row)


def mark_outside_window(db: Session, *, today: date | None = None) -> int:
    """縦覧期間より前で、まだ行の無い書類を残す。中身を読む書類は failed、ほかは meta。"""
    cutoff = keep_after(today)
    have = set(db.scalars(select(EdinetTenderOfferDoc.doc_id)))
    seen: set[str] = set()
    n = 0
    fetched_at = now_jst()
    for doc in db.scalars(_candidates(db).where(EdinetDocument.file_date < cutoff)):
        if doc.doc_id in seen or doc.doc_id in have:
            continue
        seen.add(doc.doc_id)
        if doc.doc_type_code in PARSED_TYPES:
            store_failed(db, doc, OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
                         fetched_at)
            n += 1
        else:
            _store(db, {**base_row(doc, fetched_at), "parse_status": "meta"})
    return n


def ingest_document(db: Session, client: EdinetClient, doc: EdinetDocument,
                    *, today: date | None = None) -> str:
    """1 書類。戻り値は parsed / meta / failed / missing / error。"""
    from app.edinet_wrapper import DocumentFileType

    if doc.doc_type_code not in PARSED_TYPES:
        _store(db, {**base_row(doc, now_jst()), "parse_status": "meta"})
        return "meta"
    if doc.file_date < keep_after(today):
        store_failed(db, doc, OutsideEdinetWindow(f"file_date={doc.file_date} keep_years={KEEP_YEARS}"),
                     now_jst())
        return "missing"
    if not doc.xbrl_flag:
        store_failed(db, doc, ValueError("XBRL が無い"), now_jst())
        return "failed"
    try:
        got = client.download(doc.doc_id, DocumentFileType.MAIN)
    except Exception as exc:
        reraise(exc)
        if is_not_found(exc):
            store_failed(db, doc, exc, now_jst())
            return "missing"
        return "error"
    fetched_at = now_jst()
    try:
        inst = read_instance(got.content)
        values = parse_document(doc.doc_type_code, inst, issuer=_is_issuer(doc))
    except Exception as exc:
        store_failed(db, doc, exc, fetched_at)
        return "failed"
    row = base_row(doc, fetched_at)
    row.update(values)
    row["title"] = (inst.value("DocumentTitleCoverPage") or doc.doc_description or "").strip() or None
    row.update({"parse_status": "parsed", "parse_error": None})
    _store(db, row)
    shown = {k: v for k, v in values.items() if k != "opinion_text" and v is not None}
    print(f"{_LOG} parsed doc_id={doc.doc_id} type={doc.doc_type_code} "
          + " ".join(f"{k}={v}" for k, v in shown.items()), flush=True)
    return "parsed"


# ---- 案件 -----------------------------------------------------------------

def _newest(docs: list[EdinetTenderOfferDoc], types: tuple[str, ...]) -> list[EdinetTenderOfferDoc]:
    """その種別の parsed の書類を新しい順に。"""
    got = [d for d in docs if d.doc_type_code in types and d.parse_status == "parsed" and not d.withdrawn]
    return sorted(got, key=lambda d: (d.submit_datetime or datetime.min, d.doc_id), reverse=True)  # noqa: DTZ901


def _first(docs: list[EdinetTenderOfferDoc], attr: str):
    """新しい順の書類で最初に値がある欄（訂正届出書が欄を持たなければ前の書類の値）。"""
    for d in docs:
        v = getattr(d, attr)
        if v is not None:
            return v
    return None


def offer_row(case_id: str, docs: list[EdinetTenderOfferDoc], metas: dict[str, DocMeta],
              lookup: CodeLookup, names: dict[str, str], updated_at: datetime) -> dict:
    """1 案件の行。届出書・訂正は新しい方の値、意見・結果はいちばん新しい書類。"""
    alive = [d for d in docs if not d.withdrawn]
    notif = _newest(docs, NOTIFICATION)
    first_notif = sorted((d for d in alive if d.doc_type_code == "240"),
                         key=lambda d: d.submit_datetime or datetime.min)  # noqa: DTZ901
    base = first_notif[0] if first_notif else None
    issuer = any(d.ordinance_code == ISSUER_ORDINANCE for d in docs)

    offeror = target = None
    target_sec = None
    for d in sorted(alive, key=lambda d: d.doc_type_code):
        if d.doc_type_code in OPINION:
            target = target or d.filer_edinet_code
            offeror = offeror or d.subject_edinet_code
            meta = metas.get(d.doc_id)
            target_sec = target_sec or (meta.doc.sec_code if meta else None)
        else:
            offeror = offeror or d.filer_edinet_code
            target = target or d.subject_edinet_code
    if base is not None:
        offeror, target = base.filer_edinet_code or offeror, base.subject_edinet_code or target
    if issuer:
        target = target or offeror

    def filer_name(code: str | None) -> str | None:
        if not code:
            return None
        for d in docs:
            if d.filer_edinet_code == code and d.filer_name:
                return d.filer_name
        return names.get(code)

    offeror_sec = None
    for d in docs:
        meta = metas.get(d.doc_id)
        if d.filer_edinet_code == offeror and meta and meta.doc.sec_code:
            offeror_sec = meta.doc.sec_code
            break

    reports = _newest(docs, REPORT)
    opinions = _newest(docs, OPINION)
    withdrawn = any(d.doc_type_code in WITHDRAWAL for d in alive)
    if withdrawn:
        status = "withdrawn"
    elif reports and reports[0].success is not None:
        status = "succeeded" if reports[0].success else "failed"
    else:
        status = "open"
    latest = max(docs, key=lambda d: (d.submit_datetime or datetime.min, d.doc_id))  # noqa: DTZ901
    return {
        "tender_offer_id": case_id,
        "is_issuer": issuer,
        "offeror_edinet_code": offeror,
        "offeror_name": filer_name(offeror),
        "offeror_code": lookup.code_for(offeror_sec, offeror),
        "target_edinet_code": target,
        "target_name": _first(notif + reports, "target_name") or filer_name(target),
        "target_code": lookup.code_for(target_sec, target),
        "announced_date": base.file_date if base else None,
        "period_start": _first(notif, "period_start") or _first(reports, "period_start"),
        "period_end": _first(reports, "period_end") or _first(notif, "period_end"),
        "price": _first(notif, "price"),
        "price_initial": base.price if base and base.parse_status == "parsed" else None,
        "planned_shares": _first(notif, "planned_shares"),
        "min_shares": _first(notif, "min_shares"),
        "max_shares": _first(notif, "max_shares"),
        "holding_ratio_after": _first(reports, "holding_ratio_after") or _first(notif, "holding_ratio_after"),
        "opinion": _first(opinions, "opinion"),
        "recommendation": _first(opinions, "recommendation"),
        "status": status,
        "tendered_shares": _first(reports, "tendered_shares"),
        "purchased_shares": _first(reports, "purchased_shares"),
        "result_date": reports[0].file_date if reports else None,
        "latest_doc_id": latest.doc_id,
        "doc_count": len(alive),
        "updated_at": updated_at,
    }


def _parties(docs: list[EdinetTenderOfferDoc]) -> tuple[str | None, str | None]:
    """(買付者, 対象者)。意見表明は提出者が対象者。"""
    for d in docs:
        if d.doc_type_code in OPINION:
            return d.subject_edinet_code, d.filer_edinet_code
        if d.filer_edinet_code:
            return d.filer_edinet_code, d.subject_edinet_code
    return None, None


def _merge_orphans(groups: dict[str, list[EdinetTenderOfferDoc]],
                   metas: dict[str, DocMeta]) -> dict[str, list[EdinetTenderOfferDoc]]:
    """大元が DB に無い案件（届出書が取込の範囲より前）を、買付者・対象者が同じもの同士で束ねる。

    訂正意見表明（300）の親は意見表明（290）なので、290 も 240 も無いと届出書の系統と別の大元になる。
    大元は届出書・報告書の系統（親が届出書）の方にする。
    """
    orphans: dict[tuple, list[str]] = {}
    for root, docs in groups.items():
        if root in metas or any(d.doc_id == root for d in docs):
            continue
        key = _parties(docs)
        if key[0] and key[1]:
            orphans.setdefault(key, []).append(root)
    for roots in orphans.values():
        if len(roots) < 2:
            continue
        roots.sort(key=lambda r: (not any(d.doc_type_code not in OPINION for d in groups[r]), r))
        keep = roots[0]
        for r in roots[1:]:
            groups[keep] += groups.pop(r)
    return groups


def refresh_offers(db: Session) -> int:
    """書類の大元・取下げを付け直し、案件の表を全件から作り直す。戻り値は案件の数。

    大元は parent_doc_id を届出書（240）までたどる。親が DB に無ければ親の docID を大元にする
    （同じ親を持つ書類は同じ案件にまとまる）。
    """
    from app.models import EdinetFiler

    metas = doc_metas(db, DOC_TYPES)
    docs = db.scalars(select(EdinetTenderOfferDoc)).all()
    parent: dict[str, str | None] = {}
    for k, m in metas.items():
        parent[k] = None if m.doc.doc_type_code == "240" else m.doc.parent_doc_id
    for d in docs:
        parent.setdefault(d.doc_id, None if d.doc_type_code == "240" else d.parent_doc_id)
    groups: dict[str, list[EdinetTenderOfferDoc]] = {}
    for d in docs:
        meta = metas.get(d.doc_id)
        root = chain_root(d.doc_id, parent)
        want = {
            "tender_offer_id": root,
            "withdrawn": bool(meta and meta.gone),
            # 書類情報修正があれば新しいメタデータ（対象者の付け替えなど）
            "subject_edinet_code": meta.doc.subject_edinet_code if meta else d.subject_edinet_code,
            "filer_name": (meta.doc.filer_name if meta else None) or d.filer_name,
            "parent_doc_id": meta.doc.parent_doc_id if meta else d.parent_doc_id,
        }
        for k, v in want.items():
            setattr(d, k, v)
        groups.setdefault(root, []).append(d)
    groups = _merge_orphans(groups, metas)
    for root, members in groups.items():
        for d in members:
            d.tender_offer_id = root
    lookup = CodeLookup.load(db)
    names = dict(db.execute(select(EdinetFiler.edinet_code, EdinetFiler.filer_name)).all())
    now = now_jst()
    rows = [offer_row(k, v, metas, lookup, names, now) for k, v in groups.items()]

    def write():
        db.flush()
        db.execute(delete(EdinetTenderOffer))
        upsert(db, EdinetTenderOffer, rows, commit=False)

    run_sqlite_write(db, write)
    return len(rows)


def run(db: Session, client: EdinetClient, docs: list[EdinetDocument],
        *, today: date | None = None) -> dict:
    out = {"parsed": 0, "meta": 0, "failed": 0, "missing": 0, "errors": 0}
    ids: dict[str, list[str]] = {"missing": [], "failed": [], "error": []}
    for doc in docs:
        st = ingest_document(db, client, doc, today=today)
        out[st if st in out else "errors"] += 1
        if st in ("missing", "failed"):
            ids[st].append(doc.doc_id)
        elif st == "error":
            ids["error"].append(doc.doc_id)
    for key, tag in (("missing", "missing"), ("failed", "parse-error"), ("error", "download-error")):
        if ids[key]:
            print(f"{_LOG} {tag} n={len(ids[key])} sample={sample_ids(ids[key])}", flush=True)
    out["offers"] = refresh_offers(db)
    return out


def exit_status(st: dict) -> int:
    """cron の終了コード。パース失敗と一時的な download 失敗だけ 1。404 の missing は 0。"""
    return 0 if st["failed"] + st["errors"] == 0 else 1

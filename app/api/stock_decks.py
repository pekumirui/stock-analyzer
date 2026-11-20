"""決算説明資料の一覧と、読み取った中身（KAB-350。docs/screen/stock.md）。ニュースが一覧を使い、行から中身を開く。

中身は jobs/market/briefing_extract.py（KAB-349）が書いた briefing_pages の表と要約。原本の PDF は返さない
（VPS の容量。原本は url のリンクで開く）。画面がグラフと表に組み直す（static/js/stock-deck.js）。
"""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..market.briefing_batch import SOURCE_ORDER, request_key
from ..models import BriefingDocument, BriefingExtract, BriefingFact, BriefingPage, Stock
from .monthly._common import ApiError, _parse_code

router = APIRouter()
DECKS_LIMIT = 200


def _rank(source: str) -> int:
    return SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)


def _documents(db: Session, code5: str) -> list[BriefingDocument]:
    """銘柄の資料。同じ開示番号が複数の出所にあれば 1 つ（tdnet → irbank → homepage）。新しい開示から。"""
    rows = db.execute(select(BriefingDocument).where(BriefingDocument.code == code5,
                                                     BriefingDocument.status == "saved")).scalars().all()
    best: dict[str, BriefingDocument] = {}
    for r in rows:
        cur = best.get(r.document_id)
        if cur is None or _rank(r.source) < _rank(cur.source):
            best[r.document_id] = r
    return sorted(best.values(), key=lambda r: (r.pubdate, r.document_id), reverse=True)[:DECKS_LIMIT]


@router.get("/stock/{code}/decks")
def stock_decks(code: str, db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    if db.get(Stock, code5) is None:
        raise ApiError(404, "NOT_FOUND", "銘柄がありません。")
    docs = _documents(db, code5)
    ext = {e.document_id: e for e in db.execute(
        select(BriefingExtract).where(BriefingExtract.document_id.in_([d.document_id for d in docs]))).scalars()}
    out = []
    for d in docs:
        e = ext.get(d.document_id)
        out.append({
            "key": request_key(d.document_id), "pubdate": d.pubdate, "title": d.title, "url": d.url,
            "status": e.status if e else None, "pages": e.pages if e else None, "facts": e.facts if e else None,
        })
    return {"code": code5, "decks": out}


@router.get("/stock/{code}/decks/{key}")
def stock_deck(code: str, key: str, db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    doc = next((d for d in _documents(db, code5) if request_key(d.document_id) == key), None)
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "説明資料がありません。")
    e = db.get(BriefingExtract, doc.document_id)
    head = {"key": key, "pubdate": doc.pubdate, "title": doc.title, "url": doc.url, "status": e.status if e else None}
    if e is None or e.status != "done":
        return {"document": head, "extract": None, "pages": []}
    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"unverified": 0, "mismatch": 0})
    for page, unverified, mismatch in db.execute(
            select(BriefingFact.page,
                   func.sum(case((BriefingFact.verified, 0), else_=1)),
                   func.sum(case((BriefingFact.sum_status == "mismatch", 1), else_=0)))
            .where(BriefingFact.document_id == doc.document_id).group_by(BriefingFact.page)):
        counts[page] = {"unverified": int(unverified or 0), "mismatch": int(mismatch or 0)}
    pages = []
    for p in db.execute(select(BriefingPage).where(BriefingPage.document_id == doc.document_id)
                        .order_by(BriefingPage.page)).scalars():
        pages.append({
            "page": p.page, "kind": p.kind, "title": p.title, "summary": p.summary,
            "tables": json.loads(p.tables_json) if p.tables_json else [], "error": p.error, **counts[p.page],
        })
    return {
        "document": head,
        "extract": {"model": e.model, "prompt_version": e.prompt_version, "pages": e.pages, "llm_pages": e.llm_pages,
                    "facts": e.facts, "verified": e.verified, "sum_checks": e.sum_checks,
                    "sum_mismatch": e.sum_mismatch, "extracted_at": e.extracted_at.isoformat() if e.extracted_at else None},
        "pages": pages,
    }

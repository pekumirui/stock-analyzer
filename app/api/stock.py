"""銘柄タブ（KAB-161。docs/DESIGN_stock.md）。銘柄の短信一覧と原文 HTML。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Stock, XbrlDocument, XbrlFiling
from ..xbrl.documents import load_html, sanitize_html
from .monthly._common import ApiError, _parse_code

router = APIRouter()

FILINGS_LIMIT = 200
# 会社が作った HTML。script・外への通信・フォーム・ポップアップを止める。
# allow-same-origin は親が高さを測るため（script は動かないので中から親には触れない）
DOC_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
    "frame-ancestors 'self'; sandbox allow-same-origin"
)


@router.get("/stock/{code}/filings")
def stock_filings(code: str, db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    stock = db.get(Stock, code5)
    if stock is None:
        raise ApiError(404, "NOT_FOUND", "銘柄がありません。")
    filings = (
        db.query(XbrlFiling)
        .filter(XbrlFiling.code == code5, XbrlFiling.source == "tdnet")
        .order_by(XbrlFiling.submitted_at.desc(), XbrlFiling.id.desc())
        .limit(FILINGS_LIMIT)
        .all()
    )
    parts: dict[int, list[dict]] = {}
    if filings:
        rows = (
            db.query(XbrlDocument.filing_id, XbrlDocument.seq, XbrlDocument.part, XbrlDocument.file_name)
            .filter(XbrlDocument.filing_id.in_([f.id for f in filings]))
            .order_by(XbrlDocument.filing_id, XbrlDocument.seq)
            .all()
        )
        for fid, seq, part, name in rows:
            parts.setdefault(fid, []).append({"seq": seq, "part": part, "file_name": name})
    return {
        "code": stock.code,
        "name": stock.name,
        "market": stock.market,
        "sector33": stock.sector33,
        "filings": [
            {
                "id": f.id,
                "submitted_at": f.submitted_at.isoformat(sep=" ", timespec="minutes") if f.submitted_at else None,
                "title": f.title,
                "doc_type": f.doc_type,
                "period_type": f.period_type,
                "is_amendment": bool(f.is_amendment),
                "documents": parts.get(f.id, []),
            }
            for f in filings
        ],
    }


@router.get("/stock/filings/{filing_id}/documents/{seq}", response_class=HTMLResponse)
def stock_document(filing_id: int, seq: int, request: Request, db: Session = Depends(get_session)):
    doc = db.get(XbrlDocument, (filing_id, seq))
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "原文がありません。")
    etag = f'"{doc.sha256}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "Content-Security-Policy": DOC_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(sanitize_html(load_html(doc)), headers=headers)

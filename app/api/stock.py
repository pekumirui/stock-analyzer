"""銘柄タブ（KAB-161。docs/DESIGN_stock.md）。銘柄の短信一覧と原文 HTML。

原文は 2 通り: TDnet XBRL ZIP の HTML（xbrl_documents）と、それが無い過去の回を IR BANK の PDF から
変換した HTML（tdnet_pdf_documents。KAB-164）。
"""
from __future__ import annotations

import gzip
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Stock, TdnetPdfDocument, XbrlDocument, XbrlFiling
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
PDF_SOURCE = "IR BANK（TDnet）"
_QUARTER_TITLE = re.compile(r"第\s*[1-3１-３]\s*四半期|中間")


def _pdf_doc_type(title: str | None) -> str:
    """変換した回の題名 → xbrl_filings.doc_type と同じ語（既定の書類を選ぶため）。"""
    t = title or ""
    if "決算短信" not in t and "Financial" not in t:
        return "other"
    return "quarterly" if _QUARTER_TITLE.search(t) else "annual"


def _doc_headers(sha: str) -> dict[str, str]:
    return {
        "ETag": f'"{sha}"',
        "Cache-Control": "private, no-cache",
        "Content-Security-Policy": DOC_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


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
    out = [
        {
            "id": f.id,
            "source": "xbrl",
            "submitted_at": f.submitted_at.isoformat(sep=" ", timespec="minutes") if f.submitted_at else None,
            "title": f.title,
            "doc_type": f.doc_type,
            "period_type": f.period_type,
            "is_amendment": bool(f.is_amendment),
            "documents": [
                {**d, "url": f"/api/stock/filings/{f.id}/documents/{d['seq']}"} for d in parts.get(f.id, [])
            ],
        }
        for f in filings
    ]
    have_html = {f.doc_id[-14:] for f in filings if parts.get(f.id)}
    pdfs = (
        db.query(
            TdnetPdfDocument.disclosure_no, TdnetPdfDocument.disclosed_date, TdnetPdfDocument.disclosed_time,
            TdnetPdfDocument.title, TdnetPdfDocument.pages,
        )
        .filter(TdnetPdfDocument.code == code5, TdnetPdfDocument.status == "ok")
        .all()
    )
    for no, day, tm, title, pages in pdfs:
        if no in have_html:
            continue
        at = f"{day.isoformat()} {(tm or '')[:5]}".strip() if day else None
        out.append({
            "id": None,
            "source": "pdf",
            "disclosure_no": no,
            "submitted_at": at,
            "title": title,
            "doc_type": _pdf_doc_type(title),
            "period_type": None,
            "is_amendment": "訂正" in (title or ""),
            "attribution": PDF_SOURCE,
            "documents": [{"seq": 0, "part": "pdf", "pages": pages, "url": f"/api/stock/pdf/{no}"}],
        })
    out.sort(key=lambda x: x["submitted_at"] or "", reverse=True)
    return {
        "code": stock.code,
        "name": stock.name,
        "market": stock.market,
        "sector33": stock.sector33,
        "filings": out[:FILINGS_LIMIT],
    }


@router.get("/stock/filings/{filing_id}/documents/{seq}", response_class=HTMLResponse)
def stock_document(filing_id: int, seq: int, request: Request, db: Session = Depends(get_session)):
    doc = db.get(XbrlDocument, (filing_id, seq))
    if doc is None:
        raise ApiError(404, "NOT_FOUND", "原文がありません。")
    headers = _doc_headers(doc.sha256)
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(sanitize_html(load_html(doc)), headers=headers)


@router.get("/stock/pdf/{disclosure_no}", response_class=HTMLResponse)
def stock_pdf_document(disclosure_no: str, request: Request, db: Session = Depends(get_session)):
    """IR BANK の PDF から変換した原文（tools/tdnet_pdf）。中身は変換器が作った SVG。"""
    doc = db.get(TdnetPdfDocument, disclosure_no)
    if doc is None or doc.status != "ok" or doc.html_gz is None:
        raise ApiError(404, "NOT_FOUND", "原文がありません。")
    headers = _doc_headers(doc.sha256)
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return HTMLResponse(sanitize_html(gzip.decompress(doc.html_gz).decode("utf-8")), headers=headers)

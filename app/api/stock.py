"""銘柄タブ（KAB-161 / KAB-177。docs/screen/stock.md）。共通ヘッダーと指標、短信一覧と原文 HTML。

原文は 2 通り: TDnet XBRL ZIP の HTML（xbrl_documents）と、それが無い過去の回を IR BANK の PDF から
変換した HTML（tdnet_pdf_documents。KAB-164）。
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..db import get_session
from ..holders import stock_holders as holders_payload
from ..market.chart_fund import adjust_per_share, fund_steps
from ..market.forecasts import Withdrawn, forecast_points, latest_forecast
from ..market.withdrawals import withdrawn_items
from ..models import DIVIDEND_ITEM, DailyPrice, EarningsDatePub, Statement, Stock, TdnetPdfDocument, XbrlDocument, XbrlFiling
from ..xbrl.documents import SANITIZE_VERSION, load_html, sanitize_html
from ..xbrl.three_statements import DEFAULT_PERIODS, MAX_PERIODS, financial_table
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
        "ETag": f'"{sha}.s{SANITIZE_VERSION}"',
        "Cache-Control": "private, no-cache",
        "Content-Security-Policy": DOC_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def _num(raw: dict, key: str) -> float | None:
    v = raw.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _statements_newest_first(db: Session, code5: str) -> list[tuple[Statement, dict]]:
    rows = (
        db.query(Statement)
        .filter(Statement.code == code5)
        .order_by(Statement.disclosed_date.desc(), Statement.disclosed_time.desc(),
                  Statement.disclosure_no.desc())
        .all()
    )
    out = []
    for s in rows:
        try:
            raw = json.loads(s.raw_json) if s.raw_json else {}
        except ValueError:
            raw = {}
        out.append((s, raw))
    return out


def _is_actual(s: Statement) -> bool:
    return "FinancialStatements" in (s.type_of_document or "")


def _is_fy_actual(s: Statement) -> bool:
    return _is_actual(s) and (s.type_of_document or "").startswith("FY")


def _forecast(stmts: list[tuple[Statement, dict]], key: str) -> tuple[float | None, date | None]:
    """今期の予想と開示日。新しい順に見て、決算短信本体ならそこで決める（本決算は来期予想 NxF*）。
    予想修正だけの開示は値があるときだけ使う。"""
    for s, raw in stmts:
        if _is_fy_actual(s):
            return _num(raw, f"NxF{key}"), s.disclosed_date
        if _is_actual(s):
            return _num(raw, f"F{key}"), s.disclosed_date
        v = _num(raw, f"F{key}")
        if v is not None:
            return v, s.disclosed_date
    return None, None


def _latest_with_date(stmts: list[tuple[Statement, dict]], key: str,
                      *, fy_only: bool = False) -> tuple[float | None, date | None]:
    for s, raw in stmts:
        if not _is_actual(s) or (fy_only and not _is_fy_actual(s)):
            continue
        v = _num(raw, key)
        if v is not None:
            return v, s.disclosed_date
    return None, None


def _latest(stmts: list[tuple[Statement, dict]], key: str, *, fy_only: bool = False) -> float | None:
    return _latest_with_date(stmts, key, fy_only=fy_only)[0]


def _per_share_now(value: float | None, disclosed: date | None, days: list[date],
                   factors: list[float]) -> float | None:
    """開示時点の 1 株あたりの値を、その後の分割・併合で今の株数にそろえる。"""
    if value is None or disclosed is None:
        return value
    return adjust_per_share(value, disclosed, days, factors, None)


def _dividend_forecast(stmts: list[Statement], days: list[date], factors: list[float],
                       withdrawn: Withdrawn = withdrawn_items) -> float | None:
    div = latest_forecast(forecast_points(stmts, withdrawn), DIVIDEND_ITEM)
    return _per_share_now(div.value, div.disclosed_date, days, factors)


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def _quote(db: Session, code5: str) -> dict | None:
    rows = (
        db.query(DailyPrice)
        .filter(DailyPrice.code == code5, DailyPrice.close.isnot(None))
        .order_by(DailyPrice.date.desc())
        .limit(2)
        .all()
    )
    if not rows:
        return None
    cur = rows[0]
    change = change_pct = None
    if len(rows) == 2:
        prev = rows[1]
        # 分割の日も前日比がずれないよう、率は調整後の終値で出す
        if cur.adj_close and prev.adj_close:
            change_pct = cur.adj_close / prev.adj_close - 1
            change = round(cur.close - cur.close / (1 + change_pct), 4)
        elif prev.close:
            change = cur.close - prev.close
            change_pct = change / prev.close
    return {
        "date": cur.date.isoformat(),
        "close": cur.close,
        "change": change,
        "change_pct": change_pct,
        "market_cap": cur.market_cap,
    }


def _next_earnings(db: Session, code5: str, today: date) -> dict | None:
    """四半期ごとに最新の pub_date の行を取り、今日以降の予定日のうち一番近いもの。"""
    latest: dict[str, EarningsDatePub] = {}
    for r in db.query(EarningsDatePub).filter(EarningsDatePub.code == code5).all():
        cur = latest.get(r.fq_name)
        if cur is None or r.pub_date > cur.pub_date:
            latest[r.fq_name] = r
    if not latest:
        return None
    upcoming = sorted((r.sch_date, r.fq_name) for r in latest.values() if r.sch_date and r.sch_date >= today)
    if upcoming:
        d, fq = upcoming[0]
        return {"date": d.isoformat(), "fq": fq}
    if any(r.sch_date is None for r in latest.values()):
        return {"date": None, "fq": None}
    return None


@router.get("/stock/{code}/overview")
def stock_overview(code: str, today: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                   db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    stock = db.get(Stock, code5)
    if stock is None:
        raise ApiError(404, "NOT_FOUND", "銘柄がありません。")
    quote = _quote(db, code5)
    stmts = _statements_newest_first(db, code5)
    close = quote["close"] if quote else None
    px = db.query(DailyPrice.date, DailyPrice.adj_factor).filter(DailyPrice.code == code5).order_by(DailyPrice.date).all()
    days, factors = [r[0] for r in px], [r[1] for r in px]
    rows = [s for s, _ in stmts]
    steps = fund_steps(rows, days, factors)["eps"]
    eps = steps[-1] if steps else None
    div = _dividend_forecast(rows, days, factors)
    bps = _per_share_now(*_latest_with_date(stmts, "BPS"), days, factors)
    metrics = {
        "per": _ratio(close, eps) if eps and eps > 0 else None,
        "pbr": _ratio(close, bps),
        "dividend_yield": _ratio(div, close),
        "roe": _latest(stmts, "ROE", fy_only=True),
        "equity_ratio": _latest(stmts, "EqAR"),
    }
    day = date.fromisoformat(today) if today else date.today()
    return {
        "code": stock.code,
        "name": stock.name,
        "market": stock.market,
        "sector33": stock.sector33,
        "quote": quote,
        "next_earnings": _next_earnings(db, code5, day),
        "metrics": metrics,
    }


@router.get("/stock/{code}/holders")
def stock_holders(code: str, today: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                  db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    stock = db.get(Stock, code5)
    if stock is None:
        raise ApiError(404, "NOT_FOUND", "銘柄がありません。")
    day = date.fromisoformat(today) if today else date.today()
    return holders_payload(db, code5, today=day)


@router.get("/stock/{code}/financials")
def stock_financials(code: str, statement: str = Query("BS", pattern=r"^(BS|PL|CF)$"),
                     periods: int = Query(DEFAULT_PERIODS, ge=1, le=MAX_PERIODS),
                     consolidated: str = Query("auto", pattern=r"^(auto|true|false)$"),
                     db: Session = Depends(get_session)):
    code5 = _parse_code(code)
    stock = db.get(Stock, code5)
    if stock is None:
        raise ApiError(404, "NOT_FOUND", "銘柄がありません。")
    basis = None if consolidated == "auto" else consolidated == "true"
    table = financial_table(db, code5, statement=statement, periods=periods, consolidated=basis)
    return {"code": stock.code, "name": stock.name, **table}


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

"""EDINET コードリスト → edinet_filers、証券コードで stocks.edinet_code を付ける。

設計は docs/jobs/edinet.md「提出者」。ラッパーは Python 3.11+ なので関数の中で import する。
"""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import upsert
from app.models import EdinetFiler, Stock

TOKYO_PRO_MARKET = "TOKYO PRO MARKET"
_LIST_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def parse_list_date(payload: bytes) -> date | None:
    """1 行目「ダウンロード実行日,2026年10月20日現在,…」の日付。読めなければ None。"""
    from app.edinet_wrapper.codelist import _zip_csv_text

    head = _zip_csv_text(payload).splitlines()[:1]
    m = _LIST_DATE_RE.search(head[0]) if head else None
    if not m:
        return None
    return date(int(m[1]), int(m[2]), int(m[3]))


def filer_rows(payload: bytes, now: datetime) -> list[dict]:
    from app.edinet_wrapper.codelist import parse_edinet_code_list

    list_date = parse_list_date(payload)
    rows = []
    for e in parse_edinet_code_list(payload):
        x = e.extra
        rows.append({
            "edinet_code": e.edinet_code,
            "sec_code": e.sec_code or None,
            "filer_name": e.filer_name,
            "filer_name_en": x.get("提出者名（英字）") or None,
            "filer_type": x.get("提出者種別") or None,
            "listed": x.get("上場区分") or None,
            "is_consolidated": x.get("連結の有無") or None,
            "capital": x.get("資本金") or None,
            "fiscal_year_end": x.get("決算日") or None,
            "industry": x.get("提出者業種") or None,
            "jcn": e.jcn,
            "list_date": list_date,
            "updated_at": now,
        })
    return rows


def ingest_code_list(db: Session, payload: bytes, now: datetime) -> int:
    rows = filer_rows(payload, now)
    upsert(db, EdinetFiler, rows, commit=False)
    db.commit()
    return len(rows)


def link_stocks(db: Session) -> dict:
    """stocks.edinet_code を edinet_filers.sec_code から付け直す。

    同じ証券コードに提出者が複数あるときは「上場」の行、次に list_date が新しい行。
    5 桁目が 0 でない銘柄（優先株式など）は普通株（5 桁目 0）の提出者を付ける。
    リストから外れた銘柄は NULL に戻す。TOKYO PRO MARKET は EDINET に出さないので数えない。
    """
    filers = db.execute(
        select(EdinetFiler.sec_code, EdinetFiler.edinet_code, EdinetFiler.listed,
               EdinetFiler.list_date)
        .where(EdinetFiler.sec_code.is_not(None))
    ).all()
    best: dict[str, tuple] = {}
    dup: set[str] = set()
    for sec, ec, listed, list_date in filers:
        rank = (listed == "上場", list_date or date.min, ec)
        if sec in best:
            dup.add(sec)
            if rank <= best[sec][0]:
                continue
        best[sec] = (rank, ec)

    stocks = db.execute(
        select(Stock.code, Stock.edinet_code, Stock.product_category, Stock.market)
    ).all()
    changed = linked = 0
    unmatched_equity: list[str] = []
    for code, cur, cat, market in stocks:
        hit = best.get(code) or best.get(code[:4] + "0")
        want = hit[1] if hit else None
        linked += want is not None
        if want is None and cat == "011" and market != TOKYO_PRO_MARKET:
            unmatched_equity.append(code)
        if want != cur:
            db.execute(update(Stock).where(Stock.code == code).values(edinet_code=want))
            changed += 1
    db.commit()
    return {
        "stocks": len(stocks),
        "linked": linked,
        "changed": changed,
        "duplicate_sec_codes": sorted(dup),
        "unmatched_equity": unmatched_equity,
    }

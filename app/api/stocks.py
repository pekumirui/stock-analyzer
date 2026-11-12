"""銘柄検索 /stocks/search。チャート・決算タブが共通で使う。"""
import unicodedata

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import STOCK_PRODUCT_CATEGORIES
from ..db import get_session
from ..edinet.codes import yomi_key, yomi_key_sql
from ..models import EdinetFiler, Stock


router = APIRouter()


@router.get("/stocks/search")
def search(q: str, db: Session = Depends(get_session)):
    q = q.strip()
    if not q:
        return []
    folded = unicodedata.normalize("NFKC", q)
    kata = "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in folded)
    conds = [Stock.name.contains(q), Stock.name.contains(folded), Stock.name.contains(kata),
             Stock.code.startswith(folded.upper()),
             func.lower(EdinetFiler.filer_name_en).contains(folded.lower(), autoescape=True)]
    yomi = yomi_key(kata)
    if yomi:
        conds.append(yomi_key_sql(EdinetFiler.filer_name_yomi).contains(yomi, autoescape=True))
    query = (db.query(Stock)
             .outerjoin(EdinetFiler, EdinetFiler.edinet_code == Stock.edinet_code)
             .filter(or_(*conds)))
    if db.query(Stock.code).filter(Stock.product_category.in_(STOCK_PRODUCT_CATEGORIES)).first():
        query = query.filter(Stock.product_category.in_(STOCK_PRODUCT_CATEGORIES))
    rows = query.order_by(Stock.code).limit(20).all()
    return [{"code": s.code, "name": s.name, "market": s.market} for s in rows]

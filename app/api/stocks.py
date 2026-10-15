"""銘柄検索 /stocks/search。チャート・決算タブが共通で使う。"""
import unicodedata

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Stock


router = APIRouter()


@router.get("/stocks/search")
def search(q: str, db: Session = Depends(get_session)):
    q = q.strip()
    if not q:
        return []
    # 社名はカタカナ表記が多いので、ひらがな入力もカタカナに寄せて引く。社名自体のひらがなは q のままで当たる
    folded = unicodedata.normalize("NFKC", q)
    kata = "".join(chr(ord(ch) + 0x60) if "\u3041" <= ch <= "\u3096" else ch for ch in folded)
    rows = (db.query(Stock).filter(
        Stock.name.contains(q) | Stock.name.contains(folded) | Stock.name.contains(kata)
        | Stock.code.startswith(folded.upper())).order_by(Stock.code).limit(20).all())
    return [{"code": s.code, "name": s.name, "market": s.market} for s in rows]

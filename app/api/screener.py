"""スクリーナータブ。/screener。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import RS_PRODUCT_CATEGORIES
from ..db import get_session
from ..models import Stock, Indicator


router = APIRouter()


@router.get("/screener")
def screener(stage: int | None = None, rs_min: int = 0, sort: str = "rs",
             limit: int = 100, db: Session = Depends(get_session)):
    latest = db.query(func.max(Indicator.date)).scalar()
    if latest is None:
        return []
    q = db.query(Indicator, Stock).join(Stock, Stock.code == Indicator.code) \
          .filter(Indicator.date == latest)
    if db.query(Stock.code).filter(Stock.product_category.in_(RS_PRODUCT_CATEGORIES)).first():
        q = q.filter(Stock.product_category.in_(RS_PRODUCT_CATEGORIES))
    if stage is not None:
        q = q.filter(Indicator.stage == stage)
    if rs_min:
        q = q.filter(Indicator.rs_rank >= rs_min)
    q = q.order_by(Indicator.rs_rank.desc() if sort == "rs" else Indicator.code)
    out = []
    for ind, st in q.limit(limit).all():
        out.append({"code": ind.code, "name": st.name, "sector": st.sector33,
                    "close": ind.close, "rs_rank": ind.rs_rank, "stage": ind.stage,
                    "ma50": ind.ma50, "ma150": ind.ma150, "ma200": ind.ma200,
                    "pct_from_low52": round((ind.close / ind.low_52w - 1) * 100, 1) if ind.low_52w else None,
                    "pct_from_high52": round((ind.close / ind.high_52w - 1) * 100, 1) if ind.high_52w else None})
    return {"date": latest.isoformat(), "results": out}

"""サプライタブ。/supply の素材グラフ・企業・月次量・国別。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import MaterialMonthly, MaterialTradeCountry
from ..supply.material_stats import TRADE_SOURCE
from ..supply.chain import country_payload, monthly_payload, supply_companies, supply_graph


router = APIRouter()


@router.get("/supply/graph")
def supply_graph_api():
    return supply_graph()


@router.get("/supply/companies")
def supply_companies_api():
    return supply_companies()


@router.get("/supply/monthly/{material_id}")
def supply_monthly_api(material_id: str, db: Session = Depends(get_session)):
    if material_id not in {m["id"] for m in supply_graph()["materials"]}:
        raise HTTPException(404, "unknown material")
    rows = db.query(MaterialMonthly).filter(MaterialMonthly.material_id == material_id).all()
    return monthly_payload(rows, material_id, _trade_months(db))


@router.get("/supply/countries/{material_id}")
def supply_countries_api(material_id: str, flow: str = "import", db: Session = Depends(get_session)):
    if material_id not in {m["id"] for m in supply_graph()["materials"]}:
        raise HTTPException(404, "unknown material")
    if flow not in ("import", "export"):
        raise HTTPException(400, "flow must be import or export")
    rows = (db.query(MaterialTradeCountry)
            .filter(MaterialTradeCountry.material_id == material_id, MaterialTradeCountry.flow == flow).all())
    return country_payload(rows, material_id, flow)


def _trade_months(db: Session) -> dict[str, dict[str, int]]:
    """貿易統計の公表済みの月。どれかの素材に行がある月を公表済みとみなす"""
    out: dict[str, dict[str, int]] = {}
    for metric, month, rev in (db.query(MaterialMonthly.metric, MaterialMonthly.month,
                                        func.min(MaterialMonthly.revision))
                               .filter(MaterialMonthly.source == TRADE_SOURCE)
                               .group_by(MaterialMonthly.metric, MaterialMonthly.month)):
        out.setdefault(metric, {})[month] = rev
    return out

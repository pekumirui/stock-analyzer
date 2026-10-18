"""修正履歴のデータ源: TDnet XBRL の正規化層 (xbrl_financials)。

予想は `value_kind=forecast` かつ `target_period=FY` (業績予想修正の開示を含む)。実績は
本決算 (`doc_type=annual`) の `value_kind=actual`。連結があれば連結だけ使う。レンジ予想は
point が無ければ下限と上限の中点。前回予想 (`forecast_previous`) は使わない (前の開示で拾える)。
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy.orm import Session

from app.models import XbrlFiling, XbrlFinancial, XbrlStdItem
from app.revisions import ACTUAL, FORECAST, RevisionPoint

# xbrl_std_items.code → statements の列名
STD_TO_METRIC = {"net_sales": "net_sales", "operating_income": "operating_profit",
                 "ordinary_income": "ordinary_profit", "net_income": "profit",
                 "eps_basic": "eps"}


def _pick(sides: dict[str, float]) -> float | None:
    if "point" in sides:
        return sides["point"]
    if "lower" in sides and "upper" in sides:
        return (sides["lower"] + sides["upper"]) / 2
    return None


def xbrl_points(db: Session, code: str, upto: date) -> list[RevisionPoint]:
    rows = (db.query(XbrlFinancial, XbrlFiling, XbrlStdItem.code)
            .join(XbrlFiling, XbrlFiling.id == XbrlFinancial.filing_id)
            .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
            .filter(XbrlFinancial.code == code,
                    XbrlStdItem.code.in_(STD_TO_METRIC),
                    XbrlFiling.submitted_at <= datetime.combine(upto, time.max),
                    XbrlFiling.parse_status == "parsed")
            .all())
    consolidated = {f.filing_id for f, _, _ in rows if f.is_consolidated}
    # (filing, kind, 期末) → 指標 → range_side → 値
    acc: dict[tuple, dict[str, dict[str, float]]] = {}
    filings: dict[int, XbrlFiling] = {}
    for fin, filing, std in rows:
        if fin.filing_id in consolidated and not fin.is_consolidated:
            continue
        if fin.value_kind == "forecast" and fin.target_period == "FY":
            kind = FORECAST
        elif fin.value_kind == "actual" and filing.doc_type == "annual" and filing.period_type == "FY":
            kind = ACTUAL
        else:
            continue
        filings[filing.id] = filing
        acc.setdefault((filing.id, kind, fin.period_end), {}) \
           .setdefault(STD_TO_METRIC[std], {})[fin.range_side] = float(fin.value)
    points = []
    for (fid, kind, fy_end), by_metric in acc.items():
        filing = filings[fid]
        values = {k: _pick(sides) for k, sides in by_metric.items()}
        points.append(RevisionPoint(fy_end, kind, filing.submitted_at.date(), values,
                                    (filing.submitted_at, filing.doc_id)))
    return points

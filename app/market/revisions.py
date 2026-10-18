"""修正履歴のデータ源: J-Quants の短信サマリ (statements)。

当期予想は各開示の `fy_*` (予想修正のみの開示を含む)。期初予想は前期の本決算の来期予想
(`raw_json` の `NxF*`)。実績は本決算 (FY / 4Q) の累計。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import EARNINGS_PARAMS as EP
from ..models import Statement
from ..revisions import ACTUAL, FORECAST, RevisionPoint
from .analytics import METRIC_KEYS, is_actual, metrics, next_year_forecast, statement_history


def statement_points(hist: list[Statement]) -> list[RevisionPoint]:
    points = []
    for s in hist:
        order = (s.disclosure_no or "",)
        fy = {k: getattr(s, "fy_" + k) for k in METRIC_KEYS}
        if s.fiscal_year_end and any(v is not None for v in fy.values()):
            points.append(RevisionPoint(s.fiscal_year_end, FORECAST, s.disclosed_date, fy, order))
        if s.type_of_period in ("FY", "4Q") and is_actual(s) and s.fiscal_year_end:
            points.append(RevisionPoint(s.fiscal_year_end, ACTUAL, s.disclosed_date, metrics(s), order))
            nx_end, _, nx = next_year_forecast(s)
            if nx_end and any(v is not None for v in nx.values()):
                points.append(RevisionPoint(nx_end, FORECAST, s.disclosed_date, nx, order))
    return points


def jquants_points(db: Session, stmt: Statement) -> list[RevisionPoint]:
    """選んだ開示の日までの開示から。前期本決算の来期予想を拾うため 1 年余分に遡る。"""
    hist = statement_history(db, stmt.code, stmt.disclosed_date,
                             366 * (EP["revision_history_years"] + 1))
    return statement_points(hist)

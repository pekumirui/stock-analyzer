"""決算速報の売上総利益（KAB-278）: statements の開示ごとに xbrl_financials の gross_profit を 1 つ選ぶ。

J-Quants の statements に売上総利益は無い。同じ銘柄・期末・累計期間（期首から）・連結区分の実績を、
1. その開示の短信（ZIP は doc_id の後ろ 14 桁、PDF は doc_id が開示番号）
2. 同じ期のほかの短信（提出が開示日以前で新しいもの。訂正短信の元など）
3. EDINET（有報・半期報・四半期報告書。最初に出たもの）
の順で選ぶ。ほかの列は短信の数字なので短信を先にする。EDINET は短信が無い回の穴埋め。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time

from sqlalchemy.orm import Session

from app.models import XbrlFiling, XbrlFinancial, XbrlStdItem

_TANSHIN = ("tdnet", "tdnet_pdf")


def _consolidated(stmt) -> bool:
    return "NonConsolidated" not in (stmt.type_of_document or "")


def _start(stmt):
    return stmt.period_start or stmt.fiscal_year_start


def gross_profit_query(db: Session, codes):
    """売上総利益の引き当て SELECT。covering index の EXPLAIN と共有する (KAB-295)。"""
    return (db.query(XbrlFinancial.code, XbrlFinancial.is_consolidated, XbrlFinancial.period_start,
                     XbrlFinancial.period_end, XbrlFinancial.value,
                     XbrlFiling.source, XbrlFiling.doc_id, XbrlFiling.submitted_at)
            .join(XbrlFiling, XbrlFiling.id == XbrlFinancial.filing_id)
            .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
            .filter(XbrlFinancial.code.in_(codes),
                    XbrlStdItem.code == "gross_profit",
                    XbrlFinancial.value_kind == "actual",
                    XbrlFiling.parse_status == "parsed"))


def gross_profit_map(db: Session, stmts) -> dict[str, float]:
    """開示番号 → 売上総利益（円）。値の無い開示は載らない。"""
    targets = [s for s in stmts
               if s.disclosure_no and s.period_end and "FinancialStatements" in (s.type_of_document or "")]
    if not targets:
        return {}
    codes = sorted({s.code for s in targets})
    rows = gross_profit_query(db, codes).all()
    by_key: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_key[(r.code, bool(r.is_consolidated), r.period_start, r.period_end)].append(r)

    out: dict[str, float] = {}
    for s in targets:
        cands = by_key.get((s.code, _consolidated(s), _start(s), s.period_end))
        if not cands:
            continue
        pick = _pick(cands, s)
        if pick is not None:
            out[s.disclosure_no] = float(pick.value)
    return out


def _pick(cands: list, stmt):
    no = stmt.disclosure_no
    own = [c for c in cands if c.source in _TANSHIN and (c.doc_id or "")[-14:] == no]
    if own:
        # 同じ開示に ZIP と PDF の両方は普通無い（ZIP が来たら PDF は消す）。あれば ZIP
        return min(own, key=lambda c: _TANSHIN.index(c.source))
    upto = datetime.combine(stmt.disclosed_date, time.max) if stmt.disclosed_date else None
    other = [c for c in cands if c.source in _TANSHIN and (upto is None or c.submitted_at <= upto)]
    if other:
        return max(other, key=lambda c: c.submitted_at)
    edinet = [c for c in cands if c.source == "edinet"]
    if edinet:
        return min(edinet, key=lambda c: c.submitted_at)
    return None


def attach_gross_profit(db: Session, stmts) -> None:
    """stmts の各要素に gross_profit 属性を付ける（無ければ None）。ORM の Statement か属性を持てる行。"""
    gp = gross_profit_map(db, stmts)
    for s in stmts:
        s.gross_profit = gp.get(s.disclosure_no)

"""セグメント注記の fact + mappings → xbrl_segments（KAB-77。docs/DESIGN_xbrl.md §14）。

対象は当期・`jpcrp_cor:OperatingSegmentsAxis` だけの次元（連結/単体の軸は付いてよい）。
科目は std_items の statement='SEG'。xbrl_financials の正規化はこの科目を使わない。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    XbrlContext,
    XbrlElement,
    XbrlElementMapping,
    XbrlFact,
    XbrlFiling,
    XbrlSegment,
    XbrlStdItem,
)

SEGMENT_STATEMENT = "SEG"
SEGMENT_AXES = {"OperatingSegmentsAxis"}
CONSOL_AXIS = "ConsolidatedOrNonConsolidatedAxis"
# 会社独自の調整額メンバー（トヨタ CorporateExpensesAndEliminationMember など）
RECONCILING_WORDS = ("Elimination", "Reconcil", "Adjustment", "Corporate")
# 標準メンバーの種類。会社独自のメンバーは上の語があれば reconciling、ReportableSegment があれば segment
STANDARD_MEMBER_KIND = {
    "ReportableSegmentsMember": "subtotal",
    "OperatingSegmentsNotIncludedInReportableSegmentsAndOtherRevenueGeneratingBusinessActivitiesMember": "other",
    "OtherReportableSegmentsMember": "other",
    "OtherOperatingSegmentsAxisMember": "other",
    "TotalOfReportableSegmentsAndOthersMember": "total",
    "ReconcilingItemsMember": "reconciling",
    "UnallocatedAmountsAndEliminationMember": "reconciling",
    "CorporateSharedMember": "reconciling",
}


def _local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def segment_axis_of(dimensions: dict[str, str]) -> str | None:
    """セグメント軸が 1 つだけで、ほかは連結/単体の軸だけなら、その軸。"""
    axes = [a for a in dimensions if _local(a) != CONSOL_AXIS]
    if len(axes) != 1 or _local(axes[0]) not in SEGMENT_AXES:
        return None
    return axes[0]


def is_segment_context(ctx) -> bool:
    """ParsedContext / XbrlContext どちらでも（dimensions か dimensions_json）。"""
    dims = getattr(ctx, "dimensions", None)
    if dims is None:
        dims = _dims(ctx.dimensions_json)
    return segment_axis_of(dims) is not None


def member_kind(member: str) -> str:
    local = _local(member)
    kind = STANDARD_MEMBER_KIND.get(local)
    if kind:
        return kind
    if any(w in local for w in RECONCILING_WORDS):
        return "reconciling"
    return "segment" if "ReportableSegment" in local else "unknown"


def _dims(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_segments(db: Session, filing: XbrlFiling, now: datetime | None = None) -> int:
    """filing の xbrl_segments を作り直す。戻り値は入れた行数。"""
    from app.xbrl.normalize import mapping_applies, skip_jppfs_on_ifrs_filing

    generated = now or datetime.now(timezone.utc).replace(tzinfo=None)
    std_items = {
        s.id: s for s in db.query(XbrlStdItem).filter_by(statement=SEGMENT_STATEMENT).all()
    }
    db.query(XbrlSegment).filter_by(filing_id=filing.id).delete(synchronize_session="fetch")
    db.flush()
    if not std_items:
        return 0
    by_elem: dict[tuple[str, str], list[XbrlElementMapping]] = {}
    for m in db.query(XbrlElementMapping).filter(
        XbrlElementMapping.std_item_id.in_(list(std_items))
    ):
        by_elem.setdefault((m.namespace_key, m.local_name), []).append(m)

    contexts = {}
    for c in db.query(XbrlContext).filter_by(filing_id=filing.id, has_dimensions=True):
        if c.relative_period != "current":
            continue
        dims = _dims(c.dimensions_json)
        axis = segment_axis_of(dims)
        if axis is not None:
            contexts[c.id] = (c, axis, dims[axis])
    if not contexts:
        return 0
    facts = (
        db.query(XbrlFact)
        .filter(XbrlFact.filing_id == filing.id, XbrlFact.context_id.in_(list(contexts)))
        .all()
    )
    element_ids = {f.element_id for f in facts}
    elements = {
        e.id: e for e in db.query(XbrlElement).filter(XbrlElement.id.in_(list(element_ids)))
    } if element_ids else {}

    chosen: dict[tuple, tuple] = {}
    for fact in facts:
        if fact.is_nil or fact.value_num is None:
            continue
        ctx, axis, member = contexts[fact.context_id]
        el = elements.get(fact.element_id)
        if el is None or skip_jppfs_on_ifrs_filing(filing, el):
            continue
        cands = [m for m in by_elem.get((el.namespace_key, el.local_name), [])
                 if mapping_applies(m, filing, el)]
        if not cands:
            continue
        mapping = min(cands, key=lambda m: m.priority)
        std = std_items[mapping.std_item_id]
        key = (std.id, axis, member, bool(ctx.is_consolidated), ctx.end_date)
        # 四半期報告書は 3 か月と累計が同じ期末で並ぶことがある。累計（長い方）を採る
        days = (ctx.end_date - ctx.start_date).days if ctx.start_date else 0
        rank = (mapping.priority, -days)
        prev = chosen.get(key)
        if prev is None or rank < prev[0]:
            chosen[key] = (rank, fact, el, ctx, std, member, axis)

    n = 0
    for _rank, fact, el, ctx, std, member, axis in chosen.values():
        value = Decimal(str(fact.value_num))
        if std.sign == "invert":
            value = -value
        labels = _dims(ctx.member_labels_json)
        db.add(XbrlSegment(
            code=filing.code,
            filing_id=filing.id,
            std_item_id=std.id,
            axis=axis,
            member=member,
            member_label_ja=(labels.get(member) or None),
            member_kind=member_kind(member),
            is_consolidated=bool(ctx.is_consolidated),
            period_type=filing.period_type or "FY",
            period_start=ctx.start_date,
            period_end=ctx.end_date,
            value=value,
            unit=(fact.unit or "JPY")[:32],
            source_fact_id=fact.id,
            source_element_id=el.id,
            generated_at=generated,
        ))
        n += 1
    return n

"""facts + mappings → xbrl_financials."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    XbrlContext,
    XbrlElement,
    XbrlElementMapping,
    XbrlFact,
    XbrlFiling,
    XbrlFinancial,
    XbrlStdItem,
)


CONSOL_ONLY_MEMBERS = {"ConsolidatedMember", "NonConsolidatedMember"}
RESULT_FORECAST_MEMBERS = {"ResultMember", "ForecastMember"}
REVISION_MEMBERS = {"CurrentMember", "PreviousMember"}
RANGE_LOWER = {"LowerMember", "MinimumMember"}
RANGE_UPPER = {"UpperMember", "MaximumMember"}
DIVIDEND_SLOT = {
    "FirstQuarterMember": "Q1",
    "SecondQuarterMember": "Q2",
    "InterimMember": "Q2",
    "ThirdQuarterMember": "Q3",
    "YearEndMember": "year_end",
    "AnnualMember": "annual",
}
ALLOWED_MEMBERS = (
    CONSOL_ONLY_MEMBERS
    | RESULT_FORECAST_MEMBERS
    | REVISION_MEMBERS
    | RANGE_LOWER
    | RANGE_UPPER
    | set(DIVIDEND_SLOT)
)

# 第1弾の BS/PL/CF。選定は is_primary_statement + current のまま（値を変えない）。
LEGACY_PRIMARY_CODES = {
    "cash_and_deposits",
    "current_assets",
    "noncurrent_assets",
    "total_assets",
    "current_liabilities",
    "noncurrent_liabilities",
    "total_liabilities",
    "shareholders_equity",
    "net_assets",
    "net_sales",
    "cost_of_sales",
    "gross_profit",
    "sga",
    "operating_income",
    "ordinary_income",
    "profit_before_tax",
    "net_income",
    "operating_cf",
    "investing_cf",
    "financing_cf",
    "capex",
    "depreciation",
    "cash_end",
}
FORECAST_ITEM_CODES = {
    "net_sales",
    "operating_income",
    "ordinary_income",
    "net_income",
    "eps_basic",
    "dps",
}
EPS_ITEM_CODES = {"eps_basic", "eps_diluted"}
DPS_ITEM_CODES = {"dps"}

VALUE_KIND_ACTUAL = "actual"
VALUE_KIND_FORECAST = "forecast"
VALUE_KIND_FORECAST_PREV = "forecast_previous"
RANGE_POINT = "point"
RANGE_LOWER_SIDE = "lower"
RANGE_UPPER_SIDE = "upper"
SUMMARY_NS = "tse-ed-t"
SOURCE_LAYER_PRIMARY = "primary"
SOURCE_LAYER_SUMMARY = "summary"
NET_SALES_CODE = "net_sales"


def _dims(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _member_local(raw: str) -> str:
    return str(raw).split(":", 1)[-1]


def _members_of(ctx: XbrlContext) -> list[str]:
    return [_member_local(v) for v in _dims(ctx.dimensions_json).values()]


def is_primary_statement(ctx: XbrlContext) -> bool:
    if not ctx.has_dimensions:
        return True
    dims = _dims(ctx.dimensions_json)
    if not dims:
        return True
    for member in dims.values():
        if _member_local(member) not in CONSOL_ONLY_MEMBERS:
            return False
    return True


@dataclass(frozen=True)
class ContextClass:
    value_kind: str
    range_side: str
    dividend_slot: str | None


def classify_context(ctx: XbrlContext) -> ContextClass | None:
    """連結/実績予想/レンジ/配当以外の次元（セグメント等）は None。"""
    members = _members_of(ctx)
    if any(m not in ALLOWED_MEMBERS for m in members):
        return None
    # 実データのレンジ context は ForecastMember を付けず Lower/UpperMember のみ。
    is_range = any(m in RANGE_LOWER or m in RANGE_UPPER for m in members)
    if "ForecastMember" in members or is_range:
        kind = (
            VALUE_KIND_FORECAST_PREV
            if "PreviousMember" in members
            else VALUE_KIND_FORECAST
        )
    else:
        kind = VALUE_KIND_ACTUAL
    if any(m in RANGE_LOWER for m in members):
        range_side = RANGE_LOWER_SIDE
    elif any(m in RANGE_UPPER for m in members):
        range_side = RANGE_UPPER_SIDE
    else:
        range_side = RANGE_POINT
    slot = next((DIVIDEND_SLOT[m] for m in members if m in DIVIDEND_SLOT), None)
    return ContextClass(value_kind=kind, range_side=range_side, dividend_slot=slot)


def infer_target_period(
    ctx: XbrlContext,
    filing: XbrlFiling,
    klass: ContextClass,
    std_code: str,
) -> str:
    if std_code in DPS_ITEM_CODES and klass.dividend_slot:
        return klass.dividend_slot
    if klass.value_kind == VALUE_KIND_ACTUAL:
        return filing.period_type or "FY"
    ref = (ctx.context_ref or "").lower()
    if "accumulatedq1" in ref or "firstquarter" in ref:
        return "Q1"
    if "interim" in ref or "accumulatedq2" in ref or "secondquarter" in ref:
        return "H1"
    if "accumulatedq3" in ref or "thirdquarter" in ref:
        return "Q3"
    if "nextyear" in ref or "currentyear" in ref or "fiscalyear" in ref:
        return "FY"
    if ctx.start_date and ctx.end_date:
        days = (ctx.end_date - ctx.start_date).days
        if 80 <= days <= 100:
            return "Q1"
        if 150 <= days <= 220:
            return "H1"
        if 240 <= days <= 300:
            return "Q3"
        if days >= 300:
            return "FY"
    return "FY"


def _accepts_fact(std: XbrlStdItem, ctx: XbrlContext, klass: ContextClass) -> bool:
    code = std.code
    if klass.value_kind in (VALUE_KIND_FORECAST, VALUE_KIND_FORECAST_PREV):
        return code in FORECAST_ITEM_CODES
    if code in DPS_ITEM_CODES:
        return klass.dividend_slot is not None
    if code in EPS_ITEM_CODES:
        if klass.dividend_slot:
            return False
        members = _members_of(ctx)
        if "ForecastMember" in members:
            return False
        return is_primary_statement(ctx) or "ResultMember" in members or not members
    if code in LEGACY_PRIMARY_CODES:
        return is_primary_statement(ctx) and ctx.relative_period == "current"
    return False


def _skip_non_current_actual(ctx: XbrlContext, klass: ContextClass) -> bool:
    """前期比較の実績は捨てる。予想は期末が当期とずれてよい。"""
    if klass.value_kind != VALUE_KIND_ACTUAL:
        return False
    if ctx.relative_period == "current":
        return False
    if klass.dividend_slot and ctx.relative_period is None:
        return False
    return True


def mapping_applies(mapping: XbrlElementMapping, filing: XbrlFiling) -> bool:
    if mapping.accounting_standard is None:
        return True
    return mapping.accounting_standard == filing.accounting_standard


def source_layer_of(el: XbrlElement) -> str:
    if (el.namespace_key or "") == SUMMARY_NS:
        return SOURCE_LAYER_SUMMARY
    return SOURCE_LAYER_PRIMARY


def _legacy_actual_key(std: XbrlStdItem) -> tuple:
    return ("legacy", std.id)


def is_summary_actual_net_sales_fallback(
    el: XbrlElement,
    ctx: XbrlContext,
    klass: ContextClass,
    std: XbrlStdItem,
) -> bool:
    """本表に売上がないときだけ使う、短信サマリーの当期実績売上。"""
    if std.code != NET_SALES_CODE:
        return False
    if (el.namespace_key or "") != SUMMARY_NS:
        return False
    if klass.value_kind != VALUE_KIND_ACTUAL or klass.range_side != RANGE_POINT:
        return False
    return "ResultMember" in _members_of(ctx)


def _chosen_key(
    std: XbrlStdItem,
    ctx: XbrlContext,
    klass: ContextClass,
    target_period: str,
) -> tuple:
    if (
        std.code in LEGACY_PRIMARY_CODES
        and klass.value_kind == VALUE_KIND_ACTUAL
        and klass.range_side == RANGE_POINT
    ):
        return ("legacy", std.id)
    return (
        std.id,
        bool(ctx.is_consolidated),
        ctx.end_date,
        klass.value_kind,
        klass.range_side,
        target_period,
    )


def normalize_filing(db: Session, filing: XbrlFiling, now: datetime | None = None) -> dict:
    generated = now or datetime.now(timezone.utc).replace(tzinfo=None)
    mappings = db.query(XbrlElementMapping).all()
    elements = {e.id: e for e in db.query(XbrlElement).all()}
    std_items = {s.id: s for s in db.query(XbrlStdItem).all()}
    contexts = {
        c.id: c
        for c in db.query(XbrlContext).filter_by(filing_id=filing.id).all()
    }
    facts = db.query(XbrlFact).filter_by(filing_id=filing.id).all()

    by_elem: dict[tuple[str, str], list[XbrlElementMapping]] = {}
    for m in mappings:
        by_elem.setdefault((m.namespace_key, m.local_name), []).append(m)

    chosen: dict[tuple, tuple] = {}
    fallback_sales: list[tuple] = []
    for fact in facts:
        if fact.is_nil or fact.value_num is None:
            continue
        ctx = contexts.get(fact.context_id)
        if ctx is None:
            continue
        klass = classify_context(ctx)
        if klass is None:
            continue
        if _skip_non_current_actual(ctx, klass):
            continue
        el = elements.get(fact.element_id)
        if el is None:
            continue
        cands = by_elem.get((el.namespace_key, el.local_name), [])
        applicable = [m for m in cands if mapping_applies(m, filing)]
        if not applicable:
            continue
        applicable.sort(key=lambda m: m.priority)
        mapping = applicable[0]
        std = std_items.get(mapping.std_item_id)
        if std is None:
            continue
        if not _accepts_fact(std, ctx, klass):
            if is_summary_actual_net_sales_fallback(el, ctx, klass, std):
                target_period = infer_target_period(ctx, filing, klass, std.code)
                value = Decimal(str(fact.value_num))
                if std.sign == "invert":
                    value = -value
                fallback_sales.append((
                    mapping.priority,
                    0 if ctx.is_consolidated else 1,
                    value, fact, el, ctx, mapping.priority, klass, target_period, std,
                ))
            continue
        target_period = infer_target_period(ctx, filing, klass, std.code)
        value = Decimal(str(fact.value_num))
        if std.sign == "invert":
            value = -value
        key = _chosen_key(std, ctx, klass, target_period)
        prev = chosen.get(key)
        if prev is None or mapping.priority < prev[4]:
            chosen[key] = (value, fact, el, ctx, mapping.priority, klass, target_period, std)

    net_sales_std = next((s for s in std_items.values() if s.code == NET_SALES_CODE), None)
    if net_sales_std is not None:
        sales_key = _legacy_actual_key(net_sales_std)
        if sales_key not in chosen and fallback_sales:
            fallback_sales.sort(key=lambda t: (t[0], t[1]))
            chosen[sales_key] = fallback_sales[0][2:]

    db.query(XbrlFinancial).filter_by(filing_id=filing.id).delete(synchronize_session="fetch")
    db.flush()

    upserted = 0
    filled_std_ids: set[int] = set()
    for value, fact, el, ctx, _pri, klass, target_period, std in chosen.values():
        row = XbrlFinancial(
            code=filing.code,
            filing_id=filing.id,
            std_item_id=std.id,
            is_consolidated=bool(ctx.is_consolidated),
            period_end=ctx.end_date,
            value_kind=klass.value_kind,
            range_side=klass.range_side,
            target_period=target_period,
        )
        db.add(row)
        row.period_type = filing.period_type or "FY"
        row.period_start = ctx.start_date
        row.value = value
        row.unit = (fact.unit or "JPY")[:32]
        row.source_fact_id = fact.id
        row.source_element_id = el.id
        row.source_layer = source_layer_of(el)
        row.generated_at = generated
        upserted += 1
        if std.code in LEGACY_PRIMARY_CODES and klass.value_kind == VALUE_KIND_ACTUAL:
            filled_std_ids.add(std.id)

    if filing.doc_type in {"forecast_revision", "dividend_revision"}:
        missing = []
    else:
        missing = [
            s.code for s in std_items.values()
            if s.is_required and s.id not in filled_std_ids
        ]
    return {"upserted": upserted, "missing_required": missing}


def rebuild_all(db: Session, *, code: str | None = None) -> dict[str, int]:
    q = db.query(XbrlFinancial)
    if code:
        q = q.filter_by(code=code)
    n = q.delete(synchronize_session="fetch")
    db.commit()
    db.expire_all()
    processed = errors = 0
    filings_q = db.query(XbrlFiling).filter_by(parse_status="parsed")
    if code:
        filings_q = filings_q.filter_by(code=code)
    for filing in filings_q.all():
        try:
            normalize_filing(db, filing)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            errors += 1
    return {"deleted": n, "processed": processed, "errors": errors}

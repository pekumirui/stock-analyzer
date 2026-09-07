"""facts + mappings → xbrl_financials."""
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
    XbrlFinancial,
    XbrlStdItem,
)


CONSOL_ONLY_MEMBERS = {"ConsolidatedMember", "NonConsolidatedMember"}


def _dims(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def is_primary_statement(ctx: XbrlContext) -> bool:
    if not ctx.has_dimensions:
        return True
    dims = _dims(ctx.dimensions_json)
    if not dims:
        return True
    for member in dims.values():
        name = str(member).split(":", 1)[-1]
        if name not in CONSOL_ONLY_MEMBERS:
            return False
    return True


def mapping_applies(mapping: XbrlElementMapping, filing: XbrlFiling) -> bool:
    if mapping.accounting_standard is None:
        return True
    return mapping.accounting_standard == filing.accounting_standard


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

    chosen: dict[int, tuple[Decimal, XbrlFact, XbrlElement, XbrlContext, int]] = {}
    for fact in facts:
        if fact.is_nil or fact.value_num is None:
            continue
        ctx = contexts.get(fact.context_id)
        if ctx is None or ctx.relative_period != "current":
            continue
        if not is_primary_statement(ctx):
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
        value = Decimal(str(fact.value_num))
        if std.sign == "invert":
            value = -value
        prev = chosen.get(std.id)
        if prev is None or mapping.priority < prev[4]:
            chosen[std.id] = (value, fact, el, ctx, mapping.priority)

    upserted = 0
    for std_id, (value, fact, el, ctx, _pri) in chosen.items():
        row = (
            db.query(XbrlFinancial)
            .filter_by(
                filing_id=filing.id,
                std_item_id=std_id,
                is_consolidated=bool(ctx.is_consolidated),
                period_end=ctx.end_date,
            )
            .one_or_none()
        )
        if row is None:
            row = XbrlFinancial(
                code=filing.code,
                filing_id=filing.id,
                std_item_id=std_id,
                is_consolidated=bool(ctx.is_consolidated),
                period_end=ctx.end_date,
            )
            db.add(row)
        row.period_type = filing.period_type or "FY"
        row.period_start = ctx.start_date
        row.value = value
        row.unit = fact.unit or "JPY"
        row.source_fact_id = fact.id
        row.source_element_id = el.id
        row.generated_at = generated
        upserted += 1

    required = [s for s in std_items.values() if s.is_required]
    missing = [s.code for s in required if s.id not in chosen]
    return {"upserted": upserted, "missing_required": missing}


def rebuild_all(db: Session) -> dict[str, int]:
    n = db.query(XbrlFinancial).delete()
    db.commit()
    processed = errors = 0
    filings = db.query(XbrlFiling).filter_by(parse_status="parsed").all()
    for filing in filings:
        try:
            normalize_filing(db, filing)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            errors += 1
    return {"deleted": n, "processed": processed, "errors": errors}

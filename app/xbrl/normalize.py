"""facts + mappings → xbrl_financials（とセグメント注記の xbrl_segments。app/xbrl/segments.py）。"""
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
    XbrlSegment,
    XbrlStdItem,
)
from app.xbrl.segments import SEGMENT_STATEMENT, normalize_segments


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
MONTHS_PART = "months"
ELEMENT_CHUNK = 900  # SQLite の IN の変数の上限より十分小さく
# 有報の DEI「連結財務諸表の有無」。false の書類（連結子会社が無い会社）は、
# 連結の軸が無い context（設備の状況・研究開発活動・自己株式等など）も単体として持つ（KAB-78）
CONSOLIDATED_DEI = ("jpdei_cor", "WhetherConsolidatedFinancialStatementsArePreparedDEI")


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
    # それ以外（有報の従業員・株式数・主要な経営指標等など。KAB-78）: 当期の実績の点で、
    # 連結/単体以外の次元が無いもの。連結と単体は別の行（キーに is_consolidated が入る）
    return (
        klass.value_kind == VALUE_KIND_ACTUAL
        and klass.range_side == RANGE_POINT
        and klass.dividend_slot is None
        and is_primary_statement(ctx)
        and ctx.relative_period == "current"
    )


def _skip_non_current_actual(ctx: XbrlContext, klass: ContextClass) -> bool:
    """前期比較の実績は捨てる。予想は期末が当期とずれてよい。"""
    if klass.value_kind != VALUE_KIND_ACTUAL:
        return False
    if ctx.relative_period == "current":
        return False
    if klass.dividend_slot and ctx.relative_period is None:
        return False
    return True


def local_name_is_ifrs(el: XbrlElement | None) -> bool:
    """tse-ed-t / jpigp などの local_name 末尾 IFRS を要素側の基準にする。"""
    if el is None:
        return False
    return (el.local_name or "").endswith("IFRS")


def is_jppfs_element(el: XbrlElement) -> bool:
    key = (el.namespace_key or "").lower()
    uri = (el.namespace_uri or "").lower()
    return key.startswith("jppfs") or "/jppfs/" in uri or "taxonomy/jppfs" in uri


def skip_jppfs_on_ifrs_filing(filing: XbrlFiling, el: XbrlElement) -> bool:
    """IFRS の書類では jppfs を正規化に使わない（KAB-52 案B）。"""
    if filing.accounting_standard != "IFRS":
        return False
    return is_jppfs_element(el)


def mapping_applies(
    mapping: XbrlElementMapping,
    filing: XbrlFiling,
    el: XbrlElement | None = None,
) -> bool:
    if mapping.accounting_standard is None:
        return True
    if mapping.accounting_standard == filing.accounting_standard:
        return True
    # 40530 型: 短信は JPGAAP でも予想が *IFRS 要素。要素側を優先。
    if mapping.accounting_standard == "IFRS" and local_name_is_ifrs(el):
        return True
    return False


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
    consolidated: bool,
) -> tuple:
    if (
        std.code in LEGACY_PRIMARY_CODES
        and klass.value_kind == VALUE_KIND_ACTUAL
        and klass.range_side == RANGE_POINT
    ):
        return ("legacy", std.id)
    return (
        std.id,
        consolidated,
        ctx.end_date,
        klass.value_kind,
        klass.range_side,
        target_period,
    )


def _add_months(chosen: dict[tuple, tuple], months: dict[tuple, tuple]) -> None:
    """年の値に 月÷12 を足す（小数 2 桁）。年が無く月だけ（1 年未満）なら月÷12。"""
    for key, (mval, mfact, mel, mctx, mpri, mklass, mtp, mstd) in months.items():
        base = chosen.get(key)
        years = base[0] if base else Decimal(0)
        value = (years + mval / 12).quantize(Decimal("0.01"))
        if base:
            chosen[key] = (value, *base[1:])
        else:
            chosen[key] = (value, mfact, mel, mctx, mpri, mklass, mtp, mstd)


def is_pure_unit(raw: str | None) -> bool:
    """人数・年数・比率の fact。空も科目側で単位を決める（KAB-147）。"""
    u = (raw or "").strip().lower()
    return (not u) or u == "pure" or u.endswith(":pure")


def financial_unit(std_unit: str | None, fact_unit: str | None) -> str:
    """fact が pure / 空のときだけ std.unit。USD 開示は fact の単位のまま。"""
    if is_pure_unit(fact_unit) and std_unit:
        return std_unit[:32]
    return ((fact_unit or "").strip() or "JPY")[:32]


def _elements_of(db: Session, facts: list[XbrlFact]) -> dict[int, XbrlElement]:
    """その書類の fact が使う要素だけ。全件（本番 13 万行）を書類ごとに読むと --rebuild が 1 件 20 秒かかる"""
    ids = sorted({f.element_id for f in facts})
    out: dict[int, XbrlElement] = {}
    for i in range(0, len(ids), ELEMENT_CHUNK):
        chunk = ids[i:i + ELEMENT_CHUNK]
        out.update({e.id: e for e in db.query(XbrlElement).filter(XbrlElement.id.in_(chunk))})
    return out


def has_no_consolidated_statements(facts: list[XbrlFact], elements: dict[int, XbrlElement]) -> bool:
    """DEI で「連結財務諸表を作っていない」と書いた書類。DEI が無い（TDnet）なら False。"""
    for fact in facts:
        el = elements.get(fact.element_id)
        if el is not None and (el.namespace_key, el.local_name) == CONSOLIDATED_DEI:
            return (fact.value_text or "").strip().lower() == "false"
    return False


def normalize_filing(db: Session, filing: XbrlFiling, now: datetime | None = None) -> dict:
    generated = now or datetime.now(timezone.utc).replace(tzinfo=None)
    # セグメント科目（statement='SEG'）は xbrl_segments だけ。同じ要素（NetSales 等）を取り合わない
    std_items = {
        s.id: s for s in db.query(XbrlStdItem).all() if s.statement != SEGMENT_STATEMENT
    }
    mappings = [m for m in db.query(XbrlElementMapping).all() if m.std_item_id in std_items]
    contexts = {
        c.id: c
        for c in db.query(XbrlContext).filter_by(filing_id=filing.id).all()
    }
    facts = db.query(XbrlFact).filter_by(filing_id=filing.id).all()
    elements = _elements_of(db, facts)
    single_entity = has_no_consolidated_statements(facts, elements)

    def consolidated(ctx: XbrlContext) -> bool:
        return bool(ctx.is_consolidated) and not single_entity

    by_elem: dict[tuple[str, str], list[XbrlElementMapping]] = {}
    for m in mappings:
        by_elem.setdefault((m.namespace_key, m.local_name), []).append(m)

    chosen: dict[tuple, tuple] = {}
    months: dict[tuple, tuple] = {}
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
        if skip_jppfs_on_ifrs_filing(filing, el):
            continue
        cands = by_elem.get((el.namespace_key, el.local_name), [])
        applicable = [m for m in cands if mapping_applies(m, filing, el)]
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
                    0 if ctx.is_consolidated else 1,
                    mapping.priority,
                    value, fact, el, ctx, mapping.priority, klass, target_period, std,
                ))
            continue
        target_period = infer_target_period(ctx, filing, klass, std.code)
        value = Decimal(str(fact.value_num))
        if std.sign == "invert":
            value = -value
        key = _chosen_key(std, ctx, klass, target_period, consolidated(ctx))
        pick = months if mapping.part == MONTHS_PART else chosen
        prev = pick.get(key)
        if prev is None or mapping.priority < prev[4]:
            pick[key] = (value, fact, el, ctx, mapping.priority, klass, target_period, std)
    _add_months(chosen, months)

    net_sales_std = next((s for s in std_items.values() if s.code == NET_SALES_CODE), None)
    if net_sales_std is not None:
        sales_key = _legacy_actual_key(net_sales_std)
        if sales_key not in chosen and fallback_sales:
            # 連結を先に。同じ連結区分なら mapping の priority。
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
            is_consolidated=consolidated(ctx),
            period_end=ctx.end_date,
            value_kind=klass.value_kind,
            range_side=klass.range_side,
            target_period=target_period,
        )
        db.add(row)
        row.period_type = filing.period_type or "FY"
        row.period_start = ctx.start_date
        row.value = value
        row.unit = financial_unit(std.unit, fact.unit)
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
    segments = normalize_segments(db, filing, now=generated)
    return {"upserted": upserted, "missing_required": missing, "segments": segments}


def rebuild_all(db: Session, *, code: str | None = None) -> dict[str, int]:
    """xbrl_financials / xbrl_segments を消して parsed の filing を全部やり直す。

    deleted / segments_deleted は消す前、financials / segments は作り直した後の行数（KAB-83）。
    正規化が変わっていなければ deleted と financials は同じになる。
    """
    def scoped(model):
        q = db.query(model)
        return q.filter_by(code=code) if code else q

    n = scoped(XbrlFinancial).delete(synchronize_session="fetch")
    n_seg = scoped(XbrlSegment).delete(synchronize_session="fetch")
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
    return {
        "deleted": n,
        "financials": scoped(XbrlFinancial).count(),
        "segments_deleted": n_seg,
        "segments": scoped(XbrlSegment).count(),
        "processed": processed,
        "errors": errors,
    }

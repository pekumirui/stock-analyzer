"""有報の大株主の状況・所有者別状況（KAB-266。docs/jobs/edinet.md §9）。

対象は当期の MajorShareholdersAxis / MajorShareholdersVotingRightsAxis /
ClassesOfSharesAxis（連結/単体の軸は付いてよい）。科目は標準要素名で決める。
xbrl_financials の正規化はこの次元を使わない（classify_context が落とす）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import run_sqlite_write
from app.edinet.textblocks import edinet_doc_id
from app.models import (
    EdinetDocument,
    XbrlContext,
    XbrlElement,
    XbrlFact,
    XbrlFiling,
    XbrlMajorShareholder,
    XbrlShareholderFiling,
    XbrlShareholdingByCategory,
)

CONSOL_AXIS = "ConsolidatedOrNonConsolidatedAxis"
SHAREHOLDER_AXES = {
    "MajorShareholdersAxis",
    "MajorShareholdersVotingRightsAxis",
    "ClassesOfSharesAxis",
}
BASIS_SHARES = "shares"
BASIS_VOTING = "voting_rights"
BASIS_BOTH = "both"
AMENDS_DEI = "IdentificationOfDocumentSubjectToAmendmentDEI"
DEI_KEY = "jpdei_cor"
RANK_RE = re.compile(r"^No(\d+)MajorShareholders(?:VotingRights)?Member$")
TOTAL_NAMES = {"計", "合計"}
DOC_TYPES = ("120", "130", "140", "150", "160", "170")

CATEGORY_BY_SUFFIX = {
    "NationalAndLocalGovernments": "national_local",
    "FinancialInstitutions": "financial_institutions",
    "FinancialServiceProviders": "financial_service_providers",
    "OtherCorporations": "other_corporations",
    "ForeignInvestorsOtherThanIndividuals": "foreign_non_individuals",
    "ForeignersOtherThanIndividuals": "foreign_non_individuals",
    "ForeignIndividualInvestors": "foreign_individuals",
    "ForeignIndividuals": "foreign_individuals",
    "IndividualsAndOthers": "individuals_others",
    "Total": "total",
}
COUNT_PREFIX = "NumberOfShareholders"
UNITS_PREFIX = "NumberOfSharesHeldNumberOfUnits"
PCT_PREFIX = "PercentageOfShareholdings"
LESS_THAN_UNIT = "NumberOfSharesHeldSharesLessThanOneUnit"
SHARES_PER_UNIT = "NumberOfSharesConstitutingOneUnit"
MAJOR_NAME = {
    BASIS_SHARES: "NameMajorShareholders",
    BASIS_VOTING: "NameMajorShareholdersVotingRights",
}
MAJOR_ADDR = {
    BASIS_SHARES: "AddressMajorShareholders",
    BASIS_VOTING: "AddressMajorShareholdersVotingRights",
}
MAJOR_HELD = {
    BASIS_SHARES: "NumberOfSharesHeld",
    BASIS_VOTING: "NumberOfVotingRightsHeld",
}
MAJOR_RATIO = {
    BASIS_SHARES: "ShareholdingRatio",
    BASIS_VOTING: "RatioOfVotingRightsHeld",
}
SHAREHOLDER_FACT_NAMES = frozenset({
    *MAJOR_NAME.values(), *MAJOR_ADDR.values(), *MAJOR_HELD.values(), *MAJOR_RATIO.values(),
    SHARES_PER_UNIT, LESS_THAN_UNIT,
    *(COUNT_PREFIX + s for s in CATEGORY_BY_SUFFIX),
    *(UNITS_PREFIX + s for s in CATEGORY_BY_SUFFIX),
    *(PCT_PREFIX + s for s in CATEGORY_BY_SUFFIX),
})
_WS_RE = re.compile(r"[\t\r\n]+")
_SPACES_RE = re.compile(r" {2,}")


def _local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def _dims(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def extra_axis_locals(ctx) -> set[str]:
    dims = getattr(ctx, "dimensions", None)
    if dims is None:
        dims = _dims(ctx.dimensions_json)
    return {_local(axis) for axis in dims if _local(axis) != CONSOL_AXIS}


def keep_shareholder_fact(fact, ctx) -> bool:
    """当期で株主関連の軸だけ、かつ大株主・所有者別の要素。"""
    if ctx is None or getattr(ctx, "relative_period", None) != "current":
        return False
    if fact.local_name not in SHAREHOLDER_FACT_NAMES:
        return False
    extras = extra_axis_locals(ctx)
    return bool(extras) and extras <= SHAREHOLDER_AXES


def _norm_text(raw: str | None) -> str | None:
    if not raw:
        return None
    text = _SPACES_RE.sub(" ", _WS_RE.sub("", raw)).strip()
    return text or None


def _rank_of(member: str) -> int | None:
    m = RANK_RE.match(_local(member))
    return int(m.group(1)) if m else None


def _axis_member(dims: dict[str, str], axis_local: str) -> str | None:
    for axis, member in dims.items():
        if _local(axis) == axis_local:
            return member
    return None


def _category_of(local_name: str) -> tuple[str, str] | None:
    for prefix, field in (
        (COUNT_PREFIX, "shareholder_count"),
        (UNITS_PREFIX, "units_held"),
        (PCT_PREFIX, "shareholding_ratio"),
    ):
        if local_name.startswith(prefix):
            cat = CATEGORY_BY_SUFFIX.get(local_name[len(prefix):])
            if cat:
                return cat, field
    if local_name == LESS_THAN_UNIT:
        return "total", "shares_less_than_one_unit"
    return None


def _int(value) -> int | None:
    if value is None:
        return None
    return int(Decimal(str(value)))


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _dei_text(facts: list[XbrlFact], elements: dict[int, XbrlElement], name: str) -> str | None:
    for fact in facts:
        el = elements.get(fact.element_id)
        if el is None or el.namespace_key != DEI_KEY or el.local_name != name:
            continue
        if fact.value_text:
            return fact.value_text
    return None


def _root(doc_id: str, amends: dict[str, str | None]) -> str:
    seen = {doc_id}
    cur = doc_id
    while amends.get(cur) and amends[cur] not in seen:
        cur = amends[cur]
        seen.add(cur)
    return cur


def delete_shareholders(db: Session, filing_id: int) -> None:
    db.query(XbrlMajorShareholder).filter_by(filing_id=filing_id).delete(
        synchronize_session="fetch")
    db.query(XbrlShareholdingByCategory).filter_by(filing_id=filing_id).delete(
        synchronize_session="fetch")
    db.query(XbrlShareholderFiling).filter_by(filing_id=filing_id).delete(
        synchronize_session="fetch")


def normalize_shareholders(db: Session, filing: XbrlFiling,
                           now: datetime | None = None) -> int:
    """filing の大株主・所有者別を作り直す。戻り値は大株主 + 所有者別の行数。"""
    generated = now or datetime.now(timezone.utc).replace(tzinfo=None)
    delete_shareholders(db, filing.id)
    db.flush()
    if filing.source != "edinet":
        return 0

    contexts = {
        c.id: c for c in db.query(XbrlContext).filter_by(filing_id=filing.id).all()
    }
    facts = db.query(XbrlFact).filter_by(filing_id=filing.id).all()
    el_ids = {f.element_id for f in facts}
    elements = {
        e.id: e for e in db.query(XbrlElement).filter(XbrlElement.id.in_(list(el_ids)))
    } if el_ids else {}

    holders: dict[tuple[int, str], dict] = {}
    categories: dict[tuple[str, str], dict] = {}
    shares_per_unit: dict[str, int] = {}
    as_of = None

    for fact in facts:
        el = elements.get(fact.element_id)
        ctx = contexts.get(fact.context_id)
        if el is None or ctx is None or ctx.relative_period != "current":
            continue
        if el.local_name not in SHAREHOLDER_FACT_NAMES:
            continue
        dims = _dims(ctx.dimensions_json)
        extras = extra_axis_locals(ctx)
        if extras and extras - SHAREHOLDER_AXES:
            continue
        as_of = ctx.end_date or as_of

        major_axis = _axis_member(dims, "MajorShareholdersAxis")
        voting_axis = _axis_member(dims, "MajorShareholdersVotingRightsAxis")
        class_member = _axis_member(dims, "ClassesOfSharesAxis")
        share_class = class_member or ""

        if major_axis or voting_axis:
            basis = BASIS_VOTING if voting_axis else BASIS_SHARES
            rank = _rank_of(voting_axis or major_axis or "")
            if rank is None:
                continue
            row = holders.setdefault((rank, basis), {"rank": rank, "basis": basis})
            if el.local_name == MAJOR_NAME[basis] and not fact.is_nil:
                row["name"] = _norm_text(fact.value_text)
            elif el.local_name == MAJOR_ADDR[basis] and not fact.is_nil:
                row["address"] = _norm_text(fact.value_text)
            elif el.local_name == MAJOR_HELD[basis] and not fact.is_nil:
                if basis == BASIS_SHARES:
                    row["shares_held"] = _dec(fact.value_num)
                else:
                    row["voting_rights"] = _dec(fact.value_num)
            elif el.local_name == MAJOR_RATIO[basis] and not fact.is_nil:
                if basis == BASIS_SHARES:
                    row["shareholding_ratio"] = _dec(fact.value_num)
                else:
                    row["voting_rights_ratio"] = _dec(fact.value_num)
            continue

        if el.local_name == SHARES_PER_UNIT and not fact.is_nil:
            n = _int(fact.value_num)
            if n is not None:
                shares_per_unit[share_class] = n
            continue

        parsed = _category_of(el.local_name)
        if parsed is None or fact.is_nil:
            continue
        cat, field = parsed
        row = categories.setdefault((share_class, cat), {
            "share_class": share_class, "category": cat,
        })
        if field in ("shareholder_count",):
            row[field] = _int(fact.value_num)
        elif field == "units_held":
            row[field] = _int(fact.value_num)
        else:
            row[field] = _dec(fact.value_num)

    holders = {
        key: row for key, row in holders.items()
        if row.get("name") and row["name"] not in TOTAL_NAMES
    }
    categories = {
        key: row for key, row in categories.items()
        if any(row.get(k) is not None for k in (
            "shareholder_count", "units_held", "shareholding_ratio",
            "shares_less_than_one_unit",
        ))
    }
    if not holders and not categories:
        return 0

    have_shares = any(r["basis"] == BASIS_SHARES for r in holders.values())
    have_voting = any(r["basis"] == BASIS_VOTING for r in holders.values())
    if have_shares and have_voting:
        basis = BASIS_BOTH
    elif have_voting:
        basis = BASIS_VOTING
    else:
        basis = BASIS_SHARES

    amends = edinet_doc_id(_dei_text(facts, elements, AMENDS_DEI))
    if amends is None:
        parent = db.scalars(
            select(EdinetDocument.parent_doc_id)
            .where(EdinetDocument.doc_id == filing.doc_id)
            .order_by(EdinetDocument.ope_datetime.desc())
        ).first()
        amends = edinet_doc_id(parent) if parent else None
    original = amends or filing.doc_id
    unit = shares_per_unit.get("jpcrp_cor:OrdinaryShareMember")
    if unit is None:
        unit = shares_per_unit.get("")
    if unit is None and len(shares_per_unit) == 1:
        unit = next(iter(shares_per_unit.values()))

    db.add(XbrlShareholderFiling(
        filing_id=filing.id,
        code=filing.code,
        doc_id=filing.doc_id,
        as_of=as_of or filing.fiscal_year_end,
        basis=basis,
        shares_per_unit=unit,
        is_amendment=bool(filing.is_amendment),
        amends_doc_id=amends,
        original_doc_id=original,
        is_latest=False,
        withdrawn=False,
        generated_at=generated,
    ))
    n = 0
    for row in holders.values():
        db.add(XbrlMajorShareholder(
            filing_id=filing.id,
            rank=row["rank"],
            basis=row["basis"],
            name=row.get("name"),
            address=row.get("address"),
            shares_held=row.get("shares_held"),
            shareholding_ratio=row.get("shareholding_ratio"),
            voting_rights=row.get("voting_rights"),
            voting_rights_ratio=row.get("voting_rights_ratio"),
        ))
        n += 1
    for row in categories.values():
        db.add(XbrlShareholdingByCategory(
            filing_id=filing.id,
            share_class=row["share_class"],
            category=row["category"],
            shareholder_count=row.get("shareholder_count"),
            units_held=row.get("units_held"),
            shareholding_ratio=row.get("shareholding_ratio"),
            shares_less_than_one_unit=row.get("shares_less_than_one_unit"),
        ))
        n += 1
    db.flush()
    return n


def refresh_versions(db: Session) -> int:
    """訂正の大元・取下げ・最新を全件から決め直す。

    最新は (code, fiscal_year_end, doc_type) ごとに、取下げでない提出日時が新しい 1 件。
    """
    rows = db.execute(
        select(
            XbrlShareholderFiling.filing_id, XbrlShareholderFiling.doc_id,
            XbrlShareholderFiling.amends_doc_id, XbrlShareholderFiling.original_doc_id,
            XbrlShareholderFiling.is_latest, XbrlShareholderFiling.withdrawn,
            XbrlFiling.code, XbrlFiling.doc_type, XbrlFiling.fiscal_year_end,
            XbrlFiling.submitted_at,
        ).join(XbrlFiling, XbrlFiling.id == XbrlShareholderFiling.filing_id)
    ).all()
    if not rows:
        return 0
    gone = set(db.scalars(
        select(EdinetDocument.doc_id).where(
            EdinetDocument.doc_type_code.in_(DOC_TYPES),
            (EdinetDocument.withdrawal_status == "2") | (EdinetDocument.disclosure_status == "2"),
        )
    ))
    amends = {r.doc_id: r.amends_doc_id for r in rows}
    root = {r.doc_id: _root(r.doc_id, amends) for r in rows}
    best: dict[tuple, tuple] = {}
    for r in rows:
        if r.doc_id in gone:
            continue
        key = (r.submitted_at or datetime.min, r.doc_id)
        group = (r.code, r.fiscal_year_end, r.doc_type)
        if group not in best or key > best[group]:
            best[group] = key
    latest = {k[1] for k in best.values()}

    def write():
        n = 0
        for r in rows:
            want = {
                "original_doc_id": root[r.doc_id],
                "withdrawn": r.doc_id in gone,
                "is_latest": r.doc_id in latest,
            }
            if (r.original_doc_id, r.withdrawn, r.is_latest) != tuple(want.values()):
                db.execute(
                    update(XbrlShareholderFiling)
                    .where(XbrlShareholderFiling.filing_id == r.filing_id)
                    .values(**want)
                )
                n += 1
        return n

    return run_sqlite_write(db, write)


def rebuild_shareholders(db: Session, *, code: str | None = None) -> dict[str, int]:
    """parsed の EDINET filing から大株主・所有者別を作り直す。"""
    q = db.query(XbrlFiling).filter_by(source="edinet", parse_status="parsed")
    if code:
        q = q.filter_by(code=code)
    processed = errors = rows = 0
    for filing in q.all():
        try:
            rows += normalize_shareholders(db, filing)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            errors += 1
    refreshed = refresh_versions(db)
    return {
        "processed": processed,
        "errors": errors,
        "rows": rows,
        "refreshed": refreshed,
        "filings": db.query(XbrlShareholderFiling).count() if not code
        else db.query(XbrlShareholderFiling).filter_by(code=code).count(),
    }

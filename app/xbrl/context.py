"""XBRL context interpretation. xbrlp does not expose contexts."""
from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    s = text.strip()[:10]
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


@dataclass
class ParsedContext:
    context_ref: str
    period_type: str
    start_date: date | None
    end_date: date
    is_consolidated: int
    relative_period: str | None
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def has_dimensions(self) -> bool:
        return bool(self.dimensions)

    @property
    def dimensions_json(self) -> str:
        return json.dumps(self.dimensions, ensure_ascii=False, sort_keys=True)

    def only_consol_dimensions(self) -> bool:
        if not self.dimensions:
            return True
        for member in self.dimensions.values():
            name = member.split(":", 1)[-1]
            if name not in ("ConsolidatedMember", "NonConsolidatedMember"):
                return False
        return True


def extract_dimensions(ctx: ET.Element) -> dict[str, str]:
    dims: dict[str, str] = {}
    for child in ctx.iter():
        if local_name(child.tag) != "explicitMember":
            continue
        axis = child.get("dimension") or ""
        member = (child.text or "").strip()
        if axis and member:
            dims[axis] = member
    return dims


def infer_consolidated(dimensions: dict[str, str]) -> int:
    members = " ".join(dimensions.values())
    if "NonConsolidatedMember" in members:
        return 0
    if "ConsolidatedMember" in members:
        return 1
    return 1


def parse_context_element(elem: ET.Element) -> ParsedContext | None:
    ref = elem.get("id")
    if not ref:
        return None
    instant = start = end = None
    for child in elem.iter():
        name = local_name(child.tag)
        if name == "instant":
            instant = parse_date(child.text)
        elif name == "startDate":
            start = parse_date(child.text)
        elif name == "endDate":
            end = parse_date(child.text)
    if instant is not None:
        period_type = "instant"
        end_date = instant
        start_date = None
    elif end is not None:
        period_type = "duration"
        end_date = end
        start_date = start
    else:
        return None
    dims = extract_dimensions(elem)
    return ParsedContext(
        context_ref=ref,
        period_type=period_type,
        start_date=start_date,
        end_date=end_date,
        is_consolidated=infer_consolidated(dims),
        relative_period=None,
        dimensions=dims,
    )


def parse_contexts_from_xml(path: Path) -> list[ParsedContext]:
    contexts: list[ParsedContext] = []
    seen: set[str] = set()
    try:
        for _event, elem in ET.iterparse(str(path), events=["end"]):
            if local_name(elem.tag) != "context":
                continue
            parsed = parse_context_element(elem)
            if parsed and parsed.context_ref not in seen:
                seen.add(parsed.context_ref)
                contexts.append(parsed)
            elem.clear()
    except ET.ParseError:
        return []
    return contexts


# DEI / known local names for the report's current period end (本表の期末).
# Prefer CurrentPeriod* over CurrentFiscalYear* (annual FY end) and FiscalYearEnd.
DEI_CURRENT_PERIOD_END_NAMES = (
    "CurrentPeriodEndDateDEI",
    "CurrentPeriodEndDate",
    "DocumentPeriodEndDateDEI",
    "DocumentPeriodEndDate",
)

# Monetary PL locals often present on flash-report duration contexts.
_PL_DURATION_HINTS = frozenset({
    "NetSales",
    "SalesIFRS",
    "RevenueIFRS",
    "OperatingRevenue1",
    "OperatingIncome",
    "OperatingProfitLossIFRS",
    "OrdinaryIncome",
    "ProfitLossAttributableToOwnersOfParent",
    "ProfitAttributableToOwnersOfParentIFRS",
    "ProfitLossAttributableToOwnersOfParentIFRS",
})


def _parse_iso_date_text(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    # Accept plain ISO; ignore Japanese filing-date style.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return parse_date(s[:10])
    return None


def fiscal_year_end_from_title(title: str | None) -> date | None:
    """Annual flash-report titles only: ``YYYY年M月期`` → month-end.

    Quarterly / semi-annual titles name the *annual* FY end, not the period end,
    so they are skipped.
    """
    if not title:
        return None
    if any(tok in title for tok in ("四半期", "半期", "中間")):
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月期", title)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12):
        return None
    day = monthrange(year, month)[1]
    return date(year, month, day)


def fiscal_year_end_from_dei(facts) -> date | None:
    """Pick CurrentPeriodEndDate* / DocumentPeriodEndDate* from parsed facts."""
    by_local: dict[str, date] = {}
    for fact in facts:
        local = getattr(fact, "local_name", None) or ""
        if local not in DEI_CURRENT_PERIOD_END_NAMES:
            continue
        raw = getattr(fact, "value_text", None)
        if raw is None and getattr(fact, "value_num", None) is not None:
            raw = str(fact.value_num)
        d = _parse_iso_date_text(raw)
        if d is not None:
            by_local[local] = d
    for name in DEI_CURRENT_PERIOD_END_NAMES:
        if name in by_local:
            return by_local[name]
    return None


def fiscal_year_end_from_pl_contexts(contexts: list[ParsedContext], facts) -> date | None:
    """Latest duration end among contexts that carry monetary PL facts."""
    ctx_by = {c.context_ref: c for c in contexts}
    ends: list[date] = []
    for fact in facts:
        if not getattr(fact, "is_numeric", False):
            continue
        if getattr(fact, "value_num", None) is None:
            continue
        local = getattr(fact, "local_name", "") or ""
        if local not in _PL_DURATION_HINTS:
            continue
        ctx = ctx_by.get(getattr(fact, "context_ref", ""))
        if ctx is None or ctx.period_type != "duration":
            continue
        ends.append(ctx.end_date)
    return max(ends) if ends else None


def fiscal_year_end_from_duration_contexts(contexts: list[ParsedContext]) -> date | None:
    """Fallback: max end among duration contexts only (skip filing-date instants)."""
    ends = [c.end_date for c in contexts if c.period_type == "duration"]
    return max(ends) if ends else None


def resolve_fiscal_year_end(
    contexts: list[ParsedContext],
    facts,
    *,
    title: str | None = None,
    exclude_dates: set[date] | None = None,
) -> date | None:
    """Determine 本表の期末 for relative_period assignment.

    Order: DEI CurrentPeriod/DocumentPeriod end → reliable annual title →
    PL monetary duration ends → any duration ends.
    Never returns a date in ``exclude_dates`` (e.g. submitted_at / pubdate day).
    """
    excluded = exclude_dates or set()

    def _ok(d: date | None) -> date | None:
        if d is None or d in excluded:
            return None
        return d

    for candidate in (
        fiscal_year_end_from_dei(facts),
        fiscal_year_end_from_title(title),
        fiscal_year_end_from_pl_contexts(contexts, facts),
        fiscal_year_end_from_duration_contexts(contexts),
    ):
        got = _ok(candidate)
        if got is not None:
            return got
    return None


def assign_relative_periods(
    contexts: list[ParsedContext],
    fiscal_year_end: date | None,
) -> None:
    if not contexts:
        return
    if fiscal_year_end is not None:
        fy_end = fiscal_year_end
    else:
        fy_end = (
            fiscal_year_end_from_duration_contexts(contexts)
            or max(c.end_date for c in contexts)
        )
    for ctx in contexts:
        if ctx.end_date == fy_end:
            ctx.relative_period = "current"
            continue
        prior1 = shift_years(fy_end, -1)
        prior2 = shift_years(fy_end, -2)
        if abs((ctx.end_date - prior1).days) <= 30:
            ctx.relative_period = "prior1"
        elif abs((ctx.end_date - prior2).days) <= 30:
            ctx.relative_period = "prior2"
        else:
            ctx.relative_period = None


def classify_title(title: str) -> dict[str, Any]:
    # NFKC so fullwidth tokens like ＩＦＲＳ / 〔ＩＦＲＳ〕 match halfwidth IFRS etc.
    t = unicodedata.normalize("NFKC", title or "")
    amendment = "訂正" in t
    period: str | None
    if "四半期" in t:
        doc_type = "quarterly"
        if "第1" in t or "第１" in t:
            period = "Q1"
        elif "第3" in t or "第３" in t:
            period = "Q3"
        elif "第2" in t or "第２" in t:
            period = "Q2"
        else:
            period = None
    elif "半期" in t or "中間" in t:
        doc_type = "semiannual"
        period = "Q2"
    elif "決算短信" in t or "有価証券報告書" in t:
        doc_type = "annual"
        period = "FY"
    else:
        doc_type = "other"
        period = None
    if "IFRS" in t.upper() or "国際会計" in t:
        standard = "IFRS"
    elif "米国" in t:
        standard = "USGAAP"
    elif "日本基準" in t:
        standard = "JPGAAP"
    else:
        standard = None
    return {
        "doc_type": doc_type,
        "period_type": period,
        "is_amendment": amendment,
        "accounting_standard": standard,
    }


def infer_accounting_standard_from_namespaces(
    facts,
    *,
    title_standard: str | None = None,
) -> str | None:
    """When title leaves accounting_standard NULL, prefer namespace evidence.

    DESIGN_xbrl: DEI/namespaces with ``jpigp`` / ``ifrs`` → IFRS,
    ``us-gaap`` → USGAAP; otherwise leave NULL (do not invent JPGAAP).
    Title result wins when already set.
    """
    if title_standard:
        return title_standard
    saw_ifrs = False
    saw_us = False
    for fact in facts or ():
        key = (getattr(fact, "namespace_key", None) or "").lower()
        uri = (getattr(fact, "namespace_uri", None) or "").lower()
        blob = f"{key} {uri}"
        if "jpigp" in blob or "ifrs" in blob:
            saw_ifrs = True
        if "us-gaap" in blob or "usgaap" in blob:
            saw_us = True
        # DEI text values sometimes name the standard explicitly.
        local = (getattr(fact, "local_name", None) or "").lower().replace("_", "")
        if "accountingstandard" in local:
            raw_nfkc = unicodedata.normalize("NFKC", str(getattr(fact, "value_text", None) or ""))
            upper = raw_nfkc.upper()
            if "IFRS" in upper or "国際会計" in raw_nfkc:
                return "IFRS"
            if "US-GAAP" in upper or "USGAAP" in upper or "米国" in raw_nfkc:
                return "USGAAP"
            if "日本基準" in raw_nfkc or "JPGAAP" in upper:
                return "JPGAAP"
    if saw_ifrs and not saw_us:
        return "IFRS"
    if saw_us and not saw_ifrs:
        return "USGAAP"
    return None

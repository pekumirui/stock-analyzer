"""XBRL context interpretation. xbrlp does not expose contexts."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
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


def assign_relative_periods(
    contexts: list[ParsedContext],
    fiscal_year_end: date | None,
) -> None:
    if not contexts:
        return
    fy_end = fiscal_year_end or max(c.end_date for c in contexts)
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
    t = title or ""
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

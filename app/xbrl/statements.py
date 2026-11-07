"""短信 XBRL から statements の行を作る（KAB-383）。

開示番号（doc_id の後ろ 14 桁）で J-Quants の行と同じキーにする。数値は XBRL を優先し、
XBRL に無い列は既にある値（J-Quants）を残す。raw_json は J-Quants のものがあれば残し、
無ければ画面が読むキーだけ XBRL から作る。
"""
from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Statement, XbrlFiling, XbrlFinancial, XbrlStdItem

SOURCE_XBRL = "xbrl"
SOURCE_JQUANTS = "jquants"
RAW_MARKER = "Source"

FINANCIAL_DOC_TYPES = {"annual": None, "quarterly": None, "semiannual": None}
REVISION_DOC_TYPES = {"forecast_revision": "EarnForecastRevision", "dividend_revision": "DividendForecastRevision"}
PERIOD_TYPES = {"Q1": "1Q", "Q2": "2Q", "Q3": "3Q", "FY": "FY"}
STANDARD_SUFFIX = {"IFRS": "IFRS", "USGAAP": "US"}

PL = {
    "net_sales": "net_sales",
    "operating_income": "operating_profit",
    "ordinary_income": "ordinary_profit",
    "net_income": "profit",
    "eps_basic": "eps",
}
RAW_PL = {"net_sales": "Sales", "operating_income": "OP", "ordinary_income": "OdP", "net_income": "NP", "eps_basic": "EPS"}
RAW_NEXT_PL = {**RAW_PL, "net_income": "Np"}
RAW_DIV = {"Q1": "Div1Q", "Q2": "Div2Q", "Q3": "Div3Q", "year_end": "DivFY", "annual": "DivAnn"}
RAW_RATIOS = {"bps": "BPS", "equity_ratio": "EqAR", "roe": "ROE"}
ITEM_CODES = (*PL, "dps", *RAW_RATIOS)
NUMERIC_COLUMNS = (
    "net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
    "f2_net_sales", "f2_operating_profit", "f2_ordinary_profit", "f2_profit", "f2_eps",
    "fy_net_sales", "fy_operating_profit", "fy_ordinary_profit", "fy_profit", "fy_eps",
)
META_COLUMNS = (
    "code", "disclosed_date", "disclosed_time", "type_of_period", "type_of_document",
    "period_start", "period_end", "fiscal_year_start", "fiscal_year_end", "is_revision",
)


def disclosure_no_of(doc_id: str) -> str:
    return doc_id[-14:]


def _one_year_end(start: date) -> date:
    try:
        nxt = start.replace(year=start.year + 1)
    except ValueError:
        nxt = start.replace(year=start.year + 1, day=28)
    return nxt - timedelta(days=1)


def _year_start(end: date) -> date:
    try:
        prev = end.replace(year=end.year - 1)
    except ValueError:
        prev = end.replace(year=end.year - 1, day=28)
    return prev + timedelta(days=1)


def _raw_num(value) -> str:
    if value is None:
        return ""
    f = float(value)
    return str(int(f)) if f.is_integer() and abs(f) >= 1000 else str(f)


class _Facts:
    """1 書類の xbrl_financials（点のみ）。連結区分は書類で 1 つ。配当は単体の context にある。"""

    def __init__(self, rows):
        self.rows = rows
        consolidated = [r for r in rows if r.is_consolidated]
        self.consolidated = bool(consolidated)
        self.mine = consolidated if consolidated else rows

    def pick(self, code: str, kind: str, *, target: str | None = None, start=None, end=None):
        for r in self.rows if code == "dps" else self.mine:
            if r.code != code or r.value_kind != kind:
                continue
            if target is not None and r.target_period != target:
                continue
            if start is not None and r.period_start != start:
                continue
            if end is not None and r.period_end != end:
                continue
            return float(r.value)
        return None


def _standard(filing: XbrlFiling) -> str:
    title = unicodedata.normalize("NFKC", filing.title or "")
    if "REIT" in title.upper():
        return "REIT"
    return STANDARD_SUFFIX.get(filing.accounting_standard or "", "JP")


def _financial_row(filing: XbrlFiling, facts: _Facts) -> dict | None:
    period = PERIOD_TYPES.get(filing.period_type or "")
    actual = [r for r in facts.mine if r.value_kind == "actual" and r.code in ("net_sales", "operating_income", "net_income")]
    if period is None or not actual:
        return None
    lead = min(actual, key=lambda r: (r.code != "net_sales", r.period_end))
    fy_start, period_end = lead.period_start, lead.period_end
    if fy_start is None:
        return None
    fy = [r for r in facts.mine if r.value_kind == "forecast" and r.target_period == "FY" and r.period_start == fy_start]
    if period == "FY":
        fy_end = period_end
    elif fy:
        fy_end = fy[0].period_end
    else:
        fy_end = _one_year_end(fy_start)
    row = {col: facts.pick(code, "actual", end=period_end) for code, col in PL.items()}
    raw = {RAW_PL[code]: _raw_num(row[col]) for code, col in PL.items()}
    for code, key in RAW_RATIOS.items():
        raw[key] = _raw_num(facts.pick(code, "actual", end=period_end))
    for target, key in RAW_DIV.items():
        raw[key] = _raw_num(facts.pick("dps", "actual", target=target, end=fy_end))
    if period == "FY":
        later = [r.period_end for r in facts.mine
                 if r.value_kind == "forecast" and r.target_period == "FY" and r.period_end > fy_end]
        nx_end = min(later) if later else None
        nx_start = fy_end + timedelta(days=1)
        raw["NxtFYSt"] = nx_start.isoformat() if nx_end else ""
        raw["NxtFYEn"] = nx_end.isoformat() if nx_end else ""
        for code, col in PL.items():
            full = facts.pick(code, "forecast", target="FY", end=nx_end) if nx_end else None
            half = facts.pick(code, "forecast", target="H1", start=nx_start)
            raw["NxF" + RAW_NEXT_PL[code]] = _raw_num(full)
            raw["NxF" + RAW_NEXT_PL[code] + "2Q"] = _raw_num(half)
            row["fy_" + col] = None
            row["f2_" + col] = None
        for target, key in RAW_DIV.items():
            raw["NxF" + key] = _raw_num(facts.pick("dps", "forecast", target=target, end=nx_end)) if nx_end else ""
    else:
        for code, col in PL.items():
            row["fy_" + col] = facts.pick(code, "forecast", target="FY", end=fy_end)
            row["f2_" + col] = facts.pick(code, "forecast", target="H1", start=fy_start)
            raw["F" + RAW_PL[code]] = _raw_num(row["fy_" + col])
            raw["F" + RAW_PL[code] + "2Q"] = _raw_num(row["f2_" + col])
        for target, key in RAW_DIV.items():
            raw["F" + key] = _raw_num(facts.pick("dps", "forecast", target=target, end=fy_end))
    kind = "Consolidated" if facts.consolidated else "NonConsolidated"
    row.update(
        type_of_period=period,
        type_of_document=f"{period}FinancialStatements_{kind}_{_standard(filing)}",
        period_start=fy_start,
        period_end=period_end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        is_revision=False,
    )
    row["_raw"] = raw
    return row


def _revision_row(filing: XbrlFiling, facts: _Facts) -> dict | None:
    fc = [r for r in facts.mine if r.value_kind == "forecast"]
    if not fc:
        return None
    fy_rows = [r for r in fc if r.target_period == "FY" and r.code != "dps"]
    half_rows = [r for r in fc if r.target_period == "H1"]
    dps_rows = [r for r in fc if r.code == "dps"]
    if fy_rows:
        fy_end = fy_rows[0].period_end
        fy_start = fy_rows[0].period_start
    elif half_rows:
        fy_start = half_rows[0].period_start
        fy_end = _one_year_end(fy_start) if fy_start else filing.fiscal_year_end
    elif dps_rows:
        fy_end = dps_rows[0].period_end
        fy_start = dps_rows[0].period_start
    else:
        return None
    if fy_end is None:
        return None
    if fy_start is None:
        fy_start = _year_start(fy_end)
    half_end = half_rows[0].period_end if half_rows else None
    row = {col: None for col in PL.values()}
    raw = {}
    for code, col in PL.items():
        row["fy_" + col] = facts.pick(code, "forecast", target="FY", end=fy_end)
        row["f2_" + col] = facts.pick(code, "forecast", target="H1", start=fy_start)
        raw["F" + RAW_PL[code]] = _raw_num(row["fy_" + col])
        raw["F" + RAW_PL[code] + "2Q"] = _raw_num(row["f2_" + col])
    for target, key in RAW_DIV.items():
        raw["F" + key] = _raw_num(facts.pick("dps", "forecast", target=target, end=fy_end))
    period = "2Q" if half_end else "FY"
    row.update(
        type_of_period=period,
        type_of_document=REVISION_DOC_TYPES[filing.doc_type],
        period_start=fy_start,
        period_end=half_end or fy_end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        is_revision=True,
    )
    row["_raw"] = raw
    return row


def statement_row(filing: XbrlFiling, rows) -> dict | None:
    """1 書類から statements の 1 行（dict）。作れなければ None。"""
    facts = _Facts(rows)
    if filing.doc_type in FINANCIAL_DOC_TYPES:
        row = _financial_row(filing, facts)
    elif filing.doc_type in REVISION_DOC_TYPES:
        row = _revision_row(filing, facts)
    else:
        return None
    if row is None:
        return None
    submitted: datetime = filing.submitted_at
    no = disclosure_no_of(filing.doc_id)
    raw = row.pop("_raw")
    row.update(
        disclosure_no=no,
        code=filing.code,
        disclosed_date=submitted.date(),
        disclosed_time=submitted.strftime("%H:%M:%S"),
    )
    raw.update({
        RAW_MARKER: SOURCE_XBRL,
        "DiscNo": no,
        "Code": filing.code,
        "DiscDate": row["disclosed_date"].isoformat(),
        "DiscTime": row["disclosed_time"],
        "DocType": row["type_of_document"],
        "CurPerType": row["type_of_period"],
        "CurFYSt": row["fiscal_year_start"].isoformat(),
        "CurFYEn": row["fiscal_year_end"].isoformat(),
    })
    row["raw_json"] = json.dumps(raw, ensure_ascii=False)
    return row


def is_xbrl_raw(raw_json: str | None) -> bool:
    if not raw_json:
        return False
    try:
        return json.loads(raw_json).get(RAW_MARKER) == SOURCE_XBRL
    except (ValueError, AttributeError):
        return False


def merge_into(db: Session, row: dict) -> str:
    """XBRL の行を入れる。戻りは inserted / updated。"""
    existing = db.get(Statement, row["disclosure_no"])
    if existing is None:
        db.add(Statement(**row, source=SOURCE_XBRL))
        return "inserted"
    for col in NUMERIC_COLUMNS:
        if row.get(col) is not None:
            setattr(existing, col, row[col])
    if existing.source == SOURCE_XBRL:
        for col in META_COLUMNS:
            setattr(existing, col, row[col])
    if not existing.raw_json or is_xbrl_raw(existing.raw_json):
        existing.raw_json = row["raw_json"]
    existing.source = SOURCE_XBRL
    return "updated"


def filings_since(db: Session, since: date) -> list[XbrlFiling]:
    kinds = [*FINANCIAL_DOC_TYPES, *REVISION_DOC_TYPES]
    return (db.query(XbrlFiling)
            .filter(XbrlFiling.source == "tdnet",
                    XbrlFiling.parse_status == "parsed",
                    XbrlFiling.doc_type.in_(kinds),
                    XbrlFiling.submitted_at >= datetime.combine(since, datetime.min.time()))
            .order_by(XbrlFiling.submitted_at, XbrlFiling.id)
            .all())


def _facts_by_filing(db: Session, ids: list[int]) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in (db.query(XbrlFinancial.filing_id, XbrlStdItem.code, XbrlFinancial.value_kind,
                           XbrlFinancial.target_period, XbrlFinancial.period_start, XbrlFinancial.period_end,
                           XbrlFinancial.is_consolidated, XbrlFinancial.value)
                  .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
                  .filter(XbrlFinancial.filing_id.in_(chunk),
                          XbrlStdItem.code.in_(ITEM_CODES),
                          XbrlFinancial.value_kind.in_(("actual", "forecast")),
                          XbrlFinancial.range_side == "point")
                  .order_by(XbrlFinancial.filing_id, XbrlFinancial.id)):
            out[r.filing_id].append(r)
    return out


def sync_statements(db: Session, since: date) -> dict[str, int]:
    filings = filings_since(db, since)
    facts = _facts_by_filing(db, [f.id for f in filings])
    stats = {"filings": len(filings), "inserted": 0, "updated": 0, "skipped": 0}
    for filing in filings:
        row = statement_row(filing, facts.get(filing.id, []))
        if row is None:
            stats["skipped"] += 1
            continue
        stats[merge_into(db, row)] += 1
    db.commit()
    return stats

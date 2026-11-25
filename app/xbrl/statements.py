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

from app.models import DIVIDEND_ITEM, WITHDRAWABLE_ITEMS, Statement, XbrlFiling, XbrlFinancial, XbrlStdItem

SOURCE_XBRL = "xbrl"
SOURCE_JQUANTS = "jquants"
RAW_MARKER = "Source"

FINANCIAL_DOC_TYPES = {"annual": None, "quarterly": None, "semiannual": None}
REVISION_DOC_TYPES = {"forecast_revision": "EarnForecastRevision", "dividend_revision": "DividendForecastRevision"}
NOTICE_DOC_TYPE = "other"
MARK_DOC_TYPES = (*REVISION_DOC_TYPES, NOTICE_DOC_TYPE)
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
WITHDRAWABLE_CODES = {**PL, "dps": DIVIDEND_ITEM}
WITHDRAWABLE_TARGETS = {"dps": "annual"}
DIVIDEND_KEPT_BY = "year_end"
BEFORE, AFTER = "forecast_previous", "forecast"
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
        nx_start = fy_end + timedelta(days=1)
        later = [r.period_end for r in facts.rows
                 if r.value_kind == "forecast" and r.target_period in ("FY", "annual", "year_end")
                 and r.period_end > fy_end]
        nx_end = min(later) if later else _one_year_end(nx_start)
        raw["NxtFYSt"] = nx_start.isoformat()
        raw["NxtFYEn"] = nx_end.isoformat()
        for code, col in PL.items():
            full = facts.pick(code, "forecast", target="FY", end=nx_end)
            half = facts.pick(code, "forecast", target="H1", start=nx_start)
            raw["NxF" + RAW_NEXT_PL[code]] = _raw_num(full)
            raw["NxF" + RAW_NEXT_PL[code] + "2Q"] = _raw_num(half)
            row["fy_" + col] = None
            row["f2_" + col] = None
        for target, key in RAW_DIV.items():
            raw["NxF" + key] = _raw_num(facts.pick("dps", "forecast", target=target, end=nx_end))
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


def _revision_row(filing: XbrlFiling, facts: _Facts, standalone_company: bool) -> dict | None:
    fc = [r for r in facts.mine if r.value_kind == "forecast"]
    if not fc:
        return None
    fy_rows = [r for r in fc if r.target_period == "FY" and r.code != "dps"]
    half_rows = [r for r in fc if r.target_period == "H1"]
    dps_rows = [r for r in facts.rows if r.value_kind == "forecast" and r.code == "dps"]
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
    individual = not facts.consolidated and not standalone_company
    prefix = "FNC" if individual else "F"
    for code, col in PL.items():
        full = facts.pick(code, "forecast", target="FY", end=fy_end)
        half = facts.pick(code, "forecast", target="H1", start=fy_start)
        row["fy_" + col] = None if individual else full
        row["f2_" + col] = None if individual else half
        raw[prefix + RAW_PL[code]] = _raw_num(full)
        raw[prefix + RAW_PL[code] + "2Q"] = _raw_num(half)
    for target, key in RAW_DIV.items():
        raw["F" + key] = _raw_num(facts.pick("dps", "forecast", target=target, end=fy_end))
    period = "2Q" if half_end and not dps_rows else "FY"
    row.update(
        type_of_period=period,
        type_of_document=REVISION_DOC_TYPES[filing.doc_type],
        period_start=fy_start,
        period_end=half_end if period == "2Q" else fy_end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        is_revision=True,
    )
    row["_raw"] = raw
    return row


def _dropped(rows) -> set[str]:
    before = {r.code for r in rows if r.value_kind == BEFORE}
    after = {r.code for r in rows if r.value_kind == AFTER}
    return {WITHDRAWABLE_CODES[c] for c in before - after}


def withdrawn_forecasts(points, marks, *, standalone_company: bool) -> tuple[str, ...]:
    rows = [r for r in marks if r.code in WITHDRAWABLE_CODES
            and r.target_period == WITHDRAWABLE_TARGETS.get(r.code, "FY")]
    pl = [r for r in rows if r.code in PL]
    consolidated = _Facts(points).consolidated or any(r.is_consolidated for r in pl)
    individual = not consolidated and not standalone_company
    out = _dropped([r for r in rows if r.code == "dps"])
    if any(r.code == "dps" and r.target_period == DIVIDEND_KEPT_BY and r.value_kind == AFTER for r in marks):
        out.discard(DIVIDEND_ITEM)
    if not individual:
        out |= _dropped([r for r in pl if bool(r.is_consolidated) == consolidated])
    return tuple(i for i in WITHDRAWABLE_ITEMS if i in out)


def statement_row(filing: XbrlFiling, rows, *, standalone_company: bool = False) -> dict | None:
    """1 書類から statements の 1 行（dict）。作れなければ None。

    standalone_company は直近の決算短信が単体の会社。単体だけの業績予想の修正を、連結の会社なら
    J-Quants と同じく FNC* に分け、fy_* / f2_* には入れない（個別予想を連結予想と混ぜない）。
    """
    if _standard(filing) == "REIT":
        return None
    facts = _Facts(rows)
    if filing.doc_type in FINANCIAL_DOC_TYPES:
        row = _financial_row(filing, facts)
    elif filing.doc_type in REVISION_DOC_TYPES:
        row = _revision_row(filing, facts, standalone_company)
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
    if "withdrawn_forecasts" in row:
        existing.withdrawn_forecasts = row["withdrawn_forecasts"]
    existing.source = SOURCE_XBRL
    return "updated"


def mark_withdrawn(db: Session, disclosure_no: str, items: tuple[str, ...] | None) -> bool:
    if not items:
        return False
    existing = db.get(Statement, disclosure_no)
    if existing is None:
        return False
    existing.withdrawn_forecasts = items
    return True


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


def notice_filings_since(db: Session, since: date | None) -> list[XbrlFiling]:
    q = db.query(XbrlFiling).filter(XbrlFiling.source == "tdnet", XbrlFiling.parse_status == "parsed",
                                    XbrlFiling.doc_type == NOTICE_DOC_TYPE)
    if since is not None:
        q = q.filter(XbrlFiling.submitted_at >= datetime.combine(since, datetime.min.time()))
    return q.order_by(XbrlFiling.submitted_at, XbrlFiling.id).all()


def _mark_facts_by_filing(db: Session, ids: list[int]) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in (db.query(XbrlFinancial.filing_id, XbrlStdItem.code, XbrlFinancial.value_kind,
                           XbrlFinancial.target_period, XbrlFinancial.is_consolidated)
                  .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
                  .filter(XbrlFinancial.filing_id.in_(chunk),
                          XbrlStdItem.code.in_(tuple(WITHDRAWABLE_CODES)),
                          XbrlFinancial.value_kind.in_((BEFORE, AFTER)),
                          XbrlFinancial.target_period.in_(("FY", "annual", DIVIDEND_KEPT_BY)))
                  .order_by(XbrlFinancial.filing_id, XbrlFinancial.id)):
            out[r.filing_id].append(r)
    return {k: v for k, v in out.items() if any(r.value_kind == BEFORE for r in v)}


def withdrawals_by_filing(db: Session, filings: list[XbrlFiling]) -> dict[int, tuple[str, ...]]:
    marks = _mark_facts_by_filing(db, [f.id for f in filings if f.doc_type in MARK_DOC_TYPES])
    points = _facts_by_filing(db, list(marks))
    out: dict[int, tuple[str, ...]] = {}
    for f in filings:
        if f.id not in marks:
            continue
        standalone = is_standalone_company(db, f.code, f.submitted_at.date())
        items = withdrawn_forecasts(points.get(f.id, []), marks[f.id], standalone_company=standalone)
        if items:
            out[f.id] = items
    return out


def is_standalone_company(db: Session, code: str, before: date) -> bool:
    """その日までの直近の決算短信が単体か。決算短信が無ければ False（連結として扱う）。"""
    doc = (db.query(Statement.type_of_document)
           .filter(Statement.code == code, Statement.disclosed_date <= before,
                   Statement.type_of_document.contains("FinancialStatements"))
           .order_by(Statement.disclosed_date.desc(), Statement.disclosure_no.desc())
           .limit(1).scalar())
    return "NonConsolidated" in (doc or "")


def sync_statements(db: Session, since: date) -> dict[str, int]:
    filings = filings_since(db, since)
    notices = notice_filings_since(db, since)
    facts = _facts_by_filing(db, [f.id for f in filings])
    withdrawn = withdrawals_by_filing(db, [*filings, *notices])
    stats = {"filings": len(filings), "inserted": 0, "updated": 0, "skipped": 0, "marked": 0}
    for filing in filings:
        revision = filing.doc_type in REVISION_DOC_TYPES
        standalone = revision and is_standalone_company(db, filing.code, filing.submitted_at.date())
        row = statement_row(filing, facts.get(filing.id, []), standalone_company=standalone)
        if row is None:
            marked = mark_withdrawn(db, disclosure_no_of(filing.doc_id), withdrawn.get(filing.id))
            stats["marked" if marked else "skipped"] += 1
            continue
        if revision:
            row["withdrawn_forecasts"] = withdrawn.get(filing.id)
        stats[merge_into(db, row)] += 1
    for filing in notices:
        stats["marked"] += mark_withdrawn(db, disclosure_no_of(filing.doc_id), withdrawn.get(filing.id))
    db.commit()
    return stats

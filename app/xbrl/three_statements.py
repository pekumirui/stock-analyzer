from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

__all__ = [
    "DEFAULT_PERIODS",
    "MAX_PERIODS",
    "STATEMENTS",
    "Fact",
    "Filing",
    "Names",
    "apply_learned",
    "financial_table",
    "learn_pair",
    "load_filings",
    "name_index",
    "sections",
]

STATEMENTS = ("BS", "PL", "CF")
DEFAULT_PERIODS = 8
MAX_PERIODS = 20
SOURCES = ("edinet", "tdnet", "tdnet_pdf")
SOURCE_LABEL = {"edinet": "有報", "tdnet": "XBRL", "tdnet_pdf": "PDF"}
DOC_TYPES = ("annual", "semiannual", "quarterly")
PDF_NS = {"pdf-bs": "BS", "pdf-pl": "PL", "pdf-cf": "CF"}
STANDARD_NS = frozenset({"jppfs_cor", "jpigp_cor"})
EXCLUDED_NS = ("jpdei_cor", "jpcrp_cor", "tse-ed-t", "tse-re-t", "pdf-sum")
CONSOL_MEMBERS = frozenset({"ConsolidatedMember", "NonConsolidatedMember"})
DEI_CONSOLIDATED = "WhetherConsolidatedFinancialStatementsArePreparedDEI"
PER_SHARE_UNITS = frozenset({"JPYPerShares"})
MONEY_UNITS = frozenset({None, "JPY"})

BS_TOTALS = frozenset({
    "Assets", "Liabilities", "NetAssets", "LiabilitiesAndNetAssets",
    "AssetsIFRS", "LiabilitiesIFRS", "EquityIFRS", "LiabilitiesAndEquityIFRS",
})
CF_MARK_RE = re.compile(
    r"(OpeCF|InvCF|FinCF)|^(NetCashProvidedByUsedIn|NetIncreaseDecreaseInCashAndCashEquivalents|EffectOfExchangeRateChange)"
)
CF_TOTAL_RE = re.compile(r"^NetCashProvidedByUsedIn")
CF_CASH_RE = re.compile(r"^CashAndCashEquivalents")
PL_END_RE = re.compile(r"ProfitLoss|NetIncome|ComprehensiveIncome|EarningsLossPerShare|EarningsPerShare")
SUFFIX_RE = re.compile(r"#\d+$")
LABEL_NOISE_RES = (
    re.compile(r"又は[^（(]*?(?:（△）|\(△\))"),
    re.compile(r"（△[^）]*）|\(△[^)]*\)"),
)
SECTION_HEADS = (
    "流動資産", "投資その他の資産", "繰延資産", "流動負債", "固定負債", "株主資本",
    "その他の包括利益累計額", "評価・換算差額等", "営業外収益", "営業外費用", "特別利益", "特別損失",
)
GENERIC_LABELS = frozenset({"その他"})
MIN_LOOSE_MATCH = 100
NAME_WEIGHT = 1
KEPT_WEIGHT = 1_000_000

BS_GROUPS = {
    "Assets": "資産", "Liabilities": "負債", "NetAssets": "純資産",
    "AssetsIFRS": "資産", "LiabilitiesIFRS": "負債", "EquityIFRS": "資本",
}
BS_GROUP_ORDERS = (("Assets", "Liabilities", "NetAssets"), ("AssetsIFRS", "LiabilitiesIFRS", "EquityIFRS"))
BS_GROUP_END = frozenset({"LiabilitiesAndNetAssets", "LiabilitiesAndEquityIFRS"})
BS_SECTIONS = {
    "CurrentAssets": "Assets", "PropertyPlantAndEquipment": "Assets", "IntangibleAssets": "Assets",
    "InvestmentsAndOtherAssets": "Assets", "NoncurrentAssets": "Assets", "DeferredAssets": "Assets",
    "CurrentLiabilities": "Liabilities", "NoncurrentLiabilities": "Liabilities",
    "ShareholdersEquity": "NetAssets", "ValuationAndTranslationAdjustments": "NetAssets",
    "CurrentAssetsIFRS": "AssetsIFRS", "NonCurrentAssetsIFRS": "AssetsIFRS",
    "TotalCurrentLiabilitiesIFRS": "LiabilitiesIFRS", "NonCurrentLabilitiesIFRS": "LiabilitiesIFRS",
    "EquityAttributableToOwnersOfParentIFRS": "EquityIFRS",
}
SECTION_NAMES = {
    "CurrentAssets": "流動資産", "PropertyPlantAndEquipment": "有形固定資産", "IntangibleAssets": "無形固定資産",
    "InvestmentsAndOtherAssets": "投資その他の資産", "NoncurrentAssets": "固定資産", "DeferredAssets": "繰延資産",
    "CurrentLiabilities": "流動負債", "NoncurrentLiabilities": "固定負債", "ShareholdersEquity": "株主資本",
    "ValuationAndTranslationAdjustments": "その他の包括利益累計額",
    "CurrentAssetsIFRS": "流動資産", "NonCurrentAssetsIFRS": "非流動資産", "TotalCurrentLiabilitiesIFRS": "流動負債",
    "NonCurrentLabilitiesIFRS": "非流動負債", "EquityAttributableToOwnersOfParentIFRS": "親会社の所有者に帰属する持分",
    "NetCashProvidedByUsedInOperatingActivities": "営業活動によるキャッシュ・フロー",
    "NetCashProvidedByUsedInInvestmentActivities": "投資活動によるキャッシュ・フロー",
    "NetCashProvidedByUsedInFinancingActivities": "財務活動によるキャッシュ・フロー",
}
CF_SECTION_RE = re.compile(r"^NetCashProvidedByUsedIn(Operating|Investment|Investing|Financing)Activities(IFRS)?$")
CF_SECTION_ORDER = {"Operating": 0, "Investment": 1, "Investing": 1, "Financing": 2}
TOTAL_TAIL_RE = re.compile(r"(合計|計)$")


@dataclass(frozen=True)
class Fact:
    id: int
    key: str
    local: str
    label: str
    instant: bool
    unit: str | None
    value: float


@dataclass
class Filing:
    id: int
    doc_id: str
    source: str
    period_type: str | None
    period_end: date
    submitted: str
    standard: str | None
    by_statement: dict[str, list[Fact]] = field(default_factory=dict)
    seq: list[Fact] = field(default_factory=list)

    @property
    def is_pdf(self) -> bool:
        return self.source == "tdnet_pdf"

    @property
    def cls(self) -> str:
        return "FY" if self.period_type == "FY" else "Q"


@dataclass(frozen=True)
class Mapped:
    key: str
    sign: int


@dataclass
class Learned:
    period_end: date
    cls: str
    maps: dict[str, dict[str, Mapped]] = field(default_factory=dict)
    order: dict[str, list[str]] = field(default_factory=dict)
    extras: dict[str, dict[str, str | None]] = field(default_factory=dict)
    pdf_names: dict[str, str] = field(default_factory=dict)


@dataclass
class Names:
    keys: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


@dataclass
class Column:
    primary: Filing
    values: dict[str, float]
    order: list[str]
    sources: dict[str, str]

    @property
    def period_end(self) -> date:
        return self.primary.period_end

    @property
    def source(self) -> str:
        return self.primary.source

    @property
    def standard(self) -> str | None:
        return self.primary.standard


def financial_table(
    db: Session,
    code: str,
    *,
    statement: str = "BS",
    periods: int = DEFAULT_PERIODS,
    consolidated: bool | None = None,
) -> dict[str, Any]:
    if statement not in STATEMENTS:
        raise ValueError(f"statement must be one of {', '.join(STATEMENTS)}")
    both = load_both(db, code)
    if consolidated is None:
        consolidated = _has_material(both[True]) or not _has_material(both[False])
    filings = both[consolidated]
    learned = [learn_pair(pdf, xbrl) for pdf, xbrl in _pairs(filings)]
    columns = build_columns(filings, learned, statement)[-periods:]
    labels = _labels(filings, learned)
    units = _units(filings)
    keys: list[str] = []
    for column in reversed(columns):
        keys = _merge(keys, column.order)
    parts = sections(keys, statement, labels)
    rows = [
        {
            "key": key,
            "label": labels.get(key, key),
            "unit": "per_share" if units.get(key) in PER_SHARE_UNITS else "money",
            "unmatched_pdf": key.startswith("pdf:"),
            "group": group,
            "section": section,
            "cells": [
                None if key not in column.values
                else {"value": column.values[key], "source": column.sources[key]}
                for column in columns
            ],
        }
        for key, (group, section) in zip(keys, parts)
    ]
    empty = None
    if not columns:
        empty = {
            "reason": "no_material",
            "message": "有報・短信 XBRL・短信 PDF の本表が無い。米国基準は PDF を読んでいない",
            "other_basis_available": _has_material(both[not consolidated]),
        }
    return {
        "statement": statement,
        "consolidated": consolidated,
        "basis": "連結" if consolidated else "個別",
        "accounting_standards": sorted({c.standard for c in columns if c.standard}),
        "cumulative": statement != "BS",
        "unit": "JPY",
        "periods": [_period(c) for c in columns],
        "rows": rows,
        "empty": empty,
    }


def _period(column: Column) -> dict[str, Any]:
    f = column.primary
    return {
        "period_end": f.period_end.isoformat(),
        "label": _yymm(f.period_end),
        "period_type": f.period_type,
        "source": f.source,
        "source_label": SOURCE_LABEL.get(f.source, ""),
        "doc_id": f.doc_id,
        "submitted_at": f.submitted[:16].replace("T", " ") or None,
        "accounting_standard": f.standard,
    }


def _has_material(filings: Sequence[Filing]) -> bool:
    return any(f.by_statement for f in filings)


def load_filings(db: Session, code: str, consolidated: bool) -> list[Filing]:
    return load_both(db, code)[consolidated]


def load_both(db: Session, code: str) -> dict[bool, list[Filing]]:
    rows = db.execute(
        text(
            "SELECT id, doc_id, source, period_type, fiscal_year_end, submitted_at, accounting_standard "
            "FROM xbrl_filings WHERE code = :code AND parse_status = 'parsed' "
            "AND source IN :sources AND doc_type IN :types AND fiscal_year_end IS NOT NULL"
        ).bindparams(bindparam("sources", expanding=True), bindparam("types", expanding=True)),
        {"code": code, "sources": list(SOURCES), "types": list(DOC_TYPES)},
    ).all()
    filings: dict[bool, dict[int, Filing]] = {
        basis: {
            int(r[0]): Filing(
                id=int(r[0]),
                doc_id=str(r[1] or ""),
                source=str(r[2]),
                period_type=None if r[3] is None else str(r[3]),
                period_end=date.fromisoformat(str(r[4])[:10]),
                submitted=str(r[5] or ""),
                standard=None if r[6] is None else str(r[6]),
            )
            for r in rows
        }
        for basis in (True, False)
    }
    if not rows:
        return {True: [], False: []}
    ids = sorted(filings[True])
    single = _single_entity(db, ids)
    picked: dict[bool, dict[int, list[Fact]]] = {True: defaultdict(list), False: defaultdict(list)}
    sql = text(
        "SELECT f.filing_id, f.id, e.namespace_key, e.local_name, e.label_ja, e.is_custom, "
        "c.period_type, c.is_consolidated, c.has_dimensions, c.dimensions_json, f.unit, f.value_num "
        "FROM xbrl_facts f "
        "JOIN xbrl_contexts c ON c.id = f.context_id "
        "JOIN xbrl_elements e ON e.id = f.element_id "
        "WHERE f.filing_id IN :ids "
        "AND c.relative_period = 'current' AND f.value_num IS NOT NULL "
        "AND e.namespace_key NOT IN :excluded "
        "ORDER BY f.filing_id, f.id"
    ).bindparams(bindparam("ids", expanding=True), bindparam("excluded", expanding=True))
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for r in db.execute(sql, {"ids": chunk, "excluded": list(EXCLUDED_NS)}):
            fid = int(r[0])
            ns = str(r[2])
            local = str(r[3])
            is_pdf = ns in PDF_NS
            if not is_pdf and ns not in STANDARD_NS and not r[5]:
                continue
            if not _primary_dims(r[8], r[9]):
                continue
            unit = None if r[10] is None else str(r[10])
            if unit not in MONEY_UNITS and unit not in PER_SHARE_UNITS:
                continue
            is_consol = bool(r[7]) and fid not in single
            if is_pdf:
                key = f"pdf:{local}"
            elif ns in STANDARD_NS:
                key = local
            else:
                key = f"ext:{local}"
            fact = Fact(
                id=int(r[1]), key=key, local=local, label=str(r[4] or ""),
                instant=str(r[6]) == "instant", unit=unit, value=float(r[11]),
            )
            if is_pdf:
                filings[is_consol][fid].by_statement.setdefault(PDF_NS[ns], []).append(fact)
            else:
                picked[is_consol][fid].append(fact)
    out: dict[bool, list[Filing]] = {}
    for basis, by_id in filings.items():
        for fid, facts in picked[basis].items():
            filing = by_id[fid]
            filing.seq = facts
            filing.by_statement = split_xbrl(facts)
        for filing in by_id.values():
            filing.by_statement = {s: v for s, v in filing.by_statement.items() if v}
        out[basis] = sorted(by_id.values(), key=lambda f: (f.period_end, f.submitted, f.id))
    return out


def _single_entity(db: Session, ids: Sequence[int]) -> set[int]:
    out: set[int] = set()
    sql = text(
        "SELECT f.filing_id, f.value_text FROM xbrl_facts f "
        "JOIN xbrl_elements e ON e.id = f.element_id "
        "WHERE f.filing_id IN :ids "
        "AND e.namespace_key = 'jpdei_cor' AND e.local_name = :name"
    ).bindparams(bindparam("ids", expanding=True))
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for fid, value in db.execute(sql, {"ids": chunk, "name": DEI_CONSOLIDATED}):
            if str(value or "").strip().lower() == "false":
                out.add(int(fid))
    return out


def _primary_dims(has_dims: object, raw: object) -> bool:
    if not has_dims or not raw:
        return True
    try:
        dims = json.loads(str(raw))
    except json.JSONDecodeError:
        return False
    if not isinstance(dims, dict):
        return False
    return all(str(v).split(":", 1)[-1] in CONSOL_MEMBERS for v in dims.values())


def split_xbrl(seq: Sequence[Fact]) -> dict[str, list[Fact]]:
    bs_lo, bs_hi = _bs_run(seq)
    marks = [i for i, f in enumerate(seq) if CF_MARK_RE.search(f.local)]
    cf_lo = cf_hi = len(seq)
    if marks and any(CF_TOTAL_RE.search(seq[i].local) for i in marks):
        cf_lo, cf_hi = marks[0], marks[-1] + 1
        while cf_hi < len(seq) and seq[cf_hi].instant and CF_CASH_RE.search(seq[cf_hi].local):
            cf_hi += 1
    pl_from = bs_hi if bs_hi <= cf_lo else 0
    pl = [
        (i, f) for i, f in enumerate(seq[:cf_lo])
        if i >= pl_from and not f.instant and not (bs_lo <= i < bs_hi)
    ]
    ends = [n for n, (_, f) in enumerate(pl) if PL_END_RE.search(f.local)]
    if ends:
        pl = pl[: ends[-1] + 1]
    return {
        "BS": list(seq[bs_lo:bs_hi]),
        "PL": [f for _, f in pl],
        "CF": list(seq[cf_lo:cf_hi]),
    }


def _bs_run(seq: Sequence[Fact]) -> tuple[int, int]:
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(seq):
        if not seq[i].instant:
            i += 1
            continue
        j = i
        while j < len(seq) and seq[j].instant:
            j += 1
        runs.append((i, j))
        i = j
    if not runs:
        return (0, 0)
    marked = [r for r in runs if any(seq[k].local in BS_TOTALS for k in range(*r))]
    pool = marked or runs
    return max(pool, key=lambda r: r[1] - r[0])


def _granularity(values: Iterable[float]) -> float:
    nums = [abs(v) for v in values if v]
    for g in (1e6, 1e3):
        if nums and all(v % g == 0 for v in nums):
            return g
    return 1.0


def _same(a: float, b: float, tol: float) -> bool:
    if a == 0 or b == 0:
        return False
    if abs(a) == abs(b):
        return True
    return tol > 0 and abs(abs(a) - abs(b)) < tol


def _lcs(a: Sequence[float], b: Sequence[float], tol: float) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            if _same(a[i], b[j], tol):
                row[j] = nxt[j + 1] + 1
            else:
                row[j] = max(nxt[j], row[j + 1])
    out: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if _same(a[i], b[j], tol) and dp[i][j] == dp[i + 1][j + 1] + 1:
            out.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def learn_pair(pdf: Filing, xbrl: Filing) -> Learned:
    learned = Learned(period_end=pdf.period_end, cls=pdf.cls)
    pdf_money = [f.value for facts in pdf.by_statement.values() for f in facts if f.unit in MONEY_UNITS]
    xbrl_money = [f.value for f in xbrl.seq if f.unit in MONEY_UNITS]
    g_pdf, g_xbrl = _granularity(pdf_money), _granularity(xbrl_money)
    tol = max(g_pdf, g_xbrl) if g_pdf != g_xbrl else 0.0
    loose_floor = MIN_LOOSE_MATCH * max(g_pdf, g_xbrl)
    for statement, rows in pdf.by_statement.items():
        target = xbrl.by_statement.get(statement, [])
        pairs = _lcs([f.value for f in rows], [f.value for f in target], tol)
        hit: dict[int, Fact] = {i: target[j] for i, j in pairs}
        used = {target[j].id for _, j in pairs}
        counts = defaultdict(int)
        for f in rows:
            counts[abs(f.value)] += 1
        pool = defaultdict(list)
        for f in xbrl.seq:
            pool[abs(f.value)].append(f)
        for i, f in enumerate(rows):
            if i in hit or abs(f.value) < loose_floor or counts[abs(f.value)] != 1:
                continue
            cands = [c for c in _near(pool, f.value, tol) if c.id not in used]
            if len(cands) == 1 and len({c.key for c in _near(pool, f.value, tol)}) == 1:
                hit[i] = cands[0]
                used.add(cands[0].id)
        in_rule = {f.key for f in target}
        maps: dict[str, Mapped] = {}
        order: list[str] = []
        extras: dict[str, str | None] = {}
        prev: str | None = None
        for i, f in enumerate(rows):
            x = hit.get(i)
            if x is None:
                continue
            sign = -1 if f.value * x.value < 0 else 1
            maps[f.local] = Mapped(x.key, sign)
            order.append(x.key)
            learned.pdf_names.setdefault(x.key, f.local)
            if x.key not in in_rule:
                extras[x.key] = prev
            prev = x.key
        learned.maps[statement] = maps
        learned.order[statement] = order
        learned.extras[statement] = extras
    return learned


def _near(pool: Mapping[float, list[Fact]], value: float, tol: float) -> list[Fact]:
    if tol <= 0:
        return list(pool.get(abs(value), []))
    return [f for v, facts in pool.items() if _same(v, value, tol) for f in facts]


def _pairs(filings: Sequence[Filing]) -> list[tuple[Filing, Filing]]:
    by_end: dict[date, list[Filing]] = defaultdict(list)
    for f in filings:
        by_end[f.period_end].append(f)
    out: list[tuple[Filing, Filing]] = []
    for group in by_end.values():
        pdfs = [f for f in group if f.is_pdf and f.by_statement]
        xbrls = [f for f in group if not f.is_pdf and f.by_statement]
        if not pdfs or not xbrls:
            continue
        top = min(SOURCES.index(f.source) for f in xbrls)
        xbrl = _newest([f for f in xbrls if SOURCES.index(f.source) == top])
        out.append((_newest(pdfs), xbrl))
    return out


def _newest(filings: Sequence[Filing]) -> Filing:
    return max(filings, key=lambda f: (f.submitted, f.id))


def _ranked(learned: Sequence[Learned], period_end: date, cls: str) -> list[Learned]:
    return sorted(
        learned,
        key=lambda item: (item.cls != cls, abs((item.period_end - period_end).days), -item.period_end.toordinal()),
    )


def _global_order(learned: Sequence[Learned], statement: str) -> dict[str, int]:
    keys: list[str] = []
    for item in sorted(learned, key=lambda x: x.period_end, reverse=True):
        keys = _merge(keys, item.order.get(statement, []))
    return {k: i for i, k in enumerate(keys)}


def apply_learned(
    rows: Sequence[Fact], learned: Sequence[Learned], statement: str, period_end: date, cls: str,
    names: Names | None = None,
) -> list[tuple[Fact, Mapped | None]]:
    ranked = _ranked(learned, period_end, cls)
    proposals: list[Mapped | None] = []
    for f in rows:
        found = None
        for item in ranked:
            m = item.maps.get(statement, {}).get(f.local)
            if m is not None:
                found = m
                break
        proposals.append(found)
    claimed = {m.key for m in proposals if m is not None}
    counts = defaultdict(int)
    for m in proposals:
        if m is not None:
            counts[m.key] += 1
    proposals = [m if m is not None and counts[m.key] == 1 else None for m in proposals]
    rank = _global_order(learned, statement)
    keep = _lis([
        (i, rank[m.key], 1 if _generic(rows[i].local) else 2)
        for i, m in enumerate(proposals) if m is not None and m.key in rank
    ])
    by_name = _by_name(rows, learned, statement, names, claimed) if names else {}
    if by_name:
        order = _merge(sorted(rank, key=rank.__getitem__), names.order)
        rank = {k: i for i, k in enumerate(order)}
        picked = _lis([
            (i, rank[by_name[i].key] if i in by_name else rank[proposals[i].key],
             NAME_WEIGHT if i in by_name else KEPT_WEIGHT)
            for i in range(len(rows)) if (i in by_name and by_name[i].key in rank) or i in keep
        ])
        for i, m in by_name.items():
            if i in picked:
                proposals[i] = m
                keep.add(i)
    return [(f, proposals[i] if i in keep else None) for i, f in enumerate(rows)]


def name_index(filings: Sequence[Filing], statement: str) -> Names:
    found: dict[str, set[str]] = defaultdict(set)
    order: list[str] = []
    for filing in sorted(filings, key=lambda f: (f.period_end, f.submitted), reverse=True):
        if filing.is_pdf:
            continue
        facts = filing.by_statement.get(statement, [])
        for f in facts:
            name = _clean_label(f.label)
            if name:
                found[name].add(f.key)
        order = _merge(order, [f.key for f in facts])
    return Names({name: next(iter(keys)) for name, keys in found.items() if len(keys) == 1}, order)


def _by_name(
    rows: Sequence[Fact], learned: Sequence[Learned], statement: str, names: Names, claimed: set[str],
) -> dict[int, Mapped]:
    known = {local for item in learned for local in item.maps.get(statement, {})}
    base = defaultdict(int)
    for f in rows:
        base[_clean_label(SUFFIX_RE.sub("", f.local))] += 1
    out: dict[int, Mapped] = {}
    for i, f in enumerate(rows):
        name = _clean_label(SUFFIX_RE.sub("", f.local))
        if f.local in known or SUFFIX_RE.search(f.local) or base[name] != 1:
            continue
        key = names.keys.get(name)
        if key is not None and key not in claimed:
            out[i] = Mapped(key, 1)
    counts = defaultdict(int)
    for m in out.values():
        counts[m.key] += 1
    return {i: m for i, m in out.items() if counts[m.key] == 1}


def _generic(local: str) -> bool:
    return bool(SUFFIX_RE.search(local)) or local in GENERIC_LABELS


def _lis(items: Sequence[tuple[int, int, int]]) -> set[int]:
    best: list[int] = []
    back: list[int | None] = []
    for n, (_, rank, weight) in enumerate(items):
        score, parent = weight, None
        for k in range(n):
            if items[k][1] < rank and best[k] + weight > score:
                score, parent = best[k] + weight, k
        best.append(score)
        back.append(parent)
    if not best:
        return set()
    cur: int | None = max(range(len(best)), key=lambda n: best[n])
    out: set[int] = set()
    while cur is not None:
        out.add(items[cur][0])
        cur = back[cur]
    return out


def _merge(base: list[str], seq: Sequence[str]) -> list[str]:
    out = list(base)
    pos = {k: i for i, k in enumerate(out)}
    prev: str | None = None
    for key in seq:
        if key not in pos:
            at = pos[prev] + 1 if prev is not None else 0
            out.insert(at, key)
            pos = {k: i for i, k in enumerate(out)}
        prev = key
    return out


def _with_extras(filing: Filing, statement: str, learned: Sequence[Learned]) -> list[Fact]:
    facts = list(filing.by_statement.get(statement, []))
    present = {f.key for f in facts}
    by_key = {f.key: f for f in filing.seq}
    for item in _ranked(learned, filing.period_end, filing.cls):
        for key, prev in item.extras.get(statement, {}).items():
            if key in present or key not in by_key:
                continue
            keys = [f.key for f in facts]
            at = keys.index(prev) + 1 if prev in keys else 0
            facts.insert(at, by_key[key])
            present.add(key)
    return facts


def build_columns(filings: Sequence[Filing], learned: Sequence[Learned], statement: str) -> list[Column]:
    names = name_index(filings, statement)
    by_end: dict[date, list[Filing]] = defaultdict(list)
    for f in filings:
        if statement in f.by_statement:
            by_end[f.period_end].append(f)
    columns: list[Column] = []
    for period_end in sorted(by_end):
        group = by_end[period_end]
        chosen = []
        for source in SOURCES:
            same = [f for f in group if f.source == source]
            if same:
                chosen.append(_newest(same))
        values: dict[str, float] = {}
        sources: dict[str, str] = {}
        order: list[str] = []
        primary = chosen[0]
        for filing in chosen:
            seq: list[str] = []
            if filing.is_pdf:
                cls = filing.cls
                pairs = [x for x in learned if x.period_end == period_end]
                pool = pairs if not primary.is_pdf and pairs else learned
                facts = filing.by_statement[statement]
                by_name = names if filing is primary else None
                for fact, mapped in apply_learned(facts, pool, statement, period_end, cls, by_name):
                    if mapped is None:
                        if filing is primary:
                            values[fact.key] = fact.value
                            sources[fact.key] = filing.source
                            seq.append(fact.key)
                        continue
                    seq.append(mapped.key)
                    if mapped.key not in values:
                        values[mapped.key] = fact.value * mapped.sign
                        sources[mapped.key] = filing.source
            else:
                for fact in _with_extras(filing, statement, learned):
                    seq.append(fact.key)
                    if fact.key not in values:
                        values[fact.key] = fact.value
                        sources[fact.key] = filing.source
            order = _merge(order, [k for k in seq if k in values])
        columns.append(Column(primary, values, order, sources))
    return columns


def sections(keys: Sequence[str], statement: str, labels: Mapping[str, str]) -> list[tuple[str | None, str | None]]:
    out: list[tuple[str | None, str | None]] = [(None, None)] * len(keys)
    pos = {k: i for i, k in enumerate(keys)}
    if statement == "CF":
        totals = [(pos[k], CF_SECTION_ORDER[m.group(1)]) for k in keys if (m := CF_SECTION_RE.match(k))]
        totals.sort()
        if [n for _, n in totals] != sorted({n for _, n in totals}):
            return out
        _fill_sections(out, keys, [i for i, _ in totals], 0, len(keys), None, labels)
        return out
    if statement != "BS":
        return out
    groups = sorted(pos[k] for k in keys if k in BS_GROUPS)
    found = [keys[i] for i in groups]
    if not groups or found not in ([k for k in order if k in found] for order in BS_GROUP_ORDERS):
        return out
    end = min((pos[k] for k in keys if k in BS_GROUP_END), default=len(keys))
    if end < groups[-1]:
        return out
    lo = 0
    for gi in groups:
        group = keys[gi]
        inner = [pos[k] for k in keys if BS_SECTIONS.get(k) == group and lo <= pos[k] <= gi]
        _fill_sections(out, keys, sorted(inner), lo, gi + 1, _heading(group, labels, BS_GROUPS), labels)
        lo = gi + 1
    return out


def _fill_sections(
    out: list[tuple[str | None, str | None]], keys: Sequence[str], totals: Sequence[int], lo: int, hi: int,
    group: str | None, labels: Mapping[str, str],
) -> None:
    at = 0
    for i in range(lo, hi):
        while at < len(totals) and totals[at] < i:
            at += 1
        section = _heading(keys[totals[at]], labels, SECTION_NAMES) if at < len(totals) else None
        out[i] = (group, section)


def _heading(key: str, labels: Mapping[str, str], fallback: Mapping[str, str]) -> str:
    label = labels.get(key, "")
    name = "" if label == key else TOTAL_TAIL_RE.sub("", label)
    return name or fallback.get(key) or fallback.get(key.removesuffix("IFRS")) or key


def _clean_label(raw: str) -> str:
    out = unicodedata.normalize("NFKC", raw)
    for rx in LABEL_NOISE_RES:
        out = rx.sub("", out)
    return out.strip()


def _pdf_label(local: str, xbrl_label: str = "") -> str:
    name = SUFFIX_RE.sub("", local)
    if (
        len(xbrl_label) >= 2
        and xbrl_label not in GENERIC_LABELS
        and xbrl_label in name
        and xbrl_label != name
        and not name.endswith("計")
    ):
        return xbrl_label
    for head in sorted(SECTION_HEADS, key=len, reverse=True):
        rest = name[len(head):]
        if name.startswith(head) and rest and not rest.startswith("合計"):
            return rest
    return name


def _labels(filings: Sequence[Filing], learned: Sequence[Learned]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for filing in filings:
        for f in filing.seq:
            labels[f.key] = _clean_label(f.label) or f.local
        for facts in filing.by_statement.values():
            for f in facts:
                if f.key.startswith("pdf:"):
                    labels[f.key] = _pdf_label(f.local)
    xbrl_labels = dict(labels)
    for item in sorted(learned, key=lambda x: x.period_end):
        for key, local in item.pdf_names.items():
            labels[key] = _pdf_label(local, xbrl_labels.get(key, ""))
    return labels


def _units(filings: Sequence[Filing]) -> dict[str, str | None]:
    units: dict[str, str | None] = {}
    for filing in filings:
        for f in filing.seq:
            units[f.key] = f.unit
        for facts in filing.by_statement.values():
            for f in facts:
                units.setdefault(f.key, f.unit)
    return units


def _yymm(d: date) -> str:
    return f"{d.year % 100:02d}.{d.month:02d}"

from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
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
    "Mapped",
    "Names",
    "PdfMatch",
    "PdfRows",
    "apply_learned",
    "financial_table",
    "learn_pair",
    "load_filings",
    "name_index",
    "pdf_only_matches",
    "pdf_rows",
    "printed_twice",
    "sections",
    "split_by_layout",
    "statement_layouts",
    "strip_heads",
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
PRESENTATION_OK = "ok"
PERIOD_END_LABEL = "periodEndLabel"
PERIOD_START_LABEL = "periodStartLabel"
CI_ONLY_ROLE_RE = re.compile(r"StatementOfComprehensiveIncome(?!SingleStatement)")

BS_TOTALS = frozenset({
    "Assets", "Liabilities", "NetAssets", "LiabilitiesAndNetAssets",
    "AssetsIFRS", "LiabilitiesIFRS", "EquityIFRS", "LiabilitiesAndEquityIFRS",
})
CF_MARK_RE = re.compile(
    r"(OpeCF|InvCF|FinCF)|^(NetCashProvidedByUsedIn|NetIncreaseDecreaseInCashAndCashEquivalents|EffectOfExchangeRateChange)"
)
CF_TOTAL_RE = re.compile(r"^NetCashProvidedByUsedIn")
CF_CASH_RE = re.compile(r"^CashAndCashEquivalents")
CF_CASH_CHANGE_RE = re.compile(r"CCE$|InCashAndCashEquivalents")
PL_END_RE = re.compile(r"ProfitLoss|NetIncome|ComprehensiveIncome|EarningsLossPerShare|EarningsPerShare")
AHEAD_SKIP_RE = re.compile(r"SummaryOfBusinessResults|KeyFinancialData|InvestmentEquitySecurities")
SUFFIX_RE = re.compile(r"#\d+$")
OPENING_CASH_RE = re.compile(
    r"^[IVXⅠ-Ⅻ]*(?:会計方針の変更を反映した)?"
    r"(?:現金(?:及び|および)現金同等物の?期首残高|期首現金(?:及び|および)現金同等物残高)(?:\(.*計上額\))?$"
)
LABEL_NOISE_RES = (
    re.compile(r"又は[^（(]*?(?:（△）|\(△\))"),
    re.compile(r"（△[^）]*）|\(△[^)]*\)"),
)
SECTION_HEADS = (
    "流動資産", "投資その他の資産", "繰延資産", "流動負債", "固定負債", "株主資本",
    "その他の包括利益累計額", "評価・換算差額等", "営業外収益", "営業外費用", "特別利益", "特別損失",
)
GENERIC_LABELS = frozenset({"その他"})
SIGNED_PROFIT_RE = re.compile(r"^(.+利益)(?:又は.*損失\(△\)|\(△は?損失\))$")
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

PL_MARKERS = frozenset({
    "GrossProfit", "OperatingIncome", "NonOperatingIncome", "NonOperatingExpenses",
    "ExtraordinaryIncome", "ExtraordinaryLoss",
})
MATCH_MARKERS = {
    "BS": frozenset(BS_SECTIONS) | frozenset(BS_GROUPS),
    "PL": PL_MARKERS,
}
PDF_HEADS = {
    "BS": (
        "流動資産", "有形固定資産", "無形固定資産", "投資その他の資産", "繰延資産", "非流動資産", "流動負債",
        "固定負債", "非流動負債", "株主資本", "その他の包括利益累計額", "評価・換算差額等",
    ),
    "PL": ("営業外収益", "営業外費用", "特別利益", "特別損失"),
    "CF": ("営業活動によるキャッシュ・フロー", "投資活動によるキャッシュ・フロー", "財務活動によるキャッシュ・フロー"),
}
HEAD_KEEP_RE = re.compile(r"^(合計|小計|合$|計(#\d+|[─―ー]*)$|#|[（(、─―ー※・にへをと]|の(?!れん)|及び|並びに|等$)")
HEAD_SEP_RE = re.compile(r"^[:：]")


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
    by: str = "value"


@dataclass(frozen=True)
class PdfMatch:
    filing: Filing
    consolidated: bool
    statement: str
    fact: Fact
    mapped: Mapped | None


@dataclass
class Learned:
    period_end: date
    cls: str
    maps: dict[str, dict[str, Mapped]] = field(default_factory=dict)
    order: dict[str, list[str]] = field(default_factory=dict)
    extras: dict[str, dict[str, str | None]] = field(default_factory=dict)
    pdf_names: dict[str, str] = field(default_factory=dict)
    by_section: dict[str, dict[tuple[str, str], Mapped]] = field(default_factory=dict)
    twice: dict[str, set[str]] = field(default_factory=dict)
    losses: dict[str, dict[str, Mapped]] = field(default_factory=dict)
    homes: dict[str, dict[str, str | None]] = field(default_factory=dict)


@dataclass
class Names:
    keys: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    by_section: dict[tuple[str, str], str] = field(default_factory=dict)
    per_share: set[str] = field(default_factory=set)
    losses: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PdfRows:
    names: list[str]
    bases: list[str]
    copies: dict[int, int]


@dataclass
class Column:
    primary: Filing
    values: dict[str, float]
    order: list[str]
    sources: dict[str, str]
    consolidated: bool = True

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
    stitched = consolidated is None and _has_material(both[True]) and _has_material(both[False])
    if consolidated is None:
        consolidated = _has_material(both[True]) or not _has_material(both[False])
    columns, labels, units = _basis_columns(both[consolidated], consolidated, statement)
    if stitched:
        other, other_labels, other_units = _basis_columns(both[False], False, statement)
        columns = _stitch(columns, other)
        labels = {**other_labels, **labels}
        units = {**other_units, **units}
    columns = columns[-periods:]
    bases = {c.consolidated for c in columns}
    mixed = len(bases) > 1
    if bases and not mixed:
        consolidated = bases.pop()
    keys: list[str] = []
    for column in reversed(columns):
        keys = _merge(keys, column.order)
    if statement == "CF":
        keys = [k for k in keys if not _opening_cash(k)]
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
        "consolidated": None if mixed else consolidated,
        "basis": "連結・個別" if mixed else "連結" if consolidated else "個別",
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
        "consolidated": column.consolidated,
    }


def _basis_columns(
    filings: Sequence[Filing], consolidated: bool, statement: str,
) -> tuple[list[Column], dict[str, str], dict[str, str | None]]:
    learned = [learn_pair(pdf, xbrl) for pdf, xbrl in _pairs(filings)]
    columns = build_columns(filings, learned, statement)
    for column in columns:
        column.consolidated = consolidated
    return columns, _labels(filings, learned), _units(filings)


def _stitch(main: list[Column], other: list[Column]) -> list[Column]:
    if not main:
        return other
    lo, hi = main[0].period_end, main[-1].period_end
    outside = [c for c in other if c.period_end < lo or c.period_end > hi]
    return sorted(main + outside, key=lambda c: c.period_end)


def _has_material(filings: Sequence[Filing]) -> bool:
    return any(f.by_statement for f in filings)


def load_filings(db: Session, code: str, consolidated: bool) -> list[Filing]:
    return load_both(db, code)[consolidated]


def load_both(db: Session, code: str) -> dict[bool, list[Filing]]:
    rows = db.execute(
        text(
            "SELECT id, doc_id, source, period_type, fiscal_year_end, submitted_at, accounting_standard, "
            "presentation_status FROM xbrl_filings WHERE code = :code AND parse_status = 'parsed' "
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
    standards = {int(r[0]): None if r[6] is None else str(r[6]) for r in rows}
    layouts = _layouts(db, [int(r[0]) for r in rows if r[7] == PRESENTATION_OK], standards)
    picked: dict[bool, dict[int, list[Fact]]] = {True: defaultdict(list), False: defaultdict(list)}
    starts: dict[int, str] = {}
    sql = text(
        "SELECT f.filing_id, f.id, e.namespace_key, e.local_name, e.label_ja, e.is_custom, "
        "c.period_type, c.is_consolidated, c.has_dimensions, c.dimensions_json, f.unit, f.value_num, "
        "c.start_date "
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
                starts[fact.id] = str(r[12] or "9999")
    out: dict[bool, list[Filing]] = {}
    for basis, by_id in filings.items():
        for fid, facts in picked[basis].items():
            filing = by_id[fid]
            drop = _shorter_durations(facts, starts)
            filing.seq = [f for f in facts if f.id not in drop]
            if fid in layouts:
                filing.by_statement = split_by_layout(filing.seq, layouts[fid].get(basis, {}))
            else:
                filing.by_statement = {s: [f for f in v if f.id not in drop] for s, v in split_xbrl(facts).items()}
        for filing in by_id.values():
            filing.by_statement = {s: v for s, v in filing.by_statement.items() if v}
        out[basis] = sorted(by_id.values(), key=lambda f: (f.period_end, f.submitted, f.id))
    return out


@dataclass(frozen=True)
class Layout:
    first: dict[str, int]
    period_end: dict[str, int]
    closing: dict[str, int] = field(default_factory=dict)


def _item_key(item: Sequence[Any]) -> str:
    return f"ext:{item[1]}" if item[2] else str(item[1])


def statement_layouts(
    roles: Sequence[tuple[int, str, str, bool, bool, list]], standard: str | None,
) -> dict[bool, dict[str, Layout]]:
    """表示リンクの区分（seq, 区分名, statement, 連結か, IFRS か, 要素）→ 連結/個別 × 表の並び。

    同じ表・同じ連結区分に IFRS と日本基準の区分が両方あれば、書類の会計基準に合う方。
    区分は seq の順につなぐ。包括利益計算書だけの区分は損益計算書の区分の後ろ。
    """
    grouped: dict[tuple[bool, str], list[tuple[tuple[bool, int], bool, list]]] = defaultdict(list)
    for seq, role, statement, consolidated, ifrs, items in roles:
        rank = (CI_ONLY_ROLE_RE.search(role) is not None, seq)
        grouped[(bool(consolidated), statement)].append((rank, bool(ifrs), items))
    out: dict[bool, dict[str, Layout]] = {True: {}, False: {}}
    for (consolidated, statement), parts in grouped.items():
        if len({ifrs for _, ifrs, _ in parts}) > 1:
            want = standard == "IFRS"
            parts = [p for p in parts if p[1] == want]
        first: dict[str, int] = {}
        period_end: dict[str, int] = {}
        closing: dict[str, int] = {}
        n = 0
        for _, _, items in sorted(parts, key=lambda p: p[0]):
            for item in items:
                key = _item_key(item)
                first.setdefault(key, n)
                if item[3] == PERIOD_END_LABEL:
                    period_end.setdefault(key, n)
                if item[3] != PERIOD_START_LABEL:
                    closing[key] = n
                n += 1
        out[consolidated][statement] = Layout(first, period_end, closing)
    return out


def _layouts(db: Session, ids: Sequence[int], standards: Mapping[int, str | None]) -> dict[int, dict[bool, dict[str, Layout]]]:
    roles: dict[int, list[tuple[int, str, str, bool, bool, list]]] = {fid: [] for fid in ids}
    sql = text(
        "SELECT filing_id, seq, role, statement, is_consolidated, is_ifrs, items_json FROM xbrl_presentation_roles "
        "WHERE filing_id IN :ids ORDER BY filing_id, seq"
    ).bindparams(bindparam("ids", expanding=True))
    for start in range(0, len(ids), 500):
        for r in db.execute(sql, {"ids": list(ids[start:start + 500])}):
            roles[int(r[0])].append(
                (int(r[1]), str(r[2]), str(r[3]), bool(r[4]), bool(r[5]), json.loads(str(r[6])))
            )
    return {fid: statement_layouts(found, standards.get(fid)) for fid, found in roles.items()}


def split_by_layout(seq: Sequence[Fact], layouts: Mapping[str, Layout]) -> dict[str, list[Fact]]:
    """表示リンクの区分に載っている fact を表に分ける。並びは印字順（seq の順）。

    印字順と表示リンクの順が合う fact のいちばん長い並びを本体にする。本体の範囲の中で順が合わない fact は
    印字順のまま（会社の表示リンクで合計が内訳より前にある、同じ要素が見出しの位置にも出る）。範囲の外に
    ある fact（株主資本等変動計算書で先に印字された純資産、PL で先に印字された税引前利益）と、PL・CF の
    時点の fact（期末の現金）は、表示リンクの位置で本体の間に入れる。時点の fact の位置は periodEndLabel、
    無ければ periodStartLabel でない最後の出現。
    """
    out: dict[str, list[Fact]] = {}
    for statement, layout in layouts.items():
        balance = statement != "BS"
        placed: list[tuple[int, int, Fact]] = []
        for n, f in enumerate(seq):
            at = None
            if f.instant:
                at = layout.period_end.get(f.key, layout.closing.get(f.key))
            if at is None:
                at = layout.first.get(f.key)
            if at is not None:
                placed.append((at, n, f))
        flow = [t for t in placed if not (balance and t[2].instant)]
        body = _agreeing([t[0] for t in flow])
        if not body:
            out[statement] = [f for _, _, f in sorted(placed, key=lambda t: (t[0], t[1]))]
            continue
        lo, hi = flow[body[0]][1], flow[body[-1]][1]
        anchors = {flow[i][1] for i in body}
        inside = [t for t in flow if lo <= t[1] <= hi]
        kept = {t[1] for t in inside}
        for t in sorted((t for t in placed if t[1] not in kept), key=lambda t: (t[0], t[1])):
            before = [i for i, x in enumerate(inside) if x[0] < t[0] and x[1] in anchors]
            inside.insert(before[-1] + 1 if before else 0, t)
            anchors.add(t[1])
        out[statement] = [f for _, _, f in inside]
    return out


def _agreeing(positions: Sequence[int]) -> list[int]:
    if all(a < b for a, b in zip(positions, positions[1:])):
        return list(range(len(positions)))
    tails: list[int] = []
    tail_at: list[int] = []
    back: list[int] = [-1] * len(positions)
    for i, value in enumerate(positions):
        k = bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_at.append(i)
        else:
            tails[k] = value
            tail_at[k] = i
        back[i] = tail_at[k - 1] if k else -1
    out: list[int] = []
    i = tail_at[-1] if tail_at else -1
    while i >= 0:
        out.append(i)
        i = back[i]
    return out[::-1]


def _shorter_durations(facts: Sequence[Fact], starts: Mapping[int, str]) -> set[int]:
    best: dict[str, Fact] = {}
    for f in facts:
        if f.instant:
            continue
        kept = best.get(f.key)
        if kept is None or starts[f.id] < starts[kept.id]:
            best[f.key] = f
    return {f.id for f in facts if not f.instant and best[f.key] is not f}


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
        while cf_hi < len(seq) and _cf_tail(seq[cf_hi]):
            cf_hi += 1
    if bs_hi <= cf_lo:
        pl = [f for f in seq[bs_hi:cf_lo] if not f.instant]
        ahead = [f for f in seq[:bs_lo] if not f.instant and not AHEAD_SKIP_RE.search(f.local)]
        if _pl_ends(ahead) > _pl_ends(pl) and not any(f.local in BS_TOTALS for f in seq[:bs_lo]):
            pl = ahead
    else:
        pl = [f for i, f in enumerate(seq[:cf_lo]) if not f.instant and not (bs_lo <= i < bs_hi)]
    ends = [n for n, f in enumerate(pl) if PL_END_RE.search(f.local)]
    if ends:
        pl = pl[: ends[-1] + 1]
    return {
        "BS": list(seq[bs_lo:bs_hi]),
        "PL": pl,
        "CF": list(seq[cf_lo:cf_hi]),
    }


def _cf_tail(fact: Fact) -> bool:
    if CF_CASH_CHANGE_RE.search(fact.local):
        return True
    return fact.instant and CF_CASH_RE.search(fact.local) is not None


def _pl_ends(facts: Sequence[Fact]) -> int:
    return sum(1 for f in facts if PL_END_RE.search(f.local))


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
        in_rule = {f.key for f in target}
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
            if len(cands) != 1 or len({c.key for c in _near(pool, f.value, tol)}) != 1:
                continue
            if not _foreign(statement, cands[0], in_rule):
                hit[i] = cands[0]
                used.add(cands[0].id)
        twice: set[str] = set()
        for i, at in pdf_rows(rows, statement, None).copies.items():
            if (i in hit) != (at in hit):
                twice.add(_base(rows[i], statement))
                if i in hit:
                    hit[at] = hit.pop(i)
        info = pdf_rows(rows, statement, twice)
        names = info.names
        learned.twice[statement] = twice
        maps: dict[str, Mapped] = {}
        order: list[str] = []
        extras: dict[str, str | None] = {}
        prev: str | None = None
        for i, f in enumerate(rows):
            x = hit.get(i)
            if x is None or i in info.copies:
                continue
            sign = -1 if f.value * x.value < 0 else 1
            maps[names[i]] = Mapped(x.key, sign)
            order.append(x.key)
            learned.pdf_names.setdefault(x.key, f.local)
            if x.key not in in_rule:
                extras[x.key] = prev
            prev = x.key
        live = [i for i in range(len(rows)) if i not in info.copies]
        marks = [hit[i].key if i in hit else None for i in live]
        within = _section_names([info.bases[i] for i in live], marks, statement)
        learned.by_section[statement] = {k: maps[names[live[n]]] for n, k in within.items() if live[n] in hit}
        learned.maps[statement] = maps
        learned.losses[statement] = _losses(maps, xbrl.seq)
        learned.homes[statement] = _homes(target, statement)
        learned.order[statement] = order
        learned.extras[statement] = extras
    return learned


def _foreign(statement: str, fact: Fact, own: set[str]) -> bool:
    if statement == "BS" or fact.key in own:
        return False
    if statement == "PL" and CF_MARK_RE.search(fact.local):
        return True
    return fact.instant and not (statement == "CF" and CF_CASH_RE.search(fact.local))


def _profit_name(label: str) -> str | None:
    m = SIGNED_PROFIT_RE.match(unicodedata.normalize("NFKC", label).strip())
    return m.group(1) if m else None


def _loss_name(profit: str) -> str | None:
    if not profit.endswith("利益") or SUFFIX_RE.search(profit):
        return None
    return profit.removesuffix("利益") + "損失"


def _losses(maps: Mapping[str, Mapped], facts: Iterable[Fact]) -> dict[str, Mapped]:
    signed = {f.key: profit for f in facts if (profit := _profit_name(f.label))}
    found: dict[str, set[Mapped]] = defaultdict(set)
    for name, m in maps.items():
        if m.key not in signed:
            continue
        for profit in (name, signed[m.key]):
            loss = _loss_name(profit)
            if loss is not None:
                found[loss].add(m)
    return {name: next(iter(ms)) for name, ms in found.items() if len(ms) == 1 and name not in maps}


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


def _global_order(learned: Iterable[Learned], statement: str) -> dict[str, int]:
    keys: list[str] = []
    for item in sorted(learned, key=lambda x: x.period_end, reverse=True):
        keys = _merge(keys, item.order.get(statement, []))
    return {k: i for i, k in enumerate(keys)}


def _ranks(learned: Sequence[Learned], statement: str, cls: str) -> list[dict[str, int]]:
    ranks = [_global_order(learned, statement)]
    same = [x for x in learned if x.cls == cls]
    if same and len(same) < len(learned):
        ranks.append(_global_order(same, statement))
    return ranks


def strip_heads(name: str, statement: str) -> str:
    heads = sorted(PDF_HEADS.get(statement, ()), key=len, reverse=True)
    while True:
        for head in heads:
            if not name.startswith(head):
                continue
            rest = HEAD_SEP_RE.sub("", name[len(head):])
            if rest and not HEAD_KEEP_RE.search(rest):
                name = rest
                break
        else:
            return name


def _base(fact: Fact, statement: str) -> str:
    return _clean_label(strip_heads(SUFFIX_RE.sub("", fact.local), statement))


def pdf_rows(rows: Sequence[Fact], statement: str, twice: Iterable[str] | None = ()) -> PdfRows:
    bases = [_base(f, statement) for f in rows]
    allowed = None if twice is None else set(twice)
    copies: dict[int, int] = {}
    if statement == "PL":
        first: dict[tuple[str, float], int] = {}
        for i, f in enumerate(rows):
            if not f.value or bases[i] in GENERIC_LABELS or (allowed is not None and bases[i] not in allowed):
                continue
            at = first.setdefault((bases[i], f.value), i)
            if at != i:
                copies[i] = at
    seen: dict[str, int] = defaultdict(int)
    names: list[str] = []
    for i, base in enumerate(bases):
        if i in copies:
            names.append(names[copies[i]])
            continue
        seen[base] += 1
        names.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return PdfRows(names, bases, copies)


def printed_twice(learned: Iterable[Learned], statement: str) -> set[str]:
    return {name for item in learned for name in item.twice.get(statement, ())}


def _is_marker(key: str | None, statement: str) -> bool:
    if key is None:
        return False
    if statement == "CF":
        return bool(CF_SECTION_RE.match(key))
    return key in MATCH_MARKERS.get(statement, ())


def _section_names(
    bases: Sequence[str], marks: Sequence[str | None], statement: str,
) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    section: str | None = None
    for i in range(len(bases) - 1, -1, -1):
        if _is_marker(marks[i], statement):
            section = marks[i]
        if section is not None and bases[i]:
            out[i] = (section, bases[i])
    counts = defaultdict(int)
    for k in out.values():
        counts[k] += 1
    return {i: k for i, k in out.items() if counts[k] == 1}


def _homes(facts: Sequence[Fact], statement: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    section: str | None = None
    for f in reversed(facts):
        if _is_marker(f.key, statement):
            section = f.key
        out[f.key] = section if out.get(f.key, section) == section else None
    return out


def _lookup(ranked: Sequence[Learned], statement: str, name: str) -> Mapped | None:
    for item in ranked:
        m = item.maps.get(statement, {}).get(name)
        if m is not None:
            return m
    return None


def _lookup_section(ranked: Sequence[Learned], statement: str, key: tuple[str, str]) -> Mapped | None:
    for item in ranked:
        m = item.by_section.get(statement, {}).get(key)
        if m is not None:
            return m
    return None


def _lookup_loss(ranked: Sequence[Learned], statement: str, name: str) -> Mapped | None:
    for item in ranked:
        m = item.losses.get(statement, {}).get(name)
        if m is not None:
            return m
    return None


def _loss_rows(
    rows: Sequence[Fact], keys: Sequence[str], bases: Sequence[str], proposals: Sequence[Mapped | None],
    ranked: Sequence[Learned], statement: str,
) -> dict[int, Mapped]:
    taken = {m.key for m in proposals if m is not None}
    count = Counter(bases)
    out: dict[int, Mapped] = {}
    for i, name in enumerate(keys):
        if proposals[i] is not None or count[name] != 1 or rows[i].value > 0:
            continue
        m = _lookup_loss(ranked, statement, name)
        if m is not None and m.key not in taken:
            out[i] = m
    return out


def _ordered(
    names: Sequence[str], proposals: Sequence[Mapped | None], ranks: Sequence[Mapping[str, int]],
) -> tuple[list[Mapped | None], list[set[int]]]:
    counts = defaultdict(int)
    for m in proposals:
        if m is not None:
            counts[m.key] += 1
    unique = [m if m is not None and counts[m.key] == 1 else None for m in proposals]
    keeps = [
        _lis([
            (i, rank[m.key], 1 if _generic(names[i]) else 2)
            for i, m in enumerate(unique) if m is not None and m.key in rank
        ])
        for rank in ranks
    ]
    return unique, keeps


def apply_learned(
    rows: Sequence[Fact], learned: Sequence[Learned], statement: str, period_end: date, cls: str,
    names: Names | None = None,
) -> list[tuple[Fact, Mapped | None]]:
    info = pdf_rows(rows, statement, printed_twice(learned, statement))
    live = [i for i in range(len(rows)) if i not in info.copies]
    keys = [info.names[i] for i in live]
    bases = [info.bases[i] for i in live]
    live_rows = [rows[i] for i in live]
    found = _matched(live_rows, keys, bases, learned, statement, period_end, cls, names, {})
    if names:
        totals = _totals(keys, bases, found, names, _known(learned, statement), live_rows, statement)
        if totals:
            more = _matched(live_rows, keys, bases, learned, statement, period_end, cls, names, totals)
            taken = {m.key for m in found if m is not None}
            added = {n: m for n, m in enumerate(more) if found[n] is None and m is not None and m.key not in taken}
            counts = Counter(m.key for m in added.values())
            for n, m in added.items():
                if counts[m.key] == 1:
                    found[n] = m
    result: list[Mapped | None] = [None] * len(rows)
    for n, i in enumerate(live):
        result[i] = found[n]
    for i, at in info.copies.items():
        result[i] = result[at]
    return list(zip(rows, result))


def _known(learned: Sequence[Learned], statement: str) -> set[object]:
    return {k for item in learned for k in item.maps.get(statement, {})} | {
        k for item in learned for k in item.by_section.get(statement, {})
    } | {k for item in learned for k in item.losses.get(statement, {})}


def _total_stem(base: str) -> str | None:
    stem = TOTAL_TAIL_RE.sub("", base)
    if stem == base or len(stem) < 2 or stem in GENERIC_LABELS:
        return None
    return stem


def _is_total(key: str, statement: str) -> bool:
    return _is_marker(key, statement) or key in BS_TOTALS or key in BS_GROUP_END


def _totals(
    keys: Sequence[str], bases: Sequence[str], found: Sequence[Mapped | None], names: Names, known: set[object],
    live_rows: Sequence[Fact], statement: str,
) -> dict[int, Mapped]:
    taken = {m.key for m in found if m is not None}
    count = Counter(bases)
    out: dict[int, Mapped] = {}
    for n, base in enumerate(bases):
        if found[n] is not None or count[base] != 1 or keys[n] in known or base in names.keys:
            continue
        stem = _total_stem(base)
        key = names.keys.get(stem) if stem is not None else None
        if key is None or key in taken or (key in names.per_share) != (live_rows[n].unit in PER_SHARE_UNITS):
            continue
        out[n] = Mapped(key, 1, "name")
    counts = Counter(m.key for m in out.values())
    out = {n: m for n, m in out.items() if counts[m.key] == 1}
    votes = Counter(k.endswith("IFRS") for k in taken | {m.key for m in out.values()} if _is_total(k, statement))
    if len(votes) > 1:
        side = votes[True] > votes[False] if votes[True] != votes[False] else None
        out = {n: m for n, m in out.items() if m.key.endswith("IFRS") == side}
    return out


def _matched(
    live_rows: Sequence[Fact], keys: Sequence[str], bases: Sequence[str], learned: Sequence[Learned], statement: str,
    period_end: date, cls: str, names: Names | None, totals: Mapping[int, Mapped],
) -> list[Mapped | None]:
    ranked = _ranked(learned, period_end, cls)
    ranks = _ranks(learned, statement, cls)
    proposals = [_lookup(ranked, statement, name) for name in keys]
    losses = _loss_rows(live_rows, keys, bases, proposals, ranked, statement)
    for i, m in losses.items():
        proposals[i] = m
    first, keeps = _ordered(keys, proposals, ranks)
    kept = set().union(*keeps)
    marks = [first[i].key if i in kept and first[i] is not None else None for i in range(len(keys))]
    for i, m in totals.items():
        marks[i] = marks[i] or m.key
    by_section = _section_names(bases, marks, statement)
    count = Counter(bases)
    for i, sk in by_section.items():
        if count[bases[i]] > 1:
            m = _lookup_section(ranked, statement, sk)
            if m is not None:
                proposals[i] = m
    for i, m in losses.items():
        if proposals[i] is m and sum(1 for p in proposals if p is not None and p.key == m.key) > 1:
            proposals[i] = None
    for i in totals:
        proposals[i] = None
    used = Counter(m.key for m in proposals if m is not None)
    used.update(m.key for m in totals.values())
    claimed = set(used)
    proposals, keeps = _ordered(keys, proposals, ranks)
    keep = set().union(*keeps)
    by_name: dict[int, Mapped] = {}
    per_share = [f.unit in PER_SHARE_UNITS for f in live_rows]
    if names:
        losing = [f.value <= 0 for f in live_rows]
        by_name = _by_name(keys, bases, by_section, _known(learned, statement), names, claimed, per_share, losing)
        for i in totals:
            by_name.pop(i, None)
    picked: set[int] = set()
    if by_name:
        for rank, kept_here in zip(ranks, keeps):
            pos = {k: i for i, k in enumerate(_merge(sorted(rank, key=rank.__getitem__), names.order))}
            picked |= _lis([
                (i, pos[by_name[i].key] if i in by_name else pos[proposals[i].key],
                 NAME_WEIGHT if i in by_name else KEPT_WEIGHT)
                for i in range(len(keys)) if (i in by_name and by_name[i].key in pos) or i in kept_here
            ])
        for i, m in by_name.items():
            if i in picked:
                proposals[i] = m
                keep.add(i)
    dropped = {i: m for i, m in enumerate(proposals) if m is not None and i not in keep}
    dropped.update({i: m for i, m in by_name.items() if i not in picked})
    used.update(m.key for m in by_name.values())
    for i, m in _in_section(dropped, bases, by_section, ranked, statement, names, per_share, used).items():
        proposals[i] = m
        keep.add(i)
    for i, m in totals.items():
        proposals[i] = m
        keep.add(i)
    return [proposals[n] if n in keep else None for n in range(len(keys))]


def name_index(filings: Sequence[Filing], statement: str) -> Names:
    found: dict[str, set[str]] = defaultdict(set)
    within: dict[tuple[str, str], set[str]] = defaultdict(set)
    order: list[str] = []
    per_share: set[str] = set()
    losses: dict[str, set[str]] = defaultdict(set)
    for filing in sorted(filings, key=lambda f: (f.period_end, f.submitted), reverse=True):
        if filing.is_pdf:
            continue
        facts = filing.by_statement.get(statement, [])
        labels = [_clean_label(f.label) for f in facts]
        labels = ["" if OPENING_CASH_RE.match(name) else name for name in labels]
        per_share.update(f.key for f in facts if f.unit in PER_SHARE_UNITS)
        for f, name in zip(facts, labels):
            if name:
                found[name].add(f.key)
            loss = _loss_name(_profit_name(f.label) or "")
            if loss is not None:
                losses[loss].add(f.key)
        section: str | None = None
        for f, name in zip(reversed(facts), reversed(labels)):
            if _is_marker(f.key, statement):
                section = f.key
            if section is not None and name:
                within[(section, name)].add(f.key)
        order = _merge(order, [f.key for f in facts])
    return Names(
        {name: next(iter(keys)) for name, keys in found.items() if len(keys) == 1},
        order,
        {k: next(iter(keys)) for k, keys in within.items() if len(keys) == 1},
        per_share,
        {name: next(iter(keys)) for name, keys in losses.items() if len(keys) == 1},
    )


def _by_name(
    keys: Sequence[str], bases: Sequence[str], by_section: Mapping[int, tuple[str, str]], known: set[object],
    names: Names, claimed: set[str], per_share: Sequence[bool], losing: Sequence[bool],
) -> dict[int, Mapped]:
    count = Counter(bases)
    out: dict[int, Mapped] = {}
    for i, name in enumerate(keys):
        sk = by_section.get(i)
        if name in known or (sk is not None and sk in known):
            continue
        key = names.keys.get(name) if count[bases[i]] == 1 else None
        if key is None and count[bases[i]] == 1 and losing[i]:
            key = names.losses.get(name)
        if key is None and sk is not None and bases[i] not in GENERIC_LABELS:
            key = names.by_section.get(sk)
        if key is not None and key not in claimed and (key in names.per_share) == per_share[i]:
            out[i] = Mapped(key, 1, "name")
    counts = defaultdict(int)
    for m in out.values():
        counts[m.key] += 1
    return {i: m for i, m in out.items() if counts[m.key] == 1}


def _in_section(
    dropped: Mapping[int, Mapped], bases: Sequence[str], by_section: Mapping[int, tuple[str, str]],
    ranked: Sequence[Learned], statement: str, names: Names | None, per_share: Sequence[bool], used: Counter,
) -> dict[int, Mapped]:
    homes: dict[str, set[str | None]] = defaultdict(set)
    for item in ranked:
        for key, home in item.homes.get(statement, {}).items():
            homes[key].add(home)
    out: dict[int, Mapped] = {}
    for i, m in dropped.items():
        sk = by_section.get(i)
        if sk is None:
            continue
        got = _lookup_section(ranked, statement, sk)
        if got is None or homes.get(got.key) != {sk[0]}:
            key = None
            if names is not None and bases[i] not in GENERIC_LABELS:
                key = names.by_section.get(sk)
            if key is None or (key in names.per_share) != per_share[i]:
                continue
            got = m if m.key == key else Mapped(key, 1, "name")
        if used[got.key] - (got.key == m.key) == 0:
            out[i] = got
    counts = Counter(m.key for m in out.values())
    return {i: m for i, m in out.items() if counts[m.key] == 1}


def _generic(name: str) -> bool:
    return bool(SUFFIX_RE.search(name)) or name in GENERIC_LABELS


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


def _opening_cash(key: str) -> bool:
    return key.startswith("pdf:") and OPENING_CASH_RE.match(SUFFIX_RE.sub("", key[4:])) is not None


def pdf_only_matches(db: Session, code: str) -> list[PdfMatch]:
    out: list[PdfMatch] = []
    for basis, filings in load_both(db, code).items():
        learned = [learn_pair(pdf, xbrl) for pdf, xbrl in _pairs(filings)]
        xbrl_ends = {f.period_end for f in filings if not f.is_pdf and f.by_statement}
        for statement in STATEMENTS:
            names = name_index(filings, statement)
            for filing in filings:
                if not filing.is_pdf or filing.period_end in xbrl_ends or statement not in filing.by_statement:
                    continue
                facts = filing.by_statement[statement]
                for fact, mapped in apply_learned(facts, learned, statement, filing.period_end, filing.cls, names):
                    if statement == "CF" and mapped is None and _opening_cash(fact.key):
                        continue
                    out.append(PdfMatch(filing, basis, statement, fact, mapped))
    return out


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
                copies = pdf_rows(facts, statement, printed_twice(pool, statement)).copies
                for n, (fact, mapped) in enumerate(apply_learned(facts, pool, statement, period_end, cls, by_name)):
                    if n in copies:
                        continue
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

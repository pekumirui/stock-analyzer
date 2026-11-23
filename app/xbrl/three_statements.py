from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.config import XBRL_ROW_MERGE_EXCLUSIONS_PATH, XBRL_STANDARD_LABELS_PATH

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
    "StandardBook",
    "StandardLabel",
    "apply_learned",
    "financial_table",
    "gather_heads",
    "learn_pair",
    "load_filings",
    "merge_split_rows",
    "name_index",
    "pdf_only_matches",
    "pdf_rows",
    "printed_twice",
    "split_by_layout",
    "standard_book",
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
    re.compile(r"(?<=利益)\(損失\)|(?<=損失)\(利益\)"),
    re.compile(r"[─―]+$"),
)
TRAIL_BAR_RE = re.compile(r"[─―]+$")
CANON_NOISE_RE = re.compile(r"\((?:円|銭|注\d*)\)$")
CANON_PERIOD_RE = re.compile(r"(?:四半期|中間)(純?(?:利益|損失)|包括利益)")
CANON_TAX_RE = re.compile(r"^税金等調整前(当期純(?:利益|損失))$")
CANON_PRETAX_IFRS_RE = re.compile(r"^税引前当期(利益|損失)$")
PL_TWICE_FALLBACK = ("当期利益", "当期純利益", "当期損失", "当期純損失")
PER_SHARE_HEAD_RE = re.compile(
    r"^1株当たり(?:当期|四半期|中間)純?(?:利益|損失)(?:\(円\))?(?=(?:基本的|希薄化後)1株当たり)",
)
SECTION_HEADS = (
    "流動資産", "投資その他の資産", "繰延資産", "流動負債", "固定負債", "特別法上の準備金",
    "負債及び資本", "負債純資産", "株主資本", "その他の包括利益累計額", "評価・換算差額等",
    "販売費及び一般管理費", "営業外収益", "営業外費用", "特別利益", "特別損失", "その他の包括利益",
)
GENERIC_LABELS = frozenset({"その他"})
NEVER_MERGED_LABELS = frozenset({"その他", "貸倒引当金"})
NEVER_MERGED_PREFIXES = ("減価償却累計額", "減損損失累計額")
SIGNED_PROFIT_RE = re.compile(r"^(.+利益)(?:又は.*損失(?:\(△\))?|\(△?は?損失\))$")
SIGNED_CASH_RE = re.compile(r"^(.+の)(?:純)?増減額\(△は減少\)$")
MIN_LOOSE_MATCH = 100
PDF_FULL_RATIO = 0.7
NAME_WEIGHT = 1
KEPT_WEIGHT = 1_000_000
STANDARD_BY = "standard"
STANDARD_OUTSIDE = "-"
STANDARD_OTHER = "その他"
STANDARD_ABOVE_SUBTOTAL = ":小計まで"
STANDARD_SUBTOTAL_NAME = "小計"
STANDARD_SUBTOTAL_RE = re.compile(r"^(?:ext:)?Subtotal")
STANDARD_OPERATING_RE = re.compile(r"Operating")
NET_TAIL = "(純額)"
STANDARD_BEFORE_ACCUMULATED = ":累計額の前"
ACCUMULATED_NAMES = frozenset({"減価償却累計額", "減損損失累計額", "減価償却累計額及び減損損失累計額"})
STANDARD_EXTRA_HEADS = {"BS": ("その他利益剰余金", "資本剰余金", "利益剰余金")}
CLOSING_CASH_RE = re.compile(
    r"^[IVXⅠ-Ⅻ]*(?:現金(?:及び|および)現金同等物の?当?(?:四半期|中間)?期?末残高|期末現金(?:及び|および)現金同等物残高)$"
)
CLOSING_CASH_NAMES = ("現金及び現金同等物の残高", "現金及び現金同等物")
STANDARD_ALIASES = {"BS": {"その他の包括利益累計額": "評価・換算差額等"}}

BS_GROUPS = {
    "Assets": "資産", "Liabilities": "負債", "NetAssets": "純資産",
    "AssetsIFRS": "資産", "LiabilitiesIFRS": "負債", "EquityIFRS": "資本",
}
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
    "ReservesUnderTheSpecialLaws1": "特別法上の準備金",
    "ValuationAndTranslationAdjustments": "その他の包括利益累計額",
    "CurrentAssetsIFRS": "流動資産", "NonCurrentAssetsIFRS": "非流動資産", "TotalCurrentLiabilitiesIFRS": "流動負債",
    "NonCurrentLabilitiesIFRS": "非流動負債", "EquityAttributableToOwnersOfParentIFRS": "親会社の所有者に帰属する持分",
    "NetCashProvidedByUsedInOperatingActivities": "営業活動によるキャッシュ・フロー",
    "NetCashProvidedByUsedInInvestmentActivities": "投資活動によるキャッシュ・フロー",
    "NetCashProvidedByUsedInFinancingActivities": "財務活動によるキャッシュ・フロー",
    "NetCashProvidedByUsedInInvestingActivitiesIFRS": "投資活動によるキャッシュ・フロー",
}
CF_SECTION_RE = re.compile(r"^NetCashProvidedByUsedIn(Operating|Investment|Investing|Financing)Activities(IFRS)?$")
HEAD_GROUPS = {
    "AssetsAbstract": "Assets", "LiabilitiesAbstract": "Liabilities", "NetAssetsAbstract": "NetAssets",
    "AssetsIFRSAbstract": "AssetsIFRS", "LiabilitiesIFRSAbstract": "LiabilitiesIFRS", "EquityIFRSAbstract": "EquityIFRS",
}
HEAD_SECTIONS = {
    "BS": {
        "CurrentAssetsAbstract": "CurrentAssets", "NoncurrentAssetsAbstract": "NoncurrentAssets",
        "PropertyPlantAndEquipmentAbstract": "PropertyPlantAndEquipment", "IntangibleAssetsAbstract": "IntangibleAssets",
        "InvestmentsAndOtherAssetsAbstract": "InvestmentsAndOtherAssets", "DeferredAssetsAbstract": "DeferredAssets",
        "CurrentLiabilitiesAbstract": "CurrentLiabilities", "NoncurrentLiabilitiesAbstract": "NoncurrentLiabilities",
        "ReservesUnderTheSpecialLawsAbstract1": "ReservesUnderTheSpecialLaws1",
        "ShareholdersEquityAbstract": "ShareholdersEquity",
        "ValuationAndTranslationAdjustmentsAbstract": "ValuationAndTranslationAdjustments",
        "CurrentAssetsIFRSAbstract": "CurrentAssetsIFRS", "NonCurrentAssetsIFRSAbstract": "NonCurrentAssetsIFRS",
        "CurrentLiabilitiesIFRSAbstract": "TotalCurrentLiabilitiesIFRS",
        "NonCurrentLiabilitiesIFRSAbstract": "NonCurrentLabilitiesIFRS",
        "EquityAttributableToOwnersOfParentIFRSAbstract": "EquityAttributableToOwnersOfParentIFRS",
    },
    "PL": {
        "CostOfSalesAbstract": "CostOfSales",
        "SellingGeneralAndAdministrativeExpensesAbstract": "SellingGeneralAndAdministrativeExpenses",
        "NonOperatingIncomeAbstract": "NonOperatingIncome", "NonOperatingExpensesAbstract": "NonOperatingExpenses",
        "ExtraordinaryIncomeAbstract": "ExtraordinaryIncome", "ExtraordinaryLossAbstract": "ExtraordinaryLoss",
    },
    "CF": {
        "NetCashProvidedByUsedInOperatingActivitiesAbstract": "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInInvestmentActivitiesAbstract": "NetCashProvidedByUsedInInvestmentActivities",
        "NetCashProvidedByUsedInFinancingActivitiesAbstract": "NetCashProvidedByUsedInFinancingActivities",
        "CashFlowsFromOperatingActivitiesIFRSAbstract": "NetCashProvidedByUsedInOperatingActivitiesIFRS",
        "CashFlowsFromInvestingActivitiesIFRSAbstract": "NetCashProvidedByUsedInInvestingActivitiesIFRS",
        "CashFlowsFromFinancingActivitiesIFRSAbstract": "NetCashProvidedByUsedInFinancingActivitiesIFRS",
    },
}
SECTION_PARENTS = {
    "PropertyPlantAndEquipment": "NoncurrentAssets", "IntangibleAssets": "NoncurrentAssets",
    "InvestmentsAndOtherAssets": "NoncurrentAssets",
}
LONE_SECTIONS = {
    **{key: key for key in (
        *SECTION_PARENTS, "DeferredAssets", "CurrentAssets", "NoncurrentAssets", "CurrentLiabilities",
        "NoncurrentLiabilities", "CurrentAssetsIFRS", "NonCurrentAssetsIFRS", "TotalCurrentLiabilitiesIFRS",
        "NonCurrentLabilitiesIFRS",
    )},
    "InvestmentsAndOtherAssetsGross": "InvestmentsAndOtherAssets",
}
OPEN_AFTER_TOTAL = frozenset({
    "CurrentAssets", "NoncurrentAssets", "CurrentLiabilities", "NoncurrentLiabilities", "CurrentAssetsIFRS", "NonCurrentAssetsIFRS",
    "TotalCurrentLiabilitiesIFRS", "NonCurrentLabilitiesIFRS",
    "NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInInvestmentActivities",
    "NetCashProvidedByUsedInOperatingActivitiesIFRS", "NetCashProvidedByUsedInInvestingActivitiesIFRS",
})
OUTSIDE_SECTIONS = {
    "BS": re.compile(r"^(SubscriptionRightsToShares|NonControllingInterests)(IFRS)?$"),
    "CF": re.compile(r"^(EffectOfExchangeRateChange|NetIncreaseDecreaseInCashAndCashEquivalents|CashAndCashEquivalents)|CCE$"),
}
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
        "固定負債", "非流動負債", "特別法上の準備金", "負債及び資本", "負債純資産", "株主資本",
        "その他の包括利益累計額", "評価・換算差額等",
    ),
    "PL": ("販売費及び一般管理費", "営業外収益", "営業外費用", "特別利益", "特別損失", "その他の包括利益"),
    "CF": ("営業活動によるキャッシュ・フロー", "投資活動によるキャッシュ・フロー", "財務活動によるキャッシュ・フロー"),
}
HEAD_KEEP_RE = re.compile(
    r"^(合計|小計|累計額|合$|計(#\d+|[─―ー]*)$|又は|#|[（(、─―ー※・にへをと]|の(?!れん)|及び|並びに|等$)",
)
HEAD_SEP_RE = re.compile(r"^[:：]")
MARKER_ORDER = {
    "BS": (
        "CurrentAssets", "PropertyPlantAndEquipment", "IntangibleAssets",
        "InvestmentsAndOtherAssets", "NoncurrentAssets", "DeferredAssets", "Assets",
        "CurrentLiabilities", "NoncurrentLiabilities", "Liabilities",
        "ShareholdersEquity", "ValuationAndTranslationAdjustments", "NetAssets", "LiabilitiesAndNetAssets",
        "CurrentAssetsIFRS", "NonCurrentAssetsIFRS", "AssetsIFRS",
        "CurrentLiabilitiesIFRS", "NonCurrentLiabilitiesIFRS", "LiabilitiesIFRS",
        "EquityIFRS", "LiabilitiesAndEquityIFRS",
    ),
    "PL": (
        "GrossProfit", "OperatingIncome", "NonOperatingIncome", "NonOperatingExpenses",
        "OrdinaryIncome", "ExtraordinaryIncome", "ExtraordinaryLoss",
    ),
}


@dataclass(frozen=True)
class Fact:
    id: int
    key: str
    local: str
    label: str
    instant: bool
    unit: str | None
    value: float


Head = tuple[str, str]


@dataclass(frozen=True)
class Part:
    group: str | None
    section: str | None
    ifrs: bool
    heads: tuple[Head, ...] | None = None
    indent: int = 0
    under: str | None = None

    @property
    def coarse(self) -> tuple[str | None, str | None, bool]:
        return self.group, self.section, self.ifrs


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
    parts: dict[str, dict[str, Part]] = field(default_factory=dict)

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
    standard: str | None = None


@dataclass
class Names:
    keys: dict[str, str] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    by_section: dict[tuple[str, str], str] = field(default_factory=dict)
    per_share: set[str] = field(default_factory=set)
    losses: dict[str, str] = field(default_factory=dict)
    canon_keys: dict[str, str] = field(default_factory=dict)
    canon_by_section: dict[tuple[str, str], str] = field(default_factory=dict)
    others: dict[str, str] = field(default_factory=dict)
    accumulated: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass(frozen=True)
class StandardLabel:
    any: str | None
    home: str | None
    within: Mapping[str, str]


@dataclass(frozen=True)
class StandardBook:
    labels: Mapping[tuple[str, str, str], StandardLabel] = field(default_factory=dict)
    per_share: frozenset[str] = frozenset()
    others: Mapping[tuple[str, str, str], str] = field(default_factory=dict)
    accumulated: Mapping[tuple[str, str, str], str] = field(default_factory=dict)


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
    parts: dict[str, Part] = field(default_factory=dict)

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
    excluded = _merge_exclusions().get((code, statement), set())
    columns, labels, units = _basis_columns(both[consolidated], consolidated, statement, excluded)
    if stitched:
        other, other_labels, other_units = _basis_columns(both[False], False, statement, excluded)
        columns = _stitch(columns, other)
        labels = {**other_labels, **labels}
        units = {**other_units, **units}
    shown = {id(c) for c in columns[-periods:]}
    tables: list[dict[str, Any]] = []
    for run in _standard_runs(columns):
        visible = [c for c in run if id(c) in shown]
        if visible:
            merged = merge_split_rows(run, statement, labels, units, excluded)
            tables.append(_table(run, visible, merged, statement, labels, units))
    empty = None
    if not tables:
        tables.append(_table([], [], {}, statement, labels, units, consolidated))
        empty = {
            "reason": "no_material",
            "message": "有報・短信 XBRL・短信 PDF の本表が無い。米国基準は PDF を読んでいない",
            "other_basis_available": _has_material(both[not consolidated]),
        }
    return {
        "statement": statement,
        "cumulative": statement != "BS",
        "unit": "JPY",
        **tables[-1],
        "earlier": tables[-2::-1],
        "empty": empty,
    }


def _standard_runs(columns: Sequence[Column]) -> list[list[Column]]:
    declared = [next((part.ifrs for part in c.parts.values()), None) for c in columns]
    kinds = [
        d if d is not None or c.standard is None else c.standard == "IFRS" for c, d in zip(columns, declared)
    ]
    known = [i for i, kind in enumerate(kinds) if kind is not None]
    for n, i in enumerate(known[1:-1], 1):
        around = kinds[known[n - 1]]
        if declared[i] is None and around == kinds[known[n + 1]]:
            kinds[i] = around
    for step in (1, -1):
        last: bool | None = None
        for i in range(len(kinds))[::step]:
            if kinds[i] is None:
                kinds[i] = last
            else:
                last = kinds[i]
    runs: list[tuple[bool | None, list[Column]]] = []
    for column, kind in zip(columns, kinds):
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(column)
        else:
            runs.append((kind, [column]))
    return [run for _, run in runs]


def _table(
    run: Sequence[Column], columns: Sequence[Column], merged: Mapping[str, list[str]], statement: str,
    labels: Mapping[str, str], units: Mapping[str, str | None], consolidated: bool = True,
) -> dict[str, Any]:
    bases = {c.consolidated for c in columns}
    mixed = len(bases) > 1
    if bases and not mixed:
        consolidated = bases.pop()
    keys = _row_keys(columns, statement)
    parts = _shown(keys, row_parts(run, keys), statement, labels)
    order = gather_heads(keys, [tuple((h["key"], h["name"]) for h in heads) for heads, _ in parts], labels)
    rows = [
        {
            "key": keys[i],
            "label": labels.get(keys[i], keys[i]),
            "unit": _unit_kind(units, keys[i]),
            "unmatched_pdf": keys[i].startswith("pdf:"),
            "heads": parts[i][0],
            "indent": parts[i][1],
            "merged_keys": merged.get(keys[i], []),
            "cells": [
                None if keys[i] not in column.values
                else {"value": column.values[keys[i]], "source": column.sources[keys[i]]}
                for column in columns
            ],
        }
        for i in order
    ]
    return {
        "consolidated": None if mixed else consolidated,
        "basis": "連結・個別" if mixed else "連結" if consolidated else "個別",
        "accounting_standards": sorted({c.standard for c in columns if c.standard}),
        "periods": [_period(c) for c in columns],
        "rows": rows,
    }


def _unit_kind(units: Mapping[str, str | None], key: str) -> str:
    return "per_share" if units.get(key) in PER_SHARE_UNITS else "money"


def _row_keys(columns: Sequence[Column], statement: str) -> list[str]:
    keys: list[str] = []
    for column in reversed(columns):
        keys = _merge(keys, column.order)
    if statement == "CF":
        keys = [k for k in keys if not _opening_cash(k)]
    return keys


@lru_cache(maxsize=1)
def _merge_exclusions() -> dict[tuple[str, str], set[frozenset[str]]]:
    raw = json.loads(XBRL_ROW_MERGE_EXCLUSIONS_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], set[frozenset[str]]] = defaultdict(set)
    for item in raw.get("pairs") or []:
        out[(str(item["code"]), str(item["statement"]))].add(frozenset(str(k) for k in item["keys"]))
    return dict(out)


def standard_book(raw: Mapping[str, Any]) -> StandardBook:
    labels: dict[tuple[str, str, str], StandardLabel] = {}
    for standard, statements in (raw.get("labels") or {}).items():
        for statement, names in statements.items():
            for name, item in names.items():
                labels[(str(standard), str(statement), str(name))] = StandardLabel(
                    item.get("any"), item.get("home"), dict(item.get("within") or {}),
                )
    others = {
        (str(standard), str(statement), str(home)): str(key)
        for standard, statements in (raw.get("others") or {}).items()
        for statement, homes in statements.items()
        for home, key in homes.items()
    }
    accumulated = {
        (str(standard), str(name), str(prev)): str(key)
        for standard, names in (raw.get("accumulated") or {}).items()
        for name, prevs in names.items()
        for prev, key in prevs.items()
    }
    return StandardBook(labels, frozenset(str(k) for k in raw.get("per_share") or ()), others, accumulated)


@lru_cache(maxsize=1)
def _standard_book() -> StandardBook:
    if not XBRL_STANDARD_LABELS_PATH.exists():
        return StandardBook()
    return standard_book(json.loads(XBRL_STANDARD_LABELS_PATH.read_text(encoding="utf-8")))


def _never_merged(label: str) -> bool:
    name = label.removesuffix("(純額)")
    return name in NEVER_MERGED_LABELS or name.startswith(NEVER_MERGED_PREFIXES)


def merge_split_rows(
    columns: Sequence[Column], statement: str, labels: Mapping[str, str], units: Mapping[str, str | None],
    excluded: Iterable[frozenset[str]] = (),
) -> dict[str, list[str]]:
    """同じ区分・同じ行名・同じ単位で値の期が 1 つも重ならない XBRL の行を 1 行にまとめる（columns を書き換える）。

    残すのはいちばん新しい期に値がある行。返り値は 残した行 → まとめた行（新しい順）。
    """
    keys = _row_keys(columns, statement)
    seen = {k: frozenset(i for i, c in enumerate(columns) if k in c.values) for k in keys}
    parts = dict(zip(keys, row_parts(columns, keys)))
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key in keys:
        label = labels.get(key, key)
        if key.startswith("pdf:") or not seen[key] or _never_merged(label):
            continue
        groups[(label, _unit_kind(units, key))].append(key)
    skip = set(excluded)
    out: dict[str, list[str]] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        apart = {
            (a, b) for a in members for b in members
            if a != b and not seen[a] & seen[b] and frozenset((a, b)) not in skip and _same_part(parts[a], parts[b])
        }
        for part in _components(members, apart):
            if len(part) < 2 or any((a, b) not in apart for a in part for b in part if a != b):
                continue
            ranked = sorted(part, key=lambda k: max(seen[k]), reverse=True)
            keep, rest = ranked[0], ranked[1:]
            for column in columns:
                for key in rest:
                    if key in column.values:
                        column.values[keep] = column.values.pop(key)
                        column.sources[keep] = column.sources.pop(key)
                        if key in column.parts:
                            column.parts[keep] = column.parts.pop(key)
                        column.order = [keep if k == key else k for k in column.order]
            out[keep] = rest
    return out


def _components(members: Sequence[str], edges: set[tuple[str, str]]) -> list[list[str]]:
    out: list[list[str]] = []
    done: set[str] = set()
    for start in members:
        if start in done:
            continue
        part, todo = [], [start]
        done.add(start)
        while todo:
            key = todo.pop()
            part.append(key)
            for other in members:
                if other not in done and (key, other) in edges:
                    done.add(other)
                    todo.append(other)
        out.append(part)
    return out


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
    filings: Sequence[Filing], consolidated: bool, statement: str, excluded: Iterable[frozenset[str]] = (),
) -> tuple[list[Column], dict[str, str], dict[str, str | None]]:
    learned = [learn_pair(pdf, xbrl) for pdf, xbrl in _pairs(filings)]
    columns = build_columns(filings, learned, statement, excluded)
    labels = _labels(filings, learned)
    for column in columns:
        column.consolidated = consolidated
        for key in column.values:
            if key.startswith("pdf:") and key not in labels:
                labels[key] = _pdf_label(key[4:])
    return columns, labels, _units(filings)


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
                filing.parts = {s: layout.parts for s, layout in layouts[fid].get(basis, {}).items()}
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
    parts: dict[str, Part] = field(default_factory=dict)


def _item_key(item: Sequence[Any]) -> str:
    return f"ext:{item[1]}" if item[2] else str(item[1])


def statement_layouts(
    roles: Sequence[tuple[int, str, str, bool, bool, list]], standard: str | None,
) -> dict[bool, dict[str, Layout]]:
    """表示リンクの区分（seq, 区分名, statement, 連結か, IFRS か, 要素）→ 連結/個別 × 表の並び。

    同じ表・同じ連結区分に IFRS と日本基準の区分が両方あれば、書類の会計基準に合う方。
    区分は seq の順につなぐ。包括利益計算書だけの区分は損益計算書の区分の後ろ。
    parts は要素 → 見出しの要素から決めた区分（_role_parts）。
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
        declared: dict[str, Part] = {}
        n = 0
        for _, ifrs, items in sorted(parts, key=lambda p: p[0]):
            for key, part in _role_parts(items, statement, ifrs).items():
                declared.setdefault(key, part)
            for item in items:
                key = _item_key(item)
                first.setdefault(key, n)
                if item[3] == PERIOD_END_LABEL:
                    period_end.setdefault(key, n)
                if item[3] != PERIOD_START_LABEL:
                    closing[key] = n
                n += 1
        out[consolidated][statement] = Layout(first, period_end, closing, declared)
    return out


def _depth(item: Sequence[Any]) -> int | None:
    return item[4] if len(item) > 4 else None


def _role_parts(items: Sequence[Sequence[Any]], statement: str, ifrs: bool) -> dict[str, Part]:
    """表示リンクの 1 区分の並び → 要素の区分。

    大区分・区分（group / section。行をまとめる判定と、深さの無い書類の見出し）は、深さの有無にかかわらず並びから（_flat_parts）。
    要素すべてに深さがあれば、宣言どおりの見出しの道筋と字下げ（_tree_heads）を足す。
    """
    flat = _flat_parts([item[:4] for item in items], statement, ifrs)
    if not items or any(_depth(item) is None for item in items):
        return flat
    heads = _tree_heads(items, statement)
    return {key: replace(part, heads=heads[key][0], indent=heads[key][1], under=heads[key][2]) if key in heads else part
            for key, part in flat.items()}


def _depth(item: Sequence[Any]) -> int | None:
    return item[4] if len(item) > 4 else None


def _head_name(item: Sequence[Any]) -> str:
    label = item[5] if len(item) > 5 else None
    return str(label or item[1])


def _tree_heads(
    items: Sequence[Sequence[Any]], statement: str,
) -> dict[str, tuple[tuple[Head, ...], int, str | None]]:
    """深さのある並び → 値の要素ごとの、見出しの道筋（外側から。(要素, 表示名)）と字下げ（値のある親の数）。

    見出しは祖先の見出しの要素（名前に Abstract を含む）を全部、宣言どおり使う。値のある要素の子は親の行の下に字下げする。
    次の見出しは出さない（中の要素は親の見出しの下）: 親の見出しの合計の要素より後ろに来る、子のある見出し（TDnet の書類で、会社が足した
    科目がタクソノミの雛形の見出しごと合計の後ろに並ぶ。印字にこの見出しは無い）。子の無い見出しは、同じ段で後ろに続く要素を、
    その見出しの合計の要素まで（次の見出しか、親が閉じるまで）の見出しにする。OUTSIDE_SECTIONS の要素は、表にある区分の見出し
    （HEAD_SECTIONS）の中にあれば、その見出しの外に出す。小計の要素（名前が Subtotal で始まる）の子は字下げしない（TDnet の書類で、
    小計の前に印字する科目が小計の子として後ろに並ぶ）。
    """
    sections = HEAD_SECTIONS[statement]
    outside = OUTSIDE_SECTIONS.get(statement)
    out: dict[str, tuple[tuple[Head, ...], int, str | None]] = {}
    path: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        depth = int(item[4])
        name = str(item[1])
        head = "Abstract" in name
        kids = i + 1 < len(items) and int(items[i + 1][4]) > depth
        while path and (path[-1]["depth"] >= depth or (head and path[-1]["depth"] == depth - 0.5)):
            path.pop()
        up = path[-1] if path else None
        if head:
            hidden = kids and up is not None and up["kind"] == "head" and up["closed"]
            path.append({
                "depth": depth if kids else depth - 0.5, "kind": "hidden" if hidden else "head",
                "head": (_item_key(item), _head_name(item)), "total": name.replace("Abstract", "", 1),
                "local": name, "closed": False,
            })
            continue
        key = _item_key(item)
        shown = [e for e in path if e["kind"] == "head"]
        last = max((n for n, e in enumerate(path) if e["kind"] == "head"), default=-1)
        parents = [e["key"] for e in path[last + 1:] if e["kind"] == "value"]
        indent = len(parents)
        if outside is not None and outside.search(key):
            while shown and shown[-1]["local"] in sections:
                shown.pop()
            indent = 0
        if key not in out:
            out[key] = (tuple(e["head"] for e in shown), indent, parents[0] if indent else None)
        for entry in reversed(path):
            if entry["kind"] in ("head", "hidden"):
                if not item[2] and name == entry["total"]:
                    entry["closed"] = True
                break
        if path and path[-1]["depth"] == depth - 0.5 and not item[2] and name == path[-1]["total"]:
            path.pop()
        path.append({"depth": depth, "kind": "value" if not name.startswith("Subtotal") else "subtotal", "key": key})
    return out


def _flat_parts(items: Sequence[Sequence[Any]], statement: str, ifrs: bool) -> dict[str, Part]:
    """深さの無い並び → 要素の区分。見出しの要素（…Abstract）のうち HEAD_GROUPS / HEAD_SECTIONS にあるものだけを見る。

    区分は親でない次の見出しか、親の区分・大区分の合計で閉じる。区分の合計でも閉じるが、OPEN_AFTER_TOTAL の区分は
    合計の後ろに科目が続く書類があるので、後ろにまだ見出しがある間は閉じない。知らない見出しは飛ばす。LONE_SECTIONS の合計が見出し無しで出たら、
    その 1 行がその区分。大区分の見出しが無いまま大区分の合計が出たら、前の大区分の後ろからその合計までをその大区分にする。
    """
    heads = HEAD_SECTIONS[statement]
    out: dict[str, Part] = {}
    group: str | None = None
    stack: list[str] = []
    loose: list[str] = []
    last_head = max(
        (i for i, item in enumerate(items) if not item[2] and (item[1] in heads or item[1] in HEAD_GROUPS)), default=-1,
    )
    for i, item in enumerate(items):
        name = str(item[1])
        if not item[2]:
            if statement == "BS" and name in HEAD_GROUPS:
                group, stack, loose = HEAD_GROUPS[name], [], []
                continue
            section = heads.get(name)
            if section is not None:
                parent = SECTION_PARENTS.get(section)
                while stack and stack[-1] != parent:
                    stack.pop()
                stack.append(section)
                group = BS_SECTIONS.get(section, group)
                continue
        if "Abstract" in name:
            continue
        key = _item_key(item)
        if statement == "BS" and key in BS_GROUPS and group in (None, key):
            for held in loose:
                out[held] = Part(key, out[held].section, ifrs)
            part = Part(key, None, ifrs)
            group, stack, loose = None, [], []
        elif key in stack:
            part = Part(group, key, ifrs)
            del stack[stack.index(key) + (key in OPEN_AFTER_TOTAL and i < last_head):]
        elif key in LONE_SECTIONS and SECTION_PARENTS.get(LONE_SECTIONS[key]) in (None, *stack):
            part = Part(group, LONE_SECTIONS[key], ifrs)
            if LONE_SECTIONS[key] not in SECTION_PARENTS:
                stack = []
        else:
            part = Part(group, stack[-1] if stack else None, ifrs)
        if key not in out:
            out[key] = part
            if part.group is None:
                loose.append(key)
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


def _lcs(
    a: Sequence[float], b: Sequence[float], tol: float, prefer: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    fav = prefer or set()
    dp: list[list[tuple[int, int]]] = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt = dp[i], dp[i + 1]
        for j in range(m - 1, -1, -1):
            best = max(nxt[j], row[j + 1])
            if _same(a[i], b[j], tol):
                cand = (nxt[j + 1][0] + 1, nxt[j + 1][1] + ((i, j) in fav))
                if cand > best:
                    best = cand
            row[j] = best
    out: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if _same(a[i], b[j], tol) and dp[i][j] == (dp[i + 1][j + 1][0] + 1, dp[i + 1][j + 1][1] + ((i, j) in fav)):
            out.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def learn_pair(pdf: Filing, xbrl: Filing) -> Learned:
    learned = Learned(period_end=pdf.period_end, cls=pdf.cls, standard=xbrl.standard or pdf.standard)
    pdf_money = [f.value for facts in pdf.by_statement.values() for f in facts if f.unit in MONEY_UNITS]
    xbrl_money = [f.value for f in xbrl.seq if f.unit in MONEY_UNITS]
    g_pdf, g_xbrl = _granularity(pdf_money), _granularity(xbrl_money)
    tol = max(g_pdf, g_xbrl) if g_pdf != g_xbrl else 0.0
    loose_floor = MIN_LOOSE_MATCH * max(g_pdf, g_xbrl)
    for statement, rows in pdf.by_statement.items():
        target = xbrl.by_statement.get(statement, [])
        in_rule = {f.key for f in target}
        row_bases = [_base(f, statement) for f in rows]
        row_stems = [_total_stem(b) for b in row_bases]
        tgt_labels = [_clean_label(f.label) for f in target]
        prefer = {
            (i, j)
            for i, (b, stem) in enumerate(zip(row_bases, row_stems))
            for j, tl in enumerate(tgt_labels)
            if tl and (b == tl or stem == tl or _canon_name(b) == _canon_name(tl))
        }
        pairs = _lcs([f.value for f in rows], [f.value for f in target], tol, prefer)
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
        learned.losses[statement] = _losses(maps, xbrl.seq, statement)
        learned.homes[statement] = _homes([f.key for f in target], statement)
        learned.order[statement] = order
        learned.extras[statement] = extras
    return learned


def _foreign(statement: str, fact: Fact, own: set[str]) -> bool:
    if fact.key in own:
        return False
    if statement == "BS":
        return not fact.instant
    if statement == "PL" and CF_MARK_RE.search(fact.local):
        return True
    return fact.instant and not (statement == "CF" and CF_CASH_RE.search(fact.local))


def _profit_name(label: str) -> str | None:
    m = SIGNED_PROFIT_RE.match(unicodedata.normalize("NFKC", label).strip())
    return m.group(1) if m else None


def _cash_decrease_name(label: str) -> str | None:
    m = SIGNED_CASH_RE.match(unicodedata.normalize("NFKC", label).strip())
    return f"{m.group(1)}減少額" if m else None


def _loss_name(profit: str) -> str | None:
    if not profit.endswith("利益") or SUFFIX_RE.search(profit):
        return None
    return profit.removesuffix("利益") + "損失"


def _losses(maps: Mapping[str, Mapped], facts: Iterable[Fact], statement: str = "PL") -> dict[str, Mapped]:
    facts = list(facts)
    signed = {f.key: profit for f in facts if (profit := _profit_name(f.label))}
    found: dict[str, set[Mapped]] = defaultdict(set)
    if statement == "CF":
        dec = {f.key: name for f in facts if (name := _cash_decrease_name(f.label))}
        for m in maps.values():
            if m.key in dec:
                found[dec[m.key]].add(m)
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


def _pair_pdf(pdfs: Sequence[Filing]) -> Filing:
    newest = _newest(pdfs)
    if len(pdfs) == 1:
        return newest
    same_standard = [f for f in pdfs if f.standard == newest.standard]
    by_statement: dict[str, list[Fact]] = {}
    for st in STATEMENTS:
        if not any(st in f.by_statement for f in same_standard):
            continue
        most = max(len(f.by_statement.get(st, ())) for f in same_standard)
        full = len(newest.by_statement.get(st, ())) >= most * PDF_FULL_RATIO
        chosen = newest if full else _pdf_base(same_standard, st)
        by_statement[st] = chosen.by_statement[st]
    return replace(newest, by_statement=by_statement)


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
        out.append((_pair_pdf(pdfs), xbrl))
    return out


def _newest(filings: Sequence[Filing]) -> Filing:
    return max(filings, key=lambda f: (f.submitted, f.id))


def _pdf_base(pdfs: Sequence[Filing], statement: str) -> Filing:
    most = max(len(f.by_statement.get(statement, ())) for f in pdfs)
    return _newest([f for f in pdfs if len(f.by_statement.get(statement, ())) >= most * PDF_FULL_RATIO])


def _revisions(pdfs: Sequence[Filing], base: Filing) -> list[Filing]:
    later = [f for f in pdfs if (f.submitted, f.id) > (base.submitted, base.id)]
    return sorted(later, key=lambda f: (f.submitted, f.id), reverse=True)


def _revised(
    revisions: Sequence[Filing], pool: Sequence[Learned], statement: str, period_end: date, names: Names | None,
) -> tuple[dict[str, float], list[str]]:
    out: dict[str, float] = {}
    mapped_keys: list[str] = []
    for filing in revisions:
        facts = filing.by_statement.get(statement, [])
        copies = pdf_rows(facts, statement, printed_twice(pool, statement, names)).copies
        for n, (fact, mapped) in enumerate(apply_learned(facts, pool, statement, period_end, filing.cls, names)):
            if n in copies:
                continue
            if mapped is not None:
                if mapped.key not in out:
                    mapped_keys.append(mapped.key)
                out.setdefault(mapped.key, fact.value * mapped.sign)
            elif not _generic(fact.local):
                out.setdefault(fact.key, fact.value)
    return out, mapped_keys


def _ranked(learned: Sequence[Learned], period_end: date, cls: str) -> list[Learned]:
    return sorted(
        learned,
        key=lambda item: (item.cls != cls, abs((item.period_end - period_end).days), -item.period_end.toordinal()),
    )


def _order_map(items: Iterable[Learned], statement: str) -> dict[str, int]:
    keys: list[str] = []
    for item in items:
        keys = _merge_statement(keys, item.order.get(statement, []), statement)
    return {k: i for i, k in enumerate(keys)}


def _global_order(learned: Iterable[Learned], statement: str) -> dict[str, int]:
    return _order_map(sorted(learned, key=lambda x: x.period_end, reverse=True), statement)


def _ranks(
    learned: Sequence[Learned], statement: str, cls: str, period_end: date | None = None,
) -> list[dict[str, int]]:
    ranks = [_global_order(learned, statement)]
    same = [x for x in learned if x.cls == cls]
    if same and len(same) < len(learned):
        ranks.append(_global_order(same, statement))
    if period_end is not None and len(learned) > 1:
        near = _order_map(_ranked(learned, period_end, cls), statement)
        if near not in ranks:
            ranks.append(near)
    return ranks


def strip_heads(name: str, statement: str) -> str:
    if statement == "PL":
        norm = unicodedata.normalize("NFKC", name)
        stripped = PER_SHARE_HEAD_RE.sub("", norm)
        if stripped != norm:
            name = stripped
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


def printed_twice(learned: Iterable[Learned], statement: str, names: Names | None = None) -> set[str]:
    items = list(learned)
    out = {name for item in items for name in item.twice.get(statement, ())}
    if statement == "PL":
        for item in items:
            losses = item.losses.get("PL", {})
            for name in item.twice.get("PL", ()):
                loss = _loss_name(name)
                if loss is not None and loss in losses:
                    out.add(loss)
        if not items and names is not None:
            for name in PL_TWICE_FALLBACK:
                cname = _canon_name(name)
                if name in names.keys or cname in names.canon_keys or name in names.losses or cname in names.losses:
                    out.add(name)
    return out


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


def _homes(keys: Sequence[str], statement: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    section: str | None = None
    for key in reversed(keys):
        if _is_marker(key, statement):
            section = key
        out[key] = section if out.get(key, section) == section else None
    return out


def _lookup_all(ranked: Sequence[Learned], statement: str, name: str) -> list[Mapped]:
    seen: set[str] = set()
    out: list[Mapped] = []
    for item in ranked:
        m = item.maps.get(statement, {}).get(name)
        if m is not None and m.key not in seen:
            seen.add(m.key)
            out.append(m)
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


def _resolve_contention(
    proposals: Sequence[Mapped | None],
    bases: Sequence[str],
    cands: Sequence[Sequence[Mapped]],
    extra_taken: set[str],
) -> list[Mapped | None]:
    resolved = list(proposals)
    counts = Counter(m.key for m in resolved if m is not None)
    taken = {k for k, c in counts.items() if c == 1} | extra_taken
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(resolved):
        if m is not None and counts[m.key] > 1:
            by_key[m.key].append(i)
    for key, group in by_key.items():
        alts: dict[int, Mapped | None] = {}
        for i in group:
            own = cands[i] if resolved[i] in cands[i] else ()
            alts[i] = next((a for a in own if a.key != key and a.key not in taken), None)
        locked = [i for i in group if alts[i] is None]
        shifted = [i for i in group if alts[i] is not None]
        alt_keys = [alts[i].key for i in shifted if alts[i] is not None]
        if len(locked) == 1 and len(shifted) == len(alt_keys) == len(set(alt_keys)):
            taken.add(key)
            for i in shifted:
                alt = alts[i]
                if alt is not None:
                    resolved[i] = alt
                    taken.add(alt.key)
            continue
        if len(group) == 2 and _total_stem(bases[group[1]]) == bases[group[0]]:
            taken.add(key)
            alt0 = alts[group[0]]
            if alt0 is not None and alt0.key not in taken:
                resolved[group[0]] = alt0
                taken.add(alt0.key)
            else:
                resolved[group[0]] = None
    return resolved


def _ordered(
    names: Sequence[str], proposals: Sequence[Mapped | None], ranks: Sequence[Mapping[str, int]],
    bases: Sequence[str] = (), cands: Sequence[Sequence[Mapped]] = (), extra_taken: set[str] | None = None,
) -> tuple[list[Mapped | None], list[set[int]]]:
    resolved = (
        _resolve_contention(proposals, bases, cands, extra_taken or set())
        if bases and cands
        else list(proposals)
    )
    counts = defaultdict(int)
    for m in resolved:
        if m is not None:
            counts[m.key] += 1
    unique = [m if m is not None and counts[m.key] == 1 else None for m in resolved]
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
    names: Names | None = None, standard: str | None = None,
) -> list[tuple[Fact, Mapped | None]]:
    info = pdf_rows(rows, statement, printed_twice(learned, statement, names))
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
    if standard is not None:
        for n, m in _standard_rows(live_rows, bases, found, statement, standard, _standard_book(), names).items():
            found[n] = m
    result: list[Mapped | None] = [None] * len(rows)
    for n, i in enumerate(live):
        result[i] = found[n]
    for i, at in info.copies.items():
        result[i] = result[at]
    return list(zip(rows, result))


def _standard_entry(book: StandardBook, standard: str, statement: str, base: str) -> StandardLabel | None:
    found = book.labels.get((standard, statement, _canon_name(base)))
    stem = _total_stem(base)
    if found is None and stem is not None:
        found = book.labels.get((standard, statement, _canon_name(stem)))
    return found


def _strip_extra_heads(base: str, statement: str) -> str:
    heads = sorted(STANDARD_EXTRA_HEADS.get(statement, ()), key=len, reverse=True)
    name = base
    while True:
        for head in heads:
            rest = HEAD_SEP_RE.sub("", name[len(head):]) if name.startswith(head) else ""
            if rest and not HEAD_KEEP_RE.search(rest):
                name = rest
                break
        else:
            return name


def _section_homes(marks: Sequence[str | None], statement: str) -> list[str | None]:
    framed = any(_is_marker(k, statement) for k in marks)
    section: str | None = None
    homes: list[str | None] = [None] * len(marks)
    for n in range(len(marks) - 1, -1, -1):
        if _is_marker(marks[n], statement):
            section = marks[n]
        homes[n] = section or (STANDARD_OUTSIDE if framed else None)
    return homes


def _other_homes(
    marks: Sequence[str | None], subtotals: Sequence[bool], names: Sequence[str], statement: str,
) -> list[str | None]:
    section: str | None = None
    homes: list[str | None] = [None] * len(marks)
    for n in range(len(marks) - 1, -1, -1):
        if _is_marker(marks[n], statement):
            section = marks[n]
        elif statement == "CF" and subtotals[n] and section is not None and not section.endswith(STANDARD_ABOVE_SUBTOTAL):
            section += STANDARD_ABOVE_SUBTOTAL
        gross = section is not None and n + 1 < len(names) and names[n + 1] in ACCUMULATED_NAMES
        homes[n] = section + STANDARD_BEFORE_ACCUMULATED if gross else section
    return homes


def _accumulated_prev(names: Sequence[str], n: int) -> int | None:
    at = n - 1
    while at >= 0 and names[at] in ACCUMULATED_NAMES:
        at -= 1
    after = n + 1
    while after < len(names) and names[after] in ACCUMULATED_NAMES:
        after += 1
    if at < 0 or after >= len(names) or not names[after].endswith(NET_TAIL):
        return None
    return at


def _alone(picked: Mapping[int, str]) -> dict[int, str]:
    counts = Counter(picked.values())
    return {n: k for n, k in picked.items() if counts[k] == 1}


def _standard_rows(
    live_rows: Sequence[Fact], bases: Sequence[str], found: Sequence[Mapped | None], statement: str, standard: str,
    book: StandardBook, names: Names | None = None,
) -> dict[int, Mapped]:
    if not (book.labels or book.others or book.accumulated):
        return {}
    taken = {m.key for m in found if m is not None}
    entries = {
        n: entry for n, m in enumerate(found)
        if m is None and (entry := _standard_entry(book, standard, statement, bases[n])) is not None
    }

    def fits(key: str | None, n: int) -> bool:
        return key is not None and key not in taken and (key in book.per_share) == (live_rows[n].unit in PER_SHARE_UNITS)

    picked = _alone({n: e.any for n, e in entries.items() if _is_marker(e.any, statement) and fits(e.any, n)})
    marks = [m.key if m is not None else picked.get(n) for n, m in enumerate(found)]
    homes = _section_homes(marks, statement)
    for n, entry in entries.items():
        if n not in picked and fits(key := _standard_key(entry, homes[n]), n):
            picked[n] = str(key)
    first = _alone(picked)
    taken |= set(first.values())
    own = names or Names()
    for more in (_standard_named, _standard_again, _standard_accumulated):
        marks = [m.key if m is not None else first.get(n) for n, m in enumerate(found)]
        added = _alone({n: k for n, k in more(bases, marks, statement, standard, book, own).items() if fits(k, n)})
        taken |= set(added.values())
        first.update(added)
    return {n: Mapped(k, 1, STANDARD_BY) for n, k in first.items()}


def _closing_cash(book: StandardBook, standard: str, names: Names, home: str | None) -> str | None:
    for name in CLOSING_CASH_NAMES:
        if key := names.keys.get(name) or names.canon_keys.get(name):
            return key
    for name in CLOSING_CASH_NAMES:
        entry = book.labels.get((standard, "CF", name))
        if entry is not None and (key := _standard_key(entry, home)):
            return key
    return None


def _standard_key(entry: StandardLabel, home: str | None) -> str | None:
    return entry.any if home is None else entry.within.get(home) or (entry.any if entry.home == home else None)


def _standard_named(
    bases: Sequence[str], marks: Sequence[str | None], statement: str, standard: str, book: StandardBook, names: Names,
) -> dict[int, str]:
    out: dict[int, str] = {}
    homes = _section_homes(marks, statement)
    for n, base in enumerate(bases):
        if marks[n] is not None:
            continue
        stripped = _strip_extra_heads(base, statement)
        alias = STANDARD_ALIASES.get(statement, {}).get(_total_stem(base) or base)
        entry = _standard_entry(book, standard, statement, alias or stripped) if alias or stripped != base else None
        if entry is not None and _is_marker(entry.any, statement):
            out[n] = str(entry.any)
        elif entry is not None and (key := _standard_key(entry, homes[n])) is not None:
            out[n] = key
        if statement == "CF" and CLOSING_CASH_RE.match(base) and (key := _closing_cash(book, standard, names, homes[n])):
            out[n] = key
    subtotals = [bool(STANDARD_SUBTOTAL_RE.match(k or "")) or b == STANDARD_SUBTOTAL_NAME for k, b in zip(marks, bases)]
    canons = [_canon_name(b) for b in bases]
    other_homes = _other_homes(marks, subtotals, canons, statement)
    others = Counter(h for h, b in zip(other_homes, bases) if b == STANDARD_OTHER)
    for n, base in enumerate(bases):
        home = other_homes[n]
        if marks[n] is not None or base != STANDARD_OTHER or home is None or others[home] != 1:
            continue
        split = home + STANDARD_ABOVE_SUBTOTAL
        if split not in other_homes and (split in names.others or (standard, statement, split) in book.others):
            continue
        key = names.others.get(home) or book.others.get((standard, statement, home))
        if key is not None:
            out[n] = key
    return out


def _standard_again(
    bases: Sequence[str], marks: Sequence[str | None], statement: str, standard: str, book: StandardBook, names: Names,
) -> dict[int, str]:
    homes = _section_homes(marks, statement)
    return {
        n: key for n, base in enumerate(bases)
        if marks[n] is None and (entry := _standard_entry(book, standard, statement, base)) is not None
        and (key := _standard_key(entry, homes[n])) is not None
    }


def _standard_accumulated(
    bases: Sequence[str], marks: Sequence[str | None], statement: str, standard: str, book: StandardBook, names: Names,
) -> dict[int, str]:
    out: dict[int, str] = {}
    canons = [_canon_name(b) for b in bases]
    for n, name in enumerate(canons):
        if marks[n] is not None or name not in ACCUMULATED_NAMES:
            continue
        at = _accumulated_prev(canons, n)
        prev = marks[at] if at is not None else None
        if prev is None:
            continue
        key = names.accumulated.get((name, prev)) or book.accumulated.get((standard, name, prev))
        if key is not None:
            out[n] = key
    return out


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
        key = (names.keys.get(stem) or names.canon_keys.get(_canon_name(stem))) if stem is not None else None
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
    ranks = _ranks(learned, statement, cls, period_end)
    cands = [_lookup_all(ranked, statement, name) for name in keys]
    proposals: list[Mapped | None] = [cs[0] if cs else None for cs in cands]
    losses = _loss_rows(live_rows, keys, bases, proposals, ranked, statement)
    for i, m in losses.items():
        proposals[i] = m
    total_keys = {m.key for m in totals.values()}
    first, keeps = _ordered(keys, proposals, ranks, bases, cands, total_keys)
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
    proposals, keeps = _ordered(keys, proposals, ranks, bases, cands, total_keys)
    used = Counter(m.key for m in proposals if m is not None)
    used.update(m.key for m in totals.values())
    claimed = set(used)
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
            pos = {
                k: i
                for i, k in enumerate(_merge_statement(sorted(rank, key=rank.__getitem__), names.order, statement))
            }
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


def name_index(filings: Sequence[Filing], statement: str, excluded: Iterable[frozenset[str]] = ()) -> Names:
    xbrls = [
        f for f in sorted(filings, key=lambda f: (f.period_end, f.submitted), reverse=True)
        if not f.is_pdf
    ]
    skip = set(excluded)
    found: dict[str, list[str]] = defaultdict(list)
    within: dict[tuple[str, str], list[str]] = defaultdict(list)
    canon_found: dict[str, list[str]] = defaultdict(list)
    canon_within: dict[tuple[str, str], list[str]] = defaultdict(list)
    losses: dict[str, list[str]] = defaultdict(list)
    periods: dict[str, set[date]] = defaultdict(set)
    homes_by_key: dict[str, set[str | None]] = defaultdict(set)
    parts_by_key: dict[str, Part] = {}
    multi_name: set[str] = set()
    multi_canon: set[str] = set()
    multi_within: set[tuple[str, str]] = set()
    multi_canon_within: set[tuple[str, str]] = set()
    order: list[str] = []
    per_share: set[str] = set()
    others: dict[str, set[str]] = defaultdict(set)
    accumulated: dict[tuple[str, str], set[str]] = defaultdict(set)
    for filing in xbrls:
        facts = filing.by_statement.get(statement, [])
        if not facts:
            continue
        labels = [_clean_label(f.label) for f in facts]
        labels = ["" if OPENING_CASH_RE.match(name) else name for name in labels]
        canons = [_canon_name(name) if name else "" for name in labels]
        _own_others(facts, labels, statement, others)
        _own_accumulated(facts, canons, accumulated)
        per_share.update(f.key for f in facts if f.unit in PER_SHARE_UNITS)
        declared = filing.parts.get(statement, {})
        for k, part in declared.items():
            parts_by_key.setdefault(k, part)
        fhomes = _homes([f.key for f in facts], statement)
        seen_name: Counter[str] = Counter()
        seen_canon: Counter[str] = Counter()
        for f, name, cname in zip(facts, labels, canons):
            periods[f.key].add(filing.period_end)
            homes_by_key[f.key].add(fhomes.get(f.key))
            if name:
                found[name].append(f.key)
                seen_name[name] += 1
            if cname:
                canon_found[cname].append(f.key)
                seen_canon[cname] += 1
            dec = _cash_decrease_name(f.label) if statement == "CF" else None
            if dec is not None:
                losses[dec].append(f.key)
            loss = _loss_name(_profit_name(f.label) or "")
            if loss is not None:
                losses[loss].append(f.key)
        multi_name.update(n for n, c in seen_name.items() if c > 1)
        multi_canon.update(n for n, c in seen_canon.items() if c > 1)
        section: str | None = None
        seen_sec: Counter[tuple[str, str]] = Counter()
        seen_csec: Counter[tuple[str, str]] = Counter()
        for f, name, cname in zip(reversed(facts), reversed(labels), reversed(canons)):
            if _is_marker(f.key, statement):
                section = f.key
            if section is not None and name:
                within[(section, name)].append(f.key)
                seen_sec[(section, name)] += 1
            if section is not None and cname:
                canon_within[(section, cname)].append(f.key)
                seen_csec[(section, cname)] += 1
        multi_within.update(k for k, c in seen_sec.items() if c > 1)
        multi_canon_within.update(k for k, c in seen_csec.items() if c > 1)
        order = _merge_statement(order, [f.key for f in facts], statement)

    def pick(keys: list[str], label: str, multi: bool, check_home: bool) -> str | None:
        uniq = list(dict.fromkeys(keys))
        if len(uniq) == 1:
            return uniq[0]
        if multi or _never_merged(label):
            return None
        first = uniq[0]
        ps = first in per_share
        if any((k in per_share) != ps for k in uniq):
            return None
        if len({k.endswith("IFRS") for k in uniq}) > 1:
            return None
        key_set = set(uniq)
        if any(pair <= key_set for pair in skip):
            return None
        if sum(len(periods[k]) for k in uniq) != len(set().union(*(periods[k] for k in uniq))):
            return None
        if check_home:
            if len(set().union(*(homes_by_key[k] for k in uniq))) > 1:
                return None
            with_parts = [parts_by_key[k] for k in uniq if k in parts_by_key]
            if len(with_parts) > 1 and any(not _same_part(with_parts[0], p) for p in with_parts[1:]):
                return None
        return first

    return Names(
        {n: k for n, ks in found.items() if (k := pick(ks, n, n in multi_name, True)) is not None},
        order,
        {sk: k for sk, ks in within.items() if (k := pick(ks, sk[1], sk in multi_within, False)) is not None},
        per_share,
        {n: k for n, ks in losses.items() if (k := pick(ks, n, False, True)) is not None},
        {n: k for n, ks in canon_found.items() if (k := pick(ks, n, n in multi_canon, True)) is not None},
        {sk: k for sk, ks in canon_within.items()
         if (k := pick(ks, sk[1], sk in multi_canon_within, False)) is not None},
        {home: next(iter(ks)) for home, ks in others.items() if len(ks) == 1},
        {pair: next(iter(ks)) for pair, ks in accumulated.items() if len(ks) == 1},
    )


def _own_others(facts: Sequence[Fact], labels: Sequence[str], statement: str, out: dict[str, set[str]]) -> None:
    keys = [f.key for f in facts]
    canons = [_canon_name(name) for name in labels]
    homes = _other_homes(keys, [bool(STANDARD_SUBTOTAL_RE.match(k)) for k in keys], canons, statement)
    count = Counter(h for h, name in zip(homes, labels) if name == STANDARD_OTHER)
    for key, home, name in zip(keys, homes, labels):
        if name == STANDARD_OTHER and home is not None and count[home] == 1:
            out[home].add(key)


def _own_accumulated(facts: Sequence[Fact], canons: Sequence[str], out: dict[tuple[str, str], set[str]]) -> None:
    for n, name in enumerate(canons):
        at = _accumulated_prev(canons, n) if name in ACCUMULATED_NAMES else None
        if at is not None:
            out[(name, facts[at].key)].add(facts[n].key)


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
        cname = _canon_name(name)
        key = (names.keys.get(name) or names.canon_keys.get(cname)) if count[bases[i]] == 1 else None
        if key is None and count[bases[i]] == 1 and losing[i]:
            key = names.losses.get(name) or names.losses.get(cname)
        if key is None and sk is not None and bases[i] not in GENERIC_LABELS:
            key = names.by_section.get(sk) or names.canon_by_section.get((sk[0], _canon_name(sk[1])))
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
                key = names.by_section.get(sk) or names.canon_by_section.get((sk[0], _canon_name(sk[1])))
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


def _merge_statement(base: list[str], seq: Sequence[str], statement: str) -> list[str]:
    markers = MARKER_ORDER.get(statement)
    if not markers or not base:
        return _merge(base, seq)
    m_rank = {k: i for i, k in enumerate(markers)}
    out = list(base)
    pos = {k: i for i, k in enumerate(out)}
    i = 0
    n = len(seq)
    prev: str | None = None
    while i < n:
        if seq[i] in pos:
            prev = seq[i]
            i += 1
            continue
        j = i
        while j < n and seq[j] not in pos:
            j += 1
        run = seq[i:j]
        nxt = seq[j] if j < n else None
        at = pos[prev] + 1 if prev is not None else 0
        limit = pos[nxt] if nxt is not None and pos[nxt] >= at else len(out)
        run_m = next((m_rank[k] for k in run if k in m_rank), None)
        if run_m is not None:
            for k in range(at, limit):
                existing = m_rank.get(out[k])
                if existing is not None and existing < run_m:
                    at = k + 1
        out[at:at] = run
        pos = {k: idx for idx, k in enumerate(out)}
        prev = run[-1]
        i = j
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


def _scoped_for_pdf(
    filing: Filing,
    filings: Sequence[Filing],
    pool: Sequence[Learned],
    statement: str,
    default_names: Names,
    names_by_std: dict[str, Names],
    excluded: Iterable[frozenset[str]] = (),
) -> tuple[Sequence[Learned], Names]:
    std = filing.standard
    if not std:
        return pool, default_names
    has_same = any(not f.is_pdf and statement in f.by_statement and f.standard == std for f in filings)
    has_other = any(
        not f.is_pdf and statement in f.by_statement and f.standard is not None and f.standard != std
        for f in filings
    )
    if not (has_same and has_other):
        return pool, default_names
    scoped_pool = [x for x in pool if x.standard in (None, std)]
    if std not in names_by_std:
        names_by_std[std] = name_index(
            [f for f in filings if f.is_pdf or f.standard in (None, std)], statement, excluded,
        )
    return scoped_pool, names_by_std[std]


def pdf_only_matches(db: Session, code: str) -> list[PdfMatch]:
    out: list[PdfMatch] = []
    for basis, filings in load_both(db, code).items():
        learned = [learn_pair(pdf, xbrl) for pdf, xbrl in _pairs(filings)]
        xbrl_ends = {f.period_end for f in filings if not f.is_pdf and f.by_statement}
        for statement in STATEMENTS:
            excluded = _merge_exclusions().get((code, statement), set())
            names = name_index(filings, statement, excluded)
            names_by_std: dict[str, Names] = {}
            for filing in filings:
                if not filing.is_pdf or filing.period_end in xbrl_ends or statement not in filing.by_statement:
                    continue
                facts = filing.by_statement[statement]
                pool, scoped_names = _scoped_for_pdf(
                    filing, filings, learned, statement, names, names_by_std, excluded,
                )
                for fact, mapped in apply_learned(
                    facts, pool, statement, filing.period_end, filing.cls, scoped_names, filing.standard,
                ):
                    if statement == "CF" and mapped is None and _opening_cash(fact.key):
                        continue
                    out.append(PdfMatch(filing, basis, statement, fact, mapped))
    return out


def build_columns(
    filings: Sequence[Filing], learned: Sequence[Learned], statement: str, excluded: Iterable[frozenset[str]] = (),
) -> list[Column]:
    names = name_index(filings, statement, excluded)
    names_by_std: dict[str, Names] = {}
    by_end: dict[date, list[Filing]] = defaultdict(list)
    for f in filings:
        if statement in f.by_statement:
            by_end[f.period_end].append(f)
    columns: list[Column] = []
    slots: list[list[tuple[str, str, str | None]]] = []
    for period_end in sorted(by_end):
        group = by_end[period_end]
        chosen = []
        revisions: list[Filing] = []
        for source in SOURCES:
            same = [f for f in group if f.source == source]
            if not same:
                continue
            if source == "tdnet_pdf":
                chosen.append(_pdf_base(same, statement))
                revisions = _revisions(same, chosen[-1])
            else:
                chosen.append(_newest(same))
        values: dict[str, float] = {}
        sources: dict[str, str] = {}
        parts: dict[str, Part] = {}
        order: list[str] = []
        primary = chosen[0]
        loose: list[tuple[str, str, str | None]] = []
        for filing in chosen:
            seq: list[str] = []
            if filing.is_pdf:
                cls = filing.cls
                pairs = [x for x in learned if x.period_end == period_end]
                raw_pool = pairs if not primary.is_pdf and pairs else learned
                pool, scoped_names = _scoped_for_pdf(
                    filing, filings, raw_pool, statement, names, names_by_std, excluded,
                )
                facts = filing.by_statement[statement]
                by_name = scoped_names if filing is primary else None
                copies = pdf_rows(facts, statement, printed_twice(pool, statement, by_name)).copies
                revised, later = _revised(revisions, pool, statement, period_end, by_name)
                standard = filing.standard if filing is primary else None
                applied = apply_learned(facts, pool, statement, period_end, cls, by_name, standard)
                homes = _section_homes([None if m is None else m.key for _, m in applied], statement)
                for n, (fact, mapped) in enumerate(applied):
                    if n in copies:
                        continue
                    if mapped is None:
                        if filing is primary:
                            loose.append((fact.key, _base(fact, statement), homes[n]))
                            values[fact.key] = revised.get(fact.key, fact.value)
                            sources[fact.key] = filing.source
                            seq.append(fact.key)
                        continue
                    seq.append(mapped.key)
                    if mapped.key not in values:
                        values[mapped.key] = revised.get(mapped.key, fact.value * mapped.sign)
                        sources[mapped.key] = filing.source
                for key in later:
                    if key not in values:
                        values[key] = revised[key]
                        sources[key] = filing.source
                        seq.append(key)
            else:
                declared = filing.parts.get(statement, {})
                for fact in _with_extras(filing, statement, learned):
                    seq.append(fact.key)
                    if fact.key not in values:
                        values[fact.key] = fact.value
                        sources[fact.key] = filing.source
                        if fact.key in declared:
                            parts[fact.key] = declared[fact.key]
            order = _merge_statement(order, [k for k in seq if k in values], statement)
        columns.append(Column(primary, values, order, sources, parts=parts))
        slots.append(loose)
    if statement == "BS":
        _key_by_section(columns, slots)
    return columns


def _key_by_section(columns: Sequence[Column], slots: Sequence[Sequence[tuple[str, str, str | None]]]) -> None:
    rank = {k: i for i, k in enumerate(MARKER_ORDER["BS"])}
    found: dict[str, set[tuple[int, str, int]]] = defaultdict(set)
    placed: list[list[tuple[str, str, tuple[int, str, int]]]] = []
    for loose in slots:
        seen: Counter[tuple[str, str | None]] = Counter()
        here = []
        for key, base, home in loose:
            seen[(base, home)] += 1
            slot = (rank.get(home or "", len(rank)), home or "", seen[(base, home)])
            found[base].add(slot)
            here.append((key, base, slot))
        placed.append(here)
    for column, here in zip(columns, placed):
        renamed: dict[str, str] = {}
        for key, base, slot in here:
            ordered = sorted(found[base])
            if len(ordered) < 2:
                continue
            at = ordered.index(slot)
            renamed[key] = f"pdf:{base}" if at == 0 else f"pdf:{base}#{at + 1}"
        if not renamed or all(k == v for k, v in renamed.items()):
            continue
        column.values = {renamed.get(k, k): v for k, v in column.values.items()}
        column.sources = {renamed.get(k, k): v for k, v in column.sources.items()}
        column.order = [renamed.get(k, k) for k in column.order]


def row_parts(columns: Sequence[Column], keys: Sequence[str]) -> list[Part | None]:
    """行の区分。その行に値があるいちばん新しい列の、書類の表示リンクの区分。

    表示リンクの区分が無い行（対応の付かない PDF の行、表示リンクの無い書類だけの行）は、上で最初に区分のある行と同じ。
    その行が区分か大区分の合計なら、下で最初に区分のある行と同じ。
    """
    declared: dict[str, Part] = {}
    for column in columns:
        declared.update(column.parts)
    out = [declared.get(k) for k in keys]
    below: Part | None = None
    after: list[Part | None] = []
    for part in reversed(out):
        after.append(below)
        below = part or below
    after.reverse()
    above: Part | None = None
    closed = True
    for i, key in enumerate(keys):
        if out[i] is not None:
            above = out[i]
            closed = key in (above.group, above.section)
        else:
            out[i] = after[i] if closed else above
    return out


def gather_heads(keys: Sequence[str], paths: Sequence[tuple], labels: Mapping[str, str]) -> list[int]:
    def total(i: int, head: tuple, depth: int) -> bool:
        if len(paths[i]) != depth + 1:
            return False
        return keys[i] == str(head[0]).replace("Abstract", "", 1) or bool(TOTAL_TAIL_RE.search(labels.get(keys[i], "")))

    def gather(rows: list[int], depth: int) -> list[int]:
        segments: list[tuple[tuple | None, list[int]]] = []
        for i in rows:
            head = paths[i][depth] if len(paths[i]) > depth else None
            if segments and segments[-1][0] == head:
                segments[-1][1].append(i)
            else:
                segments.append((head, [i]))
        found: dict[tuple, list[int]] = defaultdict(list)
        for n, (head, _) in enumerate(segments):
            if head is not None:
                found[head].append(n)
        for head, at in found.items():
            if len(at) < 2:
                continue
            keep = max(at, key=lambda n: (len(segments[n][1]), -n))
            body = segments[keep][1]
            before = [i for n in at if n < keep for i in segments[n][1]]
            after = [i for n in at if n > keep for i in segments[n][1]]
            tail = [body[-1]] if after and total(body[-1], head, depth) else []
            merged = before + body[: len(body) - len(tail)] + after + tail
            for n in at:
                segments[n] = (head, merged if n == keep else [])
        out: list[int] = []
        for head, members in segments:
            out.extend(members if head is None else gather(members, depth + 1))
        return out

    return gather(list(range(len(keys))), 0)


def _same_part(a: Part | None, b: Part | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.coarse == b.coarse


def _shown(
    keys: Sequence[str], parts: Sequence[Part | None], statement: str, labels: Mapping[str, str],
) -> list[tuple[list[dict[str, str]], int]]:
    """画面に出す見出しの道筋（外側から {key, name}）と字下げ。

    深さのある書類の行は、表示リンクの宣言どおり（Part.heads / indent）。深さの無い書類の行は、表の中に同じ大区分・区分の
    合計の要素の行があって深さのある書類のものなら、その行の見出しを借りる。無ければ 大区分・区分 の 2 段（PL は出さない）。
    """
    declared = {k: p for k, p in zip(keys, parts) if p is not None and p.heads is not None}
    known: dict[tuple[tuple[Head, ...], str], Head] = {}
    for p in declared.values():
        for n, head in enumerate(p.heads or ()):
            known.setdefault((tuple(p.heads or ())[:n], head[0]), head)
    out: list[tuple[list[dict[str, str]], int]] = []
    for part in parts:
        if part is None:
            out.append(([], 0))
            continue
        if part.heads is not None:
            path, indent = part.heads, part.indent
            head = known.get((path, f"{part.under}Abstract")) if part.under else None
            if head is not None:
                path, indent = (*path, head), indent - 1
            out.append(([{"key": k, "name": n} for k, n in path], indent))
            continue
        group, section = part.group, part.section
        anchor = declared.get(section or group or "")
        if anchor is not None and (anchor.group, anchor.section) == (group, section):
            out.append(([{"key": k, "name": n} for k, n in anchor.heads or ()], 0))
        elif statement == "PL":
            out.append(([], 0))
        else:
            out.append(([
                {"key": key, "name": _heading(key, labels, names)}
                for key, names in ((group, BS_GROUPS), (section, SECTION_NAMES)) if key is not None
            ], 0))
    return out


def _heading(key: str, labels: Mapping[str, str], fallback: Mapping[str, str]) -> str:
    label = labels.get(key, "")
    name = "" if label == key else TOTAL_TAIL_RE.sub("", label)
    if name == label and key in BS_GROUPS:
        name = ""
    return name or fallback.get(key) or fallback.get(key.removesuffix("IFRS")) or key


def _clean_label(raw: str) -> str:
    out = unicodedata.normalize("NFKC", raw)
    for rx in LABEL_NOISE_RES:
        out = rx.sub("", out)
    return out.strip()


def _canon_name(name: str) -> str:
    out = CANON_NOISE_RE.sub("", name).strip()
    out = CANON_PERIOD_RE.sub(r"当期\1", out)
    out = out.replace("当期包括利益", "包括利益")
    out = CANON_TAX_RE.sub(r"税引前\1", out)
    out = CANON_PRETAX_IFRS_RE.sub(r"税引前\1", out)
    return out


def _pdf_label(local: str, xbrl_label: str = "") -> str:
    name = TRAIL_BAR_RE.sub("", SUFFIX_RE.sub("", local))
    if (
        len(xbrl_label) >= 2
        and xbrl_label not in GENERIC_LABELS
        and xbrl_label in name
        and xbrl_label != name
        and not name.endswith("計")
    ):
        return xbrl_label
    norm = unicodedata.normalize("NFKC", name)
    stripped = PER_SHARE_HEAD_RE.sub("", norm)
    if stripped != norm:
        return stripped
    for head in sorted(SECTION_HEADS, key=len, reverse=True):
        rest = HEAD_SEP_RE.sub("", name[len(head):])
        if name.startswith(head) and rest and not HEAD_KEEP_RE.search(rest):
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

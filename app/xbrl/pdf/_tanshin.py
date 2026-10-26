"""短信の見出し・列の役割・単位。分岐はここの表に置く。"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Literal, NewType

from app.models import Statement
from app.xbrl.context import ParsedContext, assign_relative_periods, shift_years
from app.xbrl.normalize import ALLOWED_MEMBERS, PDF_SUMMARY_NS
from app.xbrl.parser import ParsedFact, ParsedPackage
from app.xbrl.pdf._layout import Line, Page, PdfLayoutError, grid, split_amounts

DisclosureNo = NewType("DisclosureNo", str)
_DISCLOSURE_NO = re.compile(r"\d{14}")
_EARNINGS_DOC = re.compile(
    r"^(1Q|2Q|3Q|FY)FinancialStatements_(Consolidated|NonConsolidated)_(JP|IFRS|US)$"
)
_PERIODS = {"1Q": "Q1", "2Q": "Q2", "3Q": "Q3", "FY": "FY"}
_SPAN_OF_PERIOD = {"Q1": "AccumulatedQ1", "Q2": "AccumulatedQ2", "Q3": "AccumulatedQ3", "FY": "Year"}
_URI = "http://stock-analyzer.local/taxonomy/tdnet-pdf/"
_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
_YEAR_MONTH = re.compile(r"(\d{4})年(\d{1,2})月期")
_LEADING = re.compile(r"^(?:[①-⑳]|※\d*|\(注\d*\)|\(\d+\)|\d+\.)+")
_HEADING_LEAD = re.compile(r"^(?:[①-⑳]|\(\d+\)|\d+\.)+")
_HINTS = re.compile(r"\(△は損失\)|\(△\)")
# 本表の途中に出る分類見出し。grid が次の行名へ連結しない。
_CLASS_HEAD = re.compile(r"の部$")
# 続きページの柱（銘柄コードと題名）。数値の無い行として次の科目名に連結しない。
_RUNNING_HEAD = re.compile(r"\(\d{4}\).*決算短信")
_UNIT_ROW = re.compile(r"単位|百万円|千円|円銭|百万円未満")
_UNIT_TOKENS = frozenset({"百万円", "千円", "円", "円銭", "銭", "%", "％"})
_NUMERIC_ITEM = re.compile(r"\d")
# 1 ページ目の列見出しのうち、科目名ではなく列の役割・単位。
_HEADER_ROLE = re.compile(
    r"増減率|対前期|前回発表予想|今回修正予想|今回発表予想"
    r"|第[123]四半期末|中間期末|^期末$|^(年間)?合計$|下限|上限"
    r"|第[123]四半期|中間期|上期|通期"
)
# IFRS 本表は費用を △ で印字する。ZIP の CostOfSales は正なので符号を合わせる。
_IFRS_EXPENSE_ABS = frozenset({"売上原価", "販売費及び一般管理費"})
_STMT = r"(要約)?(四半期|中間)?(連結|個別)?"


def parse_disclosure_no(raw: str) -> DisclosureNo:
    if not _DISCLOSURE_NO.fullmatch(raw or ""):
        raise ValueError(f"disclosure_no must be 14 digits: {raw!r}")
    return DisclosureNo(raw)


class DocKind(str, Enum):
    EARNINGS = "earnings"
    FORECAST_REVISION = "forecast_revision"
    DIVIDEND_REVISION = "dividend_revision"


Period = Literal["Q1", "Q2", "Q3", "FY"]
Standard = Literal["JPGAAP", "IFRS", "USGAAP"]


@dataclass(frozen=True)
class Unsupported:
    reason: Literal["doc-type", "reit", "irregular-period", "no-dates"]


@dataclass(frozen=True)
class PdfDisclosure:
    """statements の 1 行。PDF の題名は使わない。"""

    disclosure_no: DisclosureNo
    code: str
    kind: DocKind
    period: Period
    consolidated: bool
    standard: Standard | None
    period_start: date
    period_end: date
    fiscal_year_start: date
    fiscal_year_end: date
    submitted_at: datetime
    title: str

    @classmethod
    def parse(cls, st: Statement) -> PdfDisclosure | Unsupported:
        doc = st.type_of_document or ""
        if "REIT" in doc:
            return Unsupported("reit")
        kind: DocKind | None = None
        consolidated = "NonConsolidated" not in doc
        standard: Standard | None = None
        doc_period: str | None = None
        matched = _EARNINGS_DOC.fullmatch(doc)
        if matched:
            if matched.group(3) == "US":
                return Unsupported("doc-type")
            kind = DocKind.EARNINGS
            doc_period = matched.group(1)
            consolidated = matched.group(2) == "Consolidated"
            standard = "IFRS" if matched.group(3) == "IFRS" else "JPGAAP"
        elif doc == "EarnForecastRevision":
            kind = DocKind.FORECAST_REVISION
        elif doc == "DividendForecastRevision":
            kind = DocKind.DIVIDEND_REVISION
        else:
            return Unsupported("doc-type")
        raw_period = st.type_of_period or doc_period
        if raw_period not in _PERIODS:
            return Unsupported("irregular-period")
        if any(
            value is None
            for value in (
                st.period_start,
                st.period_end,
                st.fiscal_year_start,
                st.fiscal_year_end,
                st.disclosed_date,
            )
        ):
            return Unsupported("no-dates")
        assert kind is not None
        assert st.period_start is not None and st.period_end is not None
        assert st.fiscal_year_start is not None and st.fiscal_year_end is not None
        assert st.disclosed_date is not None
        return cls(
            disclosure_no=parse_disclosure_no(st.disclosure_no),
            code=st.code,
            kind=kind,
            period=_PERIODS[raw_period],  # type: ignore[arg-type]
            consolidated=consolidated,
            standard=standard,
            period_start=st.period_start,
            period_end=st.period_end,
            fiscal_year_start=st.fiscal_year_start,
            fiscal_year_end=st.fiscal_year_end,
            submitted_at=_submitted(st),
            title=(st.type_of_document or "")[:200],
        )

    @property
    def anchor_end(self) -> date:
        """assign_relative_periods の基準日。決算短信は本表の期末、修正は会計年度末。"""
        if self.kind is DocKind.EARNINGS:
            return self.period_end
        return self.fiscal_year_end

    def filing_meta(self) -> dict:
        if self.kind is DocKind.EARNINGS:
            doc_type, period_type = ("annual", "FY") if self.period == "FY" else ("quarterly", self.period)
        elif self.kind is DocKind.FORECAST_REVISION:
            doc_type, period_type = "forecast_revision", self.period
        else:
            doc_type, period_type = "dividend_revision", self.period
        return {
            "doc_type": doc_type,
            "period_type": period_type,
            "is_amendment": False,
            "accounting_standard": self.standard,
        }


def _submitted(st: Statement) -> datetime:
    clock = (st.disclosed_time or "00:00:00").strip()
    if len(clock) == 5:
        clock = f"{clock}:00"
    return datetime.combine(st.disclosed_date, time.fromisoformat(clock))


@dataclass(frozen=True)
class Namespace:
    key: str
    uri: str


NS_SUMMARY = Namespace(PDF_SUMMARY_NS, _URI + "pdf-sum")
NS_BS = Namespace("pdf-bs", _URI + "pdf-bs")
NS_PL = Namespace("pdf-pl", _URI + "pdf-pl")
NS_CF = Namespace("pdf-cf", _URI + "pdf-cf")


class Axis(Enum):
    ITEM_ROWS = "item_rows"
    ITEM_COLUMNS = "item_columns"


class Shape(Enum):
    INSTANT = "instant"
    DURATION = "duration"
    DIVIDEND = "dividend"


@dataclass(frozen=True)
class SectionSpec:
    name: str
    headings: re.Pattern[str]
    namespace: Namespace | None
    axis: Axis
    shape: Shape
    kinds: frozenset[DocKind]
    base_members: frozenset[str]


_E = frozenset({DocKind.EARNINGS})
_FR = frozenset({DocKind.FORECAST_REVISION})
_DR = frozenset({DocKind.DIVIDEND_REVISION})
_RESULT = frozenset({"ResultMember"})
_FORECAST = frozenset({"ForecastMember"})


def _h(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# 先に一致した見出しが勝つ。ブロックはその見出しから次の見出しの手前まで。
SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("summary_pl", _h(r"^\(\d\)(連結)?経営成績"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _E, _RESULT),
    SectionSpec("summary_bs", _h(r"^\(\d\)(連結)?財政状態(?!計算書)"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.INSTANT, _E, _RESULT),
    SectionSpec(
        "summary_cf",
        _h(r"^\(\d\)(連結)?キャッシュ・フローの状況"),
        NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _E, _RESULT,
    ),
    SectionSpec("summary_div", _h(r"^\d\.配当の状況"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DIVIDEND, _E, frozenset()),
    SectionSpec("summary_fc", _h(r"^\d\..*(連結)?業績予想"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _E, _FORECAST),
    SectionSpec("summary_solo", _h(r"^\(参考\)個別"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _E, _RESULT),
    SectionSpec(
        "bs",
        _h(_STMT + r"(貸借対照表|財政状態計算書)$"),
        NS_BS, Axis.ITEM_ROWS, Shape.INSTANT, _E, frozenset(),
    ),
    SectionSpec(
        "pl",
        _h(_STMT + r"(損益計算書|損益及び包括利益計算書)(及び" + _STMT + r"包括利益計算書)?$"),
        NS_PL, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset(),
    ),
    SectionSpec(
        "cf",
        _h(_STMT + r"キャッシュ・フロー計算書$"),
        NS_CF, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset(),
    ),
    SectionSpec("ci", _h(_STMT + r"包括利益計算書$"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("equity", _h(r"株主資本等変動計算書$|持分変動計算書$"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("segment", _h(r"セグメント情報"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("fc_rev", _h(r"業績予想.*修正"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _FR, frozenset()),
    SectionSpec("div_rev", _h(r"配当予想.*修正"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DIVIDEND, _DR, frozenset()),
)


class PeriodRef(Enum):
    CURRENT = "Current"
    PRIOR = "Prior1"
    NEXT = "Next"


class Span(Enum):
    YEAR = "Year"
    ACC_Q1 = "AccumulatedQ1"
    ACC_Q2 = "AccumulatedQ2"
    ACC_Q3 = "AccumulatedQ3"
    INSTANT = "Instant"


@dataclass(frozen=True)
class Role:
    period: PeriodRef
    span: Span
    members: frozenset[str]


# 後の行が前の行を上書きする。日付は role_of が単語より後に当てる。
# ^前 は「前回」に当てない。前回は修正前の予想で、前期の列ではない。
ROLE_WORDS: tuple[tuple[re.Pattern[str], dict], ...] = (
    (_h(r"増減率|増減|対前期"), {"skip": True}),
    (_h(r"前回発表予想|前回予想|\(A\)"), {"add": frozenset({"ForecastMember", "PreviousMember"})}),
    (_h(r"今回修正予想|今回発表予想|\(B\)"), {"add": frozenset({"ForecastMember", "CurrentMember"})}),
    (_h(r"第1四半期末"), {"add": frozenset({"FirstQuarterMember"})}),
    (_h(r"第2四半期末|中間期末"), {"add": frozenset({"SecondQuarterMember"})}),
    (_h(r"第3四半期末"), {"add": frozenset({"ThirdQuarterMember"})}),
    (_h(r"^期末$"), {"add": frozenset({"YearEndMember"})}),
    (_h(r"^(年間)?合計$"), {"add": frozenset({"AnnualMember"})}),
    (_h(r"下限"), {"add": frozenset({"LowerMember"})}),
    (_h(r"上限"), {"add": frozenset({"UpperMember"})}),
    (_h(r"第2四半期\(累計\)|中間期|上期"), {"span": Span.ACC_Q2}),
    (_h(r"第1四半期(?!末)"), {"span": Span.ACC_Q1}),
    (_h(r"第3四半期(?!末)"), {"span": Span.ACC_Q3}),
    (_h(r"通期"), {"span": Span.YEAR}),
    (_h(r"^当|当期|当第\d四半期|当中間"), {"period": PeriodRef.CURRENT}),
    (_h(r"^(?!前回)(?:前|前期|前第\d四半期|前中間)"), {"period": PeriodRef.PRIOR}),
    (_h(r"次期|翌期"), {"period": PeriodRef.NEXT}),
    (_h(r"予想"), {"add": frozenset({"ForecastMember"})}),
    (_h(r"実績"), {"add": frozenset({"ResultMember"})}),
)

_AXIS = {
    "ResultMember": "ResultForecastAxis",
    "ForecastMember": "ResultForecastAxis",
    "PreviousMember": "RevisionAxis",
    "CurrentMember": "RevisionAxis",
    "LowerMember": "RangeAxis",
    "UpperMember": "RangeAxis",
    "FirstQuarterMember": "DividendAxis",
    "SecondQuarterMember": "DividendAxis",
    "ThirdQuarterMember": "DividendAxis",
    "YearEndMember": "DividendAxis",
    "AnnualMember": "DividendAxis",
}


def _emitted_members() -> frozenset[str]:
    found: set[str] = set()
    for spec in SECTIONS:
        found |= set(spec.base_members)
    for _pattern, patch in ROLE_WORDS:
        found |= set(patch.get("add") or ())
    return frozenset(found)


assert _emitted_members() <= ALLOWED_MEMBERS


class UnitKind(Enum):
    YEN = ("JPY", 0)
    YEN_THOUSANDS = ("JPY", 3)
    YEN_MILLIONS = ("JPY", 6)
    PER_SHARE = ("JPYPerShares", 0)
    PERCENT = ("pure", -2)


def _compact(raw: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw or ""))


def label_key(raw: str) -> str:
    """印字の行名 → 要素の local_name。tdnet_pdf.yaml の name はこの結果と一致する。"""
    text = unicodedata.normalize("NFKC", raw or "")
    text = text.replace("·", "・").replace("･", "・")
    text = text.replace("－", "ー").replace("−", "ー").replace("‐", "ー")
    text = re.sub(r"\s+", "", text)
    previous = None
    while previous != text:
        previous = text
        text = _LEADING.sub("", text)
        text = _HINTS.sub("", text)
    return text


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, _month_days(year, month)))


def _month_days(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _dates_in(texts: tuple[str, ...]) -> list[date]:
    found: list[date] = []
    for text in texts:
        for year, month, day in _DATE.findall(text):
            try:
                found.append(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return found


def _near(left: date, right: date) -> bool:
    return abs((left - right).days) <= 3


def _span_for(d: PdfDisclosure, spec: SectionSpec) -> Span:
    if spec.shape is Shape.INSTANT:
        return Span.YEAR
    name = _SPAN_OF_PERIOD[d.period]
    return Span(name)


def _period_from_date(end: date, d: PdfDisclosure, spec: SectionSpec) -> tuple[PeriodRef, Span] | None:
    reported = _span_for(d, spec)
    if _near(end, d.period_end):
        return PeriodRef.CURRENT, reported
    if _near(end, shift_years(d.period_end, -1)):
        return PeriodRef.PRIOR, reported
    if _near(end, d.fiscal_year_end):
        return PeriodRef.CURRENT, Span.YEAR
    if _near(end, shift_years(d.fiscal_year_end, -1)):
        return PeriodRef.PRIOR, Span.YEAR
    if _near(end, shift_years(d.fiscal_year_end, 1)):
        return PeriodRef.NEXT, Span.YEAR
    return None


def _period_from_year_month(
    year: int, month: int, d: PdfDisclosure, spec: SectionSpec,
) -> tuple[PeriodRef, Span] | None:
    """YYYY年M月期 は日が印字されない。20 日決算でも年月が合えば当期/前期。"""
    reported = _span_for(d, spec)
    for end, period, span in (
        (d.period_end, PeriodRef.CURRENT, reported),
        (shift_years(d.period_end, -1), PeriodRef.PRIOR, reported),
        (d.fiscal_year_end, PeriodRef.CURRENT, Span.YEAR),
        (shift_years(d.fiscal_year_end, -1), PeriodRef.PRIOR, Span.YEAR),
        (shift_years(d.fiscal_year_end, 1), PeriodRef.NEXT, Span.YEAR),
    ):
        if end.year == year and end.month == month:
            return period, span
    return None


def _heading_key(raw: str) -> str:
    """TOC の点線は残す。番号と囲みだけ落として見出し正規表現に渡す。"""
    text = _compact(raw)
    text = _HEADING_LEAD.sub("", text)
    if len(text) >= 2 and text[0] in "(（" and text[-1] in ")）":
        text = text[1:-1]
    return text


def _is_unit_token(raw: str) -> bool:
    key = _compact(raw)
    if key in _UNIT_TOKENS:
        return True
    return "単位" in key and len(key) <= 12


def _split_item_headers(headers: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """折り返した科目名を結合し、四半期末・単位は列の役割に残す。"""
    items: list[str] = []
    roles: list[str] = []
    for header in headers:
        compact = _compact(header)
        if not compact:
            continue
        if _is_unit_token(compact) or _DATE.search(compact) or _YEAR_MONTH.search(compact):
            roles.append(header)
            continue
        if _HEADER_ROLE.search(compact):
            roles.append(header)
            continue
        items.append(header)
    if items:
        return "".join(items), tuple(roles)
    first = next((header for header in headers if _compact(header)), "")
    return first, tuple(roles)


def role_of(
    texts: tuple[str, ...],
    spec: SectionSpec,
    d: PdfDisclosure,
    *,
    rightmost: bool = False,
) -> Role | None:
    norm = tuple(_compact(text) for text in texts if text and _compact(text))
    for text in norm:
        for pattern, patch in ROLE_WORDS:
            if patch.get("skip") and pattern.search(text):
                return None
    members: set[str] = set(spec.base_members)
    period: PeriodRef | None = None
    span: Span | None = None
    for text in norm:
        for pattern, patch in ROLE_WORDS:
            if patch.get("skip") or not pattern.search(text):
                continue
            if "period" in patch:
                period = patch["period"]
            if "span" in patch:
                span = patch["span"]
            if "add" in patch:
                members |= set(patch["add"])
    if "ResultMember" in spec.base_members and any("予想" in text for text in norm):
        members.discard("ResultMember")
        members.add("ForecastMember")
    dated = None
    for text in reversed(norm):
        found = _YEAR_MONTH.findall(text)
        if found:
            year, month = found[-1]
            dated = _period_from_year_month(int(year), int(month), d, spec)
            if dated is not None:
                break
    if dated is None and _dates_in(norm):
        dated = _period_from_date(_dates_in(norm)[-1], d, spec)
    if dated is not None:
        period, span = dated
    if "ForecastMember" in members:
        if span is None:
            span = Span.YEAR
        if period is None:
            period = (
                PeriodRef.NEXT
                if d.kind is DocKind.EARNINGS and d.period == "FY"
                else PeriodRef.CURRENT
            )
    else:
        if period is None:
            if spec.axis is Axis.ITEM_ROWS:
                period = PeriodRef.CURRENT if rightmost else PeriodRef.PRIOR
            else:
                period = PeriodRef.CURRENT
        if span is None:
            span = _span_for(d, spec)
    if spec.shape is Shape.INSTANT:
        span = Span.YEAR
    assert period is not None and span is not None
    return Role(period, span, frozenset(members))


def _bounds(role: Role, d: PdfDisclosure, instant: bool) -> tuple[date | None, date]:
    if instant:
        end = d.period_end
        if role.period is PeriodRef.PRIOR:
            end = shift_years(end, -1)
        elif role.period is PeriodRef.NEXT:
            end = shift_years(end, 1)
        return None, end
    if role.span is Span.ACC_Q1:
        start, end = d.fiscal_year_start, _add_months(d.fiscal_year_start, 3) - timedelta(days=1)
    elif role.span is Span.ACC_Q2:
        start, end = d.fiscal_year_start, _add_months(d.fiscal_year_start, 6) - timedelta(days=1)
    elif role.span is Span.ACC_Q3:
        start, end = d.fiscal_year_start, _add_months(d.fiscal_year_start, 9) - timedelta(days=1)
    else:
        start, end = d.fiscal_year_start, d.fiscal_year_end
    if role.period is PeriodRef.PRIOR:
        return shift_years(start, -1), shift_years(end, -1)
    if role.period is PeriodRef.NEXT:
        return shift_years(start, 1), shift_years(end, 1)
    return start, end


def context_for(
    role: Role,
    spec: SectionSpec,
    d: PdfDisclosure,
    *,
    consolidated: bool,
) -> ParsedContext:
    instant = spec.shape is Shape.INSTANT
    start, end = _bounds(role, d, instant)
    suffix = "Instant" if instant else "Duration"
    ref = f"{role.period.value}{role.span.value}{suffix}"
    if role.members:
        ref = f"{ref}_{'_'.join(sorted(role.members))}"
    dimensions = {f"pdf:{_AXIS.get(member, member)}:{member}": member for member in role.members}
    return ParsedContext(
        context_ref=ref,
        period_type="instant" if instant else "duration",
        start_date=None if instant else start,
        end_date=end,
        is_consolidated=1 if consolidated else 0,
        relative_period=None,
        dimensions=dimensions,
    )


def unit_of(grid_note: str | None, texts: tuple[str, ...]) -> UnitKind:
    joined = "".join(_compact(text) for text in texts)
    if "円銭" in joined or "銭" in joined or "1株当たり" in joined:
        return UnitKind.PER_SHARE
    if "%" in joined or "％" in joined or "比率" in joined:
        return UnitKind.PERCENT
    if "百万円" in joined:
        return UnitKind.YEN_MILLIONS
    if "千円" in joined:
        return UnitKind.YEN_THOUSANDS
    note = _compact(grid_note or "")
    # 配当表は 円銭 と 百万円 が同じ注記に並ぶ。列ヘッダで決まらなかった金額列は百万円。
    if "百万円" in note:
        return UnitKind.YEN_MILLIONS
    if "千円" in note:
        return UnitKind.YEN_THOUSANDS
    if "円銭" in note:
        return UnitKind.PER_SHARE
    if "円" in note:
        return UnitKind.YEN
    raise PdfLayoutError("no unit")


def scaled(value: Decimal, unit: UnitKind) -> Decimal:
    return value * (Decimal(10) ** unit.value[1])


def _consolidation(text: str, d: PdfDisclosure) -> bool | None:
    """None はそのブロックを捨てる。修正通知だけ見出しごとに連結/個別を分ける。"""
    solo = "個別" in text or "単体" in text
    cons = "連結" in text
    if d.kind is DocKind.EARNINGS:
        if d.consolidated and solo and not cons:
            return None
        if not d.consolidated and cons and not solo:
            return None
        return d.consolidated
    if solo and not cons:
        return False
    if cons and not solo:
        return True
    return d.consolidated


def _blocks(pages: list[Page], d: PdfDisclosure) -> list[tuple[SectionSpec, list[Line], bool]]:
    specs = tuple(spec for spec in SECTIONS if d.kind in spec.kinds)
    lines = [line for page in pages for line in page.lines]
    hits: list[tuple[int, SectionSpec, str]] = []
    for index, line in enumerate(lines):
        compact = _compact(line.text)
        heading = _heading_key(line.text)
        for spec in specs:
            if spec.headings.search(heading) or spec.headings.search(compact):
                hits.append((index, spec, heading or compact))
                break
    blocks: list[tuple[SectionSpec, list[Line], bool]] = []
    for nth, (index, spec, text) in enumerate(hits):
        end = hits[nth + 1][0] if nth + 1 < len(hits) else len(lines)
        if spec.namespace is None:
            continue
        consolidated = _consolidation(text, d)
        if consolidated is None:
            continue
        if spec.name == "summary_solo" and d.consolidated:
            continue
        blocks.append((spec, _table_lines(lines[index + 1:end]), consolidated))
    return blocks


def _table_lines(lines: list[Line]) -> list[Line]:
    """ブロック内の分類見出しを落とす。折り返しの行名は残す。"""
    kept: list[Line] = []
    for line in lines:
        compact = _compact(line.text)
        if not compact:
            continue
        if _CLASS_HEAD.search(compact):
            continue
        if _RUNNING_HEAD.search(compact):
            continue
        heading = _heading_key(line.text)
        if any(
            spec.headings.search(heading) or spec.headings.search(compact)
            for spec in SECTIONS
        ):
            continue
        kept.append(line)
    return kept


def _split_grids(lines: list[Line]) -> list[list[Line]]:
    """データ行のあとの単位行は次の表。直前の科目見出しは次の表へ移す。"""
    chunks: list[list[Line]] = [[]]
    saw_numeric = False
    for line in lines:
        compact = _compact(line.text)
        is_unit = bool(_UNIT_ROW.search(compact))
        amounts = split_amounts(line)[1]
        if is_unit and not amounts and saw_numeric and chunks[-1]:
            prev = chunks[-1]
            moved: list[Line] = []
            while prev and not split_amounts(prev[-1])[1]:
                moved.append(prev.pop())
            moved.reverse()
            chunks.append(moved)
            saw_numeric = False
        chunks[-1].append(line)
        if amounts:
            saw_numeric = True
    return [chunk for chunk in chunks if chunk]


def sides(role: Role, amount) -> list[tuple[Role, Decimal]]:
    if amount.value is None:
        return []
    if amount.upper is None:
        return [(role, amount.value)]
    low = replace(role, members=frozenset(set(role.members) - {"UpperMember"} | {"LowerMember"}))
    high = replace(role, members=frozenset(set(role.members) - {"LowerMember"} | {"UpperMember"}))
    return [(low, amount.value), (high, amount.upper)]


def package(pages: list[Page], d: PdfDisclosure) -> ParsedPackage:
    contexts: dict[str, ParsedContext] = {}
    facts: list[ParsedFact] = []
    labels: dict[tuple[str, str], dict[str, str]] = {}
    seen: Counter[tuple[str, str, str, str]] = Counter()
    for spec, lines, consolidated in _blocks(pages, d):
        if spec.namespace is None or not lines:
            continue
        for chunk in _split_grids(lines):
            try:
                table = grid(chunk)
            except PdfLayoutError:
                continue
            for row in table.rows:
                for index, (column, amount) in enumerate(zip(table.columns, row.cells)):
                    if amount is None or amount.value is None:
                        continue
                    if spec.axis is Axis.ITEM_ROWS:
                        item_text = row.label
                        role_texts = column.headers
                        rightmost = index == len(table.columns) - 1
                    else:
                        if not column.headers:
                            continue
                        item_text, extra = _split_item_headers(column.headers)
                        role_texts = (row.label, *extra)
                        rightmost = False
                    role = role_of(role_texts, spec, d, rightmost=rightmost)
                    if role is None or not item_text.strip() or _is_unit_token(item_text):
                        continue
                    try:
                        unit = unit_of(table.unit_note, (item_text, *role_texts))
                    except PdfLayoutError:
                        continue
                    base_local = label_key(item_text)
                    if not base_local:
                        continue
                    if _NUMERIC_ITEM.search(base_local) and "株" not in base_local:
                        continue
                    if unit is UnitKind.PERCENT and "率" not in base_local:
                        continue
                    for side, raw in sides(role, amount):
                        value = scaled(raw, unit)
                        if (
                            spec.namespace is NS_PL
                            and d.standard == "IFRS"
                            and base_local in _IFRS_EXPENSE_ABS
                            and value < 0
                        ):
                            value = -value
                        ctx = context_for(side, spec, d, consolidated=consolidated)
                        contexts.setdefault(ctx.context_ref, ctx)
                        slot = (spec.namespace.key, base_local, ctx.context_ref, unit.value[0])
                        seen[slot] += 1
                        local = base_local if seen[slot] == 1 else f"{base_local}#{seen[slot]}"
                        facts.append(ParsedFact(
                            namespace_uri=spec.namespace.uri,
                            namespace_key=spec.namespace.key,
                            local_name=local,
                            context_ref=ctx.context_ref,
                            unit=unit.value[0],
                            scale=unit.value[1],
                            value_num=value,
                            value_text=None,
                            is_nil=False,
                            is_numeric=True,
                        ))
                        labels.setdefault((spec.namespace.key, local), {"label_ja": item_text})
    if not facts:
        raise PdfLayoutError("no in-scope table")
    assign_relative_periods(list(contexts.values()), d.anchor_end)
    return ParsedPackage(
        contexts=list(contexts.values()),
        facts=facts,
        labels=labels,
        ixbrl_files=[],
        fiscal_year_end=d.anchor_end,
    )

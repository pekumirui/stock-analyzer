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
# 年は西暦 4 桁か、1〜2 桁（「令和8年」「令和」を省いた「8年6月期」、西暦の下 2 桁「22年12月期」）。
# 1 つ目のグループが「令和」。_year で西暦にする。
_DATE = re.compile(r"(令和)?(\d{4}|\d{1,2})年(\d{1,2})月(\d{1,2})日")
_YEAR_MONTH = re.compile(r"(令和)?(\d{4}|\d{1,2})年(\d{1,2})月期")
# 本表の列見出しの「2026年6月末」。日が無い。
_YEAR_MONTH_END = re.compile(r"(令和)?(\d{4}|\d{1,2})年(\d{1,2})月末")
_REIWA_BASE = 2018


def _year(reiwa: str, raw: str, d: PdfDisclosure | None = None) -> int:
    """4 桁は西暦、「令和」付きは令和。「令和」の無い 1〜2 桁は、令和の省略（8年6月期 = 2026）と
    西暦の下 2 桁（6268「22年12月期」= 2022）のうち、書類の年度末に近い方（KAB-228）。"""
    value = int(raw)
    if value >= 1000:
        return value
    if reiwa or d is None:
        return _REIWA_BASE + value
    anchor = d.fiscal_year_end.year
    return min((_REIWA_BASE + value, 2000 + value), key=lambda year: abs(year - anchor))
_DIVIDEND_SLOTS = frozenset({
    "FirstQuarterMember", "SecondQuarterMember", "ThirdQuarterMember", "YearEndMember", "AnnualMember",
})
_LEADING = re.compile(r"^(?:[①-⑳]|※\d*|\(注\d*\)|\(\d+\)|\d+\.)+")
_HEADING_LEAD = re.compile(r"^(?:[①-⑳]|\(\d+\)|\d+\.)+")
_HINTS = re.compile(r"\([△▲][^)]*\)")
# 本表の途中に出る分類見出し。grid が次の行名へ連結しない。
_CLASS_HEAD = re.compile(r"の部$")
# 続きページの柱（銘柄コードと題名）。数値の無い行として次の科目名に連結しない。
_RUNNING_HEAD = re.compile(r"\(\d{4}\).*決算短信")
_UNIT_ROW = re.compile(r"単位|百万円|千円|億円|兆円|円銭|百万円未満")
_UNIT_TOKENS = frozenset({"百万円", "千円", "億円", "兆円", "円", "円銭", "銭", "%", "％"})
# 配当の親見出し末尾の単位括弧（KAB-245。「年間配当金（円）」）。百万円・% は外さない。
_DIV_YEN_PAREN = re.compile(r"^(.+?)[\(（](円銭|円)[\)）]$")
_NUMERIC_ITEM = re.compile(r"\d")
# 1 ページ目の列見出しのうち、科目名ではなく列の役割・単位。
_HEADER_ROLE = re.compile(
    r"増減率|対前期|前回発表予想|今回修正予想|今回発表予想|前回予想|今回予想"
    r"|[\(（][ABＡＢ][\)）]"
    r"|第[123]四半期末|中間期末|^期末$|^(年間)?合計$|^年間$|下限|上限"
    r"|第[123]四半期|中間期|上期|通期"
)
# 数値の無い CF の区分見出し（KAB-216）。金額がある合計行は残す。
_CF_CLASS = re.compile(r"^(営業|投資|財務)活動によるキャッシュ・フロー$")
# 数値の無い BS の区分見出し。hanging が「資産流動資産現金及び預金」にしない。
_BS_CLASS = re.compile(
    r"^(資産|負債|純資産|資本|流動資産|固定資産|非流動資産|流動負債|固定負債|非流動負債|株主資本)$"
)
# 訂正短信の「（訂正前）」ブロック（KAB-212）。drop_pre_correction はこれだけ。
_CORR_BEFORE = re.compile(r"^[【\[(（]*訂正前[)）】\]]*$")
_CORR_AFTER = re.compile(r"^[【\[(（]*訂正後[)）】\]]*$")
# 題名に「訂正」が無い訂正短信（KAB-226 の「…決算短信…の修正について」、KAB-235 の変更前/後・訂正版）。
_AMENDMENT_TITLE = re.compile(r"決算短信.*(?:の修正について|の一部修正)")
_CORR_NOTICE = re.compile(
    r"^[【\[(（<＜]*((訂正|変更)(前|後)|訂正版)[)）】\]>＞]*$"
)
# IFRS 本表は費用を △ で印字する。ZIP の CostOfSales は正なので符号を合わせる。合計の行も同じ。
_IFRS_EXPENSE_ABS = re.compile(r"^(売上原価|原価|販売費及び一般管理費)(合計)?$")
# 本表（BS/PL/CF）。決算短信ごとに最初の 1 つだけ読む（セグメント別・子会社・単体の表を読まない）。
_STATEMENTS = frozenset({"bs", "pl", "cf"})
# サマリーは起点から 2 ページ。通常は 0。訂正理由書のあとに全文があるときは、最初の
# サマリー見出しのページ（KAB-226 / KAB-235）。本文の「(2)財政状態に関する説明」を拾わない。
_SUMMARY_PAGES = 2
# 本表として採るのに要る数値の行（1 行以上）。見出しが 2 段（「(2)連結損益計算書及び…」「(連結損益計算書)」）のとき、
# 上の見出しの下は空か単位の行だけ。
_MIN_STATEMENT_ROWS = 1
# 本表の後ろの注記の見出し（「(継続企業の前提に関する注記)」「(セグメント情報等)」「３．その他」）。
_NOTE_HEADING = re.compile(r"^([(（【].*(注記|情報|事項)(等)?[)）】]$|\d\.その他|\(\d\).*注記)")
# 目次の点線。本表・サマリーの行ではない。
_LEADER = re.compile(r"[…‥]{2,}|\.{4,}|・{4,}|･{4,}|-{4,}")
# 3 か月だけの列（第2〜第4四半期の会計期間）。累計の列と同じ期と取り違える。
_QUARTER_ONLY = re.compile(r"第[2-4]四半期(連結)?会計期間(?!末)")
# 行名の言い換え。四半期・中間の利益は当期の名前に寄せる（対応表は当期の名前だけ持つ）。
_PERIOD_PROFIT = re.compile(r"(四半期|中間)(純利益|純損失|利益|損失)")
_OWNERS_PROFIT = re.compile(r"^(当期)?純?利益の帰属:?(親会社の所有者|当社の所有者|当社株主|親会社株主)(持分)?$")
_ALTERNATIVE = re.compile(r"又は.*$")
_HEADING_NOTE = re.compile(r"[\[【〔(].*$")
# 行名の言い換え（CF の「に関する」、期末残高の「四半期末」「中間期末」）。
_SYNONYMS = (
    (re.compile(r"活動に関するキャッシュ・フロー"), "活動によるキャッシュ・フロー"),
    (re.compile(r"^(営業|投資|財務)活動(から得た|に使用した|から得た又は使用した|による)(正味)?現金(及び現金同等物)?(\(純額\))?$"),
     r"\1活動によるキャッシュ・フロー"),
    (re.compile(r"(四半期末|中間期末|中間会計期間末|四半期会計期間末)残高$"), "期末残高"),
    (re.compile(r"^(基本的|希薄化後)1株当たり(親会社の所有者|当社の所有者|親会社株主|当社株主)に帰属する当期(純)?利益$"),
     r"\g<1>1株当たり当期利益"),
)
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
    SectionSpec("summary_pl", _h(r"^\(\d\)(連結|個別)?経営成績(?!に関する)"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _E, _RESULT),
    SectionSpec("summary_bs", _h(r"^\(\d\)(連結|個別)?財政状態(?!計算書|に関する)"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.INSTANT, _E, _RESULT),
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
        _h(r"^" + _STMT + r"(貸借対照表|財政状態計算書)$"),
        NS_BS, Axis.ITEM_ROWS, Shape.INSTANT, _E, frozenset(),
    ),
    SectionSpec(
        "pl",
        _h(r"^" + _STMT + r"(損益計算書|損益及び包括利益計算書)(及び" + _STMT + r"包括利益計算書)?$"),
        NS_PL, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset(),
    ),
    SectionSpec(
        "cf",
        _h(r"^" + _STMT + r"キャッシュ・フロー計算書$"),
        NS_CF, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset(),
    ),
    SectionSpec("ci", _h(r"^" + _STMT + r"包括利益計算書$"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("equity", _h(r"株主資本等変動計算書$|持分変動計算書$"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("segment", _h(r"セグメント情報"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
    SectionSpec("fc_rev", _h(r"業績予想.*修正"), NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DURATION, _FR, frozenset()),
    # 「配当予想の修正」のほか、予定配当額・中間/期末配当（予定）の通知（KAB-237）。短信の「配当の状況」は当たない。
    SectionSpec(
        "div_rev",
        _h(r"配当予想.*修正|予定配当額について|(中間|期末)配当(\(予定\)|予想)?(額)?(の修正)?について"),
        NS_SUMMARY, Axis.ITEM_COLUMNS, Shape.DIVIDEND, _DR, frozenset(),
    ),
    # 上のどれでもない計算書・対照表（セグメント別の損益計算書、子会社の貸借対照表など）。読まないが、
    # 本表のブロックをここで切る。
    SectionSpec("other_statement", _h(r"^.{0,40}(計算書|対照表)$"), None, Axis.ITEM_ROWS, Shape.DURATION, _E, frozenset()),
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
    # 本表の比率の列（売上収益比・前年同期比・構成比）。
    (_h(r"[%％]$|^[%％]|収益比|売上比|同期比|構成比|前年比"), {"skip_rows": True}),
    (_h(r"前回発表予想|前回予想|前回予定|\(A\)"), {"add": frozenset({"ForecastMember", "PreviousMember"})}),
    (_h(r"今回修正予想|今回発表予想|今回予想|今回予定|今回決定|\(B\)"), {"add": frozenset({"ForecastMember", "CurrentMember"})}),
    (_h(r"第1四半期末"), {"add": frozenset({"FirstQuarterMember"})}),
    (_h(r"第2四半期末|中間期末"), {"add": frozenset({"SecondQuarterMember"})}),
    (_h(r"第3四半期末"), {"add": frozenset({"ThirdQuarterMember"})}),
    (_h(r"^期末$"), {"add": frozenset({"YearEndMember"})}),
    # 配当の修正は「年間」だけの列もある（KAB-223 9983「第２四半期末 / 期末 / 年間」）。
    (_h(r"^(年間)?合計$|^年間$"), {"add": frozenset({"AnnualMember"})}),
    (_h(r"下限"), {"add": frozenset({"LowerMember"})}),
    (_h(r"上限"), {"add": frozenset({"UpperMember"})}),
    (_h(r"第2四半期(?!末)|中間期|上期"), {"span": Span.ACC_Q2}),
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
    YEN_HUNDRED_MILLIONS = ("JPY", 8)
    YEN_TRILLIONS = ("JPY", 12)
    PER_SHARE = ("JPYPerShares", 0)
    PERCENT = ("pure", -2)


def _compact(raw: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw or ""))


def drop_pre_correction(pages: list[Page]) -> list[Page]:
    """「（訂正前）」から「（訂正後）」の手前までを捨てる（KAB-212）。"""
    dropping = False
    out: list[Page] = []
    for page in pages:
        kept: list[Line] = []
        for line in page.lines:
            compact = _compact(line.text)
            if _CORR_BEFORE.fullmatch(compact):
                dropping = True
                continue
            if _CORR_AFTER.fullmatch(compact):
                dropping = False
                continue
            if not dropping:
                kept.append(line)
        out.append(Page(page.index, tuple(kept)))
    return out


def is_amendment_notice(title: str | None, pages: list[Page]) -> bool:
    """訂正短信か。業績予想・配当の「修正について」だけでは True にしない。"""
    compact_title = _compact(title or "")
    if "訂正" in compact_title or _AMENDMENT_TITLE.search(compact_title):
        return True
    for page in pages:
        for line in page.lines:
            if _CORR_NOTICE.fullmatch(_compact(line.text)):
                return True
    return False


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
    # 「営業利益又は営業損失(△)」は前半の名前。符号は印字の △ のまま。
    head = _ALTERNATIVE.sub("", text)
    if head:
        text = head
    text = _PERIOD_PROFIT.sub(r"当期\2", text)
    for pattern, repl in _SYNONYMS:
        text = pattern.sub(repl, text)
    if _OWNERS_PROFIT.fullmatch(text):
        text = "親会社の所有者に帰属する当期利益"
    text = re.sub(r"[\(（][ABＡＢ][\)）]$", "", text)
    return text


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, _month_days(year, month)))


def _shift_years(day: date, years: int) -> date:
    """年をずらす。月末は月末のまま（6506 の 2023-02-28 の翌期末は 2024-02-29。KAB-228）。

    app.xbrl.context.shift_years は EDINET も使うので変えない（2/29 → 2/28 に落とすだけ）。
    """
    year = day.year + years
    if day.day == _month_days(day.year, day.month):
        return date(year, day.month, _month_days(year, day.month))
    return shift_years(day, years)


def _month_days(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _dates_in(texts: tuple[str, ...], d: PdfDisclosure | None = None) -> list[date]:
    found: list[date] = []
    for text in texts:
        for reiwa, year, month, day in _DATE.findall(text):
            try:
                found.append(date(_year(reiwa, year, d), int(month), int(day)))
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
    if _near(end, _shift_years(d.period_end, -1)):
        return PeriodRef.PRIOR, reported
    if _near(end, d.fiscal_year_end):
        return PeriodRef.CURRENT, Span.YEAR
    if _near(end, _shift_years(d.fiscal_year_end, -1)):
        return PeriodRef.PRIOR, Span.YEAR
    if _near(end, _shift_years(d.fiscal_year_end, 1)):
        return PeriodRef.NEXT, Span.YEAR
    return None


def _period_from_year_month(
    year: int, month: int, d: PdfDisclosure, spec: SectionSpec,
) -> tuple[PeriodRef, Span] | None:
    """YYYY年M月期 は日が印字されない。20 日決算でも年月が合えば当期/前期。"""
    reported = _span_for(d, spec)
    for end, period, span in (
        (d.period_end, PeriodRef.CURRENT, reported),
        (_shift_years(d.period_end, -1), PeriodRef.PRIOR, reported),
        (d.fiscal_year_end, PeriodRef.CURRENT, Span.YEAR),
        (_shift_years(d.fiscal_year_end, -1), PeriodRef.PRIOR, Span.YEAR),
        (_shift_years(d.fiscal_year_end, 1), PeriodRef.NEXT, Span.YEAR),
    ):
        if end.year == year and end.month == month:
            return period, span
    return None


def _period_from_range_end(end: date, d: PdfDisclosure) -> tuple[PeriodRef, Span] | None:
    """期首から何か月で終わるかで期を決める（「2021年9月1日～2022年2月28日」は第2四半期累計）。

    書類の期末・年度末に当たらない日（通期の書類の上期の表、20 日決算）にも使う。
    """
    for period, shift in ((PeriodRef.CURRENT, 0), (PeriodRef.PRIOR, -1), (PeriodRef.NEXT, 1)):
        start = _shift_years(d.fiscal_year_start, shift)
        for months, span in ((3, Span.ACC_Q1), (6, Span.ACC_Q2), (9, Span.ACC_Q3), (12, Span.YEAR)):
            if _near(end, _add_months(start, months) - timedelta(days=1)):
                return period, span
    return None


@dataclass(frozen=True)
class PeriodHint:
    """表の見出し・書類の題名から読んだ期。列や行に期の語が無い予想に使う（KAB-228）。"""

    period: PeriodRef | None = None
    span: Span | None = None
    # 「第２四半期累計期間の業績予想及び通期業績予想の修正」のように、累計と通期の両方がある
    ambiguous: bool = False

    @property
    def empty(self) -> bool:
        return self.span is None and not self.ambiguous


def heading_hint(text: str, d: PdfDisclosure, spec: SectionSpec) -> PeriodHint:
    """見出しの期間（日付の範囲が先、無ければ「第2四半期（累計）」「通期」の語）。"""
    norm = _compact(text)
    dates = _dates_in((norm,), d)
    if dates:
        found = _period_from_date(dates[-1], d, spec) or _period_from_range_end(dates[-1], d)
        if found is not None:
            return PeriodHint(*found)
    spans = {patch["span"] for pattern, patch in ROLE_WORDS if "span" in patch and pattern.search(norm)}
    if len(spans) == 1:
        return PeriodHint(span=spans.pop())
    return PeriodHint(ambiguous=len(spans) > 1)


def _heading_key(raw: str) -> str:
    """TOC の点線は残す。番号と囲みだけ落として見出し正規表現に渡す。"""
    # ① は NFKC で 1 になり \d+\. に当たらない（KAB-234）。正規化の前に落とす。
    text = _HEADING_LEAD.sub("", raw or "")
    text = _compact(text)
    text = _HEADING_LEAD.sub("", text)
    if len(text) >= 2 and text[0] in "(（【〔[" and text[-1] in ")）】〕]":
        text = text[1:-1]
    # 「要約四半期連結損益計算書[第1四半期連結累計期間(…3ヵ月間)]」の後ろの補足。
    trimmed = _HEADING_NOTE.sub("", text)
    if trimmed != text and re.search(r"(計算書|対照表)$", trimmed):
        text = trimmed
    return text


def _is_unit_token(raw: str) -> bool:
    key = _compact(raw)
    if key in _UNIT_TOKENS:
        return True
    return "単位" in key and len(key) <= 12


def _strip_dividend_yen_paren(item_text: str, role_texts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """配当の親見出し末尾の「（円）」「（円銭）」を外し、単位判定用に元の語を残す（KAB-245）。"""
    match = _DIV_YEN_PAREN.fullmatch(_compact(item_text))
    if match is None:
        return item_text, role_texts
    if item_text not in role_texts:
        role_texts = (*role_texts, item_text)
    return match.group(1), role_texts


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
    hint: PeriodHint | None = None,
) -> Role | None:
    norm = tuple(_compact(text) for text in texts if text and _compact(text))
    for text in norm:
        for pattern, patch in ROLE_WORDS:
            if patch.get("skip") and pattern.search(text):
                return None
            if patch.get("skip_rows") and spec.axis is Axis.ITEM_ROWS and pattern.search(text):
                return None
    if spec.axis is Axis.ITEM_ROWS and spec.shape is Shape.DURATION and any(_QUARTER_ONLY.search(t) for t in norm):
        return None
    members: set[str] = set(spec.base_members)
    period: PeriodRef | None = None
    span: Span | None = None
    evidence = False
    for text in norm:
        for pattern, patch in ROLE_WORDS:
            if patch.get("skip") or patch.get("skip_rows") or not pattern.search(text):
                continue
            evidence = True
            if "period" in patch:
                period = patch["period"]
            if "span" in patch:
                span = patch["span"]
            if "add" in patch:
                added = set(patch["add"])
                if spec.shape is not Shape.DIVIDEND:
                    # 「第1四半期末」は配当表でだけ配当の時点。本表・サマリーでは期末の言い換え。
                    added -= _DIVIDEND_SLOTS
                members |= added
    if "ResultMember" in spec.base_members and any("予想" in text for text in norm):
        members.discard("ResultMember")
        members.add("ForecastMember")
    # 日付（「至 2026年6月30日」）があれば日付で決める。「2027年3月期 第1四半期」の年月より確か。
    # 列見出しは「2026年」「6月末」「(2026年6月30日)」のように語が分かれる。つないでも読む。
    joined = ("".join(norm),)
    dated = None
    by_month = False
    if _dates_in(norm, d):
        dated = _period_from_date(_dates_in(norm, d)[-1], d, spec)
    if dated is None and _dates_in(joined, d):
        dated = _period_from_date(_dates_in(joined, d)[-1], d, spec)
    if dated is None:
        for text in (*reversed(norm), joined[0]):
            found = _YEAR_MONTH.findall(text) or _YEAR_MONTH_END.findall(text)
            if found:
                reiwa, year, month = found[-1]
                dated = _period_from_year_month(_year(reiwa, year, d), int(month), d, spec)
                if dated is not None:
                    by_month = True
                    break
    if (
        spec.axis is Axis.ITEM_COLUMNS
        and "ForecastMember" not in members
        and dated is None
        and not evidence
        and texts and _compact(texts[0])
    ):
        # サマリーの実績の行名に期が無い（「(金融を除く)」等の内訳）。当期と決めつけると当期の行と重なる。
        # 予想の表（たいてい 1 行）は今までどおり既定の期（FY の短信は来期、四半期は当期）。
        return None
    if dated is not None:
        if by_month and span is not None:
            # 「2027年3月期 第1四半期」は年度の年月。期間の長さは語（第1四半期）で決まる。
            period = dated[0]
        else:
            period, span = dated
    if "ForecastMember" in members:
        if span is None and hint is not None and not hint.empty:
            # 予想の修正の表は列にも行にも期が無い。表の見出し（無ければ題名）の期を使う。
            # 累計と通期の両方が書いてあって決まらないときは入れない（通期と決めつけて上書きしない）。
            if hint.ambiguous:
                return None
            span = hint.span
            if period is None:
                period = hint.period
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
            end = _shift_years(end, -1)
        elif role.period is PeriodRef.NEXT:
            end = _shift_years(end, 1)
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
        return _shift_years(start, -1), _shift_years(end, -1)
    if role.period is PeriodRef.NEXT:
        return _shift_years(start, 1), _shift_years(end, 1)
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
    if not consolidated:
        # context は ref で使い回す。連結の短信の単体の値（配当）が連結の本表の context を奪わない。
        ref = f"{ref}_NonConsolidated"
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
    # 億円・兆円は「円」を含む。円より先に見る（8630 20231206599881）。
    if "兆円" in joined:
        return UnitKind.YEN_TRILLIONS
    if "億円" in joined:
        return UnitKind.YEN_HUNDRED_MILLIONS
    if "百万円" in joined:
        return UnitKind.YEN_MILLIONS
    if "千円" in joined:
        return UnitKind.YEN_THOUSANDS
    # 配当列の「年間配当金（円）」。NFKC 後は "(円)"。括弧付きの円は 1 株当たり（KAB-245）。
    # 注記の裸の「円」は下の YEN（金額）。百万円・千円の列は上で先に決まる。
    if "(円)" in joined:
        return UnitKind.PER_SHARE
    note = _compact(grid_note or "")
    # 配当表は 円銭 と 百万円 が同じ注記に並ぶ。列ヘッダで決まらなかった金額列は百万円。
    if "兆円" in note:
        return UnitKind.YEN_TRILLIONS
    if "億円" in note:
        return UnitKind.YEN_HUNDRED_MILLIONS
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


def _consolidation(text: str, d: PdfDisclosure, spec: SectionSpec | None = None) -> bool | None:
    """None はそのブロックを捨てる。修正通知だけ見出しごとに連結/個別を分ける。

    連結の短信のサマリーにある個別の表（「(2)個別財政状態」）は単体の値として読む。ZIP も単体で持つ。
    見出しに「個別」「単体」も「連結」も無い修正は ``d.consolidated``（書類種別の既定、または近い
    通常の短信から引き継いだ値。KAB-247）。本文が「非連結の短信」を引用していても、表の見出しでは
    ないので見ない。
    """
    solo = "個別" in text or "単体" in text
    cons = "連結" in text
    if d.kind is DocKind.EARNINGS:
        if d.consolidated and solo and not cons:
            if spec is not None and spec.axis is Axis.ITEM_COLUMNS:
                return False
            return None
        if not d.consolidated and cons and not solo:
            return None
        return d.consolidated
    if solo and not cons:
        return False
    if cons and not solo:
        return True
    return d.consolidated


def _summary_origin(pages: list[Page], specs: tuple[SectionSpec, ...]) -> int:
    """サマリーを数える起点。通常は 0。訂正理由書のあとの全文は、サマリー見出しが最初に出るページ。"""
    summary = tuple(spec for spec in specs if spec.name.startswith("summary_"))
    if not summary:
        return 0
    for page in pages:
        for line in page.lines:
            compact = _compact(line.text)
            if _LEADER.search(compact):
                continue
            heading = _heading_key(line.text)
            if any(spec.headings.search(heading) or spec.headings.search(compact) for spec in summary):
                return page.index
    return 0


def _blocks(pages: list[Page], d: PdfDisclosure) -> list[tuple[SectionSpec, list[Line], bool, str]]:
    specs = tuple(spec for spec in SECTIONS if d.kind in spec.kinds)
    origin = _summary_origin(pages, specs)
    lines = [line for page in pages for line in page.lines]
    page_of = [page.index for page in pages for _line in page.lines]
    hits: list[tuple[int, SectionSpec, str]] = []
    for index, line in enumerate(lines):
        compact = _compact(line.text)
        if _LEADER.search(compact):
            continue
        heading = _heading_key(line.text)
        for spec in specs:
            if spec.headings.search(heading) or spec.headings.search(compact):
                if spec.name.startswith("summary_") and page_of[index] - origin >= _SUMMARY_PAGES:
                    break
                hits.append((index, spec, heading or compact))
                break
    candidates: list[tuple[SectionSpec, list[Line], bool, str]] = []
    for nth, (index, spec, text) in enumerate(hits):
        end = hits[nth + 1][0] if nth + 1 < len(hits) else len(lines)
        if spec.axis is Axis.ITEM_COLUMNS:
            # サマリーの表はページをまたがない。次のページの目次や注記を表に入れない。
            while end > index + 1 and page_of[end - 1] != page_of[index]:
                end -= 1
        if spec.namespace is None:
            continue
        consolidated = _consolidation(text, d, spec)
        if consolidated is None:
            continue
        if spec.name == "summary_solo":
            # 連結の短信の「(参考) 個別業績」は単体の値。ZIP も単体として持つ。
            consolidated = False
        body = lines[index + 1:end]
        if spec.name in _STATEMENTS:
            body = _statement_body(body)
        candidates.append((spec, _table_lines(body), consolidated, text))
    blocks: list[tuple[SectionSpec, list[Line], bool, str]] = []
    taken: set[str] = set()
    for spec, body, consolidated, text in candidates:
        if spec.name in _STATEMENTS:
            if spec.name in taken or sum(1 for line in body if split_amounts(line)[1]) < _MIN_STATEMENT_ROWS:
                continue
            if d.kind is DocKind.EARNINGS and not _statement_matches(text, spec, d, candidates):
                continue
            taken.add(spec.name)
        blocks.append((spec, body, consolidated, text))
    return blocks


def _statement_body(lines: list[Line]) -> list[Line]:
    """本表は表の終わりで切る。後ろの注記（「(継続企業の前提に関する注記)」、文章）の表を本表にしない。"""
    seen_numeric = False
    for index, line in enumerate(lines):
        compact = _compact(line.text)
        if split_amounts(line)[1]:
            seen_numeric = True
            continue
        if seen_numeric and (_NOTE_HEADING.match(compact) or "。" in compact):
            return lines[:index]
    return lines


def _statement_matches(text: str, spec: SectionSpec, d: PdfDisclosure, candidates) -> bool:
    """連結の短信は題名に「連結」のある本表、非連結は無い本表。連結の短信で「連結」の本表が無ければ無いものも読む。"""
    has_cons = "連結" in text
    if not d.consolidated:
        return not has_cons
    if has_cons:
        return True
    return not any(
        other.name == spec.name and body and "連結" in other_text
        for other, body, _cons, other_text in candidates
    )


def _table_lines(lines: list[Line]) -> list[Line]:
    """ブロック内の分類見出しを落とす。折り返しの行名は残す。"""
    kept: list[Line] = []
    for line in lines:
        compact = _compact(line.text)
        if not compact:
            continue
        if _CLASS_HEAD.search(compact):
            continue
        if _CF_CLASS.fullmatch(compact) and not split_amounts(line)[1]:
            continue
        if _BS_CLASS.fullmatch(compact) and not split_amounts(line)[1]:
            continue
        if _RUNNING_HEAD.search(compact):
            continue
        if _LEADER.search(compact):
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


def _statement_roles(table, spec: SectionSpec, d: PdfDisclosure) -> list[Role | None]:
    """本表の列ごとの役割。増減・3 か月・予想の列は None。同じ役割が 2 列に付けば、見出しで決まった方だけ残す。

    見出しで決まらない列は位置で決める（右端が当期、ほかは前期）。見出しで決まった列どうしが重なれば、
    どちらが正しいか分からないので両方捨てる（間違った値を入れるより空の方がよい）。
    """
    count = len(table.columns)
    roles: list[Role | None] = []
    explicit: list[bool] = []
    filled = [
        any(row.cells[index] is not None and row.cells[index].value is not None for row in table.rows)
        for index in range(count)
    ]
    # 「右端」はダッシュだけの列を数えない。
    last = max((index for index in range(count) if filled[index]), default=count - 1)
    for index, column in enumerate(table.columns):
        if not filled[index]:
            # ダッシュだけの列。役割を付けると値のある列と重なって両方捨てることになる。
            roles.append(None)
            explicit.append(False)
            continue
        right = role_of(column.headers, spec, d, rightmost=True)
        left = role_of(column.headers, spec, d, rightmost=False)
        role = right if index == last else left
        if role is not None and "ForecastMember" in role.members:
            role = None
        roles.append(role)
        explicit.append(right is not None and left is not None and right.period is left.period)
    by_key: dict[tuple, list[int]] = {}
    for index, role in enumerate(roles):
        if role is not None:
            by_key.setdefault((role.period, role.span, role.members), []).append(index)
    for indexes in by_key.values():
        if len(indexes) < 2:
            continue
        sure = [index for index in indexes if explicit[index]]
        for index in indexes:
            # 見出しで決まった列が 1 つならそれを残す。2 つ以上ならどちらも捨てる（ほかの列は残す）。
            if len(sure) != 1 or index != sure[0]:
                roles[index] = None
    return roles


def _without_conflicts(pending: list[tuple[ParsedFact, str]]) -> list[tuple[ParsedFact, str]]:
    """同じ (名前空間, 科目, context, 単位) に違う値があれば全部落とす。同じ値の重複は 1 つにする。"""
    values: dict[tuple, set[Decimal]] = {}
    for fact, _label in pending:
        key = (fact.namespace_key, fact.local_name, fact.context_ref, fact.unit)
        values.setdefault(key, set()).add(fact.value_num)
    kept: list[tuple[ParsedFact, str]] = []
    done: set[tuple] = set()
    for fact, label in pending:
        key = (fact.namespace_key, fact.local_name, fact.context_ref, fact.unit)
        if len(values[key]) > 1 or key in done:
            continue
        done.add(key)
        kept.append((fact, label))
    return kept


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

    def add(fact: ParsedFact, label: str) -> None:
        # 同じ科目・同じ期が 2 度出たら 2 つ目以降は #2, #3。マッピングは名前の完全一致なので 1 つ目が使われる。
        slot = (fact.namespace_key, fact.local_name, fact.context_ref, fact.unit)
        seen[slot] += 1
        if seen[slot] > 1:
            fact = replace(fact, local_name=f"{fact.local_name}#{seen[slot]}")
        facts.append(fact)
        labels.setdefault((fact.namespace_key, fact.local_name), {"label_ja": label})

    blocks = _blocks(pages, d)
    # 業績予想の修正は表ごとに見出しの期（「(1)第2四半期（累計）…（9月1日～2月28日）」）を使う。
    # 見出しに期が無ければ題名（最初の見出し）。どちらにも無ければ今までどおり通期（KAB-228）。
    title_hint = next(
        (heading_hint(text, d, spec) for spec, _lines, _cons, text in blocks if spec.name == "fc_rev"),
        PeriodHint(),
    )
    for spec, lines, consolidated, heading in blocks:
        if spec.namespace is None or not lines:
            continue
        hint = None
        if spec.name == "fc_rev":
            hint = heading_hint(heading, d, spec)
            if hint.empty:
                hint = title_hint
        for chunk in _split_grids(lines):
            try:
                table = grid(chunk)
            except PdfLayoutError:
                continue
            # サマリーの表は表ごとにためる。同じ科目・同じ期に違う値が 2 つ出たら、どの行が正しいか
            # 分からない（期の読み違い）ので、どちらも入れない。
            pending: list[tuple[ParsedFact, str]] = []
            column_roles = _statement_roles(table, spec, d) if spec.axis is Axis.ITEM_ROWS else None
            for row in table.rows:
                for index, (column, amount) in enumerate(zip(table.columns, row.cells)):
                    if amount is None or amount.value is None:
                        continue
                    slot_only = False
                    if column_roles is not None:
                        item_text = row.label
                        role_texts = column.headers
                        role = column_roles[index]
                    else:
                        if not column.headers:
                            continue
                        item_text, extra = _split_item_headers(column.headers)
                        role_texts = (row.label, *extra)
                        if spec.shape is Shape.DIVIDEND:
                            item_text, role_texts = _strip_dividend_yen_paren(item_text, role_texts)
                        if spec.shape is Shape.DIVIDEND and _HEADER_ROLE.search(_compact(item_text)):
                            # 配当表の列見出しが「第１四半期末」等の時点だけなら、科目は 1 株当たり配当金。
                            # 配当金総額・配当性向の列は見出しの科目名のまま。
                            # 「第 / 2 / 四半期末」と語が分かれると、つないだ時点は役割に残っていない（8511）。
                            if item_text not in extra:
                                role_texts = (*role_texts, item_text)
                            item_text = "年間配当金"
                            slot_only = True
                        role = role_of(role_texts, spec, d, rightmost=False, hint=hint)
                    if role is None or not item_text.strip() or _is_unit_token(item_text):
                        continue
                    try:
                        unit = unit_of(table.unit_note, (item_text, *role_texts))
                    except PdfLayoutError:
                        if not slot_only:
                            continue
                        # 単位の注記も「円銭」も無い配当の修正（KAB-223 9983）。時点だけの列は 1 株当たり。
                        # 「１株当たり配当金」は上の 1 列にしか載らないので、ほかの時点列はここで決める。
                        unit = UnitKind.PER_SHARE
                    base_local = label_key(item_text)
                    if spec.namespace is NS_SUMMARY:
                        base_local = re.sub(r"^(連結|個別)", "", base_local)
                    if not base_local:
                        continue
                    if spec.name in {"fc_rev", "div_rev"} and "ForecastMember" not in role.members:
                        # 前期実績を今期末の実績として入れない。予想の行だけ残す。
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
                            and _IFRS_EXPENSE_ABS.fullmatch(base_local)
                            and value < 0
                        ):
                            value = -value
                        # 配当は ZIP と同じく単体（連結の軸が無い）として持つ。
                        ctx = context_for(
                            side, spec, d,
                            consolidated=consolidated and spec.shape is not Shape.DIVIDEND,
                        )
                        contexts.setdefault(ctx.context_ref, ctx)
                        fact = ParsedFact(
                            namespace_uri=spec.namespace.uri,
                            namespace_key=spec.namespace.key,
                            local_name=base_local,
                            context_ref=ctx.context_ref,
                            unit=unit.value[0],
                            scale=unit.value[1],
                            value_num=value,
                            value_text=None,
                            is_nil=False,
                            is_numeric=True,
                        )
                        if spec.axis is Axis.ITEM_COLUMNS:
                            pending.append((fact, item_text))
                        else:
                            add(fact, item_text)
            for fact, label in _without_conflicts(pending):
                add(fact, label)
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

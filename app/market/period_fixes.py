"""J-Quants の決算で期の日付がずれている開示の補正・取り込まない開示の一覧と、新しい候補の WARNING。

補正・除外するのは config/market/statement_period_fixes.json に載せた開示だけ（規則で自動に直さない）。
"""
import calendar
import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..config import STATEMENT_PERIOD_FIXES_PATH
from ..models import Statement

PERIOD_COLUMNS = ("period_start", "period_end", "fiscal_year_start", "fiscal_year_end")
LATE_DAYS = 300
QUARTERS = ("1Q", "2Q", "3Q")
YEAR_MONTHS = 12


VALUE_COLUMNS = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")


@dataclass(frozen=True)
class StatementFixes:
    periods: dict[str, date]
    dropped: frozenset[str]
    values: dict[str, dict[str, float]]
    months: dict[str, int]
    dates: dict[str, dict[str, date]]


def load_statement_fixes(path: Path | None = None) -> StatementFixes:
    src = STATEMENT_PERIOD_FIXES_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    periods: dict[str, date] = {}
    dropped: set[str] = set()
    values: dict[str, dict[str, float]] = {}
    months: dict[str, int] = {}
    dates: dict[str, dict[str, date]] = {}
    for item in raw.get("fixes") or []:
        no = str(item["disclosure_no"]).strip()
        if no in periods or no in dropped or no in values:
            raise ValueError(f"duplicate disclosure_no in period fixes: {no}")
        if item.get("drop") is True:
            dropped.add(no)
        elif "values" in item:
            unknown = set(item["values"]) - set(VALUE_COLUMNS)
            if unknown or not item["values"]:
                raise ValueError(f"bad values in period fixes: {no} {sorted(unknown)}")
            values[no] = {k: float(v) for k, v in item["values"].items()}
        else:
            periods[no] = date.fromisoformat(item["period_end"])
            if "dates" in item:
                unknown = set(item["dates"]) - set(PERIOD_COLUMNS)
                if unknown or not item["dates"] or "months" in item:
                    raise ValueError(f"bad dates in period fixes: {no} {sorted(unknown)}")
                dates[no] = {k: date.fromisoformat(v) for k, v in item["dates"].items()}
                continue
            shift = item.get("months", YEAR_MONTHS)
            if not isinstance(shift, int) or isinstance(shift, bool) or shift == 0:
                raise ValueError(f"bad months in period fixes: {no} {shift!r}")
            if shift != YEAR_MONTHS:
                months[no] = shift
    return StatementFixes(periods, frozenset(dropped), values, months, dates)


def load_period_fixes(path: Path | None = None) -> dict[str, date]:
    """開示番号 → J-Quants が返す誤った期末（CurPerEn）。drop の開示は含めない。重複は drop も含めて ValueError。"""
    return load_statement_fixes(path).periods


def load_period_months(path: Path | None = None) -> dict[str, int]:
    return load_statement_fixes(path).months


def load_period_dates(path: Path | None = None) -> dict[str, dict[str, date]]:
    return load_statement_fixes(path).dates


def load_dropped_disclosures(path: Path | None = None) -> frozenset[str]:
    return load_statement_fixes(path).dropped


def load_value_fixes(path: Path | None = None) -> dict[str, dict[str, float]]:
    return load_statement_fixes(path).values


@lru_cache(maxsize=1)
def default_period_fixes() -> dict[str, date]:
    return load_period_fixes()


@lru_cache(maxsize=1)
def default_period_months() -> dict[str, int]:
    return load_period_months()


@lru_cache(maxsize=1)
def default_period_dates() -> dict[str, dict[str, date]]:
    return load_period_dates()


@lru_cache(maxsize=1)
def default_dropped_disclosures() -> frozenset[str]:
    return load_dropped_disclosures()


@lru_cache(maxsize=1)
def default_value_fixes() -> dict[str, dict[str, float]]:
    return load_value_fixes()


def apply_value_fix(row: dict, fixes: dict[str, dict[str, float]] | None = None) -> dict:
    found = (default_value_fixes() if fixes is None else fixes).get(row["disclosure_no"])
    return {**row, **found} if found else row


def shift_months(d: date | None, n: int) -> date | None:
    if d is None:
        return None
    year, month = divmod(d.year * 12 + d.month - 1 + n, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    month_end = d.day == calendar.monthrange(d.year, d.month)[1]
    return date(year, month, last if month_end else min(d.day, last))


def next_year(d: date | None) -> date | None:
    return shift_months(d, YEAR_MONTHS)


def apply_period_fix(
    row: dict, fixes: dict[str, date], months: dict[str, int] | None = None,
    dates: dict[str, dict[str, date]] | None = None,
) -> dict:
    """一覧にあり、期末が一覧の値と同じ行だけ直す。dates があればその列をその日付に、無ければ 4 列を一覧の月数（既定 12）ずらす。
    期末が違えば WARNING を出して元のまま。"""
    recorded = fixes.get(row["disclosure_no"])
    if recorded is None:
        return row
    if row["period_end"] != recorded:
        got = row["period_end"].isoformat() if row["period_end"] else "-"
        print(
            f"WARNING jquants statements period-fix-mismatch disclosure_no={row['disclosure_no']} "
            f"code={row['code']} recorded={recorded.isoformat()} period_end={got}",
            flush=True,
        )
        return row
    exact = (default_period_dates() if dates is None else dates).get(row["disclosure_no"])
    if exact is not None:
        return {**row, **exact}
    shift = (default_period_months() if months is None else months).get(row["disclosure_no"], YEAR_MONTHS)
    return {**row, **{col: shift_months(row[col], shift) for col in PERIOD_COLUMNS}}


@dataclass(frozen=True)
class PeriodCandidate:
    rule: str
    disclosure_no: str
    code: str
    period: str | None
    period_end: date
    disclosed_date: date
    detail: str

    def line(self) -> str:
        return (
            f"WARNING jquants statements period-candidate rule={self.rule} code={self.code} "
            f"disclosure_no={self.disclosure_no} period={self.period or '-'} "
            f"period_end={self.period_end.isoformat()} disclosed={self.disclosed_date.isoformat()} {self.detail}"
        )


def _future_candidate(row: dict) -> PeriodCandidate | None:
    if "FinancialStatements" not in (row["type_of_document"] or "") or row["period_end"] <= row["disclosed_date"]:
        return None
    fy_end = row["fiscal_year_end"].isoformat() if row["fiscal_year_end"] else "-"
    return PeriodCandidate(
        "future", row["disclosure_no"], row["code"], row["type_of_period"], row["period_end"], row["disclosed_date"],
        f"fy_end={fy_end}",
    )


def _fy_candidate(row: dict, next_fy_start: date | None) -> PeriodCandidate | None:
    fy_end = row["fiscal_year_end"]
    if next_fy_start is None or fy_end is None or next_fy_start == fy_end + timedelta(days=1):
        return None
    return PeriodCandidate(
        "fy", row["disclosure_no"], row["code"], row["type_of_period"], row["period_end"], row["disclosed_date"],
        f"fy_end={fy_end.isoformat()} next_fy_start={next_fy_start.isoformat()}",
    )


def _quarter_candidate(db: Session, row: dict) -> PeriodCandidate | None:
    if row["type_of_period"] not in QUARTERS or "FinancialStatements" not in (row["type_of_document"] or ""):
        return None
    S = Statement
    earlier = (
        db.query(S.disclosure_no, S.net_sales)
        .filter(
            S.code == row["code"],
            S.type_of_period == row["type_of_period"],
            S.period_end == row["period_end"],
            S.type_of_document.contains("FinancialStatements"),
            S.disclosure_no != row["disclosure_no"],
            or_(S.disclosed_date < row["disclosed_date"],
                and_(S.disclosed_date == row["disclosed_date"], S.disclosure_no < row["disclosure_no"])),
        )
        .order_by(S.disclosed_date.desc(), S.disclosure_no.desc())
        .all()
    )
    differ = [r for r in earlier if r.net_sales != row["net_sales"]]
    if not differ:
        return None
    prev = differ[0]
    return PeriodCandidate(
        "quarter", row["disclosure_no"], row["code"], row["type_of_period"], row["period_end"],
        row["disclosed_date"], f"prev={prev.disclosure_no} prev_sales={prev.net_sales} sales={row['net_sales']}",
    )


def period_candidates(
    db: Session,
    items: list[tuple[dict, date | None]],
    *,
    fixes: dict[str, date] | None = None,
) -> list[PeriodCandidate]:
    """(statements の行, 応答の NxtFYSt) のうち、期の日付がずれている疑いがあるもの。一覧にある開示は除く。

    決算短信の期末が提出日より後なら future。提出日が期末から LATE_DAYS 日以上あとで、通期は NxtFYSt が CurFYEn の
    翌日でない、四半期は同じ銘柄・同じ期の前の決算短信と売上高が違う。四半期は 1 年後の出し直し（訂正）も拾う。
    """
    table = default_period_fixes() if fixes is None else fixes
    out: list[PeriodCandidate] = []
    for row, next_fy_start in items:
        if row["disclosure_no"] in table:
            continue
        end, disclosed = row["period_end"], row["disclosed_date"]
        if end is None or disclosed is None:
            continue
        future = _future_candidate(row)
        if future is not None:
            out.append(future)
            continue
        if (disclosed - end).days < LATE_DAYS:
            continue
        found = _fy_candidate(row, next_fy_start) or _quarter_candidate(db, row)
        if found is not None:
            out.append(found)
    return out


def open_future_candidates(db: Session, *, fixes: dict[str, date] | None = None) -> list[PeriodCandidate]:
    table = default_period_fixes() if fixes is None else fixes
    S = Statement
    cols = ("disclosure_no", "code", "type_of_period", "type_of_document", "period_end", "fiscal_year_end",
            "disclosed_date")
    rows = (
        db.query(*(getattr(S, c) for c in cols))
        .filter(S.type_of_document.contains("FinancialStatements"), S.period_end > S.disclosed_date)
        .order_by(S.disclosed_date, S.disclosure_no)
        .all()
    )
    out = [_future_candidate(dict(zip(cols, r))) for r in rows if r.disclosure_no not in table]
    return [c for c in out if c is not None]


def warn_period_candidates(
    db: Session,
    items: list[tuple[dict, date | None]],
    *,
    fixes: dict[str, date] | None = None,
) -> int:
    found = period_candidates(db, items, fixes=fixes)
    seen = {c.disclosure_no for c in found}
    found += [c for c in open_future_candidates(db, fixes=fixes) if c.disclosure_no not in seen]
    for c in found:
        print(c.line(), flush=True)
    return len(found)

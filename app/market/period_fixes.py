"""J-Quants の決算で期の日付が 1 年前になっている開示の補正・取り込まない開示の一覧と、新しい候補の WARNING。

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


VALUE_COLUMNS = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")


@dataclass(frozen=True)
class StatementFixes:
    periods: dict[str, date]
    dropped: frozenset[str]
    values: dict[str, dict[str, float]]


def load_statement_fixes(path: Path | None = None) -> StatementFixes:
    src = STATEMENT_PERIOD_FIXES_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    periods: dict[str, date] = {}
    dropped: set[str] = set()
    values: dict[str, dict[str, float]] = {}
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
    return StatementFixes(periods, frozenset(dropped), values)


def load_period_fixes(path: Path | None = None) -> dict[str, date]:
    """開示番号 → J-Quants が返す誤った期末（CurPerEn）。drop の開示は含めない。重複は drop も含めて ValueError。"""
    return load_statement_fixes(path).periods


def load_dropped_disclosures(path: Path | None = None) -> frozenset[str]:
    return load_statement_fixes(path).dropped


def load_value_fixes(path: Path | None = None) -> dict[str, dict[str, float]]:
    return load_statement_fixes(path).values


@lru_cache(maxsize=1)
def default_period_fixes() -> dict[str, date]:
    return load_period_fixes()


@lru_cache(maxsize=1)
def default_dropped_disclosures() -> frozenset[str]:
    return load_dropped_disclosures()


@lru_cache(maxsize=1)
def default_value_fixes() -> dict[str, dict[str, float]]:
    return load_value_fixes()


def apply_value_fix(row: dict, fixes: dict[str, dict[str, float]] | None = None) -> dict:
    found = (default_value_fixes() if fixes is None else fixes).get(row["disclosure_no"])
    return {**row, **found} if found else row


def next_year(d: date | None) -> date | None:
    """1 年あと。月末は翌年の同じ月の月末（2/28 → 2/29 も）。"""
    if d is None:
        return None
    month_end = d.day == calendar.monthrange(d.year, d.month)[1]
    return d.replace(year=d.year + 1, day=calendar.monthrange(d.year + 1, d.month)[1] if month_end else d.day)


def apply_period_fix(row: dict, fixes: dict[str, date]) -> dict:
    """一覧にあり、期末が一覧の値と同じ行だけ 4 列を 1 年進める。期末が違えば WARNING を出して元のまま。"""
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
    return {**row, **{col: next_year(row[col]) for col in PERIOD_COLUMNS}}


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
    """(statements の行, 応答の NxtFYSt) のうち、期の日付が 1 年前の疑いがあるもの。一覧にある開示は除く。

    提出日が期末から LATE_DAYS 日以上あとで、通期は NxtFYSt が CurFYEn の翌日でない、四半期は同じ銘柄・同じ期の
    前の決算短信と売上高が違う。四半期は 1 年後の出し直し（訂正）も拾う。
    """
    table = default_period_fixes() if fixes is None else fixes
    out: list[PeriodCandidate] = []
    for row, next_fy_start in items:
        if row["disclosure_no"] in table:
            continue
        end, disclosed = row["period_end"], row["disclosed_date"]
        if end is None or disclosed is None or (disclosed - end).days < LATE_DAYS:
            continue
        found = _fy_candidate(row, next_fy_start) or _quarter_candidate(db, row)
        if found is not None:
            out.append(found)
    return out


def warn_period_candidates(
    db: Session,
    items: list[tuple[dict, date | None]],
    *,
    fixes: dict[str, date] | None = None,
) -> int:
    found = period_candidates(db, items, fixes=fixes)
    for c in found:
        print(c.line(), flush=True)
    return len(found)

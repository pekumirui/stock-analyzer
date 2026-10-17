"""決算発表予定。発表ログを畳んで、その日のカードにする。

現在の予定は (code, fq) で pub_date が最新の行。未定は sch_date NULL。
営業日ではなく暦日。土曜の PubDate も改定になる。
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Literal, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from .. import config
from ..db import upsert
from .jquants import JQuantsClient
from ..models import EarningsDateFetch, EarningsDatePub, Stock

_CODE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]$")
# 2月は 0299 だけが月末。他の月は暦の最終日を月期にする。うるう年は関係ない。
_MONTH_END = {m: calendar.monthrange(2023, m)[1] for m in range(1, 13)}


class FQName(Enum):
    """Wire の FQName。4Q と 5Q は短信の期で、予定の期ではない。"""
    Q1 = "1Q"
    Q2 = "2Q"
    Q3 = "3Q"
    FY = "FY"


_FQ_ORDER = {FQName.Q1: 0, FQName.Q2: 1, FQName.Q3: 2, FQName.FY: 3}


@dataclass(frozen=True)
class FiscalMonthDay:
    """発表時点の FYE。4桁。0299 は 2月末で、日付ではない。"""
    raw: str

    @staticmethod
    def parse(raw: str) -> FiscalMonthDay | None:
        """0299、または月 1-12 かつ日 1-31 の MMDD。それ以外は None。"""
        if not isinstance(raw, str) or len(raw) != 4 or not raw.isdigit():
            return None
        if raw == "0299":
            return FiscalMonthDay(raw)
        month = int(raw[:2])
        day = int(raw[2:])
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return FiscalMonthDay(raw)

    @property
    def label(self) -> str:
        """0299 は 2月末。2月以外の月末は N月期。それ以外は M/D。"""
        if self.raw == "0299":
            return "2月末"
        month = int(self.raw[:2])
        day = int(self.raw[2:])
        if month != 2 and day == _MONTH_END[month]:
            return f"{month}月期"
        return f"{month}/{day}"


@dataclass(frozen=True)
class Pub:
    """発表 1 件。sch_date の None は未定であり、項目の欠測ではない。"""
    code: str
    fq: FQName
    pub_date: date
    sch_date: date | None
    fye: FiscalMonthDay
    co_name: str


@dataclass(frozen=True)
class Schedule:
    """今有効な (code, fq)。sch_date は実在する日。"""
    code: str
    fq: FQName
    fye: FiscalMonthDay
    sch_date: date
    name: str


def fiscal_year_end(fq: FQName, fye: FiscalMonthDay, sch_date: date) -> date:
    """発表対象の四半期を含む事業年度の期末。

    予定の FYE は月日だけなので、sch_date より前で最後に終わった当該四半期から年を決める。
    """
    month = int(fye.raw[:2])
    day = 31 if fye.raw == "0299" else int(fye.raw[2:])
    back = (3 - _FQ_ORDER[fq]) * 3
    q_month = (month - back - 1) % 12 + 1
    year = sch_date.year
    while date(year, q_month, min(day, calendar.monthrange(year, q_month)[1])) >= sch_date:
        year -= 1
    fy_year = year if q_month <= month else year + 1
    return date(fy_year, month, min(day, calendar.monthrange(fy_year, month)[1]))


@dataclass(frozen=True)
class PeriodView:
    fq: FQName
    fye: FiscalMonthDay
    sch_date: date

    def json(self) -> dict:
        end = fiscal_year_end(self.fq, self.fye, self.sch_date)
        return {"fq": self.fq.value, "fye": self.fye.raw, "label": f"FY{end.year % 100:02d}.{end.month}"}


@dataclass(frozen=True)
class ScheduledIssuer:
    """一覧の 1 行。periods は 1Q, 2Q, 3Q, FY の順。"""
    code: str
    name: str
    periods: tuple[PeriodView, ...]


DayKind = Literal["actual", "scheduled", "mixed"]


@dataclass(frozen=True)
class Cell:
    kind: DayKind
    n: int
    pending: int


@dataclass(frozen=True)
class ActualMonth:
    """その月の短信。予定は知らない。api が組み立てる。"""
    counts: Mapping[date, int]
    codes_on_today: frozenset[str]


@dataclass(frozen=True)
class DayBoard:
    kind: DayKind
    actuals: tuple[dict, ...]
    scheduled: tuple[ScheduledIssuer, ...]


def _as_date(raw: object) -> date | None:
    # 3.11 以降の fromisoformat は 20260920 も日付にする。ワイヤは YYYY-MM-DD。
    if not isinstance(raw, str) or len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_pub(wire: dict) -> Pub | None:
    """読めなければ None。PubDate, SchDate, FQName, FYE, Code, CoName。CoNameEn は捨てる。

    SchDate が空文字なら sch_date は None。欠測や日付でない SchDate は行ごと捨てる。
    """
    if not isinstance(wire, dict):
        return None
    pub_date = _as_date(wire.get("PubDate"))
    if pub_date is None or "SchDate" not in wire:
        return None
    sch_raw = wire.get("SchDate")
    if sch_raw == "":
        sch_date = None
    else:
        sch_date = _as_date(sch_raw)
        if sch_date is None:
            return None
    try:
        fq = FQName(wire.get("FQName"))
    except ValueError:
        return None
    fye_raw = wire.get("FYE")
    fye = FiscalMonthDay.parse(fye_raw) if isinstance(fye_raw, str) else None
    if fye is None:
        return None
    code = wire.get("Code")
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        return None
    co_name = wire.get("CoName")
    if not isinstance(co_name, str):
        return None
    return Pub(code, fq, pub_date, sch_date, fye, co_name)


def fold_current(pubs: Sequence[Pub]) -> tuple[Schedule, ...]:
    """(code, fq) ごとに最新の pub_date。その sch_date が None なら予定に残さない。

    名前は Pub.co_name。load_current がマスタにあれば stocks.name に替える。
    FYE は勝った発表のものだけ。同じコードの別 fq は両方残る。
    """
    best: dict[tuple[str, FQName], Pub] = {}
    for pub in pubs:
        key = (pub.code, pub.fq)
        prev = best.get(key)
        if prev is None or pub.pub_date >= prev.pub_date:
            best[key] = pub
    out: list[Schedule] = []
    for pub in best.values():
        if pub.sch_date is None:
            continue
        out.append(Schedule(pub.code, pub.fq, pub.fye, pub.sch_date, pub.co_name))
    out.sort(key=lambda s: (s.code, _FQ_ORDER[s.fq]))
    return tuple(out)


def visible_schedules(
    day: date,
    today: date,
    schedules: Sequence[Schedule],
    actual_codes: frozenset[str],
) -> tuple[Schedule, ...]:
    """過去は空。未来はその日の全部。今日は actual_codes に無いコード。

    actual_codes は今日の開示銘柄だけ。他の日は空集合を渡す。
    引き算はコード単位。(code, fq) では引かない。
    """
    if day < today:
        return ()
    picked = tuple(s for s in schedules if s.sch_date == day)
    if day > today:
        return picked
    return tuple(s for s in picked if s.code not in actual_codes)


def kind_of(day: date, today: date, has_actuals: bool, has_pending: bool) -> DayKind:
    """過去は空でも actual。未来は空でも scheduled。

    今日が mixed なのは両方あるときだけ。今日でどちらも無いときは scheduled。
    画面の文言は 予定なし になる。
    """
    if day < today:
        return "actual"
    if day > today:
        return "scheduled"
    if has_actuals and has_pending:
        return "mixed"
    if has_actuals:
        return "actual"
    return "scheduled"


def group_issuers(rows: Sequence[Schedule]) -> tuple[ScheduledIssuer, ...]:
    """コードごとに 1 行。期は FQName の順。銘柄はコード順。"""
    by: dict[str, list[Schedule]] = {}
    for row in rows:
        by.setdefault(row.code, []).append(row)
    out: list[ScheduledIssuer] = []
    for code in sorted(by):
        ordered = sorted(by[code], key=lambda s: _FQ_ORDER[s.fq])
        periods = tuple(PeriodView(s.fq, s.fye, s.sch_date) for s in ordered)
        out.append(ScheduledIssuer(code, ordered[0].name, periods))
    return tuple(out)


def cells_for_month(
    month_start: date,
    today: date,
    actuals: ActualMonth,
    current: Sequence[Schedule],
) -> dict[date, Cell]:
    """事実がある日だけ。n も pending も 0 の日は入れない。

    current はその月の sch_date に限ってあり、REIT も除いてある。
    """
    days = set(actuals.counts) | {s.sch_date for s in current}
    out: dict[date, Cell] = {}
    for day in sorted(days):
        if (day.year, day.month) != (month_start.year, month_start.month):
            continue
        actual_n = int(actuals.counts.get(day, 0))
        codes = actuals.codes_on_today if day == today else frozenset()
        pending_n = len(group_issuers(visible_schedules(day, today, current, codes)))
        has_actuals = actual_n > 0
        has_pending = pending_n > 0
        kind = kind_of(day, today, has_actuals, has_pending)
        if day < today:
            n, pending = actual_n, 0
        elif day > today:
            n, pending = pending_n, 0
        elif kind == "mixed":
            n, pending = actual_n, pending_n
        elif kind == "actual":
            n, pending = actual_n, 0
        else:
            n, pending = pending_n, 0
        if n == 0 and pending == 0:
            continue
        out[day] = Cell(kind, n, pending)
    return out


def board_for_day(
    day: date,
    today: date,
    actual_rows: Sequence[dict],
    schedules: Sequence[Schedule],
) -> DayBoard:
    """actual_rows は code を持つ。過去と今日はそのまま返す。

    未来は actual_rows を捨てる。過去は予定を捨てる。
    今日は actual_rows にいるコードの予定を落とす。
    """
    if day > today:
        issuers = group_issuers(visible_schedules(day, today, schedules, frozenset()))
        return DayBoard("scheduled", (), issuers)
    if day < today:
        return DayBoard("actual", tuple(actual_rows), ())
    codes = frozenset(str(row["code"]) for row in actual_rows)
    issuers = group_issuers(visible_schedules(day, today, schedules, codes))
    return DayBoard(
        kind_of(day, today, bool(actual_rows), bool(issuers)),
        tuple(actual_rows),
        issuers,
    )


def days_to_fetch(today: date, known: set[date], lookback_days: int) -> list[date]:
    """台帳に無い暦日に、今日と昨日を足す。昨日は台帳にあっても取り直す。"""
    start = today - timedelta(days=lookback_days)
    window = {start + timedelta(days=i) for i in range(lookback_days + 1)}
    return sorted((window - known) | {today - timedelta(days=1), today})


def ingest_pub_date(db: Session, jq: JQuantsClient, pub_date: date) -> int:
    """取得して UPSERT し、台帳と一緒に commit する。書いた行数を返す。

    頼んだ日と pub_date が違う行は捨てる。同じ (code, fq, pub_date) は後勝ち。
    空の応答でも台帳に n=0 を書く。HTTP の失敗は書く前に上がる。
    """
    wire = jq.fins_earnings_date(pub_date.isoformat())
    dedup: dict[tuple[str, FQName, date], Pub] = {}
    skipped = 0
    for row in wire:
        pub = parse_pub(row)
        if pub is None or pub.pub_date != pub_date:
            skipped += 1
            continue
        dedup[(pub.code, pub.fq, pub.pub_date)] = pub
    rows = [
        {
            "code": pub.code,
            "fq_name": pub.fq.value,
            "pub_date": pub.pub_date,
            "sch_date": pub.sch_date,
            "fye": pub.fye.raw,
            "co_name": pub.co_name,
        }
        for pub in dedup.values()
    ]
    upsert(db, EarningsDatePub, rows, commit=False)
    upsert(db, EarningsDateFetch, [{"pub_date": pub_date, "n": len(rows)}], commit=False)
    db.commit()
    if skipped:
        print(f"  {pub_date.isoformat()} の発表予定を {skipped} 件スキップ")
    return len(rows)


def sync(db: Session, today: date, *, lookback_days: int | None = None) -> int:
    """fins earnings-date を叩いた回数。指標は触らない。クライアントはここで開く。"""
    span = config.EARNINGS_DATE_LOOKBACK_DAYS if lookback_days is None else lookback_days
    start = today - timedelta(days=span)
    known = {
        d for (d,) in db.query(EarningsDateFetch.pub_date).filter(
            EarningsDateFetch.pub_date >= start,
            EarningsDateFetch.pub_date <= today,
        )
    }
    todo = days_to_fetch(today, known, span)
    if len(todo) > 2:
        print(f"決算発表予定を {len(todo)} 日分")
    n_req = 0
    with JQuantsClient() as jq:
        for i, pub_day in enumerate(todo, 1):
            written = ingest_pub_date(db, jq, pub_day)
            n_req += 1
            if len(todo) > 2 and (i % 20 == 0 or i == len(todo)):
                print(f"  [{i}/{len(todo)}] {pub_day.isoformat()} {written}")
    return n_req


def load_current(db: Session, sch_from: date, sch_to: date) -> list[Schedule]:
    """(code, fq) で最新の発表だけを取り、その sch_date が範囲内のものを残す。

    移動前の日が戻らないよう、範囲内の行でも最新の発表でなければ捨てる。
    発表ログは 5 年分あるので畳むのは SQL 側。全件を Python に載せない。
    REIT は読むときに落とす。stocks に無いコードは残す。
    名前はマスタにあれば stocks.name。
    """
    newer = aliased(EarningsDatePub)
    latest = ~(select(1).where(
        newer.code == EarningsDatePub.code,
        newer.fq_name == EarningsDatePub.fq_name,
        newer.pub_date > EarningsDatePub.pub_date,
    ).exists())
    pubs: list[Pub] = []
    for row in db.query(EarningsDatePub).filter(
        EarningsDatePub.sch_date >= sch_from,
        EarningsDatePub.sch_date <= sch_to,
        latest,
    ):
        try:
            fq = FQName(row.fq_name)
        except ValueError:
            continue
        fye = FiscalMonthDay.parse(row.fye)
        if fye is None:
            continue
        pubs.append(Pub(row.code, fq, row.pub_date, row.sch_date, fye, row.co_name or ""))
    folded = [s for s in fold_current(pubs) if sch_from <= s.sch_date <= sch_to]
    if not folded:
        return []
    codes = {s.code for s in folded}
    master = {
        code: (name, cat)
        for code, name, cat in db.query(
            Stock.code, Stock.name, Stock.product_category,
        ).filter(Stock.code.in_(codes))
    }
    out: list[Schedule] = []
    for sched in folded:
        hit = master.get(sched.code)
        if hit is None:
            out.append(sched)
            continue
        name, cat = hit
        if cat in config.EARNINGS_EXCLUDE_PRODUCT_CATEGORIES:
            continue
        if name:
            sched = Schedule(sched.code, sched.fq, sched.fye, sched.sch_date, name)
        out.append(sched)
    return out


def _ledger_ready(db: Session, today: date) -> bool:
    """[today - lookback, today] の暦日が全部 fetches にある。"""
    start = today - timedelta(days=config.EARNINGS_DATE_LOOKBACK_DAYS)
    need = (today - start).days + 1
    got = db.query(func.count(EarningsDateFetch.pub_date)).filter(
        EarningsDateFetch.pub_date >= start,
        EarningsDateFetch.pub_date <= today,
    ).scalar()
    return int(got or 0) == need


def _issuer_json(issuer: ScheduledIssuer) -> dict:
    return {
        "code": issuer.code,
        "name": issuer.name,
        "periods": [period.json() for period in issuer.periods],
    }


def month_payload(
    db: Session, month: str, today: date, actuals: ActualMonth,
) -> dict[str, dict]:
    """ISO 日付から {kind, n}。pending は 0 より大きいときだけ。"""
    year_s, month_s = month.split("-")
    start = date(int(year_s), int(month_s), 1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    cells = cells_for_month(start, today, actuals, load_current(db, start, end))
    out: dict[str, dict] = {}
    for day, cell in cells.items():
        item = {"kind": cell.kind, "n": cell.n}
        if cell.pending > 0:
            item["pending"] = cell.pending
        out[day.isoformat()] = item
    return out


def day_payload(db: Session, day: date, today: date, actual_rows: list[dict]) -> dict:
    """{kind, actuals, scheduled}。予定を読むのは day >= today のときだけ。

    台帳が lookback を埋める前の未来には ready false を付ける。画面は空なら 予定未取得。
    """
    schedules = load_current(db, day, day) if day >= today else []
    board = board_for_day(day, today, actual_rows, schedules)
    codes = {row["code"] for row in board.actuals} | {issuer.code for issuer in board.scheduled}
    sectors = dict(db.query(Stock.code, Stock.sector33).filter(Stock.code.in_(codes)).all())
    out: dict = {
        "kind": board.kind,
        "actuals": [{**row, "sector33": sectors.get(row["code"])} for row in board.actuals],
        "scheduled": [{**_issuer_json(issuer), "sector33": sectors.get(issuer.code)}
                      for issuer in board.scheduled],
    }
    if day > today and not _ledger_ready(db, today):
        out["ready"] = False
    return out

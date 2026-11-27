from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..models import MonthlyPublication, MonthlyValue

_JST = ZoneInfo("Asia/Tokyo")

BASIS = ("tdnet", "ir_page", "first_seen", "unknown")
REASONS = ("carried_history", "manual", "no_source", "no_page_date", "unspecified")
CONFIDENCE = ("exact", "estimated", "unknown")
RANK = {"unknown": 0, "first_seen": 1, "ir_page": 2, "tdnet": 3}
_CONF = {"unknown": 0, "estimated": 1, "exact": 2}
_TIME_RE_LEN = (5, 8)


class PublicationError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Pub:
    basis: str
    on: date | None
    time: str | None
    reason: str | None
    ref: str | None
    confidence: str

    def as_dict(self) -> dict:
        return {
            "basis": self.basis,
            "on": self.on.isoformat() if self.on is not None else None,
            "time": self.time,
            "reason": self.reason,
            "ref": self.ref,
            "confidence": self.confidence,
        }


def unknown(reason: str, ref: str | None = None) -> Pub:
    return Pub(
        basis="unknown", on=None, time=None, reason=reason, ref=ref, confidence="unknown",
    )


def dated(
    basis: str,
    on: date,
    time: str | None = None,
    ref: str | None = None,
    confidence: str | None = None,
) -> Pub:
    if confidence is None:
        confidence = "estimated" if basis == "first_seen" else "exact"
    return Pub(
        basis=basis, on=on, time=time or None, reason=None, ref=ref, confidence=confidence,
    )


def check(pub: Pub) -> Pub:
    if pub.basis not in RANK:
        raise PublicationError(f"basis は {' / '.join(BASIS)} です。")
    if pub.confidence not in _CONF:
        raise PublicationError(f"confidence は {' / '.join(CONFIDENCE)} です。")
    if pub.basis == "unknown" and pub.confidence != "unknown":
        raise PublicationError("unknown の confidence は unknown です。")
    if pub.basis != "unknown" and pub.confidence == "unknown":
        raise PublicationError("日付がある行の confidence は exact か estimated です。")
    if pub.time is not None and (
        len(pub.time) not in _TIME_RE_LEN or pub.time[2] != ":"
        or not pub.time.replace(":", "").isdigit()
    ):
        raise PublicationError("time は HH:MM または HH:MM:SS です。")
    if pub.basis == "unknown":
        if pub.on is not None or pub.time is not None:
            raise PublicationError("unknown に日付は入れません。")
        if pub.reason not in REASONS:
            raise PublicationError(f"reason は {' / '.join(REASONS)} です。")
        return pub
    if pub.on is None:
        raise PublicationError("日付が要ります。")
    if pub.reason is not None:
        raise PublicationError("日付がある行に reason は入れません。")
    return pub


def parse_publication(raw: object) -> Pub:
    if raw is None:
        return check(unknown("unspecified"))
    if not isinstance(raw, dict):
        raise PublicationError("publication はオブジェクトです。")
    basis = raw.get("basis")
    if not isinstance(basis, str):
        raise PublicationError(f"basis は {' / '.join(BASIS)} です。")
    on = _parse_on(raw.get("on"))
    clock = _parse_time(raw.get("time"))
    reason = raw.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise PublicationError("reason は文字列です。")
    ref = raw.get("ref")
    if ref is not None and (not isinstance(ref, str) or len(ref) > 240):
        raise PublicationError("ref は 240 字までの文字列です。")
    confidence = raw.get("confidence")
    if confidence is None:
        confidence = "unknown" if basis == "unknown" else (
            "estimated" if basis == "first_seen" else "exact"
        )
    if not isinstance(confidence, str):
        raise PublicationError(f"confidence は {' / '.join(CONFIDENCE)} です。")
    if basis == "unknown":
        if on is not None or clock is not None:
            raise PublicationError("unknown に日付は入れません。")
        return check(Pub(
            basis="unknown", on=None, time=None,
            reason=reason if isinstance(reason, str) else None,
            ref=ref, confidence="unknown",
        ))
    return check(Pub(
        basis=basis, on=on, time=clock,
        reason=reason if isinstance(reason, str) else None,
        ref=ref, confidence=confidence,
    ))


def _parse_on(raw: object) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PublicationError("on は YYYY-MM-DD です。")
    try:
        got = date.fromisoformat(raw)
    except ValueError as exc:
        raise PublicationError("on は YYYY-MM-DD です。") from exc
    if got.isoformat() != raw:
        raise PublicationError("on は YYYY-MM-DD です。")
    return got


def _parse_time(raw: object) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise PublicationError("time は HH:MM または HH:MM:SS です。")
    if len(raw) == 5:
        raw = raw + ":00"
    if len(raw) != 8 or raw[2] != ":" or raw[5] != ":":
        raise PublicationError("time は HH:MM または HH:MM:SS です。")
    hh, mm, ss = raw.split(":")
    if not (hh.isdigit() and mm.isdigit() and ss.isdigit()):
        raise PublicationError("time は HH:MM または HH:MM:SS です。")
    if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59 and 0 <= int(ss) <= 59):
        raise PublicationError("time は HH:MM または HH:MM:SS です。")
    return raw


def better(current: Pub, incoming: Pub) -> Pub:
    if _CONF[current.confidence] != _CONF[incoming.confidence]:
        return current if _CONF[current.confidence] > _CONF[incoming.confidence] else incoming
    if current.confidence == "unknown":
        return current
    assert current.on is not None and incoming.on is not None
    if current.on != incoming.on:
        return current if current.on < incoming.on else incoming
    if RANK[current.basis] != RANK[incoming.basis]:
        return current if RANK[current.basis] > RANK[incoming.basis] else incoming
    if bool(current.time) != bool(incoming.time):
        return current if current.time else incoming
    return current


def _now() -> datetime:
    return datetime.now(_JST).replace(tzinfo=None)


def _from_row(row: MonthlyPublication) -> Pub:
    return Pub(
        basis=row.basis,
        on=row.published_on,
        time=row.published_time,
        reason=row.unknown_reason,
        ref=row.source_ref,
        confidence=row.confidence,
    )


def _write(row: MonthlyPublication, pub: Pub, now: datetime) -> None:
    row.published_on = pub.on
    row.published_time = pub.time
    row.basis = pub.basis
    row.unknown_reason = pub.reason
    row.source_ref = pub.ref
    row.confidence = pub.confidence
    row.recorded_at = now


def note(db, code: str, year: int, month: int, pub: Pub) -> str:
    check(pub)
    db.flush()
    row = db.get(MonthlyPublication, (code, year, month))
    now = _now()
    if row is None:
        db.add(MonthlyPublication(
            code=code, year=year, month=month,
            published_on=pub.on, published_time=pub.time, basis=pub.basis,
            unknown_reason=pub.reason, source_ref=pub.ref, confidence=pub.confidence,
            recorded_at=now,
        ))
        return "insert"
    chosen = better(_from_row(row), pub)
    if (
        chosen.basis == row.basis
        and chosen.on == row.published_on
        and chosen.time == row.published_time
        and chosen.reason == row.unknown_reason
        and chosen.ref == row.source_ref
        and chosen.confidence == row.confidence
    ):
        return "keep"
    _write(row, chosen, now)
    return "replace"


def value_months(db, code: str) -> set[tuple[int, int]]:
    db.flush()
    rows = (
        db.query(MonthlyValue.year, MonthlyValue.month)
        .filter(MonthlyValue.code == code)
        .distinct()
        .all()
    )
    return {(int(year), int(month)) for year, month in rows}


def drop(db, code: str, year: int, month: int) -> None:
    row = db.get(MonthlyPublication, (code, year, month))
    if row is not None:
        db.delete(row)


def _drop_gone(db, code: str, before: set[tuple[int, int]], after: set[tuple[int, int]]) -> None:
    for year, month in before - after:
        drop(db, code, year, month)


def record_written(db, code: str, before: set[tuple[int, int]], pub: Pub) -> None:
    check(pub)
    after = value_months(db, code)
    _drop_gone(db, code, before, after)
    new = after - before
    if not new:
        return
    anchor = max(new)
    note(db, code, anchor[0], anchor[1], pub)
    if len(new) == 1:
        return
    history = unknown("carried_history", pub.ref)
    for year, month in new:
        if (year, month) != anchor:
            note(db, code, year, month, history)


def record_manual(db, code: str, before: set[tuple[int, int]]) -> None:
    after = value_months(db, code)
    _drop_gone(db, code, before, after)
    manual = unknown("manual")
    for year, month in after - before:
        note(db, code, year, month, manual)

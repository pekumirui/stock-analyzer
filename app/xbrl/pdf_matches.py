from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select, text, union
from sqlalchemy.orm import Session

from app.models import Stock, XbrlFiling, XbrlPdfMatch
from app.xbrl.three_statements import pdf_only_matches

__all__ = ["LOCK_RETRY_WAIT", "rebuild_code", "rebuild_matches", "summary", "target_codes", "unmatched_names"]

LOCK_RETRY_WAIT = 5.0
COUNT_KEYS = ("codes", "filings", "matched", "unmatched", "failed", "skipped", "deleted")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_locked(exc: Exception) -> bool:
    return "database is locked" in str(exc)


def listed_codes(db: Session) -> set[str]:
    return {str(code) for (code,) in db.execute(select(Stock.code))}


def _candidate_codes(db: Session) -> set[str]:
    stmt = union(
        select(XbrlFiling.code).where(XbrlFiling.source == "tdnet_pdf"),
        select(XbrlPdfMatch.code),
    )
    return {str(code) for (code,) in db.execute(stmt)}


def target_codes(db: Session) -> list[str]:
    return sorted(_candidate_codes(db) & listed_codes(db))


def stale_codes(db: Session) -> list[str]:
    stored = {str(code) for (code,) in db.execute(select(XbrlPdfMatch.code).distinct())}
    return sorted(stored - listed_codes(db))


def purge_code(db: Session, code: str) -> Counter:
    result = db.execute(delete(XbrlPdfMatch).where(XbrlPdfMatch.code == code))
    db.commit()
    return Counter(deleted=result.rowcount or 0)


def rebuild_code(db: Session, code: str) -> Counter:
    matches = pdf_only_matches(db, code)
    now = _now()
    rows = [
        {
            "fact_id": m.fact.id,
            "filing_id": m.filing.id,
            "code": code,
            "is_consolidated": m.consolidated,
            "statement": m.statement,
            "element_key": None if m.mapped is None else m.mapped.key,
            "sign": 1 if m.mapped is None else m.mapped.sign,
            "matched_by": None if m.mapped is None else m.mapped.by,
            "generated_at": now,
        }
        for m in matches
    ]
    db.execute(delete(XbrlPdfMatch).where(XbrlPdfMatch.code == code))
    if rows:
        db.execute(insert(XbrlPdfMatch), rows)
    db.commit()
    counts: Counter = Counter()
    counts["codes"] += 1
    counts["filings"] += len({m.filing.id for m in matches})
    counts["matched"] += sum(1 for m in matches if m.mapped is not None)
    counts["unmatched"] += sum(1 for m in matches if m.mapped is None)
    return counts


def _line(counts: Counter) -> str:
    return " ".join(f"{key}={counts[key]}" for key in COUNT_KEYS)


def rebuild_matches(
    db: Session,
    *,
    codes: list[str] | None = None,
    batch: int | None = None,
    retry_wait: float = LOCK_RETRY_WAIT,
) -> Counter:
    counts: Counter = Counter()
    stale: set[str] = set()
    if codes:
        listed = listed_codes(db)
        targets = sorted(set(codes) & listed)
        for code in sorted(set(codes) - listed):
            counts["skipped"] += 1
            print(f"pdf_matches skipped code={code} reason=not in stocks", flush=True)
    else:
        targets = target_codes(db)
        stale = set(stale_codes(db))
        counts["skipped"] = len(_candidate_codes(db) - set(targets))
        targets = sorted(set(targets) | stale)
    step = batch if batch and batch > 0 else len(targets) or 1

    def run(code: str) -> None:
        counts.update(purge_code(db, code) if code in stale else rebuild_code(db, code))

    def failed(code: str, exc: Exception) -> None:
        db.rollback()
        counts["failed"] += 1
        print(f"pdf_matches failed code={code} err={exc}", flush=True)

    for start in range(0, len(targets), step):
        chunk = targets[start:start + step]
        locked: list[str] = []
        for code in chunk:
            try:
                run(code)
            except Exception as exc:
                if batch and _is_locked(exc):
                    db.rollback()
                    locked.append(code)
                    continue
                failed(code, exc)
        if locked:
            print(f"pdf_matches locked retry={len(locked)} wait={retry_wait:g}s", flush=True)
            time.sleep(retry_wait)
            for code in locked:
                try:
                    run(code)
                except Exception as exc:
                    failed(code, exc)
        if batch:
            done = start + len(chunk)
            print(
                f"pdf_matches batch done={done}/{len(targets)} {_line(counts)} left={len(targets) - done}",
                flush=True,
            )
    return counts


def summary(counts: Counter) -> str:
    return f"pdf_matches {_line(counts)}"


def unmatched_names(db: Session, limit: int = 100) -> list[tuple[str, str, int]]:
    rows = db.execute(
        text(
            "SELECT m.statement, e.local_name, count(*) AS n FROM xbrl_pdf_matches m "
            "JOIN xbrl_facts f ON f.id = m.fact_id JOIN xbrl_elements e ON e.id = f.element_id "
            "WHERE m.element_key IS NULL GROUP BY m.statement, e.local_name "
            "ORDER BY n DESC, m.statement, e.local_name LIMIT :limit"
        ),
        {"limit": limit},
    ).all()
    return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]

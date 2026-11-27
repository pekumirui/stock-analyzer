from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.monthly.publications import Pub, better, dated, unknown
from app.xbrl.codes import jq_lookup_code
from jobs._r2common import resolve_database_url, sqlite_path_from_url
from jobs.monthly.publication_claim import (
    html_backfill_claim,
    meta_clock,
    pdf_backfill_claim,
    read_html,
    report_month,
)
from jobs.monthly.tdnet_monthly_r2 import DEFAULT_DEST

_CODE_DIR_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_PDF_RE = re.compile(r"^(\d{8})_.*\.pdf$", re.I)
_YMD_RE = re.compile(r"^(\d{8})_")


def _ymd(name: str) -> date | None:
    m = _YMD_RE.match(name)
    if m is None:
        return None
    raw = m.group(1)
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _clock(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) == 5:
        text = text + ":00"
    if len(text) < 8 or text[2] != ":" or text[5] != ":":
        return None
    return text[:8]


def scan_files(dest: Path) -> dict[str, list[tuple[tuple[int, int], Pub]]]:
    found: dict[str, list[tuple[tuple[int, int], Pub]]] = defaultdict(list)
    if not dest.is_dir():
        return found
    for code_dir in sorted(p for p in dest.iterdir() if p.is_dir()):
        if _CODE_DIR_RE.fullmatch(code_dir.name) is None:
            continue
        code5 = jq_lookup_code(code_dir.name)
        if not code5:
            continue
        for path in sorted(code_dir.iterdir()):
            if not path.is_file():
                continue
            if _PDF_RE.match(path.name):
                disclosed = _ymd(path.name)
                if disclosed is None:
                    continue
                claim = pdf_backfill_claim(
                    path.name, disclosed, meta_clock(path, disclosed), path.name,
                )
                if claim is not None:
                    found[code5].append(claim)
                continue
            if path.name.endswith(".ingest.json") and not path.name.endswith(".pdf-ingest.json"):
                claim = _html_file(path)
                if claim is not None:
                    found[code5].append(claim)
    return found


def _html_file(path: Path) -> tuple[tuple[int, int], Pub] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("as_of"), str):
        return None
    try:
        as_of = date.fromisoformat(raw["as_of"])
    except ValueError:
        return None
    if as_of.isoformat() != raw["as_of"]:
        return None
    html_path = path.with_name(path.name[: -len(".ingest.json")])
    html = read_html(html_path if html_path.is_file() else None)
    return html_backfill_claim(as_of, _ymd(path.name), html, path.name)


def scan_statements(con: sqlite3.Connection) -> dict[str, list[tuple[tuple[int, int], Pub]]]:
    found: dict[str, list[tuple[tuple[int, int], Pub]]] = defaultdict(list)
    names = {
        row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "statements" not in names:
        return found
    rows = con.execute(
        "SELECT code, disclosed_date, disclosed_time, type_of_document, disclosure_no "
        "FROM statements WHERE disclosed_date IS NOT NULL AND code IS NOT NULL"
    )
    for code, disclosed, clock, title, disclosure_no in rows:
        if not isinstance(disclosed, str):
            continue
        try:
            day = date.fromisoformat(disclosed[:10])
        except ValueError:
            continue
        ym = report_month(str(title or ""), day)
        if ym is None:
            continue
        found[str(code)].append((
            ym, dated("tdnet", day, _clock(clock), str(disclosure_no or "") or None),
        ))
    return found


def _load_values(con: sqlite3.Connection) -> list[tuple[str, int, int]]:
    return [
        (str(code), int(year), int(month))
        for code, year, month in con.execute(
            "SELECT DISTINCT code, year, month FROM monthly_values"
        )
    ]


def _load_pubs(con: sqlite3.Connection) -> dict[tuple[str, int, int], Pub]:
    names = {
        row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "monthly_publications" not in names:
        return {}
    cols = {row[1] for row in con.execute("PRAGMA table_info(monthly_publications)")}
    sql = (
        "SELECT code, year, month, basis, published_on, published_time, "
        "unknown_reason, source_ref, confidence FROM monthly_publications"
        if "confidence" in cols else
        "SELECT code, year, month, basis, published_on, published_time, "
        "unknown_reason, source_ref, NULL FROM monthly_publications"
    )
    out: dict[tuple[str, int, int], Pub] = {}
    rows = con.execute(sql)
    for code, year, month, basis, on, clock, reason, ref, confidence in rows:
        got = date.fromisoformat(on) if isinstance(on, str) and on else None
        if confidence is None:
            confidence = "unknown" if basis == "unknown" or got is None else "exact"
        out[(str(code), int(year), int(month))] = Pub(
            basis=str(basis), on=got, time=clock, reason=reason, ref=ref,
            confidence=str(confidence),
        )
    return out


def _fold(current: Pub | None, claims: list[tuple[tuple[int, int], Pub]], ym: tuple[int, int]) -> Pub:
    best = current
    for claim_ym, pub in claims:
        if claim_ym != ym:
            continue
        best = pub if best is None else better(best, pub)
    if best is None:
        return unknown("no_source")
    return best


def _same(a: Pub, b: Pub) -> bool:
    return (
        a.basis == b.basis and a.on == b.on and a.time == b.time
        and a.reason == b.reason and a.ref == b.ref and a.confidence == b.confidence
    )


def plan_rows(
    months: list[tuple[str, int, int]],
    existing: dict[tuple[str, int, int], Pub],
    claims: dict[str, list[tuple[tuple[int, int], Pub]]],
) -> tuple[list[tuple[str, int, int, Pub]], dict[str, int]]:
    writes: list[tuple[str, int, int, Pub]] = []
    counts = {level: 0 for level in ("exact", "estimated", "unknown")}
    for code, year, month in months:
        key = (code, year, month)
        current = existing.get(key)
        chosen = _fold(current, claims.get(code, []), (year, month))
        counts[chosen.confidence] = counts.get(chosen.confidence, 0) + 1
        if current is None or not _same(current, chosen):
            writes.append((code, year, month, chosen))
    return writes, counts


def open_ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build(db_path: Path, dest: Path) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int, Pub]], dict[str, int]]:
    file_claims = scan_files(dest)
    con = open_ro(db_path)
    try:
        months = _load_values(con)
        existing = _load_pubs(con)
        claims: dict[str, list[tuple[tuple[int, int], Pub]]] = defaultdict(list)
        for code, rows in file_claims.items():
            claims[code].extend(rows)
        for code, rows in scan_statements(con).items():
            claims[code].extend(rows)
    finally:
        con.close()
    writes, counts = plan_rows(months, existing, claims)
    return months, writes, counts


def apply_writes(db_path: Path, writes: list[tuple[str, int, int, Pub]]) -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import MonthlyPublication
    from app.monthly.publications import note

    engine = create_engine(f"sqlite:///{db_path}")
    MonthlyPublication.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        for code, year, month, pub in writes:
            note(db, code, year, month, pub)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        engine.dispose()
    return len(writes)


def _summary(months: list, counts: dict[str, int], writes: list) -> str:
    total = len(months)
    exact = counts.get("exact", 0)
    estimated = counts.get("estimated", 0)
    unknown_n = counts.get("unknown", 0)
    exact_ratio = (exact / total) if total else 0.0
    dated_ratio = ((exact + estimated) / total) if total else 0.0
    return (
        f"value_months={total} exact={exact} estimated={estimated} unknown={unknown_n} "
        f"writes={len(writes)} fraction_exact={exact_ratio:.4f} "
        f"fraction_dated={dated_ratio:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fill monthly_publications from TDnet filenames, IR sidecars, and statements. Dry-run unless --write.",
    )
    parser.add_argument("--db", default="", help="sqlite file (default DATABASE_URL)")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="data/monthly directory")
    parser.add_argument("--write", action="store_true", help="apply the plan. Default is dry-run.")
    args = parser.parse_args(argv)
    if args.db:
        db_path = Path(args.db)
    else:
        resolved = sqlite_path_from_url(resolve_database_url())
        if resolved is None:
            print("db is not a sqlite file", file=sys.stderr)
            return 2
        db_path = resolved
    if not db_path.is_file():
        print(f"db missing {db_path}", file=sys.stderr)
        return 2
    months, writes, counts = build(db_path, Path(args.dest))
    line = _summary(months, counts, writes)
    if not args.write:
        print(f"dry-run {line}")
        return 0
    n = apply_writes(db_path, writes)
    print(f"wrote {n} {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""TDnet 月次 PDF の未投入レポート（読み取り専用）。

R2 の `monthly/{code}/YYYYMMDD_CODE_….pdf` と DB `monthly_values` の
最新 (year, month) を銘柄ごとに突き合わせる。R2 にも DB にも書かない。
ネットワークには出ない。

  python tools/tdnet_monthly_pending.py
  python tools/tdnet_monthly_pending.py --only-stale
  python tools/tdnet_monthly_pending.py --code 9279 --json
  python tools/tdnet_monthly_pending.py --dest /tmp/monthly --db /tmp/stock.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TextIO

JOBS = Path(__file__).resolve().parent.parent / "jobs"
if str(JOBS) not in sys.path:
    sys.path.insert(0, str(JOBS))

from _r2common import MountDisconnected, check_r2_mount, resolve_database_url, sqlite_path_from_url  # noqa: E402
from tdnet_monthly_r2 import DEFAULT_DEST, display_code, jq_lookup_code  # noqa: E402

_ISSUER_DIR_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_PDF_NAME_RE = re.compile(r"^(\d{8})_.*\.pdf$", re.I)

# display_code（4桁）。PDF があっても MISSING に出さない。MONTHLY_SKIP_CODES とは別。
# 7062 G-フレアス: 2026-08-20 に月次開示終了。表がなく NO_TABLE のため投入できない。
PENDING_IGNORE_CODES = frozenset({"7062"})

HEURISTIC_LINES = (
    "heuristic: filename YYYYMMDD prefix = TDnet pubdate (ingest as_of).",
    (
        "expected_month = calendar month before that date "
        "(usual 月次売上: 2026-08-05 開示 → 2026-07)."
    ),
    (
        "Matches ingest _anchor_year(last_month, as_of) when the PDF's rightmost "
        "column is that previous calendar month. PDF bytes and fiscal_year_end "
        "are not read; exact column years stay ambiguous without the table."
    ),
    (
        "stale = expected_month > DB latest (year, month). "
        "missing = PDF on R2 but no monthly_values. Age is not a filter "
        "(21:00 TDnet batch PDFs may never have been ingested). "
        "PENDING_IGNORE_CODES (7062 only) are omitted from the report."
    ),
    (
        "Scan is monthly/{code}/*.pdf with a date prefix. HTML/xlsx/xls are ignored. "
        "Read-only sqlite (mode=ro). No network."
    ),
)


@dataclass(frozen=True)
class PdfHit:
    code: str
    pdf_date: date
    pdf_name: str


@dataclass(frozen=True)
class ReportRow:
    code: str
    code5: str
    pdf_date: str
    pdf_name: str
    expected_year: int
    expected_month: int
    db_year: int | None
    db_month: int | None
    gap_months: int | None
    status: str


def pdf_date_from_name(name: str) -> date | None:
    m = _PDF_NAME_RE.match(name or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def expected_covered_month(as_of: date) -> tuple[int, int]:
    """as_of（ファイル名の開示日）→ 右端列が前暦月のときの (year, month)。"""
    if as_of.month == 1:
        return as_of.year - 1, 12
    return as_of.year, as_of.month - 1


def ym_index(year: int, month: int) -> int:
    return year * 12 + month


def gap_months(
    expected: tuple[int, int],
    db: tuple[int, int] | None,
) -> int | None:
    if db is None:
        return None
    return ym_index(expected[0], expected[1]) - ym_index(db[0], db[1])


def classify(gap: int | None) -> str:
    if gap is None:
        return "missing"
    if gap > 0:
        return "stale"
    return "ok"


def is_issuer_dir_name(name: str) -> bool:
    return bool(_ISSUER_DIR_RE.match(name or ""))


def connect_sqlite_ro(db_path: Path, timeout: float = 8) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def resolve_db_path(
    db_arg: str | None,
    *,
    repo_root: Path | None = None,
    environ: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> Path:
    root = JOBS.parent if repo_root is None else repo_root
    if db_arg:
        raw = db_arg.strip()
        if raw.startswith("sqlite:"):
            parsed = sqlite_path_from_url(raw, repo_root=root)
            if parsed is None:
                raise ValueError(f"not a sqlite file URL: {raw}")
            return parsed
        return Path(raw)
    url = resolve_database_url(
        environ=environ, dotenv_path=dotenv_path, repo_root=root
    )
    parsed = sqlite_path_from_url(url, repo_root=root)
    if parsed is None:
        raise ValueError(f"DATABASE_URL is not sqlite: {url}")
    return parsed


def scan_latest_pdfs(dest: Path) -> dict[str, PdfHit]:
    """code4 → latest dated PDF. dest は monthly/。1段だけ見る。"""
    out: dict[str, PdfHit] = {}
    if not dest.is_dir():
        raise FileNotFoundError(f"dest not a directory: {dest}")
    try:
        entries = list(dest.iterdir())
    except OSError as e:
        raise FileNotFoundError(f"dest unreadable: {dest}: {e}") from e
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        code = display_code(entry.name)
        if not is_issuer_dir_name(code):
            continue
        best: tuple[date, str] | None = None
        try:
            files = list(entry.iterdir())
        except OSError:
            continue
        for path in files:
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            name = path.name
            if name.endswith(".part"):
                continue
            pdf_date = pdf_date_from_name(name)
            if pdf_date is None:
                continue
            key = (pdf_date, name)
            if best is None or key > best:
                best = key
        if best is None:
            continue
        out[code] = PdfHit(code=code, pdf_date=best[0], pdf_name=best[1])
    return out


def load_db_latest(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """monthly_values.code (5桁) → 値のある最大 (year, month)。"""
    try:
        rows = conn.execute(
            "SELECT code, year, month FROM monthly_values"
        ).fetchall()
    except sqlite3.OperationalError as e:
        raise ValueError(f"monthly_values unreadable: {e}") from e
    out: dict[str, tuple[int, int]] = {}
    for code, year, month in rows:
        if not code or year is None or month is None:
            continue
        try:
            ym = (int(year), int(month))
        except (TypeError, ValueError):
            continue
        if ym[1] < 1 or ym[1] > 12:
            continue
        key = str(code)
        prev = out.get(key)
        if prev is None or ym_index(ym[0], ym[1]) > ym_index(prev[0], prev[1]):
            out[key] = ym
    return out


def build_rows(
    pdfs: dict[str, PdfHit],
    db_latest: dict[str, tuple[int, int]],
    *,
    only_codes: set[str] | None = None,
) -> list[ReportRow]:
    rows: list[ReportRow] = []
    for code, hit in pdfs.items():
        if code in PENDING_IGNORE_CODES:
            continue
        if only_codes is not None and code not in only_codes:
            continue
        expected = expected_covered_month(hit.pdf_date)
        code5 = jq_lookup_code(code)
        db = db_latest.get(code5)
        gap = gap_months(expected, db)
        status = classify(gap)
        rows.append(
            ReportRow(
                code=code,
                code5=code5,
                pdf_date=hit.pdf_date.isoformat(),
                pdf_name=hit.pdf_name,
                expected_year=expected[0],
                expected_month=expected[1],
                db_year=None if db is None else db[0],
                db_month=None if db is None else db[1],
                gap_months=gap,
                status=status,
            )
        )
    status_rank = {"missing": 0, "stale": 1, "ok": 2}

    def sort_key(row: ReportRow) -> tuple:
        gap = row.gap_months if row.gap_months is not None else 10**9
        return (status_rank.get(row.status, 9), -gap, row.code)

    rows.sort(key=sort_key)
    return rows


def filter_stale(rows: list[ReportRow]) -> list[ReportRow]:
    return [r for r in rows if r.status in ("stale", "missing")]


def summarize(rows: list[ReportRow]) -> dict[str, int]:
    counts = {"scanned": len(rows), "ok": 0, "stale": 0, "missing": 0}
    for row in rows:
        if row.status in counts:
            counts[row.status] += 1
    return counts


def _ym_label(year: int | None, month: int | None) -> str:
    if year is None or month is None:
        return "-"
    return f"{year:04d}-{month:02d}"


def format_text_table(
    rows: list[ReportRow],
    *,
    dest: Path,
    db_path: Path,
    summary: dict[str, int],
) -> str:
    lines = [
        "# TDnet monthly 未投入レポート",
        f"# dest={dest}  db={db_path}  (sqlite mode=ro)",
        *[f"# {line}" for line in HEURISTIC_LINES],
        "",
        (
            f"{'code':<6} {'pdf_date':<12} {'expected':<10} {'db_latest':<10} "
            f"{'gap':>4}  {'status':<8} pdf"
        ),
    ]
    for row in rows:
        gap = "-" if row.gap_months is None else str(row.gap_months)
        mark = "*" if row.status in ("stale", "missing") else " "
        status = row.status.upper() if row.status != "ok" else "ok"
        lines.append(
            f"{mark}{row.code:<5} {row.pdf_date:<12} "
            f"{_ym_label(row.expected_year, row.expected_month):<10} "
            f"{_ym_label(row.db_year, row.db_month):<10} "
            f"{gap:>4}  {status:<8} {row.pdf_name}"
        )
    lines.append("")
    lines.append(
        "scanned={scanned} stale={stale} missing={missing} ok={ok}".format(
            **summary
        )
    )
    return "\n".join(lines) + "\n"


def format_json(
    rows: list[ReportRow],
    *,
    dest: Path,
    db_path: Path,
    summary: dict[str, int],
) -> str:
    payload = {
        "heuristic": list(HEURISTIC_LINES),
        "dest": str(dest),
        "db": str(db_path),
        "readonly": True,
        "rows": [asdict(r) for r in rows],
        "summary": summary,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def parse_codes(raw: list[str] | None) -> set[str] | None:
    if not raw:
        return None
    out: set[str] = set()
    for item in raw:
        for part in item.split(","):
            token = part.strip().upper()
            if not token:
                continue
            out.add(display_code(token))
    return out or None


def run_report(
    *,
    dest: Path,
    db_path: Path,
    only_stale: bool = False,
    only_codes: set[str] | None = None,
    as_json: bool = False,
) -> str:
    pdfs = scan_latest_pdfs(dest)
    conn = connect_sqlite_ro(db_path)
    try:
        db_latest = load_db_latest(conn)
    finally:
        conn.close()
    rows = build_rows(pdfs, db_latest, only_codes=only_codes)
    if only_stale:
        rows = filter_stale(rows)
    summary = summarize(rows)
    if as_json:
        return format_json(rows, dest=dest, db_path=db_path, summary=summary)
    return format_text_table(rows, dest=dest, db_path=db_path, summary=summary)


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only report of TDnet monthly PDFs not yet in monthly_values"
    )
    ap.add_argument(
        "--dest",
        default=DEFAULT_DEST,
        help=f"R2 monthly directory (default {DEFAULT_DEST})",
    )
    ap.add_argument(
        "--db",
        default=None,
        help="sqlite file (default DATABASE_URL / repo stock.db)",
    )
    ap.add_argument("--json", action="store_true", help="JSON instead of a text table")
    ap.add_argument(
        "--only-stale",
        action="store_true",
        help="print stale and missing issuers only",
    )
    ap.add_argument(
        "--code",
        action="append",
        default=None,
        help="issuer code (4 or 5 char; repeat or comma-separate)",
    )
    args = ap.parse_args(argv)
    stream = sys.stdout if out is None else out
    dest = Path(args.dest)
    try:
        db_path = resolve_db_path(args.db)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        check_r2_mount(dest)
    except MountDisconnected as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError:
        pass
    if not dest.is_dir():
        print(f"dest not found: {dest}", file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2
    try:
        text = run_report(
            dest=dest,
            db_path=db_path,
            only_stale=args.only_stale,
            only_codes=parse_codes(args.code),
            as_json=args.json,
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as e:
        print(str(e), file=sys.stderr)
        return 2
    stream.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

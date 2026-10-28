#!/usr/bin/env python3
"""同じ開示の TDnet ZIP と短信 PDF をスクラッチ SQLite で突き合わせる（KAB-173）。

本番 stock.db も IR BANK も、フラグ無しでは呼ばない。ZIP / HTML（または PDF）は
作業ディレクトリに置く。テストは tests/xbrl/test_tdnet_pdf_verify.py が合成ペアだけ使う。

  python tools/tdnet_pdf/verify_financials.py --work DIR --scratch PATH --source-db stock.db
  python tools/tdnet_pdf/verify_financials.py --work DIR --scratch PATH --source-db stock.db --fetch-pdf
  python tools/tdnet_pdf/verify_financials.py --work DIR --scratch PATH --source-db stock.db --convert

DIR の ``*.zip`` の名前の後ろ 14 桁が開示番号。同じ番号の ``.html`` / ``.pdf`` を探す。
比較キーは (std_item, is_consolidated, period_end, value_kind, range_side, target_period)。
1Q / 3Q の CF が ZIP にだけあるのは expected_zip_only。
"""
from __future__ import annotations

import argparse
import calendar
import gzip
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PDF_URL = "https://api.irbank.net/api/market-files/v1/tdnet/{no}.pdf"
USER_AGENT = "stock-analyzer/1.0 (personal; tools/tdnet_pdf/verify_financials)"
_NO = re.compile(r"(\d{14})")
_FY = re.compile(r"(\d{4})年(\d{1,2})月期")
_FY_REIWA = re.compile(r"令和(\d{1,2})年(\d{1,2})月期")
_TITLE_PERIOD = (
    (re.compile(r"第\s*[1１]\s*四半期"), "Q1"),
    (re.compile(r"第\s*[2２]\s*四半期|中間"), "Q2"),
    (re.compile(r"第\s*[3３]\s*四半期"), "Q3"),
)


def period_from_title(title: str, period: str | None) -> str | None:
    """ZIP の filing に期が無い（period_type が空）ときは題名の「第1四半期」「中間期」から。"""
    if period:
        return period
    text = unicodedata.normalize("NFKC", title or "")
    for pattern, token in _TITLE_PERIOD:
        if pattern.search(text):
            return token
    return None
_ZIP_ID = re.compile(r"(0812|0912)(\d{14})")
CF_ITEMS = frozenset({"operating_cf", "investing_cf", "financing_cf", "capex", "depreciation", "cash_end"})
MONEY_TOL = Decimal("1")
RATIO_TOL = Decimal("0.0005")
SHARE_TOL = Decimal("0.05")
PER_SHARE = frozenset({"eps_basic", "eps_diluted", "dps", "bps"})
RATIO = frozenset({"equity_ratio", "roe"})


@dataclass(frozen=True)
class Slot:
    std_item: str
    is_consolidated: int
    period_end: str
    value_kind: str
    range_side: str
    target_period: str


@dataclass
class PairResult:
    disclosure_no: str
    code: str
    title: str
    zip_status: str
    pdf_status: str
    matched: int = 0
    mismatched: int = 0
    pdf_only: int = 0
    zip_only: int = 0
    expected_zip_only: int = 0
    details: list[dict] | None = None


def disclosure_no_of(path: Path) -> str:
    text = path.name
    found = _ZIP_ID.search(text) or _NO.search(text)
    if found is None:
        raise ValueError(f"14 桁の開示番号がファイル名に無い: {path.name}")
    return found.group(2) if found.lastindex == 2 else found.group(1)


def last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def fy_start(end: date) -> date:
    prev = date(end.year - 1, end.month, end.day)
    return prev + timedelta(days=1)


def has_complete_dates(st) -> bool:
    """J-Quants の期日がそろっている。そろっているときは ZIP の期末で上書きしない（KAB-219）。"""
    return (
        st is not None
        and getattr(st, "period_start", None) is not None
        and getattr(st, "period_end", None) is not None
        and getattr(st, "fiscal_year_start", None) is not None
        and getattr(st, "fiscal_year_end", None) is not None
    )


def align_statement_from_zip(st, filing) -> None:
    """ZIP の本表期末を statements に写す。題名の月末推定（20 日決算など）を上書きする。"""
    period_end = filing.fiscal_year_end
    if period_end is None:
        return
    token = st.type_of_period or "FY"
    months = {"1Q": 3, "2Q": 6, "3Q": 9}.get(token)
    if months is None:
        start = fy_start(period_end)
        st.period_start = start
        st.period_end = period_end
        st.fiscal_year_start = start
        st.fiscal_year_end = period_end
        return
    start = add_months(period_end + timedelta(days=1), -months)
    fy_end = add_months(start, 12) - timedelta(days=1)
    st.period_start = start
    st.period_end = period_end
    st.fiscal_year_start = start
    st.fiscal_year_end = fy_end


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def type_of_document(title: str, period: str, standard: str | None) -> str:
    if ("業績予想" in title and "修正" in title) or "EarnForecastRevision" in title:
        return "EarnForecastRevision"
    if ("配当予想" in title and "修正" in title) or "DividendForecastRevision" in title:
        return "DividendForecastRevision"
    token = "IFRS" if (standard or "").upper() == "IFRS" else "JP"
    consol = "非連結" not in title and "NonConsolidated" not in title
    mid = "Consolidated" if consol else "NonConsolidated"
    prefix = {"Q1": "1Q", "Q2": "2Q", "Q3": "3Q", "FY": "FY"}.get(period or "", "FY")
    return f"{prefix}FinancialStatements_{mid}_{token}"


def type_of_period(doc: str, period: str | None) -> str:
    if doc.startswith("1Q"):
        return "1Q"
    if doc.startswith("2Q"):
        return "2Q"
    if doc.startswith("3Q"):
        return "3Q"
    if doc.startswith("FY"):
        return "FY"
    return {"Q1": "1Q", "Q2": "2Q", "Q3": "3Q", "FY": "FY"}.get(period or "FY", "FY")


def dates_from_title(title: str, period: str | None) -> tuple[date, date, date, date] | None:
    text = unicodedata.normalize("NFKC", title or "")
    found = _FY.search(text)
    if found is not None:
        year = int(found.group(1))
    else:
        found = _FY_REIWA.search(text)
        if found is None:
            return None
        year = 2018 + int(found.group(1))
    end = last_day(year, int(found.group(2)))
    start = fy_start(end)
    token = period or "FY"
    if token == "Q1":
        period_end = add_months(start, 3) - timedelta(days=1)
    elif token == "Q2":
        period_end = add_months(start, 6) - timedelta(days=1)
    elif token == "Q3":
        period_end = add_months(start, 9) - timedelta(days=1)
    else:
        period_end = end
    return start, period_end, start, end


_ZIP_DATE = re.compile(r"^(\d{8})_")


def submitted_at_of(*, statement, zip_path: Path) -> str:
    """提出日は statements を優先し、無ければ ZIP 名の先頭 8 桁。開示番号からは作らない（KAB-219）。"""
    if statement is not None and getattr(statement, "disclosed_date", None) is not None:
        clock = (statement.disclosed_time or "15:00:00").strip()
        if len(clock) == 5:
            clock = f"{clock}:00"
        day = statement.disclosed_date
        iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
        return f"{iso} {clock[:8]}"
    found = _ZIP_DATE.match(zip_path.name)
    if found is not None:
        raw = found.group(1)
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} 15:00:00"
    raise ValueError(f"提出日が statements にも ZIP 名にも無い: {zip_path.name}")


def close_enough(item: str, left: Decimal, right: Decimal) -> bool:
    if left == right:
        return True
    delta = abs(left - right)
    if item in RATIO:
        return delta <= RATIO_TOL
    if item in PER_SHARE:
        return delta <= SHARE_TOL
    scale = max(abs(left), abs(right), Decimal("1"))
    return delta <= MONEY_TOL or delta / scale <= Decimal("0.001")


def slot_of(row) -> Slot:
    period_end = row.period_end.isoformat() if hasattr(row.period_end, "isoformat") else str(row.period_end)
    return Slot(
        std_item=row.std_item,
        is_consolidated=int(row.is_consolidated or 0),
        period_end=period_end,
        value_kind=row.value_kind or "actual",
        range_side=row.range_side or "point",
        target_period=row.target_period or "period",
    )


def financials_of(db, source: str, doc_id: str) -> dict[Slot, Decimal]:
    from app.models import XbrlFiling, XbrlFinancial, XbrlStdItem

    filing = db.query(XbrlFiling).filter_by(source=source, doc_id=doc_id).one_or_none()
    if filing is None:
        return {}
    found: dict[Slot, Decimal] = {}
    query = (
        db.query(XbrlFinancial, XbrlStdItem)
        .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
        .filter(XbrlFinancial.filing_id == filing.id)
    )
    for row, item in query:
        row.std_item = item.code
        found[slot_of(row)] = Decimal(str(row.value))
    return found


def expected_cf_gap(period: str | None, item: str) -> bool:
    return item in CF_ITEMS and period in {"Q1", "Q3", "1Q", "3Q"}


def compare_maps(
    zip_rows: dict[Slot, Decimal],
    pdf_rows: dict[Slot, Decimal],
    *,
    period: str | None,
) -> tuple[Counter, list[dict]]:
    counts: Counter = Counter()
    details: list[dict] = []
    keys = set(zip_rows) | set(pdf_rows)
    for key in sorted(keys, key=lambda s: (s.std_item, s.period_end, s.value_kind, s.target_period, s.range_side)):
        left, right = zip_rows.get(key), pdf_rows.get(key)
        rec = asdict(key)
        if left is not None and right is not None:
            if close_enough(key.std_item, left, right):
                counts["matched"] += 1
                rec["status"] = "matched"
            else:
                counts["mismatched"] += 1
                rec["status"] = "mismatched"
            rec["zip"] = str(left)
            rec["pdf"] = str(right)
        elif left is not None:
            rec["zip"] = str(left)
            if expected_cf_gap(period, key.std_item):
                counts["expected_zip_only"] += 1
                rec["status"] = "expected_zip_only"
            else:
                counts["zip_only"] += 1
                rec["status"] = "zip_only"
        else:
            counts["pdf_only"] += 1
            rec["status"] = "pdf_only"
            rec["pdf"] = str(right)
        details.append(rec)
    return counts, details


def copy_master(source_db: Path, scratch: Path) -> None:
    import sqlite3

    scratch.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(scratch)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (str(source_db.resolve()),))
        for table in ("stocks", "statements"):
            src_cols = [r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")]
            dest_cols = {r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")}
            cols = [c for c in src_cols if c in dest_cols]
            if not cols:
                continue
            col_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
            conn.execute(f"DELETE FROM main.{table}")
            conn.execute(f"INSERT INTO main.{table} ({col_sql}) SELECT {col_sql} FROM src.{table}")
        conn.commit()
    finally:
        conn.close()


def ensure_statement(db, *, no: str, code: str, title: str, submitted_at: str, period: str | None, standard: str | None):
    from app.models import Statement

    period = period_from_title(title, period)
    row = db.get(Statement, no)
    if row is not None and row.period_start and row.period_end and row.fiscal_year_start and row.fiscal_year_end:
        if not row.type_of_document:
            row.type_of_document = type_of_document(title, period or "FY", standard)
        if not row.type_of_period:
            row.type_of_period = type_of_period(row.type_of_document, period)
        return row
    doc = type_of_document(title, period or "FY", standard)
    token = type_of_period(doc, period)
    guessed = dates_from_title(title, {"1Q": "Q1", "2Q": "Q2", "3Q": "Q3", "FY": "FY"}[token])
    if guessed is None:
        raise ValueError(f"statements に日付が無く題名からも取れない: {no} {title}")
    period_start, period_end, fy_s, fy_e = guessed
    day, clock = submitted_at.split(" ", 1)
    if row is None:
        row = Statement(disclosure_no=no, code=code)
        db.add(row)
    row.code = code
    row.disclosed_date = date.fromisoformat(day)
    row.disclosed_time = clock[:8]
    row.type_of_period = token
    row.type_of_document = doc
    row.period_start = period_start
    row.period_end = period_end
    row.fiscal_year_start = fy_s
    row.fiscal_year_end = fy_e
    return row


def put_html(db, *, no: str, code: str, html: str, disclosed_date: date | None, disclosed_time: str | None, title: str):
    from app.models import TdnetPdfDocument

    raw = html.encode("utf-8")
    row = db.get(TdnetPdfDocument, no)
    if row is None:
        row = TdnetPdfDocument(disclosure_no=no)
        db.add(row)
    row.code = code
    row.disclosed_date = disclosed_date
    row.disclosed_time = disclosed_time
    row.title = (title or "")[:200]
    row.status = "ok"
    row.html_gz = gzip.compress(raw)
    row.bytes = len(raw)
    row.sha256 = hashlib.sha256(raw).hexdigest()
    row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.converter = "svg2"
    row.error = None


def fetch_pdf(no: str, dest: Path) -> Path:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    url = PDF_URL.format(no=no)
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        res = client.get(url)
    if res.status_code == 404:
        raise FileNotFoundError(f"IR BANK 404 {url}")
    if res.status_code in (429, 503):
        raise RuntimeError(f"IR BANK rate limited {res.status_code}")
    res.raise_for_status()
    dest.write_bytes(res.content)
    return dest


def convert_one(pdf_path: Path, html_path: Path) -> Path:
    from tools.tdnet_pdf.convert import convert_pdf

    converted = convert_pdf(pdf_path)
    html_path.write_text(converted.html, encoding="utf-8")
    return html_path


def parse_zip(db, zip_path: Path, *, code: str, no: str, title: str, submitted_at: str):
    from app.config import XBRL_CACHE_DIR
    from app.xbrl.normalize import normalize_filing
    from app.xbrl.store import parse_zip_into_filing

    doc_id = _ZIP_ID.search(zip_path.name)
    xid = doc_id.group(0) if doc_id else f"0812{no}"
    filing = parse_zip_into_filing(
        db,
        zip_path=zip_path,
        code=code,
        doc_id=xid,
        title=title,
        raw_path=str(zip_path),
        raw_sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        submitted_at=submitted_at,
        cache_dir=Path(XBRL_CACHE_DIR),
    )
    if filing.parse_status == "parsed":
        normalize_filing(db, filing)
        db.commit()
    return filing


def code_of_zip_name(name: str, fallback: str | None) -> str:
    parts = name.split("_")
    if len(parts) >= 2 and re.fullmatch(r"[0-9][0-9A-Z]{3}", parts[1]):
        return parts[1] + "0"
    if fallback:
        return fallback
    raise ValueError(f"銘柄コードが ZIP 名から取れない: {name}")


def title_of_zip_name(name: str) -> str:
    stem = Path(name).stem
    parts = stem.split("_")
    if len(parts) >= 3:
        return "_".join(parts[2:-1]) if _ZIP_ID.search(parts[-1]) else "_".join(parts[2:])
    return stem


def pairs_in(work: Path) -> list[tuple[str, Path, Path | None, Path | None]]:
    found: dict[str, dict[str, Path]] = {}
    for path in sorted(work.iterdir()):
        if not path.is_file():
            continue
        try:
            no = disclosure_no_of(path)
        except ValueError:
            continue
        bucket = found.setdefault(no, {})
        suffix = path.suffix.lower()
        if suffix == ".zip":
            bucket["zip"] = path
        elif suffix in {".html", ".htm"}:
            bucket["html"] = path
        elif suffix == ".pdf":
            bucket["pdf"] = path
    rows = []
    for no, files in sorted(found.items()):
        if "zip" not in files:
            continue
        rows.append((no, files["zip"], files.get("html"), files.get("pdf")))
    return rows


def run_pair(db, *, no: str, zip_path: Path, html_path: Path | None, pdf_path: Path | None, work: Path, fetch: bool, convert: bool) -> PairResult:
    from app.models import Statement, Stock
    from app.xbrl.context import classify_title
    from app.xbrl.pdf import sync_pdf_filing

    title = title_of_zip_name(zip_path.name)
    meta = classify_title(title)
    golden = db.get(Statement, no)
    submitted = submitted_at_of(statement=golden, zip_path=zip_path)
    code = (golden.code if golden is not None else None) or code_of_zip_name(zip_path.name, None)
    if db.get(Stock, code) is None:
        raise ValueError(f"stocks に無い: {code}（--source-db にマスタを入れてください）")
    had_jq_dates = has_complete_dates(golden)
    ensure_statement(
        db,
        no=no,
        code=code,
        title=title,
        submitted_at=submitted,
        period=meta.get("period_type"),
        standard=meta.get("accounting_standard"),
    )
    db.commit()
    zip_filing = parse_zip(db, zip_path, code=code, no=no, title=title, submitted_at=submitted)
    zip_status = zip_filing.parse_status
    st = db.get(Statement, no)
    if st is not None and zip_status == "parsed" and not had_jq_dates:
        align_statement_from_zip(st, zip_filing)
        db.commit()
    if html_path is None:
        if pdf_path is None:
            pdf_path = work / f"{no}.pdf"
            if fetch and not pdf_path.is_file():
                fetch_pdf(no, pdf_path)
        if pdf_path is not None and pdf_path.is_file():
            html_path = work / f"{no}.html"
            if convert or not html_path.is_file():
                convert_one(pdf_path, html_path)
    pdf_status = "absent"
    if html_path is not None and html_path.is_file():
        st = db.get(Statement, no)
        put_html(
            db,
            no=no,
            code=code,
            html=html_path.read_text(encoding="utf-8"),
            disclosed_date=st.disclosed_date if st is not None else None,
            disclosed_time=st.disclosed_time if st is not None else None,
            title=title,
        )
        db.commit()
        outcome = sync_pdf_filing(db, no, reparse=True, ignore_zip=True)
        pdf_status = outcome.value
    zip_rows = financials_of(db, "tdnet", zip_filing.doc_id)
    pdf_rows = financials_of(db, "tdnet_pdf", no)
    period = meta.get("period_type")
    counts, details = compare_maps(zip_rows, pdf_rows, period=period)
    return PairResult(
        disclosure_no=no,
        code=code,
        title=title,
        zip_status=zip_status,
        pdf_status=pdf_status,
        matched=counts["matched"],
        mismatched=counts["mismatched"],
        pdf_only=counts["pdf_only"],
        zip_only=counts["zip_only"],
        expected_zip_only=counts["expected_zip_only"],
        details=details,
    )


def print_report(results: list[PairResult]) -> None:
    print(
        f"{'no':<14} {'code':<6} {'zip':<8} {'pdf':<10} "
        f"{'match':>5} {'diff':>4} {'pdf+':>5} {'zip+':>5} {'cf-ok':>5}  title"
    )
    totals: Counter = Counter()
    for row in results:
        totals["matched"] += row.matched
        totals["mismatched"] += row.mismatched
        totals["pdf_only"] += row.pdf_only
        totals["zip_only"] += row.zip_only
        totals["expected_zip_only"] += row.expected_zip_only
        print(
            f"{row.disclosure_no} {row.code:<6} {row.zip_status:<8} {row.pdf_status:<10} "
            f"{row.matched:5d} {row.mismatched:4d} {row.pdf_only:5d} {row.zip_only:5d} "
            f"{row.expected_zip_only:5d}  {row.title[:40]}"
        )
        interesting = [d for d in (row.details or []) if d["status"] in {"mismatched", "pdf_only", "zip_only"}]
        for rec in interesting[:40]:
            print(
                f"    {rec['status']:<12} {rec['std_item']:<18} "
                f"kind={rec['value_kind']:<18} tgt={rec['target_period']:<8} "
                f"end={rec['period_end']} zip={rec.get('zip','-')} pdf={rec.get('pdf','-')}"
            )
        leftover = max(0, len(interesting) - 40)
        if leftover:
            print(f"    … {leftover} more")
    print(
        "total "
        f"matched={totals['matched']} mismatched={totals['mismatched']} "
        f"pdf_only={totals['pdf_only']} zip_only={totals['zip_only']} "
        f"expected_zip_only={totals['expected_zip_only']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True, type=Path, help="ZIP / HTML / PDF を置く作業ディレクトリ")
    parser.add_argument("--scratch", required=True, type=Path, help="スクラッチ SQLite。本番 stock.db は拒否")
    parser.add_argument("--source-db", required=True, type=Path, help="stocks / statements の読み元（ゴールデン可）")
    parser.add_argument("--out", type=Path, help="JSON の書き出し先")
    parser.add_argument("--fetch-pdf", action="store_true", help="欠けた PDF を IR BANK から取る")
    parser.add_argument("--convert", action="store_true", help="PDF があれば HTML に変換する")
    args = parser.parse_args(argv)

    scratch = args.scratch.expanduser().resolve()
    source = args.source_db.expanduser().resolve()
    work = args.work.expanduser().resolve()
    if scratch == source or scratch.name == "stock.db" and scratch.parent == ROOT:
        raise SystemExit(f"本番 DB へは書かない: {scratch}")
    if not source.is_file():
        raise SystemExit(f"source-db が無い: {source}")
    work.mkdir(parents=True, exist_ok=True)

    os.environ["DATABASE_URL"] = f"sqlite:///{scratch}"
    from app.db import SessionLocal, init_db
    from app.xbrl.mappings import apply_mappings
    from app.xbrl.parser import reset_taxonomy_caches

    reset_taxonomy_caches()
    init_db()
    copy_master(source, scratch)

    db = SessionLocal()
    results: list[PairResult] = []
    try:
        apply_mappings(db)
        rows = pairs_in(work)
        if not rows:
            raise SystemExit(f"ZIP が無い: {work}")
        for no, zip_path, html_path, pdf_path in rows:
            try:
                results.append(
                    run_pair(
                        db,
                        no=no,
                        zip_path=zip_path,
                        html_path=html_path,
                        pdf_path=pdf_path,
                        work=work,
                        fetch=args.fetch_pdf,
                        convert=args.convert,
                    )
                )
            except Exception as exc:
                db.rollback()
                results.append(
                    PairResult(
                        disclosure_no=no,
                        code="?",
                        title=zip_path.name,
                        zip_status="error",
                        pdf_status=str(exc)[:200],
                    )
                )
                print(f"pair failed no={no} err={exc}", flush=True)
    finally:
        db.close()
    print_report(results)
    payload = {
        "pairs": [
            {**{k: getattr(row, k) for k in PairResult.__dataclass_fields__ if k != "details"}, "details": row.details}
            for row in results
        ]
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = any(row.mismatched or row.zip_status == "error" or row.pdf_status in {"failed", "error"} for row in results)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

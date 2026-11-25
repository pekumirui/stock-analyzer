#!/usr/bin/env python3
"""IR BANK が保管する TDnet の開示 PDF → HTML → tdnet_pdf_documents（KAB-164。docs/jobs/xbrl.md「過去の短信を PDF から HTML にする」）。

指定した銘柄の statements（J-Quants の開示。2021-08〜）のうち、原文の無い回を新しい順に取る。
原文がある回は飛ばす: XBRL ZIP の HTML（xbrl_documents。ZIP 名の後ろ 14 桁が disclosure_no）と、
この表に ok / missing で入っている回。1 件ごとに --sleep 秒あける。IR BANK が 429 / 503 を返したら
上限とみなしてそこで止める（終了コード 0）。1 件ごとに commit するので、途中で止めても続きから再開できる。

  python jobs/xbrl/tdnet_pdf_ingest.py --code 5933 --code 7203
  python jobs/xbrl/tdnet_pdf_ingest.py --code 5933 --limit 50 --sleep 2
  python jobs/xbrl/tdnet_pdf_ingest.py --all --limit 1000
  python jobs/xbrl/tdnet_pdf_ingest.py --code 5933 --dry-run       # 対象の件数と先頭だけ。取りに行かない
  python jobs/xbrl/tdnet_pdf_ingest.py --code 5933 --retry-failed  # failed も取り直す
  python jobs/xbrl/tdnet_pdf_ingest.py --redo nos.txt              # 一覧の回を今の変換器で変換し直す（KAB-572）

最後に 1 行: tdnet_pdf stopped=<done|limit|rate_limited> ok=… missing=… failed=… remaining=…
remaining は実行後に targets() を取り直した件数。failed（この実行で failed にした件数）とは別で、
ok/missing/failed 済みと XBRL 原文のある回は入らない。--retry-failed のときだけ failed 済みも残件に入る。
表 tdnet_pdf_documents はデプロイの jobs/migrate.py が作る。ここでは作らない（無ければ no such table で落ちる）。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.db import SessionLocal, run_sqlite_write
from app.models import Statement, Stock, TdnetPdfDocument, XbrlDocument, XbrlFiling
from app.xbrl.pdf.convert import CONVERTER, convert_pdf, utf8_safe

PDF_URL = "https://api.irbank.net/api/market-files/v1/tdnet/{no}.pdf"
USER_AGENT = "stock-analyzer/1.0 (personal; tools/tdnet_pdf)"
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_LINEARIZED_LEN = re.compile(rb"/Linearized\s+[0-9.]+\s*/L\s+([0-9]+)")


class RateLimited(RuntimeError):
    pass


class Missing(RuntimeError):
    pass


def code5(raw: str) -> str:
    s = raw.strip().upper()
    if not _CODE_RE.fullmatch(s):
        raise ValueError(f"code は 4〜5 桁の英数字です: {raw}")
    return s + "0" if len(s) == 4 else s


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def targets(db: Session, codes: list[str] | None, *, retry_failed: bool = False) -> list[Statement]:
    """原文の無い開示。新しい順。codes が None なら全銘柄。"""
    q = db.query(Statement).filter(Statement.disclosure_no.isnot(None))
    q = q.filter(exists().where(Stock.code == Statement.code))
    if codes is not None:
        q = q.filter(Statement.code.in_(codes))
    done_status = ["ok", "missing"] if retry_failed else ["ok", "missing", "failed"]
    q = q.filter(~exists().where(
        (TdnetPdfDocument.disclosure_no == Statement.disclosure_no) & TdnetPdfDocument.status.in_(done_status)
    ))
    rows = q.order_by(Statement.disclosed_date.desc(), Statement.disclosure_no.desc()).all()
    xq = (
        db.query(XbrlFiling.doc_id)
        .filter(XbrlFiling.source == "tdnet")
        .filter(exists().where(XbrlDocument.filing_id == XbrlFiling.id))
    )
    if codes is not None:
        xq = xq.filter(XbrlFiling.code.in_(codes))
    have_html = {doc_id[-14:] for (doc_id,) in xq}
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.disclosure_no in have_html or r.disclosure_no in seen:
            continue
        seen.add(r.disclosure_no)
        out.append(r)
    return out


def redo_targets(db: Session, nos: list[str]) -> list[Statement]:
    stale = ~exists().where(
        (TdnetPdfDocument.disclosure_no == Statement.disclosure_no) & (TdnetPdfDocument.converter == CONVERTER)
    )
    rows = (
        db.query(Statement).filter(Statement.disclosure_no.in_(nos)).filter(stale)
        .order_by(Statement.disclosed_date.desc(), Statement.disclosure_no.desc()).all()
    )
    out: dict[str, Statement] = {}
    for r in rows:
        out.setdefault(r.disclosure_no, r)
    return list(out.values())


def fetch_pdf(client: httpx.Client, no: str) -> bytes:
    r = client.get(PDF_URL.format(no=no))
    if r.status_code in (429, 503):
        raise RateLimited(f"http={r.status_code}")
    if r.status_code == 404:
        raise Missing("http=404")
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"not a pdf (content-type={r.headers.get('content-type')})")
    m = _LINEARIZED_LEN.search(r.content[:1024])
    if m and len(r.content) < int(m.group(1)):
        # IR BANK に途中までしか無い PDF がある（20260212556420）。pdfium も pdfminer も読めない
        raise ValueError(f"truncated pdf (bytes={len(r.content)} linearized_L={m.group(1)})")
    return r.content


def _save(db: Session, row: TdnetPdfDocument) -> None:
    run_sqlite_write(db, lambda: db.merge(row))


def ingest_one(db: Session, st: Statement, pdf: bytes) -> TdnetPdfDocument:
    conv = convert_pdf(pdf)
    html = utf8_safe(conv.html)
    raw = html.encode("utf-8")
    title = conv.title or st.type_of_document
    if title:
        title = utf8_safe(title)
    row = TdnetPdfDocument(
        disclosure_no=st.disclosure_no, code=st.code, disclosed_date=st.disclosed_date,
        disclosed_time=st.disclosed_time, title=title, status="ok", error=None,
        html_gz=gzip.compress(raw, compresslevel=6, mtime=0), bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), pages=conv.pages,
        pdf_bytes=len(pdf), pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        converter=CONVERTER, created_at=_now(),
    )
    _save(db, row)
    return row


def _mark(db: Session, st: Statement, status: str, error: str) -> None:
    _save(db, TdnetPdfDocument(
        disclosure_no=st.disclosure_no, code=st.code, disclosed_date=st.disclosed_date,
        disclosed_time=st.disclosed_time, title=st.type_of_document, status=status, error=error[:500],
        created_at=_now(),
    ))


def run(
    db: Session,
    codes: list[str] | None,
    *,
    limit: int | None = None,
    sleep: float = 1.0,
    retry_failed: bool = False,
    redo: list[str] | None = None,
    client: httpx.Client | None = None,
    log=print,
) -> dict:
    def pending() -> list[Statement]:
        return redo_targets(db, redo) if redo is not None else targets(db, codes, retry_failed=retry_failed)

    todo = pending()
    stats = {"ok": 0, "missing": 0, "failed": 0, "stopped": "done"}
    own = client is None
    client = client or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        for i, st in enumerate(todo):
            if limit is not None and i >= limit:
                stats["stopped"] = "limit"
                break
            if i and sleep:
                time.sleep(sleep)
            t0 = time.monotonic()
            try:
                pdf = fetch_pdf(client, st.disclosure_no)
                row = ingest_one(db, st, pdf)
                stats["ok"] += 1
                log(f"ok {st.disclosure_no} code={st.code} date={st.disclosed_date} pages={row.pages} "
                    f"kb={len(row.html_gz) // 1024} sec={time.monotonic() - t0:.1f} title={row.title}")
            except RateLimited as exc:
                stats["stopped"] = "rate_limited"
                log(f"rate_limited {st.disclosure_no} {exc}")
                break
            except Missing as exc:
                if redo is None:
                    _mark(db, st, "missing", str(exc))
                stats["missing"] += 1
                log(f"missing {st.disclosure_no} code={st.code} date={st.disclosed_date}")
            except Exception as exc:
                db.rollback()
                if redo is None:
                    _mark(db, st, "failed", f"{type(exc).__name__}: {exc}")
                stats["failed"] += 1
                log(f"failed {st.disclosure_no} code={st.code} err={type(exc).__name__}: {exc}")
    finally:
        if own:
            client.close()
    stats["remaining"] = len(pending())
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code", action="append", default=[], help="銘柄コード（4 桁か 5 桁）。複数可")
    ap.add_argument("--all", action="store_true", help="全銘柄（statements にある銘柄すべて）")
    ap.add_argument("--limit", type=int, help="この実行で取る最大件数")
    ap.add_argument("--sleep", type=float, default=1.0, help="1 件ごとの間隔（秒）。既定 1")
    ap.add_argument("--retry-failed", action="store_true", help="failed の回も取り直す")
    ap.add_argument("--redo", help="開示番号を 1 行 1 つ書いたファイル。今の変換器で変換し直していない回だけ取り直す")
    ap.add_argument("--dry-run", action="store_true", help="対象の件数と先頭 10 件だけ出す")
    args = ap.parse_args(argv)
    if bool(args.code) + args.all + bool(args.redo) != 1:
        ap.error("--code か --all か --redo のどれか 1 つを指定してください")
    codes = None if args.all or args.redo else sorted({code5(c) for c in args.code})
    redo = sorted({ln.strip() for ln in Path(args.redo).read_text().splitlines() if ln.strip()}) if args.redo else None

    db = SessionLocal()
    try:
        if args.dry_run:
            todo = redo_targets(db, redo) if redo is not None else targets(db, codes, retry_failed=args.retry_failed)
            for st in todo[:10]:
                print(f"todo {st.disclosure_no} code={st.code} date={st.disclosed_date} {st.type_of_document}")
            print(f"tdnet_pdf dry_run targets={len(todo)}")
            return 0
        stats = run(db, codes, limit=args.limit, sleep=args.sleep, retry_failed=args.retry_failed, redo=redo)
        print(
            f"tdnet_pdf stopped={stats['stopped']} ok={stats['ok']} missing={stats['missing']} "
            f"failed={stats['failed']} remaining={stats['remaining']}",
            flush=True,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

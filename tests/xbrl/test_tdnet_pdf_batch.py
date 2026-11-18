"""短信 PDF の一括取込の区切り（--batch）と database is locked のやり直し（KAB-435）。空 DB。"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app.xbrl import pdf
from app.db import SessionLocal
from app.models import Statement, TdnetPdfDocument, XbrlFiling
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import SyncOutcome, sync_pdf_filings

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.xbrl.test_tdnet_pdf_financials import (  # noqa: E402
    _dt,
    _fail,
    _ifrs_1q,
    _seed,
    _statement,
    _stock,
)

def _seed_reits(db, code: str, count: int) -> list[str]:
    """読まずに absent になる開示を count 件。新しい順の開示番号を返す。DB はファイルで 1 つなので code を分ける。"""
    _stock(db, code)
    numbers = []
    for index in range(count):
        day = date(2024, 1, count - index)
        no = f"202401{count - index:02d}5{code}"
        numbers.append(no)
        db.merge(Statement(
            disclosure_no=no, code=code, disclosed_date=day, disclosed_time="15:00",
            type_of_period="FY", type_of_document="REITFinancialStatements_Consolidated_JP",
            period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
            fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
        ))
        raw = b"<html></html>"
        db.merge(TdnetPdfDocument(
            disclosure_no=no, code=code, disclosed_date=day, disclosed_time="15:00",
            title="reit", status="ok", html_gz=gzip.compress(raw), bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), created_at=_dt(2024, 1, 3),
        ))
    db.commit()
    return numbers


def _batch_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("tdnet_pdf_financials batch ")]


def _locked() -> OperationalError:
    return OperationalError("UPDATE xbrl_filings", {}, Exception("database is locked"))


def _lock_first(monkeypatch, times: dict[str, int]) -> list[str]:
    """times[開示番号] 回だけ database is locked で落とす。呼ばれた順の開示番号を返す。"""
    real = pdf._sync
    calls: list[str] = []
    left = dict(times)

    def fake(db, number, zip_parsed, *, reparse):
        calls.append(number)
        if left.get(number, 0) > 0:
            left[number] -= 1
            raise _locked()
        return real(db, number, zip_parsed, reparse=reparse)

    monkeypatch.setattr(pdf, "_sync", fake)
    return calls


def _no_sleep(monkeypatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(pdf.time, "sleep", waits.append)
    return waits


def test_batch_prints_one_line_per_batch(capsys) -> None:
    db = SessionLocal()
    try:
        _seed_reits(db, "99971", 5)
        counts = sync_pdf_filings(db, codes=["99971"], limit=10, batch=2)
        lines = _batch_lines(capsys.readouterr().out)
        tail = "garbled=0 textless=0"
        head = "tdnet_pdf_financials batch"
        want = [
            f"{head} done=2/5 parsed=0 unchanged=0 failed=0 retired=0 absent=2 {tail} left=3",
            f"{head} done=4/5 parsed=0 unchanged=0 failed=0 retired=0 absent=4 {tail} left=1",
            f"{head} done=5/5 parsed=0 unchanged=0 failed=0 retired=0 absent=5 {tail} left=0",
        ]
        if lines != want:
            _fail(f"lines {lines}")
        if counts[SyncOutcome.ABSENT] != 5 or counts["remaining"] != 0:
            _fail(f"counts {dict(counts)}")
    finally:
        db.close()


def test_batch_respects_limit(capsys) -> None:
    db = SessionLocal()
    try:
        _seed_reits(db, "99972", 5)
        counts = sync_pdf_filings(db, codes=["99972"], limit=3, batch=2)
        lines = _batch_lines(capsys.readouterr().out)
        if [line.split()[2] for line in lines] != ["done=2/3", "done=3/3"]:
            _fail(f"lines {lines}")
        if counts[SyncOutcome.ABSENT] != 3 or counts["remaining"] != 2:
            _fail(f"counts {dict(counts)}")
    finally:
        db.close()


def test_without_batch_prints_no_progress_and_does_not_retry(capsys, monkeypatch) -> None:
    db = SessionLocal()
    try:
        numbers = _seed_reits(db, "99973", 3)
        calls = _lock_first(monkeypatch, {numbers[1]: 1})
        waits = _no_sleep(monkeypatch)
        counts = sync_pdf_filings(db, codes=["99973"], limit=10)
        out = capsys.readouterr().out
        if _batch_lines(out) or "pdf locked" in out:
            _fail(f"out {out}")
        if calls != numbers or waits:
            _fail(f"calls {calls} waits {waits}")
        if counts[SyncOutcome.FAILED] != 1 or counts[SyncOutcome.ABSENT] != 2:
            _fail(f"counts {dict(counts)}")
        if f"pdf failed disclosure_no={numbers[1]}" not in out:
            _fail(f"out {out}")
    finally:
        db.close()


def test_locked_is_retried_once_at_batch_end(capsys, monkeypatch) -> None:
    db = SessionLocal()
    try:
        numbers = _seed_reits(db, "99974", 4)
        calls = _lock_first(monkeypatch, {numbers[0]: 1, numbers[2]: 2})
        waits = _no_sleep(monkeypatch)
        counts = sync_pdf_filings(db, codes=["99974"], limit=10, batch=2, retry_wait=7)
        out = capsys.readouterr().out
        if calls != [numbers[0], numbers[1], numbers[0], numbers[2], numbers[3], numbers[2]]:
            _fail(f"calls {calls}")
        if waits != [7, 7]:
            _fail(f"waits {waits}")
        if counts[SyncOutcome.FAILED] != 1 or counts[SyncOutcome.ABSENT] != 3:
            _fail(f"counts {dict(counts)}")
        if out.count("pdf failed disclosure_no=") != 1 or f"pdf failed disclosure_no={numbers[2]}" not in out:
            _fail(f"out {out}")
        lines = _batch_lines(out)
        if [line.split()[2] for line in lines] != ["done=2/4", "done=4/4"]:
            _fail(f"lines {lines}")
        if "failed=0" not in lines[0] or "absent=2" not in lines[0]:
            _fail(f"first {lines[0]}")
        if "failed=1" not in lines[1] or "absent=3" not in lines[1] or "left=0" not in lines[1]:
            _fail(f"last {lines[1]}")
    finally:
        db.close()


def test_other_errors_are_not_retried(capsys, monkeypatch) -> None:
    db = SessionLocal()
    try:
        numbers = _seed_reits(db, "99975", 2)
        real = pdf._sync
        calls: list[str] = []

        def fake(db, number, zip_parsed, *, reparse):
            calls.append(number)
            if number == numbers[0]:
                raise RuntimeError("boom")
            return real(db, number, zip_parsed, reparse=reparse)

        monkeypatch.setattr(pdf, "_sync", fake)
        waits = _no_sleep(monkeypatch)
        counts = sync_pdf_filings(db, codes=["99975"], limit=10, batch=2)
        if calls != numbers or waits:
            _fail(f"calls {calls} waits {waits}")
        if counts[SyncOutcome.FAILED] != 1 or counts[SyncOutcome.ABSENT] != 1:
            _fail(f"counts {dict(counts)}")
        if "err=boom" not in capsys.readouterr().out:
            _fail("failed line")
    finally:
        db.close()


def test_stopped_run_resumes_from_committed(monkeypatch) -> None:
    """1 件ごとにコミットする。途中で止めても、流し直すと取り込み済みは unchanged で飛ばす。"""
    first, second = "20240802512345", "20240801512345"
    db = SessionLocal()
    try:
        _seed(db, first, _ifrs_1q(), _statement(disclosure_no=first, disclosed_date=date(2024, 8, 2)))
        _seed(db, second, _ifrs_1q(), _statement(disclosure_no=second))
        apply_mappings(db)
        real = pdf._sync

        def stop_at_second(db, number, zip_parsed, *, reparse):
            if number == second:
                raise KeyboardInterrupt
            return real(db, number, zip_parsed, reparse=reparse)

        monkeypatch.setattr(pdf, "_sync", stop_at_second)
        with pytest.raises(KeyboardInterrupt):
            sync_pdf_filings(db, codes=["72030"], limit=10, batch=500)
        db.rollback()
    finally:
        db.close()
    monkeypatch.setattr(pdf, "_sync", real)
    db = SessionLocal()
    try:
        done = {row.doc_id: row.parse_status for row in db.query(XbrlFiling).filter_by(source="tdnet_pdf")}
        if done != {first: "parsed"}:
            _fail(f"after stop {done}")
        counts = sync_pdf_filings(db, codes=["72030"], limit=10, batch=500)
        if counts[SyncOutcome.UNCHANGED] != 1 or counts[SyncOutcome.PARSED] != 1:
            _fail(f"counts {dict(counts)}")
    finally:
        db.close()


def _cli():
    spec = importlib.util.spec_from_file_location(
        "tdnet_pdf_financials_cli", ROOT / "tools" / "tdnet_pdf" / "financials.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_batch_keeps_final_line(capsys) -> None:
    db = SessionLocal()
    try:
        _seed_reits(db, "99960", 3)
    finally:
        db.close()
    cli = _cli()
    final = "tdnet_pdf_financials parsed=0 unchanged=0 failed=0 retired=0 absent=3 garbled=0 textless=0 remaining=0"
    if cli.main(["--code", "9996"]) != 0:
        _fail("exit")
    plain = capsys.readouterr().out.splitlines()
    if plain != [final]:
        _fail(f"plain {plain}")
    if cli.main(["--code", "9996", "--batch", "2"]) != 0:
        _fail("exit")
    batched = capsys.readouterr().out.splitlines()
    if len(batched) != 3 or batched[-1] != final or len(_batch_lines("\n".join(batched))) != 2:
        _fail(f"batched {batched}")


def test_cli_rejects_batch_below_one() -> None:
    with pytest.raises(SystemExit):
        _cli().main(["--batch", "0"])

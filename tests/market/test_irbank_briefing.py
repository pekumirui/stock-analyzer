"""IR BANK の過去の決算説明資料（KAB-330）。ネットワークには出ない。"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.db import SessionLocal, apply_sqlite_pragmas, engine
from app.market.briefing_store import SOURCE_IRBANK, SOURCE_TDNET, record_briefing
from app.models import Base, BriefingDocument, BriefingIrbankScan, Stock
from jobs.monthly.tdnet_monthly_r2 import dest_path
from tools.briefing import irbank_ingest as mod

ROOT = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.4 briefing"
MAT = "2027年3月期第1四半期決算報告プレゼンテーション資料"
OLDER = "2025年3月期決算報告プレゼンテーション資料"
CORR = "(訂正)「2027年3月期第1四半期決算短信〔IFRS〕(連結)」補足資料の一部訂正に関するお知らせ"


def _db():
    db = SessionLocal()
    db.query(BriefingIrbankScan).delete()
    db.query(BriefingDocument).delete()
    db.query(Stock).delete()
    db.commit()
    return db


def _stock(db, code: str, name: str, category: str | None) -> None:
    db.add(Stock(code=code, name=name, product_category=category))
    db.commit()


def _page(code4: str, rows: list[tuple[str, str, str, str]]) -> str:
    parts = ['<dl class="sdl">']
    for doc, title, ymd, hm in rows:
        attr = f"{code4} 社 | {title}（{ymd} {hm}提出）".replace("&", "&amp;")
        parts.append(
            f'<dt>{ymd}</dt><dd><a class="nxq" title="{attr}" href="/{code4}/{doc}">'
            f"{title}（{hm}）</a></dd>"
        )
    parts.append("</dl>")
    return "".join(parts)


def _client(routes: dict[str, str | bytes | int], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        found = routes.get(str(request.url))
        if isinstance(found, str):
            return httpx.Response(200, text=found)
        if isinstance(found, bytes):
            return httpx.Response(200, content=found)
        if isinstance(found, int):
            return httpx.Response(found)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(db, dest: Path, routes: dict, calls: list[str], **kwargs):
    kwargs.setdefault("sleep", 0)
    with _client(routes, calls) as client:
        return mod.run(db, dest, client=client, log=lambda *_: None, **kwargs)


def _row(source: str, document_id: str) -> BriefingDocument | None:
    db = SessionLocal()
    try:
        return db.get(BriefingDocument, (source, document_id))
    finally:
        db.close()


def _scanned() -> list[str]:
    db = SessionLocal()
    try:
        return [code for (code,) in db.query(BriefingIrbankScan.code).order_by(BriefingIrbankScan.code)]
    finally:
        db.close()


def test_issuer_filter_follows_cover_target_rules() -> None:
    assert mod.is_briefing_issuer("011", "トヨタ自動車")
    assert mod.is_briefing_issuer("021", "外国株")
    assert mod.is_briefing_issuer("013", "日本都市投資法人")
    assert mod.is_briefing_issuer("011", "名前にファンドが付く普通株")
    assert mod.is_briefing_issuer(None, "区分の無い会社")
    assert mod.is_briefing_issuer("", "区分の無い会社")
    assert not mod.is_briefing_issuer("014", "上場ETF")
    assert not mod.is_briefing_issuer("023", "外国ETF")
    assert not mod.is_briefing_issuer("013", "産業ファンド投資法人")
    assert not mod.is_briefing_issuer("013", "いちごグリーンインフラ投資法人")
    assert not mod.is_briefing_issuer("012", "その他の区分")
    assert not mod.is_briefing_issuer(None, "テストETF")
    assert not mod.is_briefing_issuer("", "インフラファンド")


def test_parse_reads_title_and_uses_the_shared_gate() -> None:
    page = _page("7203", [
        ("140120260804507860", MAT, "2026/08/04", "14:00"),
        ("140120260807513909", CORR, "2026/08/07", "12:00"),
        ("140120260101000001", "決算&説明資料", "2026/01/01", "15:00"),
    ])
    items = mod.parse_ir_list(page, "7203")
    assert [item.document_id for item in items] == [
        "140120260804507860", "140120260807513909", "140120260101000001",
    ]
    assert items[0].pdf_id == "20260804507860"
    assert items[0].pubdate == "2026-08-04 14:00:00"
    assert items[0].title == MAT
    assert items[2].title == "決算&説明資料"
    picked = mod.select_materials(items)
    assert [item.title for item in picked] == [MAT, "決算&説明資料"]
    assert CORR not in [item.title for item in picked]
    # 実ページは <dt class="dsp">。表題属性に提出日時が無いときはリンク文字と dt から取る
    fallback = (
        '<dl><dt class="dsp">2026/08/04</dt><dd><a class="nxq" title="plain" '
        'href="/7203/140120260804507860">2027年3月期決算説明資料（14:00）</a></dd></dl>'
    )
    got = mod.parse_ir_list(fallback, "7203")
    assert len(got) == 1
    assert got[0].title == "2027年3月期決算説明資料"
    assert got[0].pubdate == "2026-08-04 14:00:00"
    assert got[0].pdf_id == "20260804507860"


def test_skips_indexed_disclosure_and_saves_irbank_once(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        _stock(db, "72031", "トヨタ自動車優先", "011")
        record_briefing(
            source=SOURCE_TDNET, document_id="140120260804507860", code="72030",
            company_name="トヨタ自動車", pubdate="2026-08-04 14:00:00", title=MAT,
            rel_path="7203/already.pdf", url="https://www.release.tdnet.info/inbs/140120260804507860.pdf",
            sha256="abc", size=3,
        )
        record_briefing(
            source=SOURCE_TDNET, document_id="20250508534308", code="72030",
            company_name="トヨタ自動車", pubdate="2025-05-08 16:00:00", title=OLDER,
            rel_path="7203/short.pdf", url=None, sha256="def", size=3,
        )
        new = "140120241106513334"
        new_title = "2025年3月期第2四半期(中間期)決算報告プレゼンテーション資料"
        page = _page("7203", [
            ("140120260804507860", MAT, "2026/08/04", "14:00"),
            ("140120250508534308", OLDER, "2025/05/08", "16:00"),
            (new, new_title, "2024/11/06", "18:00"),
            ("140120260807513909", CORR, "2026/08/07", "12:00"),
        ])
        routes = {
            "https://irbank.net/7203/ir": page,
            "https://api.irbank.net/api/market-files/v1/tdnet/20241106513334.pdf": PDF,
        }
        calls: list[str] = []
        stats = _run(db, tmp_path, routes, calls, codes=["72030"])
        assert stats.stopped == "done"
        assert stats.saved == 1 and stats.skipped == 2 and stats.listed == 3
        assert stats.remaining == 0
        assert calls == [
            "https://irbank.net/7203/ir",
            "https://api.irbank.net/api/market-files/v1/tdnet/20241106513334.pdf",
        ]
        row = _row(SOURCE_IRBANK, new)
        assert row is not None
        assert row.code == "72030"
        assert row.title == new_title
        assert row.pubdate == "2024-11-06 18:00:00"
        assert row.rel_path.startswith("7203/")
        assert "20241106" in row.rel_path
        assert row.url.endswith("/20241106513334.pdf")
        assert row.status == "saved"
        saved = tmp_path / row.rel_path
        assert saved.read_bytes() == PDF
        assert row.size == len(PDF)
        assert _row(SOURCE_IRBANK, "140120260804507860") is None
        again: list[str] = []
        second = _run(db, tmp_path, routes, again, codes=["72030"])
        assert again == []
        assert second.companies == 0 and second.remaining == 0
        assert _scanned() == ["72030"]
    finally:
        db.close()


def test_rate_limit_stops_and_next_run_continues(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        page = _page("7203", [
            ("140120260804507860", MAT, "2026/08/04", "14:00"),
            ("140120250508534308", OLDER, "2025/05/08", "16:00"),
        ])
        pdf_newer = "https://api.irbank.net/api/market-files/v1/tdnet/20260804507860.pdf"
        pdf_older = "https://api.irbank.net/api/market-files/v1/tdnet/20250508534308.pdf"
        state = {"older": 429}

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            calls.append(url)
            if url.endswith("/7203/ir"):
                return httpx.Response(200, text=page)
            if url == pdf_newer:
                return httpx.Response(200, content=PDF)
            if url == pdf_older:
                code = state["older"]
                state["older"] = 200
                if code != 200:
                    return httpx.Response(code)
                return httpx.Response(200, content=PDF + b"2")
            return httpx.Response(503)

        calls: list[str] = []
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            first = mod.run(db, tmp_path, codes=["72030"], sleep=0, client=client, log=lambda *_: None)
            assert first.stopped == "rate_limited"
            assert first.saved == 1 and first.companies == 0
            assert _scanned() == []
            assert calls == [
                "https://irbank.net/7203/ir",
                pdf_newer,
                pdf_older,
            ]
            calls.clear()
            second = mod.run(db, tmp_path, codes=["72030"], sleep=0, client=client, log=lambda *_: None)
        assert second.stopped == "done"
        assert second.saved == 1 and second.skipped == 1
        assert pdf_newer not in calls
        assert _scanned() == ["72030"]
        assert _row(SOURCE_IRBANK, "140120250508534308") is not None
    finally:
        db.close()


def test_list_503_does_not_fetch_the_next_company(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "11110", "先", "011")
        _stock(db, "22220", "後", "011")
        calls: list[str] = []
        routes = {
            "https://irbank.net/1111/ir": 503,
            "https://irbank.net/2222/ir": _page("2222", []),
        }
        stats = _run(db, tmp_path, routes, calls, codes=None)
        assert stats.stopped == "rate_limited"
        assert calls == ["https://irbank.net/1111/ir"]
        assert _scanned() == []
        assert stats.remaining == 2
    finally:
        db.close()


def test_missing_list_is_not_fetched_again(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "11110", "ページ無し", "011")
        calls: list[str] = []
        routes = {"https://irbank.net/1111/ir": 404}
        stats = _run(db, tmp_path, routes, calls, codes=["11110"])
        assert stats.companies == 1 and stats.listed == 0 and _scanned() == ["11110"]
        calls.clear()
        again = _run(db, tmp_path, routes, calls, codes=["11110"])
        assert calls == [] and again.remaining == 0
    finally:
        db.close()


def test_limit_resumes_with_the_next_issuer(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "22220", "後", "011")
        _stock(db, "11110", "先", "011")
        routes = {
            "https://irbank.net/1111/ir": "<dl></dl>",
            "https://irbank.net/2222/ir": "<dl></dl>",
        }
        calls: list[str] = []
        first = _run(db, tmp_path, routes, calls, codes=None, limit=1)
        assert first.stopped == "limit" and first.companies == 1 and first.remaining == 1
        assert calls == ["https://irbank.net/1111/ir"]
        calls.clear()
        second = _run(db, tmp_path, routes, calls, codes=None, limit=1)
        assert second.stopped == "done" and second.remaining == 0
        assert calls == ["https://irbank.net/2222/ir"]
    finally:
        db.close()


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        page = _page("7203", [("140120260804507860", MAT, "2026/08/04", "14:00")])
        calls: list[str] = []
        lines: list[str] = []
        routes = {"https://irbank.net/7203/ir": page}
        with _client(routes, calls) as client:
            stats = mod.run(
                db, tmp_path, codes=["72030"], sleep=0, dry_run=True, client=client, log=lines.append,
            )
        assert stats.stopped == "done" and stats.listed == 1 and stats.saved == 0
        assert stats.remaining == 1
        assert calls == ["https://irbank.net/7203/ir"]
        assert any(line.startswith("todo 72030 140120260804507860") and MAT in line for line in lines)
        assert not list(tmp_path.rglob("*.pdf"))
        assert _row(SOURCE_IRBANK, "140120260804507860") is None
        assert _scanned() == []
    finally:
        db.close()


def test_etf_and_funds_are_not_requested(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "13060", "上場ETF", "014")
        _stock(db, "89510", "産業ファンド投資法人", "013")
        _stock(db, "92820", "いちごグリーンインフラ投資法人", "013")
        _stock(db, "72030", "トヨタ自動車", "011")
        _stock(db, "00010", "外国株", "021")
        calls: list[str] = []
        routes = {
            "https://irbank.net/7203/ir": "<dl></dl>",
            "https://irbank.net/0001/ir": "<dl></dl>",
            "https://irbank.net/1306/ir": "<dl></dl>",
        }
        stats = _run(db, tmp_path, routes, calls, codes=None)
        assert stats.companies == 2
        assert sorted(calls) == [
            "https://irbank.net/0001/ir",
            "https://irbank.net/7203/ir",
        ]
    finally:
        db.close()


def test_missing_pdf_is_finished_and_bad_pdf_is_retried(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "11110", "無い", "011")
        _stock(db, "22220", "壊れた", "011")
        missing_page = _page("1111", [("140120260101000011", "2026年3月期決算説明資料", "2026/01/01", "15:00")])
        bad_page = _page("2222", [("140120260101000022", "2026年3月期決算補足資料", "2026/01/02", "15:00")])
        routes = {
            "https://irbank.net/1111/ir": missing_page,
            "https://irbank.net/2222/ir": bad_page,
            "https://api.irbank.net/api/market-files/v1/tdnet/20260101000011.pdf": 404,
            "https://api.irbank.net/api/market-files/v1/tdnet/20260101000022.pdf": b"not-a-pdf",
        }
        calls: list[str] = []
        stats = _run(db, tmp_path, routes, calls, codes=None)
        assert stats.missing == 1 and stats.failed == 1
        assert _scanned() == ["11110"]
        assert stats.remaining == 1
        calls.clear()
        again = _run(db, tmp_path, routes, calls, codes=None)
        assert "1111/ir" not in "".join(calls)
        assert "2222/ir" in "".join(calls)
        assert again.failed == 1 and _scanned() == ["11110"]
    finally:
        db.close()


def test_existing_pdf_is_indexed_without_download(tmp_path: Path) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        page = _page("7203", [("140120260804507860", MAT, "2026/08/04", "14:00")])
        path = dest_path(tmp_path, "2026-08-04 14:00:00", "7203", "トヨタ自動車", MAT)
        path.parent.mkdir(parents=True)
        path.write_bytes(PDF)
        calls: list[str] = []
        routes = {"https://irbank.net/7203/ir": page}
        stats = _run(db, tmp_path, routes, calls, codes=["72030"])
        assert stats.reindexed == 1 and stats.saved == 0
        assert calls == ["https://irbank.net/7203/ir"]
        row = _row(SOURCE_IRBANK, "140120260804507860")
        assert row is not None and row.rel_path == path.relative_to(tmp_path).as_posix()
    finally:
        db.close()


def test_gate_is_is_material_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        monkeypatch.setattr(mod, "is_material_title", lambda _title: False)
        page = _page("7203", [("140120260804507860", MAT, "2026/08/04", "14:00")])
        calls: list[str] = []
        routes = {"https://irbank.net/7203/ir": page}
        stats = _run(db, tmp_path, routes, calls, codes=["72030"])
        assert stats.listed == 0 and stats.saved == 0
        assert calls == ["https://irbank.net/7203/ir"]
        assert _scanned() == ["72030"]
    finally:
        db.close()


def test_sleep_is_between_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        page = _page("7203", [("140120260804507860", MAT, "2026/08/04", "14:00")])
        slept: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", slept.append)
        routes = {
            "https://irbank.net/7203/ir": page,
            "https://api.irbank.net/api/market-files/v1/tdnet/20260804507860.pdf": PDF,
        }
        _run(db, tmp_path, routes, [], codes=["72030"], sleep=1.5)
        assert slept == [1.5]
    finally:
        db.close()


def test_not_scheduled_and_documented() -> None:
    scheduled = []
    for line in (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        if "irbank_ingest" in line:
            scheduled.append(line)
    assert scheduled == []
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "python tools/briefing/irbank_ingest.py --code 7203 --dry-run" in readme
    assert "tools/briefing/irbank_ingest.py --all --limit 80 --sleep 1" in readme
    sys.path.insert(0, str(ROOT / "mcp"))
    import log_mcp

    assert "irbank_ingest" in log_mcp.BATCH_JOBS
    assert "irbank_ingest" not in log_mcp.crontab_job_names()


def test_download_does_not_hold_a_reader_or_a_second_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF を取っている間、一覧のトランザクションも 2 本目の接続も残さない。"""
    db = _db()
    held = {"list": None, "pdf": None}
    real_list, real_pdf = mod.fetch_list, mod.fetch_pdf

    def fetch_list(client, code4):
        held["list"] = db.in_transaction()
        return real_list(client, code4)

    def fetch_pdf(client, pdf_id):
        held["pdf"] = db.in_transaction()
        return real_pdf(client, pdf_id)

    monkeypatch.setattr(mod, "fetch_list", fetch_list)
    monkeypatch.setattr(mod, "fetch_pdf", fetch_pdf)
    live = {"n": 0, "max": 0}

    def _out(*_args) -> None:
        live["n"] += 1
        live["max"] = max(live["max"], live["n"])

    def _in(*_args) -> None:
        live["n"] -= 1

    event.listen(engine, "checkout", _out)
    event.listen(engine, "checkin", _in)
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        page = _page("7203", [("140120260804507860", MAT, "2026/08/04", "14:00")])
        routes = {
            "https://irbank.net/7203/ir": page,
            "https://api.irbank.net/api/market-files/v1/tdnet/20260804507860.pdf": PDF,
        }
        stats = _run(db, tmp_path, routes, [], codes=["72030"])
        assert stats.saved == 1
        assert held["list"] is False and held["pdf"] is False
        assert live["max"] == 1 and live["n"] == 0
        assert not db.in_transaction()
    finally:
        event.remove(engine, "checkout", _out)
        event.remove(engine, "checkin", _in)
        db.close()


def test_record_briefing_retries_past_one_busy_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """別接続の書き込みロックが 1 回の busy_timeout を超えても、索引の行は残る。"""
    path = Path(tempfile.mkdtemp(prefix="briefing-lock-")) / "stock.db"
    timeout_s, hold_s = 0.25, 0.45
    eng = create_engine(
        f"sqlite:///{path}", future=True, poolclass=NullPool,
        connect_args={"timeout": timeout_s},
    )
    event.listen(eng, "connect", apply_sqlite_pragmas)

    @event.listens_for(eng, "connect")
    def _short(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        cur = dbapi_connection.cursor()
        try:
            cur.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        finally:
            cur.close()

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, future=True)
    probe = Session()
    try:
        got = int(probe.execute(text("PRAGMA busy_timeout")).scalar())
    finally:
        probe.rollback()
        probe.close()
    assert got == int(timeout_s * 1000)
    monkeypatch.setattr("app.market.briefing_store.SessionLocal", Session)

    started = threading.Event()
    result: dict[str, str] = {}

    def holder() -> None:
        conn = sqlite3.connect(path, timeout=timeout_s, check_same_thread=False)
        try:
            apply_sqlite_pragmas(conn)
            conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS lock_hold (id INTEGER)")
            conn.execute("INSERT INTO lock_hold (id) VALUES (1)")
            started.set()
            time.sleep(hold_s)
            conn.commit()
            result["holder"] = "ok"
        except Exception as exc:  # noqa: BLE001
            result["holder"] = f"{type(exc).__name__}: {exc}"
            started.set()
        finally:
            conn.close()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert started.wait(timeout=2)
        t0 = time.monotonic()
        record_briefing(
            source=SOURCE_IRBANK, document_id="140120260101000099", code="72030",
            company_name="トヨタ自動車", pubdate="2026-01-01 15:00:00", title=MAT,
            rel_path="7203/locked.pdf", url=None, sha256="abc", size=3,
        )
        waited = time.monotonic() - t0
        check = Session()
        try:
            row = check.get(BriefingDocument, (SOURCE_IRBANK, "140120260101000099"))
            assert row is not None and row.rel_path == "7203/locked.pdf"
        finally:
            check.close()
        assert waited >= timeout_s * 0.6
    finally:
        thread.join(timeout=3)
        eng.dispose()
    assert result.get("holder") == "ok"


def _scanned_row(db, code: str) -> None:
    db.add(BriefingIrbankScan(
        code=code,
        scanned_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        listed=1,
        saved=1,
        skipped=0,
        reindexed=0,
        missing=0,
        failed=0,
    ))
    db.commit()


def test_rescan_rereads_scanned_companies_and_skips_indexed_pdfs(tmp_path: Path) -> None:
    """記録済みの会社は --rescan で一覧を読み直す。付けなければ飛ばす。索引済み PDF は取り直さない。"""
    db = _db()
    try:
        _stock(db, "72030", "トヨタ自動車", "011")
        _stock(db, "11110", "別の会社", "011")
        indexed = "140120260804507860"
        fresh = "140120260807599001"
        other_indexed = "140120260101000111"
        other_fresh = "140120260202000222"
        qa = "2027年3月期第1四半期決算説明会質疑応答(要旨)"
        other_qa = "2026年3月期第2四半期決算説明会質疑応答(要旨)"
        record_briefing(
            source=SOURCE_IRBANK, document_id=indexed, code="72030",
            company_name="トヨタ自動車", pubdate="2026-08-04 14:00:00", title=MAT,
            rel_path="7203/already.pdf", url="https://api.irbank.net/already.pdf",
            sha256="abc", size=3,
        )
        record_briefing(
            source=SOURCE_IRBANK, document_id=other_indexed, code="11110",
            company_name="別の会社", pubdate="2026-01-01 10:00:00", title=MAT,
            rel_path="1111/already.pdf", url="https://api.irbank.net/other.pdf",
            sha256="def", size=3,
        )
        _scanned_row(db, "72030")
        _scanned_row(db, "11110")
        page = _page("7203", [
            (indexed, MAT, "2026/08/04", "14:00"),
            (fresh, qa, "2026/08/07", "15:00"),
        ])
        other = _page("1111", [
            (other_indexed, MAT, "2026/01/01", "10:00"),
            (other_fresh, other_qa, "2026/02/02", "11:00"),
        ])
        fresh_pdf = "https://api.irbank.net/api/market-files/v1/tdnet/20260807599001.pdf"
        other_pdf = "https://api.irbank.net/api/market-files/v1/tdnet/20260202000222.pdf"
        indexed_pdf = "https://api.irbank.net/api/market-files/v1/tdnet/20260804507860.pdf"
        other_indexed_pdf = "https://api.irbank.net/api/market-files/v1/tdnet/20260101000111.pdf"
        routes = {
            "https://irbank.net/7203/ir": page,
            "https://irbank.net/1111/ir": other,
            fresh_pdf: PDF,
            other_pdf: PDF,
        }

        held, held_notes = mod.issuers(db, ["72030"])
        assert held == [] and held_notes == ["skip scanned 72030"]
        assert mod.issuers(db, None)[0] == []
        assert [item.code for item in mod.issuers(db, ["72030"], rescan=True)[0]] == ["72030"]
        assert [item.code for item in mod.issuers(db, None, rescan=True)[0]] == ["11110", "72030"]

        calls: list[str] = []
        skipped = _run(db, tmp_path, routes, calls, codes=["72030"])
        assert calls == []
        assert skipped.companies == 0 and skipped.saved == 0 and skipped.remaining == 0
        calls = []
        skipped_all = _run(db, tmp_path, routes, calls, codes=None)
        assert calls == []
        assert skipped_all.companies == 0 and skipped_all.remaining == 0

        calls = []
        again = _run(db, tmp_path, routes, calls, codes=["72030"], rescan=True)
        assert calls == ["https://irbank.net/7203/ir", fresh_pdf]
        assert indexed_pdf not in calls
        assert again.companies == 1 and again.listed == 2 and again.saved == 1 and again.skipped == 1
        assert again.remaining == 0
        row = _row(SOURCE_IRBANK, fresh)
        assert row is not None and row.title == qa
        assert (tmp_path / row.rel_path).read_bytes() == PDF
        assert _scanned() == ["11110", "72030"]

        calls = []
        whole = _run(db, tmp_path, routes, calls, codes=None, rescan=True)
        assert calls == ["https://irbank.net/1111/ir", other_pdf, "https://irbank.net/7203/ir"]
        assert indexed_pdf not in calls and other_indexed_pdf not in calls and fresh_pdf not in calls
        assert whole.companies == 2 and whole.listed == 4 and whole.saved == 1 and whole.skipped == 3
        assert whole.remaining == 0
        other_row = _row(SOURCE_IRBANK, other_fresh)
        assert other_row is not None and (tmp_path / other_row.rel_path).read_bytes() == PDF
    finally:
        db.close()


def test_main_passes_rescan_for_code_and_all(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[dict] = []

    def fake_run(_db, _dest, **kwargs):
        seen.append(kwargs)
        return mod.RunStats()

    class _DB:
        def close(self) -> None:
            return None

    monkeypatch.setattr(mod, "init_db", lambda: None)
    monkeypatch.setattr(mod, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(mod, "run", fake_run)
    dest = str(tmp_path)
    assert mod.main(["--code", "7203", "--rescan", "--dry-run", "--dest", dest]) == 0
    assert mod.main(["--all", "--rescan", "--dry-run", "--dest", dest]) == 0
    assert seen[0]["codes"] == ["72030"] and seen[0]["rescan"] is True and seen[0]["dry_run"] is True
    assert seen[1]["codes"] is None and seen[1]["rescan"] is True
    seen.clear()
    assert mod.main(["--code", "7203", "--dry-run", "--dest", dest]) == 0
    assert seen[0]["rescan"] is False


def test_main_requires_one_scope() -> None:
    with pytest.raises(SystemExit) as both:
        mod.main(["--code", "7203", "--all"])
    assert both.value.code == 2
    with pytest.raises(SystemExit) as neither:
        mod.main([])
    assert neither.value.code == 2
    assert mod.main(["--code", "!!!!"]) == 2

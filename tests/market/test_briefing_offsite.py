"""1 年以上前の決算説明資料を OneDrive へ移す（KAB-345）。rclone は差し替える。"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import text

from app.db import SessionLocal, engine, migrate_add_columns, missing_columns
from app.market.briefing_files import open_briefing, read_indexed
from app.market.briefing_store import (
    SOURCE_TDNET,
    STORAGE_LOCAL,
    STORAGE_ONEDRIVE,
    find_saved,
    get_saved,
    has_saved,
    mark_storage,
    record_briefing,
    saved_document_ids,
)
from app.models import BriefingDocument, BriefingIrbankScan, Stock
from jobs.ops.backup_db import CmdResult

ROOT = Path(__file__).resolve().parents[2]
PDF = b"%PDF-1.4 briefing"


def _clear() -> None:
    db = SessionLocal()
    try:
        db.query(BriefingIrbankScan).delete()
        db.query(BriefingDocument).delete()
        db.query(Stock).delete()
        db.commit()
    finally:
        db.close()


def _add(
    root: Path | None,
    *,
    code: str,
    rel: str,
    pubdate: str,
    document_id: str,
    body: bytes | None = PDF,
    storage: str | None = None,
) -> None:
    record_briefing(
        source=SOURCE_TDNET,
        document_id=document_id,
        code=code,
        company_name=code,
        pubdate=pubdate,
        title="決算説明資料",
        rel_path=rel,
        url=None,
        sha256="abc",
        size=len(body) if body is not None else 1,
    )
    if storage is not None:
        mark_storage(SOURCE_TDNET, document_id, storage)
    if root is not None and body is not None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def _rows() -> list[BriefingDocument]:
    db = SessionLocal()
    try:
        return list(db.query(BriefingDocument).all())
    finally:
        db.close()


class _Rclone:
    def __init__(self, root: Path, *, fail: str | None = None, remote_size: int | None = None) -> None:
        self.root = root
        self.fail = fail
        self.remote_size = remote_size
        self.calls: list[list[str]] = []
        self.by_rel: dict[str, bytes] = {}

    def rel_of(self, remote: str) -> str:
        matches = [rel for rel in self.by_rel if remote.endswith(rel)]
        if not matches:
            raise AssertionError(remote)
        return max(matches, key=len)

    def __call__(self, argv: list[str], timeout: float) -> CmdResult:  # noqa: ARG002
        op = argv[0]
        self.calls.append(list(argv))
        if op == "move" or op == "delete" or op == "deletefile":
            raise AssertionError(argv)
        if op == self.fail:
            return CmdResult(1, "", "boom")
        if op == "copyto":
            src = Path(argv[1])
            rel = src.resolve().relative_to(self.root.resolve()).as_posix()
            if not argv[2].endswith(rel):
                raise AssertionError(argv[2])
            saved = get_saved(SOURCE_TDNET, _doc_id(rel))
            if saved is None or saved.storage != STORAGE_LOCAL or not src.is_file():
                raise AssertionError("copy before the index is still local")
            self.by_rel[rel] = src.read_bytes()
            return CmdResult(0, "", "")
        if op == "size":
            rel = self.rel_of(argv[1])
            saved = get_saved(SOURCE_TDNET, _doc_id(rel))
            if saved is None or saved.storage != STORAGE_LOCAL or not (self.root / rel).is_file():
                raise AssertionError("size before the local file is removed")
            size = self.remote_size if self.remote_size is not None else len(self.by_rel[rel])
            return CmdResult(0, json.dumps({"bytes": size}), "")
        return CmdResult(1, "", f"unexpected {op}")


_IDS: dict[str, str] = {}


def _doc_id(rel: str) -> str:
    return _IDS[rel]


def _run(root: Path, rclone, **kwargs) -> tuple[int, str]:
    import jobs.market.briefing_offsite as mod

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.run(root, rclone=rclone, **kwargs)
    return rc, buf.getvalue()


def test_only_files_a_year_older_than_the_companys_latest_move(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    specs = [
        ("72030", "7203/20240115_7203_old.pdf", "2024-01-15 09:00:00", "old", True),
        ("72030", "7203/20250301_7203_exact.pdf", "2025-03-01 09:00:00", "exact", True),
        ("72030", "7203/20250302_7203_keep.pdf", "2025-03-02 09:00:00", "keep", False),
        ("72030", "7203/20260301_7203_new.pdf", "2019-01-01 00:00:00", "new", False),
        ("72030", "7203/nodate.pdf", "2020-05-01 10:00:00", "nodate", True),
        ("72030", "7203/20200101_7203_note.txt", "2020-01-01 00:00:00", "txt", False),
        ("99840", "9984/20240601_9984_latest.pdf", "2024-06-01 09:00:00", "sbnew", False),
        ("99840", "9984/20230101_9984_old.pdf", "2023-01-01 09:00:00", "sbold", True),
        ("11110", "1111/20240229_1111_leap.pdf", "2024-02-29 09:00:00", "leap", False),
        ("11110", "1111/20230228_1111_old.pdf", "2023-02-28 09:00:00", "leapold", True),
        ("11110", "1111/20230301_1111_keep.pdf", "2023-03-01 09:00:00", "leapkeep", False),
    ]
    bodies = {}
    for code, rel, pub, doc, _move in specs:
        body = PDF + doc.encode()
        bodies[rel] = body
        _IDS[rel] = doc
        _add(root, code=code, rel=rel, pubdate=pub, document_id=doc, body=body)
    before = len(_rows())
    rclone = _Rclone(root)
    rc, out = _run(root, rclone)
    assert rc == 0, out
    assert before == len(_rows())
    moved = {rel for _c, rel, _p, _d, flag in specs if flag}
    kept = {rel for _c, rel, _p, _d, flag in specs if not flag}
    for rel in moved:
        assert not (root / rel).exists(), rel
        saved = get_saved(SOURCE_TDNET, _IDS[rel])
        assert saved is not None
        assert saved.storage == STORAGE_ONEDRIVE
        assert saved.status == "saved"
        assert saved.rel_path == rel
        assert has_saved(SOURCE_TDNET, _IDS[rel])
        with open_briefing(rel, storage=STORAGE_ONEDRIVE, remote_get=rclone.by_rel.get) as path:
            assert path.read_bytes() == bodies[rel]
            tmp = path
        assert not tmp.exists()
        assert not (root / rel).exists()
    for rel in kept:
        assert (root / rel).is_file(), rel
        assert get_saved(SOURCE_TDNET, _IDS[rel]).storage == STORAGE_LOCAL
    verbs = [call[0] for call in rclone.calls]
    assert verbs == ["copyto", "size"] * len(moved)
    again = _Rclone(root)
    rc2, out2 = _run(root, again)
    assert rc2 == 0, out2
    assert again.calls == []
    assert "moved=0" in out2


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "dry"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="dry", body=PDF)
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"new",
    )
    body = (root / rel).read_bytes()

    def boom(argv, timeout):  # noqa: ARG001
        raise AssertionError(argv)

    rc, out = _run(root, boom, dry_run=True)
    assert rc == 0, out
    assert "dry-run" in out
    assert rel in out
    assert (root / rel).read_bytes() == body
    assert get_saved(SOURCE_TDNET, "dry").storage == STORAGE_LOCAL
    assert get_saved(SOURCE_TDNET, "fresh").storage == STORAGE_LOCAL
    assert len(_rows()) == 2


def test_copy_failure_keeps_local_file_and_index(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "copyfail"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="copyfail")
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh",
    )
    rclone = _Rclone(root, fail="copyto")
    rc, out = _run(root, rclone)
    assert rc == 1
    assert "fail" in out
    assert (root / rel).read_bytes() == PDF
    saved = get_saved(SOURCE_TDNET, "copyfail")
    assert saved is not None and saved.storage == STORAGE_LOCAL and saved.rel_path == rel
    assert all(call[0] != "size" for call in rclone.calls)


def test_size_mismatch_keeps_local_file_and_index(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "sizemismatch"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="sizemismatch")
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh",
    )
    rclone = _Rclone(root, remote_size=1)
    rc, _out = _run(root, rclone)
    assert rc == 1
    assert (root / rel).read_bytes() == PDF
    assert get_saved(SOURCE_TDNET, "sizemismatch").storage == STORAGE_LOCAL
    assert [call[0] for call in rclone.calls] == ["copyto", "size"]


def test_limit_moves_the_oldest_first(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    older = "7203/20200101_7203_older.pdf"
    later = "7203/20240101_7203_later.pdf"
    _IDS[older] = "older"
    _IDS[later] = "later"
    _add(root, code="72030", rel=older, pubdate="2020-01-01 00:00:00", document_id="older", body=PDF + b"a")
    _add(root, code="72030", rel=later, pubdate="2024-01-01 00:00:00", document_id="later", body=PDF + b"b")
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"c",
    )
    rclone = _Rclone(root)
    rc, out = _run(root, rclone, limit=1)
    assert rc == 0, out
    assert "batch=1" in out
    assert not (root / older).exists()
    assert (root / later).is_file()
    assert get_saved(SOURCE_TDNET, "older").storage == STORAGE_ONEDRIVE
    assert get_saved(SOURCE_TDNET, "later").storage == STORAGE_LOCAL


def test_leftover_local_file_is_removed_on_the_next_run(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "half"
    _add(
        root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00",
        document_id="half", storage=STORAGE_ONEDRIVE,
    )
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh",
    )
    # 索引は更新済みで手元が残っている。コピーの時点ではもう local ではない。
    calls: list[str] = []

    def allow_already_offsite(argv, timeout):  # noqa: ARG001
        calls.append(argv[0])
        if argv[0] in ("move", "delete", "deletefile"):
            raise AssertionError(argv)
        if argv[0] == "copyto":
            src = Path(argv[1])
            rel_now = src.resolve().relative_to(root.resolve()).as_posix()
            if not argv[2].endswith(rel_now) or not src.is_file():
                raise AssertionError(argv)
            return CmdResult(0, "", "")
        if argv[0] == "size":
            if not (root / rel).is_file():
                raise AssertionError("removed before size check")
            return CmdResult(0, json.dumps({"bytes": (root / rel).stat().st_size}), "")
        return CmdResult(1, "", argv[0])

    rc, out = _run(root, allow_already_offsite)
    assert calls == ["copyto", "size"]
    assert rc == 0, out
    assert not (root / rel).exists()
    saved = get_saved(SOURCE_TDNET, "half")
    assert saved is not None and saved.storage == STORAGE_ONEDRIVE and saved.rel_path == rel
    assert len(_rows()) == 2


def test_moved_rows_stay_saved_for_lookup() -> None:
    _clear()
    record_briefing(
        source=SOURCE_TDNET, document_id="140120240115000099", code="72030",
        company_name="トヨタ", pubdate="2024-01-15 09:00:00", title="決算説明資料",
        rel_path="7203/20240115_7203_old.pdf", url=None, sha256="abc", size=3,
    )
    mark_storage(SOURCE_TDNET, "140120240115000099", STORAGE_ONEDRIVE)
    assert has_saved(SOURCE_TDNET, "140120240115000099")
    assert saved_document_ids(["140120240115000099", "20240115000099"]) == {"140120240115000099"}
    found = find_saved("20240115000099")
    assert found is not None and found.storage == STORAGE_ONEDRIVE
    assert found.rel_path == "7203/20240115_7203_old.pdf"


def test_irbank_does_not_download_a_moved_document(tmp_path: Path) -> None:
    _clear()
    from tools.briefing import irbank_ingest as irbank

    db = SessionLocal()
    try:
        db.add(Stock(code="72030", name="トヨタ自動車", product_category="011"))
        db.commit()
        doc = "140120240115000099"
        record_briefing(
            source=SOURCE_TDNET, document_id=doc, code="72030", company_name="トヨタ自動車",
            pubdate="2024-01-15 09:00:00", title="2025年3月期決算説明資料",
            rel_path="7203/20240115_7203_old.pdf", url=None, sha256="abc", size=3,
        )
        mark_storage(SOURCE_TDNET, doc, STORAGE_ONEDRIVE)
        title = "2025年3月期決算説明資料"
        page = (
            '<dl class="sdl"><dt>2024/01/15</dt><dd><a class="nxq" '
            f'title="7203 社 | {title}（2024/01/15 09:00提出）" href="/7203/{doc}">'
            f"{title}（09:00）</a></dd></dl>"
        )
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if str(request.url).endswith("/ir"):
                return httpx.Response(200, text=page)
            return httpx.Response(200, content=PDF)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            stats = irbank.run(
                db, tmp_path, codes=["72030"], sleep=0, client=client, log=lambda *_: None,
            )
        assert stats.saved == 0 and stats.skipped == 1
        assert not any(url.endswith(".pdf") for url in calls)
        assert get_saved(SOURCE_TDNET, doc).storage == STORAGE_ONEDRIVE
        assert not (tmp_path / "7203" / "20240115_7203_old.pdf").exists()
    finally:
        db.close()


def test_tdnet_does_not_download_a_moved_document(tmp_path: Path) -> None:
    _clear()
    import jobs.market.tdnet_briefing_r2 as mod

    doc = "140120261030000099"
    record_briefing(
        source=SOURCE_TDNET, document_id=doc, code="72030", company_name="トヨタ",
        pubdate="2026-10-30 15:00:00", title="2027年3月期 決算説明資料",
        rel_path="7203/moved.pdf", url=None, sha256="abc", size=3,
    )
    mark_storage(SOURCE_TDNET, doc, STORAGE_ONEDRIVE)
    row = {
        "pubdate": "2026-10-30 15:00:00",
        "company_code": "72030",
        "company_name": "トヨタ",
        "title": "2027年3月期 決算説明資料",
        "document_url": f"https://www.release.tdnet.info/inbs/{doc}.pdf",
        "url_xbrl": "",
    }
    gets: list[str] = []
    buf = io.StringIO()
    with patch.object(mod.tdnet_list, "fetch_days", lambda *a, **k: [row]), \
            patch.object(mod, "http_get", lambda url, timeout=30.0: gets.append(url) or PDF), \
            patch.object(mod, "date_window", lambda days: (date(2026, 10, 30), date(2026, 10, 30))), \
            patch.object(mod.time, "sleep", lambda s: None), \
            redirect_stdout(buf):
        rc = mod.run(1, tmp_path)
    assert rc == 0, buf.getvalue()
    assert gets == []
    assert "skip offsite" in buf.getvalue()
    assert not list(tmp_path.rglob("*.pdf"))
    assert get_saved(SOURCE_TDNET, doc).storage == STORAGE_ONEDRIVE


def test_fetch_docs_reads_moved_pdf_and_still_downloads_the_rest(tmp_path: Path, monkeypatch) -> None:
    _clear()
    doc = "140120240115000099"
    record_briefing(
        source=SOURCE_TDNET, document_id=doc, code="72030", company_name="トヨタ",
        pubdate="2024-01-15 09:00:00", title="決算説明資料",
        rel_path="7203/20240115_7203_old.pdf", url=None, sha256="abc", size=3,
    )
    mark_storage(SOURCE_TDNET, doc, STORAGE_ONEDRIVE)
    payload = b"%PDF-1.4 from-onedrive"
    monkeypatch.setattr(
        "app.market.briefing_files.rclone_cat_bytes",
        lambda rel, environ=None: payload,
    )
    forecast = str(ROOT / "analyzer" / "agent_forecast")
    sys.path.insert(0, forecast)
    import fetch_docs

    opened: list[str] = []

    def urlopen(url, timeout=60):  # noqa: ARG001
        opened.append(url)
        raise AssertionError(url)

    monkeypatch.setattr(fetch_docs.urllib.request, "urlopen", urlopen)
    dest = tmp_path / "briefing_pdf"
    fetch_docs.fetch(
        [{"date": "2024-01-15", "title": "決算説明資料", "doc_id": "20240115000099"}],
        dest,
        indexed=fetch_docs.indexed_briefing,
        pause=0,
    )
    written = dest / "20240115_決算説明資料.pdf"
    assert written.read_bytes() == payload
    assert opened == []
    assert read_indexed("20240115000099", remote_get=lambda rel: payload) == payload

    class _Resp:
        def read(self) -> bytes:
            return b"%PDF-1.4 web"

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

    opened.clear()
    monkeypatch.setattr(
        fetch_docs.urllib.request, "urlopen", lambda url, timeout=60: opened.append(url) or _Resp(),
    )
    fetch_docs.fetch(
        [{"date": "2026-03-01", "title": "新しい資料", "doc_id": "20260301000001"}],
        dest,
        indexed=fetch_docs.indexed_briefing,
        pause=0,
    )
    assert opened
    assert (dest / "20260301_新しい資料.pdf").read_bytes().startswith(b"%PDF")


def test_adding_storage_keeps_existing_rows() -> None:
    _clear()
    record_briefing(
        source=SOURCE_TDNET, document_id="keep-me", code="72030", company_name="トヨタ",
        pubdate="2024-01-15 09:00:00", title="決算説明資料",
        rel_path="7203/keep.pdf", url=None, sha256="abc", size=4,
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE briefing_documents DROP COLUMN storage"))
            left = conn.execute(text(
                "SELECT document_id, rel_path, status FROM briefing_documents"
            )).fetchall()
        assert left == [("keep-me", "7203/keep.pdf", "saved")]
        assert "storage" in missing_columns().get("briefing_documents", [])
        assert migrate_add_columns(verbose=False) >= 1
        saved = get_saved(SOURCE_TDNET, "keep-me")
        assert saved is not None
        assert saved.storage == STORAGE_LOCAL
        assert saved.rel_path == "7203/keep.pdf"
        assert len(_rows()) == 1
    finally:
        migrate_add_columns(verbose=False)


def test_job_is_not_scheduled() -> None:
    import mcp.log_mcp as logs

    for line in (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "briefing_offsite" not in stripped
    assert "briefing_offsite" in logs.BATCH_JOBS
    assert "briefing_offsite" not in logs.crontab_job_names()


def test_cli_dry_run(tmp_path: Path) -> None:
    _clear()
    import jobs.market.briefing_offsite as mod

    rel = "7203/20200101_7203_old.pdf"
    _add(tmp_path, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="cli")
    _add(
        tmp_path, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh",
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = mod.main(["--dry-run", "--dest", str(tmp_path), "--limit", "5"])
    assert rc == 0, buf.getvalue()
    assert rel in buf.getvalue()
    assert (tmp_path / rel).is_file()
    with pytest.raises(SystemExit):
        mod.main(["--limit", "0"])

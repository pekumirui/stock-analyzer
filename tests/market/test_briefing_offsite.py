"""1 年以上前の決算説明資料を OneDrive へ移す（KAB-345）。rclone は差し替える。"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
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


def _flag(argv: list[str], name: str) -> str:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise AssertionError(argv) from exc


def _listed(argv: list[str]) -> list[str]:
    text = Path(_flag(argv, "--files-from")).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


class _Rclone:
    """copy --files-from で受け、check --one-way の combined を書く。"""

    def __init__(
        self,
        root: Path,
        *,
        fail: str | None = None,
        remote_size: int | None = None,
        differ: set[str] | None = None,
        copy_rc: int = 0,
        already_offsite: bool = False,
    ) -> None:
        self.root = root
        self.fail = fail
        self.remote_size = remote_size
        self.differ = differ or set()
        self.copy_rc = copy_rc
        self.already_offsite = already_offsite
        self.calls: list[list[str]] = []
        self.by_rel: dict[str, bytes] = {}
        self.files_from: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> CmdResult:
        op = argv[0]
        self.calls.append(list(argv))
        if op in ("copyto", "size", "move", "delete", "deletefile"):
            raise AssertionError(argv)
        if op == self.fail:
            return CmdResult(2 if op == "check" else 1, "", "boom")
        if op == "copy":
            rels = _listed(argv)
            self.files_from.append(rels)
            if not str(argv[2]).rstrip("/").endswith("data/briefing"):
                raise AssertionError(argv[2])
            for rel in rels:
                src = self.root / rel
                if not src.is_file():
                    raise AssertionError(rel)
                saved = get_saved(SOURCE_TDNET, _doc_id(rel))
                if saved is None:
                    raise AssertionError(rel)
                if not self.already_offsite and saved.storage != STORAGE_LOCAL:
                    raise AssertionError("index changed before copy")
                self.by_rel[rel] = src.read_bytes()
            return CmdResult(self.copy_rc, "", "" if self.copy_rc == 0 else "partial")
        if op == "check":
            if "--one-way" not in argv:
                raise AssertionError(argv)
            rels = _listed(argv)
            lines: list[str] = []
            bad = False
            for rel in rels:
                if not (self.root / rel).is_file():
                    raise AssertionError("removed before check")
                saved = get_saved(SOURCE_TDNET, _doc_id(rel))
                if saved is None:
                    raise AssertionError(rel)
                if not self.already_offsite and saved.storage != STORAGE_LOCAL:
                    raise AssertionError("index changed before check")
                size = self.remote_size if self.remote_size is not None else len(self.by_rel.get(rel, b""))
                if rel not in self.by_rel or rel in self.differ or size != len(self.by_rel[rel]):
                    lines.append(f"* {rel}")
                    bad = True
                else:
                    lines.append(f"= {rel}")
            Path(_flag(argv, "--combined")).write_text("\n".join(lines) + "\n", encoding="utf-8")
            return CmdResult(1 if bad else 0, "", "")
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
    assert [call[0] for call in rclone.calls] == ["copy", "check"]
    assert set(rclone.files_from[0]) == moved
    assert not (set(rclone.files_from[0]) & kept)
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

    def boom(argv, timeout):
        raise AssertionError(argv)

    rc, out = _run(root, boom, dry_run=True)
    assert rc == 0, out
    assert "dry-run" in out
    assert rel in out
    assert (root / rel).read_bytes() == body
    assert get_saved(SOURCE_TDNET, "dry").storage == STORAGE_LOCAL
    assert get_saved(SOURCE_TDNET, "fresh").storage == STORAGE_LOCAL
    assert len(_rows()) == 2


def test_missing_file_is_logged_and_does_not_fail(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    gone = "1332/20230512_1332_note.pdf"
    fresh = "1332/20260301_1332_new.pdf"
    _IDS[gone] = "gone"
    _IDS[fresh] = "fresh"
    _add(
        root, code="13320", rel=gone, pubdate="2023-05-12 15:00:00",
        document_id="gone", body=None,
    )
    _add(root, code="13320", rel=fresh, pubdate="2026-03-01 15:00:00", document_id="fresh")
    rclone = _Rclone(root)

    def boom(argv, timeout):
        raise AssertionError(argv)

    rc, out = _run(root, boom)
    assert rc == 0, out
    assert "gone 13320 2023-05-12 1332/20230512_1332_note.pdf: missing on disk" in out
    assert "failed=0" in out
    assert "gone=1" in out
    assert "fail " not in out
    assert rclone.calls == []
    assert get_saved(SOURCE_TDNET, "gone").storage == STORAGE_LOCAL
    assert (root / fresh).is_file()
    assert not (root / gone).exists()


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
    rclone = _Rclone(root, fail="copy")
    rc, out = _run(root, rclone)
    assert rc == 1
    assert "fail" in out
    assert (root / rel).read_bytes() == PDF
    saved = get_saved(SOURCE_TDNET, "copyfail")
    assert saved is not None and saved.storage == STORAGE_LOCAL and saved.rel_path == rel
    assert [call[0] for call in rclone.calls] == ["copy", "check"]


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
    assert [call[0] for call in rclone.calls] == ["copy", "check"]


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
    assert "files=1" in out
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
    rclone = _Rclone(root, already_offsite=True)
    rc, out = _run(root, rclone)
    assert [call[0] for call in rclone.calls] == ["copy", "check"]
    assert rc == 0, out
    assert not (root / rel).exists()
    saved = get_saved(SOURCE_TDNET, "half")
    assert saved is not None and saved.storage == STORAGE_ONEDRIVE and saved.rel_path == rel
    assert len(_rows()) == 2


def test_one_mismatch_does_not_block_the_files_that_match(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    older = "7203/20200101_7203_older.pdf"
    later = "7203/20240101_7203_later.pdf"
    fresh = "7203/20260301_7203_new.pdf"
    _IDS[older] = "older"
    _IDS[later] = "later"
    _add(root, code="72030", rel=older, pubdate="2020-01-01 00:00:00", document_id="older", body=PDF + b"a")
    _add(root, code="72030", rel=later, pubdate="2024-01-01 00:00:00", document_id="later", body=PDF + b"b")
    _add(root, code="72030", rel=fresh, pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"c")
    rclone = _Rclone(root, differ={later})
    rc, out = _run(root, rclone)
    assert rc == 1, out
    assert [call[0] for call in rclone.calls] == ["copy", "check"]
    assert set(rclone.files_from[0]) == {older, later}
    assert not (root / older).exists()
    assert (root / later).read_bytes() == PDF + b"b"
    assert (root / fresh).is_file()
    assert get_saved(SOURCE_TDNET, "older").storage == STORAGE_ONEDRIVE
    assert get_saved(SOURCE_TDNET, "later").storage == STORAGE_LOCAL


def test_nonzero_copy_still_moves_files_the_check_accepts(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "partial"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="partial", body=PDF + b"p")
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"n",
    )
    rclone = _Rclone(root, copy_rc=1)
    rc, out = _run(root, rclone)
    assert rc == 0, out
    assert not (root / rel).exists()
    assert get_saved(SOURCE_TDNET, "partial").storage == STORAGE_ONEDRIVE
    assert rclone.by_rel[rel] == PDF + b"p"


def test_check_error_keeps_every_local_file(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "checkfail"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="checkfail")
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh",
    )
    rclone = _Rclone(root, fail="check")
    rc, _out = _run(root, rclone)
    assert rc == 1
    assert (root / rel).read_bytes() == PDF
    assert get_saved(SOURCE_TDNET, "checkfail").storage == STORAGE_LOCAL
    assert [call[0] for call in rclone.calls] == ["copy", "check"]


def test_index_failure_leaves_the_file_for_the_next_run(tmp_path: Path, monkeypatch) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = "crash"
    body = PDF + b"crash"
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id="crash", body=body)
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"n",
    )
    import jobs.market.briefing_offsite as offsite

    real = offsite.mark_storage
    calls = {"n": 0}

    def flaky(source: str, document_id: str, storage: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("crash")
        real(source, document_id, storage)

    monkeypatch.setattr(offsite, "mark_storage", flaky)
    first = _Rclone(root)
    rc, out = _run(root, first)
    assert rc == 1, out
    assert (root / rel).read_bytes() == body
    assert get_saved(SOURCE_TDNET, "crash").storage == STORAGE_LOCAL
    second = _Rclone(root)
    rc2, out2 = _run(root, second)
    assert rc2 == 0, out2
    assert not (root / rel).exists()
    assert get_saved(SOURCE_TDNET, "crash").storage == STORAGE_ONEDRIVE
    with open_briefing(rel, storage=STORAGE_ONEDRIVE, remote_get=second.by_rel.get) as path:
        assert path.read_bytes() == body


def test_each_batch_rereads_what_is_still_local(tmp_path: Path, monkeypatch) -> None:
    _clear()
    _IDS.clear()
    import jobs.market.briefing_offsite as offsite

    monkeypatch.setattr(offsite, "BATCH_SIZE", 1)
    root = tmp_path / "briefing"
    older = "7203/20200101_7203_older.pdf"
    later = "7203/20240101_7203_later.pdf"
    extra = "7203/20190101_7203_extra.pdf"
    fresh = "7203/20260301_7203_new.pdf"
    _IDS[older] = "older"
    _IDS[later] = "later"
    _add(root, code="72030", rel=older, pubdate="2020-01-01 00:00:00", document_id="older", body=PDF + b"a")
    _add(root, code="72030", rel=later, pubdate="2024-01-01 00:00:00", document_id="later", body=PDF + b"b")
    _add(root, code="72030", rel=fresh, pubdate="2026-03-01 00:00:00", document_id="fresh", body=PDF + b"c")
    added = {"done": False}

    class _Adding(_Rclone):
        def __call__(self, argv: list[str], timeout: float) -> CmdResult:
            result = super().__call__(argv, timeout)
            if argv[0] == "check" and not added["done"]:
                added["done"] = True
                _IDS[extra] = "extra"
                _add(
                    root, code="72030", rel=extra, pubdate="2019-01-01 00:00:00",
                    document_id="extra", body=PDF + b"e",
                )
            return result

    rclone = _Adding(root)
    rc, out = _run(root, rclone)
    assert rc == 0, out
    assert extra not in rclone.files_from[0]
    assert all(len(batch) == 1 for batch in rclone.files_from)
    assert any(extra in batch for batch in rclone.files_from[1:])
    assert not (root / extra).exists()
    assert not (root / older).exists()
    assert not (root / later).exists()
    assert (root / fresh).is_file()
    assert get_saved(SOURCE_TDNET, "extra").storage == STORAGE_ONEDRIVE
    assert get_saved(SOURCE_TDNET, "fresh").storage == STORAGE_LOCAL


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

    def urlopen(url, timeout=60):
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


def _field_values(field: str, low: int, high: int) -> set[int]:
    if field == "*":
        return set(range(low, high + 1))
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        body = part
        if "/" in part:
            body, step_text = part.split("/", 1)
            step = int(step_text)
        if body in {"*", ""}:
            start, end = low, high
        elif "-" in body:
            start_text, end_text = body.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(body)
        values.update(range(start, end + 1, step))
    return values


def _cron_slots(spec: list[str]) -> set[tuple[int, int, int]]:
    minute, hour, _dom, _month, dow = spec
    minutes = _field_values(minute, 0, 59)
    hours = _field_values(hour, 0, 23)
    dows = _field_values(dow, 0, 6)
    return {(day, hr, min_) for day in dows for hr in hours for min_ in minutes}


def _active_crontab_lines() -> list[str]:
    lines: list[str] = []
    for raw in (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped.split()[0]:
            continue
        lines.append(stripped)
    return lines


def test_daily_run_uses_cron_runner_and_does_not_share_a_start() -> None:
    """時刻そのものは見ない。毎日 1 回、深夜、他のジョブと同時に始まらないこと。"""
    import mcp.log_mcp as logs

    lines = _active_crontab_lines()
    ours = [line for line in lines if "briefing_offsite.py" in line]
    assert len(ours) == 1
    parts = ours[0].split()
    assert len(parts) >= 6
    minute, hour, dom, month, dow = parts[:5]
    hours = _field_values(hour, 0, 23)
    minutes = _field_values(minute, 0, 59)
    assert len(hours) == 1 and len(minutes) == 1
    assert hours <= set(range(7))
    assert _field_values(dom, 1, 31) == set(range(1, 32))
    assert _field_values(month, 1, 12) == set(range(1, 13))
    assert _field_values(dow, 0, 6) == set(range(7))
    command = " ".join(parts[5:])
    assert command.startswith("cd ")
    assert command.count("cron-run.sh") == 1
    assert "deploy/cron-run.sh" in command
    assert "jobs/market/briefing_offsite.py" in command
    assert ">" not in command
    mine = _cron_slots(parts[:5])
    for other in lines:
        if other == ours[0]:
            continue
        overlap = mine & _cron_slots(other.split()[:5])
        assert not overlap, other
    assert "briefing_offsite" in logs.BATCH_JOBS
    assert "briefing_offsite" in logs.crontab_job_names()


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) if not prev else str(ROOT) + os.pathsep + prev
    return env


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


def _wait_text(path: Path, proc: subprocess.Popen[bytes]) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return path.read_text(encoding="utf-8")
        if proc.poll() is not None:
            err = proc.stderr.read().decode() if proc.stderr else ""
            raise AssertionError(f"child exited {proc.returncode}: {err}")
        time.sleep(0.05)
    raise AssertionError("child did not signal")


def _pair(root: Path, document_id: str) -> str:
    rel = "7203/20200101_7203_old.pdf"
    _IDS[rel] = document_id
    _add(root, code="72030", rel=rel, pubdate="2020-01-01 00:00:00", document_id=document_id)
    _add(
        root, code="72030", rel="7203/20260301_7203_new.pdf",
        pubdate="2026-03-01 00:00:00", document_id=document_id + "-new",
    )
    return rel


def _assert_left_alone(root: Path, rel: str, document_id: str, rclone: _Rclone, out: str, rc: int) -> None:
    assert rc == 0, out
    assert rclone.calls == []
    assert (root / rel).is_file()
    saved = get_saved(SOURCE_TDNET, document_id)
    assert saved is not None and saved.storage == STORAGE_LOCAL
    assert "already running" in out
    assert "done moved=" not in out


def test_second_run_does_not_move_while_the_lock_is_held(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = _pair(root, "locked")
    ready = tmp_path / "ready"
    code = (
        "import time\n"
        "from pathlib import Path\n"
        "import jobs.market.briefing_offsite as job\n"
        "with job.instance_lock() as acquired:\n"
        f"    Path({str(ready)!r}).write_text('1' if acquired else '0', encoding='utf-8')\n"
        "    if not acquired:\n"
        "        raise SystemExit(3)\n"
        "    time.sleep(120)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_child_env(),
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert _wait_text(ready, proc) == "1"
        rclone = _Rclone(root)
        rc, out = _run(root, rclone)
        _assert_left_alone(root, rel, "locked", rclone, out, rc)
        assert proc.poll() is None
    finally:
        _stop(proc)
    again = _Rclone(root)
    rc2, out2 = _run(root, again)
    assert rc2 == 0, out2
    assert not (root / rel).exists()
    assert get_saved(SOURCE_TDNET, "locked").storage == STORAGE_ONEDRIVE


def test_run_skips_a_manual_process_that_does_not_hold_the_lock(tmp_path: Path) -> None:
    _clear()
    _IDS.clear()
    root = tmp_path / "briefing"
    rel = _pair(root, "manual")
    script = ROOT / "jobs" / "market" / "briefing_offsite.py"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", str(script)],
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        needle = b"briefing_offsite.py"
        while True:
            try:
                raw = Path(f"/proc/{proc.pid}/cmdline").read_bytes()
            except OSError:
                raw = b""
            if needle in raw:
                break
            if time.monotonic() > deadline or proc.poll() is not None:
                raise AssertionError("manual process was not visible")
            time.sleep(0.02)
        rclone = _Rclone(root)
        rc, out = _run(root, rclone)
        _assert_left_alone(root, rel, "manual", rclone, out, rc)
        assert proc.poll() is None
    finally:
        _stop(proc)
    again = _Rclone(root)
    rc2, out2 = _run(root, again)
    assert rc2 == 0, out2
    assert not (root / rel).exists()
    assert get_saved(SOURCE_TDNET, "manual").storage == STORAGE_ONEDRIVE


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

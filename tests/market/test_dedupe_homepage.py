"""会社ページの決算説明資料の重複を一度だけ消すツール（KAB-440）。本番で流したらツールと一緒に消す。"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.models import BriefingDocument, BriefingExtract
from jobs.ops.backup_db import CmdResult
from tools.briefing import dedupe_homepage as mod

SAVED_AT = datetime(2026, 11, 11, 20, 0)  # noqa: DTZ001


@pytest.fixture(autouse=True)
def _clean() -> None:
    db = SessionLocal()
    try:
        db.query(BriefingExtract).delete()
        db.query(BriefingDocument).delete()
        db.commit()
    finally:
        db.close()


def _doc(source: str, doc: str, code: str, sha: str, pubdate: str, storage: str = "local", root: Path | None = None) -> None:
    rel = f"{code[:4]}/{doc}.pdf"
    if root is not None and storage == "local":
        path = root / "briefing" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF " + doc.encode())
    db = SessionLocal()
    try:
        db.add(BriefingDocument(
            source=source, document_id=doc, code=code, company_name="x", pubdate=pubdate, title=f"t-{doc}",
            rel_path=rel, url=None, sha256=sha, size=1, status="saved", storage=storage, saved_at=SAVED_AT,
        ))
        db.commit()
    finally:
        db.close()


def _left() -> set[tuple[str, str]]:
    db = SessionLocal()
    try:
        return {(row.source, row.document_id) for row in db.query(BriefingDocument).all()}
    finally:
        db.close()


def _seed(tmp_path: Path) -> None:
    _doc("homepage", "hpA1", "80020", "a", "2026-08-01 00:00:00", root=tmp_path)
    _doc("homepage", "hpA2", "80020", "a", "2026-08-01 00:00:00", storage="onedrive")
    _doc("homepage", "hpA3", "80020", "a", "2026-08-05 00:00:00", root=tmp_path)
    _doc("homepage", "hpB1", "80020", "b", "2026-08-01 00:00:00", root=tmp_path)
    _doc("tdnet", "0812T", "34750", "c", "2026-09-14 15:00:00")
    _doc("homepage", "hpC1", "34750", "c", "2026-09-14 00:00:00", storage="onedrive")
    _doc("homepage", "hpD1", "69190", "a", "2026-08-01 00:00:00", root=tmp_path)


def test_dry_run_lists_drops_and_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHARE_ROOT", str(tmp_path))
    _seed(tmp_path)
    calls: list[list[str]] = []
    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(dry_run=True, rclone=lambda argv, timeout: calls.append(list(argv)) or CmdResult(0, "", ""))
    assert rc == 0
    text = out.getvalue()
    assert "would delete 80020 hpA2 keep=hpA1" in text
    assert "would delete 80020 hpA3 keep=hpA1" in text
    assert "would delete 34750 hpC1 keep=0812T" in text
    assert "found=3 onedrive=2 deleted=0 failed=0 held=0" in text
    assert calls == []
    assert len(_left()) == 7


def test_run_deletes_files_rows_and_records_dup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHARE_ROOT", str(tmp_path))
    monkeypatch.setenv("BACKUP_OFFSITE_REMOTE", "onedrive")
    monkeypatch.setenv("BACKUP_OFFSITE_PATH", "backups")
    _seed(tmp_path)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"old": {"x": "2020-01-01 00:00:00"}, "checked": {}, "dup": {}}), encoding="utf-8")
    calls: list[list[str]] = []

    def rclone(argv, timeout):
        calls.append(list(argv))
        return CmdResult(0, "", "")

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(dry_run=False, ledger=ledger, rclone=rclone)
    assert rc == 0
    assert "found=3 onedrive=2 deleted=3 failed=0 held=0" in out.getvalue()
    assert _left() == {("homepage", "hpA1"), ("homepage", "hpB1"), ("tdnet", "0812T"), ("homepage", "hpD1")}
    assert not (tmp_path / "briefing" / "8002" / "hpA3.pdf").exists()
    assert (tmp_path / "briefing" / "8002" / "hpA1.pdf").exists()
    assert sorted(c[1] for c in calls) == [
        "onedrive:backups/data/briefing/3475/hpC1.pdf",
        "onedrive:backups/data/briefing/8002/hpA2.pdf",
    ]
    book = json.loads(ledger.read_text(encoding="utf-8"))
    assert book["dup"] == {"hpA2": "hpA1", "hpA3": "hpA1", "hpC1": "0812T"}
    assert book["old"] == {"x": "2020-01-01 00:00:00"}


def test_rclone_failure_keeps_the_row_and_extracted_rows_are_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHARE_ROOT", str(tmp_path))
    _seed(tmp_path)
    db = SessionLocal()
    try:
        db.add(BriefingExtract(document_id="hpA3", source="homepage", code="80020", status="done",
                               request_key="dx", model="m", prompt_version="v1", submitted_at=SAVED_AT))
        db.commit()
    finally:
        db.close()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(dry_run=False, rclone=lambda argv, timeout: CmdResult(1, "", "boom"))
    assert rc == 1
    text = out.getvalue()
    assert "held extracted hpA3" in text
    assert "found=2 onedrive=2 deleted=0 failed=2 held=1" in text
    assert len(_left()) == 7

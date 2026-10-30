"""EDINET の ZIP を残す・閲覧期間を過ぎたら OneDrive から読む（KAB-281）。"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(tempfile.mkdtemp()) / 'stock.db'}")

import pytest  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.edinet import archive  # noqa: E402
from app.edinet.archive import (  # noqa: E402
    GONE,
    SAVED,
    archive_dir,
    backfill,
    backfill_targets,
    download,
    offsite_root,
    rel_path,
)
from app.edinet.large_holdings import ingest_document  # noqa: E402
from app.models import EdinetArchive, LargeHoldingReport  # noqa: E402
from tests.edinet.test_edinet_large_holdings import (  # noqa: E402
    EdinetNotFoundError,
    _api_err,
    _doc,
    _ErrClient,
    _joint_report,
    _reset,
)

CSV = 5


def _zip(name: str = "a.txt", body: bytes = b"x") -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def db():
    s = SessionLocal()
    _reset(s)
    s.execute(delete(EdinetArchive))
    s.commit()
    yield s
    s.close()


def _not_found(doc_id: str):
    return _api_err(EdinetNotFoundError, 404, "Not Found", doc_id)


def test_rel_path_and_offsite_root() -> None:
    assert rel_path("350", "S100AAAA", 5) == "350/S100AAAA_type5.zip"
    assert offsite_root({}) == "onedrive:stock-analyzer-backups/data/edinet"
    assert offsite_root({"BACKUP_OFFSITE_REMOTE": "od:", "BACKUP_OFFSITE_PATH": "/x/"}) == "od:x/data/edinet"


def test_edinet_then_local(db) -> None:
    doc = _doc(1, "S100AAAA")
    db.add(doc)
    db.commit()
    payload = _zip()
    client = _ErrClient({"S100AAAA": payload})
    got = download(db, client, doc, CSV)
    assert (got.content, got.source) == (payload, "edinet")
    path = archive_dir() / "350" / "S100AAAA_type5.zip"
    assert path.read_bytes() == payload
    assert not list(path.parent.glob("*.tmp"))
    row = db.get(EdinetArchive, ("S100AAAA", CSV))
    assert (row.status, row.bytes, row.rel_path) == (SAVED, len(payload), "350/S100AAAA_type5.zip")
    # 2 回目は手元から。EDINET に行かない
    client.calls.clear()
    got = download(db, client, doc, CSV)
    assert got.source == "local" and client.calls == []


def test_broken_zip_is_not_kept(db) -> None:
    doc = _doc(1, "S100BRKN")
    client = _ErrClient({"S100BRKN": b"not a zip"})
    got = download(db, client, doc, CSV)
    assert got.content == b"not a zip"
    assert db.get(EdinetArchive, ("S100BRKN", CSV)) is None
    assert not (archive_dir() / "350" / "S100BRKN_type5.zip").exists()


def test_404_reads_offsite_only_when_archived(db) -> None:
    doc = _doc(1, "S100GONE")
    client = _ErrClient({"S100GONE": _not_found("S100GONE")})
    asked: list[str] = []

    def remote(rel: str) -> bytes:
        asked.append(rel)
        return b"from-onedrive"

    with pytest.raises(EdinetNotFoundError):
        download(db, client, doc, CSV, remote_get=remote)
    assert asked == []
    db.add(EdinetArchive(doc_id="S100GONE", file_type=CSV, doc_type_code="350",
                         rel_path="350/S100GONE_type5.zip", status=SAVED, saved_at=date(2026, 1, 1)))
    db.commit()
    got = download(db, client, doc, CSV, remote_get=remote)
    assert (got.content, got.source) == (b"from-onedrive", "offsite")
    assert asked == ["350/S100GONE_type5.zip"]


def test_expired_skips_edinet(db) -> None:
    doc = _doc(1, "S100OLD1")
    db.add(EdinetArchive(doc_id="S100OLD1", file_type=CSV, doc_type_code="350",
                         rel_path="350/S100OLD1_type5.zip", status=SAVED, saved_at=date(2026, 1, 1)))
    db.commit()
    client = _ErrClient({})
    got = download(db, client, doc, CSV, expired=True, remote_get=lambda _rel: b"zip")
    assert got.source == "offsite" and client.calls == []


def test_large_holdings_outside_window_reads_archive(db, monkeypatch) -> None:
    """閲覧期間外でも、残してあれば missing にせず OneDrive の ZIP をパースする。"""
    doc = _doc(1, "S100ORIG")
    db.add(doc)
    db.add(EdinetArchive(doc_id="S100ORIG", file_type=CSV, doc_type_code="350",
                         rel_path="350/S100ORIG_type5.zip", status=SAVED, saved_at=date(2026, 1, 1)))
    db.commit()
    payload = _joint_report()
    monkeypatch.setattr(archive, "rclone_get", lambda rel: payload)
    client = _ErrClient({})
    with contextlib.redirect_stdout(io.StringIO()):
        st = ingest_document(db, client, doc, _lookup(db), today=date(2032, 1, 1))
    assert st == "parsed" and client.calls == []
    assert db.get(LargeHoldingReport, "S100ORIG").parse_status == "parsed"
    # 残していなければ今までどおり missing（ダウンロードしない）
    other = _doc(2, "S100NONE")
    db.add(other)
    db.commit()
    with contextlib.redirect_stdout(io.StringIO()):
        st = ingest_document(db, client, other, _lookup(db), today=date(2032, 1, 1))
    assert st == "missing" and client.calls == []


def _lookup(db):
    from app.edinet.documents import CodeLookup

    return CodeLookup.load(db)


def test_backfill_order_and_gone(db) -> None:
    from datetime import datetime

    fetched = datetime(2026, 10, 30)  # noqa: DTZ001
    # 大量保有（5 年）は 2024 年提出、自己株買い（1 年）は 2026 年提出 → 自己株買いが先に期限
    lvh = _doc(1, "S100LVH1", file_date=date(2024, 1, 10))
    bb = _doc(2, "S100BB01", file_date=date(2026, 3, 1), doc_type_code="220")
    done = _doc(3, "S100DONE")
    db.add_all([lvh, bb, done])
    for doc_id in ("S100LVH1", "S100DONE"):
        db.add(LargeHoldingReport(doc_id=doc_id, file_date=date(2024, 1, 10), doc_type_code="350",
                                  original_doc_id=doc_id, parse_status="parsed", fetched_at=fetched,
                                  is_latest=True, withdrawn=False))
    db.add(EdinetArchive(doc_id="S100DONE", file_type=CSV, doc_type_code="350",
                         rel_path="x", status=SAVED, saved_at=fetched))
    _add_buyback(db, "S100BB01", fetched)
    db.commit()
    targets = backfill_targets(db)
    assert [(t.doc.doc_id, t.file_type, t.expires) for t in targets] == [
        ("S100BB01", 1, date(2027, 3, 1)), ("S100LVH1", 5, date(2029, 1, 10))]
    client = _ErrClient({"S100BB01": _not_found("S100BB01"), "S100LVH1": _zip()})
    st = backfill(db, client, targets)
    assert st == {"saved": 1, "gone": 1, "errors": 0}
    assert db.get(EdinetArchive, ("S100BB01", 1)).status == GONE
    assert db.get(EdinetArchive, ("S100LVH1", 5)).status == SAVED
    assert backfill_targets(db) == []


def _add_buyback(db, doc_id: str, fetched) -> None:
    from app.models import EdinetBuyback

    cols = {c.name for c in EdinetBuyback.__table__.columns}
    row = {"doc_id": doc_id, "parse_status": "parsed", "fetched_at": fetched}
    for name, default in (("file_date", date(2026, 3, 1)), ("doc_type_code", "220"),
                          ("original_doc_id", doc_id), ("is_latest", True), ("withdrawn", False)):
        if name in cols:
            row[name] = default
    db.add(EdinetBuyback(**row))


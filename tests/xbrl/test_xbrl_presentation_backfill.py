"""過去の書類の表示リンクを ZIP から埋めるバッチ（jobs/xbrl/xbrl_presentation.py。docs/DESIGN_xbrl.md §4.5）。"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, text

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal, engine, init_db, migrate_add_columns, missing_columns  # noqa: E402
from app.edinet.archive import rel_path  # noqa: E402
from app.edinet.client import EdinetMaintenanceError  # noqa: E402
from app.models import EdinetArchive, EdinetDocument, Stock, XbrlFiling, XbrlPresentationRole  # noqa: E402
from app.xbrl import presentation_backfill as pb  # noqa: E402
from tests.xbrl.test_xbrl_parse import MANUFACTURING, build_zip, seed_stock, write_zip  # noqa: E402
from tests.xbrl.test_xbrl_presentation import (  # noqa: E402
    COMPANY_XSD,
    _annual_pre,
    _company_xsd,
    _ingest,
    _pl,
    _pre,
)

CODE = "99990"
OTHER = "88880"
CREATED = datetime(2026, 1, 1)  # noqa: DTZ001
PRE = f"XBRL/PublicDoc/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml"


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


ANNUAL_ZIP = _zip({
    PRE: _annual_pre(),
    f"XBRL/PublicDoc/{COMPANY_XSD}": _company_xsd("WidgetsCA"),
    "XBRL/PublicDoc/0101010_honbun.htm": "<html/>",
    "XBRL/AuditDoc/jpaud-aar-cn-001_pre.xml": "<broken",
})
NOTICE_ZIP = _zip({"XBRLData/Summary/tse-rvfc-99990-pre.xml": _pre([]), "XBRLData/Summary/x.htm": "<html/>"})


def _reset(db) -> None:
    for model in (XbrlPresentationRole, XbrlFiling, EdinetDocument, EdinetArchive, Stock):
        db.execute(delete(model))
    db.commit()
    seed_stock(db, CODE, "テスト")
    seed_stock(db, OTHER, "ほか")


def _filing(db, fid: int, source: str, submitted: str, *, code: str = CODE, status: str = "parsed",
            raw_path: str | None = None, presentation: str | None = None) -> None:
    db.add(XbrlFiling(
        id=fid, code=code, source=source, doc_id=f"D{fid}", doc_type="annual",
        submitted_at=datetime.fromisoformat(submitted), raw_path=raw_path or f"edinet:D{fid}",
        parse_status=status, presentation_status=presentation, created_at=CREATED,
    ))
    db.commit()


def _doc(db, fid: int, file_date: date = date(2025, 6, 27)) -> None:
    db.add(EdinetDocument(
        file_date=file_date, seq_number=fid, doc_id=f"D{fid}", code=CODE, doc_type_code="120",
        withdrawal_status="0", doc_info_edit_status="0", disclosure_status="0", xbrl_flag=True,
        pdf_flag=True, attach_doc_flag=False, english_doc_flag=False, csv_flag=True, legal_status="1",
        fetched_at=CREATED,
    ))
    db.commit()


def _status(db, fid: int) -> str | None:
    db.expire_all()
    return db.get(XbrlFiling, fid).presentation_status


def _roles(fid: int) -> list[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(
            text("SELECT role FROM xbrl_presentation_roles WHERE filing_id = :f ORDER BY seq"), {"f": fid})]


def test_targets_order_and_filters() -> None:
    db = SessionLocal()
    try:
        _reset(db)
        _filing(db, 1, "edinet", "2024-06-27 15:00")
        _filing(db, 2, "tdnet", "2026-05-10 15:00", raw_path="9999/a.zip")
        _filing(db, 3, "edinet", "2025-06-27 15:00", code=OTHER)
        _filing(db, 4, "edinet", "2026-06-27 15:00", presentation="ok")
        _filing(db, 5, "edinet", "2026-07-01 15:00", status="failed")
        _filing(db, 6, "tdnet_pdf", "2026-08-01 15:00")
        assert pb.targets(db) == [2, 3, 1]
        assert pb.targets(db, sources=["edinet"]) == [3, 1]
        assert pb.targets(db, codes=[OTHER]) == [3]
    finally:
        db.close()


class _Fetch:
    def __init__(self, by_id: dict[int, object]):
        self.by_id = by_id
        self.calls: list[int] = []

    def __call__(self, filing):
        self.calls.append(filing.id)
        got = self.by_id[filing.id]
        if isinstance(got, BaseException):
            raise got
        return pb.Got(got, remote=True)


def test_run_marks_and_resumes() -> None:
    db = SessionLocal()
    try:
        _reset(db)
        for fid, day in ((1, "2026-06-01"), (2, "2026-05-01"), (3, "2026-04-01"), (4, "2026-03-01"),
                         (5, "2026-02-01")):
            _filing(db, fid, "edinet", f"{day} 15:00")
        fetch = _Fetch({
            1: ANNUAL_ZIP,
            2: NOTICE_ZIP,
            3: pb.ZipGone("404"),
            4: b"not a zip",
            5: RuntimeError("timeout"),
        })
        slept: list[float] = []
        counts = pb.run(db, pb.targets(db), {"edinet": fetch}, batch=2, sleep=0.5, sleeper=slept.append)
        assert fetch.calls == [1, 2, 3, 4, 5]
        assert {k: counts[k] for k in ("done", "ok", "none", "gone", "failed", "errors")} == {
            "done": 4, "ok": 1, "none": 1, "gone": 1, "failed": 1, "errors": 1}
        assert slept == [0.5, 0.5, 0.5]
        assert [_status(db, i) for i in (1, 2, 3, 4, 5)] == ["ok", "none", "gone", "failed", None]
        assert _roles(1)[:2] == ["rol_ConsolidatedBalanceSheet", "rol_ConsolidatedStatementOfIncome"]
        assert _roles(2) == []
        assert pb.targets(db) == [5]

        fetch.by_id[5] = _zip({PRE: _pre([_pl("rol_StatementOfIncome")])})
        pb.run(db, pb.targets(db), {"edinet": fetch})
        assert fetch.calls[-1] == 5 and _status(db, 5) == "ok"
        assert pb.targets(db) == []
    finally:
        db.close()


def test_maintenance_stops_after_commits() -> None:
    db = SessionLocal()
    try:
        _reset(db)
        _filing(db, 1, "edinet", "2026-06-01 15:00")
        _filing(db, 2, "edinet", "2026-05-01 15:00")
        _filing(db, 3, "edinet", "2026-04-01 15:00")
        fetch = _Fetch({1: ANNUAL_ZIP, 2: EdinetMaintenanceError("maint"), 3: ANNUAL_ZIP})
        with pytest.raises(EdinetMaintenanceError):
            pb.run(db, pb.targets(db), {"edinet": fetch})
        assert [_status(db, i) for i in (1, 2, 3)] == ["ok", None, None]
        assert pb.targets(db) == [2, 3]
    finally:
        db.close()


def test_tdnet_fetcher(tmp: Path) -> None:
    db = SessionLocal()
    try:
        _reset(db)
        (tmp / "9999").mkdir()
        (tmp / "9999" / "a.zip").write_bytes(ANNUAL_ZIP)
        _filing(db, 1, "tdnet", "2026-06-01 15:00", raw_path="9999/a.zip")
        _filing(db, 2, "tdnet", "2026-05-01 15:00", raw_path="9999/missing.zip")
        pb.run(db, pb.targets(db), {"tdnet": pb.tdnet_fetcher(tmp)})
        assert [_status(db, 1), _status(db, 2)] == ["ok", "gone"]
    finally:
        db.close()


class NotFound(Exception):
    status = 404


class _Client:
    def __init__(self, payloads: dict[str, object]):
        self.payloads = payloads
        self.calls: list[str] = []

    def download(self, doc_id, file_type):
        self.calls.append(doc_id)
        got = self.payloads[doc_id]
        if isinstance(got, BaseException):
            raise got

        class _Got:
            content = got

        return _Got()


def test_edinet_fetcher(tmp: Path) -> None:
    db = SessionLocal()
    try:
        _reset(db)
        for fid in (1, 2, 3, 4, 5, 6):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00")
        for fid in (1, 2, 3, 4, 5):
            _doc(db, fid)
        local = tmp / rel_path("120", "D1", 1)
        local.parent.mkdir(parents=True)
        local.write_bytes(ANNUAL_ZIP)
        client = _Client({"D2": ANNUAL_ZIP, "D3": NotFound("404"), "D4": RuntimeError("503"), "D5": ANNUAL_ZIP})
        fetch = pb.edinet_fetcher(db, client, root=tmp, remote_get=lambda rel: pytest.fail(rel))
        counts = pb.run(db, [1, 2, 3, 4, 6], {"edinet": fetch})
        assert client.calls == ["D2", "D3", "D4"]
        assert [_status(db, i) for i in (1, 2, 3, 4, 6)] == ["ok", "ok", "gone", None, "gone"]
        assert counts["errors"] == 1
        assert (tmp / rel_path("120", "D2", 1)).is_file()

        local_only = pb.edinet_fetcher(db, None, root=tmp)
        counts = pb.run(db, [5], {"edinet": local_only})
        assert counts["skipped"] == 1 and _status(db, 5) is None
    finally:
        db.close()


def test_archived_zip_is_read_from_offsite_without_edinet(tmp: Path) -> None:
    from app.edinet.archive import SAVED
    from app.edinet.documents import now_jst
    from app.models import EdinetArchive

    db = SessionLocal()
    try:
        _reset(db)
        for fid in (1, 2):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00")
            _doc(db, fid)
        db.add(EdinetArchive(doc_id="D1", file_type=1, doc_type_code="120", rel_path=rel_path("120", "D1", 1),
                             status=SAVED, bytes=len(ANNUAL_ZIP), sha256="x", saved_at=now_jst()))
        db.commit()
        assert pb.targets(db, archived_only=True) == [1]
        client = _Client({"D2": ANNUAL_ZIP})
        asked: list[str] = []

        def remote_get(rel: str) -> bytes:
            asked.append(rel)
            return ANNUAL_ZIP

        fetch = pb.edinet_fetcher(db, client, root=tmp, remote_get=remote_get)
        counts = pb.run(db, [1, 2], {"edinet": fetch})
        assert asked == [rel_path("120", "D1", 1)] and client.calls == ["D2"]
        assert [_status(db, i) for i in (1, 2)] == ["ok", "ok"]
        assert counts["offsite"] == 1 and counts["edinet"] == 1 and counts["local"] == 0
        assert pb.origins(counts).startswith("local=0/0s edinet=1/")
    finally:
        db.close()


def test_archived_zips_are_staged_in_bulk(tmp: Path) -> None:
    from app.edinet.archive import SAVED
    from app.edinet.documents import now_jst
    from app.models import EdinetArchive

    db = SessionLocal()
    try:
        _reset(db)
        for fid in (1, 2, 3):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00")
            _doc(db, fid)
        for doc_id in ("D1", "D2"):
            db.add(EdinetArchive(doc_id=doc_id, file_type=1, doc_type_code="120", rel_path=rel_path("120", doc_id, 1),
                                 status=SAVED, bytes=len(ANNUAL_ZIP), sha256="x", saved_at=now_jst()))
        db.commit()
        root, staged = tmp / "root", tmp / "staged"
        local = root / rel_path("120", "D2", 1)
        local.parent.mkdir(parents=True)
        local.write_bytes(ANNUAL_ZIP)
        asked: list[list[str]] = []

        def copy(rels: list[str], dest: Path) -> None:
            asked.append(rels)
            for rel in rels:
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_bytes(ANNUAL_ZIP)

        assert pb.stage_offsite(db, [1, 2, 3], staged, root=root, copy=copy) == 1
        assert asked == [[rel_path("120", "D1", 1)]]
        client = _Client({"D3": ANNUAL_ZIP})
        fetch = pb.edinet_fetcher(db, client, root=root, remote_get=lambda rel: pytest.fail(rel), staged=staged)
        counts = pb.run(db, [1, 2, 3], {"edinet": fetch}, sleep=1.0, sleeper=lambda s: None)
        assert client.calls == ["D3"]
        assert [_status(db, i) for i in (1, 2, 3)] == ["ok", "ok", "ok"]
        assert (counts["offsite"], counts["local"], counts["edinet"]) == (1, 1, 1)
        assert pb.stage_offsite(db, [1, 2, 3], staged, root=root, copy=copy) == 0 and len(asked) == 1
    finally:
        db.close()


def _stored(db, fid: int, items: list[list]) -> None:
    db.add(XbrlPresentationRole(
        filing_id=fid, role="rol_BalanceSheet", seq=0, statement="BS", is_consolidated=False, is_ifrs=False,
        items_json=json.dumps(items), generated_at=CREATED,
    ))
    db.commit()


def _first_items(fid: int) -> list[list]:
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT items_json FROM xbrl_presentation_roles WHERE filing_id = :f AND seq = 0"),
                           {"f": fid}).scalar()
    return json.loads(raw)


FLAT = [["jppfs_cor", "AssetsAbstract", 0, None], ["jppfs_cor", "Assets", 0, "totalLabel"]]
DEPTH_ONLY = [["jppfs_cor", "AssetsAbstract", 0, None, 0], ["jppfs_cor", "Assets", 0, "totalLabel", 1]]
DEEP = [["jppfs_cor", "AssetsAbstract", 0, None, 0, "資産の部"], ["jppfs_cor", "Assets", 0, "totalLabel", 1, None]]


def test_redo_targets_ok_filings_without_depth() -> None:
    db = SessionLocal()
    try:
        _reset(db)
        for fid, presentation, items in ((1, "ok", FLAT), (2, "ok", DEEP), (3, None, None), (4, "none", None),
                                         (5, "ok", DEPTH_ONLY)):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00", presentation=presentation,
                    code=OTHER if fid == 5 else CODE)
            if items:
                _stored(db, fid, items)
        _filing(db, 6, "tdnet", "2026-06-06 15:00", raw_path="9999/a.zip", presentation="ok")
        _stored(db, 6, FLAT)
        assert pb.targets(db) == [3]
        assert pb.targets(db, redo=True) == [6, 5, 1]
        assert pb.targets(db, redo=True, sources=["edinet"], codes=[CODE]) == [1]
        assert pb.run(db, [3], {"edinet": _Fetch({3: ANNUAL_ZIP})}, redo=True)["skipped"] == 1
    finally:
        db.close()


def test_redo_rewrites_with_depth_and_keeps_rows_it_cannot_read() -> None:
    db = SessionLocal()
    try:
        _reset(db)
        for fid in (1, 2, 3, 4, 5):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00", presentation="ok")
            _stored(db, fid, FLAT)
        fetch = _Fetch({1: ANNUAL_ZIP, 2: pb.ZipGone("404"), 3: b"not a zip", 4: NOTICE_ZIP,
                        5: RuntimeError("timeout")})
        counts = pb.run(db, pb.targets(db, redo=True), {"edinet": fetch}, redo=True)
        assert {k: counts[k] for k in ("ok", "none", "gone", "failed", "errors")} == {
            "ok": 1, "none": 1, "gone": 1, "failed": 1, "errors": 1}
        assert [_status(db, i) for i in (1, 2, 3, 4, 5)] == ["ok"] * 5
        assert [item[:5] for item in _first_items(1)[:2]] == [["jppfs_cor", "AssetsAbstract", 0, None, 0],
                                                            ["jppfs_cor", "CashAndDeposits", 0, None, 1]]
        assert len(_first_items(1)[0]) == 6
        assert all(_first_items(i) == FLAT for i in (2, 3, 4, 5))
        assert pb.targets(db, redo=True) == [5, 4, 3, 2]
    finally:
        db.close()


def test_ingest_marks_status(tmp: Path) -> None:
    db = SessionLocal()
    try:
        _reset(db)
        plain = _ingest(db, write_zip(tmp, "plain.zip", build_zip(MANUFACTURING)), doc_id="plain")
        assert _status(db, plain.id) == "none"
        files = {f"XBRLData/Attachment/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml": _annual_pre()}
        zpath = write_zip(tmp, "pre.zip", build_zip(MANUFACTURING, extra_files=files))
        filing = _ingest(db, zpath)
        assert _status(db, filing.id) == "ok"
        assert filing.id not in pb.targets(db)
        files[f"XBRLData/Attachment/{COMPANY_XSD.removesuffix('.xsd')}_pre.xml"] = _pre([])
        _ingest(db, write_zip(tmp, "pre.zip", build_zip(MANUFACTURING, extra_files=files)))
        assert _status(db, filing.id) == "none" and _roles(filing.id) == []
    finally:
        db.close()


def test_migrate_adds_column_to_existing_db() -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE xbrl_filings DROP COLUMN presentation_status"))
    assert missing_columns() == {"xbrl_filings": ["presentation_status"]}
    assert migrate_add_columns(verbose=False) == 1
    init_db(strict=True)
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(xbrl_filings)"))]
    assert "presentation_status" in cols


def test_cli_dry_run(capsys) -> None:
    from jobs.xbrl import xbrl_presentation

    db = SessionLocal()
    try:
        _reset(db)
        _filing(db, 1, "edinet", "2026-06-01 15:00")
        _filing(db, 2, "tdnet", "2026-05-01 15:00", raw_path="9999/a.zip")
    finally:
        db.close()
    assert xbrl_presentation.main(["--dry-run"]) == 0
    assert "remaining=2" in capsys.readouterr().out
    assert xbrl_presentation.main(["--dry-run", "--source", "tdnet", "--code", "9999"]) == 0
    assert "remaining=1" in capsys.readouterr().out


def test_cli_redo_flat(capsys) -> None:
    from jobs.xbrl import xbrl_presentation

    db = SessionLocal()
    try:
        _reset(db)
        _filing(db, 1, "edinet", "2026-06-01 15:00")
        _filing(db, 2, "edinet", "2026-06-02 15:00", presentation="ok")
        _stored(db, 2, FLAT)
        _filing(db, 3, "edinet", "2026-06-03 15:00", presentation="ok")
        _stored(db, 3, DEEP)
    finally:
        db.close()
    assert xbrl_presentation.main(["--dry-run", "--redo-flat"]) == 0
    assert "remaining=1" in capsys.readouterr().out


def test_cli_offsite_chunk(capsys, monkeypatch) -> None:
    from app.edinet.archive import SAVED
    from app.edinet.documents import now_jst
    from app.models import EdinetArchive
    from jobs.xbrl import xbrl_presentation

    db = SessionLocal()
    try:
        _reset(db)
        for fid in (1, 2, 3):
            _filing(db, fid, "edinet", f"2026-06-0{fid} 15:00")
            _doc(db, fid)
            db.add(EdinetArchive(doc_id=f"D{fid}", file_type=1, doc_type_code="120",
                                 rel_path=rel_path("120", f"D{fid}", 1), status=SAVED, bytes=len(ANNUAL_ZIP),
                                 sha256="x", saved_at=now_jst()))
        db.commit()
    finally:
        db.close()
    chunks: list[list[int]] = []

    def stage(db, ids, staged, **_kw) -> int:
        chunks.append(list(ids))
        for fid in ids:
            path = staged / rel_path("120", f"D{fid}", 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ANNUAL_ZIP)
        return len(chunks[-1])

    monkeypatch.setattr(xbrl_presentation, "stage_offsite", stage)
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    assert xbrl_presentation.main(["--archived-only", "--offsite-chunk", "2"]) == 0
    out = capsys.readouterr().out
    assert chunks == [[3, 2], [1]]
    assert "offsite-copy files=2" in out and "offsite-copy files=1" in out
    assert "done=3 ok=3" in out and "remaining=0" in out and "offsite=3/" in out

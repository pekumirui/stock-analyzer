"""EDINET メンテ中は 1 件目で打ち切り、WARN 1 行、exit 0（KAB-149）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.edinet.client import (
    EdinetMaintenanceError,
    is_maintenance,
    reraise,
    skip_on_maintenance,
    warn_maintenance,
)
from app.edinet.documents import ingest_days
from app.edinet_wrapper.errors import EdinetMaintenanceError as VendorMaint
from app.edinet_wrapper.errors import EdinetServerError
from app.models import EdinetDocument, EdinetListDay, XbrlJobRun
from tests.edinet.test_edinet_buybacks import _doc as _buyback_doc
from tests.edinet.test_edinet_buybacks import _reset as _buyback_reset
from tests.edinet.test_edinet_large_holdings import _ErrClient
from tests.edinet.test_edinet_large_holdings import _doc as _lvh_doc
from tests.edinet.test_edinet_large_holdings import _reset as _lvh_reset
from tests.edinet.test_edinet_tender_offers import _doc as _tob_doc
from tests.edinet.test_edinet_tender_offers import _reset as _tob_reset
from tests.edinet.test_edinet_xbrl import _doc as _xbrl_doc
from tests.edinet.test_edinet_xbrl import _reset as _xbrl_reset


def _html() -> VendorMaint:
    return VendorMaint("EDINET returned HTML instead of API data", content_type="text/html")


def _redirect() -> VendorMaint:
    return VendorMaint(
        "HTTP 302 redirect; treating as a maintenance page", content_type="text/html",
    )


def _server() -> EdinetServerError:
    return EdinetServerError("Internal Server Error", status=500, endpoint="/documents/S100MNT1")


class MaintClient:
    def __init__(self, exc: BaseException):
        self.exc = exc
        self.calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def download(self, doc_id, file_type):
        self.calls.append(doc_id)
        raise self.exc

    def list_documents(self, day):
        self.calls.append(day)
        raise self.exc


def test_is_maintenance_and_reraise() -> None:
    assert is_maintenance(_html())
    assert is_maintenance(_redirect())
    assert is_maintenance(EdinetMaintenanceError("app"))
    assert not is_maintenance(_server())
    assert not is_maintenance(RuntimeError("boom"))
    with pytest.raises(EdinetMaintenanceError) as ei:
        reraise(_html())
    assert "HTML instead of API data" in str(ei.value)
    reraise(_server())
    reraise(RuntimeError("boom"))


def test_warn_maintenance(capsys) -> None:
    assert warn_maintenance("edinet_xbrl", _html()) == 0
    out = capsys.readouterr().out
    assert out.count("WARN edinet maintenance skip") == 1
    assert "job=edinet_xbrl" in out
    assert skip_on_maintenance("edinet_xbrl", _server()) is None


def _assert_aborts(run_fn, client, first) -> None:
    with pytest.raises(EdinetMaintenanceError):
        run_fn()
    assert client.calls == [first]


def test_xbrl_run_aborts_after_first() -> None:
    from app.edinet.xbrl import run

    init_db()
    db = SessionLocal()
    try:
        _xbrl_reset(db)
        db.add_all([_xbrl_doc(1, "S100MNT1"), _xbrl_doc(2, "S100MNT2")])
        db.commit()
        client = _ErrClient({"S100MNT1": _html(), "S100MNT2": _html()})
        _assert_aborts(lambda: run(db, client, [_xbrl_doc(1, "S100MNT1"), _xbrl_doc(2, "S100MNT2")]),
                       client, "S100MNT1")
    finally:
        db.close()


def test_lvh_run_aborts_after_first() -> None:
    from app.edinet.large_holdings import run

    init_db()
    db = SessionLocal()
    try:
        _lvh_reset(db)
        docs = [_lvh_doc(1, "S100MNT1"), _lvh_doc(2, "S100MNT2")]
        db.add_all(docs)
        db.commit()
        client = _ErrClient({"S100MNT1": _redirect(), "S100MNT2": _html()})
        _assert_aborts(lambda: run(db, client, docs), client, "S100MNT1")
    finally:
        db.close()


def test_buybacks_run_aborts_after_first() -> None:
    from app.edinet.buybacks import run

    init_db()
    db = SessionLocal()
    try:
        _buyback_reset(db)
        docs = [_buyback_doc(1, "S100MNT1"), _buyback_doc(2, "S100MNT2")]
        db.add_all(docs)
        db.commit()
        client = _ErrClient({"S100MNT1": _html(), "S100MNT2": _html()})
        _assert_aborts(lambda: run(db, client, docs), client, "S100MNT1")
    finally:
        db.close()


def test_tender_offers_run_aborts_after_first() -> None:
    from app.edinet.tender_offers import run

    init_db()
    db = SessionLocal()
    try:
        _tob_reset(db)
        docs = [_tob_doc(1, "S100MNT1", "240"), _tob_doc(2, "S100MNT2", "240")]
        db.add_all(docs)
        db.commit()
        client = _ErrClient({"S100MNT1": _html(), "S100MNT2": _html()})
        _assert_aborts(lambda: run(db, client, docs), client, "S100MNT1")
    finally:
        db.close()


def test_documents_ingest_days_aborts_after_first() -> None:
    init_db()
    db = SessionLocal()
    try:
        db.execute(delete(EdinetListDay))
        db.execute(delete(EdinetDocument))
        db.commit()
        days = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
        client = MaintClient(_html())
        _assert_aborts(lambda: ingest_days(db, client, days, log=lambda *_: None),
                       client, days[0])
    finally:
        db.close()


def test_xbrl_5xx_continues_all_docs() -> None:
    from app.edinet.xbrl import run

    init_db()
    db = SessionLocal()
    try:
        _xbrl_reset(db)
        docs = [_xbrl_doc(1, "S100SRV1"), _xbrl_doc(2, "S100SRV2")]
        db.add_all(docs)
        db.commit()
        err = EdinetServerError("Internal Server Error", status=500, endpoint="/documents/x")
        client = _ErrClient({"S100SRV1": err, "S100SRV2": err})
        st = run(db, client, docs)
        assert client.calls == ["S100SRV1", "S100SRV2"]
        assert st["errors"] == 2
    finally:
        db.close()


def _patch_client(monkeypatch, client) -> None:
    monkeypatch.setenv("EDINET_API_KEY", "test-key")
    monkeypatch.setattr("app.edinet_wrapper.EdinetClient", lambda *a, **k: client)


def _seed_two(db, kind: str) -> None:
    if kind == "xbrl":
        _xbrl_reset(db)
        db.add_all([_xbrl_doc(1, "S100MNT1"), _xbrl_doc(2, "S100MNT2")])
    elif kind == "lvh":
        _lvh_reset(db)
        db.add_all([_lvh_doc(1, "S100MNT1"), _lvh_doc(2, "S100MNT2")])
    elif kind == "buyback":
        _buyback_reset(db)
        db.add_all([_buyback_doc(1, "S100MNT1"), _buyback_doc(2, "S100MNT2")])
    elif kind == "tob":
        _tob_reset(db)
        db.add_all([_tob_doc(1, "S100MNT1", "240"), _tob_doc(2, "S100MNT2", "240")])
    else:
        db.execute(delete(EdinetListDay))
        db.execute(delete(EdinetDocument))
    db.commit()


@pytest.mark.parametrize("job,kind,mod,argv", [
    ("edinet_documents", "docs", "jobs.edinet.edinet_documents",
     ["edinet_documents.py", "--from", "2026-10-20", "--to", "2026-10-22", "--force"]),
    ("edinet_xbrl", "xbrl", "jobs.edinet.edinet_xbrl", ["edinet_xbrl.py", "--limit", "10"]),
    ("edinet_large_holdings", "lvh", "jobs.edinet.edinet_large_holdings",
     ["edinet_large_holdings.py", "--limit", "10"]),
    ("edinet_tender_offers", "tob", "jobs.edinet.edinet_tender_offers",
     ["edinet_tender_offers.py", "--limit", "10"]),
    ("edinet_buybacks", "buyback", "jobs.edinet.edinet_buybacks",
     ["edinet_buybacks.py", "--limit", "10"]),
])
def test_job_main_maintenance_exit_0(monkeypatch, capsys, job, kind, mod, argv) -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed_two(db, kind)
    finally:
        db.close()
    client = MaintClient(_html())
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(sys, "argv", argv)
    import importlib
    main = importlib.import_module(mod).main
    assert main() == 0
    out = capsys.readouterr().out
    assert out.count("WARN edinet maintenance skip") == 1
    assert f"job={job}" in out
    assert "download-failed" not in out
    assert len(client.calls) == 1
    if job == "edinet_xbrl":
        db = SessionLocal()
        try:
            row = (db.query(XbrlJobRun).filter_by(job_name="edinet_xbrl")
                   .order_by(XbrlJobRun.id.desc()).first())
            assert (row.status, row.message) == ("skipped", "maintenance skip")
        finally:
            db.close()


def test_xbrl_main_5xx_still_exit_1(monkeypatch, capsys) -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed_two(db, "xbrl")
    finally:
        db.close()
    client = MaintClient(_server())
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(sys, "argv", ["edinet_xbrl.py", "--limit", "10"])
    from jobs.edinet.edinet_xbrl import main
    assert main() == 1
    out = capsys.readouterr().out
    assert "WARN edinet maintenance skip" not in out
    assert client.calls == ["S100MNT1", "S100MNT2"]


def test_documents_main_5xx_still_raises(monkeypatch) -> None:
    init_db()
    client = MaintClient(_server())
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        sys, "argv",
        ["edinet_documents.py", "--from", "2026-10-20", "--to", "2026-10-22", "--force"],
    )
    from jobs.edinet.edinet_documents import main
    with pytest.raises(EdinetServerError):
        main()
    assert len(client.calls) == 1

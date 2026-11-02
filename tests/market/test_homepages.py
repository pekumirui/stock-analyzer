"""KAB-319: homepages の抽出・順位・冪等。"""
from __future__ import annotations

import gzip
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal, upsert
from app.market.homepages import (
    SOURCE_MANUAL,
    SOURCE_TANSHIN_HTML,
    SOURCE_XBRL_URL,
    compare_hosts,
    extract_urls_from_html,
    host_is_skipped,
    normalize_url,
    ordinary_code,
    pick_homepage_url,
    set_manual,
    sync_homepages,
    url_host,
)
from app.models import (
    Homepage,
    Stock,
    TdnetPdfDocument,
    XbrlContext,
    XbrlElement,
    XbrlFact,
    XbrlFiling,
)
from jobs.market.homepages import main as homepages_main

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_HTML = ROOT / "tests" / "golden" / "tdnet_pdf" / "html"
NOW = datetime(2026, 11, 2, 16, 30)  # noqa: DTZ001
LATER = datetime(2026, 11, 3, 9, 0)  # noqa: DTZ001


@pytest.fixture(autouse=True)
def _clean_homepage_tables() -> None:
    db = SessionLocal()
    try:
        db.query(Homepage).delete()
        db.query(TdnetPdfDocument).delete()
        db.query(XbrlFact).delete()
        db.query(XbrlContext).delete()
        db.query(XbrlFiling).delete()
        db.commit()
    finally:
        db.close()


def _gz(path: Path) -> bytes:
    return path.read_bytes()


def _seed_stock(db, code: str, name: str = "x") -> None:
    upsert(db, Stock, [{
        "code": code, "name": name, "sector17": "x", "sector33": "x",
        "market": "プライム", "scale_category": "", "margin_type": "",
        "product_category": "011",
    }])


def _seed_tanshin(
    db,
    *,
    disclosure_no: str,
    code: str,
    title: str,
    html_gz: bytes,
    disclosed_date: date | None = None,
    status: str = "ok",
) -> None:
    db.merge(TdnetPdfDocument(
        disclosure_no=disclosure_no,
        code=code,
        disclosed_date=disclosed_date or date.fromisoformat(
            disclosure_no[:4] + "-" + disclosure_no[4:6] + "-" + disclosure_no[6:8]),
        title=title,
        status=status,
        html_gz=html_gz,
        created_at=NOW,
    ))
    db.commit()


def _seed_xbrl_url(
    db, *, code: str, url: str, doc_id: str, submitted_at: datetime,
) -> None:
    elem = db.execute(
        select(XbrlElement).where(
            XbrlElement.namespace_key == "tse-ed-t",
            XbrlElement.local_name == "URL",
        )
    ).scalar_one_or_none()
    if elem is None:
        elem = XbrlElement(
            namespace_uri="http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12",
            namespace_key="tse-ed-t",
            local_name="URL",
            first_seen_at=NOW,
        )
        db.add(elem)
        db.flush()
    filing = XbrlFiling(
        code=code,
        source="tdnet",
        doc_id=doc_id,
        doc_type="tanshin",
        submitted_at=submitted_at,
        raw_path=f"{doc_id}.zip",
        parse_status="ok",
        created_at=NOW,
    )
    db.add(filing)
    db.flush()
    ctx = XbrlContext(
        filing_id=filing.id,
        context_ref="CurrentYearInstant",
        period_type="instant",
        end_date=date(2026, 3, 31),
    )
    db.add(ctx)
    db.flush()
    db.add(XbrlFact(
        filing_id=filing.id,
        element_id=elem.id,
        context_id=ctx.id,
        value_text=url,
        is_nil=False,
    ))
    db.commit()


def test_ordinary_code_collapses_preferred_and_four_digit() -> None:
    assert ordinary_code("7203") == "72030"
    assert ordinary_code("72031") == "72030"
    assert ordinary_code("262A0") == "262A0"


def test_skip_host_is_exact_or_dot_suffix() -> None:
    assert host_is_skipped("x.com")
    assert host_is_skipped("www.x.com")
    assert host_is_skipped("sec.gov")
    assert host_is_skipped("www.sec.gov")
    assert host_is_skipped("line.me")
    assert not host_is_skipped("examplex.com")
    assert not host_is_skipped("toyota-tsusho.com")
    assert not host_is_skipped("nsec.gov")
    assert not host_is_skipped("online.me")
    assert normalize_url("https://examplex.com/") == "https://examplex.com/"
    assert normalize_url("https://www.x.com/foo") is None
    assert normalize_url("https://x.com/foo") is None
    assert normalize_url("https://online.me/") == "https://online.me/"


def test_normalize_url_fullwidth_colon_and_schemeless_www() -> None:
    assert normalize_url("https：//www.example.co.jp/") == "https://www.example.co.jp/"
    assert normalize_url("www.nabtesco.com") == "https://www.nabtesco.com"
    assert normalize_url("https://www.w3.org/TR/html") is None
    assert normalize_url("https://www.tepco.co.jp/a.pdf") is None
    assert url_host("https://www.terumo.co.jp/") == "terumo.co.jp"


def test_golden_tanshin_takes_cover_homepage_not_ir_path() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20260212557842.html.gz")).decode("utf-8")
    url = pick_homepage_url(html)
    assert url == "https://www.tepco.co.jp/index-j.html"


def test_golden_tanshin_homepage() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20240510589463.html.gz")).decode("utf-8")
    assert pick_homepage_url(html) == "https://www.terumo.co.jp/"


def test_label_near_url_beats_earlier_auditor_url() -> None:
    html = (
        "<p>監査法人 https://www.deloitte.com/</p>"
        "<p>URL https://www.example.co.jp/</p>"
        "<p>親会社 https://www.parent.co.jp/</p>"
    )
    assert pick_homepage_url(html) == "https://www.example.co.jp/"
    assert extract_urls_from_html(html)[0] == "https://www.deloitte.com/"


def test_homepage_label_beats_first_url() -> None:
    html = (
        "<p>https://www.auditor.example/</p>"
        "<p>ホームページ https://www.child.example/</p>"
    )
    assert pick_homepage_url(html) == "https://www.child.example/"


def test_non_tanshin_title_is_not_read() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "72030")
        _seed_tanshin(
            db, disclosure_no="20211215452790", code="72030",
            title="配当予想に関するお知らせ",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_homepages(db, ["72030"], now=NOW)
        assert report.chosen.get("none") == 1
        assert db.get(Homepage, "72030") is None
    finally:
        db.close()


def test_tanshin_html_writes_homepage() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        first = sync_homepages(db, ["45430"], now=NOW)
        row = db.get(Homepage, "45430")
        assert (row.source, row.url, row.origin, row.host) == (
            SOURCE_TANSHIN_HTML, "https://www.terumo.co.jp/",
            "tanshin:20240510589463", "terumo.co.jp")
        assert row.changed_at == NOW and row.checked_at == NOW
        assert first.put == 1
        second = sync_homepages(db, ["45430"], now=LATER)
        row = db.get(Homepage, "45430")
        assert second.put == 0 and second.unchanged == 1
        assert row.changed_at == NOW and row.checked_at == LATER
    finally:
        db.close()


def test_xbrl_url_wins_when_not_older_than_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "95010")
        _seed_xbrl_url(
            db, code="95010", url="https://www.tepco.co.jp/",
            doc_id="20260212557842", submitted_at=datetime(2026, 2, 12, 17),  # noqa: DTZ001
        )
        _seed_tanshin(
            db, disclosure_no="20260212557842", code="95010",
            title="2026年３月期第３四半期決算短信〔日本基準〕（連結）",
            html_gz=_gz(GOLDEN_HTML / "20260212557842.html.gz"),
        )
        sync_homepages(db, ["95010"], now=NOW)
        row = db.get(Homepage, "95010")
        assert row.source == SOURCE_XBRL_URL
        assert row.url == "https://www.tepco.co.jp/"
        assert row.origin == "xbrl:20260212557842"
        assert row.host == "tepco.co.jp"
    finally:
        db.close()


def test_newer_tanshin_beats_older_xbrl() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "45430")
        _seed_xbrl_url(
            db, code="45430", url="https://www.old-terumo.example/",
            doc_id="old-xbrl", submitted_at=datetime(2024, 1, 1),  # noqa: DTZ001
        )
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        sync_homepages(db, ["45430"], now=NOW)
        row = db.get(Homepage, "45430")
        assert row.source == SOURCE_TANSHIN_HTML
        assert row.url == "https://www.terumo.co.jp/"
        assert row.origin == "tanshin:20240510589463"
    finally:
        db.close()


def test_latest_xbrl_invalid_does_not_fall_back() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "80150")
        _seed_xbrl_url(
            db, code="80150", url="https://www.nabtesco.com",
            doc_id="older", submitted_at=datetime(2025, 1, 1),  # noqa: DTZ001
        )
        _seed_xbrl_url(
            db, code="80150", url="https://www.w3.org/TR",
            doc_id="newer", submitted_at=datetime(2026, 6, 1),  # noqa: DTZ001
        )
        report = sync_homepages(db, ["80150"], now=NOW)
        assert report.chosen.get("none") == 1
        assert db.get(Homepage, "80150") is None
    finally:
        db.close()


def test_manual_survives_refresh() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "72030")
        set_manual(db, "7203", "https://global.toyota/jp/", now=NOW)
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="72030",
            title="決算短信",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_homepages(db, ["72030"], refresh=True, now=LATER)
        row = db.get(Homepage, "72030")
        assert report.unchanged == 1
        assert row.source == SOURCE_MANUAL
        assert row.url == "https://global.toyota/jp/"
        assert row.origin == "manual"
        assert row.checked_at == LATER
        assert row.changed_at == NOW
    finally:
        db.close()


def test_xbrl_normalizes_schemeless_www() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "80150")
        _seed_xbrl_url(
            db, code="80150", url="www.nabtesco.com",
            doc_id="20260399999999", submitted_at=datetime(2026, 5, 1),  # noqa: DTZ001
        )
        sync_homepages(db, ["80150"], now=NOW)
        row = db.get(Homepage, "80150")
        assert row.url == "https://www.nabtesco.com"
        assert row.host == "nabtesco.com"
    finally:
        db.close()


def test_dry_run_does_not_write() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_homepages(db, ["45430"], dry_run=True, now=NOW)
        assert report.put == 1
        assert db.get(Homepage, "45430") is None
    finally:
        db.close()


def test_preferred_share_writes_ordinary_code() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "72030")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="72030",
            title="決算短信",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        sync_homepages(db, ["72031"], now=NOW)
        assert db.get(Homepage, "72030") is not None
        assert db.get(Homepage, "72031") is None
    finally:
        db.close()


def test_compare_hosts_same_and_diff() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "95010")
        _seed_xbrl_url(
            db, code="95010", url="https://www.tepco.co.jp/",
            doc_id="x", submitted_at=datetime(2026, 2, 12),  # noqa: DTZ001
        )
        _seed_tanshin(
            db, disclosure_no="20260212557842", code="95010",
            title="2026年３月期第３四半期決算短信〔日本基準〕（連結）",
            html_gz=_gz(GOLDEN_HTML / "20260212557842.html.gz"),
        )
        report = compare_hosts(db, ["95010"])
        assert report.both == 1 and report.same_host == 1 and report.diff_host == 0
    finally:
        db.close()


def test_job_does_not_create_tables() -> None:
    src = (ROOT / "jobs" / "market" / "homepages.py").read_text(encoding="utf-8")
    assert "init_db" not in src and "create_all" not in src


def test_sources_do_not_use_ir_monthly_or_url_kind() -> None:
    needles = (
        "ir_monthly.json", "url_kind", "homepage_nav", "ir_sites",
        "_r2common", "http_get_result",
    )
    for rel in ("app/market/homepages.py", "jobs/market/homepages.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in src, f"{rel} still mentions {needle}"
    model = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert "url_kind" not in model and "ir_sites" not in model and "class IrSite" not in model
    assert "confidence" not in model.split("class Homepage")[1].split("class ")[0]
    assert "fetched_at" not in model.split("class Homepage")[1].split("class ")[0]


def test_job_main_dry_run_does_not_write() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
    finally:
        db.close()
    rc = homepages_main(["--code", "4543", "--dry-run"])
    assert rc == 0
    db = SessionLocal()
    try:
        assert db.get(Homepage, "45430") is None
    finally:
        db.close()


def test_job_set_manual() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "72030")
    finally:
        db.close()
    rc = homepages_main(["--set", "7203", "https://global.toyota/jp/"])
    assert rc == 0
    db = SessionLocal()
    try:
        row = db.get(Homepage, "72030")
        assert row.source == SOURCE_MANUAL and row.host == "global.toyota"
    finally:
        db.close()

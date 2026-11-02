"""KAB-319: homepages の抽出・順位・冪等。"""
from __future__ import annotations

import gzip
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import SessionLocal, upsert
from app.market.homepages import (
    SOURCE_TANSHIN_HTML,
    SOURCE_XBRL_URL,
    extract_urls_from_html,
    normalize_url,
    ordinary_code,
    pick_homepage_url,
    sync_homepages,
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


def _seed_xbrl_url(db, *, code: str, url: str, doc_id: str, submitted_at: datetime) -> None:
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


def test_normalize_url_fullwidth_colon_and_schemeless_www() -> None:
    assert normalize_url("https：//www.example.co.jp/") == "https://www.example.co.jp/"
    assert normalize_url("www.nabtesco.com") == "https://www.nabtesco.com"
    assert normalize_url("https://www.w3.org/TR/html") is None
    assert normalize_url("https://www.tepco.co.jp/a.pdf") is None


def test_golden_tanshin_takes_cover_homepage_not_ir_path() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20260212557842.html.gz")).decode("utf-8")
    url = pick_homepage_url(extract_urls_from_html(html))
    assert url == "https://www.tepco.co.jp/index-j.html"


def test_golden_tanshin_homepage() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20240510589463.html.gz")).decode("utf-8")
    assert pick_homepage_url(extract_urls_from_html(html)) == "https://www.terumo.co.jp/"


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
        assert (row.source, row.url, row.confidence, row.origin) == (
            SOURCE_TANSHIN_HTML, "https://www.terumo.co.jp/", "medium",
            "20240510589463")
        assert first.put == 1
        second = sync_homepages(db, ["45430"], now=NOW)
        assert second.put == 0 and second.unchanged == 1
    finally:
        db.close()


def test_xbrl_url_wins_over_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "95010")
        _seed_xbrl_url(
            db, code="95010", url="https://www.tepco.co.jp/",
            doc_id="20260212557842", submitted_at=datetime(2026, 2, 12),  # noqa: DTZ001
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
        assert row.confidence == "high"
        assert row.origin == "xbrl:20260212557842"
    finally:
        db.close()


def test_does_not_downgrade_without_refresh() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "45430")
        _seed_xbrl_url(
            db, code="45430", url="https://www.terumo.co.jp/",
            doc_id="20240510589463", submitted_at=datetime(2026, 5, 1),  # noqa: DTZ001
        )
        sync_homepages(db, ["45430"], now=NOW)
        db.query(XbrlFact).delete()
        db.query(XbrlContext).delete()
        db.query(XbrlFiling).delete()
        db.commit()
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_homepages(db, ["45430"], now=NOW)
        assert report.unchanged == 1
        assert db.get(Homepage, "45430").source == SOURCE_XBRL_URL
        refreshed = sync_homepages(db, ["45430"], refresh=True, now=NOW)
        assert refreshed.put == 1
        assert db.get(Homepage, "45430").source == SOURCE_TANSHIN_HTML
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
        assert db.get(Homepage, "80150").url == "https://www.nabtesco.com"
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


def test_skips_tdnet_and_takes_first_company_url() -> None:
    html = (
        "<p>https://www.tdnet.info/foo</p>"
        "<p>https://www.example.co.jp/</p>"
        "<p>https://www.example.co.jp/ir/</p>"
    )
    assert pick_homepage_url(extract_urls_from_html(html)) == "https://www.example.co.jp/"


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

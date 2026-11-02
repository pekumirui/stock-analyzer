"""KAB-319: ir_sites の抽出・順位・冪等。"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import select

from app.db import SessionLocal, upsert
from app.market.ir_sites import (
    SOURCE_HOMEPAGE_NAV,
    SOURCE_IR_MONTHLY,
    SOURCE_TANSHIN_HTML,
    extract_urls_from_html,
    harvest_ir_href,
    load_ir_monthly_urls,
    normalize_url,
    ordinary_code,
    pick_tanshin_url,
    sync_ir_sites,
)
from app.models import (
    IrSite,
    Stock,
    TdnetPdfDocument,
    XbrlContext,
    XbrlElement,
    XbrlFact,
    XbrlFiling,
)
from jobs.market.ir_sites import main as ir_sites_main

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_HTML = ROOT / "tests" / "golden" / "tdnet_pdf" / "html"
NOW = datetime(2026, 11, 2, 16, 30)  # noqa: DTZ001


@pytest.fixture(autouse=True)
def _clean_ir_site_tables() -> None:
    db = SessionLocal()
    try:
        db.query(IrSite).delete()
        db.query(TdnetPdfDocument).delete()
        db.query(XbrlFact).delete()
        db.query(XbrlContext).delete()
        db.query(XbrlFiling).delete()
        db.commit()
    finally:
        db.close()


class FakeFetch:
    def __init__(self, pages: dict[str, tuple[bytes, str] | BaseException]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str) -> tuple[bytes, str]:
        self.urls.append(url)
        item = self.pages[url]
        if isinstance(item, BaseException):
            raise item
        return item


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
        disclosed_date=disclosed_date or date.fromisoformat(disclosure_no[:4] + "-" + disclosure_no[4:6] + "-" + disclosure_no[6:8]),
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
    element_id = elem.id
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
        element_id=element_id,
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


def test_golden_tanshin_prefers_ir_path() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20260212557842.html.gz")).decode("utf-8")
    urls = extract_urls_from_html(html)
    picked = pick_tanshin_url(urls)
    assert picked is not None
    assert picked[1] == "ir"
    assert "tepco.co.jp/about/ir/" in picked[0]
    assert not picked[0].endswith(".pdf")


def test_golden_tanshin_homepage_when_no_ir_path() -> None:
    html = gzip.decompress(_gz(GOLDEN_HTML / "20240510589463.html.gz")).decode("utf-8")
    picked = pick_tanshin_url(extract_urls_from_html(html))
    assert picked == ("https://www.terumo.co.jp/", "home")


def test_non_tanshin_title_is_not_read(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    db = SessionLocal()
    try:
        _seed_stock(db, "72030")
        _seed_tanshin(
            db, disclosure_no="20211215452790", code="72030",
            title="配当予想に関するお知らせ",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_ir_sites(db, ["72030"], fetch=None, now=NOW, ir_monthly=empty)
        assert report.chosen.get("none") == 1
        assert db.get(IrSite, "72030") is None
    finally:
        db.close()


def test_harvest_prefers_investors_over_guessing_ir() -> None:
    html = (
        '<html><a href="/company">会社情報</a>'
        '<a href="/investors">投資家情報</a>'
        '<a href="/ir/library/foo.pdf">IR PDF</a></html>'
    )
    assert harvest_ir_href(html, "https://www.terumo.co.jp/") == "https://www.terumo.co.jp/investors"


def test_harvest_skips_ireland_and_prefers_overview_to_events() -> None:
    html = (
        '<a href="/en-ie/">IrelandEnglish</a>'
        '<a href="/investors/events">Investor Events</a>'
        '<a href="/investors/overview">Overview</a>'
    )
    assert harvest_ir_href(html, "https://www.takeda.com/") == "https://www.takeda.com/investors/overview"


def test_harvest_prefers_company_ir_label_to_notice() -> None:
    html = (
        '<a href="/company/?mnu=hd">会社案内・IR情報</a>'
        '<a href="/company/ir/stockholder/notify/">電子公告</a>'
    )
    assert harvest_ir_href(html, "https://www.matsui.co.jp/") == "https://www.matsui.co.jp/company/"


def test_harvest_prefers_ir_top_to_library() -> None:
    html = (
        '<a href="/about/ir/library/">IRライブラリ</a>'
        '<a href="/about/ir/">投資家情報</a>'
    )
    assert harvest_ir_href(html, "https://www.nabtesco.com/") == "https://www.nabtesco.com/about/ir/"


def test_ir_monthly_wins_over_tanshin(tmp_path: Path) -> None:
    db = SessionLocal()
    cfg = tmp_path / "ir_monthly.json"
    cfg.write_text(json.dumps({
        "html": [{"code": "4543", "index_url": "https://www.terumo.co.jp/investors/library/monthly/"}],
    }), encoding="utf-8")
    try:
        _seed_stock(db, "45430", "テルモ")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        fetch = FakeFetch({})
        report = sync_ir_sites(db, ["4543"], fetch=fetch, now=NOW, ir_monthly=cfg)
        row = db.get(IrSite, "45430")
        assert row.source == SOURCE_IR_MONTHLY
        assert row.confidence == "verified"
        assert row.url_kind == "ir"
        assert "monthly" in row.url
        assert fetch.urls == []
        assert report.put == 1
        second = sync_ir_sites(db, ["45430"], fetch=fetch, now=NOW, ir_monthly=cfg)
        assert second.put == 0 and second.unchanged == 1 and fetch.urls == []
    finally:
        db.close()


def test_homepage_nav_from_tanshin_home(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    home_html = (
        b'<html><a href="/investors">Investor Relations</a></html>'
    )
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        fetch = FakeFetch({
            "https://www.terumo.co.jp/": (home_html, "https://www.terumo.co.jp/"),
        })
        first = sync_ir_sites(db, ["45430"], fetch=fetch, now=NOW, ir_monthly=empty)
        row = db.get(IrSite, "45430")
        assert (row.source, row.url) == (SOURCE_HOMEPAGE_NAV, "https://www.terumo.co.jp/investors")
        assert row.confidence == "high"
        assert first.fetched == 1 and first.put == 1
        second = sync_ir_sites(db, ["45430"], fetch=fetch, now=NOW, ir_monthly=empty)
        assert second.put == 0 and second.fetched == 0
        assert fetch.urls == ["https://www.terumo.co.jp/"]
    finally:
        db.close()


def test_does_not_downgrade_without_refresh(tmp_path: Path) -> None:
    db = SessionLocal()
    cfg = tmp_path / "ir_monthly.json"
    cfg.write_text(json.dumps({
        "html": [{"code": "4543", "index_url": "https://www.terumo.co.jp/investors/"}],
    }), encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        sync_ir_sites(db, ["45430"], fetch=None, now=NOW, ir_monthly=cfg)
        report = sync_ir_sites(db, ["45430"], fetch=None, now=NOW, ir_monthly=empty)
        assert report.unchanged == 1
        assert db.get(IrSite, "45430").source == SOURCE_IR_MONTHLY
        refreshed = sync_ir_sites(db, ["45430"], fetch=None, refresh=True, now=NOW, ir_monthly=empty)
        assert refreshed.put == 1
        assert db.get(IrSite, "45430").source == SOURCE_TANSHIN_HTML
    finally:
        db.close()


def test_xbrl_url_normalized_and_used_as_homepage(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "80150")
        _seed_xbrl_url(
            db, code="80150", url="www.toyota.co.jp",
            doc_id="20260399999999", submitted_at=datetime(2026, 5, 1),  # noqa: DTZ001
        )
        fetch = FakeFetch({
            "https://www.toyota.co.jp": (
                b'<a href="/ir/">IR</a>',
                "https://www.toyota.co.jp/",
            ),
        })
        report = sync_ir_sites(db, ["80150"], fetch=fetch, now=NOW, ir_monthly=empty)
        row = db.get(IrSite, "80150")
        assert row.source == SOURCE_HOMEPAGE_NAV
        assert row.url == "https://www.toyota.co.jp/ir/"
        assert report.fetched == 1
    finally:
        db.close()


def test_tanshin_ir_beats_xbrl_homepage(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "95010")
        _seed_xbrl_url(
            db, code="95010", url="https://www.tepco.co.jp/index-j.html",
            doc_id="20260212557842", submitted_at=datetime(2026, 2, 12),  # noqa: DTZ001
        )
        _seed_tanshin(
            db, disclosure_no="20260212557842", code="95010",
            title="2026年３月期第３四半期決算短信〔日本基準〕（連結）",
            html_gz=_gz(GOLDEN_HTML / "20260212557842.html.gz"),
        )
        report = sync_ir_sites(db, ["95010"], fetch=None, now=NOW, ir_monthly=empty)
        row = db.get(IrSite, "95010")
        assert row.source == SOURCE_TANSHIN_HTML
        assert row.url_kind == "ir"
        assert "about/ir/" in row.url
        assert report.fetched == 0
    finally:
        db.close()


def test_urlerror_keeps_previous_and_retries(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        fail = FakeFetch({
            "https://www.terumo.co.jp/": URLError("timed out"),
        })
        first = sync_ir_sites(db, ["45430"], fetch=fail, now=NOW, ir_monthly=empty)
        row = db.get(IrSite, "45430")
        assert row.source == SOURCE_TANSHIN_HTML
        assert first.unreachable == 1
        ok = FakeFetch({
            "https://www.terumo.co.jp/": (
                b'<a href="/investors">IR</a>',
                "https://www.terumo.co.jp/",
            ),
        })
        second = sync_ir_sites(db, ["45430"], fetch=ok, now=NOW, ir_monthly=empty)
        assert db.get(IrSite, "45430").source == SOURCE_HOMEPAGE_NAV
        assert second.put == 1 and second.fetched == 1
    finally:
        db.close()


def test_404_falls_through_to_homepage(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    err = HTTPError("https://www.terumo.co.jp/", 404, "Not Found", hdrs=None, fp=None)
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        fetch = FakeFetch({"https://www.terumo.co.jp/": err})
        report = sync_ir_sites(db, ["45430"], fetch=fetch, now=NOW, ir_monthly=empty)
        assert db.get(IrSite, "45430").source == SOURCE_TANSHIN_HTML
        assert report.unreachable == 1
    finally:
        db.close()


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "45430")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="45430",
            title="2024年３月期決算短信〔ＩＦＲＳ〕(連結)",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        report = sync_ir_sites(
            db, ["45430"], fetch=None, dry_run=True, now=NOW, ir_monthly=empty)
        assert report.put == 1
        assert db.get(IrSite, "45430") is None
    finally:
        db.close()


def test_preferred_share_writes_ordinary_code(tmp_path: Path) -> None:
    db = SessionLocal()
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    try:
        _seed_stock(db, "72030")
        _seed_tanshin(
            db, disclosure_no="20240510589463", code="72030",
            title="決算短信",
            html_gz=_gz(GOLDEN_HTML / "20240510589463.html.gz"),
        )
        sync_ir_sites(db, ["72031"], fetch=None, now=NOW, ir_monthly=empty)
        assert db.get(IrSite, "72030") is not None
        assert db.get(IrSite, "72031") is None
    finally:
        db.close()


def test_load_ir_monthly_keeps_eir_index_url() -> None:
    urls = load_ir_monthly_urls()
    assert urls["76160"].startswith("https://www.colowide.co.jp/")
    assert urls["81530"].startswith("https://www.mos.co.jp/")
    assert "76490" not in urls


def test_job_main_dry_run_prints_and_does_not_need_init_db(tmp_path: Path, monkeypatch) -> None:
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

    def fake_fetch(url: str) -> tuple[bytes, str]:
        raise URLError("dry-run does not hit the network")

    monkeypatch.setattr("jobs.market.ir_sites.http_get_result", fake_fetch)
    rc = ir_sites_main(["--code", "4543", "--dry-run"])
    assert rc == 0
    db = SessionLocal()
    try:
        assert db.get(IrSite, "45430") is None
    finally:
        db.close()

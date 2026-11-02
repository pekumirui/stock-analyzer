"""KAB-319: homepages の抽出・順位・冪等。"""
from __future__ import annotations

import gzip
import json
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
    Statement,
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
        db.query(Statement).delete()
        db.query(TdnetPdfDocument).delete()
        db.query(XbrlFact).delete()
        db.query(XbrlContext).delete()
        db.query(XbrlFiling).delete()
        db.commit()
    finally:
        db.close()


def _gz(path: Path) -> bytes:
    return path.read_bytes()


def _page(body: str) -> bytes:
    return gzip.compress(f"<html><body>{body}</body></html>".encode(), mtime=0)


def _cid_ascii(text: str) -> str:
    """pdfium の (cid:N)。N + 29 が ASCII。"""
    return "".join(f"(cid:{ord(ch) - 29})" for ch in text)


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


def test_manual_file_dry_run_does_not_write(capsys: pytest.CaptureFixture[str]) -> None:
    path = ROOT / "config" / "market" / "homepage_manual.json"
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert [item["code"] for item in entries] == ["43430", "93480", "93270"]
    for item in entries:
        assert normalize_url(item["url"]) == item["url"]
    rc = homepages_main(["--set-file", str(path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "put 43430 manual https://www.fantasy.co.jp/company/" in out
    assert "put 93480 manual https://www.ispace-inc.com/ja/home-jp/" in out
    assert "put 93270 manual https://www.e-logit.com/" in out
    assert "set codes=3 put=3" in out
    db = SessionLocal()
    try:
        assert db.get(Homepage, "43430") is None
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


def test_normalize_url_bare_domain_and_jpx() -> None:
    assert normalize_url("wize-inc.jp") == "https://wize-inc.jp"
    assert normalize_url("jp.medialinks.com") == "https://jp.medialinks.com"
    assert normalize_url("www.kao.com/jp/") == "https://www.kao.com/jp/"
    assert normalize_url("file.txt") is None
    assert normalize_url("not a domain") is None
    assert normalize_url("https://www.jpx.co.jp/") is None
    assert normalize_url("https://www.jpx.co.jp/", allow_jpx=True) == "https://www.jpx.co.jp/"
    assert host_is_skipped("www.jpx.co.jp")
    assert not host_is_skipped("www.jpx.co.jp", allow_jpx=True)


def test_normalize_repairs_space_double_scheme_and_drops_tldless_host() -> None:
    assert normalize_url("http:// www.appbank.co.jp/") == "http://www.appbank.co.jp/"
    assert normalize_url("http://https://hikaribf.co.jp") == "https://hikaribf.co.jp"
    assert normalize_url("https://www.prologis") is None
    assert normalize_url("https://www.prologis-reit.co.jp") == "https://www.prologis-reit.co.jp"
    assert normalize_url("https://global.toyota/") == "https://global.toyota/"
    assert normalize_url("https://holdings.panasonic") == "https://holdings.panasonic"
    assert pick_homepage_url(
        "ＵＲＬ http://https://hikaribf.co.jp 代表者"
    ) == "https://hikaribf.co.jp"
    assert pick_homepage_url(
        "https://www.prologis コード番号 3283 Ｕ Ｒ Ｌ -reit.co.jp"
    ) is None


def test_xbrl_url_space_after_scheme_is_removed() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "61770")
        _seed_xbrl_url(
            db, code="61770", url="http:// www.appbank.co.jp/",
            doc_id="081220260813519721",
            submitted_at=datetime(2026, 8, 13, 16),  # noqa: DTZ001
        )
        sync_homepages(db, ["61770"], now=NOW)
        row = db.get(Homepage, "61770")
        assert row.url == "http://www.appbank.co.jp/"
        assert row.source == SOURCE_XBRL_URL
        assert row.origin == "xbrl:081220260813519721"
    finally:
        db.close()


def test_truncated_host_falls_through_to_older_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "32830")
        _seed_tanshin(
            db, disclosure_no="20260716595085", code="32830",
            title="2026年5月期 決算短信（ＲＥＩＴ）",
            html_gz=_page("https://www.prologis コード番号 3283 Ｕ Ｒ Ｌ -reit.co.jp"),
            disclosed_date=date(2026, 7, 16),
        )
        _seed_tanshin(
            db, disclosure_no="20240718550963", code="32830",
            title="2024年５月期 決算短信（ＲＥＩＴ）",
            html_gz=_page("ＵＲＬ https://www.prologis-reit.co.jp"),
            disclosed_date=date(2024, 7, 18),
        )
        sync_homepages(db, ["32830"], now=NOW)
        row = db.get(Homepage, "32830")
        assert row.url == "https://www.prologis-reit.co.jp"
        assert row.origin == "tanshin:20240718550963"
    finally:
        db.close()


def test_schemeless_url_only_near_label() -> None:
    buried = "<p>監査法人 https://www.deloitte.com/</p><p>参照 www.toray.co.jp</p>"
    assert pick_homepage_url(buried) == "https://www.deloitte.com/"
    assert pick_homepage_url("<p>www.only.example</p>") is None
    labeled = "<p>URL www.toray.co.jp</p><p>https://www.auditor.example/</p>"
    assert pick_homepage_url(labeled) == "https://www.toray.co.jp"
    assert pick_homepage_url("<p>ＵＲＬ jp.medialinks.com</p>") == "https://jp.medialinks.com"
    assert pick_homepage_url("<p>ホームページ www.kao.com/jp/</p>") == "https://www.kao.com/jp/"


def test_cid_ascii_cover_url() -> None:
    html = f"<p>{_cid_ascii('URL https://www.nihon-trim.co.jp/')}</p>"
    assert pick_homepage_url(html) == "https://www.nihon-trim.co.jp/"
    kept = "<p>URL https://www.terumo.co.jp/</p><p>(cid:9000)(cid:9001)</p>"
    assert pick_homepage_url(kept) == "https://www.terumo.co.jp/"


def test_xbrl_bare_domain_without_www() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "36640")
        _seed_xbrl_url(
            db, code="36640", url="wize-inc.jp",
            doc_id="wize-xbrl", submitted_at=datetime(2026, 8, 14),  # noqa: DTZ001
        )
        sync_homepages(db, ["36640"], now=NOW)
        row = db.get(Homepage, "36640")
        assert row.source == SOURCE_XBRL_URL
        assert row.url == "https://wize-inc.jp"
        assert row.host == "wize-inc.jp"
    finally:
        db.close()


def test_correction_title_skips_to_older_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "23950")
        _seed_tanshin(
            db, disclosure_no="20260805510172", code="23950",
            title="(訂正・数値データ訂正)「2027年３月期 第１四半期決算短信」の一部",
            html_gz=_page("URL https://www.wrong.example/"),
            disclosed_date=date(2026, 8, 5),
        )
        _seed_tanshin(
            db, disclosure_no="20260804508568", code="23950",
            title="2027年３月期 第１四半期決算短信〔日本基準〕（連結）",
            html_gz=_page("ＵＲＬ https://snbl.com/"),
            disclosed_date=date(2026, 8, 4),
        )
        sync_homepages(db, ["23950"], now=NOW)
        row = db.get(Homepage, "23950")
        assert row.url == "https://snbl.com/"
        assert row.origin == "tanshin:20260804508568"
    finally:
        db.close()


def test_short_html_skips_to_older_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "40280")
        short = _seed_doc_with_bytes(
            db, disclosure_no="20260807514112", code="40280",
            title="2026年３月期 決算短信〔日本基準〕(連結)",
            html_gz=_page("URL https://www.short.example/"),
            disclosed_date=date(2026, 8, 7), nbytes=4000,
        )
        assert short.bytes == 4000
        _seed_doc_with_bytes(
            db, disclosure_no="20260807513965", code="40280",
            title="2027年３月期 第１四半期決算短信〔日本基準〕(連結)",
            html_gz=_page("URL https://www.iskweb.co.jp/"),
            disclosed_date=date(2026, 8, 6), nbytes=20_000,
        )
        sync_homepages(db, ["40280"], now=NOW)
        row = db.get(Homepage, "40280")
        assert row.url == "https://www.iskweb.co.jp/"
        assert row.origin == "tanshin:20260807513965"
    finally:
        db.close()


def test_financial_statements_title_is_a_tanshin() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "67880")
        _seed_tanshin(
            db, disclosure_no="20260806511222", code="67880",
            title="1QFinancialStatements_Consolidated_JP",
            html_gz=_page(_cid_ascii("URL https://www.nihon-trim.co.jp/")),
            disclosed_date=date(2026, 8, 6),
        )
        sync_homepages(db, ["67880"], now=NOW)
        row = db.get(Homepage, "67880")
        assert row.url == "https://www.nihon-trim.co.jp/"
        assert row.origin == "tanshin:20260806511222"
    finally:
        db.close()


def test_fragment_title_needs_statement_type() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "72420")
        title = "(注)１．直近に公表されている配当予想からの修正の有無 ：無"
        _seed_tanshin(
            db, disclosure_no="20260806511329", code="72420",
            title=title,
            html_gz=_page("ＵＲＬ https://www.kyb.co.jp/"),
            disclosed_date=date(2026, 8, 6),
        )
        assert sync_homepages(db, ["72420"], now=NOW).chosen.get("none") == 1
        db.merge(Statement(
            disclosure_no="20260806511329",
            code="72420",
            type_of_document="1QFinancialStatements_Consolidated_IFRS",
        ))
        db.commit()
        sync_homepages(db, ["72420"], now=NOW)
        row = db.get(Homepage, "72420")
        assert row.url == "https://www.kyb.co.jp/"
        assert row.origin == "tanshin:20260806511329"
    finally:
        db.close()


def test_walks_back_past_five_when_newer_tanshin_have_no_url() -> None:
    """直近に URL が無ければ 5 本で止めず、取れるまで古い短信を見る。"""
    db = SessionLocal()
    try:
        _seed_stock(db, "41250")
        for i in range(5):
            _seed_tanshin(
                db, disclosure_no=f"2026080{i}000000", code="41250",
                title="決算短信 本文にURLなし" if i else "決算短信の一部訂正について",
                html_gz=_page("URLが無く、訂正でもない本文" if i else "URL https://www.skip.example/"),
                disclosed_date=date(2026, 8, 7 - i),
            )
        _seed_tanshin(
            db, disclosure_no="20260101000000", code="41250",
            title="2026年３月期 決算短信〔日本基準〕(連結)",
            html_gz=_page("URL https://www.old-cover.example/"),
            disclosed_date=date(2026, 1, 1),
        )
        sync_homepages(db, ["41250"], now=NOW)
        row = db.get(Homepage, "41250")
        assert row.url == "https://www.old-cover.example/"
        assert row.origin == "tanshin:20260101000000"
        _seed_tanshin(
            db, disclosure_no="20260804000001", code="41250",
            title="2027年３月期 第１四半期決算短信〔日本基準〕(連結)",
            html_gz=_page("URL https://www.sanwayuka.co.jp/"),
            disclosed_date=date(2026, 8, 3),
        )
        # より新しい短信に URL があれば、さかのぼった古い URL よりそちらを採る
        sync_homepages(db, ["41250"], now=NOW)
        row = db.get(Homepage, "41250")
        assert row.url == "https://www.sanwayuka.co.jp/"
        assert row.origin == "tanshin:20260804000001"
    finally:
        db.close()


def test_empty_recent_financial_statements_falls_back_to_older_cover() -> None:
    """ジャストシステム型。新しい FinancialStatements は空変換や URL 無し。古い決算短信の表紙を採る。"""
    db = SessionLocal()
    try:
        _seed_stock(db, "46860")
        _seed_doc_with_bytes(
            db, disclosure_no="20260807000001", code="46860",
            title="1QFinancialStatements_Consolidated_JP",
            html_gz=_page("１．経営成績等の概況"),
            disclosed_date=date(2026, 8, 7), nbytes=73_901,
        )
        _seed_doc_with_bytes(
            db, disclosure_no="20260206000001", code="46860",
            title="3QFinancialStatements_Consolidated_JP",
            html_gz=_page(""),
            disclosed_date=date(2026, 2, 6), nbytes=1_122,
        )
        for i, when in enumerate((
            date(2025, 11, 7), date(2025, 8, 8), date(2025, 5, 14),
            date(2025, 2, 7), date(2024, 11, 8),
        )):
            _seed_tanshin(
                db, disclosure_no=f"2025{i + 1:02d}01000000", code="46860",
                title=f"{i + 1}QFinancialStatements_Consolidated_JP",
                html_gz=_page("(株)ジャストシステム 添付資料の目次"),
                disclosed_date=when,
            )
        _seed_tanshin(
            db, disclosure_no="20240510590709", code="46860",
            title="2024年3月期 決算短信〔日本基準〕（連結）",
            html_gz=_page("URL https://www.justsystems.com/jp/"),
            disclosed_date=date(2024, 5, 14),
        )
        sync_homepages(db, ["46860"], now=NOW)
        row = db.get(Homepage, "46860")
        assert row.url == "https://www.justsystems.com/jp/"
        assert row.origin == "tanshin:20240510590709"
    finally:
        db.close()


def test_jpx_url_kept_only_for_jpx() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "86970")
        _seed_stock(db, "72030")
        page = _page("ＵＲＬ https://www.jpx.co.jp/")
        _seed_tanshin(
            db, disclosure_no="20261026556190", code="86970",
            title="2027年３月期 第２四半期（中間期）決算短信〔ＩＦＲＳ〕（連結）",
            html_gz=page, disclosed_date=date(2026, 10, 27),
        )
        _seed_tanshin(
            db, disclosure_no="20261026556191", code="72030",
            title="決算短信",
            html_gz=page, disclosed_date=date(2026, 10, 27),
        )
        sync_homepages(db, ["86970", "72030"], now=NOW)
        jpx = db.get(Homepage, "86970")
        assert jpx.url == "https://www.jpx.co.jp/"
        assert jpx.host == "jpx.co.jp"
        assert db.get(Homepage, "72030") is None
    finally:
        db.close()


def test_holding_company_reads_predecessor_until_it_has_its_own() -> None:
    db = SessionLocal()
    try:
        _seed_stock(db, "641A0")
        _seed_stock(db, "72400")
        _seed_tanshin(
            db, disclosure_no="20260803507417", code="72400",
            title="2027年３月期 第１四半期決算短信〔日本基準〕（連結）",
            html_gz=_page("ＵＲＬ https://www.nokgrp.com"),
            disclosed_date=date(2026, 8, 6),
        )
        sync_homepages(db, ["641A0"], now=NOW)
        row = db.get(Homepage, "641A0")
        assert row.url == "https://www.nokgrp.com"
        assert row.origin == "tanshin:20260803507417"
        assert db.get(Homepage, "72400") is None
        _seed_tanshin(
            db, disclosure_no="20260901000001", code="641A0",
            title="決算短信",
            html_gz=_page("URL https://www.nok-group.example/"),
            disclosed_date=date(2026, 9, 1),
        )
        sync_homepages(db, ["641A0"], now=LATER)
        row = db.get(Homepage, "641A0")
        assert row.url == "https://www.nok-group.example/"
        assert row.origin == "tanshin:20260901000001"
    finally:
        db.close()


def _seed_doc_with_bytes(
    db, *, disclosure_no: str, code: str, title: str, html_gz: bytes,
    disclosed_date: date, nbytes: int,
) -> TdnetPdfDocument:
    doc = TdnetPdfDocument(
        disclosure_no=disclosure_no,
        code=code,
        disclosed_date=disclosed_date,
        title=title,
        status="ok",
        html_gz=html_gz,
        bytes=nbytes,
        created_at=NOW,
    )
    db.merge(doc)
    db.commit()
    return doc

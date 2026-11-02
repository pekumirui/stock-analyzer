"""KAB-325: 短信1ページ目の説明資料・説明会の有無と、TDnet / ホームページの試行。"""
from __future__ import annotations

import gzip
import re
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request

import pytest

from app.db import SessionLocal
from app.market.briefing_docs import (
    HttpResponse,
    Pacer,
    ascii_url,
    browser_headers,
    is_material_title,
    judge_tdnet_rows,
    probe_homepage,
)
from app.market.tanshin_cover import (
    cover_text_from_html,
    homepage_targets,
    parse_cover_text,
    probe_saved,
    sample_from_tdnet,
    sync_covers,
)
from app.models import (
    Homepage,
    TanshinCover,
    TdnetPdfDocument,
    XbrlDocument,
    XbrlFiling,
)
from jobs._r2common import BROWSER_UA
from jobs.market.tanshin_covers import main as covers_main
from jobs.market.tanshin_covers import parse_args

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "tdnet_pdf" / "html"
NOW = datetime(2026, 11, 2, 12, 0)  # noqa: DTZ001


@pytest.fixture(autouse=True)
def _clean() -> None:
    db = SessionLocal()
    try:
        db.query(TanshinCover).delete()
        db.query(Homepage).delete()
        db.query(XbrlDocument).delete()
        db.query(XbrlFiling).delete()
        db.query(TdnetPdfDocument).delete()
        db.commit()
    finally:
        db.close()


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode(), mtime=0)


def _svg(lines: list[str]) -> str:
    body = []
    y = 40
    for line in lines:
        body.append(f'<text x="40" y="{y}" font-size="10">{line}</text>')
        y += 16
    return "<svg xmlns=\"http://www.w3.org/2000/svg\">" + "".join(body) + "</svg>"


def _pdf_html(page1: list[str], page2: list[str] | None = None) -> str:
    pages = _svg(page1)
    if page2:
        pages += _svg(page2)
    return "<!DOCTYPE html><html><body>" + pages + "</body></html>"


def _ixbrl(material: str, briefing: str, note: str = "") -> str:
    extra = f"<span>（{note}）</span>" if note else ""
    return (
        "<html><body><table><tr>"
        "<td>決算補足説明資料作成の有無</td><td>：</td>"
        f"<td><ix:nonNumeric name=\"tse-ed-t:SupplementalMaterialOfAnnualResults\">{material}</ix:nonNumeric></td>"
        "</tr><tr>"
        "<td>決算説明会開催の有無</td><td>：</td>"
        f"<td><ix:nonNumeric>{briefing}</ix:nonNumeric></td><td>{extra}</td>"
        "</tr></table>"
        "<p>直近に公表されている配当予想からの修正の有無：無</p>"
        "</body></html>"
    )


def _filing(db, code: str, doc_id: str, title: str, submitted_at: datetime, html: str, *, amended: bool = False) -> None:
    filing = XbrlFiling(
        code=code,
        source="tdnet",
        doc_id=doc_id,
        doc_type="annual",
        submitted_at=submitted_at,
        title=title,
        is_amendment=amended,
        raw_path=f"{doc_id}.zip",
        parse_status="ok",
        created_at=NOW,
    )
    db.add(filing)
    db.flush()
    raw = html.encode()
    db.add(XbrlDocument(
        filing_id=filing.id,
        seq=0,
        part="summary",
        file_name="summary.htm",
        html_gz=gzip.compress(raw, mtime=0),
        bytes=len(raw),
        sha256="a" * 64,
        created_at=NOW,
    ))
    db.commit()


def _pdf(db, code: str, disclosure_no: str, title: str, when: date, html: str, hhmm: str = "15:00") -> None:
    db.add(TdnetPdfDocument(
        disclosure_no=disclosure_no,
        code=code,
        disclosed_date=when,
        disclosed_time=hhmm,
        title=title,
        status="ok",
        html_gz=_gz(html),
        created_at=NOW,
    ))
    db.commit()


def test_parse_cover_text_yes_no_and_note() -> None:
    flags = parse_cover_text("決算補足説明資料作成の有無：有\n決算説明会開催の有無：有（機関投資家・アナリスト向け）")
    assert flags.has_material is True
    assert flags.has_briefing is True
    assert flags.briefing_note == "機関投資家・アナリスト向け"
    blank = parse_cover_text("四半期決算補足説明資料作成の有無：無\n四半期決算説明会開催の有無：無")
    assert blank.has_material is False and blank.has_briefing is False
    split = parse_cover_text("決算補足説明資料作成の有無\n：有\n決算説明会開催の有無\n：無\n(百万円未満切捨て)")
    assert split.has_material is True and split.has_briefing is False
    assert split.briefing_note is None
    ambiguous = parse_cover_text("決算補足説明資料作成の有無：有・無")
    assert ambiguous.has_material is None
    other = parse_cover_text("直近に公表されている配当予想からの修正の有無：無")
    assert other.found_any is False
    noise = parse_cover_text("決算補足説明資料作成の有無：有（氏名）\n決算説明会開催の有無：有（氏名）")
    assert noise.has_material is True and noise.has_briefing is True
    assert noise.material_note is None and noise.briefing_note is None


def test_pdf_html_uses_page1_only_and_ixbrl_summary() -> None:
    html = _pdf_html(
        ["決算補足説明資料作成の有無：無", "決算説明会開催の有無：無"],
        ["決算補足説明資料作成の有無：有", "決算説明会開催の有無：有"],
    )
    flags = parse_cover_text(cover_text_from_html(html))
    assert flags.has_material is False and flags.has_briefing is False
    ix = parse_cover_text(cover_text_from_html(_ixbrl("有", "有", "アナリスト向け")))
    assert ix.has_material is True and ix.has_briefing is True
    assert ix.briefing_note == "アナリスト向け"


def test_golden_page1_flags() -> None:
    cases = {
        "20240229545849": (False, False, None),
        "20250709511068": (True, True, "機関投資家・アナリスト向け"),
        "20220719501546": (True, True, None),
    }
    for name, (material, briefing, note) in cases.items():
        html = gzip.decompress((GOLDEN / f"{name}.html.gz").read_bytes()).decode("utf-8")
        flags = parse_cover_text(cover_text_from_html(html))
        assert flags.has_material is material, name
        assert flags.has_briefing is briefing, name
        assert flags.briefing_note == note, name
    notice = gzip.decompress((GOLDEN / "20260212557842.html.gz").read_bytes()).decode("utf-8")
    assert parse_cover_text(cover_text_from_html(notice)).found_any is False


def test_sync_prefers_same_day_readable_cover_and_skips_amendment() -> None:
    db = SessionLocal()
    try:
        _pdf(
            db, "72030", "20261101000001", "2026年3月期 決算短信〔日本基準〕（連結）",
            date(2026, 11, 1),
            _pdf_html(["決算補足説明資料作成の有無：無", "決算説明会開催の有無：無"]),
        )
        _filing(
            db, "72030", "081220261101000001", "2026年3月期 決算短信〔日本基準〕（連結）",
            datetime(2026, 11, 1, 15, 30), _ixbrl("有", "有", "アナリスト向け"),  # noqa: DTZ001
        )
        _filing(
            db, "72030", "081220261102000002", "「2026年3月期 決算短信」の修正について",
            datetime(2026, 11, 2, 9, 0), _ixbrl("無", "無"),  # noqa: DTZ001
        )
        report = sync_covers(db, ["7203"], dry_run=False)
        row = db.get(TanshinCover, "72030")
        assert row is not None
        assert row.source == "xbrl_summary"
        assert row.has_material is True
        assert row.briefing_note == "アナリスト向け"
        assert row.disclosure_no == "081220261101000001"
        assert any(line.startswith("72030 ") for line in report.lines)
        row.tdnet_status = "absent"
        db.commit()
        sync_covers(db, ["72030"])
        assert db.get(TanshinCover, "72030").tdnet_status == "absent"
    finally:
        db.close()


def test_older_pdf_does_not_override_a_newer_blank_cover() -> None:
    db = SessionLocal()
    try:
        _pdf(
            db, "67580", "20261001000001", "2026年3月期 第1四半期決算短信",
            date(2026, 10, 1),
            _pdf_html(["四半期決算補足説明資料作成の有無：有", "四半期決算説明会開催の有無：有"]),
        )
        _filing(
            db, "67580", "081220261101000009", "2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            datetime(2026, 11, 1, 15, 0), "<html><body><p>本文だけ</p></body></html>",  # noqa: DTZ001
        )
        sync_covers(db, ["67580"])
        row = db.get(TanshinCover, "67580")
        assert row.source == "xbrl_summary"
        assert row.has_material is None
        assert row.disclosure_no == "081220261101000009"
    finally:
        db.close()


def test_dry_run_does_not_write() -> None:
    db = SessionLocal()
    try:
        _pdf(
            db, "72030", "20261101000001", "2026年3月期 決算短信",
            date(2026, 11, 1),
            _pdf_html(["決算補足説明資料作成の有無：有", "決算説明会開催の有無：無"]),
        )
        sync_covers(db, ["72030"], dry_run=True)
        assert db.get(TanshinCover, "72030") is None
        assert covers_main(["--code", "7203", "--dry-run"]) == 0
        assert db.get(TanshinCover, "72030") is None
    finally:
        db.close()


def test_material_title_and_tdnet_window() -> None:
    assert is_material_title("2026年3月期 決算説明資料")
    assert is_material_title("2026年3月期 第2四半期 決算説明会資料")
    assert is_material_title("2026年3月期 決算補足説明資料")
    assert is_material_title("2025年6月期 財務補足資料")
    assert not is_material_title("2026年3月期 決算短信〔日本基準〕（連結）")
    assert not is_material_title("10月度月次売上高のお知らせ")
    rows = [
        {"company_code": "7203", "title": "2026年3月期 決算短信", "pubdate": "2026-10-20 15:00:00",
         "document_url": "https://www.release.tdnet.info/inbs/1401tanshin.pdf"},
        {"company_code": "72030", "title": "2026年3月期 決算説明資料", "pubdate": "2026-10-20 15:10:00",
         "document_url": "https://www.release.tdnet.info/inbs/1401material.pdf"},
        {"company_code": "72030", "title": "2026年3月期 決算説明資料", "pubdate": "2026-11-01 15:00:00",
         "document_url": "https://www.release.tdnet.info/inbs/late.pdf"},
    ]
    hit = judge_tdnet_rows(rows, "72030", date(2026, 10, 20), today=date(2026, 11, 2))
    assert hit.status == "listed"
    assert hit.url.endswith("1401material.pdf")
    absent = judge_tdnet_rows(rows, "67580", date(2026, 10, 20), today=date(2026, 11, 2))
    assert absent.status == "absent"
    unknown = judge_tdnet_rows(
        [], "72030", date(2026, 8, 1),
        missing_days={date(2026, 8, 1)}, today=date(2026, 11, 2),
    )
    assert unknown.status == "unknown"


def _list_page(day: date, rows: list[str]) -> bytes:
    body = "".join(
        "<tr>"
        f'<td class="oddnew-L kjTime">{hhmm}</td>'
        f'<td class="oddnew-M kjCode">{code}</td>'
        f'<td class="oddnew-M kjName">{name}</td>'
        f'<td class="oddnew-M kjTitle"><a href="{pdf}">{title}</a></td>'
        '<td class="oddnew-M kjXbrl"></td>'
        "</tr>"
        for hhmm, code, name, title, pdf in rows
    )
    return (
        f'<html><body><table id="main-list-table">{body}</table></body></html>'
    ).encode()


def test_sample_from_tdnet_confirms_pdf_and_caps_limit() -> None:
    today = date(2026, 11, 10)
    pages = {
        date(2026, 11, 1): [
            ("15:00", "99840", "ソフトバンク", "2026年3月期 第2四半期決算短信", "t9984.pdf"),
        ],
        date(2026, 11, 2): [
            ("15:00", "72030", "トヨタ", "2026年3月期 決算短信〔日本基準〕（連結）", "t7203.pdf"),
            ("15:05", "72030", "トヨタ", "2026年3月期 決算説明資料", "m7203.pdf"),
        ],
        date(2026, 11, 9): [
            ("15:10", "67580", "ソニー", "2026年3月期 第2四半期決算短信", "t6758.pdf"),
        ],
    }
    texts = {
        "t7203.pdf": "決算補足説明資料作成の有無：有\n決算説明会開催の有無：有",
        "t6758.pdf": "決算補足説明資料作成の有無：無\n決算説明会開催の有無：無",
        "t9984.pdf": "決算補足説明資料作成の有無：有\n決算説明会開催の有無：無",
    }

    def get(url: str) -> bytes:
        if "I_list_" in url:
            match = re.search(r"(\d{8})\.html", url)
            assert match is not None
            stamp = match.group(1)
            day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
            return _list_page(day, pages.get(day, []))
        name = url.rsplit("/", 1)[-1]
        return texts[name].encode()

    def pdf_text(blob: bytes) -> str:
        return blob.decode()

    def confirm(url: str) -> bool:
        return url.endswith("m7203.pdf")

    db = SessionLocal()
    try:
        report = sample_from_tdnet(
            db, days=10, limit=10, today=today, get=get, pdf_text=pdf_text,
            confirm=confirm, sleep_sec=0,
        )
        assert report.n == 3
        assert report.material_yes == 2
        assert report.material_no == 1
        assert report.tdnet_found == 1
        assert report.tdnet_absent == 1
        toyota = db.get(TanshinCover, "72030")
        assert toyota.has_material is True and toyota.tdnet_status == "found"
        assert toyota.tdnet_url.endswith("m7203.pdf")
        softbank = db.get(TanshinCover, "99840")
        assert softbank.has_material is True and softbank.tdnet_status == "absent"
        with pytest.raises(ValueError):
            sample_from_tdnet(
                db, days=10, limit=81, today=today, get=get, pdf_text=pdf_text, confirm=confirm,
            )
    finally:
        db.close()


def test_probe_homepage_finds_pdf_with_browser_ua_and_gap() -> None:
    slept: list[float] = []
    seen_ua: list[str] = []
    clock = {"t": 0.0}

    def opener(req: Request, limit: int = 0) -> HttpResponse:
        seen_ua.append(req.get_header("User-agent") or "")
        url = req.full_url
        if url.endswith("/ir/a.pdf"):
            return HttpResponse(200, url, {"Content-Type": "application/pdf"}, b"%PDF-1.4 data")
        if "eir-parts.net" in url:
            return HttpResponse(200, url, {"Content-Type": "application/pdf"}, b"%PDF-1.4 eir")
        html = (
            '<a href="https://evil.example/secret.pdf">決算説明資料</a>'
            '<a href="/ir/a.pdf">2026年3月期 決算説明資料</a>'
        )
        return HttpResponse(200, url, {"Content-Type": "text/html; charset=utf-8"}, html.encode())

    def fake_sleep(gap: float) -> None:
        slept.append(gap)
        clock["t"] += gap

    result = probe_homepage(
        "https://www.example.co.jp/",
        opener,
        Pacer(2.5, sleep=fake_sleep, clock=lambda: clock["t"]),
    )
    assert result.status == "found"
    assert result.url.endswith("/ir/a.pdf")
    assert seen_ua == [BROWSER_UA, BROWSER_UA]
    assert slept == [2.5]
    assert "Mozilla/5.0" in browser_headers()["User-Agent"]
    assert "stock-analyzer" not in browser_headers()["User-Agent"]

    blocked = probe_homepage(
        "https://blocked.example/",
        lambda req, limit=0: HttpResponse(403, req.full_url, {}, b"denied"),
        Pacer(2.5, sleep=lambda _s: None),
    )
    assert blocked.status == "blocked"
    encoded = ascii_url("https://www.example.co.jp/ir/決算説明.pdf")
    assert encoded.isascii() and encoded.endswith(".pdf") and "%E6" in encoded

    def encoded_opener(req: Request, limit: int = 0) -> HttpResponse:
        if ".pdf" in req.full_url:
            assert req.full_url.isascii()
            return HttpResponse(200, req.full_url, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
        html = '<a href="/ir/決算説明.pdf">決算説明資料</a>'
        return HttpResponse(200, req.full_url, {"Content-Type": "text/html"}, html.encode())

    japanese = probe_homepage(
        "https://www.example.co.jp/",
        encoded_opener,
        Pacer(2.5, sleep=lambda _s: None),
    )
    assert japanese.status == "found"
    assert japanese.url is not None and japanese.url.isascii()

    def tanshin_only(req: Request, limit: int = 0) -> HttpResponse:
        if req.full_url.endswith("tanshin.pdf"):
            return HttpResponse(200, req.full_url, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
        html = '<a href="/ir/2026年3月期決算短信.pdf">決算短信</a>'
        return HttpResponse(200, req.full_url, {"Content-Type": "text/html"}, html.encode())

    only_flash = probe_homepage(
        "https://www.example.co.jp/",
        tanshin_only,
        Pacer(2.5, sleep=lambda _s: None),
    )
    assert only_flash.status == "not_found"

    def securities_report(req: Request, limit: int = 0) -> HttpResponse:
        if req.full_url.endswith(".pdf"):
            return HttpResponse(200, req.full_url, {"Content-Type": "application/pdf"}, b"%PDF-1.4")
        html = '<a href="/ir/hanki.pdf">半期報告書</a><a href="/ir/library/">IRライブラリ</a>'
        return HttpResponse(200, req.full_url, {"Content-Type": "text/html"}, html.encode())

    report = probe_homepage(
        "https://www.example.co.jp/",
        securities_report,
        Pacer(2.5, sleep=lambda _s: None),
    )
    assert report.status == "not_found"


def test_probe_saved_skips_companies_without_homepage() -> None:
    db = SessionLocal()
    try:
        db.add(TanshinCover(
            code="72030", disclosure_no="x", source="tdnet_live", has_material=True,
            tdnet_status="absent", checked_at=NOW, disclosed_at=datetime(2026, 11, 2, 15, 0),  # noqa: DTZ001
        ))
        db.add(TanshinCover(
            code="99840", disclosure_no="y", source="tdnet_live", has_material=True,
            tdnet_status="absent", checked_at=NOW, disclosed_at=datetime(2026, 10, 1, 15, 0),  # noqa: DTZ001
        ))
        db.add(Homepage(
            code="72030", url="https://www.example.co.jp/", host="example.co.jp",
            source="manual", origin="manual", changed_at=NOW, checked_at=NOW,
        ))
        db.commit()
        targets = homepage_targets(db, 8)
        assert [code for code, _url in targets] == ["72030", "99840"]
        calls = {"n": 0}

        def opener2(req: Request, limit: int = 0) -> HttpResponse:
            calls["n"] += 1
            if req.full_url.endswith(".pdf"):
                return HttpResponse(200, req.full_url, {}, b"%PDF-1.4")
            return HttpResponse(
                200, req.full_url, {"Content-Type": "text/html"},
                '<a href="/ir/a.pdf">決算説明資料</a>'.encode(),
            )

        report = probe_saved(db, opener2, Pacer(2.5, sleep=lambda _s: None), limit=8)
        assert report.found == 1
        assert report.no_homepage == 1
        assert calls["n"] == 2
        assert db.get(TanshinCover, "72030").hp_status == "found"
        assert db.get(TanshinCover, "99840").hp_status == "no_homepage"
    finally:
        db.close()


def test_parse_args_rejects_a_full_fetch() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--sample-tdnet", "--limit", "81"])
    with pytest.raises(SystemExit):
        parse_args(["--probe-homepage", "--probe-limit", "13"])
    args = parse_args(["--sample-tdnet", "--days", "14", "--limit", "40", "--probe-homepage"])
    assert args.limit == 40 and args.probe_limit == 8

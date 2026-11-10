"""会社の IR ページから決算説明資料を拾う。ネットワークには出ない。"""
from __future__ import annotations

import hashlib
import io
import json
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.market.briefing_store import SOURCE_HOMEPAGE
from app.models import BriefingDocument
from jobs.market import homepage_briefing as mod

ROOT = Path(__file__).resolve().parents[2]
INDEX_URL = "https://ir.example.co.jp/ja/ir/library/result.html"


def _at(*parts: int) -> datetime:
    return datetime(*parts)  # noqa: DTZ001


NOW = _at(2026, 11, 10, 18, 0, 0)
PAGE = """
<html><body>
<h1>決算短信・決算説明資料</h1>
<ul><li><a href="https://data.example.net/140120261109569902.pdf">第1四半期 決算短信〔日本基準〕(連結)</a></li></ul>
<div><h2>2027年6月期 第1四半期</h2><ul>
<li><a href="/ja/lib/main/00/linkList/09/link/(JP)Scripts_Q1.pdf"><img src="pdf.png" alt=""></a>
    <a href="/ja/lib/main/00/linkList/09/link/(JP)Scripts_Q1.pdf">ノーツ付き決算説明資料</a></li>
<li><a href="/ja/lib/main/00/linkList/08/link/(JP)Presentation%20Q1.pdf">決算説明資料</a></li>
</ul></div>
<div><h2>2026年6月期</h2><ul>
<li><a href="/ja/lib/main/00/linkList/07/link/QA.pdf">質疑応答サマリー</a></li>
<li><a href="/ja/lib/main/00/linkList/08/link/FY2026.pdf">決算説明資料</a></li>
<li><a href="https://video.example.com/index.html">決算説明動画</a></li>
<li><a href="/ja/lib/main/00/linkList/06/link/notice.pdf">決算説明会開催のお知らせ</a></li>
</ul></div>
<div><h2>2005年6月期</h2><h3>第2四半期</h3><ul>
<li><a href="/ja/lib/main/019/link/File596536573.pdf">中間決算説明資料</a></li>
</ul></div>
<h2><a href="/company/guide.pdf">企業情報</a></h2>
</body></html>
"""


@pytest.fixture(autouse=True)
def _no_rows() -> None:
    db = SessionLocal()
    try:
        db.query(BriefingDocument).delete()
        db.commit()
    finally:
        db.close()


def _pdf(tag: str) -> bytes:
    return b"%PDF-1.4 " + tag.encode()


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "briefing_pages.json"
    path.write_text(
        json.dumps({"issuers": [{"code": "6728", "name": "アルバック", "index_url": INDEX_URL}]}),
        encoding="utf-8",
    )
    return path


def _rows() -> list[BriefingDocument]:
    db = SessionLocal()
    try:
        return (
            db.query(BriefingDocument)
            .filter(BriefingDocument.source == SOURCE_HOMEPAGE)
            .order_by(BriefingDocument.title)
            .all()
        )
    finally:
        db.close()


def _run(tmp_path: Path, page: str = PAGE, dates: dict[str, datetime] | None = None, **kwargs):
    gets: list[str] = []

    def fake_get(url: str) -> bytes:
        gets.append(url)
        if url == INDEX_URL:
            return page.encode("utf-8")
        return _pdf(url)

    def fake_date(data: bytes) -> datetime | None:
        for key, when in (dates or {}).items():
            if key.encode() in data:
                return when
        return None

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(
            tmp_path / "briefing", _config(tmp_path),
            get=fake_get, read_date=fake_date, now=NOW, **kwargs,
        )
    return rc, gets, out.getvalue()


def test_links_take_heading_path_and_skip_tanshin() -> None:
    links = mod.parse_deck_links(PAGE, INDEX_URL, "67280")
    assert [link.title for link in links] == [
        "2027年6月期 第1四半期 ノーツ付き決算説明資料",
        "2027年6月期 第1四半期 決算説明資料",
        "2026年6月期 質疑応答サマリー",
        "2026年6月期 決算説明資料",
        "2005年6月期 第2四半期 中間決算説明資料",
    ]
    assert links[1].url == "https://ir.example.co.jp/ja/lib/main/00/linkList/08/link/(JP)Presentation%20Q1.pdf"
    assert len({link.document_id for link in links}) == 5
    assert all(link.document_id.isalnum() for link in links)


def test_document_id_survives_a_shifted_path() -> None:
    before = mod.parse_deck_links(PAGE, INDEX_URL, "67280")
    after = mod.parse_deck_links(PAGE.replace("linkList/08", "linkList/010"), INDEX_URL, "67280")
    assert [link.document_id for link in before] == [link.document_id for link in after]
    assert before[1].url != after[1].url


def test_pdf_date_string_becomes_jst() -> None:
    assert mod.parse_pdf_date("D:20261110061810Z") == _at(2026, 11, 10, 15, 18, 10)
    assert mod.parse_pdf_date("D:20260512144728+09'00'") == _at(2026, 5, 12, 14, 47, 28)
    assert mod.parse_pdf_date("D:20260512014728-04'00'") == _at(2026, 5, 12, 14, 47, 28)
    assert mod.parse_pdf_date("D:20260512") == _at(2026, 5, 12)
    assert mod.parse_pdf_date("D:20261399") is None
    assert mod.parse_pdf_date("") is None


def test_run_saves_and_indexes_then_skips(tmp_path: Path) -> None:
    dates = {"Presentation%20Q1": _at(2026, 11, 10, 15, 18, 10), "FY2026": _at(2099, 1, 1)}
    rc, gets, out = _run(tmp_path, dates=dates)
    assert rc == 0
    assert "listed=5 saved=5 skipped=0 failed=0" in out
    rows = {row.title: row for row in _rows()}
    assert len(rows) == 5
    deck = rows["2027年6月期 第1四半期 決算説明資料"]
    assert deck.code == "67280"
    assert deck.company_name == "アルバック"
    assert deck.pubdate == "2026-11-10 15:18:10"
    assert deck.rel_path == "6728/20261110_6728_アルバック_2027年6月期 第1四半期 決算説明資料.pdf"
    body = (tmp_path / "briefing" / deck.rel_path).read_bytes()
    assert body == _pdf(deck.url)
    assert deck.sha256 == hashlib.sha256(body).hexdigest()
    assert deck.size == len(body)
    assert rows["2026年6月期 決算説明資料"].pubdate == "2026-11-10 18:00:00"
    assert rows["2026年6月期 質疑応答サマリー"].pubdate == "2026-11-10 18:00:00"

    rc, gets, out = _run(tmp_path, dates=dates)
    assert rc == 0
    assert gets == [INDEX_URL]
    assert "listed=5 saved=0 skipped=5 failed=0" in out


def test_limit_takes_new_ones_from_the_top(tmp_path: Path) -> None:
    rc, _gets, out = _run(tmp_path, limit=2)
    assert rc == 0
    assert "saved=2" in out
    assert [row.title for row in _rows()] == [
        "2027年6月期 第1四半期 ノーツ付き決算説明資料",
        "2027年6月期 第1四半期 決算説明資料",
    ]
    rc, _gets, out = _run(tmp_path, limit=2)
    assert "saved=2 skipped=2" in out
    assert len(_rows()) == 4


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    rc, gets, out = _run(tmp_path, dry_run=True)
    assert rc == 0
    assert gets == [INDEX_URL]
    assert out.count("would save 6728") == 5
    assert _rows() == []
    assert not (tmp_path / "briefing").exists()


def test_non_pdf_body_fails_without_a_row(tmp_path: Path) -> None:
    def fake_get(url: str) -> bytes:
        return PAGE.encode("utf-8") if url == INDEX_URL else b"<html>404</html>"

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(
            tmp_path / "briefing", _config(tmp_path),
            get=fake_get, read_date=lambda data: None, now=NOW, limit=1,
        )
    assert rc == 1
    assert "not a PDF" in out.getvalue()
    assert _rows() == []


def test_only_rejects_codes_outside_the_config(tmp_path: Path) -> None:
    rc, gets, _out = _run(tmp_path, only=["7203"])
    assert rc == 2
    assert gets == []


def test_shipped_config_loads() -> None:
    issuers = mod.load_registry(ROOT / "config" / "market" / "briefing_pages.json")
    codes = [issuer.code for issuer in issuers]
    assert "6728" in codes
    assert codes == sorted(codes)


def test_link_text_drops_size_and_format_notes() -> None:
    cases = {
        "プレゼンテーション資料(1.7MB)": "プレゼンテーション資料",
        "質疑応答[PDF: 548KB]": "質疑応答",
        "決算説明資料(第2四半期)(PDF 4,887KB)": "決算説明資料(第2四半期)",
        "要旨(Q&A) (486 KB) pdf": "要旨(Q&A)",
        "Q&A (PDF形式)": "Q&A",
        "【一括版】プレゼンテーション資料(6.10MB/106ページ)": "【一括版】プレゼンテーション資料",
        "2026年度第1四半期決算プレゼンテーション資料PDF": "2026年度第1四半期決算プレゼンテーション資料",
        "(591KB)(新しいウィンドウで開きます)": "",
        "ノーツ付き決算説明資料(新中長期経営計画)": "ノーツ付き決算説明資料(新中長期経営計画)",
    }
    for raw, want in cases.items():
        assert mod.clean_link_text(raw) == want, raw


def test_size_change_keeps_the_document_id() -> None:
    page = '<h2>2027年3月期 第2四半期</h2><a href="/a/q2.pdf">説明資料(%s)</a>'
    before = mod.parse_deck_links(page % "1.54MB", INDEX_URL, "63670")
    after = mod.parse_deck_links(page % "1.60MB", INDEX_URL, "63670")
    assert before[0].title == "2027年3月期 第2四半期 説明資料"
    assert before[0].document_id == after[0].document_id


def test_link_re_takes_texts_without_a_kind_word() -> None:
    page = (
        "<h2>2026年度</h2>"
        '<a href="/a/pre.pdf">プレゼンテーション:2027年3月期 第2四半期 連結決算の概要 (PDF形式)</a>'
        '<a href="/a/digital.pdf">プレゼンテーション:Hitachi Digital Presentation (PDF形式)</a>'
        '<a href="/a/tanshin.pdf">決算短信 (PDF形式)</a>'
        '<a href="/a/qa.pdf">決算説明会 Q&amp;A (PDF形式)</a>'
    )
    assert [link.title for link in mod.parse_deck_links(page, INDEX_URL, "65010")] == ["2026年度 決算説明会 Q&A"]
    links = mod.parse_deck_links(page, INDEX_URL, "65010", "^プレゼンテーション:.*決算")
    assert [link.title for link in links] == [
        "2026年度 プレゼンテーション:2027年3月期 第2四半期 連結決算の概要",
        "2026年度 決算説明会 Q&A",
    ]


def test_bad_link_re_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"issuers": [{"code": "6501", "name": "日立", "index_url": INDEX_URL, "link_re": "("}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bad link_re"):
        mod.load_registry(path)


def test_index_follows_redirect_and_meta_refresh(tmp_path: Path) -> None:
    year_url = "https://ir.example.com/ja/meeting/2027/index.html"
    pages = {
        INDEX_URL: ('<meta http-equiv="Refresh" content="0;URL=/ja/meeting/2027/index.html">', "https://ir.example.com/ja/meeting/index.html"),
        year_url: ('<h2>2027年3月期</h2><a href="../pdf/q2_ppt.pdf">プレゼンテーション資料 (PDF 4.08MB)</a>', year_url),
    }
    asked: list[str] = []

    def fake_page(url: str) -> tuple[bytes, str]:
        asked.append(url)
        body, final = pages[url]
        return body.encode("utf-8"), final

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(
            tmp_path / "briefing", _config(tmp_path),
            get=lambda url: _pdf(url), get_page=fake_page, read_date=lambda data: None, now=NOW,
        )
    assert rc == 0
    assert asked == [INDEX_URL, year_url]
    (row,) = _rows()
    assert row.url == "https://ir.example.com/ja/meeting/pdf/q2_ppt.pdf"
    assert row.title == "2027年3月期 プレゼンテーション資料"


def test_date_falls_back_to_the_day_in_the_url(tmp_path: Path) -> None:
    page = (
        '<h2>2026年度 第1四半期</h2><a href="/pdf/260803/20260803j.pdf">決算説明会資料</a>'
        '<h2>2026年度 第2四半期</h2><a href="/pdf/20269999j.pdf">決算説明会資料</a>'
    )
    rc, _gets, _out = _run(tmp_path, page=page)
    assert rc == 0
    rows = {row.title: row.pubdate for row in _rows()}
    assert rows["2026年度 第1四半期 決算説明会資料"] == "2026-08-03 00:00:00"
    assert rows["2026年度 第2四半期 決算説明会資料"] == "2026-11-10 18:00:00"


def test_same_day_same_title_gets_its_own_file(tmp_path: Path) -> None:
    page = (
        '<h2>2027年3月期</h2>'
        '<a href="/pdf/q2_ppt.pdf">プレゼンテーション資料</a>'
        '<a href="/pdf/q1_ppt.pdf">プレゼンテーション資料</a>'
    )
    rc, _gets, _out = _run(tmp_path, page=page)
    assert rc == 0
    rows = _rows()
    assert len(rows) == 2
    assert len({row.rel_path for row in rows}) == 2
    for row in rows:
        assert (tmp_path / "briefing" / row.rel_path).read_bytes() == _pdf(row.url)


def test_hidden_and_icon_text_inside_a_link_is_not_read() -> None:
    page = (
        "<h2>2026年度 第2四半期</h2>"
        '<a href="/a/p01.pdf" aria-label="プレゼンテーション"><div><span>プレゼンテーション</span>'
        '<span aria-hidden="true">プレゼンテーション<span>x</span></span></div>'
        "<svg><title>Download プレゼンテーション</title><path d=\"M0\"/></svg></a>"
        '<a href="/a/sum.pdf"><style>.css-1{width:16px;}</style>決算説明資料<img aria-hidden="true" src="i.png">(1.2MB)</a>'
        '<div aria-hidden="true"><a href="/a/old.pdf">決算説明会資料</a></div>'
    )
    links = mod.parse_deck_links(page, INDEX_URL, "45020", "^プレゼンテーション$")
    assert [link.title for link in links] == [
        "2026年度 第2四半期 プレゼンテーション",
        "2026年度 第2四半期 決算説明資料",
        "2026年度 第2四半期 決算説明会資料",
    ]


def test_format_only_link_takes_the_label_before_it() -> None:
    page = (
        "<h2>決算発表資料</h2>"
        '<div>決算短信</div><a href="/f/earnings.pdf">PDF</a><a href="/f/earnings.html">HTML</a>'
        '<div>プレゼンテーション資料</div><a href="/f/presentation.pdf">PDF</a>'
        '<a href="/f/again.pdf">PDF</a>'
        "<p>" + "長い説明文です。" * 10 + '決算説明資料</p><a href="/f/prose.pdf">PDF</a>'
        '<div>決算説明資料</div><a href="/f/icon.pdf">(591KB)(新しいウィンドウで開きます)</a>'
    )
    links = mod.parse_deck_links(page, INDEX_URL, "60980")
    assert [(link.title, link.url.rsplit("/", 1)[-1]) for link in links] == [
        ("決算発表資料 プレゼンテーション資料", "presentation.pdf"),
    ]


def test_issuer_with_two_pages_reads_both(tmp_path: Path) -> None:
    back_url = "https://ir.example.co.jp/ja/ir/library/result_back.html"
    path = tmp_path / "two.json"
    path.write_text(
        json.dumps({"issuers": [{"code": "9983", "name": "ＦＲ", "index_url": [INDEX_URL, back_url]}]}),
        encoding="utf-8",
    )
    pages = {
        INDEX_URL: '<h2>2026年8月期</h2><a href="/a/2026.pdf">決算説明会資料</a>',
        back_url: (
            '<h2>2026年8月期</h2><a href="/a/2026.pdf">決算説明会資料</a>'
            '<h2>2025年8月期</h2><a href="/a/2025.pdf">決算説明会資料</a>'
        ),
    }

    def fake_get(url: str) -> bytes:
        return pages[url].encode("utf-8") if url in pages else _pdf(url)

    out = io.StringIO()
    with redirect_stdout(out):
        rc = mod.run(tmp_path / "briefing", path, get=fake_get, read_date=lambda data: None, now=NOW)
    assert rc == 0
    assert "listed=2 saved=2" in out.getvalue()
    assert [row.title for row in _rows()] == ["2025年8月期 決算説明会資料", "2026年8月期 決算説明会資料"]


EIR_URL = "https://ssl4.eir-parts.net/V4Public/EIR/8002/ja/announcement/announcement_2.js"


def _eir_item(title: str, link: str, news_type: str = "ir_material_for_fiscal_ym", kind: str = "pdf",
              date: str = "2026/11/04 00:00:00", fiscal_ym: str = "202703") -> dict:
    return {
        "date": date, "link": link, "title": title, "type": kind, "news_type": news_type,
        "fiscal_ym": fiscal_ym, "quarter": "2",
    }


def _eir_feed(items: list[dict]) -> str:
    return "\ufeffeolparts_announcement_2(\n" + json.dumps({"header": [{}], "item": items}, ensure_ascii=False) + "\n);"


EIR_ITEMS = [
    _eir_item("第2四半期 IR資料（スクリプト付）", "https://ssl4.eir-parts.net/doc/8002/ir_material_for_fiscal_ym5/217107/00.pdf"),
    _eir_item("質疑応答", "https://ssl4.eir-parts.net/doc/8002/ir_material_for_fiscal_ym5/217319/00.pdf"),
    _eir_item("IR資料_2027年3月期第2四半期", "https://ssl4.eir-parts.net/doc/8002/ir_material_for_fiscal_ym4/216973/00.pdf"),
    _eir_item("純利益", "", kind="space"),
    _eir_item("説明会動画 2026年11月4日", "https://www.irwebcasting.com/20261104/3/index.html", kind="url"),
    _eir_item("2027年3月期 第2四半期（中間期）決算短信〔ＩＦＲＳ〕（連結）",
              "https://ssl4.eir-parts.net/doc/8002/tdnet/2903789/00.pdf", news_type="tanshin"),
    _eir_item("決算説明資料", "https://ssl4.eir-parts.net/doc/8002/tdnet/2903790/00.pdf", news_type="press"),
    _eir_item("個人投資家向け説明会　プレゼンテーション資料",
              "https://ssl4.eir-parts.net/doc/8002/ir_material2/286209/00.pdf",
              news_type="ir_material", date="2026/09/04 00:00:00", fiscal_ym=""),
    _eir_item("2026年3月期 決算説明会資料", "https://ssl4.eir-parts.net/doc/8002/ir_material2/280000/00.pdf",
              news_type="ir_material", date="2026/05/01 00:00:00", fiscal_ym=""),
]


def test_eir_feed_takes_ir_material_pdfs_with_dates() -> None:
    assert mod.is_eir_feed(EIR_URL)
    assert not mod.is_eir_feed("https://www.marubeni.com/jp/ir/reports/year/")
    links = mod.parse_eir_feed(_eir_feed(EIR_ITEMS), "80020", "IR資料")
    assert [(link.title, link.pubdate) for link in links] == [
        ("2027年3月期 第2四半期 IR資料(スクリプト付)", "2026-11-04 00:00:00"),
        ("2027年3月期 質疑応答", "2026-11-04 00:00:00"),
        ("IR資料_2027年3月期第2四半期", "2026-11-04 00:00:00"),
        ("2026年3月期 決算説明会資料", "2026-05-01 00:00:00"),
    ]
    assert len({link.document_id for link in links}) == 4
    without = mod.parse_eir_feed(_eir_feed(EIR_ITEMS), "80020")
    assert [link.title for link in without] == ["2027年3月期 質疑応答", "2026年3月期 決算説明会資料"]


def test_eir_issuer_saves_with_the_feed_date(tmp_path: Path) -> None:
    path = tmp_path / "eir.json"
    path.write_text(
        json.dumps({"issuers": [{"code": "8002", "name": "丸紅", "index_url": EIR_URL, "link_re": "IR資料"}]}),
        encoding="utf-8",
    )
    gets: list[str] = []

    def fake_get(url: str) -> bytes:
        gets.append(url)
        return _eir_feed(EIR_ITEMS).encode("utf-8") if url == EIR_URL else _pdf(url)

    def run_once() -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            assert mod.run(
                tmp_path / "briefing", path, get=fake_get,
                read_date=lambda data: _at(2020, 1, 1), now=NOW,
            ) == 0
        return out.getvalue()

    assert "listed=4 saved=4 skipped=0 failed=0" in run_once()
    rows = {row.title: row for row in _rows()}
    deck = rows["2027年3月期 第2四半期 IR資料(スクリプト付)"]
    assert deck.pubdate == "2026-11-04 00:00:00"
    assert deck.code == "80020"
    assert deck.rel_path == "8002/20261104_8002_丸紅_2027年3月期 第2四半期 IR資料(スクリプト付).pdf"
    assert deck.url.endswith("/ir_material_for_fiscal_ym5/217107/00.pdf")
    gets.clear()
    assert "listed=4 saved=0 skipped=4 failed=0" in run_once()
    assert gets == [EIR_URL]

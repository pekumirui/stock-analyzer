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
    assert [issuer.code for issuer in issuers] == ["6728"]

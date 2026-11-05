"""短信 ZIP の原文 HTML（KAB-161）: 選び方・保存・流し込み・API。"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from app.db import SessionLocal
from app.models import Stock, XbrlDocument, XbrlFiling
from app.xbrl.documents import fill_missing_documents, load_html, pick_documents, sanitize_html

SUMMARY = "XBRLData/Summary/tse-scedjpsy-99990-20261020399990-ixbrl.htm"
QUAL = "XBRLData/Attachment/qualitative.htm"
BS = "XBRLData/Attachment/0101010-scbs15-tse-scedjpfr-99990-2026-09-20-01-2026-10-20-ixbrl.htm"
PL = "XBRLData/Attachment/0102010-scpl15-tse-scedjpfr-99990-2026-09-20-01-2026-10-20-ixbrl.htm"
# 業績予想・配当予想の修正（tse-rvfc）はフォルダなしの ZIP
RVFC = "tse-rvfcjpsy-99990-20261020399990-ixbrl.htm"

SUMMARY_HTML = (
    '﻿<?xml version="1.0" encoding="utf-8"?><html><head><title>s</title>'
    '<script>alert(1)</script><link rel="stylesheet" href="https://example.com/x.css"/></head>'
    '<body onload="x()"><p onclick=\'y()\'>売上高 <ix:nonFraction name="tse-ed-t:NetSales">33,024</ix:nonFraction></p>'
    '<img src="https://www.release.tdnet.info/img/mark1.gif" alt="" />'
    '<a href="javascript:z()">link</a><iframe src="https://example.com"></iframe></body></html>'
)


def _zip(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for n in names:
            zf.writestr(n, SUMMARY_HTML if n == SUMMARY else f"<html><body>{n}</body></html>")
        zf.writestr("XBRLData/Attachment/manifest.xml", "<manifest/>")
    return path


def test_pick_documents_prefers_qualitative() -> None:
    assert pick_documents([PL, QUAL, BS, SUMMARY]) == [("summary", SUMMARY), ("qualitative", QUAL)]


def test_pick_documents_falls_back_to_attachment_ixbrl_in_order() -> None:
    assert pick_documents([PL, SUMMARY, BS]) == [
        ("summary", SUMMARY), ("attachment", BS), ("attachment", PL),
    ]
    assert pick_documents([PL]) == [("attachment", PL)]
    assert pick_documents(["XBRLData/Attachment/manifest.xml"]) == []


def test_pick_documents_folderless_zip_as_summary() -> None:
    assert pick_documents(["manifest.xml", RVFC, "tse-rvfcjpsy-99990-20261020399990.xsd"]) == [("summary", RVFC)]
    # フォルダつきの書類が 1 つでもあればそちらだけ
    assert pick_documents([RVFC, PL]) == [("attachment", PL)]


def test_sanitize_drops_active_and_external_content() -> None:
    s = sanitize_html(SUMMARY_HTML.lstrip("﻿"))
    assert "<script" not in s and "alert" not in s
    assert "<iframe" not in s and "<link" not in s
    assert "onload" not in s and "onclick" not in s
    assert "javascript:" not in s
    assert "mark1.gif" not in s
    assert "<?xml" not in s
    assert '<ix:nonFraction name="tse-ed-t:NetSales">33,024</ix:nonFraction>' in s


def _seed(db, tmp: Path) -> tuple[XbrlFiling, ...]:
    db.add(Stock(code="99990", name="テスト工業", market="プライム", sector33="金属製品"))
    _zip(tmp / "a.zip", [SUMMARY, QUAL, BS])
    _zip(tmp / "b.zip", [SUMMARY, PL, BS])
    _zip(tmp / "d.zip", [RVFC])
    _zip(tmp / "e.zip", [])
    now = datetime(2026, 10, 20, 15, 30)  # noqa: DTZ001
    rows = []
    for i, (raw, err) in enumerate([
        ("a.zip", None), ("b.zip", None), ("gone.zip", None), ("c.zip", "no-ixbrl"), ("d.zip", None), ("e.zip", None),
    ]):
        f = XbrlFiling(
            code="99990", source="tdnet", doc_id=f"doc{i}", doc_type="quarterly", period_type="Q2",
            submitted_at=now.replace(day=20 - i), title=f"短信{i}", raw_path=raw,
            parse_status="skipped" if err else "parsed", parse_error=err, created_at=now,
        )
        db.add(f)
        rows.append(f)
    db.add(XbrlFiling(
        code="99990", source="edinet", doc_id="S100", doc_type="annual", submitted_at=now,
        title="有報", raw_path="edinet:S100", parse_status="parsed", created_at=now,
    ))
    db.commit()
    return rows[0], rows[1], rows[2], rows[4], rows[5]


def test_fill_missing_documents_and_api(tmp: Path, c) -> None:
    db = SessionLocal()
    try:
        a, b, gone, rvfc, empty = _seed(db, tmp)
        stats = fill_missing_documents(db, tmp)
        assert stats == {"stored": 3, "empty": 1, "missing_zip": 1, "errors": 0}
        # 2 回目は入れ直さない（ZIP が無い分だけ数える。0 件だった ZIP も読み直さない）
        assert fill_missing_documents(db, tmp) == {"stored": 0, "empty": 0, "missing_zip": 1, "errors": 0}
        db.refresh(empty)
        assert empty.documents_status == "empty" and a.documents_status == "stored"
        rv = db.query(XbrlDocument).filter_by(filing_id=rvfc.id).all()
        assert [(d.seq, d.part, d.file_name) for d in rv] == [(0, "summary", RVFC)]

        docs = db.query(XbrlDocument).filter_by(filing_id=a.id).order_by(XbrlDocument.seq).all()
        assert [(d.seq, d.part, d.file_name) for d in docs] == [
            (0, "summary", SUMMARY.rsplit("/", 1)[-1]), (1, "qualitative", "qualitative.htm"),
        ]
        assert docs[0].bytes == len(SUMMARY_HTML.encode("utf-8"))
        assert load_html(docs[0]) == SUMMARY_HTML.lstrip("﻿")
        parts_b = [d.part for d in db.query(XbrlDocument).filter_by(filing_id=b.id).order_by(XbrlDocument.seq)]
        assert parts_b == ["summary", "attachment", "attachment"]
        a_id, gone_id = a.id, gone.id
    finally:
        db.close()

    r = c.get("/api/stock/9999/filings")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == "99990" and body["name"] == "テスト工業"
    # TDnet だけ。新しい順
    assert [f["title"] for f in body["filings"]] == ["短信0", "短信1", "短信2", "短信3", "短信4", "短信5"]
    by_id = {f["id"]: f for f in body["filings"]}
    assert [d["part"] for d in by_id[a_id]["documents"]] == ["summary", "qualitative"]
    assert by_id[gone_id]["documents"] == []

    r = c.get(f"/api/stock/filings/{a_id}/documents/0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "sandbox" in csp and "allow-scripts" not in csp
    assert "<script" not in r.text and "33,024" in r.text
    etag = r.headers["etag"]
    assert c.get(f"/api/stock/filings/{a_id}/documents/0", headers={"If-None-Match": etag}).status_code == 304

    assert c.get(f"/api/stock/filings/{a_id}/documents/9").status_code == 404
    assert c.get("/api/stock/1234/filings").status_code == 404
    assert c.get("/api/stock/xx/filings").status_code == 400


def test_spa_has_stock_tab() -> None:
    root = Path(__file__).resolve().parents[2]
    html = (root / "static/index.html").read_text(encoding="utf-8")
    tabs = (root / "static/js/tabs.js").read_text(encoding="utf-8")
    js = (root / "static/js/stock.js").read_text(encoding="utf-8")
    # 銘柄はメニューに置かず、ヘッダー右上の銘柄検索から開く（KAB-365）
    assert 'data-tab="stock"' not in html and 'id="tab-stock"' in html
    assert 'id="sym-btn"' in html and 'id="sym-q"' in html and 'id="st-sym"' not in html
    assert '/static/js/stock.js' in html
    assert '"stock"' in tabs and "openStockRoute(code)" in tabs
    # 原文は script を動かさない iframe で出す
    assert 'setAttribute("sandbox", "allow-same-origin")' in js
    assert "/api/stocks/search" in js
    # スマホは既定で幅に合わせる（KAB-163）
    assert 'id="st-zoom"' in html and 'let stockZoom = "fit"' in js
    # 縦スクロールバーで #st-doc が縮んだら測り直す（KAB-175）
    assert "new ResizeObserver" in js and ".observe(stDoc)" in js
    assert "requestAnimationFrame(() => fitStockFrame(frame))" in js


def test_sanitize_stops_ios_text_autosizing() -> None:
    """iOS Safari が目次の行だけ文字を大きくするのを止める（KAB-168）。head の直後に 1 つ。"""
    from app.xbrl.documents import TEXT_SIZE_ADJUST

    s = sanitize_html('<html><head><title>t</title></head><body><p>x</p></body></html>')
    assert s.count(TEXT_SIZE_ADJUST) == 1
    assert s.index("<head>") < s.index(TEXT_SIZE_ADJUST) < s.index("<title>")
    assert "-webkit-text-size-adjust:100%" in TEXT_SIZE_ADJUST
    # head の無い断片は先頭に付ける
    assert sanitize_html("<p>x</p>").startswith(TEXT_SIZE_ADJUST)


def test_etag_changes_with_sanitize_version(monkeypatch) -> None:
    """出力を変えたら ETag も変わる。原文の sha256 だけだと 304 が返り続けて古い HTML が残る（KAB-168）。"""
    from app.api import stock

    a = stock._doc_headers("ab" * 32)["ETag"]
    monkeypatch.setattr(stock, "SANITIZE_VERSION", "999")
    b = stock._doc_headers("ab" * 32)["ETag"]
    assert a != b and a.startswith('"' + "ab" * 32)


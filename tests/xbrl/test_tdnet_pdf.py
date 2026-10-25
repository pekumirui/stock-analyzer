"""IR BANK の TDnet PDF → HTML → tdnet_pdf_documents（KAB-164）: 変換・対象の選び方・取込・API。"""
from __future__ import annotations

import gzip
from datetime import date, datetime

import httpx

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlDocument, XbrlFiling
from tools.tdnet_pdf import ingest
from tools.tdnet_pdf.convert import CONVERTER, convert_pdf


def make_pdf(title: str = "Financial Results FY2025", value: str = "1,234") -> bytes:
    """Helvetica の文字 2 行と、網掛けの四角 1 つだけの PDF。"""
    content = (
        f"0.8 g 50 700 200 20 re f 0 g 0.5 w 50 650 m 250 650 l S "
        f"BT /F1 14 Tf 50 750 Td ({title}) Tj ET BT /F1 10 Tf 60 705 Td ({value}) Tj ET"
    ).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _pdf(objs)


def _pdf(objs: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def test_convert_pdf_draws_text_rules_and_shading() -> None:
    conv = convert_pdf(make_pdf())
    assert conv.pages == 1
    assert conv.title == "Financial Results FY2025"
    assert ">Financial Results FY2025</text>" in conv.html
    assert ">1,234</text>" in conv.html
    assert 'textLength="' in conv.html and 'font-family="sans-serif"' in conv.html
    assert 'fill="rgb(204,204,204)"' in conv.html  # 0.8 g の網掛け
    assert conv.html.count("<path ") == 2  # 網掛けと線
    assert 'stroke="rgb(0,0,0)"' in conv.html
    assert "<script" not in conv.html and CONVERTER == "svg2"
    # バイト列でもパスでも読める
    assert convert_pdf(make_pdf("Other title")).title is None


def test_convert_pdf_joins_surrogate_pairs() -> None:
    """ToUnicode が BMP の外（𠮷）なら pdfium はサロゲート 2 つで返す。1 字に戻し、組にならないものは U+FFFD（KAB-169）。"""
    cmap = (
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def "
        b"1 begincodespacerange <00> <FF> endcodespacerange "
        b"3 beginbfchar <41> <D842DFB7> <42> <D842> <43> <DC00> endbfchar endcmap end end"
    )
    content = b"BT /F1 12 Tf 50 750 Td (A) Tj ET BT /F1 12 Tf 50 700 Td (B) Tj ET BT /F1 12 Tf 50 650 Td (C) Tj ET"
    pdf = _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream",
    ])
    conv = convert_pdf(pdf)
    conv.html.encode("utf-8")  # 落ちない
    assert ">\U00020bb7</text>" in conv.html
    assert conv.html.count(">\ufffd</text>") == 2


def test_fetch_pdf_rejects_truncated_linearized() -> None:
    """IR BANK の PDF が途中で切れている（/Linearized の /L より短い）なら failed にする（KAB-169。20260212556420）。"""
    head = b"%PDF-1.4\r%\xe2\xe3\xcf\xd3\r\n92 0 obj\r\n<</Linearized 1/L 277188/H [ 1066 307]/O 94/E 106362/N 11/T 275228>>\r\nendobj\r\n"
    body = head + b"x" * 1000

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            ingest.fetch_pdf(client, "20260212556420")
        except ValueError as exc:
            assert "truncated" in str(exc) and "277188" in str(exc)
        else:
            raise AssertionError("truncated pdf was accepted")
    # 長さが /L 以上なら通す（後ろに追記された PDF もある）
    full = head + b"x" * (277188 - len(head))
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=full))) as client:
        assert ingest.fetch_pdf(client, "20260212556420") == full


def _seed(db) -> None:
    db.add(Stock(code="99990", name="テスト工業", market="プライム", sector33="金属製品"))
    for no, d in [
        ("20260101000001", date(2026, 1, 1)),   # XBRL の HTML がある → 飛ばす
        ("20250101000002", date(2025, 1, 1)),   # 取る
        ("20240101000003", date(2024, 1, 1)),   # 404 → missing
        ("20230101000004", date(2023, 1, 1)),   # 429 → ここで止まる
        ("20220101000005", date(2022, 1, 1)),   # failed 済み → 既定では飛ばす
    ]:
        db.add(Statement(disclosure_no=no, code="99990", disclosed_date=d, disclosed_time="15:30:00",
                         type_of_document="FYFinancialStatements_Consolidated_JP"))
    now = datetime(2026, 1, 1, 15, 30)  # noqa: DTZ001
    f = XbrlFiling(code="99990", source="tdnet", doc_id="0812" + "20260101000001", doc_type="annual",
                   submitted_at=now, title="短信", raw_path="x.zip", parse_status="parsed", created_at=now)
    db.add(f)
    db.flush()
    db.add(XbrlDocument(filing_id=f.id, seq=0, part="summary", file_name="s.htm", html_gz=gzip.compress(b"<p>x</p>"),
                        bytes=8, sha256="0" * 64, created_at=now))
    db.add(TdnetPdfDocument(disclosure_no="20220101000005", code="99990", status="failed", error="x", created_at=now))
    db.commit()


def test_targets_run_and_api(c) -> None:
    db = SessionLocal()
    try:
        _seed(db)
        nos = [s.disclosure_no for s in ingest.targets(db, ["99990"])]
        assert nos == ["20250101000002", "20240101000003", "20230101000004"]
        assert "20220101000005" in [s.disclosure_no for s in ingest.targets(db, ["99990"], retry_failed=True)]
        assert ingest.targets(db, ["12340"]) == []

        pdf = make_pdf("Financial Results FY2024")

        def handler(req: httpx.Request) -> httpx.Response:
            no = req.url.path.rsplit("/", 1)[-1].removesuffix(".pdf")
            if no == "20250101000002":
                return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
            if no == "20240101000003":
                return httpx.Response(404)
            return httpx.Response(429)

        logs: list[str] = []
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            stats = ingest.run(db, ["99990"], sleep=0, client=client, log=logs.append)
        assert stats == {"ok": 1, "missing": 1, "failed": 0, "stopped": "rate_limited", "remaining": 1}
        ok = db.get(TdnetPdfDocument, "20250101000002")
        assert ok.status == "ok" and ok.pages == 1 and ok.converter == CONVERTER
        assert ok.title == "Financial Results FY2024" and ok.pdf_bytes == len(pdf)
        assert gzip.decompress(ok.html_gz).decode().count("<svg") == 1
        assert db.get(TdnetPdfDocument, "20240101000003").status == "missing"
        assert any(line.startswith("rate_limited 20230101000004") for line in logs)

        # --limit で止まる。もう一度流すと残りだけ
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            again = ingest.run(db, ["99990"], sleep=0, limit=0, client=client, log=logs.append)
        assert again["stopped"] == "limit" and again["remaining"] == 1
    finally:
        db.close()

    body = c.get("/api/stock/9999/filings").json()
    kinds = [(f["source"], f["submitted_at"][:10]) for f in body["filings"]]
    assert kinds == [("xbrl", "2026-01-01"), ("pdf", "2025-01-01")]
    pdf_entry = body["filings"][1]
    assert pdf_entry["disclosure_no"] == "20250101000002"
    assert pdf_entry["attribution"].startswith("IR BANK")
    assert pdf_entry["doc_type"] == "annual"  # 題名の Financial Results（四半期・中間の語なし）
    assert pdf_entry["documents"] == [{"seq": 0, "part": "pdf", "pages": 1, "url": "/api/stock/pdf/20250101000002"}]
    assert body["filings"][0]["documents"][0]["url"].startswith("/api/stock/filings/")

    r = c.get("/api/stock/pdf/20250101000002")
    assert r.status_code == 200 and "<svg" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert c.get("/api/stock/pdf/20250101000002", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert c.get("/api/stock/pdf/20240101000003").status_code == 404  # missing
    assert c.get("/api/stock/pdf/20990101000000").status_code == 404


def test_cli_requires_code_or_all(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit):
        ingest.main([])
    assert ingest.code5("5933") == "59330" and ingest.code5("262A0") == "262A0"


def test_cli_does_not_create_tables() -> None:
    """表はデプロイの migrate が作る。ツールは作らず、無ければ落ちる（KAB-165）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "tools/tdnet_pdf/ingest.py").read_text(encoding="utf-8")
    assert "init_db" not in src and "create_all" not in src

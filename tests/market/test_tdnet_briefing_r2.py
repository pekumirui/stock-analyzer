"""決算説明資料の表題判定と、TDnet 保存の冪等。ネットワークには出ない。"""
from __future__ import annotations

import hashlib
import io
import re
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from app.db import SessionLocal
from app.market.briefing_store import SOURCE_HOMEPAGE, SOURCE_TDNET, record_briefing
from app.market.briefing_title import is_material_title, title_decision
from app.models import BriefingDocument

ROOT = Path(__file__).resolve().parents[2]


def _fields(source: str, document_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(BriefingDocument, (source, document_id))
        if row is None:
            return None
        return {
            "source": row.source,
            "document_id": row.document_id,
            "code": row.code,
            "company_name": row.company_name,
            "pubdate": row.pubdate,
            "title": row.title,
            "rel_path": row.rel_path,
            "url": row.url,
            "sha256": row.sha256,
            "size": row.size,
            "status": row.status,
        }
    finally:
        db.close()


def test_material_titles_match_briefing_pdfs() -> None:
    accept = (
        "2027年3月期 第2四半期決算説明資料",
        "2026年12月期 第3四半期決算補足説明資料",
        "2026年12月期第3四半期決算説明会資料",
        "2027年3月期 第２四半期連結決算短信 補足資料（ＩＦＲＳ）",
        "2027年3月期 第2四半期 決算説明会 プレゼンテーション資料",
        "2027年6月期 第1四半期説明補足資料",
        "決算補足資料（2026年度 第2四半期決算）",
        "2026年３月期　決算　説明資料",
        "2027年3月期 第2四半期（中間期） 決算補足説明資料（通期連結業績予想修正）",
        "Financial Results Presentation",
        "Q2 Earnings Presentation Materials",
        "Supplementary Materials for the Financial Results",
    )
    for title in accept:
        assert is_material_title(title), title
        assert title_decision(title) == "accept"


def test_corrections_and_notices_are_not_materials() -> None:
    corrections = (
        "（訂正）「2026年８月期通期 決算説明資料」の一部訂正について",
        "Correction of the Earnings Presentation",
    )
    notices = (
        "決算説明会開催のお知らせ",
        "決算説明会の開催中止に関するお知らせ",
        "2027年５月期第１四半期 決算説明資料の掲載に関するお知らせ",
        "決算説明資料の公開について",
        "Notice of Financial Results Briefing",
    )
    others = (
        "2027年3月期 第2四半期決算短信〔日本基準〕（連結）",
        "2027年２月期第２四半期決算説明会及び新中期経営計画説明会",
        "事業計画及び成長可能性に関する説明資料",
        "ファイナンス補足説明資料",
        "本日公表のプレスリリースに関する補足説明資料",
        "中期経営計画説明会資料",
        "8月度 月次売上高のお知らせ",
    )
    for title in corrections:
        assert not is_material_title(title), title
        assert title_decision(title) == "correction"
    for title in notices:
        assert not is_material_title(title), title
        assert title_decision(title) == "notice"
    for title in others:
        assert not is_material_title(title), title


def test_crontab_retries_missed_days() -> None:
    days: list[int] = []
    for line in (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.search(r"jobs/market/tdnet_briefing_r2\.py\s+(\d+)\b", line)
        if match:
            days.append(int(match.group(1)))
    assert days, "deploy/crontab has no tdnet_briefing_r2.py days"
    assert all(n >= 4 for n in days)
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert f"jobs/market/tdnet_briefing_r2.py {days[0]}" in readme


def _rec(code: str, name: str, title: str, pdf: str) -> dict:
    return {
        "pubdate": "2026-10-30 15:00:00",
        "company_code": code,
        "company_name": name,
        "title": title,
        "document_url": f"https://www.release.tdnet.info/inbs/{pdf}.pdf" if pdf else "",
        "url_xbrl": "",
    }


def _run(mod, dest: Path, rows: list[dict], body: bytes | None = None, *, dry_run: bool = False):
    gets: list[str] = []

    def fake_days(start, end, get, sleep_sec):  # noqa: ARG001
        return rows

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        gets.append(url)
        if body is not None:
            return body
        return b"%PDF-1.4 " + url.encode()

    buf = io.StringIO()
    with patch.object(mod.tdnet_list, "fetch_days", fake_days), \
            patch.object(mod, "http_get", fake_get), \
            patch.object(mod, "date_window", lambda days: (date(2026, 10, 30), date(2026, 10, 30))), \
            patch.object(mod.time, "sleep", lambda s: None), \
            redirect_stdout(buf):
        rc = mod.run(1, dest, dry_run=dry_run)
    return rc, buf.getvalue(), gets


def test_saves_pdf_and_tdnet_index_once(tmp_path: Path) -> None:
    import jobs.market.tdnet_briefing_r2 as mod

    title = "2027年3月期 第2四半期決算説明資料"
    row = _rec("72030", "トヨタ", title, "140120261030000001")
    correction = _rec("72030", "トヨタ", "（訂正）決算説明資料の一部訂正について", "140120261030000002")
    notice = _rec("99840", "ソフトバンク", "決算説明会開催のお知らせ", "140120261030000003")
    tanshin = _rec("67580", "ソニー", "2027年3月期 第2四半期決算短信", "140120261030000004")
    no_id = _rec("72030", "トヨタ", "決算説明資料", "")
    alpha = _rec("386A0", "例", "2027年3月期 決算補足説明資料", "140120261030000005")
    dup = dict(row)
    dest = tmp_path / "briefing"
    rc, out, gets = _run(mod, dest, [row, correction, notice, tanshin, no_id, alpha, dup])
    assert rc == 0, out
    assert gets == [
        row["document_url"],
        alpha["document_url"],
    ]
    saved = _fields(SOURCE_TDNET, "140120261030000001")
    assert saved is not None
    assert saved["code"] == "72030"
    assert saved["company_name"] == "トヨタ"
    assert saved["pubdate"] == "2026-10-30 15:00:00"
    assert saved["title"] == title
    assert saved["status"] == "saved"
    assert saved["url"] == row["document_url"]
    assert saved["rel_path"].startswith("7203/")
    assert saved["rel_path"].endswith(".pdf")
    assert "7203" in Path(saved["rel_path"]).name
    path = dest / saved["rel_path"]
    assert path.is_file()
    assert saved["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert saved["size"] == path.stat().st_size
    alpha_row = _fields(SOURCE_TDNET, "140120261030000005")
    assert alpha_row is not None and alpha_row["code"] == "386A0"
    assert alpha_row["rel_path"].startswith("386A/")
    assert _fields(SOURCE_TDNET, "140120261030000002") is None
    assert "skip no-pdf-id 72030" in out
    assert "saved=2" in out and "failed=0" in out

    rc2, out2, gets2 = _run(mod, dest, [row, alpha])
    assert rc2 == 0, out2
    assert gets2 == []
    assert "skip exists" in out2
    assert _fields(SOURCE_TDNET, "140120261030000001")["sha256"] == saved["sha256"]


def test_existing_pdf_is_indexed_without_download(tmp_path: Path) -> None:
    import jobs.market.tdnet_briefing_r2 as mod

    title = "2027年3月期 決算説明会資料"
    row = _rec("67580", "ソニーＧ", title, "140120261030000011")
    dest = tmp_path / "briefing"
    path = mod.dest_path(dest, row["pubdate"], row["company_code"], row["company_name"], title)
    path.parent.mkdir(parents=True)
    body = b"%PDF-1.4 already"
    path.write_bytes(body)
    rc, out, gets = _run(mod, dest, [row])
    assert rc == 0, out
    assert gets == []
    assert "reindexed " in out
    assert "reindexed=1" in out
    saved = _fields(SOURCE_TDNET, "140120261030000011")
    assert saved["rel_path"] == path.relative_to(dest).as_posix()
    assert saved["sha256"] == hashlib.sha256(body).hexdigest()
    assert path.read_bytes() == body


def test_non_pdf_is_not_kept(tmp_path: Path) -> None:
    import jobs.market.tdnet_briefing_r2 as mod

    row = _rec("72030", "トヨタ", "決算説明資料", "140120261030000021")
    dest = tmp_path / "briefing"
    rc, out, gets = _run(mod, dest, [row], body=b"<html>")
    assert rc == 1
    assert gets
    assert not list(dest.rglob("*.pdf"))
    assert not list(dest.rglob("*.part"))
    assert _fields(SOURCE_TDNET, "140120261030000021") is None
    assert "failed=1" in out


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    import jobs.market.tdnet_briefing_r2 as mod

    row = _rec("72030", "トヨタ", "2027年3月期 決算説明資料", "140120261030000031")
    notice = _rec("72030", "トヨタ", "決算説明会開催のお知らせ", "140120261030000032")
    dest = tmp_path / "briefing"
    rc, out, gets = _run(mod, dest, [row, notice], dry_run=True)
    assert rc == 0, out
    assert gets == []
    assert not dest.exists()
    assert _fields(SOURCE_TDNET, "140120261030000031") is None
    assert "dry-run listed=1" in out
    assert "example accept 72030" in out
    assert row["title"] in out


def test_run_uses_shared_title_gate(tmp_path: Path) -> None:
    import jobs.market.tdnet_briefing_r2 as mod

    row = _rec("72030", "トヨタ", "2027年3月期 決算説明資料", "140120261030000041")
    dest = tmp_path / "briefing"
    buf = io.StringIO()

    def fake_days(start, end, get, sleep_sec):  # noqa: ARG001
        return [row]

    with patch.object(mod, "is_material_title", lambda title: False), \
            patch.object(mod.tdnet_list, "fetch_days", fake_days), \
            patch.object(mod, "http_get", lambda url, timeout=30.0: b"%PDF-1.4"), \
            patch.object(mod, "date_window", lambda days: (date(2026, 10, 30), date(2026, 10, 30))), \
            patch.object(mod.time, "sleep", lambda s: None), \
            redirect_stdout(buf):
        rc = mod.run(1, dest)
    assert rc == 0, buf.getvalue()
    assert "listed=0" in buf.getvalue()
    assert not list(dest.rglob("*.pdf"))


def test_homepage_uses_the_same_index() -> None:
    record_briefing(
        source=SOURCE_HOMEPAGE,
        document_id="140120261030000001",
        code="72030",
        company_name="トヨタ",
        pubdate="2026-10-30 15:00:00",
        title="2027年3月期 決算説明資料",
        rel_path="7203/from-homepage.pdf",
        url="https://example.test/ir/briefing.pdf",
        sha256="abc",
        size=12,
    )
    home = _fields(SOURCE_HOMEPAGE, "140120261030000001")
    tdnet = _fields(SOURCE_TDNET, "140120261030000001")
    assert home is not None and home["source"] == SOURCE_HOMEPAGE
    assert home["rel_path"] == "7203/from-homepage.pdf"
    assert home["status"] == "saved"
    assert tdnet is None or tdnet["source"] == SOURCE_TDNET
    with pytest.raises(ValueError):
        record_briefing(
            source="ir",
            document_id="x",
            code="72030",
            company_name=None,
            pubdate="2026-10-30 15:00:00",
            title="決算説明資料",
            rel_path="7203/x.pdf",
            url=None,
            sha256="abc",
            size=1,
        )

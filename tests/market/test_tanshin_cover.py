"""KAB-325: 短信1ページ目の説明資料・説明会の有無。書類ごとに残し、ETF とファンドは除く。"""
from __future__ import annotations

import gzip
from datetime import date, datetime
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.market.tanshin_cover import (
    cover_text_from_html,
    is_cover_target,
    parse_cover_text,
    sync_covers,
)
from app.models import Stock, TanshinCover, TdnetPdfDocument, XbrlDocument, XbrlFiling
from jobs.market.tanshin_covers import main as covers_main
from jobs.market.tanshin_covers import parse_args, since_from_days

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "tdnet_pdf" / "html"
NOW = datetime(2026, 11, 2, 12, 0)  # noqa: DTZ001


@pytest.fixture(autouse=True)
def _clean() -> None:
    db = SessionLocal()
    try:
        db.query(TanshinCover).delete()
        db.query(XbrlDocument).delete()
        db.query(XbrlFiling).delete()
        db.query(TdnetPdfDocument).delete()
        db.query(Stock).delete()
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


def _stock(db, code: str, category: str, name: str) -> None:
    db.add(Stock(code=code, name=name, product_category=category))
    db.commit()


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


def test_cover_target_keeps_ordinary_and_reit_and_drops_etf_and_funds() -> None:
    assert is_cover_target("011", "トヨタ自動車") is True
    assert is_cover_target("021", "メディシノバ・インク") is True
    assert is_cover_target("013", "森トラストリート投資法人") is True
    assert is_cover_target("014", "ｉＦｒｅｅＥＴＦ　Ｓ＆Ｐ５００") is False
    assert is_cover_target("023", "ＳＰＤＲゴールド・シェア") is False
    assert is_cover_target("013", "産業ファンド投資法人") is False
    assert is_cover_target("013", "いちごグリーンインフラ投資法人") is False
    assert is_cover_target("012", "日本銀行") is False
    assert is_cover_target(None, None, "2026年9月期 決算短信") is True
    assert is_cover_target(None, None, "ｉＦｒｅｅＥＴＦ 決算短信") is False


def test_sync_prefers_same_day_readable_cover_and_skips_amendment() -> None:
    db = SessionLocal()
    try:
        _stock(db, "72030", "011", "トヨタ自動車")
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
        row = db.get(TanshinCover, "081220261101000001")
        assert row is not None
        assert row.code == "72030"
        assert row.source == "xbrl_summary"
        assert row.has_material is True
        assert row.briefing_note == "アナリスト向け"
        assert db.get(TanshinCover, "20261101000001") is None
        assert db.get(TanshinCover, "081220261102000002") is None
        assert any(line.startswith("72030 ") for line in report.lines)
    finally:
        db.close()


def test_newer_quarter_keeps_the_older_row() -> None:
    db = SessionLocal()
    try:
        _stock(db, "67580", "011", "ソニーグループ")
        _pdf(
            db, "67580", "20261001000001", "2026年3月期 第1四半期決算短信",
            date(2026, 10, 1),
            _pdf_html(["四半期決算補足説明資料作成の有無：有", "四半期決算説明会開催の有無：有"]),
        )
        sync_covers(db, ["67580"])
        assert db.get(TanshinCover, "20261001000001").has_material is True
        _filing(
            db, "67580", "081220261101000009", "2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            datetime(2026, 11, 1, 15, 0), "<html><body><p>本文だけ</p></body></html>",  # noqa: DTZ001
        )
        sync_covers(db, ["67580"])
        newer = db.get(TanshinCover, "081220261101000009")
        older = db.get(TanshinCover, "20261001000001")
        assert newer.source == "xbrl_summary" and newer.has_material is None
        assert older.has_material is True
    finally:
        db.close()


def test_sync_skips_etf_and_infrastructure_fund() -> None:
    db = SessionLocal()
    try:
        _stock(db, "22370", "014", "ｉＦｒｅｅＥＴＦ　Ｓ＆Ｐ５００レバレッジ")
        _stock(db, "92820", "013", "いちごグリーンインフラ投資法人")
        _stock(db, "89610", "013", "森トラストリート投資法人")
        html = _pdf_html(["決算補足説明資料作成の有無：有", "決算説明会開催の有無：無"])
        _pdf(db, "22370", "etf-1", "2026年9月期 決算短信", date(2026, 10, 23), html)
        _pdf(db, "92820", "infra-1", "2026年6月期 決算短信", date(2026, 8, 1), html)
        _pdf(db, "89610", "reit-1", "2026年8月期 決算短信", date(2026, 10, 23), html)
        report = sync_covers(db, ["22370", "92820", "89610"])
        assert report.written == 1
        assert db.get(TanshinCover, "etf-1") is None
        assert db.get(TanshinCover, "infra-1") is None
        assert db.get(TanshinCover, "reit-1").code == "89610"
    finally:
        db.close()


def test_dry_run_does_not_write() -> None:
    db = SessionLocal()
    try:
        _stock(db, "72030", "011", "トヨタ自動車")
        _pdf(
            db, "72030", "20261101000001", "2026年3月期 決算短信",
            date(2026, 11, 1),
            _pdf_html(["決算補足説明資料作成の有無：有", "決算説明会開催の有無：無"]),
        )
        sync_covers(db, ["72030"], dry_run=True)
        assert db.get(TanshinCover, "20261101000001") is None
        assert covers_main(["--code", "7203", "--dry-run"]) == 0
        assert db.get(TanshinCover, "20261101000001") is None
    finally:
        db.close()


def test_parse_args_rejects_a_full_fetch() -> None:
    with pytest.raises(SystemExit):
        parse_args([])
    with pytest.raises(SystemExit):
        parse_args(["--limit", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--days", "0"])
    with pytest.raises(SystemExit):
        parse_args(["--days", "3", "--limit", "5"])
    with pytest.raises(SystemExit):
        parse_args(["--days", "3", "--code", "7203"])
    assert parse_args(["--days", "3"]).days == 3
    args = parse_args(["--code", "7203", "--dry-run"])
    assert args.dry_run is True
    assert args.code == ["7203"]


def test_days_window_includes_today() -> None:
    assert since_from_days(1, NOW) == datetime(2026, 11, 2)  # noqa: DTZ001
    assert since_from_days(3, NOW) == datetime(2026, 10, 31)  # noqa: DTZ001


def test_since_takes_companies_that_filed_in_the_window() -> None:
    db = SessionLocal()
    try:
        _stock(db, "72030", "011", "トヨタ自動車")
        _stock(db, "67580", "011", "ソニーグループ")
        _stock(db, "99840", "011", "ソフトバンクグループ")
        _stock(db, "22370", "014", "ｉＦｒｅｅＥＴＦ　Ｓ＆Ｐ５００レバレッジ")
        yes = _pdf_html(["決算補足説明資料作成の有無：有", "決算説明会開催の有無：無"])
        _filing(
            db, "72030", "081220261101000001", "2026年3月期 第2四半期決算短信〔日本基準〕（連結）",
            datetime(2026, 11, 1, 15, 30), _ixbrl("有", "有"),  # noqa: DTZ001
        )
        _pdf(db, "67580", "20261031000002", "2026年3月期 第2四半期決算短信", date(2026, 10, 31), yes)
        _pdf(db, "99840", "20261030000003", "2026年3月期 第2四半期決算短信", date(2026, 10, 30), yes)
        _pdf(db, "22370", "etf-1", "2026年9月期 決算短信", date(2026, 11, 1), yes)
        _pdf(db, "67580", "20261101000004", "業績予想の修正に関するお知らせ", date(2026, 11, 1), yes)
        report = sync_covers(db, None, since=datetime(2026, 10, 31))  # noqa: DTZ001
        assert report.written == 2
        assert report.skipped == 1
        assert db.get(TanshinCover, "081220261101000001").has_material is True
        assert db.get(TanshinCover, "20261031000002").has_material is True
        assert db.get(TanshinCover, "20261030000003") is None
        assert db.get(TanshinCover, "etf-1") is None
    finally:
        db.close()


def test_crontab_runs_covers_daily() -> None:
    lines = [
        line for line in (ROOT / "deploy" / "crontab").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") and "jobs/market/tanshin_covers.py" in line
    ]
    assert len(lines) == 1
    assert "deploy/cron-run.sh jobs/market/tanshin_covers.py --days 3" in lines[0]
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    assert "jobs/market/tanshin_covers.py --days 3" in readme

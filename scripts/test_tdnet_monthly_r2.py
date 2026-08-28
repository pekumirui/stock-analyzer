"""tdnet_monthly_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tdnet_monthly_r2 import (  # noqa: E402
    already_have,
    date_window,
    dest_filename,
    display_code,
    is_monthly_title,
    parse_list_body,
    tdnet_pdf_id,
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_display_code() -> None:
    if display_code("92790") != "9279":
        _fail(f"92790 → {display_code('92790')!r}")
    if display_code("92510") != "9251":
        _fail(f"92510 → {display_code('92510')!r}")
    if display_code("386A0") != "386A":
        _fail(f"386A0 → {display_code('386A0')!r}")
    if display_code("7203") != "7203":
        _fail(f"7203 → {display_code('7203')!r}")


def test_tdnet_pdf_id() -> None:
    url = (
        "https://webapi.yanoshin.jp/rd.php?"
        "https://www.release.tdnet.info/inbs/140120260805509698.pdf"
    )
    got = tdnet_pdf_id(url)
    if got != "140120260805509698":
        _fail(f"pdf id from document_url: {got!r}")
    if got == "1271048":
        _fail("used yanoshin numeric id")
    direct = "https://www.release.tdnet.info/inbs/140120260805509698.pdf"
    if tdnet_pdf_id(direct) != "140120260805509698":
        _fail(f"direct inbs url: {tdnet_pdf_id(direct)!r}")
    # やのしん id だけの URL は使わない
    if tdnet_pdf_id("https://example.invalid/1271048") is not None:
        _fail("should ignore non-inbs url")


def test_dest_filename_gift() -> None:
    got = dest_filename(
        "2026-08-05 19:00:00",
        "92790",
        "ギフトHD",
        "2026年10月期7月度_直営店売上速報についてのお知らせ",
    )
    expect = "20260805_9279_ギフトHD_2026年10月期7月度_直営店売上速報についてのお知らせ.pdf"
    if got != expect:
        _fail(f"dest_filename: {got!r} != {expect!r}")
    alnum = dest_filename(
        "2026-08-05",
        "386A0",
        "テスト",
        "月次売上",
    )
    if not alnum.startswith("20260805_386A_"):
        _fail(f"386A dest: {alnum!r}")
    messy = dest_filename(
        "2026-08-05 19:00:00",
        "92790",
        "ギフト/HD",
        "速報\nについてのお知らせ",
    )
    if "/" in messy or "\n" in messy:
        _fail(f"unsanitized: {messy!r}")
    # やのしん実タイトルは全角７。既存 R2 名は半角7
    nfkc = dest_filename(
        "2026-08-05 15:30:00",
        "92790",
        "ギフトHD",
        "2026年10月期７月度_直営店売上速報についてのお知らせ",
    )
    if nfkc != expect:
        _fail(f"nfkc dest_filename: {nfkc!r} != {expect!r}")


def test_is_monthly_title() -> None:
    include = [
        "2026年10月期 7月度 月次売上のお知らせ",
        "2026年10月期7月度_直営店売上速報についてのお知らせ",
        "月次決算短信〔日本基準〕",
        "前年比のお知らせ",
        "2026年8月 営業概況",
        "Monthly Report",
        "店舗売上について",
        "月次 人事異動のお知らせ",
        "自己株式の状況（月次）",
        "有報提出に伴う月次補足",
    ]
    for t in include:
        if not is_monthly_title(t):
            _fail(f"should include: {t!r}")
    exclude = [
        "2026年3月期 決算短信〔日本基準〕（連結）",
        "有報の提出に関するお知らせ",
        "人事異動のお知らせ",
        "自己株式の取得状況に関するお知らせ",
        "自己株式立会外買付取引による自己株式の買付け",
        "業績予想の修正に関するお知らせ",
        "売上速報および人事異動のお知らせ",
        "前年比を含む決算短信",
    ]
    for t in exclude:
        if is_monthly_title(t):
            _fail(f"should exclude: {t!r}")


def test_parse_list_body_tdnet_key() -> None:
    raw = """
    {"items": [
      {"Tdnet": {
        "id": "1271048",
        "pubdate": "2026-08-05 19:00:00",
        "company_code": "92790",
        "company_name": "ギフトHD",
        "title": "直営店売上速報についてのお知らせ",
        "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260805509698.pdf"
      }},
      {"TDnet": {
        "id": "1",
        "title": "月次",
        "document_url": "https://www.release.tdnet.info/inbs/140199999999999999.pdf"
      }}
    ]}
    """
    rows = parse_list_body(raw)
    if len(rows) != 2:
        _fail(f"parse_list_body rows: {rows!r}")
    if tdnet_pdf_id(rows[0]["document_url"]) != "140120260805509698":
        _fail("Tdnet wrapper pdf id")
    if rows[0]["id"] == tdnet_pdf_id(rows[0]["document_url"]):
        _fail("yanoshin id must not equal TDnet pdf id")


def test_date_window_and_skip(tmp: Path) -> None:
    start, end = date_window(1, today=__import__("datetime").date(2026, 8, 5))
    if start != end or str(start) != "2026-08-05":
        _fail(f"N=1 window: {start} {end}")
    start, end = date_window(30, today=__import__("datetime").date(2026, 8, 5))
    if str(start) != "2026-07-07" or str(end) != "2026-08-05":
        _fail(f"N=30 window: {start} {end}")
    p = tmp / "empty.pdf"
    p.write_bytes(b"")
    if already_have(p):
        _fail("empty file should not skip")
    p.write_bytes(b"%PDF-1.4")
    if not already_have(p):
        _fail("non-empty should skip")
    missing = tmp / "nope.pdf"
    if already_have(missing):
        _fail("missing should not skip")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_display_code()
    test_tdnet_pdf_id()
    test_dest_filename_gift()
    test_is_monthly_title()
    test_parse_list_body_tdnet_key()
    with TemporaryDirectory() as d:
        test_date_window_and_skip(Path(d))
    print("ok: tdnet monthly r2")


if __name__ == "__main__":
    main()

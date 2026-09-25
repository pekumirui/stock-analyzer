"""tdnet_monthly_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))

from tdnet_monthly_r2 import (  # noqa: E402
    already_have,
    date_window,
    dest_filename,
    dest_path,
    display_code,
    download_skip_reason,
    is_monthly_title,
    jq_lookup_code,
    load_stock_product_map,
    parse_list_body,
    should_skip_infra_fund,
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


def test_jq_lookup_code() -> None:
    cases = (
        ("9282", "92820"),
        ("92820", "92820"),
        ("262A", "262A0"),
        ("262A0", "262A0"),
    )
    for raw, expect in cases:
        got = jq_lookup_code(raw)
        if got != expect:
            _fail(f"jq_lookup_code({raw!r}) → {got!r} != {expect!r}")


def test_should_skip_infra_fund() -> None:
    # 013 + インフラ (9282 / 9284 / 9285)
    if not should_skip_infra_fund(
        "013", "ジャパン・インフラファンド投資法人", "ジャパン・インフラファンド投資法人"
    ):
        _fail("9282 013+インフラ should skip")
    if not should_skip_infra_fund("013", "カナディアン・ソーラー・インフラ投資法人", ""):
        _fail("9284 013+インフラ should skip")
    if not should_skip_infra_fund(
        "013", "東京インフラ・エネルギー投資法人", "東京インフラ・エネルギー投資法人"
    ):
        _fail("9285 013+インフラ should skip")
    # 022 + インフラ
    if not should_skip_infra_fund("022", "外国インフラREIT", "foreign infra"):
        _fail("022 + インフラ should skip")
    # either name is enough when 013/022
    if not should_skip_infra_fund("013", "何かリート", "インフラファンド投資法人"):
        _fail("013 + company_name インフラ should skip")
    if not should_skip_infra_fund("013", "インフラファンド投資法人", "何かリート"):
        _fail("013 + stock_name インフラ should skip")
    # 013 without インフラ: 8985 ジャパン・ホテル・リート投資法人
    if should_skip_infra_fund(
        "013", "ジャパン・ホテル・リート投資法人", "ジャパン・ホテル・リート投資法人"
    ):
        _fail("8985 REIT should keep")
    # master missing / unset + インフラ投資法人
    if not should_skip_infra_fund(None, None, "○○インフラ投資法人"):
        _fail("master missing + インフラ投資法人 should skip")
    if not should_skip_infra_fund("", None, "インフラ投資法人"):
        _fail("unset category + インフラ投資法人 should skip")
    # master missing + ホテル・リート投資法人 (no インフラ投資法人)
    if should_skip_infra_fund(None, None, "ジャパン・ホテル・リート投資法人"):
        _fail("master missing + ホテル・リート should keep")
    # 011 + インフラ in name → keep
    if should_skip_infra_fund("011", "インフラ関連株式会社", "インフラ関連"):
        _fail("011 + インフラ should keep")
    if should_skip_infra_fund("011", "インフラ投資法人もどき", "インフラ投資法人"):
        _fail("011 + インフラ投資法人 should keep")
    # ファンドが挟まると「インフラ投資法人」非該当。マスタ無しでは残す
    if should_skip_infra_fund(None, None, "ジャパン・インフラファンド投資法人"):
        _fail("fallback should not skip インフラファンド投資法人 (no インフラ投資法人)")


def test_stock_product_map_sqlite(tmp: Path) -> None:
    import sqlite3

    db = tmp / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, product_category TEXT)"
    )
    conn.execute(
        "INSERT INTO stocks VALUES ('92820', 'ジャパン・インフラファンド投資法人', '013')"
    )
    conn.execute(
        "INSERT INTO stocks VALUES ('89850', 'ジャパン・ホテル・リート投資法人', '013')"
    )
    conn.execute(
        "INSERT INTO stocks VALUES ('72030', 'インフラ工事株式会社', '011')"
    )
    conn.commit()
    conn.close()

    m = load_stock_product_map(db_path=db)
    cat, name = m[jq_lookup_code("9282")]
    if not should_skip_infra_fund(cat, name, "ジャパン・インフラファンド投資法人"):
        _fail("sqlite 9282 should skip")
    cat, name = m[jq_lookup_code("8985")]
    if should_skip_infra_fund(cat, name, "ジャパン・ホテル・リート投資法人"):
        _fail("sqlite 8985 should keep")
    cat, name = m[jq_lookup_code("7203")]
    if should_skip_infra_fund(cat, name, "インフラ工事株式会社"):
        _fail("sqlite 011 should keep")

    empty = load_stock_product_map(db_path=tmp / "absent.db")
    if empty:
        _fail(f"missing db should be empty map: {empty!r}")
    if not should_skip_infra_fund(None, None, "○○インフラ投資法人"):
        _fail("DB missing fallback should still skip インフラ投資法人")
    if should_skip_infra_fund(None, None, "ジャパン・ホテル・リート投資法人"):
        _fail("DB missing fallback should keep ホテル・リート")
    if download_skip_reason("3032", "ゴルフ・ドゥ", empty) is not None:
        _fail("missing db must not skip unknown 3032")
    if download_skip_reason("3066", "JBイレブン", empty) is not None:
        _fail("missing db must not skip unknown 3066")
    if download_skip_reason("3032", "ゴルフ・ドゥ", m) != "unknown-stock 3032 ゴルフ・ドゥ":
        _fail("loaded map must skip unknown 3032")


def test_download_skip_reason() -> None:
    stock_map: dict[str, tuple[str | None, str | None]] = {
        "92820": ("013", "ジャパン・インフラファンド投資法人"),
        "89850": ("013", "ジャパン・ホテル・リート投資法人"),
        "89520": ("013", "テストリート投資法人"),
        "72030": ("011", "トヨタ自動車"),
        "92790": ("010", "ギフト"),
    }
    # 1. non-empty map + absent code (名古屋単独など) → skip, do not download
    got = download_skip_reason("3032", "ゴルフ・ドゥ", stock_map)
    if got != "unknown-stock 3032 ゴルフ・ドゥ":
        _fail(f"3032 unknown: {got!r}")
    got = download_skip_reason("3066", "JBイレブン", stock_map)
    if got != "unknown-stock 3066 JBイレブン":
        _fail(f"3066 unknown: {got!r}")
    got = download_skip_reason("30320", "ゴルフ・ドゥ", stock_map)
    if got != "unknown-stock 3032 ゴルフ・ドゥ":
        _fail(f"30320 padded unknown: {got!r}")
    # unknown takes precedence over infra-looking name when not in stocks
    got = download_skip_reason("9999", "○○インフラ投資法人", stock_map)
    if got != "unknown-stock 9999 ○○インフラ投資法人":
        _fail(f"unknown infra-looking name: {got!r}")

    # 2. empty map (DB unreadable / missing) → unknown filter off
    empty: dict[str, tuple[str | None, str | None]] = {}
    if download_skip_reason("3032", "ゴルフ・ドゥ", empty) is not None:
        _fail("empty map must not skip 3032")
    if download_skip_reason("3066", "JBイレブン", empty) is not None:
        _fail("empty map must not skip 3066")
    if download_skip_reason("7203", "トヨタ自動車", empty) is not None:
        _fail("empty map must not skip known-looking 7203")
    # empty map still infra-skips via company_name fallback
    got = download_skip_reason("9282", "○○インフラ投資法人", empty)
    if got != "infra-fund 9282 ○○インフラ投資法人":
        _fail(f"empty map infra fallback: {got!r}")
    got = download_skip_reason("8985", "ジャパン・ホテル・リート投資法人", empty)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"empty map 8985 excluded-code: {got!r}")
    if download_skip_reason("8952", "テストリート投資法人", empty) is not None:
        _fail("empty map must keep ordinary REIT 8952")

    # 3. code in stocks + infra fund → still infra skip
    got = download_skip_reason("9282", "ジャパン・インフラファンド投資法人", stock_map)
    if got != "infra-fund 9282 ジャパン・インフラファンド投資法人":
        _fail(f"9282 infra: {got!r}")
    got = download_skip_reason("92820", "ジャパン・インフラファンド投資法人", stock_map)
    if got != "infra-fund 9282 ジャパン・インフラファンド投資法人":
        _fail(f"92820 infra: {got!r}")

    # 4. 8985 is on MONTHLY_SKIP_CODES. Other ordinary REIT stays.
    got = download_skip_reason("8985", "ジャパン・ホテル・リート投資法人", stock_map)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"8985 excluded-code: {got!r}")
    got = download_skip_reason("89850", "ジャパン・ホテル・リート投資法人", stock_map)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"89850 excluded-code: {got!r}")
    if download_skip_reason("8952", "テストリート投資法人", stock_map) is not None:
        _fail("8952 ordinary REIT should keep")
    if download_skip_reason("89520", "テストリート投資法人", stock_map) is not None:
        _fail("89520 ordinary REIT should keep")
    if download_skip_reason("7203", "トヨタ自動車", stock_map) is not None:
        _fail("7203 should keep")
    if download_skip_reason("9279", "ギフト", stock_map) is not None:
        _fail("9279 should keep")


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


def test_dest_path_per_code_dir() -> None:
    dest = Path("/home/stock/r2-share/monthly")
    got = dest_path(
        dest,
        "2026-08-05 19:00:00",
        "92790",
        "ギフトHD",
        "2026年10月期7月度_直営店売上速報についてのお知らせ",
    )
    expect = (
        dest
        / "9279"
        / "20260805_9279_ギフトHD_2026年10月期7月度_直営店売上速報についてのお知らせ.pdf"
    )
    if got != expect:
        _fail(f"dest_path: {got} != {expect}")
    if got.parent.name != "9279":
        _fail(f"code dir: {got.parent.name!r}")
    if "92790" in str(got):
        _fail(f"must not pad 9279 to 5-digit: {got}")

    as_is = dest_path(dest, "2026-08-05", "9279", "ギフトHD", "月次")
    if as_is.parent.name != "9279" or as_is.name.split("_")[1] != "9279":
        _fail(f"4-char as-is: {as_is}")

    a = dest_path(dest, "2026-08-05", "262A", "テスト", "月次")
    if a.parent.name != "262A":
        _fail(f"262A dir: {a.parent.name!r}")
    if a.name.split("_")[1] != "262A":
        _fail(f"262A filename code: {a.name!r}")
    if "262A0" in str(a):
        _fail(f"must not pad 262A: {a}")

    b = dest_path(dest, "2026-08-05", "386A0", "テスト", "月次")
    if b.parent.name != "386A":
        _fail(f"386A dir: {b.parent.name!r}")
    if not b.name.startswith("20260805_386A_"):
        _fail(f"386A dest: {b.name!r}")
    if "386A0" in str(b):
        _fail(f"must not keep yanoshin 5-char padding: {b}")
    if b.parent == dest:
        _fail("dest must be nested under {code}, not flat monthly/")


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

    dest = tmp / "monthly"
    dest.mkdir()
    nested = dest_path(dest, "2026-08-05", "92790", "ギフトHD", "月次")
    if already_have(nested):
        _fail("missing nested should not skip")
    flat = dest / dest_filename("2026-08-05", "92790", "ギフトHD", "月次")
    flat.write_bytes(b"%PDF-1.4")
    if already_have(nested):
        _fail("old flat dest/name must not skip the nested path")
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"%PDF-1.4")
    if not already_have(nested):
        _fail("nested non-empty should skip")
    if nested != dest / "9279" / dest_filename("2026-08-05", "92790", "ギフトHD", "月次"):
        _fail(f"skip path is not nested: {nested}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_display_code()
    test_jq_lookup_code()
    test_should_skip_infra_fund()
    test_download_skip_reason()
    test_tdnet_pdf_id()
    test_dest_filename_gift()
    test_dest_path_per_code_dir()
    test_is_monthly_title()
    test_parse_list_body_tdnet_key()
    with TemporaryDirectory() as d:
        test_date_window_and_skip(Path(d))
        test_stock_product_map_sqlite(Path(d))
    print("ok: tdnet monthly r2")


if __name__ == "__main__":
    main()

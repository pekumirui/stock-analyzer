"""tdnet_monthly_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.tdnet_monthly_r2 import (  # noqa: E402
    INCLUDE_EXTRA,
    already_have,
    date_window,
    dest_filename,
    dest_path,
    display_code,
    download_skip_reason,
    is_monthly_title,
    jq_lookup_code,
    load_stock_product_map,
    has_list_keyword,
    should_skip_infra_fund,
    tdnet_pdf_id,
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


_CRON_DAYS_RE = re.compile(
    r"^[^#]*jobs/monthly/tdnet_monthly_r2\.py\s+(\d+)\b"
)


def test_crontab_lookback_covers_late_days() -> None:
    """日次 cron が当日1日だと、取りこぼした日を翌日以降拾えない（KAB-275）。"""
    crontab = (ROOT / "deploy" / "crontab").read_text(encoding="utf-8")
    days_vals: list[int] = []
    for line in crontab.splitlines():
        m = _CRON_DAYS_RE.search(line)
        if m:
            days_vals.append(int(m.group(1)))
    if not days_vals:
        _fail("deploy/crontab has no tdnet_monthly_r2.py days argument")
    if len(set(days_vals)) != 1:
        _fail(f"tdnet_monthly_r2.py days must be the same on every cron line: {days_vals}")
    days = days_vals[0]
    if days < 4:
        _fail(f"tdnet_monthly_r2.py cron days={days} must be >= 4 (KAB-275)")
    readme = (ROOT / "deploy" / "README.md").read_text(encoding="utf-8")
    needle = f"`jobs/monthly/tdnet_monthly_r2.py {days}`"
    if needle not in readme:
        _fail(f"deploy/README.md must document {needle}")


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
    if not should_skip_infra_fund("022", "外国インフラREIT", "foreign infra"):
        _fail("022 + インフラ should skip")
    if not should_skip_infra_fund("013", "何かリート", "インフラファンド投資法人"):
        _fail("013 + company_name インフラ should skip")
    if not should_skip_infra_fund("013", "インフラファンド投資法人", "何かリート"):
        _fail("013 + stock_name インフラ should skip")
    if should_skip_infra_fund(
        "013", "ジャパン・ホテル・リート投資法人", "ジャパン・ホテル・リート投資法人"
    ):
        _fail("8985 REIT should keep")
    if not should_skip_infra_fund(None, None, "○○インフラ投資法人"):
        _fail("master missing + インフラ投資法人 should skip")
    if not should_skip_infra_fund("", None, "インフラ投資法人"):
        _fail("unset category + インフラ投資法人 should skip")
    if should_skip_infra_fund(None, None, "ジャパン・ホテル・リート投資法人"):
        _fail("master missing + ホテル・リート should keep")
    if should_skip_infra_fund("011", "インフラ関連株式会社", "インフラ関連"):
        _fail("011 + インフラ should keep")
    if should_skip_infra_fund("011", "インフラ投資法人もどき", "インフラ投資法人"):
        _fail("011 + インフラ投資法人 should keep")
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

    before = db.read_bytes()
    m = load_stock_product_map(db_path=db)
    # 読み取り専用で開く。DB の中身を変えない（この DB は WAL でないので -wal / -shm もできない。WAL の DB は ro でも SQLite が作る）
    if db.read_bytes() != before or any(Path(f"{db}{s}").exists() for s in ("-wal", "-shm", "-journal")):
        _fail("load_stock_product_map must not write to the db")
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
    if (tmp / "absent.db").exists():
        _fail("missing db must not be created")
    broken = tmp / "broken.db"
    broken.write_bytes(b"not a sqlite file")
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = load_stock_product_map(db_path=broken)
    if got != {} or "warn stocks db unreadable" not in buf.getvalue():
        _fail(f"unreadable db should warn and return empty: {got!r} {buf.getvalue()!r}")
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
        "87980": ("011", "アドバンスクリエイト"),
        "87390": ("011", "スパークス・グループ"),
    }
    got = download_skip_reason("3032", "ゴルフ・ドゥ", stock_map)
    if got != "unknown-stock 3032 ゴルフ・ドゥ":
        _fail(f"3032 unknown: {got!r}")
    got = download_skip_reason("3066", "JBイレブン", stock_map)
    if got != "unknown-stock 3066 JBイレブン":
        _fail(f"3066 unknown: {got!r}")
    got = download_skip_reason("30320", "ゴルフ・ドゥ", stock_map)
    if got != "unknown-stock 3032 ゴルフ・ドゥ":
        _fail(f"30320 padded unknown: {got!r}")
    got = download_skip_reason("9999", "○○インフラ投資法人", stock_map)
    if got != "unknown-stock 9999 ○○インフラ投資法人":
        _fail(f"unknown infra-looking name: {got!r}")

    empty: dict[str, tuple[str | None, str | None]] = {}
    if download_skip_reason("3032", "ゴルフ・ドゥ", empty) is not None:
        _fail("empty map must not skip 3032")
    if download_skip_reason("3066", "JBイレブン", empty) is not None:
        _fail("empty map must not skip 3066")
    if download_skip_reason("7203", "トヨタ自動車", empty) is not None:
        _fail("empty map must not skip known-looking 7203")
    got = download_skip_reason("9282", "○○インフラ投資法人", empty)
    if got != "infra-fund 9282 ○○インフラ投資法人":
        _fail(f"empty map infra fallback: {got!r}")
    got = download_skip_reason("8985", "ジャパン・ホテル・リート投資法人", empty)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"empty map 8985 excluded-code: {got!r}")
    got = download_skip_reason("8798", "アドバンスクリエイト", empty)
    if got != "excluded-code 8798 アドバンスクリエイト":
        _fail(f"empty map 8798 excluded-code: {got!r}")
    got = download_skip_reason("8739", "スパークス・グループ", empty)
    if got != "excluded-code 8739 スパークス・グループ":
        _fail(f"empty map 8739 excluded-code: {got!r}")
    if download_skip_reason("8952", "テストリート投資法人", empty) is not None:
        _fail("empty map must keep ordinary REIT 8952")

    got = download_skip_reason("9282", "ジャパン・インフラファンド投資法人", stock_map)
    if got != "infra-fund 9282 ジャパン・インフラファンド投資法人":
        _fail(f"9282 infra: {got!r}")
    got = download_skip_reason("92820", "ジャパン・インフラファンド投資法人", stock_map)
    if got != "infra-fund 9282 ジャパン・インフラファンド投資法人":
        _fail(f"92820 infra: {got!r}")

    got = download_skip_reason("8985", "ジャパン・ホテル・リート投資法人", stock_map)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"8985 excluded-code: {got!r}")
    got = download_skip_reason("89850", "ジャパン・ホテル・リート投資法人", stock_map)
    if got != "excluded-code 8985 ジャパン・ホテル・リート投資法人":
        _fail(f"89850 excluded-code: {got!r}")
    got = download_skip_reason("8798", "アドバンスクリエイト", stock_map)
    if got != "excluded-code 8798 アドバンスクリエイト":
        _fail(f"8798 excluded-code: {got!r}")
    got = download_skip_reason("87980", "アドバンスクリエイト", stock_map)
    if got != "excluded-code 8798 アドバンスクリエイト":
        _fail(f"87980 excluded-code: {got!r}")
    got = download_skip_reason("8739", "スパークス・グループ", stock_map)
    if got != "excluded-code 8739 スパークス・グループ":
        _fail(f"8739 excluded-code: {got!r}")
    got = download_skip_reason("87390", "スパークス・グループ", stock_map)
    if got != "excluded-code 8739 スパークス・グループ":
        _fail(f"87390 excluded-code: {got!r}")
    if download_skip_reason("8952", "テストリート投資法人", stock_map) is not None:
        _fail("8952 ordinary REIT should keep")
    if download_skip_reason("89520", "テストリート投資法人", stock_map) is not None:
        _fail("89520 ordinary REIT should keep")
    if download_skip_reason("7203", "トヨタ自動車", stock_map) is not None:
        _fail("7203 should keep")
    if download_skip_reason("9279", "ギフト", stock_map) is not None:
        _fail("9279 should keep")


def test_tdnet_pdf_id() -> None:
    direct = "https://www.release.tdnet.info/inbs/140120260805509698.pdf"
    if tdnet_pdf_id(direct) != "140120260805509698":
        _fail(f"direct inbs url: {tdnet_pdf_id(direct)!r}")
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
        _fail(f"must not keep the 5-char code padding: {b}")
    if b.parent == dest:
        _fail("dest must be nested under {code}, not flat monthly/")


def test_is_monthly_title() -> None:
    if "月次IRニュース" not in INCLUDE_EXTRA:
        _fail("INCLUDE_EXTRA must list 月次IRニュース")
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
        "2026年８月度 受注速報",
        "2026年7月度 受注速報",
        "2027年4月期8月度の売上高についてのお知らせ",
        "2026年８月期８月度 直営店売上高速報、店舗数推移について",
        "2027年1月期8月度販売状況のお知らせ",
        "2026年９月期売上高推移報告について",
        "2026年８月度の売上高のお知らせ",
        "2027年2月期　8月度概況について　[国内]",
        "(第55期）　2026年８月の売上推移速報に関するお知らせ",
        "２０２６年７月度　Ｊ．フロントリテイリング　連結売上収益報告（ＩＦＲＳ）",
        "2026年8月度　すかいらーくグループIRレポート",
        "2026年8月度売上高速報（前期対比）",
        "2027年1月期8月度売上高速報及び概況等について",
        "2027年２月期 ７月度の売上概況",
        "2026年10月期　速報数値　7月度",
        "第47期（2027年３月期）８月の売上の状況に関するお知らせ（速報）",
        "2026年8月の業績速報",
        "2027年3月期8月度売上高推移速報についてのお知らせ（個別）",
        "2027年3月期8月度売上高に関するお知らせ(単体)",
        "2027年３月期８月度売上推移速報に関するお知らせ",
        "2027年２月期８月度売上高速報についてのお知らせ",
        "2026年8月度 売上高の状況（2026年7月1日～2026年8月31日）",
        "2027年１月期 ８月度売上高前年対比等のお知らせ",
        "2026年7月度 高島屋営業報告",
        "売上高前年同月比推移に関するお知らせ",
        "2026年7月 月次業績報告",
        "2027年３月期７月度月次売上（速報）に関するお知らせ",
        "月次IRニュース（2024年10月）",
        "2026年8月度 月次IRニュース",
        "2024年10月度 IRニュース",
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
        "新株予約権の月間行使状況に関するお知らせ",
        "2026年8月度 新株予約権発行のお知らせ",
        "2026年8月度 取締役選任のお知らせ",
        "四半期短信〔日本基準〕（連結）",
        "2026年8月度 業績予想の修正に関するお知らせ",
        "IRニュースのお知らせ",
    ]
    for t in exclude:
        if is_monthly_title(t):
            _fail(f"should exclude: {t!r}")


def test_has_list_keyword() -> None:
    for t in ("8月度 月次売上高", "売上速報", "MONTHLY REPORT 2026", "店舗売上のお知らせ"):
        if not has_list_keyword(t):
            _fail(f"keyword: {t!r}")
    for t in ("受注速報", "決算短信", ""):
        if has_list_keyword(t):
            _fail(f"no keyword: {t!r}")


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


def test_http_get_uses_job_ua_then_browser() -> None:
    from urllib.error import HTTPError
    from unittest.mock import MagicMock, patch

    import jobs.monthly.tdnet_monthly_r2 as mod
    from jobs._r2common import BROWSER_UA

    uas: list[str] = []

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        ua = req.get_header("User-agent") or req.get_header("User-Agent")
        uas.append(ua)
        if ua == mod.USER_AGENT:
            raise HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"ok"
        cm.__exit__.return_value = False
        return cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        body = mod.http_get("https://www.release.tdnet.info/inbs/I_list_001_20260805.html")
    if body != b"ok":
        _fail(f"body {body!r}")
    if uas != [mod.USER_AGENT, BROWSER_UA]:
        _fail(f"uas {uas}")
    if mod.USER_AGENT == BROWSER_UA:
        _fail("job UA must stay distinct from the browser fallback")


def test_resolve_database_url(tmp: Path) -> None:
    from jobs._r2common import resolve_database_url, sqlite_path_from_url

    root = tmp / "repo"
    root.mkdir()
    (root / ".env").write_text(
        '# c\nexport DATABASE_URL="sqlite:///data/from_env_file.db"\nJQUANTS_API_KEY=x\n',
        encoding="utf-8",
    )
    got = resolve_database_url(environ={"DATABASE_URL": "sqlite:////abs/env.db"}, repo_root=root)
    if got != "sqlite:////abs/env.db":
        _fail(f"env must win over .env: {got}")
    got = resolve_database_url(environ={}, repo_root=root)
    if got != "sqlite:///data/from_env_file.db":
        _fail(f".env DATABASE_URL: {got}")
    got = resolve_database_url(environ={}, repo_root=tmp / "no-env")
    if got != f"sqlite:///{tmp / 'no-env' / 'stock.db'}":
        _fail(f"fallback stock.db: {got}")

    cases = {
        "sqlite:///data/x.db?mode=ro": root / "data" / "x.db",
        f"sqlite:///{tmp / 'abs.db'}": tmp / "abs.db",
        "sqlite:///:memory:": None,
        "postgresql://u@h/db": None,
    }
    for url, want in cases.items():
        got_path = sqlite_path_from_url(url, repo_root=root)
        if got_path != want:
            _fail(f"sqlite_path_from_url({url!r}) → {got_path} != {want}")
    if load_stock_product_map(environ={"DATABASE_URL": "postgresql://u@h/db"}, repo_root=root) != {}:
        _fail("non-sqlite DATABASE_URL must give an empty map")


def test_run_filters_dedups_and_saves(tmp: Path) -> None:
    import io
    from contextlib import redirect_stdout
    from datetime import date
    from unittest.mock import patch

    import jobs.monthly.tdnet_monthly_r2 as mod

    def rec(code: str, name: str, title: str, pdf: str) -> dict:
        return {
            "pubdate": "2026-08-05 15:00:00", "company_code": code,
            "company_name": name, "title": title,
            "document_url": f"https://www.release.tdnet.info/inbs/{pdf}.pdf" if pdf else "",
            "url_xbrl": "",
        }

    gift = rec("92790", "ギフトHD", "8月度 月次売上高のお知らせ", "140120260805000001")
    kichiri = rec("30820", "きちり", "8月度 月次報告", "140120260805000002")
    tanshin = rec("72030", "トヨタ", "決算短信", "140120260805000003")
    no_id = rec("72030", "トヨタ", "8月度 月次売上高", "")
    unknown = rec("99990", "未上場", "8月度 月次売上高", "140120260805000005")
    broken = rec("26640", "カワチ薬品", "8月度 月次営業概況", "140120260805000006")
    # 月次らしいが LIST_KEYWORDS に無い表題は取らない
    juchu = rec("64660", "x", "受注速報", "140120260805000007")
    listed = [gift, tanshin, no_id, kichiri, gift, unknown, broken, juchu]
    days_calls: list[tuple] = []
    pdf_gets: list[str] = []

    def fake_days(start, end, get, sleep_sec):
        days_calls.append((start, end))
        return listed

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        pdf_id = url.rsplit("/", 1)[1].removesuffix(".pdf")
        pdf_gets.append(pdf_id)
        return b"<html>" if pdf_id.endswith("6") else b"%PDF-1.4 " + pdf_id.encode()

    stocks = {c: ("011", n) for c, n in (("92790", "ギフトHD"), ("30820", "きちり"),
                                         ("72030", "トヨタ"), ("26640", "カワチ薬品"),
                                         ("64660", "x"))}
    dest = tmp / "run-monthly"
    buf = io.StringIO()
    with patch.object(mod, "http_get", fake_get), \
            patch.object(mod.tdnet_list, "fetch_days", fake_days), \
            patch.object(mod, "load_stock_product_map", lambda: stocks), \
            patch.object(mod, "date_window", lambda days: (date(2026, 8, 5), date(2026, 8, 5))), \
            patch.object(mod.time, "sleep", lambda s: None), \
            redirect_stdout(buf):
        rc = mod.run(1, dest)
    out = buf.getvalue()
    if rc != 1:
        _fail(f"broken PDF must fail the run rc={rc}\n{out}")
    if days_calls != [(date(2026, 8, 5), date(2026, 8, 5))]:
        _fail(f"fetch_days {days_calls}")
    if sorted(pdf_gets) != ["140120260805000001", "140120260805000002", "140120260805000006"]:
        _fail(f"downloads {pdf_gets}\n{out}")
    for r, pdf in ((gift, "140120260805000001"), (kichiri, "140120260805000002")):
        path = dest_path(dest, r["pubdate"], r["company_code"], r["company_name"], r["title"])
        if not path.is_file() or path.read_bytes() != b"%PDF-1.4 " + pdf.encode():
            _fail(f"missing saved PDF {path}\n{out}")
    if list(dest.rglob("*.part")) or list((dest / "2664").glob("*.pdf")):
        _fail(f"non-PDF body must not be saved: {list(dest.rglob('*'))}")
    if "skip no-pdf-id 72030" not in out:
        _fail(out)
    if "skip unknown-stock 9999" not in out or "listed=4 saved=2 skipped=1 failed=1" not in out:
        _fail(out)


def main() -> None:
    from tempfile import TemporaryDirectory

    test_crontab_lookback_covers_late_days()
    test_display_code()
    test_jq_lookup_code()
    test_should_skip_infra_fund()
    test_download_skip_reason()
    test_tdnet_pdf_id()
    test_dest_filename_gift()
    test_dest_path_per_code_dir()
    test_is_monthly_title()
    test_has_list_keyword()
    test_http_get_uses_job_ua_then_browser()
    with TemporaryDirectory() as d:
        test_date_window_and_skip(Path(d))
        test_stock_product_map_sqlite(Path(d))
        test_resolve_database_url(Path(d))
        test_run_filters_dedups_and_saves(Path(d))
    print("ok: tdnet monthly r2")


if __name__ == "__main__":
    main()

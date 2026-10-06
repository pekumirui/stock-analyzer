"""tools/keeper_monthly.py。R2 の 6036 月次速報 PDF を pdftotext -layout したテキストで、書式ごとに読む。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import keeper_monthly as km  # noqa: E402

DATA = ROOT / "tests" / "testdata" / "keeper_6036"
L = km.LABO


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _file(prefix: str) -> Path:
    return next(DATA.glob(f"{prefix}_*.txt"))


def _values(prefix: str) -> tuple[dict, dict[str, float]]:
    args = km.ingest_args(_file(prefix))
    return args, {r["element"]: float(next(iter(r["values"].values()))) for r in args["rows"]}


def test_report_month() -> None:
    got = km.report_month("20261005_6036_KeePer技研_キーパーラボ運営事業とキーパー製品等関連事業の月次速報(9月).pdf")
    if got != ("2026-10-05", 2026, 9):
        _fail(f"report_month: {got!r}")
    got = km.report_month("20240104_6036_ＫｅｅＰｅｒ技研_…の月次速報（12月度）.pdf")
    if got != ("2024-01-04", 2023, 12):
        _fail(f"report_month year wrap: {got!r}")


def test_current_format() -> None:
    # 2026年9月: 本表 + 月次動向推移（直営 / FC 新店と FC 込みの店舗合計）
    args, v = _values("20261005")
    if args["code"] != "60360" or args["as_of"] != "2026-10-05" or args["months"] != [9]:
        _fail(f"args head: {args['code']} {args['as_of']} {args['months']}")
    want = {
        "全社 売上高": 2057112788,
        "全社 売上高(前年比)": 112.6,
        f"{L} 売上高": 1028239755,
        f"{L} 直営 全店 売上高": 982437513,
        f"{L} 直営 全店 店舗数": 150,
        f"{L} 直営 既存 売上高(前年比)": 97.8,
        f"{L} 直営 既存 店舗数": 136,
        f"{L} 直営 既存 客数(前年比)": 83.2,
        f"{L} 直営 既存 客単価(前年比)": 117.7,
        f"{L} FC 売上高": 45802242,
        f"{L} 直営+FC 店舗数": 189,
        f"{L} FC 店舗数": 39,
        "キーパー製品等関連事業 売上高": 1028873033,
        "新車マーケット 売上高(前年比)": 153.8,
        "海外 売上高": 0,
    }
    bad = {k: (v.get(k), w) for k, w in want.items() if v.get(k) != w}
    if bad:
        _fail(f"2026-09: {bad!r}")
    # 海外 0 円・前年比 0.0% は前年ゼロかもしれず入れない
    if "海外 売上高(前年比)" in v:
        _fail("2026-09: 海外 前年比 should be skipped")
    if km.check_sums(v):
        _fail(f"2026-09 sums: {km.check_sums(v)}")
    units = {r["element"]: r["unit"] for r in args["rows"]}
    if units["全社 売上高"] != "number" or units["全社 売上高(前年比)"] != "yoy_index":
        _fail(f"units: {units!r}")


def test_fc_first_format() -> None:
    # 2024年8月: FC関連売上 を外数で開示し始めた書式。推移表に FC 列が無いので店舗数は出さない
    _, v = _values("20240904")
    want = {
        f"{L} 売上高": 979592588,
        f"{L} FC 売上高": 11198270,
        f"{L} 直営 全店 売上高": 968394318,
        f"{L} 直営 既存 客数(前年比)": 97.5,
        "その他コーティング 売上高": 8588978,
    }
    bad = {k: (v.get(k), w) for k, w in want.items() if v.get(k) != w}
    if bad:
        _fail(f"2024-08: {bad!r}")
    if f"{L} 直営+FC 店舗数" in v or f"{L} FC 店舗数" in v:
        _fail("2024-08: 旧書式では店舗数を出さない")
    if km.check_sums(v):
        _fail(f"2024-08 sums: {km.check_sums(v)}")


def test_old_format() -> None:
    # 2023年11月: 「ラボ 全109店合計」「キーパー製品等関連事業合計」。前年比は次の行。FC 行もマーケット別も無い
    _, v = _values("20231201")
    want = {
        "全社 売上高": 1757524288,
        "全社 売上高(前年比)": 118.8,
        f"{L} 直営 全店 売上高": 872717914,
        f"{L} 直営 全店 売上高(前年比)": 123.8,
        f"{L} 直営 全店 店舗数": 109,
        f"{L} 直営 既存 店舗数": 96,
        f"{L} 直営 既存 客数(前年比)": 106,
        "キーパー製品等関連事業 売上高(前年比)": 115.5,
    }
    bad = {k: (v.get(k), w) for k, w in want.items() if v.get(k) != w}
    if bad:
        _fail(f"2023-11: {bad!r}")
    if any(k.startswith(("アフターマーケット", f"{L} FC")) for k in v):
        _fail(f"2023-11 extra rows: {sorted(v)!r}")


def test_split_numbers() -> None:
    text = "キーパー製品等関連事業       960,142,240     108 .8%\n全社合計  1,000   98 .7 %\n"
    v = km.parse_text(text, 2025, 7)
    if v.get("キーパー製品等関連事業 売上高(前年比)") != 108.8 or v.get("全社 売上高(前年比)") != 98.7:
        _fail(f"split numbers: {v!r}")


def test_check_sums_detects_misread() -> None:
    v = {"全社 売上高": 100.0, f"{L} 売上高": 60.0, "キーパー製品等関連事業 売上高": 30.0}
    if not km.check_sums(v):
        _fail("check_sums should flag 60 + 30 != 100")


def main() -> None:
    test_report_month()
    test_current_format()
    test_fc_first_format()
    test_old_format()
    test_split_numbers()
    test_check_sums_detects_misread()
    print("ok: keeper monthly")


if __name__ == "__main__":
    main()

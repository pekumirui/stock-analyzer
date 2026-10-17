"""9946 ミニストップ: 全店売上の上期+下期を同じ行に足す (KAB-5 / #621)。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.html_issuers.i9946 import parse as parse_9946  # noqa: E402
from jobs.monthly.html_monthly import decode_html_bytes  # noqa: E402

from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label  # noqa: E402

FIXTURE_20261013 = ROOT / "tests" / "monthly" / "testdata" / "html_9946" / "20261013.html"

# 1店1日当り（既存・全店）にも下期表がある。全店売上の下期だけ足す。
MINISTOP_H1H2_HTML = """
<html><body>
<h1>月次営業報告 2026年</h1>
<p>2026年9月30日 現在</p>
<h2>既存店1店1日当り</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>平均売上高前年比（％）</td><td>95.9</td><td>92.2</td><td>96.8</td><td>91.6</td><td>95.5</td><td>96.4</td><td>94.8</td></tr>
</table>
<table>
<tr><th></th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>通期累計</th></tr>
<tr><td>平均売上高前年比（％）</td><td>100.2</td><td></td><td></td><td></td><td></td><td></td><td>95.5</td></tr>
</table>
<h2>全店1店1日当り</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>平均売上高前年比（％）</td><td>97.1</td><td>93.9</td><td>98.0</td><td>93.1</td><td>97.3</td><td>98.1</td><td>96.5</td></tr>
</table>
<table>
<tr><th></th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>通期累計</th></tr>
<tr><td>平均売上高前年比（％）</td><td>101.9</td><td></td><td></td><td></td><td></td><td></td><td>97.1</td></tr>
</table>
<h2>全店売上</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>売上高合計前年比（％）</td><td>93.5</td><td>89.6</td><td>93.7</td><td>89.2</td><td>92.4</td><td>92.9</td><td>91.0</td></tr>
<tr><td>CVS前年比（％）</td><td>97.8</td><td>92.9</td><td>95.8</td><td>93.9</td><td>95.6</td><td>93.3</td><td>94.9</td></tr>
<tr><td>店内加工FF前年比（％）</td><td>73.8</td><td>74.8</td><td>84.0</td><td>68.2</td><td>78.6</td><td>90.8</td><td>78.3</td></tr>
</table>
<table>
<tr><th></th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>通期累計</th></tr>
<tr><td>売上高合計前年比（％）</td><td>96.9</td><td></td><td></td><td></td><td></td><td></td><td>92.5</td></tr>
<tr><td>CVS前年比（％）</td><td>95.8</td><td></td><td></td><td></td><td></td><td></td><td>95.0</td></tr>
<tr><td>店内加工FF前年比（％）</td><td>104.2</td><td></td><td></td><td></td><td></td><td></td><td>80.9</td></tr>
</table>
</body></html>
"""


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _stored_name(element: str, unit: str, as_of: date) -> str:
    name = apply_issuer_aliases(
        "99460",
        canonicalize_element_label(element, unit),
        as_of=as_of,
    )
    return canonicalize_element_label(name, unit)


def _assert_all_store_total(
    got: dict,
    *,
    as_of: str,
    filled_months: list[int],
    september: float,
) -> None:
    if got["as_of"] != as_of:
        _fail(f"9946 as_of {got['as_of']!r}, want {as_of}")
    if got["months"] != filled_months:
        _fail(f"9946 months {got['months']}, want {filled_months}")
    if len(got["rows"]) != 1:
        _fail(f"9946 rows {got['rows']}")
    sales = got["rows"][0]
    if sales["element"] != "全店 売上高合計前年比（％）":
        _fail(f"9946 element {sales}")
    if sales["unit"] != "yoy_index":
        _fail(f"9946 unit {sales}")
    values = sales["values"]
    want = {"3": 93.5, "4": 89.6, "5": 93.7, "6": 89.2, "7": 92.4, "8": 92.9, "9": september}
    if values != want:
        _fail(f"9946 values {values}, want {want}")
    if values.get("9") in (100.2, 101.9, 95.9):
        _fail(f"picked 1店1日当り {sales}")
    labels = [r["element"] for r in got["rows"]]
    if any("CVS" in el or "FF" in el or "平均" in el or "客数" in el for el in labels):
        _fail(f"breakdown leaked {labels}")
    stored = _stored_name(sales["element"], sales["unit"], date.fromisoformat(as_of))
    if stored != "全店 売上高(前年比)":
        _fail(f"9946 canonical {stored}")


def test_9946_h1h2_synthetic_skips_per_store() -> None:
    got = parse_9946(MINISTOP_H1H2_HTML)
    _assert_all_store_total(
        got,
        as_of="2026-09-30",
        filled_months=[3, 4, 5, 6, 7, 8, 9],
        september=96.9,
    )


def test_9946_20261013_saved_html_fills_september() -> None:
    """2026-10-13 保存 HTML: 値のある月は [3..9]、9月=96.9。"""
    if not FIXTURE_20261013.is_file():
        _fail(f"missing fixture {FIXTURE_20261013}")
    html = decode_html_bytes(FIXTURE_20261013.read_bytes())
    got = parse_9946(html)
    _assert_all_store_total(
        got,
        as_of="2026-09-30",
        filled_months=[3, 4, 5, 6, 7, 8, 9],
        september=96.9,
    )


def main() -> None:
    test_9946_h1h2_synthetic_skips_per_store()
    test_9946_20261013_saved_html_fills_september()
    print("ok: html 9946 h1+h2")


if __name__ == "__main__":
    main()

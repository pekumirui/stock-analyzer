"""ir_monthly_html_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))

from html_monthly import (  # noqa: E402
    _sticky_section_fy,
    decode_html_bytes,
    dumps_ingest,
    parse_as_of_text,
    parse_grid,
    parse_month_cell,
    parse_month_year,
    parse_number,
    parse_tables,
)
from html_issuers import parse_issuer  # noqa: E402
from html_issuers.i3148 import parse as parse_3148  # noqa: E402
from html_issuers.i7522 import parse as parse_7522  # noqa: E402
from html_issuers.i7561 import parse as parse_7561  # noqa: E402
from html_issuers.i8179 import parse as parse_8179  # noqa: E402
from html_issuers.i8278 import parse as parse_8278  # noqa: E402
from html_issuers.i9946 import parse as parse_9946  # noqa: E402
from html_issuers.i9948 import parse as parse_9948  # noqa: E402
from html_issuers.i9993 import parse as parse_9993  # noqa: E402
from html_issuers.i9994 import parse as parse_9994  # noqa: E402
from ir_monthly_html_r2 import (  # noqa: E402
    HtmlIssuer,
    eir_url,
    existing_html_hashes,
    fetch_issuer,
    ingest_path,
    is_rate_limited,
    load_html_registry,
    parse_eir_jsonp,
    pick_eir_monthly,
    pick_follow_url,
    reparse_issuer,
    run,
    write_ingest,
)

from ir_monthly_r2 import dest_filename, dest_path, looks_like_file  # noqa: E402

CAWACHI_HTML = """
<html><body>
<h2>売上高前年同月比</h2>
<h3>【連結】2027年3月期（2026年3月16日～2027年3月15日）</h3>
<table>
<thead><tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月 </th>
<th>9月 </th><th>10月 </th><th>11月  </th><th>12月 </th><th>1月</th><th>2月</th><th>3月</th>
<th>累計 </th></tr></thead>
<tbody>
<tr><td>全店<br></td><td>1.9</td><td>2.0</td><td>0.3</td><td>△4.3</td><td>△1.9</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>△0.5</td></tr>
<tr><td>既存店</td><td>1.3</td><td>1.8</td><td>0.4</td><td>△3.8</td><td>△1.5</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td><td>△0.4</td></tr>
</tbody>
<tfoot><tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td><td>(%)</td></tr></tfoot>
</table>
</body></html>
"""

HALOWS_HTML = """
<html><body>
<h1>月次情報</h1>
<table>
<tr>
<td colspan="2"></td>
<td>売上高</td>
<td>売上高前年同月比</td>
<td>客数前年同月比</td>
<td>客単価前年同月比</td>
</tr>
<tr>
<td rowspan="2">2026/07</td>
<td>全店</td>
<td>20,038 百万円</td>
<td>106.1%</td>
<td>104.8%</td>
<td>101.3%</td>
</tr>
<tr>
<td>既存店</td>
<td>18,791 百万円</td>
<td>101.4%</td>
<td>100.0%</td>
<td>101.4%</td>
</tr>
<tr>
<td rowspan="2">2026/03</td>
<td>全店</td>
<td>19,099 百万円</td>
<td>106.1%</td>
<td>105.7%</td>
<td>100.4%</td>
</tr>
<tr>
<td>既存店</td>
<td>18,192 百万円</td>
<td>101.1%</td>
<td>100.7%</td>
<td>100.4%</td>
</tr>
</table>
</body></html>
"""

OKUWA_HTML = """
<html><body>
<h2>月次売上前年同月比</h2>
<table>
  <tr><th colspan="4">2027年2月期</th></tr>
  <tr>
    <th>年月</th>
    <th>既存店</th>
    <th>全店</th>
    <th>店舗数</th>
  </tr>
  <tr><th>2026年8月度</th><td>99.6％</td><td>99.3％</td><td>151</td></tr>
  <tr><th>2026年7月度</th><td>96.2％</td><td>95.9％</td><td>151</td></tr>
  <tr><th>2026年3月度</th><td>99.1％</td><td>99.4％</td><td>152</td></tr>
</table>
</body></html>
"""

COLOWIDE_HTML = """
<p>（2026年9月4日更新）</p>
<div class="tableWrapperTypeScroll irtable_sale">
<table class="tableType02"><tbody>
<tr><th rowspan="2">2026年度</th><th colspan="3">既存店舗（全業態）</th><th colspan="3">全店舗（全業態）</th></tr>
<tr><th>売上高</th><th>客数</th><th>客単価</th><th>売上高</th><th>客数</th><th>客単価</th></tr>
<tr><th>4月</th><td>101.8％</td><td>100.5％</td><td>101.3％</td><td>120.0％</td><td>150.9％</td><td>79.6％</td></tr>
<tr><th>8月</th><td>99.5％</td><td>99.5％</td><td>99.9％</td><td>115.2％</td><td>142.4％</td><td>80.9％</td></tr>
<tr><th>9月</th><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td></tr>
<tr><th>3月</th><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td><td><br/></td></tr>
<tr><th>通期計</th><td>102.0％</td><td>100.9％</td><td>101.1％</td><td>120.1％</td><td>150.0％</td><td>80.0％</td></tr>
</tbody></table></div>
"""

EIR_JSONP = (
    "eolparts_announcement_0("
    + json.dumps(
        {
            "item_count": 2,
            "item": [
                {
                    "date": "2026/04/06 15:55:00",
                    "title": "月次売上推移速報",
                    "text": "<table><tr><th>4月</th><td>1</td></tr></table>",
                },
                {
                    "date": "2026/09/04 09:00:00",
                    "title": "月次売上推移速報",
                    "text": COLOWIDE_HTML,
                },
                {
                    "date": "2026/09/01 00:00:00",
                    "title": "決算短信",
                    "text": "<p>no</p>",
                },
            ],
        },
        ensure_ascii=False,
    )
    + ");"
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _row(payload: dict, element: str, *, unit: str | None = None) -> dict:
    hits = [
        r
        for r in payload["rows"]
        if r["element"] == element and (unit is None or r["unit"] == unit)
    ]
    if len(hits) != 1:
        _fail(f"element {element!r} unit={unit}: {payload['rows']}")
    return hits[0]


def test_parse_number_and_month() -> None:
    if parse_number("△4.3") != -4.3:
        _fail("triangle")
    if parse_number("▲1.9") != -1.9:
        _fail("filled triangle")
    if parse_number("106.1%") != 106.1:
        _fail("pct")
    if parse_number("99.6％") != 99.6:
        _fail("fullwidth pct")
    if parse_number("20,038 百万円") != 20038:
        _fail("million yen")
    if parse_number("125.8") != 125.8:
        _fail("index stays")
    if parse_number("") is not None or parse_number("―") is not None:
        _fail("empty")
    if parse_month_cell("4月") != 4:
        _fail("4月")
    if parse_month_cell("2026年8月度") != 8:
        _fail("8月度")
    if parse_month_cell("2026/07") != 7:
        _fail("2026/07")
    if parse_month_cell("累計") is not None or parse_month_cell("通期計") is not None:
        _fail("skip axis")
    if parse_as_of_text("（2026年9月4日更新）") != "2026-09-04":
        _fail("日更新")
    if parse_as_of_text("2026/09/11 公表") != "2026-09-11":
        _fail("slash 公表")
    if parse_as_of_text("2026年8月31日 現在") != "2026-08-31":
        _fail("日現在")
    if parse_as_of_text("<h2>2026年8月</h2>") != "2026-08-31":
        _fail("heading YYYY年M月")
    if parse_as_of_text(CAWACHI_HTML) is not None:
        _fail("cawachi range must not be as_of")
    if parse_as_of_text("<p>※2026年4月度より、算定対象を見直し</p>") is not None:
        _fail("footnote 月度より is not as_of")
    if parse_month_year("2026年度 ３月期") != (None, None):
        _fail("年度+月期 is not a month axis")
    if parse_month_year("2026年3月期") != (None, None):
        _fail("月期 caption is not a month axis")
    if parse_month_year("2026年03月") != (2026, 3):
        _fail("YYYY年MM月 stays a month")


def test_cawachi_col_months() -> None:
    got = parse_issuer(CAWACHI_HTML, "2664")
    if got["code"] != "2664":
        _fail(f"code {got['code']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"months {got['months']}")
    if got["as_of"] != "2026-08-31":
        _fail(f"as_of from 2027年3月期 + 8月 {got['as_of']}")
    all_store = _row(got, "全店 売上高前年同月比")
    exist = _row(got, "既存店 売上高前年同月比")
    if all_store["unit"] != "yoy_pct" or exist["unit"] != "yoy_pct":
        _fail(f"unit {all_store} {exist}")
    if all_store["values"] != {"4": 1.9, "5": 2, "6": 0.3, "7": -4.3, "8": -1.9}:
        _fail(f"all values {all_store['values']}")
    if exist["values"] != {"4": 1.3, "5": 1.8, "6": 0.4, "7": -3.8, "8": -1.5}:
        _fail(f"exist values {exist['values']}")
    if any("累計" in r["element"] for r in got["rows"]):
        _fail("累計 row leaked")
    for r in got["rows"]:
        if "13" in r["values"] or "累計" in "".join(r["values"]):
            _fail(f"累計 col leaked {r}")


def test_halows_rowspan() -> None:
    got = parse_issuer(HALOWS_HTML, "2742")
    if got["as_of"] != "2026-07-31":
        _fail(f"as_of from 2026/07 {got['as_of']}")
    if got["months"] != [3, 7]:
        _fail(f"months should be fiscal order {got['months']}")
    sales = _row(got, "全店 売上高", unit="number")
    if sales["unit"] != "number" or sales["values"].get("7") != 20038:
        _fail(f"sales {sales}")
    yoy = _row(got, "全店 売上高前年同月比", unit="yoy_index")
    if yoy["values"].get("7") != 106.1:
        _fail(f"yoy {yoy}")
    exist_yoy = _row(got, "既存店 売上高前年同月比", unit="yoy_index")
    if exist_yoy["values"].get("3") != 101.1:
        _fail(f"exist yoy {exist_yoy}")
    guest = _row(got, "全店 客数前年同月比", unit="yoy_index")
    if guest["values"].get("7") != 104.8:
        _fail(f"guest {guest}")


def test_okuwa_reverse_months_and_store_count() -> None:
    got = parse_issuer(OKUWA_HTML, "8217")
    if got["as_of"] != "2026-08-31":
        _fail(f"as_of from 2026年8月度 {got['as_of']}")
    if got["months"] != [3, 7, 8]:
        _fail(f"months {got['months']}")
    exist = _row(got, "既存店")
    if exist["unit"] != "yoy_index" or exist["values"] != {"8": 99.6, "7": 96.2, "3": 99.1}:
        _fail(f"exist {exist}")
    stores = _row(got, "店舗数")
    if stores["unit"] != "number" or stores["values"].get("8") != 151:
        _fail(f"stores {stores}")


def test_colowide_grouped_header_skips_total() -> None:
    got = parse_issuer(COLOWIDE_HTML, "7616")
    if got["as_of"] != "2026-09-04":
        _fail(f"update date {got['as_of']}")
    if got["months"] != [4, 8]:
        _fail(f"months keep fiscal holes {got['months']}")
    exist_sales = _row(got, "既存店舗（全業態） 売上高")
    if exist_sales["unit"] != "yoy_index":
        _fail(f"unit {exist_sales}")
    if exist_sales["values"] != {"4": 101.8, "8": 99.5}:
        _fail(f"通期計 leaked or empty kept {exist_sales['values']}")
    all_guest = _row(got, "全店舗（全業態） 客数")
    if all_guest["values"].get("8") != 142.4:
        _fail(f"all guest {all_guest}")


def test_eir_jsonp_picks_latest_monthly() -> None:
    payload = parse_eir_jsonp("\ufeff" + EIR_JSONP)
    html, as_of = pick_eir_monthly(payload, "月次")
    if as_of != "2026-09-04":
        _fail(f"as_of {as_of}")
    if "99.5" not in html:
        _fail("not latest html")
    if "決算" in html:
        _fail("earnings leaked")
    if eir_url("76160", 0) != "https://ssl4.eir-parts.net/V4Public/eir/7616/ja/announcement/announcement_0.js":
        _fail(eir_url("76160", 0))


def test_decode_shift_jis() -> None:
    raw = (
        b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS" />'
        b"</head><body>"
        + "月次".encode("cp932")
        + b"</body></html>"
    )
    text = decode_html_bytes(raw)
    if "月次" not in text:
        _fail(f"sjis decode {text!r}")
    tables = parse_tables(CAWACHI_HTML)
    if len(tables) != 1 or tables[0][1][0] != "全店":
        _fail(f"grid {tables[0][:3] if tables else None}")


def test_dest_html_and_ingest_sidecar() -> None:
    name = dest_filename("2026-09-14", "2664", "カワチ薬品", "月次", "html")
    if name != "20260914_2664_カワチ薬品_月次.html":
        _fail(name)
    dest = Path("/home/stock/r2-share/monthly")
    p = dest_path(dest, "2026-09-14", "2664", "カワチ薬品", "月次", "html")
    if p != dest / "2664" / name:
        _fail(str(p))
    if ingest_path(p).name != "20260914_2664_カワチ薬品_月次.ingest.json":
        _fail(ingest_path(p).name)
    if not looks_like_file(b"<!doctype html><p>x</p>", "html"):
        _fail("html magic")
    if looks_like_file(b"%PDF-1.4", "html"):
        _fail("pdf is not html")


def _module_force_units() -> dict[str, str]:
    """i<code>.py の単位宣言。Page.unit、または force_unit / _apply_force_unit。"""
    import importlib
    import inspect
    import pkgutil
    import re

    import html_issuers

    decl = re.compile(
        r"""(?:force_unit\s*=\s*|_apply_force_unit\(\s*\w+\s*,\s*)"""
        r"""["'](number|yoy_index|yoy_pct)["']"""
    )
    out: dict[str, str] = {}
    for info in pkgutil.iter_modules(html_issuers.__path__):
        if not info.name.startswith("i"):
            continue
        mod = importlib.import_module(f"html_issuers.{info.name}")
        page = getattr(mod, "PAGE", None)
        if page is not None:
            if page.unit:
                out[str(page.code)] = str(page.unit)
            continue
        found = decl.findall(inspect.getsource(mod))
        if not found:
            continue
        if len(set(found)) != 1:
            _fail(f"{info.name} mixed units {found}")
        out[info.name[1:]] = found[0]
    return out


def test_load_html_registry() -> None:
    raw_items = json.loads((ROOT / "config" / "monthly" / "ir_monthly.json").read_text(encoding="utf-8"))["html"]
    leaked = [
        str(item.get("code"))
        for item in raw_items
        if "table" in item or "force_unit" in item
    ]
    if leaked:
        _fail(f"json still has table/force_unit {leaked}")
    live = load_html_registry(ROOT / "config" / "monthly" / "ir_monthly.json")
    codes = [i.code for i in live]
    if len(codes) != 55:
        _fail(f"repo html issuers count {len(codes)}: {codes}")
    if "1878" in codes:
        _fail("1878 moved to issuers (xls), must not stay in html")
    if codes != sorted(codes):
        _fail(f"html issuers must be code-sorted: {codes}")
    colo = next(i for i in live if i.code == "7616")
    if colo.source != "eir" or colo.eir_n != 0:
        _fail(f"colo {colo}")
    mos = next(i for i in live if i.code == "8153")
    if mos.source != "eir" or mos.eir_n != 24:
        _fail(f"mos {mos}")
    no_parse = {i.code for i in live if not i.parse}
    if no_parse != {"2792", "3080", "3196", "7544"}:
        _fail(f"parse=false {no_parse}")
    follows = {i.code: i.follow_re for i in live if i.follow_re}
    if "7522" not in follows or "1928" not in follows or "2685" not in follows or "8153" in follows:
        _fail(f"follow_re {follows}")
    if "9843" in follows:
        _fail(f"9843 must not follow_re {follows}")
    import html_issuers.i9843 as nitori_mod

    if nitori_mod.PAGE.table is not None:
        _fail(f"9843 table {nitori_mod.PAGE.table}")
    forced = _module_force_units()
    if forced != {
        "2769": "yoy_index",
        "3148": "yoy_index",
        "8179": "yoy_index",
        "8281": "yoy_index",
        "9994": "yoy_index",
    }:
        _fail(f"force_unit {forced}")


ROCKFIELD_SECTION_UNIT_HTML = """
<html><body>
<h2>業態別売上推移（前年同月比）</h2>
<p>2026年5月度</p>
<p>(単位：%)</p>
<table>
<tr><td></td><td>3月</td><td>4月</td><td>5月</td></tr>
<tr><td>RF1</td><td>101.0</td><td>102.0</td><td>103.4</td></tr>
</table>
</body></html>
"""

ROCKFIELD_DUAL_SCOPE_HTML = """
<html><body>
<select>
<option value="/ir/library/monthly/">2027年4月期</option>
<option value="/ir/library/monthly/2026.html">2026年4月期</option>
</select>
<h2>2027年4月期　 業態別売上推移（前年同月比）</h2>
<h3>既存店売上</h3>
<p>(単位：%)</p>
<table>
<tr><td></td><td>5月</td><td>6月</td><td>7月</td><td>8月</td><td>合計</td></tr>
<tr><td>RF1</td><td>103.4</td><td>100.2</td><td>104.9</td><td>104.2</td><td></td></tr>
<tr><td>グリーン・グルメ</td><td>99.4</td><td>98.8</td><td>102.5</td><td>101.9</td><td></td></tr>
<tr><td>神戸コロッケ</td><td>99.7</td><td>98.7</td><td>103.9</td><td>103.2</td><td></td></tr>
<tr><td>合計</td><td>102.0</td><td>99.1</td><td>104.0</td><td>103.2</td><td></td></tr>
</table>
<h3>全店売上</h3>
<p>(単位：%)</p>
<table>
<tr><td></td><td>5月</td><td>6月</td><td>7月</td><td>8月</td><td>合計</td></tr>
<tr><td>RF1</td><td>102.8</td><td>99.9</td><td>104.4</td><td>103.1</td><td></td></tr>
<tr><td>グリーン・グルメ</td><td>99.5</td><td>99.2</td><td>102.9</td><td>102.1</td><td></td></tr>
<tr><td>神戸コロッケ</td><td>98.7</td><td>97.5</td><td>102.9</td><td>102.0</td><td></td></tr>
<tr><td>合計</td><td>101.4</td><td>99.4</td><td>103.9</td><td>103.0</td><td></td></tr>
</table>
</body></html>
"""

ROYAL_TRAILING_UNIT_HTML = """
<html><body>
<h2>既存店売上（前年同月比）</h2>
<p>2026年5月度</p>
<table>
<tr><td></td><td>3月</td><td>4月</td><td>5月</td></tr>
<tr><td>既存店</td><td>99.0</td><td>98.0</td><td>98.5</td></tr>
</table>
<p>単位:%</p>
</body></html>
"""

# Live 8179 index shape: nav both brands, h2 + year h3 with （既存店前年比）,
# H1 1–6 / H2 7–12 (7–8 filled). Values from /tmp/royal_index.html.
ROYAL_DUAL_BRAND_HTML = """
<html><body>
<nav>
<ul>
<li><a href="#royalhost">ロイヤルホスト</a></li>
<li><a href="#tenya">てんや</a></li>
</ul>
</nav>
<section id="royalhost">
<h2>ロイヤルホスト</h2>
<h3>2026年（令和8年）（既存店前年比）</h3>
<table>
<tr><th></th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>上半期</th></tr>
<tr><th>売上高</th><td>106.5</td><td>104.3</td><td>104.4</td><td>106.1</td><td>112.2</td><td>99.8</td><td>105.6</td></tr>
<tr><th>来客数</th><td>100.1</td><td>98.9</td><td>98.7</td><td>99.7</td><td>105.9</td><td>92.7</td><td>99.3</td></tr>
<tr><th>客単価</th><td>106.4</td><td>105.5</td><td>105.7</td><td>106.4</td><td>105.9</td><td>107.7</td><td>106.3</td></tr>
</table>
<table>
<tr><th></th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>下半期</th><th>年間</th></tr>
<tr><th>売上高</th><td>106.8</td><td>108.1</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>来客数</th><td>100.0</td><td>101.3</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>客単価</th><td>106.7</td><td>106.8</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
<p>（単位：％）</p>
<ul>
<li>上記数値は2026年8月度の確定値です。</li>
</ul>
</section>
<section id="tenya">
<h2>てんや</h2>
<h3>2026年（令和8年）（既存店前年比）</h3>
<table>
<tr><th></th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>上半期</th></tr>
<tr><th>売上高</th><td>106.2</td><td>105.6</td><td>106.6</td><td>100.1</td><td>103.2</td><td>98.4</td><td>103.4</td></tr>
<tr><th>来客数</th><td>94.1</td><td>97.6</td><td>100.1</td><td>94.3</td><td>97.3</td><td>92.1</td><td>95.9</td></tr>
<tr><th>客単価</th><td>112.9</td><td>108.2</td><td>106.5</td><td>106.2</td><td>106.0</td><td>106.9</td><td>107.7</td></tr>
</table>
<table>
<tr><th></th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>下半期</th><th>年間</th></tr>
<tr><th>売上高</th><td>102.3</td><td>98.0</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>来客数</th><td>94.7</td><td>90.6</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><th>客単価</th><td>108.0</td><td>108.2</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
<p>（単位：％）</p>
<ul>
<li>上記数値は2026年8月度の確定値です。</li>
</ul>
</section>
</body></html>
"""

FORCE_UNIT_HTML = """
<html><body>
<h2>全店売上高前年同月比</h2>
<p>2026年5月度</p>
<table>
<tr><td></td><td>3月</td><td>4月</td><td>5月</td></tr>
<tr><td>全店</td><td>101.0</td><td>102.0</td><td>103.0</td></tr>
<tr><td>店舗数</td><td>10</td><td>10</td><td>11</td></tr>
</table>
</body></html>
"""


KOBEBUSSAN_HALF_HTML = """
<html><body>
<h4>単体月次業績<span>（単位：百万円）</span></h4>
<p>2026年10月期</p>
<table>
<tr><td></td><td>11月</td><td>12月</td><td>1月</td><td>2月</td><td>3月</td><td>4月</td><td>上期</td></tr>
<tr><td>売上高</td><td>47,188</td><td>52,864</td><td>43,146</td><td>44,805</td><td>49,071</td><td>52,403</td><td>289,480</td></tr>
<tr><td>前年比</td><td>106.9</td><td>106.5</td><td>107.7</td><td>105.3</td><td>102.9</td><td>102.3</td><td>105.2</td></tr>
<tr><td></td><td>5月</td><td>6月</td><td>7月</td><td>8月</td><td>9月</td><td>10月</td><td>下期</td></tr>
<tr><td>売上高</td><td>49,860</td><td>47,520</td><td>50,636</td><td>48,242</td><td></td><td></td><td></td></tr>
<tr><td>前年比</td><td>103.0</td><td>105.4</td><td>107.6</td><td>105.9</td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""


def test_kobebussan_purchase_segment_is_shiire() -> None:
    """仕入れ前年対比を足す。脚注の出荷はセグメントにしない。"""
    html = (ROOT / "tests" / "testdata" / "html_issue248" / "3038.html").read_text(
        encoding="utf-8"
    )
    got = parse_issuer(html, "3038", table=None)
    labels = [r["element"] for r in got["rows"]]
    if any("出荷" in el for el in labels):
        _fail(f"footnote 出荷 leaked {labels}")
    exist = _row(got, "直轄エリア 仕入れ 既存店")
    area_all = _row(got, "直轄エリア 仕入れ 全店")
    national = _row(got, "全国※ 仕入れ 全店")
    if exist["unit"] != "yoy_index" or exist["values"].get("8") != 102.8:
        _fail(f"purchase existing {exist}")
    if exist["values"].get("11") != 105.4 or exist["values"].get("5") != 101.7:
        _fail(f"purchase existing halves {exist}")
    if area_all["values"].get("8") != 104.7 or area_all["values"].get("4") != 102.4:
        _fail(f"purchase area all {area_all}")
    if national["values"].get("8") != 105.6 or national["values"].get("1") != 106.5:
        _fail(f"purchase national {national}")
    sales = _row(got, "売上高", unit="number")
    if sales["values"].get("5") != 49860:
        _fail(f"sales half dropped {sales}")


def test_kobebussan_second_half_stays_on_sales() -> None:
    """1表の下期見出しを別の月にする。全店は付けない。"""
    got = parse_issuer(KOBEBUSSAN_HALF_HTML, "3038", table=None)
    if got["months"] != [11, 12, 1, 2, 3, 4, 5, 6, 7, 8]:
        _fail(f"months {got['months']}")
    if got["as_of"] != "2026-08-31":
        _fail(f"as_of {got['as_of']}")
    sales = _row(got, "売上高", unit="number")
    yoy = _row(got, "売上高", unit="yoy_index")
    if sales["values"].get("4") != 52403 or sales["values"].get("5") != 49860:
        _fail(f"sales {sales}")
    if sales["values"].get("8") != 48242 or "9" in sales["values"]:
        _fail(f"aug/sep {sales}")
    if yoy["values"].get("11") != 106.9 or yoy["values"].get("5") != 103.0:
        _fail(f"yoy {yoy}")
    if any("全店" in r["element"] for r in got["rows"]):
        _fail(f"scoped {got['rows']}")
    if sales["values"].get("11") == 49860:
        _fail(f"second half overwrote Nov {sales}")


def test_section_unit_percent_is_yoy_index() -> None:
    got = parse_issuer(ROCKFIELD_SECTION_UNIT_HTML, "2910")
    row = _row(got, "RF1 売上高前年同月比")
    if row["unit"] != "yoy_index":
        _fail(f"section unit:% must be yoy_index {row}")
    if row["values"].get("5") != 103.4:
        _fail(f"value must stay raw {row}")


def test_rockfield_existing_and_all_store_stay_separate() -> None:
    """既存店売上と全店売上は別行。既存の数値を裸の売上高(前年比)にしない。"""
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import canonicalize_element_label

    got = parse_issuer(ROCKFIELD_DUAL_SCOPE_HTML, "2910")
    labels = [r["element"] for r in got["rows"]]
    if any(el in ("RF1 売上高前年同月比", "グリーン・グルメ 売上高前年同月比") for el in labels):
        _fail(f"unscoped duplicate {labels}")
    if any(el.startswith("合計") or el == "合計" for el in labels):
        _fail(f"total row leaked {labels}")
    exist = _row(got, "既存店 RF1 売上高前年同月比")
    all_store = _row(got, "全店 RF1 売上高前年同月比")
    if exist["unit"] != "yoy_index" or exist["values"].get("5") != 103.4:
        _fail(f"existing {exist}")
    if exist["values"].get("8") != 104.2:
        _fail(f"existing aug {exist}")
    if all_store["unit"] != "yoy_index" or all_store["values"].get("5") != 102.8:
        _fail(f"all store {all_store}")
    if all_store["values"].get("8") != 103.1:
        _fail(f"all store aug {all_store}")
    gg = _row(got, "既存店 グリーン・グルメ 売上高前年同月比")
    if gg["values"].get("6") != 98.8:
        _fail(f"existing gourmet {gg}")
    kobe_all = _row(got, "全店 神戸コロッケ 売上高前年同月比")
    if kobe_all["values"].get("5") != 98.7:
        _fail(f"all kobe {kobe_all}")
    if canonicalize_element_label(exist["element"], exist["unit"]) != "RF1 既存 売上高(前年比)":
        _fail(f"canon exist {exist['element']}")
    if canonicalize_element_label(all_store["element"], all_store["unit"]) != "RF1 全店 売上高(前年比)":
        _fail(f"canon all {all_store['element']}")


def test_trailing_unit_percent_is_yoy_index() -> None:
    got = parse_8179(ROYAL_TRAILING_UNIT_HTML)
    row = _row(got, "既存店 売上高前年同月比")
    if row["unit"] != "yoy_index":
        _fail(f"trailing 単位:% must be yoy_index {row}")
    if row["values"].get("5") != 98.5:
        _fail(f"value must stay raw {row}")


def test_force_unit_overrides_yoy_keeps_number() -> None:
    got = parse_issuer(FORCE_UNIT_HTML, "2769", force_unit="yoy_index")
    yoy = _row(got, "全店 売上高前年同月比")
    if yoy["unit"] != "yoy_index":
        _fail(f"force_unit {yoy}")
    if yoy["values"].get("5") != 103.0:
        _fail(f"force_unit value {yoy}")
    stores = _row(got, "店舗数")
    if stores["unit"] != "number" or stores["values"].get("5") != 11:
        _fail(f"number row must stay number {stores}")


CREATE_SD_NOBIRITSU_HTML = """
<html><body>
<h2>2027年5月期（FYE May 2027）</h2>
<p>売上伸び率（単位：％） Year-on-year change in sales(%)</p>
<table>
<tr><td></td><td>6月</td><td>7月</td><td>8月</td></tr>
<tr><td>既存店 Existing stores 単月 Monthly</td><td>100.0</td><td>103.7</td><td>101.8</td></tr>
<tr><td>全店 All stores 単月 Monthly</td><td>103.6</td><td>107.1</td><td>105.5</td></tr>
</table>
</body></html>
"""


def test_create_sd_nobiritsu_force_yoy_index() -> None:
    """3148: 見出しは伸び率だがセルは100基準。モジュールは yoy_index のまま出す。"""
    declared = _module_force_units().get("3148")
    if declared != "yoy_index":
        _fail(f"3148 force_unit {declared}")
    got = parse_3148(CREATE_SD_NOBIRITSU_HTML)
    exist = _row(got, "既存店 単月")
    if exist["unit"] != "yoy_index":
        _fail(f"3148 must emit yoy_index {exist}")
    if exist["values"].get("7") != 103.7:
        _fail(f"3148 value must stay 103.7 not +100 {exist}")
    all_store = _row(got, "全店 単月")
    if all_store["unit"] != "yoy_index" or all_store["values"].get("7") != 107.1:
        _fail(f"3148 all store {all_store}")


def test_3148_real_page_drops_english_label() -> None:
    """実ページの日英併記を落とし、正規化後は既存の行名になる。"""
    path = ROOT / "tests" / "testdata" / "html_3148" / "2026.html"
    got = parse_3148(decode_html_bytes(path.read_bytes()))
    if got["as_of"] != "2026-08-31":
        _fail(f"3148 as_of {got['as_of']}")
    if got["months"] != [6, 7, 8]:
        _fail(f"3148 months {got['months']}")
    labels = [r["element"] for r in got["rows"]]
    if labels != ["既存店 単月", "全店 単月"]:
        _fail(f"3148 elements {labels}")
    if any(ch.isascii() and ch.isalpha() for el in labels for ch in el):
        _fail(f"3148 english remains {labels}")
    exist = _row(got, "既存店 単月")
    all_store = _row(got, "全店 単月")
    if exist["unit"] != "yoy_index" or all_store["unit"] != "yoy_index":
        _fail(f"3148 units {exist['unit']} {all_store['unit']}")
    if exist["values"] != {"6": 100, "7": 103.4, "8": 101.8}:
        _fail(f"3148 existing {exist['values']}")
    if all_store["values"] != {"6": 103.6, "7": 107.1, "8": 105.5}:
        _fail(f"3148 all store {all_store['values']}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    as_of = date(2026, 8, 31)
    stored = []
    for row in (exist, all_store):
        name = apply_issuer_aliases(
            "31480",
            canonicalize_element_label(row["element"], row["unit"]),
            as_of=as_of,
        )
        stored.append(canonicalize_element_label(name, row["unit"]))
    if stored != ["既存 売上高(前年比)", "全店 売上高(前年比)"]:
        _fail(f"3148 canonical {stored}")


def test_3549_sticky_metric_on_bare_kizon() -> None:
    """prompts/3549-2026.html を 2026-10-01 に投入した R2 HTML と同じバイト列。"""
    path = ROOT / "tests" / "testdata" / "html_3549" / "2026.html"
    got = parse_issuer(decode_html_bytes(path.read_bytes()), "3549")
    if got["as_of"] != "2026-09-30":
        _fail(f"3549 as_of {got['as_of']}")
    if 9 not in got["months"]:
        _fail(f"3549 months {got['months']}")
    sales = _row(got, "売上高 既存店")
    guests = _row(got, "客数 既存店")
    spend = _row(got, "客単価 既存店")
    if sales["values"].get("9") != 102.4:
        _fail(f"3549 売上高 既存店 {sales}")
    if guests["values"].get("9") != 98:
        _fail(f"3549 客数 既存店 {guests}")
    if spend["values"].get("9") != 104.4:
        _fail(f"3549 客単価 既存店 {spend}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    as_of = date(2026, 9, 30)
    stored = []
    for row in (guests, spend):
        name = apply_issuer_aliases(
            "35490",
            canonicalize_element_label(row["element"], row["unit"]),
            as_of=as_of,
        )
        stored.append(canonicalize_element_label(name, row["unit"]))
    if stored != ["既存 客数(前年比)", "既存 客単価(前年比)"]:
        _fail(f"3549 canonical {stored}")


def test_3549_store_status_prefix_strips_to_open_close() -> None:
    """［出退店］出店数 は接頭辞を落とし、サーバ正規化後は 出店 / 退店。"""
    path = ROOT / "tests" / "testdata" / "html_3549" / "2026.html"
    got = parse_issuer(decode_html_bytes(path.read_bytes()), "3549")
    if got["as_of"] != "2026-09-30":
        _fail(f"3549 as_of {got['as_of']}")
    if got["months"] != [6, 7, 8, 9]:
        _fail(f"3549 months {got['months']}")
    opened = _row(got, "出店数", unit="number")
    closed = _row(got, "退店数", unit="number")
    if opened["values"] != {"6": 10, "7": 1, "8": 10, "9": 13}:
        _fail(f"3549 出店数 {opened}")
    if closed["values"] != {"6": 0, "7": 4, "8": 1, "9": 1}:
        _fail(f"3549 退店数 {closed}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    as_of = date(2026, 9, 30)
    stored = []
    for row in (opened, closed):
        name = apply_issuer_aliases(
            "35490",
            canonicalize_element_label(row["element"], row["unit"]),
            as_of=as_of,
        )
        stored.append(canonicalize_element_label(name, row["unit"]))
    if stored != ["出店", "退店"]:
        _fail(f"3549 store-status canonical {stored}")


NITORI_HTML = """
<html><body>
<h1>2027年3月期 月次売上概況</h1>
<table>
<tr><th></th><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th></tr>
<tr><td>売上（%）</td><td>既存店</td><td>101.2</td><td>99.5</td><td>102.0</td><td>100.1</td><td>98.8</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>売上（%）</td><td>全店</td><td>103.0</td><td>101.1</td><td>104.2</td><td>102.5</td><td>100.9</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>店舗数</td><td>出店</td><td>2</td><td>1</td><td>0</td><td>3</td><td>1</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

YAMAZAWA_HTML = """
<html><body>
<h1>月次業績推移</h1>
<h2><strong>65期</strong>（2026.03.01～2027.02.28）</h2>
<p>(単位：％)</p>
<table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期計</th></tr>
<tr><td>全店</td><td>売 上</td><td>92.4</td><td>93.2</td><td>97.2</td><td>91.3</td><td>92.6</td><td>90.9</td><td>92.9</td></tr>
<tr><td>既存店</td><td>売 上</td><td>100.0</td><td>101.1</td><td>105.6</td><td>99.2</td><td>100.7</td><td>98.9</td><td>100.9</td></tr>
</table>
<h2><strong>64期</strong>（2025.03.01～2026.02.28）</h2>
<p>(単位：％)</p>
<table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>全店</td><td>売 上</td><td>105.6</td><td>105.6</td><td>101.9</td><td>103.6</td><td>104.6</td><td>103.1</td>
<td>102.0</td><td>101.0</td><td>100.0</td><td>99.0</td><td>98.0</td><td>97.0</td></tr>
<tr><td>既存店</td><td>売 上</td><td>106.2</td><td>106.1</td><td>102.4</td><td>104.2</td><td>105.2</td><td>103.6</td>
<td>102.5</td><td>101.5</td><td>100.5</td><td>99.5</td><td>98.5</td><td>97.5</td></tr>
</table>
<h2><strong>40期</strong>（2004.03.01～2005.02.28）</h2>
<table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>全店</td><td>売 上</td><td>90.0</td><td>91.0</td><td>92.0</td><td>93.0</td><td>94.0</td><td>95.0</td>
<td>96.0</td><td>97.0</td><td>98.0</td><td>99.0</td><td>100.0</td><td>101.0</td></tr>
<tr><td>既存店</td><td>売 上</td><td>90.0</td><td>91.0</td><td>92.0</td><td>93.0</td><td>94.0</td><td>95.0</td>
<td>96.0</td><td>97.0</td><td>98.0</td><td>99.0</td><td>100.0</td><td>101.0</td></tr>
</table>
</body></html>
"""

AEON_HOKKAIDO_HTML = """
<html><body>
<h1>月次売上速報</h1>
<div class="slide-item">
<p class="year-tit">2026年(令和8年)</p>
<table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>企業計</td><td>既存計</td><td>102.6</td><td>101.4</td><td>104.3</td><td>99.8</td><td>101.0</td><td>102.5</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>企業計</td><td>企業計</td><td>102.2</td><td>100.9</td><td>103.8</td><td>99.4</td><td>100.6</td><td>102.1</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</div>
<div class="slide-item">
<p class="year-tit">2023年(令和5年)</p>
<table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>企業計</td><td>既存計</td><td>103.6</td><td>100.7</td><td>100.2</td><td>105.9</td><td>100.7</td><td>103.0</td>
<td>105.1</td><td>98.5</td><td>101.2</td><td>100.4</td><td>101.6</td><td>104.2</td></tr>
<tr><td>企業計</td><td>企業計</td><td>104.3</td><td>101.4</td><td>100.8</td><td>106.4</td><td>101.4</td><td>103.5</td>
<td>105.8</td><td>99.3</td><td>102.0</td><td>101.0</td><td>102.6</td><td>105.3</td></tr>
</table>
</div>
</body></html>
"""

# Archive table is complete; current FY caption is next year. Page-global FY + n_val
# used to stamp 2027-05-31 / 2027-03-31. table=None must keep last filled month.
VILLAGE_HTML = """
<html><body>
<h2>2026年5月期</h2>
<table>
<tr><th></th><th>6月</th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th>
<th>12月</th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th></tr>
<tr><td>全店</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td>
<td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>
</table>
<h2>2027年5月期</h2>
<table>
<tr><th></th><th>6月</th><th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th>
<th>12月</th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th></tr>
<tr><td>全店</td><td>10</td><td>11</td><td>12</td><td></td><td></td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

NAFCO_HTML = """
<html><body>
<h2>2026年3月期</h2>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th>
<th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th></tr>
<tr><td>既存店</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td>
<td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>
</table>
<h2>2027年3月期</h2>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th>
<th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th></tr>
<tr><td>既存店</td><td>10</td><td>11</td><td>12</td><td>13</td><td>14</td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

SAGAMI_HTML = """
<html><body>
<h2>2026年3月期</h2>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th>
<th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th></tr>
<tr><td>全店</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td>
<td>1</td><td>1</td><td>1</td><td>1</td><td>1</td><td>1</td></tr>
</table>
<h2>2027年3月期</h2>
<table>
<tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th>
<th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th></tr>
<tr><td>全店</td><td>20</td><td>21</td><td>22</td><td>23</td><td>24</td><td></td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""


def test_nitori_direct_page_no_follow() -> None:
    got = parse_issuer(NITORI_HTML, "9843", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"nitori as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"nitori months {got['months']}")
    exist = _row(got, "売上（%） 既存店")
    if exist["values"].get("8") != 98.8:
        _fail(f"nitori {exist}")
    stores = _row(got, "店舗数 出店")
    if stores["unit"] != "number" or stores["values"].get("8") != 1:
        _fail(f"nitori stores {stores}")


def test_yamazawa_prefers_current_period() -> None:
    got = parse_issuer(YAMAZAWA_HTML, "9993", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"yamazawa as_of {got['as_of']}")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"yamazawa months {got['months']}")
    all_store = _row(got, "全店 売上")
    if all_store["values"].get("8") != 90.9:
        _fail(f"yamazawa picked old period {all_store}")
    if any("上期" in r["element"] for r in got["rows"]):
        _fail("上期計 leaked")


def test_aeon_hokkaido_prefers_current_year_slide() -> None:
    got = parse_issuer(AEON_HOKKAIDO_HTML, "7512", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"aeon as_of {got['as_of']}")
    exist = _row(got, "企業計 既存計")
    if exist["values"].get("8") != 102.5:
        _fail(f"aeon picked old year {exist}")
    if exist["values"].get("2") is not None:
        _fail(f"aeon should not use full old year values {exist}")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"aeon months {got['months']}")


def test_village_nafco_sagami_as_of_not_future_fy() -> None:
    cases = (
        (VILLAGE_HTML, "2769", "全店", 12),
        (NAFCO_HTML, "2790", "既存店", 14),
        (SAGAMI_HTML, "9900", "全店", 24),
    )
    for html, code, element, august in cases:
        got = parse_issuer(html, code, table=None)
        if got["as_of"] != "2026-08-31":
            _fail(f"{code} as_of {got['as_of']}")
        row = _row(got, element)
        if row["values"].get("8") != august:
            _fail(f"{code} picked archive {row}")
        if len(row["values"]) >= 12:
            _fail(f"{code} leaked archive months {row}")


def test_yoy_context_does_not_rename_guest_metrics() -> None:
    html = """
    <html><body>
    <h2>2027年3月期 売上高前年同月比</h2>
    <table>
    <tr><th></th><th>1月</th><th>2月</th><th>8月</th></tr>
    <tr><td>売上高</td><td>106.5</td><td>104.3</td><td>99.8</td></tr>
    <tr><td>来客数</td><td>100.1</td><td>98.9</td><td>92.7</td></tr>
    <tr><td>客単価</td><td>106.4</td><td>105.5</td><td>107.7</td></tr>
    </table>
    </body></html>
    """
    got = parse_8179(html)
    labels = [r["element"] for r in got["rows"]]
    if any("売上高前年同月比" in el for el in labels if "来客" in el or "客単" in el):
        _fail(f"guest metric leaked sales yoy {labels}")
    if not any("来客数" == el or el.startswith("来客数") for el in labels):
        _fail(f"来客数 missing {labels}")


def test_royal_two_brands_union_halves_and_keep_existing_scope() -> None:
    got = parse_8179(ROYAL_DUAL_BRAND_HTML)
    if got["as_of"] != "2026-08-31":
        _fail(f"royal as_of {got['as_of']}")
    if 1 not in got["months"] or 8 not in got["months"]:
        _fail(f"royal months {got['months']}")
    labels = [r["element"] for r in got["rows"]]
    host = _row(got, "ロイヤルホスト 既存店 売上高")
    if host["unit"] != "yoy_index":
        _fail(f"host unit {host}")
    if host["values"].get("1") != 106.5 or host["values"].get("8") != 108.1:
        _fail(f"host H1+H2 {host}")
    tenya_sales = _row(got, "てんや 既存店 売上高")
    if tenya_sales["values"].get("1") != 106.2:
        _fail(f"tenya H1 dropped {tenya_sales}")
    tenya_guest = _row(got, "てんや 既存店 来客数")
    if tenya_guest["unit"] != "yoy_index":
        _fail(f"tenya guest unit {tenya_guest}")
    branded = (
        "ロイヤルホスト 既存店 売上高",
        "ロイヤルホスト 既存店 来客数",
        "ロイヤルホスト 既存店 客単価",
        "てんや 既存店 売上高",
        "てんや 既存店 来客数",
        "てんや 既存店 客単価",
    )
    for el in branded:
        row = _row(got, el)
        if row["unit"] != "yoy_index":
            _fail(f"{el} unit {row}")
    if any(el in ("売上高", "来客数", "客単価") or el.startswith("既存店 ") for el in labels):
        _fail(f"bare rows leaked {labels}")
    if any(not el.startswith(("ロイヤルホスト ", "てんや ")) for el in labels):
        _fail(f"unbranded {labels}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import canonicalize_element_label

    stored = canonicalize_element_label(tenya_guest["element"], tenya_guest["unit"])
    if stored != "てんや 既存 客数(前年比)":
        _fail(f"royal canonicalize {stored}")


def test_repeated_scope_row_prefixes_existing_and_all_store() -> None:
    html = """
    <html><body>
    <h2>2027年3月期</h2>
    <table>
    <tr><th></th><th>4月</th><th>5月</th><th>8月</th><th>下半期</th></tr>
    <tr><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td></tr>
    <tr><td>売上高</td><td>98.3</td><td>105.8</td><td>101.0</td><td>98.4</td></tr>
    <tr><td>客数</td><td>100.3</td><td>104.0</td><td>96.2</td><td>95.8</td></tr>
    <tr><td>全店</td><td>全店</td><td>全店</td><td>全店</td><td>全店</td></tr>
    <tr><td>売上高</td><td>94.4</td><td>103.1</td><td>96.7</td><td>94.7</td></tr>
    <tr><td>客数</td><td>95.9</td><td>101.8</td><td>91.7</td><td>92.0</td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "7494", table=None)
    labels = [r["element"] for r in got["rows"]]
    if "既存店 売上高" not in labels or "全店 売上高" not in labels:
        _fail(f"scope prefix missing {labels}")
    if labels.count("売上高"):
        _fail(f"bare 売上高 remained {labels}")
    exist = _row(got, "既存店 売上高")
    if exist["values"].get("8") != 101:
        _fail(f"既存店 {exist}")


def test_same_fy_prefers_later_filled_half() -> None:
    html = """
    <html><body>
    <h2>2026年9月期(前年同月比%)</h2>
    <table>
    <tr><th></th><th>10月</th><th>11月</th><th>3月</th><th>上半期</th></tr>
    <tr><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td></tr>
    <tr><td>売上高</td><td>96.4</td><td>105.1</td><td>99.8</td><td>99.6</td></tr>
    <tr><td>全店</td><td>全店</td><td>全店</td><td>全店</td><td>全店</td></tr>
    <tr><td>売上高</td><td>95.9</td><td>106.1</td><td>96.4</td><td>98.8</td></tr>
    </table>
    <table>
    <tr><th></th><th>4月</th><th>5月</th><th>8月</th><th>下半期</th></tr>
    <tr><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td></tr>
    <tr><td>売上高</td><td>98.3</td><td>105.8</td><td>101.0</td><td>98.4</td></tr>
    <tr><td>全店</td><td>全店</td><td>全店</td><td>全店</td><td>全店</td></tr>
    <tr><td>売上高</td><td>94.4</td><td>103.1</td><td>96.7</td><td>94.7</td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "7494", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"same-fy as_of {got['as_of']}")
    if got["months"] != [4, 5, 8]:
        _fail(f"same-fy months {got['months']}")
    exist = _row(got, "既存店 売上高")
    if exist["values"].get("8") != 101:
        _fail(f"picked H1 {exist}")


def test_same_fy_prefers_last_quarter_over_prior() -> None:
    html = """
    <html><body>
    <h2>2025年度 月次推移</h2>
    <table>
    <tr><td></td><td>2026.1</td><td>2026.2</td><td>2026.3</td></tr>
    <tr><td>全店 (全業態)</td><td>110.1</td><td>106.1</td><td>106.7</td></tr>
    </table>
    <table>
    <tr><td></td><td>2025.10</td><td>2025.11</td><td>2025.12</td></tr>
    <tr><td>全店 (全業態)</td><td>108.0</td><td>107.0</td><td>106.0</td></tr>
    </table>
    </body></html>
    """
    got = parse_7522(html)
    if got["as_of"] != "2026-03-31":
        _fail(f"last quarter as_of {got['as_of']}")
    if got["months"] != [1, 2, 3]:
        _fail(f"last quarter months {got['months']}")
    sales = _row(got, "全店 (全業態)")
    if sales["values"].get("3") != 106.7:
        _fail(f"picked prior quarter {sales}")


def test_calendar_ytd_drops_trailing_empty_months() -> None:
    html = """
    <html><body>
    <p>（2026年9月4日更新）</p>
    <h2>2026年 月次</h2>
    <table>
    <tr><th></th><th>1月</th><th>2月</th><th>3月</th><th>4月</th><th>5月</th><th>6月</th>
    <th>7月</th><th>8月</th><th>9月</th><th>10月</th><th>11月</th><th>12月</th></tr>
    <tr><td>全店</td><td>92.6</td><td>90.0</td><td>81.1</td><td>104.2</td><td>94.5</td><td>85.0</td>
    <td>107.4</td><td>80.5</td><td></td><td></td><td></td><td></td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "6537", table=None)
    if got["months"] != [1, 2, 3, 4, 5, 6, 7, 8]:
        _fail(f"calendar YTD months {got['months']}")
    sales = _row(got, "全店")
    if sales["values"].get("8") != 80.5:
        _fail(f"calendar YTD values {sales}")
    if any(k in sales["values"] for k in ("9", "10", "11", "12")):
        _fail(f"trailing months leaked into values {sales['values']}")


WASHHOUSE_PAIR_HTML = """
<html><body>
<h3>国内都道府県別店舗数</h3>
<p>総店舗数 563店舗</p>
<p>2026年8月31日現在</p>
<h3>店舗数推移と売上高前年同月比</h3>
<p>2026年12月期（26期 2026年1月1日～2026年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="4">店舗数</th></tr>
<tr><th>新規出店数</th><th>一時閉店</th><th>退店</th><th>店舗数累計</th></tr>
<tr><th>2026年1月</th><td>0</td><td>0</td><td>0</td><td>571</td></tr>
<tr><th>2026年2月</th><td>1</td><td>0</td><td>0</td><td>572</td></tr>
<tr><th>2026年3月</th><td>1</td><td>0</td><td>4</td><td>569</td></tr>
<tr><th>2026年4月</th><td>0</td><td>0</td><td>0</td><td>569</td></tr>
<tr><th>2026年5月</th><td>0</td><td>0</td><td>2</td><td>567</td></tr>
<tr><th>2026年6月</th><td>0</td><td>0</td><td>1</td><td>566</td></tr>
<tr><th>2026年7月</th><td>0</td><td>0</td><td>1</td><td>565</td></tr>
<tr><th>2026年8月</th><td>0</td><td>0</td><td>2</td><td>563</td></tr>
<tr><th>2026年9月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2026年10月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2026年11月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2026年12月</th><td></td><td></td><td></td><td></td></tr>
</table>
<h3>売上高前年同月比</h3>
<p>2026年12月期（26期 2026年1月1日～2026年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="2">売上高前年同月比（％）</th></tr>
<tr><th>全店</th><th>既存店</th></tr>
<tr><th>2026年1月</th><td>92.6%</td><td>93.5%</td></tr>
<tr><th>2026年2月</th><td>90.0%</td><td>93.0%</td></tr>
<tr><th>2026年3月</th><td>81.1%</td><td>81.8%</td></tr>
<tr><th>2026年4月</th><td>104.2%</td><td>109.3%</td></tr>
<tr><th>2026年5月</th><td>94.5%</td><td>99.4%</td></tr>
<tr><th>2026年6月</th><td>85.0%</td><td>89.7%</td></tr>
<tr><th>2026年7月</th><td>107.4%</td><td>114.0%</td></tr>
<tr><th>2026年8月</th><td>80.5%</td><td>87.5%</td></tr>
<tr><th>2026年9月</th><td></td><td></td></tr>
<tr><th>2026年10月</th><td></td><td></td></tr>
<tr><th>2026年11月</th><td></td><td></td></tr>
<tr><th>2026年12月</th><td></td><td></td></tr>
</table>
<p>2024年12月期（24期 2024年1月1日～2024年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="4">店舗数</th></tr>
<tr><th>新規出店数</th><th>一時閉店</th><th>退店</th><th>店舗数累計</th></tr>
<tr><th>2024年1月</th><td>1</td><td>0</td><td>2</td><td>596</td></tr>
<tr><th>2024年2月</th><td>0</td><td>0</td><td>1</td><td>595</td></tr>
<tr><th>2024年3月</th><td>0</td><td>0</td><td>1</td><td>594</td></tr>
<tr><th>2024年4月</th><td>2</td><td>0</td><td>4</td><td>592</td></tr>
<tr><th>2024年5月</th><td>0</td><td>0</td><td>4</td><td>588</td></tr>
<tr><th>2024年6月</th><td>1</td><td>0</td><td>1</td><td>588</td></tr>
<tr><th>2024年7月</th><td>0</td><td>0</td><td>3</td><td>585</td></tr>
<tr><th>2024年8月</th><td>1</td><td>0</td><td>2</td><td>584</td></tr>
<tr><th>2024年9月</th><td>0</td><td>0</td><td>0</td><td>584</td></tr>
<tr><th>2024年10月</th><td>0</td><td>0</td><td>3</td><td>581</td></tr>
<tr><th>2024年11月</th><td>0</td><td>0</td><td>1</td><td>580</td></tr>
<tr><th>2024年12月</th><td>4</td><td>0</td><td>1</td><td>583</td></tr>
</table>
<h3>売上高前年同月比</h3>
<p>2024年12月期（24期 2024年1月1日～2024年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="2">売上高前年同月比（％）</th></tr>
<tr><th>全店</th><th>既存店</th></tr>
<tr><th>2024年1月</th><td>94.3%</td><td>96.0%</td></tr>
<tr><th>2024年2月</th><td>118.5%</td><td>120.6%</td></tr>
<tr><th>2024年3月</th><td>107.5%</td><td>109.3%</td></tr>
<tr><th>2024年4月</th><td>111.4%</td><td>113.2%</td></tr>
<tr><th>2024年5月</th><td>85.0%</td><td>86.1%</td></tr>
<tr><th>2024年6月</th><td>101.7%</td><td>104.1%</td></tr>
<tr><th>2024年7月</th><td>97.8%</td><td>99.9%</td></tr>
<tr><th>2024年8月</th><td>84.9%</td><td>87.0%</td></tr>
<tr><th>2024年9月</th><td>86.0%</td><td>88.3%</td></tr>
<tr><th>2024年10月</th><td>102.9%</td><td>105.1%</td></tr>
<tr><th>2024年11月</th><td>119.6%</td><td>121.9%</td></tr>
<tr><th>2024年12月</th><td>91.8%</td><td>93.2%</td></tr>
</table>
</body></html>
"""

WASHHOUSE_FY2024_HTML = """
<html><body>
<p>2026年8月31日現在</p>
<p>2024年12月期（24期 2024年1月1日～2024年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="4">店舗数</th></tr>
<tr><th>新規出店数</th><th>一時閉店</th><th>退店</th><th>店舗数累計</th></tr>
<tr><th>2024年1月</th><td>1</td><td>0</td><td>2</td><td>596</td></tr>
<tr><th>2024年2月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年3月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年4月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年5月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年6月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年7月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年8月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年9月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年10月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年11月</th><td></td><td></td><td></td><td></td></tr>
<tr><th>2024年12月</th><td>4</td><td>0</td><td>1</td><td>583</td></tr>
</table>
<h3>売上高前年同月比</h3>
<p>2024年12月期（24期 2024年1月1日～2024年12月31日）</p>
<table>
<tr><th rowspan="2">月</th><th colspan="2">売上高前年同月比（％）</th></tr>
<tr><th>全店</th><th>既存店</th></tr>
<tr><th>2024年1月</th><td>94.3%</td><td>96.0%</td></tr>
<tr><th>2024年2月</th><td></td><td></td></tr>
<tr><th>2024年3月</th><td></td><td></td></tr>
<tr><th>2024年4月</th><td></td><td></td></tr>
<tr><th>2024年5月</th><td></td><td></td></tr>
<tr><th>2024年6月</th><td></td><td></td></tr>
<tr><th>2024年7月</th><td></td><td></td></tr>
<tr><th>2024年8月</th><td></td><td></td></tr>
<tr><th>2024年9月</th><td></td><td></td></tr>
<tr><th>2024年10月</th><td></td><td></td></tr>
<tr><th>2024年11月</th><td></td><td></td></tr>
<tr><th>2024年12月</th><td>91.8%</td><td>93.2%</td></tr>
</table>
</body></html>
"""


def _washhouse_stored(element: str, unit: str) -> str:
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import canonicalize_element_label

    return canonicalize_element_label(element, unit)


def test_washhouse_merges_store_and_sales_latest_pair() -> None:
    """6537: 最新の店舗数+売上ペアを同一 payload に。2024 は混ぜない。"""
    got = parse_issuer(WASHHOUSE_PAIR_HTML, "6537")
    if got["as_of"] != "2026-08-31":
        _fail(f"6537 as_of {got['as_of']}")
    if got["months"] != [1, 2, 3, 4, 5, 6, 7, 8]:
        _fail(f"6537 months {got['months']}")
    stored = {_washhouse_stored(r["element"], r["unit"]) for r in got["rows"]}
    if stored != {
        "出店",
        "一時 退店",
        "退店",
        "店舗数",
        "全店 売上高(前年比)",
        "既存 売上高(前年比)",
    }:
        _fail(f"6537 stored labels {sorted(stored)}")
    by_stored = {
        _washhouse_stored(r["element"], r["unit"]): r for r in got["rows"]
    }
    all_store = by_stored["全店 売上高(前年比)"]
    exist = by_stored["既存 売上高(前年比)"]
    if all_store["unit"] != "yoy_index" or all_store["values"].get("1") != 92.6:
        _fail(f"6537 all-store {all_store}")
    if all_store["values"].get("8") != 80.5:
        _fail(f"6537 all-store Aug {all_store}")
    if exist["unit"] != "yoy_index" or exist["values"].get("1") != 93.5:
        _fail(f"6537 existing {exist}")
    if exist["values"].get("1") == 96 or exist["values"].get("1") == 96.0:
        _fail(f"6537 mixed 2024 sales {exist}")
    opens = by_stored["出店"]
    stores = by_stored["店舗数"]
    closes = by_stored["退店"]
    if opens["unit"] != "number" or opens["values"].get("2") != 1:
        _fail(f"6537 出店 {opens}")
    if stores["unit"] != "number" or stores["values"].get("1") != 571:
        _fail(f"6537 店舗数 {stores}")
    if stores["values"].get("8") != 563:
        _fail(f"6537 店舗数 Aug {stores}")
    if stores["values"].get("12") == 583:
        _fail(f"6537 mixed 2024 stores {stores}")
    if closes["values"].get("3") != 4:
        _fail(f"6537 退店 {closes}")


def test_washhouse_past_year_as_of_from_row_dates() -> None:
    """6537: 過去年ペアは行の YYYY年M月 から as_of。ページの 2026-08-31 現在で毒さない。"""
    got = parse_issuer(WASHHOUSE_FY2024_HTML, "6537")
    if got["as_of"] != "2024-12-31":
        _fail(f"6537 fy2024 as_of {got['as_of']}")
    if got["months"] != [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
        _fail(f"6537 fy2024 months {got['months']}")
    by_stored = {
        _washhouse_stored(r["element"], r["unit"]): r for r in got["rows"]
    }
    if set(by_stored) != {
        "出店",
        "一時 退店",
        "退店",
        "店舗数",
        "全店 売上高(前年比)",
        "既存 売上高(前年比)",
    }:
        _fail(f"6537 fy2024 labels {sorted(by_stored)}")
    all_store = by_stored["全店 売上高(前年比)"]
    stores = by_stored["店舗数"]
    if all_store["unit"] != "yoy_index" or all_store["values"].get("1") != 94.3:
        _fail(f"6537 fy2024 sales {all_store}")
    if all_store["values"].get("12") != 91.8:
        _fail(f"6537 fy2024 Dec sales {all_store}")
    if stores["unit"] != "number" or stores["values"].get("12") != 583:
        _fail(f"6537 fy2024 stores {stores}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import plan_column_years

    _layout, mapping = plan_column_years(got["months"], 12, date(2024, 12, 31))
    years = dict(mapping)
    if years.get(1) != 2024 or years.get(12) != 2024:
        _fail(f"6537 fy2024 calendar years {mapping}")


def test_partial_apr_sep_ytd_drops_trailing_empty_months() -> None:
    html = """
    <html><body>
    <h2>2026年9月期(前年同月比%)</h2>
    <table>
    <tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th></tr>
    <tr><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td><td>既存店</td></tr>
    <tr><td>売上高</td><td>98.3</td><td>105.8</td><td>87.4</td><td>100.6</td><td>101.0</td><td></td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "7494", table=None)
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"partial YTD months {got['months']}")
    exist = _row(got, "既存店 売上高")
    if exist["values"].get("8") != 101:
        _fail(f"partial YTD {exist}")
    if "9" in exist["values"]:
        _fail(f"Sep empty leaked {exist}")


def test_trailing_empty_months_keep_middle_gaps() -> None:
    html = """
    <html><body>
    <p>（2026年9月4日更新）</p>
    <table>
    <tr><th></th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th></tr>
    <tr><td>売上高</td><td>98.3</td><td></td><td>87.4</td><td></td><td>101.0</td><td></td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "7494", table=None)
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"middle gaps months {got['months']}")
    sales = _row(got, "売上高")
    if sales["values"] != {"4": 98.3, "6": 87.4, "8": 101}:
        _fail(f"middle gaps values {sales['values']}")


def test_hash_skip_and_reparse(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    html = CAWACHI_HTML.encode("utf-8")
    bodies = {"https://example.test/ir/": html}
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="2664",
            name="カワチ薬品",
            index_url="https://example.test/ir/",
            title="月次",
            source="html",
            eir_code="2664",
            eir_n=0,
            eir_title_re="月次",
        )
        stock_map = {"26640": ("011", "カワチ薬品")}
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 14), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"first {n, s, k, f} {calls}")
        html_files = list((dest / "2664").glob("*.html"))
        json_files = list((dest / "2664").glob("*.ingest.json"))
        if [p.name for p in html_files] != ["20260914_2664_カワチ薬品_月次.html"]:
            _fail(f"html {html_files}")
        if [p.name for p in json_files] != ["20260914_2664_カワチ薬品_月次.ingest.json"]:
            _fail(f"json {json_files}")
        payload = json.loads(json_files[0].read_text(encoding="utf-8"))
        if payload["rows"][0]["values"]["7"] != -4.3:
            _fail(payload)
        if payload["as_of"] != "2026-08-31":
            _fail(f"cawachi as_of {payload['as_of']}")
        if "year" in payload:
            _fail("year must not be in ingest json")

        calls.clear()
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 15), dry_run=False)
        if (n, s, k, f) != (1, 0, 1, 0):
            _fail(f"hash skip {n, s, k, f}")
        if len(list((dest / "2664").glob("*.html"))) != 1:
            _fail("hash skip wrote html")

        bodies["https://example.test/ir/"] = CAWACHI_HTML.replace("1.9", "9.9").encode("utf-8")
        calls.clear()
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 15), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"changed {n, s, k, f}")
        names = sorted(p.name for p in (dest / "2664").glob("*.html"))
        if names != [
            "20260914_2664_カワチ薬品_月次.html",
            "20260915_2664_カワチ薬品_月次.html",
        ]:
            _fail(f"history {names}")
        hashes = existing_html_hashes(dest / "2664")
        if len(hashes) != 2:
            _fail(f"hashes {hashes}")

        sidecar = dest / "2664" / "20260915_2664_カワチ薬品_月次.ingest.json"
        sidecar.unlink()
        calls.clear()
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 16), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"missing ingest {n, s, k, f}")
        if not sidecar.is_file():
            _fail("hash skip should rebuild missing ingest.json")
        if len(list((dest / "2664").glob("*.html"))) != 2:
            _fail("missing ingest rebuilt html")

        rewritten = dest / "2664" / "20260914_2664_カワチ薬品_月次.ingest.json"
        rewritten.write_text("{}", encoding="utf-8")
        p, k, f = reparse_issuer(iss, dest)
        if (p, k, f) != (2, 0, 0):
            _fail(f"reparse {p, k, f}")
        again = json.loads(rewritten.read_text(encoding="utf-8"))
        if again["rows"][0]["values"]["4"] != 1.9:
            _fail(again)
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_hash_skip_refreshes_stale_sidecar(tmp: Path) -> None:
    """同じ HTML でも古い sidecar（上期・ブランド無し）は現行パーサで書き直す。"""
    import ir_monthly_html_r2 as mod

    dest = tmp / "stale_sidecar" / "monthly"
    bodies = {"https://example.test/royal/": ROYAL_DUAL_BRAND_HTML.encode("utf-8")}

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="8179",
            name="ロイヤルホールディングス",
            index_url="https://example.test/royal/",
            title="月次",
            source="html",
            eir_code="8179",
            eir_n=0,
            eir_title_re="月次",
        )
        stock_map = {"81790": ("011", "ロイヤルホールディングス")}
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 17), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"first {n, s, k, f}")
        sidecar = dest / "8179" / "20260917_8179_ロイヤルホールディングス_月次.ingest.json"
        stale = {
            "code": "8179",
            "months": [1, 2, 3, 4, 5, 6],
            "rows": [
                {
                    "element": "売上高",
                    "unit": "yoy_index",
                    "values": {"1": 106.5, "6": 99.8},
                },
            ],
            "as_of": "2026-08-31",
        }
        sidecar.write_text(
            json.dumps(stale, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 26), dry_run=False)
        text = buf.getvalue()
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"refresh {n, s, k, f} {text}")
        if "refresh ingest" not in text:
            _fail(text)
        if len(list((dest / "8179").glob("*.html"))) != 1:
            _fail("hash skip wrote html")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if payload["months"] != [1, 2, 3, 4, 5, 6, 7, 8]:
            _fail(f"months {payload['months']}")
        labels = [r["element"] for r in payload["rows"]]
        host = next(r for r in payload["rows"] if r["element"] == "ロイヤルホスト 既存店 売上高")
        if host["values"].get("8") != 108.1:
            _fail(host)
        if "売上高" in labels:
            _fail(f"bare leaked {labels}")
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 27), dry_run=False)
        if (n, s, k, f) != (1, 0, 1, 0):
            _fail(f"second skip {n, s, k, f}")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def _cawachi_issuer() -> HtmlIssuer:
    return HtmlIssuer(
        code="2664",
        name="カワチ薬品",
        index_url="https://example.test/ir/",
        title="月次",
        source="html",
        eir_code="2664",
        eir_n=0,
        eir_title_re="月次",
    )


def test_force_resaves_unchanged_html(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "force_resave" / "monthly"
    bodies = {"https://example.test/ir/": CAWACHI_HTML.encode("utf-8")}

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = _cawachi_issuer()
        stock_map = {"26640": ("011", "カワチ薬品")}
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 14), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"first {n, s, k, f}")
        buf = io.StringIO()
        with redirect_stdout(buf):
            n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 15), dry_run=False)
        if (n, s, k, f) != (1, 0, 1, 0):
            _fail(f"hash skip {n, s, k, f} {buf.getvalue()}")
        if "skip hash" not in buf.getvalue():
            _fail(buf.getvalue())
        if len(list((dest / "2664").glob("*.html"))) != 1:
            _fail("unchanged without force wrote html")
        prev_side = dest / "2664" / "20260914_2664_カワチ薬品_月次.ingest.json"
        prev_side.write_text("not-json", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            n, s, k, f = fetch_issuer(
                iss, dest, stock_map, date(2026, 9, 15), dry_run=False, force=True,
            )
        text = buf.getvalue()
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"force {n, s, k, f} {text}")
        if "force " not in text or "unchanged ==" not in text:
            _fail(text)
        today_html = dest / "2664" / "20260915_2664_カワチ薬品_月次.html"
        today_json = dest / "2664" / "20260915_2664_カワチ薬品_月次.ingest.json"
        if not today_html.is_file() or today_html.read_bytes() != bodies["https://example.test/ir/"]:
            _fail("today html missing")
        if prev_side.read_text(encoding="utf-8") != "not-json":
            _fail("force rewrote the previous sidecar in place")
        payload = json.loads(today_json.read_text(encoding="utf-8"))
        if payload["rows"][0]["values"]["7"] != -4.3 or payload["as_of"] != "2026-08-31":
            _fail(payload)
        if "year" in payload:
            _fail("year must not be in ingest json")
        today_json.write_text("garbage", encoding="utf-8")
        n, s, k, f = fetch_issuer(
            iss, dest, stock_map, date(2026, 9, 15), dry_run=False, force=True,
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"same-day force {n, s, k, f}")
        if len(list((dest / "2664").glob("*.html"))) != 2:
            _fail("same-day force added another html")
        again = json.loads(today_json.read_text(encoding="utf-8"))
        if again["rows"][0]["values"]["4"] != 1.9:
            _fail(again)
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_run_force_requires_only_and_skips_reparse(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    base = tmp / "force_usage"
    base.mkdir()
    calls: list[str] = []

    def boom(*_a: object, **_k: object) -> bytes:
        calls.append("called")
        raise AssertionError("should not load")

    orig_get = mod.http_get
    orig_map = mod.load_stock_product_map
    mod.http_get = boom  # type: ignore[assignment]
    mod.load_stock_product_map = boom  # type: ignore[assignment]
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run(base / "out", base / "missing.json", force=True)
        if rc != 2 or buf.getvalue().strip() != "error: --force requires --only":
            _fail(buf.getvalue() or str(rc))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run(
                base / "out", base / "missing.json", force=True, reparse=True, only=["3082"],
            )
        if rc != 2 or "error:" not in buf.getvalue() or "reparse" not in buf.getvalue():
            _fail(buf.getvalue() or str(rc))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mod.main(["--dest", str(base / "out"), "--force", "--config", str(base / "missing.json")])
        if rc != 2 or "error: --force requires --only" not in buf.getvalue():
            _fail(buf.getvalue() or str(rc))
        if calls:
            _fail(f"usage error touched network {calls}")
    finally:
        mod.http_get = orig_get
        mod.load_stock_product_map = orig_map

    cfg = base / "ir.json"
    cfg.write_text(
        json.dumps(
            {"html": [{
                "code": "2664",
                "name": "カワチ薬品",
                "index_url": "https://example.test/ir/",
                "title": "月次",
            }]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hits: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        hits.append(url)
        return b"<html></html>"

    orig_get = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {"26640": ("011", "カワチ薬品")}  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run(base / "out", cfg, only=["2664"], dry_run=True, force=True, today=date(2026, 9, 14))
        text = buf.getvalue()
        if rc != 0:
            _fail(text or str(rc))
        if hits:
            _fail(f"dry-run force downloaded {hits}")
        if " force=1" not in text or "dry-run 2664" not in text:
            _fail(text)
        if list((base / "out").rglob("*")):
            _fail("dry-run force wrote files")
    finally:
        mod.http_get = orig_get
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep


def test_eir_fetch_saves_item_html(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    url = eir_url("7616", 0)

    def fake_get(u: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        if u != url:
            raise RuntimeError(u)
        return EIR_JSONP.encode("utf-8")

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="7616",
            name="コロワイド",
            index_url="https://www.colowide.co.jp/ir/sale/",
            title="月次",
            source="eir",
            eir_code="7616",
            eir_n=0,
            eir_title_re="月次",
        )
        n, s, k, f = fetch_issuer(
            iss, dest, {"76160": ("011", "コロワイド")}, date(2026, 9, 14), dry_run=False
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"eir fetch {n, s, k, f}")
        html_path = dest / "7616" / "20260914_7616_コロワイド_月次.html"
        body = html_path.read_text(encoding="utf-8")
        if "eolparts" in body or "announcement_0" in body:
            _fail("saved jsonp instead of item html")
        if "既存店舗" not in body:
            _fail(body[:200])
        payload = json.loads(ingest_path(html_path).read_text(encoding="utf-8"))
        if payload["as_of"] != "2026-09-04":
            _fail(payload["as_of"])
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_unknown_stock_skip(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    def boom(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        raise RuntimeError(f"network {url}")

    orig = mod.http_get
    mod.http_get = boom  # type: ignore[assignment]
    try:
        iss = HtmlIssuer(
            code="2664",
            name="カワチ薬品",
            index_url="https://example.test/ir/",
            title="月次",
            source="html",
            eir_code="2664",
            eir_n=0,
            eir_title_re="月次",
        )
        n, s, k, f = fetch_issuer(
            iss, tmp / "monthly", {"72030": ("011", "トヨタ")}, date(2026, 9, 14), dry_run=False
        )
        if (n, s, k, f) != (0, 0, 1, 0):
            _fail(f"unknown-stock {n, s, k, f}")
    finally:
        mod.http_get = orig


def _http_err(url: str, code: int, msg: str, retry_after: str | None = None):
    from email.message import EmailMessage
    from urllib.error import HTTPError

    hdrs = None
    if retry_after is not None:
        hdrs = EmailMessage()
        hdrs["Retry-After"] = retry_after
    return HTTPError(url, code, msg, hdrs=hdrs, fp=None)


def _srs_issuer() -> HtmlIssuer:
    return HtmlIssuer(
        code="8163",
        name="ＳＲＳホールディングス",
        index_url="https://srsholdings.com/pages/ir-library-monthly/",
        title="月次",
        source="html",
        eir_code="8163",
        eir_n=0,
        eir_title_re="月次",
        parse=False,
    )


def test_fetch_issuer_retries_429_then_saves(tmp: Path) -> None:
    from unittest.mock import MagicMock, patch

    import _r2common
    import ir_monthly_html_r2 as mod

    url = "https://srsholdings.com/pages/ir-library-monthly/"
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(req.full_url, 429, "Too Many Requests")
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"<html>srs-ok</html>"
        cm.__exit__.return_value = False
        return cm

    orig_sleep = mod.SLEEP_SEC
    mod.SLEEP_SEC = 0
    slept: list[float] = []
    buf = io.StringIO()
    try:
        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch.object(_r2common.time, "sleep", slept.append),
            patch.object(_r2common.random, "random", return_value=0.5),
            redirect_stdout(buf),
        ):
            n, s, k, f = fetch_issuer(
                _srs_issuer(),
                tmp / "srs_ok",
                {"81630": ("011", "ＳＲＳホールディングス")},
                date(2026, 10, 2),
                dry_run=False,
            )
    finally:
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if (n, s, k, f) != (1, 1, 0, 0):
        _fail(f"429 then save {n, s, k, f} {out}")
    if calls["n"] != 2:
        _fail(f"429 then save calls {calls['n']}")
    if slept[:1] != [5.0]:
        _fail(f"429 then save wait {slept}")
    if "retry 429 wait=5.0s" not in out or url not in out:
        _fail(out)
    if "fail issuer 8163" in out:
        _fail(out)
    htmls = list((tmp / "srs_ok" / "8163").glob("*.html"))
    if len(htmls) != 1 or htmls[0].read_bytes() != b"<html>srs-ok</html>":
        _fail(f"saved html {htmls}")


def test_fetch_issuer_exhausted_429_is_skipped(tmp: Path) -> None:
    from unittest.mock import patch

    import _r2common
    import ir_monthly_html_r2 as mod

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        raise _http_err(req.full_url, 429, "Too Many Requests")

    orig_sleep = mod.SLEEP_SEC
    mod.SLEEP_SEC = 0
    slept: list[float] = []
    buf = io.StringIO()
    try:
        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch.object(_r2common.time, "sleep", slept.append),
            patch.object(_r2common.random, "random", return_value=0.5),
            redirect_stdout(buf),
        ):
            n, s, k, f = fetch_issuer(
                _srs_issuer(),
                tmp / "srs_fail",
                {"81630": ("011", "ＳＲＳホールディングス")},
                date(2026, 10, 2),
                dry_run=False,
            )
    finally:
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if (n, s, k, f) != (1, 0, 1, 0):
        _fail(f"exhausted 429 {n, s, k, f} {out}")
    if out.count("retry 429 ") != 5:
        _fail(out)
    if "skip issuer 8163 rate-limited: HTTP Error 429: Too Many Requests" not in out:
        _fail(out)
    if "fail issuer 8163" in out:
        _fail(out)
    if list((tmp / "srs_fail").rglob("*.html")):
        _fail("exhausted 429 must not write html")


def test_fetch_issuer_exhausted_404_stays_failed(tmp: Path) -> None:
    from unittest.mock import patch

    import ir_monthly_html_r2 as mod

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        raise _http_err(req.full_url, 404, "Not Found")

    orig_sleep = mod.SLEEP_SEC
    mod.SLEEP_SEC = 0
    buf = io.StringIO()
    try:
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), redirect_stdout(buf):
            n, s, k, f = fetch_issuer(
                _srs_issuer(),
                tmp / "srs_404",
                {"81630": ("011", "ＳＲＳホールディングス")},
                date(2026, 10, 3),
                dry_run=False,
            )
    finally:
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if (n, s, k, f) != (1, 0, 0, 1):
        _fail(f"404 {n, s, k, f} {out}")
    if "fail issuer 8163 fetch: HTTP Error 404: Not Found" not in out:
        _fail(out)
    if "rate-limited" in out:
        _fail(out)


def test_run_persistent_429_does_not_fail_job(tmp: Path) -> None:
    """1 社の永続 429 は skip。他社は保存し、failed=0 / exit=0。"""
    import ir_monthly_html_r2 as mod

    dest = tmp / "partial429"
    cfg = tmp / "ir.json"
    srs_url = "https://srsholdings.com/pages/ir-library-monthly/"
    kawa_url = "https://example.test/cawachi/"
    cfg.write_text(
        json.dumps(
            {
                "html": [
                    {
                        "code": "8163",
                        "name": "ＳＲＳホールディングス",
                        "index_url": srs_url,
                        "title": "月次",
                        "parse": False,
                    },
                    {
                        "code": "2664",
                        "name": "カワチ薬品",
                        "index_url": kawa_url,
                        "title": "月次",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        if url == srs_url:
            raise _http_err(url, 429, "Too Many Requests")
        if url == kawa_url:
            return CAWACHI_HTML.encode("utf-8")
        raise RuntimeError(url)

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {  # type: ignore[assignment]
        "81630": ("011", "ＳＲＳホールディングス"),
        "26640": ("011", "カワチ薬品"),
    }
    mod.SLEEP_SEC = 0
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = run(dest, cfg, today=date(2026, 10, 3))
    finally:
        mod.http_get = orig
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if rc != 0:
        _fail(f"partial 429 rc {rc} {out}")
    if "skip issuer 8163 rate-limited:" not in out:
        _fail(out)
    if "fail issuer 8163" in out:
        _fail(out)
    if "ir_monthly_html_r2 done listed=2 saved=1 skipped=1 failed=0" not in out:
        _fail(out)
    if srs_url not in calls or kawa_url not in calls:
        _fail(f"calls {calls}")
    if list((dest / "8163").rglob("*.html")):
        _fail("429 must not write html")
    if not list((dest / "2664").glob("*.html")):
        _fail("other issuer must save")
    err = _http_err(srs_url, 429, "Too Many Requests")
    if not is_rate_limited(err):
        _fail("is_rate_limited 429")
    if is_rate_limited(_http_err(srs_url, 404, "Not Found")):
        _fail("is_rate_limited 404")


def test_run_host_cooldown_skips_same_host(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "cooldown"
    cfg = tmp / "ir.json"
    host = "https://srsholdings.com"
    first = f"{host}/pages/ir-library-monthly/"
    second = f"{host}/pages/other-monthly/"
    cfg.write_text(
        json.dumps(
            {
                "html": [
                    {
                        "code": "8163",
                        "name": "ＳＲＳホールディングス",
                        "index_url": first,
                        "title": "月次",
                        "parse": False,
                    },
                    {
                        "code": "8164",
                        "name": "同一ホスト",
                        "index_url": second,
                        "title": "月次",
                        "parse": False,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        raise _http_err(url, 429, "Too Many Requests")

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {  # type: ignore[assignment]
        "81630": ("011", "ＳＲＳホールディングス"),
        "81640": ("011", "同一ホスト"),
    }
    mod.SLEEP_SEC = 0
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = run(dest, cfg, today=date(2026, 10, 3))
    finally:
        mod.http_get = orig
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if rc != 0:
        _fail(f"host-cooled rc {rc} {out}")
    if calls != [first]:
        _fail(f"second issuer must not fetch {calls}")
    if "skip issuer 8163 rate-limited:" not in out:
        _fail(out)
    if "skip issuer 8164 host-cooled srsholdings.com" not in out:
        _fail(out)
    if "ir_monthly_html_r2 done listed=2 saved=0 skipped=2 failed=0" not in out:
        _fail(out)


def test_run_404_still_exits_1(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "hardfail"
    cfg = tmp / "ir.json"
    cfg.write_text(
        json.dumps(
            {
                "html": [{
                    "code": "8163",
                    "name": "ＳＲＳホールディングス",
                    "index_url": "https://srsholdings.com/pages/ir-library-monthly/",
                    "title": "月次",
                    "parse": False,
                }]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        raise _http_err(url, 404, "Not Found")

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {"81630": ("011", "ＳＲＳホールディングス")}  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = run(dest, cfg, today=date(2026, 10, 3))
    finally:
        mod.http_get = orig
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep
    out = buf.getvalue()
    if rc != 1:
        _fail(f"404 rc {rc} {out}")
    if "ir_monthly_html_r2 done listed=1 saved=0 skipped=0 failed=1" not in out:
        _fail(out)
    if "fail issuer 8163 fetch: HTTP Error 404: Not Found" not in out:
        _fail(out)


def test_run_only_dry_run(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    cfg = tmp / "ir.json"
    cfg.write_text(
        json.dumps(
            {
                "html": [
                    {
                        "code": "2664",
                        "name": "カワチ薬品",
                        "index_url": "https://example.test/cawachi/",
                        "title": "月次",
                    },
                    {
                        "code": "2742",
                        "name": "ハローズ",
                        "index_url": "https://example.test/halows/",
                        "title": "月次",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        return b"<html></html>"

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {"26640": ("011", "カワチ"), "27420": ("011", "ハローズ")}  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        rc = run(tmp / "out", cfg, only=["2664"], dry_run=True, today=date(2026, 9, 14))
        if rc != 0:
            _fail(f"dry-run rc {rc}")
        if calls:
            _fail(f"dry-run must not fetch {calls}")
        if list((tmp / "out").rglob("*.html")):
            _fail("dry-run wrote html")
    finally:
        mod.http_get = orig
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep


def test_dumps_matches_ingest_shape() -> None:
    got = parse_issuer(CAWACHI_HTML, "26640")
    text = dumps_ingest(got)
    parsed = json.loads(text)
    if set(parsed) != {"code", "months", "rows", "as_of"}:
        _fail(f"keys {parsed.keys()}")
    if not isinstance(next(iter(parsed["rows"][0]["values"])), str):
        _fail("values keys must be strings in json")


MOS_HTML = """
<p>2026年4月～2027年3月（対前年比：%）</p>
<table>
<tr><th></th><th>既存店売上高</th><th>既存店客数</th><th>全店売上高</th></tr>
<tr><th>4月</th><td>102.6</td><td>102.7</td><td>102.0</td></tr>
<tr><th>8月</th><td>103.9</td><td>100.3</td><td>103.5</td></tr>
<tr><th>9月</th><td></td><td></td><td></td></tr>
<tr><th>上期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
<tr><th>3月</th><td></td><td></td><td></td></tr>
<tr><th>下期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
<tr><th>通期計</th><td>103.8</td><td>102.2</td><td>103.2</td></tr>
</table>
"""

WATAMI_HTML = """
<html><body>
<h2>2025年度 月次推移</h2>
<table>
<tr><td></td><td></td><td>2026.1</td><td>2026.2</td><td>2026.3</td><td>4Q</td><td>通年</td></tr>
<tr><td>全店 (全業態)</td><td>売上高（%）</td><td>110.1</td><td>106.1</td><td>106.7</td><td>107.7</td><td>107.5</td></tr>
<tr><td>全店 (全業態)</td><td>客数（%）</td><td>111.5</td><td>108.9</td><td>110.0</td><td>110.2</td><td>108.2</td></tr>
</table>
</body></html>
"""

WATAMI_KYOTEN_HTML = """
<html><body>
<h2>2024年度 月次推移</h2>
<p>既存店(売上高・客数比・客単価)前年比は業態転換店を含んでおります。</p>
<h4>宅食事業</h4>
<table>
<tr><td colspan="2"></td><th>2024.10</th><th>2024.11</th><th>2024.12</th>
<th>2025.1</th><th>2025.2</th><th>2025.3</th></tr>
<tr>
<th rowspan="2">拠点数</th>
<th>調理済み商品お届け数(※)（千食）</th>
<td>228</td><td>225</td><td>218</td><td>224</td><td>223</td><td>219</td>
</tr>
<tr>
<th>営業所数合計</th>
<td>516</td><td>516</td><td>516</td><td>516</td><td>515</td><td>508</td>
</tr>
</table>
</body></html>
"""

WATAMI_SECTIONS_HTML = """
<html><body>
<h2>2026年度 月次推移</h2>
<p>※既存店(売上高・客数比・客単価)前年比は業態転換店を含んでおります。</p>
<h3>国内外食事業(月次前年比推移)</h3>
<table>
<tr><td></td><td></td><td></td><td>2026.4</td><td>2026.5</td><td>2026.6</td><td>1Q</td></tr>
<tr><td>全店 (全業態)</td><td>全店 (全業態)</td><td>売上高（%）</td><td>107.8</td><td>117.6</td><td>103.3</td><td>108.1</td></tr>
<tr><td>国内外食 （サブウェイ事業除く）</td><td>全店</td><td>売上高（%）</td><td>100.4</td><td>104.0</td><td>97.6</td><td>100.8</td></tr>
</table>
<table>
<tr><td></td><td></td><td></td><td>2026.7</td><td>2026.8</td><td>2Q</td></tr>
<tr><td>全店 (全業態)</td><td>全店 (全業態)</td><td>売上高（%）</td><td>110.4</td><td>104.9</td></tr>
<tr><td>全店 (全業態)</td><td>全店 (全業態)</td><td>期末店舗数</td><td>525</td><td>527</td></tr>
<tr><td>国内外食 （サブウェイ事業除く）</td><td>全店</td><td>売上高（%）</td><td>103.6</td><td>97.5</td></tr>
<tr><td>国内外食 （サブウェイ事業除く）</td><td>既存店</td><td>売上高（%）</td><td>107.4</td><td>100.5</td></tr>
<tr><td>サブウェイ</td><td>全店</td><td>売上高（%）</td><td>128.0</td><td>125.2</td></tr>
<tr><td>サブウェイ</td><td>既存店</td><td>対象店舗数</td><td>174</td><td>174</td></tr>
</table>
<h3>海外事業</h3>
<table>
<tr><td></td><td></td><td>2026.1</td><td>2026.7</td><td>2026.8</td></tr>
<tr><td>店舗数</td><td>新規出店数</td><td>2</td><td>1</td><td>1</td></tr>
<tr><td>店舗数</td><td>転換出店数</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>店舗数</td><td>撤退数</td><td>0</td><td>0</td><td>3</td></tr>
<tr><td>店舗数</td><td>店舗数合計</td><td>83</td><td>84</td><td>82</td></tr>
</table>
<h3>宅食事業</h3>
<table>
<tr><td></td><td></td><td>2026.4</td><td>2026.7</td><td>2026.8</td></tr>
<tr><td>拠点数</td><td>調理済み商品お届け数(※)（千食）</td><td>231</td><td>238</td><td>241</td></tr>
<tr><td>拠点数</td><td>新規開設数</td><td>-</td><td>1</td><td>-</td></tr>
<tr><td>拠点数</td><td>撤退数</td><td>-</td><td>0</td><td>-</td></tr>
<tr><td>拠点数</td><td>営業所数合計</td><td>506</td><td>507</td><td>507</td></tr>
</table>
</body></html>
"""

WATAMI_QUARTER_BANNER_HTML = """
<html><body>
<h2>2026年度 月次推移</h2>
<p>※既存店(売上高・客数比・客単価)前年比は業態転換店を含んでおります。</p>
<h3>国内外食事業（月次前年比推移）</h3>
<table>
<tr>
<td colspan="3" class="quarter">【第2四半期】</td>
<th>2026.7</th><th>2026.8</th><th>2026.9</th>
<th>2Q</th><th>半期</th>
</tr>
<tr><td>全店 (全業態)</td><td>全店 (全業態)</td><td>売上高（%）</td><td>110.4</td><td>104.9</td><td></td><td></td><td></td></tr>
<tr><td>国内外食 （サブウェイ事業除く）</td><td>既存店</td><td>売上高（%）</td><td>107.4</td><td>100.5</td><td></td><td></td><td></td></tr>
<tr><td>サブウェイ</td><td>全店</td><td>売上高（%）</td><td>128.0</td><td>125.2</td><td></td><td></td><td></td></tr>
</table>
<h3>海外事業</h3>
<table>
<tr><td></td><td></td><td>2026.6</td><td>2026.7</td><td>2026.8</td></tr>
<tr><td>店舗数</td><td>店舗数合計</td><td>83</td><td>84</td><td>82</td></tr>
</table>
<h3>宅食事業</h3>
<table>
<tr><td></td><td></td><td>2026.6</td><td>2026.7</td><td>2026.8</td></tr>
<tr><td>拠点数</td><td>営業所数合計</td><td>506</td><td>507</td><td>507</td></tr>
</table>
</body></html>
"""

HURXLEY_SECTIONS_HTML = """
<html><body>
<p>ほっかほっか亭 店舗流通ネット株式会社</p>
<h2>ほっかほっか亭</h2>
<div class="u-select-year">
<select>
<option value="year_2027年3月期">2027年3月期</option>
<option value="year_2026年3月期">2026年3月期</option>
</select>
</div>
<table>
<tr>
<th colspan="2">2027年3月期</th>
<th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th>
</tr>
<tr>
<th rowspan="2">売上(前年同月比:%)</th>
<th>全店</th>
<td>88.6</td><td>92.7</td><td>90.0</td><td>92.3</td><td>89.4</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th>既存店</th>
<td>92.0</td><td>94.7</td><td>91.1</td><td>94.1</td><td>91.3</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th colspan="2">店舖数(店)</th>
<td>713</td><td>714</td><td>713</td><td>711</td><td>710</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th colspan="2">出店(店)</th>
<td>2</td><td>3</td><td>1</td><td>1</td><td>0</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th colspan="2">閉店(店)</th>
<td>5</td><td>2</td><td>2</td><td>3</td><td>1</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
</table>
<table>
<tr>
<th colspan="2">2026年3月期</th>
<th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th>
</tr>
<tr>
<th rowspan="2">売上(前年同月比:%)</th>
<th>全店</th>
<td>101.9</td><td>98.7</td><td>96.4</td><td>92.2</td><td>95.5</td>
<td>92.8</td><td>93.5</td><td>96.4</td><td>89.8</td><td>89.9</td><td>93.4</td><td>89.5</td>
</tr>
<tr>
<th>既存店</th>
<td>104.1</td><td>102.7</td><td>101.3</td><td>96.8</td><td>99.8</td>
<td>97.6</td><td>98.4</td><td>101.3</td><td>96.2</td><td>93.9</td><td>99.9</td><td>89.5</td>
</tr>
<tr>
<th colspan="2">店舖数(店)</th>
<td>810</td><td>798</td><td>793</td><td>793</td><td>792</td>
<td>791</td><td>788</td><td>788</td><td>785</td><td>784</td><td>786</td><td>716</td>
</tr>
</table>
<h2>店舗流通ネット株式会社</h2>
<div class="u-select-year">
<select>
<option value="year_2027年3月期">2027年3月期</option>
<option value="year_2026年3月期">2026年3月期</option>
</select>
</div>
<table>
<tr>
<th colspan="2">2027年3月期</th>
<th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th><th>3月</th>
</tr>
<tr>
<th colspan="2">稼働店舖数(①+②) (店)</th>
<td>1,032</td><td>1,039</td><td>1,041</td><td>1,050</td><td>1,063</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th colspan="2">① 店舗不動産の<br>管理テナント数 (店)</th>
<td>167</td><td>170</td><td>171</td><td>171</td><td>177</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th colspan="2">② 店舖リースの<br>取引店舖数 (店)</th>
<td>865</td><td>869</td><td>870</td><td>879</td><td>886</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th></th><th>増加(店)</th>
<td>13</td><td>20</td><td>13</td><td>16</td><td>15</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
<tr>
<th></th><th>減少(店)</th>
<td>11</td><td>16</td><td>12</td><td>7</td><td>8</td>
<td></td><td></td><td></td><td></td><td></td><td></td><td></td>
</tr>
</table>
</body></html>
"""

WATAMI_FY2024_HTML = """
<html><body>
<h2>2024年度 月次推移</h2>
<p>※SUBWAY事業の数値は除いております。 ※上記の数値はフランチャイズ店舗を除いております。</p>
<h3>国内外食事業</h3>
<table>
<tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td><td>4Q</td></tr>
<tr><td>既存店</td><td>売上高前年比（%）</td><td>109.2</td><td>103.9</td><td>105.3</td><td>106.1</td></tr>
<tr><td>全店合計</td><td>売上高前年比（%）</td><td>104.0</td><td>99.0</td><td>100.3</td><td>101.1</td></tr>
</table>
<h3>海外事業</h3>
<table>
<tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
<tr><td>店舗数</td><td>店舗数合計</td><td>72</td><td>71</td><td>72</td></tr>
</table>
<h3>宅食事業</h3>
<table>
<tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
<tr><td>拠点数</td><td>調理済み商品お届け数(※)（千食）</td><td>224</td><td>223</td><td>219</td></tr>
<tr><td>拠点数</td><td>営業所数合計</td><td>516</td><td>515</td><td>508</td></tr>
</table>
</body></html>
"""

WATAMI_NAV_PERIOD_HTML = """
<html><body>
<p>国内外食事業 海外事業 宅食事業</p>
<h2>2024年度 月次推移</h2>
<h3>国内外食事業</h3>
<table>
<tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
<tr><td>既存店</td><td>売上高前年比（%）</td><td>109.2</td><td>103.9</td><td>105.3</td></tr>
<tr><td>全店合計</td><td>店舗数合計</td><td>188</td><td>186</td><td>186</td></tr>
</table>
<table>
<tr><td></td><td></td><td>2024.10</td><td>2024.11</td><td>2024.12</td></tr>
<tr><td>既存店</td><td>売上高前年比（%）</td><td>105.2</td><td>112.2</td><td>106.1</td></tr>
</table>
<h3>海外事業</h3>
<table>
<tr><td></td><td></td><td>2024.1</td><td>2024.2</td><td>2024.6</td></tr>
<tr><td>店舗数</td><td>店舗数合計</td><td>57</td><td>57</td><td>62</td></tr>
</table>
<table>
<tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
<tr><td>店舗数</td><td>店舗数合計</td><td>72</td><td>71</td><td>72</td></tr>
</table>
<h3>宅食事業</h3>
<table>
<tr><td></td><td></td><td>2024.4</td><td>2024.9</td></tr>
<tr><td>拠点数</td><td>調理済み商品お届け数(※)（千食）</td><td>209</td><td>226</td></tr>
</table>
<table>
<tr><td></td><td></td><td>2024.10</td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
<tr><td>拠点数</td><td>調理済み商品お届け数(※)（千食）</td><td>228</td><td>224</td><td>223</td><td>219</td></tr>
<tr><td>拠点数</td><td>営業所数合計</td><td>516</td><td>516</td><td>515</td><td>508</td></tr>
</table>
</body></html>
"""

KAPPA_HTML = """
<html><body>
<h3>国内回転寿司事業 月次売上推移速報</h3>
<table>
<tr><td rowspan="2">2026年度</td><td colspan="3">既存店舗</td><td colspan="3">全店舗</td></tr>
<tr><td>売上高</td><td>客数</td><td>客単価</td><td>売上高</td><td>客数</td><td>客単価</td></tr>
<tr><td>4月</td><td>94.0%</td><td>99.3%</td><td>94.7%</td><td>95.4%</td><td>100.7%</td><td>94.7%</td></tr>
<tr><td>8月</td><td>98.1%</td><td>99.6%</td><td>98.5%</td><td>97.7%</td><td>99.2%</td><td>98.4%</td></tr>
<tr><td>累計</td><td>98.6%</td><td>101.7%</td><td>96.9%</td><td>99.5%</td><td>101.8%</td><td>97.7%</td></tr>
</table>
</body></html>
"""

TSURUHA_HTML = """
<html><body>
<p>1. ツルハグループの既存店売上は「ドラッグ(物販)・調剤」の内訳を記載。</p>
<p>2. 開店、閉店の状況、グループ店舗数内訳は割愛。</p>
<h2>連結月次前年対比伸び率</h2>
<p>2027年２月期 (2026年3月1日～2027年2月28日)</p>
<p>単位:%</p>
<table>
<tr><th>ツルハグループ</th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>既存店</td><td>単月売上</td><td>3.3</td><td>5.0</td><td>4.6</td><td>▲ 1.1</td><td>1.7</td><td>1.4</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>既存店</td><td>（内 物販）</td><td>1.5</td><td>3.8</td><td>4.8</td><td>▲ 3.8</td><td>0.0</td><td>0.2</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>既存店</td><td>（内 調剤）</td><td>11.2</td><td>10.0</td><td>3.9</td><td>11.4</td><td>9.0</td><td>7.0</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>全店</td><td>客単価</td><td>2.9</td><td>3.9</td><td>2.2</td><td>3.1</td><td>3.5</td><td>3.7</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>店舗状況</td><td>店舗数</td><td>5663</td><td>5665</td><td>5665</td><td>5663</td><td>5660</td><td>5652</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>店舗状況</td><td>（内 開店）</td><td>17</td><td>14</td><td>5</td><td>3</td><td>8</td><td>6</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>店舗状況</td><td>（内 閉店）</td><td>30</td><td>12</td><td>5</td><td>5</td><td>11</td><td>14</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
<p>（注）既存店は開店後またはグループ加入後13ヶ月を経過した店舗を対象。</p>
<h2>参考:経営統合前月次営業速報(各社従来ベース)</h2>
<p>月次前年対比伸び率 2027年２月期 (2026年3月1日～2027年2月28日)</p>
<p>単位:%</p>
<table>
<tr><th>統合前ツルハグループ</th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>既存店</td><td>単月売上</td><td>2.9</td><td>4.0</td><td>3.5</td><td>▲ 1.9</td><td>▲ 0.1</td><td>0.9</td></tr>
<tr><td>既存店</td><td>（内 物販）</td><td>1.3</td><td>2.9</td><td>3.2</td><td>▲ 4.1</td><td>▲ 1.5</td><td>▲ 0.3</td></tr>
<tr><td>全店</td><td>単月売上</td><td>4.3</td><td>5.1</td><td>4.2</td><td>▲ 0.9</td><td>0.8</td><td>1.8</td></tr>
<tr><td>店舗状況</td><td>店舗数</td><td>2688</td><td>2691</td><td>2693</td><td>2691</td><td>2693</td><td>2694</td></tr>
<tr><td>店舗状況</td><td>（内 開店）</td><td>6</td><td>6</td><td>3</td><td>2</td><td>4</td><td>3</td></tr>
<tr><td>店舗状況</td><td>（内 閉店）</td><td>20</td><td>3</td><td>1</td><td>4</td><td>2</td><td>2</td></tr>
</table>
<table>
<tr><th>統合前ウエルシアグループ</th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>既存店</td><td>単月売上</td><td>3.7</td><td>5.8</td><td>5.6</td><td>▲ 0.4</td><td>3.2</td><td>2.1</td></tr>
<tr><td>店舗状況</td><td>店舗数</td><td>2975</td><td>2974</td><td>2972</td><td>2972</td><td>2967</td><td>2958</td></tr>
</table>
</body></html>
"""

ARCS_HTML = """
<html><body>
<h3>2027年２月期（2026.03.01～2027.02.28）</h3>
<table>
<tr><td></td><td></td><td>3月</td><td>4月</td><td>5月</td><td>3～5 月計</td><td>6月</td><td>7月</td></tr>
<tr><td>売上高</td><td>全店</td><td>100.8</td><td>102.2</td><td>105.2</td><td>102.7</td><td>99.0</td><td>100.7</td></tr>
<tr><td>売上高</td><td>既存店</td><td>100.6</td><td>102.3</td><td>105.3</td><td>102.7</td><td>98.7</td><td>100.2</td></tr>
</table>
</body></html>
"""


def test_mos_skips_half_year_total() -> None:
    got = parse_issuer(MOS_HTML, "8153")
    if got["as_of"] != "2026-08-31":
        _fail(f"mos as_of {got['as_of']}")
    sales = _row(got, "既存店売上高")
    if sales["unit"] != "yoy_index":
        _fail(f"mos unit {sales}")
    if sales["values"] != {"4": 102.6, "8": 103.9}:
        _fail(f"上期計 leaked {sales['values']}")
    if "9" in sales["values"] or "3" in sales["values"]:
        _fail(f"empty/total months kept as values {sales['values']}")


def test_year_dot_month_and_skip_quarter() -> None:
    if parse_month_cell("2026.1") != 1 or parse_month_cell("2026.10") != 10:
        _fail("YYYY.M")
    if parse_month_year("26.03") != (2026, 3):
        _fail("YY.MM full cell")
    if parse_month_cell("2026.03.01") is not None:
        _fail("date range is not a month")
    got = parse_7522(WATAMI_HTML)
    if got["months"] != [1, 2, 3]:
        _fail(f"watami months {got['months']}")
    sales = _row(got, "全店 (全業態) 売上高（%）")
    if sales["values"] != {"1": 110.1, "2": 106.1, "3": 106.7}:
        _fail(f"4Q/通年 leaked {sales['values']}")
    if sales["unit"] != "yoy_index":
        _fail(f"watami unit {sales}")


def test_watami_kyoten_count_is_number() -> None:
    got = parse_7522(WATAMI_KYOTEN_HTML)
    meals = _row(got, "宅食 調理済み商品お届け数(※)（千食）", unit="number")
    if meals["unit"] != "number":
        _fail(f"お届け数 unit {meals}")
    if meals["values"].get("12") != 218:
        _fail(f"お届け数 values {meals['values']}")
    if "売上" in meals["element"] or "前年" in meals["element"]:
        _fail(f"拠点数 must not get yoy rename {meals}")
    offices = _row(got, "宅食 営業所数合計", unit="number")
    if offices["unit"] != "number" or offices["values"].get("12") != 516:
        _fail(f"営業所数 {offices}")


def test_watami_prefers_sales_over_kyoten() -> None:
    pad = "あ" * 400
    html = f"""
    <html><body>
    <h2>2024年度 月次推移</h2>
    <p>既存店(売上高・客数比・客単価)前年比は業態転換店を含んでおります。</p>
    <h4>国内外食事業</h4>
    <table>
    <tr><td></td><td></td><td>2025.1</td><td>2025.2</td><td>2025.3</td></tr>
    <tr><td>全店合計</td><td>売上高前年比（%）</td><td>104.0</td><td>99.0</td><td>100.3</td></tr>
    <tr><td>全店合計</td><td>店舗数合計</td><td>188</td><td>186</td><td>186</td></tr>
    </table>
    <p>{pad}</p>
    <h4>宅食事業</h4>
    <table>
    <tr><td colspan="2"></td><th>2024.10</th><th>2024.11</th><th>2024.12</th>
    <th>2025.1</th><th>2025.2</th><th>2025.3</th></tr>
    <tr>
    <th rowspan="2">拠点数</th>
    <th>調理済み商品お届け数(※)（千食）</th>
    <td>228</td><td>225</td><td>218</td><td>224</td><td>223</td><td>219</td>
    </tr>
    <tr>
    <th>営業所数合計</th>
    <td>516</td><td>516</td><td>516</td><td>516</td><td>515</td><td>508</td>
    </tr>
    </table>
    </body></html>
    """
    got = parse_7522(html)
    sales = _row(got, "全店合計 売上高前年比（%）")
    if sales["unit"] != "yoy_index" or sales["values"].get("1") != 104:
        _fail(f"picked kyoten {got['rows']}")
    if any("拠点数" in r["element"] for r in got["rows"]):
        _fail(f"kyoten table won {got['rows']}")


def _watami_stored(element: str, unit: str, *, year: int = 2026) -> str:
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    name = canonicalize_element_label(element, unit)
    name = apply_issuer_aliases("75220", name, year=year)
    return canonicalize_element_label(name, unit)


def test_watami_sections_prefix_and_count_units() -> None:
    got = parse_7522(WATAMI_SECTIONS_HTML)
    if got["as_of"] != "2026-08-31":
        _fail(f"watami sections as_of {got['as_of']}")
    if got["months"] != [1, 4, 7, 8]:
        _fail(f"watami sections months {got['months']}")
    sales = _row(got, "全店 (全業態) 売上高（%）")
    if sales["values"] != {"7": 110.4, "8": 104.9}:
        _fail(f"picked Q1 or leaked 2Q {sales['values']}")
    if any(
        "売上高前年同月比" in r["element"]
        for r in got["rows"]
        if "宅食" in r["element"] or "お届け" in r["element"]
    ):
        _fail(f"宅食 yoy rename {got['rows']}")
    meals = _row(got, "宅食 調理済み商品お届け数(※)（千食）", unit="number")
    if meals["values"].get("8") != 241 or meals["values"].get("4") != 231:
        _fail(f"watami meals {meals}")
    if "売上高" in meals["element"] or "前年" in meals["element"]:
        _fail(f"meals must not get yoy rename {meals}")
    offices = _row(got, "宅食 営業所数合計", unit="number")
    if offices["values"].get("8") != 507:
        _fail(f"watami offices {offices}")
    opened = _row(got, "宅食 新規開設数", unit="number")
    if opened["values"].get("7") != 1:
        _fail(f"watami 開設 {opened}")
    overseas = _row(got, "海外 店舗数 店舗数合計", unit="number")
    if overseas["values"] != {"1": 83, "7": 84, "8": 82}:
        _fail(f"watami overseas stores {overseas}")
    overseas_open = _row(got, "海外 店舗数 新規出店数", unit="number")
    if overseas_open["values"].get("1") != 2:
        _fail(f"watami overseas open {overseas_open}")
    convert = _row(got, "海外 店舗数 転換出店数", unit="number")
    if convert["values"].get("8") != 0:
        _fail(f"watami convert {convert}")
    if _watami_stored(sales["element"], sales["unit"]) != "国内外食 全店 売上高(前年比)":
        _fail(f"alias 全業態 {_watami_stored(sales['element'], sales['unit'])}")
    stores_all = _row(got, "全店 (全業態) 期末店舗数", unit="number")
    if _watami_stored(stores_all["element"], stores_all["unit"]) != "国内外食 全店 店舗数":
        _fail(f"alias 全業態 店舗数 {_watami_stored(stores_all['element'], stores_all['unit'])}")
    ex = _row(got, "国内外食 （サブウェイ事業除く） 既存店 売上高（%）")
    if _watami_stored(ex["element"], ex["unit"]) != "国内外食 サブウェイ除く 既存 売上高(前年比)":
        _fail(f"alias 除く {_watami_stored(ex['element'], ex['unit'])}")
    sub = _row(got, "サブウェイ 全店 売上高（%）")
    if _watami_stored(sub["element"], sub["unit"]) != "国内外食 サブウェイ 全店 売上高(前年比)":
        _fail(f"alias subway {_watami_stored(sub['element'], sub['unit'])}")
    if _watami_stored(meals["element"], meals["unit"]) != "宅食 調理済み商品お届け数（千食）":
        _fail(f"alias meals {_watami_stored(meals['element'], meals['unit'])}")
    if _watami_stored(opened["element"], opened["unit"]) != "宅食 出店":
        _fail(f"alias 開設 {_watami_stored(opened['element'], opened['unit'])}")
    if _watami_stored(overseas["element"], overseas["unit"]) != "海外 店舗数":
        _fail(f"alias overseas {_watami_stored(overseas['element'], overseas['unit'])}")
    if _watami_stored(convert["element"], convert["unit"]) != "海外 転換出店":
        _fail(f"alias convert {_watami_stored(convert['element'], convert['unit'])}")
    targets = _row(got, "サブウェイ 既存店 対象店舗数", unit="number")
    if _watami_stored(targets["element"], targets["unit"]) != "国内外食 サブウェイ 既存 店舗数":
        _fail(f"alias 対象 {_watami_stored(targets['element'], targets['unit'])}")


def test_watami_quarter_banner_keeps_sales_labels() -> None:
    got = parse_7522(WATAMI_QUARTER_BANNER_HTML)
    sales = _row(got, "全店 (全業態) 売上高（%）")
    if sales["unit"] != "yoy_index" or sales["values"] != {"7": 110.4, "8": 104.9}:
        _fail(f"quarter banner dropped 全店 sales {sales}")
    existing = _row(got, "国内外食 （サブウェイ事業除く） 既存店 売上高（%）")
    if existing["unit"] != "yoy_index" or existing["values"] != {"7": 107.4, "8": 100.5}:
        _fail(f"quarter banner dropped 既存店 sales {existing}")
    subway = _row(got, "サブウェイ 全店 売上高（%）")
    if subway["values"] != {"7": 128.0, "8": 125.2}:
        _fail(f"quarter banner dropped subway sales {subway}")
    overseas = _row(got, "海外 店舗数 店舗数合計", unit="number")
    if overseas["values"] != {"6": 83, "7": 84, "8": 82}:
        _fail(f"overseas counts {overseas}")
    offices = _row(got, "宅食 営業所数合計", unit="number")
    if offices["values"] != {"6": 506, "7": 507, "8": 507}:
        _fail(f"宅食 counts {offices}")


def test_hurxley_dual_brand_prefixes_and_count_units() -> None:
    got = parse_7561(HURXLEY_SECTIONS_HTML)
    if got["as_of"] != "2026-08-31":
        _fail(f"hurxley as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"hurxley months {got['months']}")
    all_store = _row(got, "ほっかほっか亭 売上(前年同月比:%) 全店")
    if all_store["unit"] != "yoy_index":
        _fail(f"hokka sales unit {all_store}")
    if all_store["values"] != {"4": 88.6, "5": 92.7, "6": 90, "7": 92.3, "8": 89.4}:
        _fail(f"hokka all-store {all_store['values']}")
    exist = _row(got, "ほっかほっか亭 売上(前年同月比:%) 既存店")
    if exist["unit"] != "yoy_index" or exist["values"].get("4") != 92:
        _fail(f"hokka existing {exist}")
    store_hits = [
        r
        for r in got["rows"]
        if r["element"] in ("ほっかほっか亭 店舖数(店)", "ほっかほっか亭 店舗数(店)")
        and r["unit"] == "number"
    ]
    if len(store_hits) != 1:
        _fail(f"hokka stores {got['rows']}")
    if store_hits[0]["values"] != {"4": 713, "5": 714, "6": 713, "7": 711, "8": 710}:
        _fail(f"hokka store values {store_hits[0]['values']}")
    opened = _row(got, "ほっかほっか亭 出店(店)", unit="number")
    if opened["values"].get("4") != 2:
        _fail(f"hokka open {opened}")
    closed = _row(got, "ほっかほっか亭 閉店(店)", unit="number")
    if closed["values"].get("4") != 5:
        _fail(f"hokka close {closed}")
    active = _row(got, "店舗流通ネット 稼働店舖数(①+②) (店)", unit="number")
    if active["values"] != {"4": 1032, "5": 1039, "6": 1041, "7": 1050, "8": 1063}:
        _fail(f"trn active {active}")
    tenants = [
        r
        for r in got["rows"]
        if r["unit"] == "number"
        and str(r["element"]).startswith("店舗流通ネット")
        and "テナント数" in str(r["element"])
    ]
    if len(tenants) != 1 or tenants[0]["values"].get("8") != 177:
        _fail(f"trn tenants {got['rows']}")
    traded = [
        r
        for r in got["rows"]
        if r["unit"] == "number"
        and str(r["element"]).startswith("店舗流通ネット")
        and "取引店" in str(r["element"]).replace("舖", "舗")
    ]
    if len(traded) != 1 or traded[0]["values"].get("8") != 886:
        _fail(f"trn traded {got['rows']}")
    plus = _row(got, "店舗流通ネット 増加(店)", unit="number")
    if plus["values"].get("5") != 20:
        _fail(f"trn plus {plus}")
    minus = _row(got, "店舗流通ネット 減少(店)", unit="number")
    if minus["values"].get("4") != 11:
        _fail(f"trn minus {minus}")
    if any(not str(r["element"]).startswith(("ほっかほっか亭 ", "店舗流通ネット ")) for r in got["rows"]):
        _fail(f"unprefixed {got['rows']}")
    archive_vals = {101.9, 810, 716}
    for row in got["rows"]:
        overlap = archive_vals.intersection((row.get("values") or {}).values())
        if overlap:
            _fail(f"FY2026 archive leaked {row}")


def _hurxley_stored(element: str, unit: str, *, year: int = 2026) -> str:
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    name = canonicalize_element_label(element, unit)
    name = apply_issuer_aliases("75610", name, year=year)
    return canonicalize_element_label(name, unit)


def test_hurxley_canonicalize_aliases() -> None:
    # parser raw → canonicalize → 75610 alias → ops final (no extra parser 名寄せ)
    cases = [
        ("ほっかほっか亭 売上(前年同月比:%) 全店", "yoy_index", "ほっかほっか亭 全店 売上高(前年比)"),
        ("ほっかほっか亭 売上(前年同月比:%) 既存店", "yoy_index", "ほっかほっか亭 既存 売上高(前年比)"),
        ("ほっかほっか亭 店舖数(店)", "number", "ほっかほっか亭 店舗数"),
        ("ほっかほっか亭 出店(店)", "number", "ほっかほっか亭 出店"),
        ("ほっかほっか亭 閉店(店)", "number", "ほっかほっか亭 退店"),
        ("店舗流通ネット 稼働店舖数(①+②) (店)", "number", "店舗流通ネット 稼働店舗数"),
        ("店舗流通ネット ① 店舗不動産の 管理テナント数 (店)", "number", "店舗流通ネット 稼働店舗数 管理テナント数"),
        ("店舗流通ネット ② 店舖リースの 取引店舖数 (店)", "number", "店舗流通ネット 稼働店舗数 取引店舗数"),
        ("店舗流通ネット 増加(店)", "number", "店舗流通ネット 稼働店舗数 増加"),
        ("店舗流通ネット 減少(店)", "number", "店舗流通ネット 稼働店舗数 減少"),
        # previous flat ops names also fold into the parent/child form
        ("店舗流通ネット 管理テナント数", "number", "店舗流通ネット 稼働店舗数 管理テナント数"),
        ("店舗流通ネット 取引 店舗数", "number", "店舗流通ネット 稼働店舗数 取引店舗数"),
        ("店舗流通ネット 増加", "number", "店舗流通ネット 稼働店舗数 増加"),
        ("店舗流通ネット 減少", "number", "店舗流通ネット 稼働店舗数 減少"),
        ("店舗流通ネット 稼働 店舗数", "number", "店舗流通ネット 稼働店舗数"),
    ]
    for src, unit, want in cases:
        got = _hurxley_stored(src, unit)
        if got != want:
            _fail(f"hurxley alias {src!r} → {got!r}, want {want!r}")
    parent = _hurxley_stored("店舗流通ネット 稼働店舖数(①+②) (店)", "number")
    from app.monthly.ingest import monthly_element_label

    lab = monthly_element_label(parent, "number")
    if lab["group"] != "店舗流通ネット" or lab["metric"] != "稼働店舗数" or lab["breakdown"]:
        _fail(f"hurxley TRN parent label {lab}")
    child = _hurxley_stored("店舗流通ネット 増加(店)", "number")
    lab_c = monthly_element_label(child, "number")
    if (
        lab_c["group"] != "店舗流通ネット"
        or lab_c["breakdown"] != "増加"
        or lab_c["breakdown_depth"] != 1
    ):
        _fail(f"hurxley TRN child label {lab_c}")


def test_watami_canonicalize_aliases() -> None:
    cases = [
        ("全店 (全業態) 売上高（%）", "yoy_index", "国内外食 全店 売上高(前年比)"),
        (
            "国内外食 （サブウェイ事業除く） 既存店 売上高（%）",
            "yoy_index",
            "国内外食 サブウェイ除く 既存 売上高(前年比)",
        ),
        ("既存店 売上高前年比（%）", "yoy_index", "国内外食 サブウェイ除く 既存 売上高(前年比)"),
        ("全店 (全業態) 期末店舗数", "number", "国内外食 全店 店舗数"),
        (
            "国内外食 （サブウェイ事業除く） 全店 期末店舗数",
            "number",
            "国内外食 サブウェイ除く 全店 店舗数",
        ),
        ("サブウェイ 全店 期末店舗数", "number", "国内外食 サブウェイ 全店 店舗数"),
        ("全店合計 店舗数合計", "number", "国内外食 サブウェイ除く 全店 店舗数"),
        (
            "拠点数 調理済み商品お届け数(※)（千食）",
            "number",
            "宅食 調理済み商品お届け数（千食）",
        ),
    ]
    for src, unit, want in cases:
        got = _watami_stored(src, unit)
        if got != want:
            _fail(f"alias {src!r} → {got!r}, want {want!r}")
        if len(got) > 50:
            _fail(f"alias dest longer than 50: {got!r}")
    all_brand = _watami_stored("全店 (全業態) 売上高（%）", "yoy_index")
    if "サブウェイ除く" in all_brand:
        _fail(f"全業態 must not become サブウェイ除く {all_brand}")


def test_watami_nav_does_not_prefix_domestic_or_mix_years() -> None:
    got = parse_7522(WATAMI_NAV_PERIOD_HTML)
    if got["as_of"] != "2025-03-31":
        _fail(f"nav as_of {got['as_of']}")
    exist = _row(got, "既存店 売上高前年比（%）")
    if exist["values"] != {"1": 109.2, "2": 103.9, "3": 105.3}:
        _fail(f"picked Q3 or prefixed 宅食 {exist}")
    if any(str(r.get("element") or "").startswith("宅食 既存") for r in got["rows"]):
        _fail(f"nav 宅食 leaked onto 国内外食 {got['rows']}")
    overseas = _row(got, "海外 店舗数 店舗数合計", unit="number")
    if overseas["values"] != {"1": 72, "2": 71, "3": 72}:
        _fail(f"2024 H1 海外 mixed into Q4 {overseas}")
    meals = _row(got, "宅食 調理済み商品お届け数(※)（千食）", unit="number")
    if meals["values"].get("1") != 224 or meals["values"].get("10") != 228:
        _fail(f"same-as_of 宅食 {meals}")
    if meals["values"].get("4") == 209 or meals["values"].get("9") == 226:
        _fail(f"earlier 宅食 H1 mixed {meals}")
    if _watami_stored(exist["element"], exist["unit"], year=2025) != (
        "国内外食 サブウェイ除く 既存 売上高(前年比)"
    ):
        _fail(f"nav alias {_watami_stored(exist['element'], exist['unit'], year=2025)}")


def test_watami_fy2024_bare_names_alias_to_subway_excluded() -> None:
    got = parse_7522(WATAMI_FY2024_HTML)
    exist = _row(got, "既存店 売上高前年比（%）")
    if exist["values"] != {"1": 109.2, "2": 103.9, "3": 105.3}:
        _fail(f"fy2024 exist {exist}")
    stored = _watami_stored(exist["element"], exist["unit"], year=2025)
    if stored != "国内外食 サブウェイ除く 既存 売上高(前年比)":
        _fail(f"fy2024 alias {stored}")
    all_store = _row(got, "全店合計 売上高前年比（%）")
    stored_all = _watami_stored(all_store["element"], all_store["unit"], year=2025)
    if stored_all != "国内外食 サブウェイ除く 全店 売上高(前年比)":
        _fail(f"fy2024 all alias {stored_all}")
    meals = _row(got, "宅食 調理済み商品お届け数(※)（千食）", unit="number")
    if meals["values"].get("1") != 224:
        _fail(f"fy2024 meals {meals}")


def test_nendo_and_fullwidth_fy_as_of() -> None:
    got = parse_issuer(KAPPA_HTML, "7421")
    if got["as_of"] != "2026-08-31":
        _fail(f"kappa 2026年度 + 8月 {got['as_of']}")
    exist = _row(got, "既存店舗 売上高")
    if exist["unit"] != "yoy_index" or exist["values"].get("8") != 98.1:
        _fail(f"kappa {exist}")
    if any("累計" in r["element"] for r in got["rows"]):
        _fail("累計 row leaked")
    got2 = parse_issuer(TSURUHA_HTML, "3391", table=None)
    if got2["as_of"] != "2026-08-31":
        _fail(f"tsuruha fullwidth 月期 {got2['as_of']}")
    sales = _row(got2, "既存店 単月売上")
    if sales["unit"] != "yoy_pct" or sales["values"].get("6") != -1.1:
        _fail(f"tsuruha {sales}")
    if sales["values"].get("3") != 3.3:
        _fail(f"tsuruha must pick 連結 not 統合前 {sales['values']}")
    goods = _row(got2, "既存店 （内 物販） 売上高前年同月比")
    if goods["unit"] != "yoy_pct" or goods["values"].get("6") != -3.8:
        _fail(f"tsuruha goods {goods}")
    pharm = _row(got2, "既存店 （内 調剤） 売上高前年同月比")
    if pharm["unit"] != "yoy_pct" or pharm["values"].get("8") != 7.0:
        _fail(f"tsuruha 調剤 must be yoy_pct (伸び率) {pharm}")
    guest = _row(got2, "全店 客単価")
    if guest["unit"] != "yoy_pct" or guest["values"].get("8") != 3.7:
        _fail(f"tsuruha 客単価 must be yoy_pct (伸び率) {guest}")
    stores = _row(got2, "店舗数")
    if stores["unit"] != "number" or stores["values"].get("3") != 5663:
        _fail(f"tsuruha stores {stores}")
    if stores["values"].get("8") == 2694:
        _fail(f"tsuruha picked 統合前 stores {stores['values']}")
    opened = _row(got2, "出店")
    if opened["unit"] != "number" or opened["values"].get("3") != 17:
        _fail(f"tsuruha open count {opened}")
    if "売上高" in opened["element"] or "前年" in opened["element"]:
        _fail(f"open must not get yoy rename {opened}")
    closed = _row(got2, "退店")
    if closed["unit"] != "number" or closed["values"].get("3") != 30:
        _fail(f"tsuruha close count {closed}")
    if "売上高" in closed["element"] or "前年" in closed["element"]:
        _fail(f"close must not get yoy rename {closed}")
    if closed["element"] == opened["element"]:
        _fail(f"open/close labels collided {opened} {closed}")


def test_sagami_zougenhi_is_yoy_pct() -> None:
    """前年同月増減比:% は変化率。単位:% だけで指数にしない。"""
    html = """
    <html><body>
    <h2>2026年度 和食麺処サガミ部門 (前年同月増減比:%)</h2>
    <table>
    <tr><td></td><td>全店</td><td>全店</td><td>全店</td><td>既存店</td><td>既存店</td><td>既存店</td></tr>
    <tr><td></td><td>売上高</td><td>客数</td><td>客単価</td><td>売上高</td><td>客数</td><td>客単価</td></tr>
    <tr><td>4月</td><td>8.1</td><td>5.6</td><td>2.4</td><td>0.4</td><td>-1.7</td><td>2.1</td></tr>
    <tr><td>5月</td><td>18.3</td><td>16.1</td><td>1.9</td><td>9.8</td><td>8.1</td><td>1.7</td></tr>
    <tr><td>8月</td><td>9.3</td><td>7.5</td><td>1.6</td><td>1.2</td><td>-0.4</td><td>1.6</td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "9900", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"sagami as_of {got['as_of']}")
    for el in ("全店 売上高", "全店 客数", "全店 客単価", "既存店 売上高", "既存店 客数", "既存店 客単価"):
        row = _row(got, el)
        if row["unit"] != "yoy_pct":
            _fail(f"sagami {el} must be yoy_pct {row}")
    sales = _row(got, "全店 売上高")
    if sales["values"].get("8") != 9.3 or sales["values"].get("4") != 8.1:
        _fail(f"sagami values {sales}")
    guest = _row(got, "既存店 客数")
    if guest["values"].get("4") != -1.7:
        _fail(f"sagami signed {guest}")


def test_skip_span_total_column() -> None:
    got = parse_issuer(ARCS_HTML, "9948")
    if got["as_of"] != "2026-07-31":
        _fail(f"arcs as_of {got['as_of']}")
    sales = _row(got, "売上高 全店")
    if "計" in "".join(str(k) for k in sales["values"]):
        _fail(f"span total col {sales}")
    if sales["values"] != {"3": 100.8, "4": 102.2, "5": 105.2, "6": 99, "7": 100.7}:
        _fail(f"arcs values {sales['values']}")


def test_eir_same_date_picks_later_fy() -> None:
    payload = parse_eir_jsonp(
        "eolparts_announcement_24("
        + json.dumps(
            {
                "item": [
                    {
                        "date": "2026/09/07 17:00:00",
                        "title": "2026年3月期　月次情報",
                        "text": "<table><tr><th>4月</th><td>1</td></tr></table>",
                    },
                    {
                        "date": "2026/09/07 17:00:00",
                        "title": "2027年3月期　月次情報（8月）",
                        "text": MOS_HTML,
                    },
                ]
            },
            ensure_ascii=False,
        )
        + ");"
    )
    html, as_of = pick_eir_monthly(payload, "月次情報")
    if as_of != "2026-09-07":
        _fail(f"as_of {as_of}")
    if "103.9" not in html or "2026年3月期" in html:
        _fail("did not pick later FY")


def test_eir_skips_empty_url_same_date() -> None:
    payload = parse_eir_jsonp(
        "eolparts_announcement_24("
        + json.dumps(
            {
                "item": [
                    {
                        "date": "2026/09/07 17:00:00",
                        "title": "2026年3月期　月次情報",
                        "text": MOS_HTML,
                        "type": "space",
                    },
                    {
                        "date": "2026/09/07 17:00:00",
                        "title": "2027年3月期　月次情報（8月）",
                        "text": "",
                        "type": "url",
                        "link": "https://www.mos.co.jp/company/ir/library/monthly_info/",
                    },
                    {
                        "date": "2026/04/07 18:00:00",
                        "title": "2026年3月期　月次情報",
                        "text": "<p>2025年4月～2026年3月</p><table><tr><th>4月</th><td>1</td></tr></table>",
                        "type": "space",
                    },
                ]
            },
            ensure_ascii=False,
        )
        + ");"
    )
    html, as_of = pick_eir_monthly(payload, "月次情報")
    if as_of != "2026-09-07":
        _fail(f"as_of {as_of}")
    if "103.9" not in html:
        _fail("did not pick widget with table")
    if "2025年4月" in html:
        _fail("picked previous FY widget")


def test_follow_re_picks_latest_year() -> None:
    listing = """
    <html><body>
    <a href="/ir/library/recent/fy2024/">2024</a>
    <a href="/ir/library/recent/fy2025/">2025</a>
    <a href="/ir/library/recent/fy2023/">2023</a>
    </body></html>
    """
    url = pick_follow_url(listing, "https://www.watami.co.jp/ir/library/recent", r"fy20\d{2}")
    if url != "https://www.watami.co.jp/ir/library/recent/fy2025/":
        _fail(url)


SEKISUI_MONTH_HTML = """
<html><body>
<h1>2026年8月度受注速報（対前年同期比）（2026年2月～2027年1月）</h1>
<table>
<tr><th></th><th>累計 （2026年2月～2026年8月）</th><th>単月 （2026年8月）</th></tr>
<tr><td>全体</td><td>101%</td><td>94%</td></tr>
<tr><td>【戸建住宅】</td><td>101%</td><td>102%</td></tr>
<tr><td>【賃貸･事業用建物 (RC造除く)】</td><td>93%</td><td>78%</td></tr>
<tr><td>【リフォーム】</td><td>117%</td><td>111%</td></tr>
<tr><td>【マンション】</td><td>126%</td><td>131%</td></tr>
</table>
</body></html>
"""

SEKISUI_YEAR_HTML = """
<html><body>
<h1>2026年：受注速報</h1>
<table>
<tr><td>9月10日</td><td><a href="/company/financial/order/orders_2026/2026_8">2026年8月度受注速報</a></td></tr>
<tr><td>8月10日</td><td><a href="/company/financial/order/orders_2026/2026_7">2026年7月度受注速報</a></td></tr>
<tr><td>7月10日</td><td><a href="/company/financial/order/orders_2026/2026_10">2026年10月度受注速報</a></td></tr>
<tr><td>1月9日</td><td><a href="/company/financial/order/orders_2026/2025_12">2025年12月度受注速報</a></td></tr>
</table>
</body></html>
"""

SEKISUI_INDEX_HTML = """
<html><body>
<a href="/company/financial/order/orders_2025/">2025年</a>
<a href="/company/financial/order/orders_2026/">2026年</a>
</body></html>
"""


def test_follow_re_picks_latest_month_not_year_folder() -> None:
    url = pick_follow_url(
        SEKISUI_YEAR_HTML,
        "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/",
        r"orders_20\d{2}",
    )
    if url != "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_10":
        _fail(f"string sort would pick 2026_8; got {url}")
    year = pick_follow_url(
        SEKISUI_INDEX_HTML,
        "https://www.sekisuihouse.co.jp/company/financial/order/",
        r"orders_20\d{2}",
    )
    if year != "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/":
        _fail(year)
    child = pick_follow_url(
        SEKISUI_YEAR_HTML,
        "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/",
        r"orders_20\d{2}",
        under="https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/",
    )
    if child != "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_10":
        _fail(child)
    none = pick_follow_url(
        SEKISUI_MONTH_HTML,
        "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_8",
        r"orders_20\d{2}",
        under="https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_8",
    )
    if none is not None:
        _fail(f"month page has no child {none}")


def test_sekisui_single_month_col_skips_cumulative() -> None:
    got = parse_issuer(SEKISUI_MONTH_HTML, "1928")
    if got["as_of"] != "2026-08-31":
        _fail(f"sekisui as_of {got['as_of']}")
    if got["months"] != [8]:
        _fail(f"sekisui months {got['months']}")
    overall = _row(got, "全体")
    if overall["unit"] != "yoy_index" or overall["values"] != {"8": 94}:
        _fail(f"累計 leaked into values {overall}")
    house = _row(got, "【戸建住宅】")
    if house["values"].get("8") != 102:
        _fail(f"house {house}")
    if any("累計" in r["element"] for r in got["rows"]):
        _fail("累計 element leaked")
    if any("2" in r["values"] for r in got["rows"]):
        _fail("累計 range parsed as month 2")


SUMITOMO_HTML = """
<html><body>
<h1>国内受注速報</h1>
<h2>2026年8月</h2>
<table>
<tr><th>戸建注文住宅</th><th>戸建注文住宅</th><th>賃貸住宅</th><th>賃貸住宅</th>
<th>リフォーム</th><th>リフォーム</th><th>合計</th><th>合計</th></tr>
<tr><th>単月</th><th>累計</th><th>単月</th><th>累計</th>
<th>単月</th><th>累計</th><th>単月</th><th>累計</th></tr>
<tr><td>+0%</td><td>+7%</td><td>+74%</td><td>+25%</td>
<td>+15%</td><td>+12%</td><td>+6%</td><td>+9%</td></tr>
</table>
<table>
<tr><th>年月</th><th>戸建注文住宅</th><th>戸建注文住宅</th></tr>
<tr><th>年月</th><th>単月</th><th>累計</th></tr>
<tr><th>7月</th><td>+4%</td><td>+8%</td></tr>
<tr><th>2026年1月</th><td>+14%</td><td>+14%</td></tr>
<tr><th>8月</th><td>+9%</td><td>+4%</td></tr>
</table>
</body></html>
"""

HIMALAYA_HTML = """
<html><body>
<h1>月次売上/店舗数</h1>
<ul>
<li class="here"><a href="/ir/library/monthly/?y=2026">2026</a></li>
<li><a href="/ir/library/monthly/?y=2025">2025</a></li>
</ul>
<table>
<tr><td></td><td>9月</td><td>10月</td><td>11月</td><td>12月</td>
<td>1月</td><td>2月</td><td>3月</td><td>4月</td><td>5月</td>
<td>6月</td><td>7月</td><td>8月</td><td>年度累計</td></tr>
<tr><td>既存店</td><td>96.2</td><td>101.6</td><td>98.6</td><td>91.3</td>
<td>104.9</td><td>96.5</td><td>98.8</td><td>100.0</td><td>105.6</td>
<td>94.1</td><td>106.1</td><td>105.3</td><td>100.0</td></tr>
<tr><td>全店</td><td>96.2</td><td>101.6</td><td>98.6</td><td>91.3</td>
<td>104.9</td><td>96.5</td><td>98.8</td><td>100.0</td><td>105.6</td>
<td>94.1</td><td>106.1</td><td>105.3</td><td>100.0</td></tr>
</table>
</body></html>
"""

MINISTOP_HTML = """
<html><body>
<h1>月次営業報告 2026年</h1>
<p>2026年8月31日 現在</p>
<h2>既存店1店1日当り</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>平均売上高（千円）</td><td>407</td><td>407</td><td>421</td><td>414</td><td>444</td><td>443</td><td>422</td></tr>
<tr><td>平均売上高前年比（％）</td><td>95.9</td><td>92.2</td><td>96.8</td><td>91.6</td><td>95.5</td><td>96.4</td><td>94.8</td></tr>
<tr><td>客数前年比（％）</td><td>94.5</td><td>91.9</td><td>95.5</td><td>90.6</td><td>94.3</td><td>94.8</td><td>93.6</td></tr>
</table>
<h2>全店1店1日当り</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>平均売上高前年比（％）</td><td>97.1</td><td>93.9</td><td>98.0</td><td>93.1</td><td>97.3</td><td>98.1</td><td>96.2</td></tr>
</table>
<h2>全店売上</h2>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>上期累計</th></tr>
<tr><td>売上高合計前年比（％）</td><td>93.5</td><td>89.6</td><td>93.7</td><td>89.2</td><td>92.4</td><td>92.9</td><td>91.0</td></tr>
<tr><td>CVS前年比（％）</td><td>97.8</td><td>92.9</td><td>95.8</td><td>93.9</td><td>95.6</td><td>93.3</td><td>94.9</td></tr>
<tr><td>店内加工FF前年比（％）</td><td>73.8</td><td>74.8</td><td>84.0</td><td>68.2</td><td>78.6</td><td>90.8</td><td>78.3</td></tr>
</table>
</body></html>
"""


def test_sumitomo_snapshot_heading_month() -> None:
    got = parse_issuer(SUMITOMO_HTML, "1911", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"sumitomo as_of {got['as_of']}")
    if 8 not in got["months"]:
        _fail(f"sumitomo months {got['months']}")
    house = _row(got, "戸建注文住宅")
    if house["values"].get("8") != 0:
        _fail(f"current Aug overwritten by old 8月 {house}")
    if house["unit"] != "yoy_pct":
        _fail(f"unit {house}")


def test_himalaya_year_tab_as_of() -> None:
    got = parse_issuer(HIMALAYA_HTML, "7514", table=None)
    if got["as_of"] != "2026-08-31":
        _fail(f"himalaya as_of {got['as_of']}")
    exist = _row(got, "既存店")
    if exist["unit"] != "yoy_index":
        _fail(f"himalaya unit {exist}")
    if exist["values"].get("8") != 105.3:
        _fail(f"himalaya {exist}")
    if "13" in exist["values"] or "累計" in "".join(exist["values"]):
        _fail(f"年度累計 leaked {exist}")


def test_drop_ytd_row_when_monthly_pair() -> None:
    html = """
    <html><body>
    <h2>2027年5月期 月次 売上伸び率</h2>
    <table>
    <tr><td></td><td>6月</td><td>7月</td><td>8月</td></tr>
    <tr><td>既存店 Existing stores 単月 Monthly</td><td>100</td><td>103.4</td><td>101.8</td></tr>
    <tr><td>既存店 Existing stores</td><td>100</td><td>101.7</td><td>101.7</td></tr>
    </table>
    </body></html>
    """
    got = parse_issuer(html, "3148", table=None)
    labels = [r["element"] for r in got["rows"]]
    monthly = [el for el in labels if "単月" in el or "Monthly" in el]
    bare = [el for el in labels if "Existing stores" in el and "単月" not in el and "Monthly" not in el]
    if not monthly:
        _fail(f"monthly row missing {labels}")
    if bare:
        _fail(f"ytd pair must drop bare row {labels}")
    row = got["rows"][0]
    if row["unit"] not in ("yoy_index", "yoy_pct") or row["values"].get("7") != 103.4:
        _fail(f"monthly row {row}")


XEVO_HTML = """
<!doctype html><html><body>
<p class="notes">※2026年4月度より、月次売上高前年比の算定対象を一部見直し</p>
<table>
<tr><th>2026年度 2027年 3月期</th><th>ゼビオ</th><th>ゼビオ</th><th>ゼビオ</th><th>ゼビオ</th></tr>
<tr><th>2026年度 ３月期</th><th>単月</th><th>単月</th><th>累計</th><th>累計</th></tr>
<tr><th>2026年度 ３月期</th><th>既存店</th><th>全店</th><th>既存店</th><th>全店</th></tr>
<tr><td>2026年04月</td><td>103.2</td><td>103.0</td><td>103.2</td><td>103.0</td></tr>
<tr><td>2026年05月</td><td>110.5</td><td>110.6</td><td>106.9</td><td>106.8</td></tr>
<tr><td>2026年06月</td><td>96.7</td><td>97.2</td><td>103.5</td><td>103.7</td></tr>
<tr><td>2026年07月</td><td>101.2</td><td>102.9</td><td>101.5</td><td>103.1</td></tr>
<tr><td>2026年08月</td><td>99.9</td><td>102.3</td><td>99.6</td><td>102.4</td></tr>
<tr><td>2026年09月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2026年10月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2026年11月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2026年12月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2027年01月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2027年02月</td><td></td><td></td><td></td><td></td></tr>
<tr><td>2027年03月</td><td></td><td></td><td></td><td></td></tr>
</table>
<table>
<tr><th>2025年度 2026年 3月期</th><th>ゼビオ</th><th>ゼビオ</th><th>ゼビオ</th><th>ゼビオ</th></tr>
<tr><th>2025年度 2026年 3月期</th><th>単月</th><th>単月</th><th>累計</th><th>累計</th></tr>
<tr><th>2025年度 2026年 3月期</th><th>既存店</th><th>全店</th><th>既存店</th><th>全店</th></tr>
<tr><td>2025年04月</td><td>93.4</td><td>96.3</td><td>93.4</td><td>96.3</td></tr>
<tr><td>2025年05月</td><td>97.9</td><td>99.8</td><td>95.6</td><td>98.0</td></tr>
<tr><td>2025年06月</td><td>96.9</td><td>99.5</td><td>96.0</td><td>98.5</td></tr>
<tr><td>2025年07月</td><td>101.3</td><td>100.2</td><td>98.7</td><td>98.9</td></tr>
<tr><td>2025年08月</td><td>102.0</td><td>101.9</td><td>99.3</td><td>99.5</td></tr>
<tr><td>2025年09月</td><td>97.1</td><td>98.8</td><td>99.1</td><td>99.4</td></tr>
<tr><td>2025年10月</td><td>104.4</td><td>104.7</td><td>99.8</td><td>100.1</td></tr>
<tr><td>2025年11月</td><td>103.6</td><td>103.8</td><td>100.2</td><td>100.5</td></tr>
<tr><td>2025年12月</td><td>92.5</td><td>93.3</td><td>99.2</td><td>99.5</td></tr>
<tr><td>2026年01月</td><td>99.8</td><td>102.3</td><td>99.2</td><td>99.8</td></tr>
<tr><td>2026年02月</td><td>94.4</td><td>96.6</td><td>98.8</td><td>99.6</td></tr>
<tr><td>2026年03月</td><td>97.7</td><td>99.2</td><td>98.7</td><td>99.6</td></tr>
</table>
</body></html>
"""


def test_xevo_prefers_current_fy_drops_ytd() -> None:
    """ゼビオ: 現行FYの単月のみ。月期キャプションと注記月度で年をずらさない。"""
    got = parse_issuer(XEVO_HTML, "8281", table=None, force_unit="yoy_index")
    if got["as_of"] != "2026-08-31":
        _fail(f"xevo as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"xevo months {got['months']}")
    labels = [r["element"] for r in got["rows"]]
    if any("累計" in el for el in labels):
        _fail(f"ytd leaked {labels}")
    if any(el in labels for el in ("ゼビオ 既存店", "ゼビオ 全店")):
        _fail(f"bare ytd labels {labels}")
    exist = _row(got, "ゼビオ 単月 既存店")
    all_store = _row(got, "ゼビオ 単月 全店")
    if exist["unit"] != "yoy_index" or exist["values"].get("8") != 99.9:
        _fail(f"xevo exist {exist}")
    if all_store["values"].get("4") != 103.0 and all_store["values"].get("4") != 103:
        _fail(f"xevo all {all_store}")
    if exist["values"].get("4") == 93.4:
        _fail(f"picked prior FY {exist}")


FUJI_HTML = """
<!doctype html><html><body>
<h4>月次売上：2026年度月次営業速報</h4>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th></tr>
<tr><th>既存店</th><td>99.5%</td><td>98.8%</td><td>103.3%</td><td>97.5%</td><td>99.3%</td></tr>
<tr><th>全店</th><td>97.1%</td><td>95.7%</td><td>99.9%</td><td>94.2%</td><td>95.8%</td></tr>
</table>
<h4>店舗推移表</h4>
<table>
<tr><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th></tr>
<tr><th>新店舗</th><td>1</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><th>閉店店舗</th><td>1</td><td>1</td><td>1</td><td>0</td><td>0</td></tr>
<tr><th>活性化店舗</th><td>0</td><td>5</td><td>3</td><td>3</td><td>2</td></tr>
<tr><th>総店舗数</th><td>481</td><td>479</td><td>478</td><td>478</td><td>478</td></tr>
</table>
""" + ("<!--" + ("pad " * 250) + "-->") + """
<p>2026年5月31日 (2027年春 予定) マックスバリュ安田店</p>
<h4>既存店の改装</h4>
<table>
<tr><th>活性化年月</th><th>店名</th><th>県</th><th>店舗面積</th></tr>
<tr><td>2026年4月</td><td>マルナカ善通寺店</td><td>香川県</td><td>3,541</td></tr>
<tr><td>2026年7月</td><td>マルナカ三木店</td><td>香川県</td><td>7,170</td></tr>
</table>
</body></html>
"""


YAMAYA_HTML = """
<html><body>
<a href="?y=2024">2024年3月期</a>
<a href="?y=2023">2023年3月期</a>
<table><tr><td>既存店</td><td>（単位：千円、千円未満切り捨て）</td><td></td><td></td></tr></table>
<table>
<tr><td>区分</td><td>当 年</td><td>前 年</td><td>前年比</td></tr>
<tr><td>2026年04月</td><td>9,909,844</td><td>9,480,775</td><td>104.53</td></tr>
<tr><td>2026年05月</td><td>10,770,579</td><td>10,276,913</td><td>104.80</td></tr>
<tr><td>2026年06月</td><td>10,363,354</td><td>10,791,836</td><td>96.03</td></tr>
<tr><td>2026年07月</td><td>10,927,664</td><td>11,125,322</td><td>98.22</td></tr>
<tr><td>累計(前期除く)</td><td>41,971,441</td><td>41,674,846</td><td>100.71</td></tr>
</table>
<table><tr><td>全店</td><td>（単位：千円、千円未満切り捨て）</td><td></td><td></td></tr></table>
<table>
<tr><td>区分</td><td>当 年</td><td>前 年</td><td>前年比</td></tr>
<tr><td>2026年04月</td><td>10,005,362</td><td>9,497,043</td><td>105.35</td></tr>
<tr><td>2026年05月</td><td>10,876,990</td><td>10,294,162</td><td>105.66</td></tr>
<tr><td>2026年06月</td><td>10,495,148</td><td>10,809,483</td><td>97.09</td></tr>
<tr><td>2026年07月</td><td>11,012,467</td><td>11,168,134</td><td>98.61</td></tr>
<tr><td>累計(前期除く)</td><td>42,389,967</td><td>41,768,822</td><td>101.49</td></tr>
</table>
<table><tr><td>全店・卸計</td><td>（単位：千円、千円未満切り捨て）</td><td></td><td></td></tr></table>
<table>
<tr><td>区分</td><td>当 年</td><td>前 年</td><td>前年比</td></tr>
<tr><td>2026年04月</td><td>10,196,527</td><td>9,735,063</td><td>104.74</td></tr>
<tr><td>2026年05月</td><td>11,054,982</td><td>10,487,886</td><td>105.41</td></tr>
<tr><td>2026年06月</td><td>10,653,734</td><td>10,963,486</td><td>97.17</td></tr>
<tr><td>2026年07月</td><td>11,199,723</td><td>11,439,301</td><td>97.91</td></tr>
<tr><td>累計(前期除く)</td><td>43,104,966</td><td>42,625,736</td><td>101.12</td></tr>
</table>
</body></html>
"""


def test_fuji_skips_renovation_merges_total_stores() -> None:
    """改装の店舗面積リストを選ばず、営業速報+総店舗数を取る。"""
    got = parse_8278(FUJI_HTML)
    labels = [r["element"] for r in got["rows"]]
    if any("店舗面積" in el for el in labels):
        _fail(f"renovation area must not win {labels}")
    exist = _row(got, "既存店")
    all_store = _row(got, "全店")
    if exist["unit"] != "yoy_index" or exist["values"].get("7") != 99.3:
        _fail(f"fuji existing {exist}")
    if all_store["unit"] != "yoy_index" or all_store["values"].get("7") != 95.8:
        _fail(f"fuji all {all_store}")
    total = _row(got, "総店舗数")
    if total["unit"] != "number" or total["values"].get("3") != 481:
        _fail(f"fuji total stores {total}")
    if any(el in labels for el in ("新店舗", "閉店店舗", "活性化店舗")):
        _fail(f"companion merge must not pull open/close counts {labels}")


def test_yamaya_scopes_skip_prior_and_wholesale() -> None:
    """やまや: 既存+全店をマージ。当年→売上高、前年・累計・卸計は落とす。"""
    got = parse_9994(YAMAYA_HTML)
    if got["months"] != [4, 5, 6, 7]:
        _fail(f"yamaya months {got['months']}")
    labels = [(r["element"], r["unit"]) for r in got["rows"]]
    if labels != [
        ("全店 売上高", "number"),
        ("全店 売上高", "yoy_index"),
        ("既存店 売上高", "number"),
        ("既存店 売上高", "yoy_index"),
    ]:
        _fail(f"yamaya labels {labels}")
    all_sales = _row(got, "全店 売上高", unit="number")
    if all_sales["values"].get("7") != 11012467:
        _fail(f"yamaya all sales {all_sales}")
    all_yoy = _row(got, "全店 売上高", unit="yoy_index")
    if all_yoy["values"].get("7") != 98.61:
        _fail(f"yamaya all yoy {all_yoy}")
    exist_sales = _row(got, "既存店 売上高", unit="number")
    if exist_sales["values"].get("4") != 9909844:
        _fail(f"yamaya exist sales {exist_sales}")
    exist_yoy = _row(got, "既存店 売上高", unit="yoy_index")
    if exist_yoy["values"].get("4") != 104.53:
        _fail(f"yamaya exist yoy {exist_yoy}")
    blob = " ".join(el for el, _u in labels)
    if "前 年" in blob or "卸" in blob or "累計" in blob:
        _fail(f"prior/wholesale/ytd leaked {labels}")


DAIKOKUTEN_HTML = """
<html><body>
<h2>月次情報(全店)</h2>
<table>
<tr><td></td><td>前年比</td><td>前年比</td><td>前年比</td></tr>
<tr><td>年月</td><td>売上高</td><td>既存店売上高</td><td>客数</td></tr>
<tr><td>※ 2026年（令和8年）度</td><td>※ 2026年（令和8年）度</td><td>※ 2026年（令和8年）度</td><td>※ 2026年（令和8年）度</td></tr>
<tr><td>2026年8月</td><td>108.9％</td><td>101.1%</td><td>109.2％</td></tr>
<tr><td>2026年7月</td><td>106.9％</td><td>99.0%</td><td>106.0％</td></tr>
<tr><td>2026年6月</td><td>101.7％</td><td>94.8%</td><td>102.8％</td></tr>
<tr><td>※ 2025年（令和7年）度</td><td>※ 2025年（令和7年）度</td><td>※ 2025年（令和7年）度</td><td>※ 2025年（令和7年）度</td></tr>
<tr><td>2026年5月</td><td>106.7％</td><td></td><td>107.9％</td></tr>
<tr><td>2022年8月</td><td>100.7％</td><td></td><td>101.6％</td></tr>
</table>
</body></html>
"""


def test_daikokuten_drops_nendo_keeps_newest_month() -> None:
    """年度帯は行名に入れず、同月の古い年で上書きしない。既存の空欄はソースどおり。"""
    got = parse_issuer(DAIKOKUTEN_HTML, "2791", table=None)
    labels = [r["element"] for r in got["rows"]]
    if any("令和" in el or "年度" in el or "2026年" in el for el in labels):
        _fail(f"nendo leaked into labels {labels}")
    sales = _row(got, "前年比 売上高")
    if sales["unit"] != "yoy_index" or sales["values"].get("8") != 108.9:
        _fail(f"want newest Aug 108.9, got {sales}")
    if sales["values"].get("8") == 100.7:
        _fail(f"old year overwrote {sales}")
    exist = _row(got, "前年比 既存店売上高")
    if exist["values"].get("8") != 101.1 or exist["values"].get("5") is not None:
        _fail(f"exist blank months {exist}")


def test_ministop_genzai_as_of() -> None:
    """1店1日当りより全店売上を選び、売上高合計だけ残す。"""
    got = parse_9946(MINISTOP_HTML)
    if got["as_of"] != "2026-08-31":
        _fail(f"ministop as_of {got['as_of']}")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"ministop months {got['months']}")
    labels = [r["element"] for r in got["rows"]]
    if labels != ["全店 売上高合計前年比（％）"]:
        _fail(f"want only 全店売上 total {labels}")
    sales = _row(got, "全店 売上高合計前年比（％）")
    if sales["unit"] != "yoy_index" or sales["values"].get("8") != 92.9:
        _fail(f"ministop {sales}")
    if sales["values"].get("3") == 95.9:
        _fail(f"picked 既存店1店1日 {sales}")
    if any("上期" in str(r) for r in got["rows"]):
        _fail("上期累計 leaked")
    if any("CVS" in el or "FF" in el or "平均" in el or "客数" in el for el in labels):
        _fail(f"breakdown leaked {labels}")


def test_9946_all_store_sales_total() -> None:
    """実ページの全店売上合計は行名に全店を付け、正規化後は全店 売上高(前年比)になる。"""
    html = (ROOT / "tests" / "testdata" / "html_9946" / "2026.html").read_text(
        encoding="utf-8"
    )
    got = parse_9946(html)
    if got["as_of"] != "2026-08-31":
        _fail(f"9946 as_of {got['as_of']}")
    if len(got["rows"]) != 1:
        _fail(f"9946 rows {got['rows']}")
    sales = got["rows"][0]
    if sales["element"] != "全店 売上高合計前年比（％）":
        _fail(f"9946 element {sales}")
    if sales["unit"] != "yoy_index":
        _fail(f"9946 unit {sales}")
    want = {"3": 93.5, "4": 89.6, "5": 93.7, "6": 89.2, "7": 92.4, "8": 92.9}
    if sales["values"] != want:
        _fail(f"9946 values {sales['values']}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import apply_issuer_aliases, canonicalize_element_label

    as_of = date(2026, 8, 31)
    name = apply_issuer_aliases(
        "99460",
        canonicalize_element_label(sales["element"], sales["unit"]),
        as_of=as_of,
    )
    stored = canonicalize_element_label(name, sales["unit"])
    if stored != "全店 売上高(前年比)":
        _fail(f"9946 canonical {stored}")


def test_follow_year_then_latest_month(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "sekisui"
    index = "https://www.sekisuihouse.co.jp/company/financial/order/"
    year = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/"
    month = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_8"
    year_html = """
    <html><body>
    <h1>2026年：受注速報</h1>
    <table>
    <tr><td>9月10日</td><td><a href="/company/financial/order/orders_2026/2026_8">2026年8月度受注速報</a></td></tr>
    <tr><td>8月10日</td><td><a href="/company/financial/order/orders_2026/2026_7">2026年7月度受注速報</a></td></tr>
    <tr><td>1月9日</td><td><a href="/company/financial/order/orders_2026/2025_12">2025年12月度受注速報</a></td></tr>
    </table>
    </body></html>
    """
    bodies = {
        index: SEKISUI_INDEX_HTML.encode("utf-8"),
        year: year_html.encode("utf-8"),
        month: SEKISUI_MONTH_HTML.encode("utf-8"),
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        if url not in bodies:
            raise RuntimeError(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="1928",
            name="積水ハウス",
            index_url=index,
            title="月次",
            source="html",
            eir_code="1928",
            eir_n=0,
            eir_title_re="月次",
            follow_re=r"orders_20\d{2}",
            parse=True,
        )
        n, s, k, f = fetch_issuer(
            iss, dest, {"19280": ("011", "積水ハウス")}, date(2026, 9, 14), dry_run=False
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"sekisui fetch {n, s, k, f} calls={calls}")
        if calls != [index, year, month]:
            _fail(f"expected index→year→month {calls}")
        htmls = list((dest / "1928").glob("*.html"))
        jsons = list((dest / "1928").glob("*.ingest.json"))
        if len(htmls) != 1 or len(jsons) != 1:
            _fail(f"files {htmls} {jsons}")
        body = htmls[0].read_text(encoding="utf-8")
        if "単月" not in body or "2026年：受注速報" in body:
            _fail("saved year listing instead of month page")
        payload = json.loads(jsons[0].read_text(encoding="utf-8"))
        if payload["as_of"] != "2026-08-31" or payload["rows"][0]["values"] != {"8": 94}:
            _fail(payload)
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_parse_false_saves_html_without_json(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = lambda url, timeout=30.0: b"<html><body><p>graph only</p></body></html>"  # noqa: ARG005
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="1878",
            name="大東建託",
            index_url="https://example.test/kentaku/",
            title="月次",
            source="html",
            eir_code="1878",
            eir_n=0,
            eir_title_re="月次",
            follow_re="",
            parse=False,
        )
        n, s, k, f = fetch_issuer(
            iss, dest, {"18780": ("011", "大東建託")}, date(2026, 9, 14), dry_run=False
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"parse=false {n, s, k, f}")
        htmls = list((dest / "1878").glob("*.html"))
        jsons = list((dest / "1878").glob("*.ingest.json"))
        if len(htmls) != 1:
            _fail(f"html {htmls}")
        if jsons:
            _fail(f"json must not be written {jsons}")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_no_as_of_does_not_write_json(tmp: Path) -> None:
    import ir_monthly_html_r2 as mod

    bare = """
    <html><body>
    <table>
    <tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr>
    <tr><td>全店</td><td>1.0</td><td>2.0</td><td>3.0</td></tr>
    </table>
    </body></html>
    """
    try:
        parse_issuer(bare, "2664")
    except ValueError as e:
        if "no as_of" not in str(e):
            _fail(f"expected no as_of, got {e}")
    else:
        _fail("bare table must not emit ingest json")

    dest = tmp / "no-as-of"
    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = lambda url, timeout=30.0: bare.encode("utf-8")  # noqa: ARG005
    mod.SLEEP_SEC = 0
    try:
        iss = HtmlIssuer(
            code="2664",
            name="カワチ薬品",
            index_url="https://example.test/bare/",
            title="月次",
            source="html",
            eir_code="2664",
            eir_n=0,
            eir_title_re="月次",
        )
        n, s, k, f = fetch_issuer(
            iss, dest, {"26640": ("011", "カワチ薬品")}, date(2026, 9, 14), dry_run=False
        )
        if (n, s, k, f) != (1, 1, 0, 1):
            _fail(f"html saved, ingest failed {n, s, k, f}")
        htmls = list((dest / "2664").glob("*.html"))
        jsons = list((dest / "2664").glob("*.ingest.json"))
        if len(htmls) != 1:
            _fail(f"html {htmls}")
        if jsons:
            _fail(f"json must not be written {jsons}")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


CANDO_YYMM_HTML = """
<html><body>
<h2>月次売上高前年比速報 2026年8月度</h2>
<table>
<tr><td></td><td>26.03</td><td>26.04</td><td>26.05</td><td>26.06</td><td>26.07</td><td>26.08</td></tr>
<tr><td>全社売上高</td><td>103.6</td><td>103.4</td><td>104.3</td><td>101.0</td><td>101.2</td><td>104.5</td></tr>
<tr><td>既存直営店売上高</td><td>101.8</td><td>103.9</td><td>106.0</td><td>100.5</td><td>102.9</td><td>104.4</td></tr>
</table>
</body></html>
"""


def test_yy_mm_cando_table() -> None:
    got = parse_issuer(CANDO_YYMM_HTML, "2698")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"cando months {got['months']}")
    sales = _row(got, "全社売上高")
    if sales["values"].get("3") != 103.6 or sales["values"].get("8") != 104.5:
        _fail(f"cando sales {sales}")
    exist = _row(got, "既存直営店売上高")
    if exist["values"].get("3") != 101.8:
        _fail(f"cando exist {exist}")


TRIAL_LETTERSPACING_HTML = """
<html><body>
<h2>2027年6月期 月次売上高速報</h2>
<table>
<tr><td></td><td>7月</td><td>8月</td><td>9月</td></tr>
<tr><td>ト ラ イ ア ル 既 存 店 売上高</td><td>104.0</td><td>103.5</td><td></td></tr>
<tr><td>ト ラ イ ア ル 全 店 売上高</td><td>110.6</td><td>108.6</td><td></td></tr>
<tr><td>全店 売上高</td><td>99.0</td><td>98.5</td><td></td></tr>
</table>
</body></html>
"""


def test_letterspacing_trial_table() -> None:
    got = parse_issuer(TRIAL_LETTERSPACING_HTML, "141A")
    labels = [r["element"] for r in got["rows"]]
    if "トライアル既存店 売上高" not in labels:
        _fail(f"letterspacing not collapsed {labels}")
    if any("ト ラ イ" in el for el in labels):
        _fail(f"letterspacing leaked {labels}")
    if "全店 売上高" not in labels:
        _fail(f"word spaces must stay {labels}")
    trial = next(r for r in got["rows"] if "トライアル" in r["element"] and "既存" in r["element"])
    if trial["values"].get("7") != 104:
        _fail(f"trial exist {trial}")


FY_CAPTION_HTML = """
<html><body>
<h2>月次速報</h2>
<table>
<tr><td></td><td>4月</td><td>5月</td><td>6月</td><td>7月</td></tr>
<tr><td>2027年3月期 取扱高</td><td>7,212</td><td>6,589</td><td>6,668</td><td>5,706</td></tr>
<tr><td>2026年3月期 取扱高</td><td>2,688</td><td>3,013</td><td>2,692</td><td>2,673</td></tr>
<tr><td>2027年3月期 売上高</td><td>4,187</td><td>3,664</td><td>3,960</td><td>3,191</td></tr>
<tr><td>2026年3月期 売上高</td><td>333</td><td>344</td><td>342</td><td>358</td></tr>
</table>
</body></html>
"""


def test_fy_caption_handling_keeps_metric_newer_wins() -> None:
    got = parse_issuer(FY_CAPTION_HTML, "6045")
    labels = [r["element"] for r in got["rows"]]
    if any("2027年" in el or "2026年" in el or "月期" in el for el in labels):
        _fail(f"fy caption leaked {labels}")
    handling = _row(got, "取扱高")
    if handling["values"].get("4") != 7212 or handling["values"].get("7") != 5706:
        _fail(f"newer FY must win {handling}")
    sales = _row(got, "売上高")
    if sales["values"].get("4") != 4187:
        _fail(f"sales newer FY {sales}")


VEGA_PERIOD_HTML = """
<html><body>
<h2>2026年8月度 月次業績概況</h2>
<table>
<tr><td></td><td>4月</td><td>5月</td><td>6月</td><td>7月</td><td>8月</td><td>9月</td></tr>
<tr><td>当期</td><td>1,810</td><td>1,564</td><td>1,543</td><td>1,764</td><td>1,489</td><td></td></tr>
<tr><td>前期</td><td>1,604</td><td>1,376</td><td>1,281</td><td>1,415</td><td>1,169</td><td>1,265</td></tr>
<tr><td>対前年同月比（%）</td><td>112.8</td><td>113.7</td><td>120.4</td><td>124.7</td><td>127.4</td><td></td></tr>
</table>
</body></html>
"""


def test_current_period_rewrites_skips_prior() -> None:
    got = parse_issuer(VEGA_PERIOD_HTML, "3542")
    if got["as_of"] != "2026-08-31":
        _fail(f"vega as_of {got['as_of']}")
    labels = [r["element"] for r in got["rows"]]
    if any("前期" in el or el == "当期" for el in labels):
        _fail(f"prior/当期 leaked {labels}")
    sales = _row(got, "売上高")
    if sales["unit"] != "number" or sales["values"].get("8") != 1489:
        _fail(f"当期 → 売上高 {sales}")
    if sales["values"].get("4") == 1604:
        _fail(f"前期 leaked into 売上高 {sales}")
    yoy = _row(got, "対前年同月比（%）")
    if yoy["values"].get("8") != 127.4:
        _fail(f"yoy {yoy}")


def test_sticky_fy_inherits_caption_to_unheaded_monthly() -> None:
    got = _sticky_section_fy(
        [
            "2027年3月期 売上高（累計）",
            "売上高（単月）",
            "2026年3月期 売上高（累計）",
            "売上高（単月）",
        ]
    )
    if got != [(2027, 3), (2027, 3), (2026, 3), (2026, 3)]:
        _fail(f"sticky fy {got}")
    nav = _sticky_section_fy(["2027年3月期 2026年3月期"])
    if nav != [(2027, 3)]:
        _fail(f"year select {nav}")


def test_nsd_monthly_keeps_current_fy_not_prior_complete() -> None:
    path = ROOT / "tests" / "testdata" / "html_issue248" / "9759.html"
    got = parse_issuer(path.read_text(encoding="utf-8"), "9759", table=None)
    sales = _row(got, "売上高（百万円）")
    if sales["unit"] != "number":
        _fail(f"nsd sales unit {sales}")
    if sales["values"].get("4") != 9905:
        _fail(f"picked prior-year 単月 {sales}")
    if sales["values"].get("5") != 9786:
        _fail(f"current 単月 May {sales}")
    if sales["values"].get("3") is not None:
        _fail(f"prior March leaked {sales}")
    yoy = _row(got, "売上高(前期比)")
    if yoy["unit"] != "yoy_pct":
        _fail(f"nsd yoy unit {yoy}")
    if yoy["values"].get("4") != 8.7:
        _fail(f"nsd Apr 増減率 {yoy}")
    if yoy["values"].get("5") != 9.7:
        _fail(f"nsd May 増減率 {yoy}")
    names = [str(r.get("element") or "") for r in got["rows"]]
    if any("前期比（" in n or n.startswith("前期比") for n in names):
        _fail(f"nsd kept 前期比百万円 {got['rows']}")
    if any("増減率" in n for n in names):
        _fail(f"nsd left 増減率 unaliased {got['rows']}")
    if got["as_of"] != "2026-08-31":
        _fail(f"nsd as_of {got['as_of']}")


def test_6045_prior_fy_rows_not_merged() -> None:
    path = ROOT / "tests" / "testdata" / "html_year_drift" / "6045.html"
    got = parse_issuer(decode_html_bytes(path.read_bytes()), "6045")
    if got["as_of"] != "2026-08-31":
        _fail(f"6045 as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"6045 months {got['months']}")
    handling = _row(got, "取扱高")
    if handling["values"] != {"4": 7212, "5": 6589, "6": 6668, "7": 5706, "8": 5786}:
        _fail(f"6045 取扱高 {handling['values']}")
    sales = _row(got, "売上高")
    if sales["values"] != {"4": 4187, "5": 3664, "6": 3960, "7": 3191, "8": 3210}:
        _fail(f"6045 売上高 {sales['values']}")
    banned = {3044, 389, 2688, 333}
    for row in got["rows"]:
        if "9" in (row.get("values") or {}):
            _fail(f"6045 month 9 leaked {row}")
        for v in (row.get("values") or {}).values():
            if v in banned:
                _fail(f"6045 prior FY value leaked {v} in {row}")
    totals = {31966, 18216}
    h2 = {"10", "11", "12", "1", "2", "3"}
    for row in got["rows"]:
        values = row.get("values") or {}
        if h2 & values.keys():
            _fail(f"6045 h2 key leaked {row}")
        for v in values.values():
            if v in totals:
                _fail(f"6045 full-year total leaked {v} in {row}")


def test_6045_second_half_band_read() -> None:
    """6045.html（2026-09-25 取得の実ページ）から 2027年3月期の tr 4行だけ除いた実HTML。"""
    path = ROOT / "tests" / "testdata" / "html_year_drift" / "6045_fy2026_only.html"
    got = parse_issuer(decode_html_bytes(path.read_bytes()), "6045")
    if got["as_of"] != "2026-03-31":
        _fail(f"6045 fy2026 as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]:
        _fail(f"6045 fy2026 months {got['months']}")
    if [r["element"] for r in got["rows"]] != ["取扱高", "売上高"]:
        _fail(f"6045 fy2026 rows {got['rows']}")
    handling = _row(got, "取扱高", unit="number")
    sales = _row(got, "売上高", unit="number")
    if handling["values"] != {
        "4": 2688, "5": 3013, "6": 2692, "7": 2673, "8": 2778, "9": 3044,
        "10": 2897, "11": 2678, "12": 2530, "1": 2747, "2": 2944, "3": 3521,
    }:
        _fail(f"6045 fy2026 取扱高 {handling['values']}")
    if sales["values"] != {
        "4": 333, "5": 344, "6": 342, "7": 358, "8": 302, "9": 389,
        "10": 376, "11": 392, "12": 386, "1": 339, "2": 389, "3": 408,
    }:
        _fail(f"6045 fy2026 売上高 {sales['values']}")
    banned = {16889, 2138, 17328, 34217, 2301, 4440}
    for row in got["rows"]:
        for v in (row.get("values") or {}).values():
            if v in banned:
                _fail(f"6045 half total leaked {v} in {row}")


def test_8163_note_year_month_not_as_of() -> None:
    path = ROOT / "tests" / "testdata" / "html_year_drift" / "8163.html"
    html = decode_html_bytes(path.read_bytes())
    explicit = parse_as_of_text(html)
    if explicit is not None:
        _fail(f"8163 parse_as_of_text {explicit}")
    got = parse_issuer(html, "8163")
    if got["as_of"] != "2026-08-31":
        _fail(f"8163 as_of {got['as_of']}")
    if got["months"] != [4, 5, 6, 7, 8]:
        _fail(f"8163 months {got['months']}")
    all_store = _row(got, "全店 売上高(1)")
    if all_store["values"].get("8") != 108.2:
        _fail(f"8163 全店 売上高(1) {all_store}")


def test_p_fallback_takes_report_title_only() -> None:
    vega = parse_as_of_text(
        "<p>JP EN 月次報告 トップ IR 月次報告 2026年8月の 月次業績に関するお知らせ</p>"
    )
    if vega != "2026-08-31":
        _fail(f"vega p title {vega}")
    paren = parse_as_of_text("<p>月次概況(2026年8月)</p>")
    if paren != "2026-08-31":
        _fail(f"paren p title {paren}")
    note = parse_as_of_text(
        "<p>*月次データは速報値です。 *売上高(2)は株式会社すし弁慶(2025年9月に子会社化)の売上高を考慮しない前年比較です。</p>"
    )
    if note is not None:
        _fail(f"note year-month taken as as_of {note}")


def test_row_months_drops_prior_fy_columns() -> None:
    html = """
    <table>
    <tr><td></td><td>2027年3月期 売上高</td><td>2026年3月期 売上高</td></tr>
    <tr><td>4月</td><td>4,187</td><td>333</td></tr>
    <tr><td>5月</td><td>3,664</td><td>344</td></tr>
    <tr><td>6月</td><td></td><td>342</td></tr>
    </table>
    """
    tables = parse_tables(html)
    parsed = parse_grid(tables[0])
    if parsed is None:
        _fail("row months fy filter: no parse")
    sales = [r for r in parsed["rows"] if r["element"] == "売上高"]
    if len(sales) != 1:
        _fail(f"expected one 売上高 {parsed['rows']}")
    vals = sales[0]["values"]
    if "6" in vals:
        _fail(f"prior FY 6月 leaked {vals}")
    if vals.get("4") != 4187 or vals.get("5") != 3664:
        _fail(f"2027 values {vals}")


def test_brand_issuers_reject_foreign_page_without_sidecar() -> None:
    import tempfile

    foreign = """
    <html><body>
    <h2>グループ合計</h2>
    <table>
    <tr><th>グループ合計</th><th>4月</th><th>5月</th><th>6月</th></tr>
    <tr><td>全店 売上高</td><td>1.0%</td><td>2.0%</td><td>3.0%</td></tr>
    </table>
    <h2>ドラッグストア事業</h2>
    <table>
    <tr><th>ドラッグストア事業</th><th>4月</th><th>5月</th><th>6月</th></tr>
    <tr><td>全店 売上高</td><td>4.0%</td><td>5.0%</td><td>6.0%</td></tr>
    </table>
    </body></html>
    """
    live = {i.code: i for i in load_html_registry(ROOT / "config" / "monthly" / "ir_monthly.json")}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for code in ("2910", "3148", "7522", "7561", "8179", "8278", "9946", "9994"):
            fresh = tmp / f"{code}_fresh.html"
            fresh.write_text(foreign, encoding="utf-8")
            try:
                write_ingest(fresh, foreign, live[code])
            except ValueError:
                pass
            else:
                _fail(f"{code} foreign page should raise")
            if ingest_path(fresh).exists():
                _fail(f"{code} wrote a sidecar")
            kept = tmp / f"{code}_kept.html"
            kept.write_text(foreign, encoding="utf-8")
            prior = ingest_path(kept)
            prior.write_text('{"kept": true}\n', encoding="utf-8")
            try:
                write_ingest(kept, foreign, live[code])
            except ValueError:
                pass
            else:
                _fail(f"{code} foreign page should raise")
            if prior.read_text(encoding="utf-8") != '{"kept": true}\n':
                _fail(f"{code} removed an existing sidecar")


def test_7522_fy2025_q4_months_map_to_2026() -> None:
    """fy2025 の Q4 と宅食を連結しても 1〜3月は 2026、10〜12月は 2025。"""
    path = ROOT / "tests" / "testdata" / "html_7522" / "fy2025.html"
    got = parse_7522(decode_html_bytes(path.read_bytes()))
    if got["as_of"] != "2026-03-31":
        _fail(f"7522 fy2025 as_of {got['as_of']}")
    if got["months"] != [10, 11, 12, 1, 2, 3]:
        _fail(f"7522 fy2025 months {got['months']}")
    sys.path.insert(0, str(ROOT))
    from app.monthly.ingest import plan_column_years

    _layout, mapping = plan_column_years(got["months"], 3, date(2026, 3, 31))
    years = dict(mapping)
    if any(years[m] != 2026 for m in (1, 2, 3)):
        _fail(f"jan-mar not 2026 {mapping}")
    if any(years[m] != 2025 for m in (10, 11, 12)):
        _fail(f"oct-dec not 2025 {mapping}")
    stores = _row(got, "国内外食 （サブウェイ事業除く） 全店 期末店舗数")
    if stores["values"] != {"1": 289, "2": 288, "3": 288}:
        _fail(f"subway-excluded stores {stores}")


def test_7522_index_follows_js_location_to_fy2026() -> None:
    """目次の最新年度は <a> ではなく location.href。"""
    path = ROOT / "tests" / "testdata" / "html_7522" / "index-recent.html"
    html = decode_html_bytes(path.read_bytes())
    live = {i.code: i for i in load_html_registry(ROOT / "config" / "monthly" / "ir_monthly.json")}
    iss = live["7522"]
    url = pick_follow_url(html, iss.index_url, iss.follow_re)
    if url != "https://www.watami.co.jp/ir/library/recent/fy2026/":
        _fail(url)


def test_9948_customers_are_yoy_index() -> None:
    """客数・客単価は100基準の指数。値は変えず、店舗数は実数のまま。"""
    path = ROOT / "tests" / "testdata" / "html_9948" / "2026.html"
    got = parse_9948(decode_html_bytes(path.read_bytes()))
    if got["as_of"] != "2026-08-31":
        _fail(f"9948 as_of {got['as_of']}")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"9948 months {got['months']}")
    want = {
        "客数 全店": {
            "3": 100.7, "4": 100.6, "5": 101.2, "6": 97.6, "7": 97.9, "8": 99,
        },
        "客数 既存店": {
            "3": 100.7, "4": 100.8, "5": 101.3, "6": 97.4, "7": 97.6, "8": 98.9,
        },
        "客単価 全店": {
            "3": 100, "4": 101.6, "5": 104, "6": 101.4, "7": 102.9, "8": 102.8,
        },
        "客単価 既存店": {
            "3": 99.9, "4": 101.6, "5": 104, "6": 101.3, "7": 102.7, "8": 102.6,
        },
    }
    for element, values in want.items():
        row = _row(got, element)
        if row["unit"] != "yoy_index" or row["values"] != values:
            _fail(f"9948 {element} {row}")
    sales = _row(got, "売上高 全店")
    if sales["unit"] != "yoy_index":
        _fail(f"9948 売上高 全店 {sales}")
    stores = _row(got, "店舗数")
    if stores["unit"] != "number" or stores["values"].get("8") != 374:
        _fail(f"9948 店舗数 {stores}")


def test_9993_customers_are_yoy_index() -> None:
    """客数・客単価は売上と同じ前年比指数。値は変えない。"""
    path = ROOT / "tests" / "testdata" / "html_9993" / "2026.html"
    got = parse_9993(decode_html_bytes(path.read_bytes()))
    if got["as_of"] != "2026-08-31":
        _fail(f"9993 as_of {got['as_of']}")
    if got["months"] != [3, 4, 5, 6, 7, 8]:
        _fail(f"9993 months {got['months']}")
    want = {
        "全店 客数": {
            "3": 93.2, "4": 92.3, "5": 94.7, "6": 92.1, "7": 91.1, "8": 91.5,
        },
        "全店 客単価": {
            "3": 99.2, "4": 101.3, "5": 102.3, "6": 99.5, "7": 99.8, "8": 100.2,
        },
        "既存店 客数": {
            "3": 102, "4": 101, "5": 103.7, "6": 101, "7": 99.7, "8": 100.2,
        },
        "既存店 客単価": {
            "3": 98.2, "4": 100.4, "5": 101.5, "6": 98.6, "7": 99.1, "8": 99.7,
        },
    }
    for element, values in want.items():
        row = _row(got, element)
        if row["unit"] != "yoy_index" or row["values"] != values:
            _fail(f"9993 {element} {row}")
    for element in ("全店 売上", "既存店 売上"):
        row = _row(got, element)
        if row["unit"] != "yoy_index":
            _fail(f"9993 {element} {row}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_parse_number_and_month()
    test_cawachi_col_months()
    test_halows_rowspan()
    test_okuwa_reverse_months_and_store_count()
    test_colowide_grouped_header_skips_total()
    test_eir_jsonp_picks_latest_monthly()
    test_decode_shift_jis()
    test_dest_html_and_ingest_sidecar()
    test_kobebussan_purchase_segment_is_shiire()
    test_kobebussan_second_half_stays_on_sales()
    test_section_unit_percent_is_yoy_index()
    test_rockfield_existing_and_all_store_stay_separate()
    test_trailing_unit_percent_is_yoy_index()
    test_force_unit_overrides_yoy_keeps_number()
    test_create_sd_nobiritsu_force_yoy_index()
    test_3148_real_page_drops_english_label()
    test_3549_sticky_metric_on_bare_kizon()
    test_3549_store_status_prefix_strips_to_open_close()
    test_load_html_registry()
    test_dumps_matches_ingest_shape()
    test_mos_skips_half_year_total()
    test_year_dot_month_and_skip_quarter()
    test_yy_mm_cando_table()
    test_letterspacing_trial_table()
    test_fy_caption_handling_keeps_metric_newer_wins()
    test_current_period_rewrites_skips_prior()
    test_sticky_fy_inherits_caption_to_unheaded_monthly()
    test_nsd_monthly_keeps_current_fy_not_prior_complete()
    test_6045_prior_fy_rows_not_merged()
    test_6045_second_half_band_read()
    test_8163_note_year_month_not_as_of()
    test_p_fallback_takes_report_title_only()
    test_row_months_drops_prior_fy_columns()
    test_watami_kyoten_count_is_number()
    test_watami_prefers_sales_over_kyoten()
    test_watami_sections_prefix_and_count_units()
    test_watami_quarter_banner_keeps_sales_labels()
    test_hurxley_dual_brand_prefixes_and_count_units()
    test_hurxley_canonicalize_aliases()
    test_watami_canonicalize_aliases()
    test_watami_nav_does_not_prefix_domestic_or_mix_years()
    test_watami_fy2024_bare_names_alias_to_subway_excluded()
    test_brand_issuers_reject_foreign_page_without_sidecar()
    test_nendo_and_fullwidth_fy_as_of()
    test_sagami_zougenhi_is_yoy_pct()
    test_skip_span_total_column()
    test_9948_customers_are_yoy_index()
    test_eir_same_date_picks_later_fy()
    test_eir_skips_empty_url_same_date()
    test_follow_re_picks_latest_year()
    test_7522_fy2025_q4_months_map_to_2026()
    test_7522_index_follows_js_location_to_fy2026()
    test_follow_re_picks_latest_month_not_year_folder()
    test_sekisui_single_month_col_skips_cumulative()
    test_sumitomo_snapshot_heading_month()
    test_himalaya_year_tab_as_of()
    test_drop_ytd_row_when_monthly_pair()
    test_xevo_prefers_current_fy_drops_ytd()
    test_fuji_skips_renovation_merges_total_stores()
    test_yamaya_scopes_skip_prior_and_wholesale()
    test_daikokuten_drops_nendo_keeps_newest_month()
    test_ministop_genzai_as_of()
    test_9946_all_store_sales_total()
    test_nitori_direct_page_no_follow()
    test_yamazawa_prefers_current_period()
    test_9993_customers_are_yoy_index()
    test_aeon_hokkaido_prefers_current_year_slide()
    test_village_nafco_sagami_as_of_not_future_fy()
    test_yoy_context_does_not_rename_guest_metrics()
    test_royal_two_brands_union_halves_and_keep_existing_scope()
    test_repeated_scope_row_prefixes_existing_and_all_store()
    test_same_fy_prefers_later_filled_half()
    test_same_fy_prefers_last_quarter_over_prior()
    test_calendar_ytd_drops_trailing_empty_months()
    test_washhouse_merges_store_and_sales_latest_pair()
    test_washhouse_past_year_as_of_from_row_dates()
    test_partial_apr_sep_ytd_drops_trailing_empty_months()
    test_trailing_empty_months_keep_middle_gaps()
    with TemporaryDirectory() as d:
        p = Path(d)
        test_hash_skip_and_reparse(p)
        test_hash_skip_refreshes_stale_sidecar(p)
        test_force_resaves_unchanged_html(p)
        test_run_force_requires_only_and_skips_reparse(p)
        test_eir_fetch_saves_item_html(p)
        test_unknown_stock_skip(p)
        test_fetch_issuer_retries_429_then_saves(p)
        test_fetch_issuer_exhausted_429_is_skipped(p)
        test_fetch_issuer_exhausted_404_stays_failed(p)
        test_run_persistent_429_does_not_fail_job(p)
        test_run_host_cooldown_skips_same_host(p)
        test_run_404_still_exits_1(p)
        test_run_only_dry_run(p)
        test_no_as_of_does_not_write_json(p)
        test_parse_false_saves_html_without_json(p)
        test_follow_year_then_latest_month(p)
    print("ok: ir monthly html r2")


if __name__ == "__main__":
    main()

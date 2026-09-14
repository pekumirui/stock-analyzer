"""ir_monthly_html_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ir_monthly_html_r2 import (  # noqa: E402
    HtmlIssuer,
    decode_html_bytes,
    dumps_ingest,
    eir_url,
    existing_html_hashes,
    fetch_issuer,
    html_to_ingest,
    ingest_path,
    load_html_registry,
    parse_as_of_text,
    parse_eir_jsonp,
    parse_month_cell,
    parse_number,
    parse_tables,
    pick_eir_monthly,
    pick_follow_url,
    reparse_issuer,
    run,
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


def test_cawachi_col_months() -> None:
    got = html_to_ingest(CAWACHI_HTML, "2664")
    if got["code"] != "2664":
        _fail(f"code {got['code']}")
    if got["months"] != [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]:
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
    got = html_to_ingest(HALOWS_HTML, "2742")
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
    got = html_to_ingest(OKUWA_HTML, "8217")
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
    got = html_to_ingest(COLOWIDE_HTML, "7616")
    if got["as_of"] != "2026-09-04":
        _fail(f"update date {got['as_of']}")
    if got["months"] != [4, 8, 9, 3]:
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


def test_load_html_registry() -> None:
    live = load_html_registry(ROOT / "config" / "ir_monthly.json")
    codes = [i.code for i in live]
    if len(codes) != 41:
        _fail(f"repo html issuers count {len(codes)}: {codes}")
    if codes != sorted(codes):
        _fail(f"html issuers must be code-sorted: {codes}")
    colo = next(i for i in live if i.code == "7616")
    if colo.source != "eir" or colo.eir_n != 0:
        _fail(f"colo {colo}")
    mos = next(i for i in live if i.code == "8153")
    if mos.source != "eir" or mos.eir_n != 24:
        _fail(f"mos {mos}")
    no_parse = {i.code for i in live if not i.parse}
    if no_parse != {"1878", "2792", "3080", "3196", "7544"}:
        _fail(f"parse=false {no_parse}")
    follows = {i.code: i.follow_re for i in live if i.follow_re}
    if "7522" not in follows or "1928" not in follows or "8153" in follows:
        _fail(f"follow_re {follows}")


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
            table=0,
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
            table=0,
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
            table=0,
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
    got = html_to_ingest(CAWACHI_HTML, "26640")
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
<h2>連結月次前年対比伸び率</h2>
<p>2027年２月期 (2026年3月1日～2027年2月28日)</p>
<table>
<tr><th>ツルハグループ</th><th></th><th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th>
<th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th></tr>
<tr><td>既存店</td><td>単月売上</td><td>3.3</td><td>5.0</td><td>4.6</td><td>▲ 1.1</td><td>1.7</td><td>1.4</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>既存店</td><td>（内 物販）</td><td>1.5</td><td>3.8</td><td>4.8</td><td>▲ 3.8</td><td>0.0</td><td>0.2</td>
<td></td><td></td><td></td><td></td><td></td><td></td></tr>
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
    got = html_to_ingest(MOS_HTML, "8153")
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
    if parse_month_cell("2026.03.01") is not None:
        _fail("date range is not a month")
    got = html_to_ingest(WATAMI_HTML, "7522")
    if got["months"] != [1, 2, 3]:
        _fail(f"watami months {got['months']}")
    sales = _row(got, "全店 (全業態) 売上高（%）")
    if sales["values"] != {"1": 110.1, "2": 106.1, "3": 106.7}:
        _fail(f"4Q/通年 leaked {sales['values']}")
    if sales["unit"] != "yoy_index":
        _fail(f"watami unit {sales}")


def test_nendo_and_fullwidth_fy_as_of() -> None:
    got = html_to_ingest(KAPPA_HTML, "7421")
    if got["as_of"] != "2026-08-31":
        _fail(f"kappa 2026年度 + 8月 {got['as_of']}")
    exist = _row(got, "既存店舗 売上高")
    if exist["unit"] != "yoy_index" or exist["values"].get("8") != 98.1:
        _fail(f"kappa {exist}")
    if any("累計" in r["element"] for r in got["rows"]):
        _fail("累計 row leaked")
    got2 = html_to_ingest(TSURUHA_HTML, "3391")
    if got2["as_of"] != "2026-08-31":
        _fail(f"tsuruha fullwidth 月期 {got2['as_of']}")
    sales = _row(got2, "既存店 単月売上")
    if sales["unit"] != "yoy_pct" or sales["values"].get("6") != -1.1:
        _fail(f"tsuruha {sales}")
    goods = _row(got2, "既存店 （内 物販）")
    if goods["values"].get("6") != -3.8:
        _fail(f"tsuruha goods {goods}")


def test_skip_span_total_column() -> None:
    got = html_to_ingest(ARCS_HTML, "9948")
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
            table=0,
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
        html_to_ingest(bare, "2664")
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
            table=0,
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
    test_load_html_registry()
    test_dumps_matches_ingest_shape()
    test_mos_skips_half_year_total()
    test_year_dot_month_and_skip_quarter()
    test_nendo_and_fullwidth_fy_as_of()
    test_skip_span_total_column()
    test_eir_same_date_picks_later_fy()
    test_follow_re_picks_latest_year()
    with TemporaryDirectory() as d:
        p = Path(d)
        test_hash_skip_and_reparse(p)
        test_eir_fetch_saves_item_html(p)
        test_unknown_stock_skip(p)
        test_run_only_dry_run(p)
        test_no_as_of_does_not_write_json(p)
        test_parse_false_saves_html_without_json(p)
    print("ok: ir monthly html r2")


if __name__ == "__main__":
    main()

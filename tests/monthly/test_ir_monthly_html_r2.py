"""ir_monthly_html_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs.monthly.html_monthly import (
    decode_html_bytes,
)
from jobs.monthly.html_issuers import parse_issuer
from jobs.monthly.ir_monthly_html_r2 import (
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

from jobs.monthly.ir_monthly_r2 import dest_filename, dest_path, looks_like_file
from monthly_html_fixtures import (
    CAWACHI_HTML,
    COLOWIDE_HTML,
    MOS_HTML,
    ROYAL_DUAL_BRAND_HTML,
    SEKISUI_MONTH_HTML,
)
from monthly_html_fixtures import module_force_units as _module_force_units


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
    import jobs.monthly.html_issuers.i9843 as nitori_mod

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


def test_hash_skip_and_reparse(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    html = CAWACHI_HTML.encode("utf-8")
    bodies = {"https://example.test/ir/": html}
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
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
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "stale_sidecar" / "monthly"
    bodies = {"https://example.test/royal/": ROYAL_DUAL_BRAND_HTML.encode("utf-8")}

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
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
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "force_resave" / "monthly"
    bodies = {"https://example.test/ir/": CAWACHI_HTML.encode("utf-8")}

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
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
    import jobs.monthly.ir_monthly_html_r2 as mod

    base = tmp / "force_usage"
    base.mkdir()
    calls: list[str] = []

    def boom(*_a: object, **_k: object) -> bytes:
        calls.append("called")
        raise AssertionError("should not load")

    orig_get = mod.http_get
    orig_map = mod.load_stock_product_map
    mod.http_get = boom
    mod.load_stock_product_map = boom
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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        hits.append(url)
        return b"<html></html>"

    orig_get = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.load_stock_product_map = lambda **_k: {"26640": ("011", "カワチ薬品")}
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
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    url = eir_url("7616", 0)

    def fake_get(u: str, timeout: float = 30.0) -> bytes:
        if u != url:
            raise RuntimeError(u)
        return EIR_JSONP.encode("utf-8")

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
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
    import jobs.monthly.ir_monthly_html_r2 as mod

    def boom(url: str, timeout: float = 30.0) -> bytes:
        raise RuntimeError(f"network {url}")

    orig = mod.http_get
    mod.http_get = boom
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

    from jobs import _r2common
    import jobs.monthly.ir_monthly_html_r2 as mod

    url = "https://srsholdings.com/pages/ir-library-monthly/"
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30.0, context=None):
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

    from jobs import _r2common
    import jobs.monthly.ir_monthly_html_r2 as mod

    def fake_urlopen(req, timeout=30.0, context=None):
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

    import jobs.monthly.ir_monthly_html_r2 as mod

    def fake_urlopen(req, timeout=30.0, context=None):
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
    import jobs.monthly.ir_monthly_html_r2 as mod

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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        if url == srs_url:
            raise _http_err(url, 429, "Too Many Requests")
        if url == kawa_url:
            return CAWACHI_HTML.encode("utf-8")
        raise RuntimeError(url)

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.load_stock_product_map = lambda **_k: {
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
    import jobs.monthly.ir_monthly_html_r2 as mod

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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        raise _http_err(url, 429, "Too Many Requests")

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.load_stock_product_map = lambda **_k: {
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
    import jobs.monthly.ir_monthly_html_r2 as mod

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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        raise _http_err(url, 404, "Not Found")

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.load_stock_product_map = lambda **_k: {"81630": ("011", "ＳＲＳホールディングス")}
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
    import jobs.monthly.ir_monthly_html_r2 as mod

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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        return b"<html></html>"

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.load_stock_product_map = lambda **_k: {"26640": ("011", "カワチ"), "27420": ("011", "ハローズ")}
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


SEKISUI_REDIRECT_SHELL = """
<html><head>
<link rel="canonical" href="https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/">
</head><body><h1>2026年：受注速報</h1></body></html>
"""


def test_follow_re_uses_redirected_page_url_without_anchors() -> None:
    """リダイレクト後の年 URL は <a> が無くても follow_re 候補。"""
    year = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/"
    index = "https://www.sekisuihouse.co.jp/company/financial/order/"
    shell = "<html><body><h1>2026年：受注速報</h1></body></html>"
    url = pick_follow_url(shell, year, r"orders_20\d{2}")
    if url != year:
        _fail(url)
    via_canonical = pick_follow_url(SEKISUI_REDIRECT_SHELL, index, r"orders_20\d{2}")
    if via_canonical != year:
        _fail(via_canonical)
    child = pick_follow_url(SEKISUI_REDIRECT_SHELL, year, r"orders_20\d{2}", under=year)
    if child is not None:
        _fail(f"canonical self is not a child {child}")


def test_follow_re_resolves_relative_hrefs_against_redirected_base() -> None:
    html = """
    <html><body>
    <a href="2026_8">8月</a>
    <a href="2026_10">10月</a>
    </body></html>
    """
    year = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/"
    url = pick_follow_url(html, year, r"orders_20\d{2}")
    if url != year + "2026_10":
        _fail(url)
    try:
        pick_follow_url(
            html,
            "https://www.sekisuihouse.co.jp/company/financial/order/",
            r"orders_20\d{2}",
        )
    except ValueError as e:
        if "no follow url" not in str(e):
            _fail(e)
    else:
        _fail("relative hrefs must not match against the pre-redirect index")


def test_follow_re_page_url_does_not_outrank_newer_link() -> None:
    listing = """
    <html><body>
    <a href="/ir/library/recent/fy2026/">2026</a>
    </body></html>
    """
    url = pick_follow_url(
        listing,
        "https://www.watami.co.jp/ir/library/recent/fy2025/",
        r"fy20\d{2}",
    )
    if url != "https://www.watami.co.jp/ir/library/recent/fy2026/":
        _fail(url)


def test_follow_year_then_latest_month(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_html_r2 as mod

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

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        if url not in bodies:
            raise RuntimeError(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
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


def _sekisui_issuer() -> HtmlIssuer:
    return HtmlIssuer(
        code="1928",
        name="積水ハウス",
        index_url="https://www.sekisuihouse.co.jp/company/financial/order/",
        title="月次",
        source="html",
        eir_code="1928",
        eir_n=0,
        eir_title_re="月次",
        follow_re=r"orders_20\d{2}",
        parse=True,
    )


def test_follow_index_redirect_shell_then_year_then_month(tmp: Path) -> None:
    """index が年ページへ 302。ホップ後の HTML に orders_20 の <a> が無い。"""
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "sekisui_redirect"
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
        index: SEKISUI_REDIRECT_SHELL.encode("utf-8"),
        year: year_html.encode("utf-8"),
        month: SEKISUI_MONTH_HTML.encode("utf-8"),
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        calls.append(url)
        if url not in bodies:
            raise RuntimeError(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.SLEEP_SEC = 0
    try:
        n, s, k, f = fetch_issuer(
            _sekisui_issuer(),
            dest,
            {"19280": ("011", "積水ハウス")},
            date(2026, 9, 14),
            dry_run=False,
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"redirect canonical {n, s, k, f} calls={calls}")
        if calls != [index, year, month]:
            _fail(f"expected index→year→month via canonical {calls}")
        htmls = list((dest / "1928").glob("*.html"))
        jsons = list((dest / "1928").glob("*.ingest.json"))
        if len(htmls) != 1 or len(jsons) != 1:
            _fail(f"files {htmls} {jsons}")
        payload = json.loads(jsons[0].read_text(encoding="utf-8"))
        if payload["as_of"] != "2026-08-31" or payload["rows"][0]["values"] != {"8": 94}:
            _fail(payload)
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_follow_index_redirect_final_url_without_canonical(tmp: Path) -> None:
    """geturl() だけが年 URL。canonical も <a> も無いホップ後ページ。"""
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "sekisui_final"
    index = "https://www.sekisuihouse.co.jp/company/financial/order/"
    year = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/"
    month = "https://www.sekisuihouse.co.jp/company/financial/order/orders_2026/2026_8"
    shell = b"<html><body><h1>year</h1></body></html>"
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
        year: year_html.encode("utf-8"),
        month: SEKISUI_MONTH_HTML.encode("utf-8"),
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0):
        calls.append(url)
        if url == index:
            return shell, year
        if url not in bodies:
            raise RuntimeError(url)
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get
    mod.SLEEP_SEC = 0
    try:
        n, s, k, f = fetch_issuer(
            _sekisui_issuer(),
            dest,
            {"19280": ("011", "積水ハウス")},
            date(2026, 9, 14),
            dry_run=False,
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"redirect geturl {n, s, k, f} calls={calls}")
        if calls != [index, year, month]:
            _fail(f"expected index→year→month via final url {calls}")
        jsons = list((dest / "1928").glob("*.ingest.json"))
        if len(jsons) != 1:
            _fail(f"json {jsons}")
        payload = json.loads(jsons[0].read_text(encoding="utf-8"))
        if payload["as_of"] != "2026-08-31" or payload["rows"][0]["values"] != {"8": 94}:
            _fail(payload)
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_parse_false_saves_html_without_json(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_html_r2 as mod

    dest = tmp / "monthly"
    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = lambda url, timeout=30.0: b"<html><body><p>graph only</p></body></html>"
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
    import jobs.monthly.ir_monthly_html_r2 as mod

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
    mod.http_get = lambda url, timeout=30.0: bare.encode("utf-8")
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


def test_7522_index_follows_js_location_to_fy2026() -> None:
    """目次の最新年度は <a> ではなく location.href。"""
    path = ROOT / "tests" / "monthly" / "testdata" / "html_7522" / "index-recent.html"
    html = decode_html_bytes(path.read_bytes())
    live = {i.code: i for i in load_html_registry(ROOT / "config" / "monthly" / "ir_monthly.json")}
    iss = live["7522"]
    url = pick_follow_url(html, iss.index_url, iss.follow_re)
    if url != "https://www.watami.co.jp/ir/library/recent/fy2026/":
        _fail(url)


def main() -> None:
    from tempfile import TemporaryDirectory

    test_eir_jsonp_picks_latest_monthly()
    test_dest_html_and_ingest_sidecar()
    test_load_html_registry()
    test_brand_issuers_reject_foreign_page_without_sidecar()
    test_eir_same_date_picks_later_fy()
    test_eir_skips_empty_url_same_date()
    test_follow_re_picks_latest_year()
    test_7522_index_follows_js_location_to_fy2026()
    test_follow_re_picks_latest_month_not_year_folder()
    test_follow_re_uses_redirected_page_url_without_anchors()
    test_follow_re_resolves_relative_hrefs_against_redirected_base()
    test_follow_re_page_url_does_not_outrank_newer_link()
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
        test_follow_index_redirect_shell_then_year_then_month(p)
        test_follow_index_redirect_final_url_without_canonical(p)
    print("ok: ir monthly html r2")


if __name__ == "__main__":
    main()

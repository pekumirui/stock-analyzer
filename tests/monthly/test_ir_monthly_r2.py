"""ir_monthly_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs._r2common import (  # noqa: E402
    BROWSER_UA,
    HTTP_RETRY_ATTEMPTS,
    USER_AGENT,
    _aia_chains_to_default_trust,
    _is_incomplete_chain,
    aia_ca_issuer_uris,
    backoff_delay,
    retry_after_seconds,
    ssl_context_with_aia,
)
from jobs.monthly.ir_monthly_r2 import (  # noqa: E402
    FileLink,
    Issuer,
    dest_filename,
    dest_path,
    dest_title,
    existing_hashes,
    fetch_issuer,
    filter_links,
    http_get,
    load_registry,
    pick_latest_year,
    looks_like_file,
    parse_file_links,
    run,
    sha256_bytes,
    url_stem,
)
from jobs.monthly.tdnet_monthly_r2 import display_code  # noqa: E402

KURA_HTML = """
<html><body>
<a href="../upload_file/m005-m005_05/0903month.pdf">月次報告書を更新しました</a>
<a href="../upload_file/m005-m005_05/getuji.pdf">月次報告書を更新しました</a>
<a href="../upload_file/m005-m005_05/2024getuji.pdf">月次報告書を更新しました</a>
<a href="../upload_file/m005-m005_05/ir2019month.pdf">2019年10月期月次報告書</a>
<a href="https://example.test/other.html">IRトップ</a>
</body></html>
"""

SEVEN_HTML = """
<html><body>
<p>
<a href="/ir/file/financial/mh/Monthly_Business_Performance_FY2026_0910.xlsx"
   class="pbOpenNewWindow">2027年2月期<span>[XLSX:42KB]</span></a>
</p>
<!-- <p>
<a href="/ir/file/financial/mh/Monthly_Business_Performance_FY2024_0716.xlsx"
   class="pbOpenNewWindow">2025年2月期<span>[XLSX:43KB]</span></a>
</p> -->
<p>
<a href="/ir/file/financial/mh/Monthly_Business_Performance_FY2025.xlsx">2026年2月期[XLSX:44KB]</a>
</p>
<a href="/ir/file/financial/other.pdf">説明会資料</a>
</body></html>
"""

BOTH_HTML = """
<html><body>
<a href="/files/2026-08.xlsx">8月 xlsx</a>
<a href="/files/2026-08.pdf">8月 pdf</a>
<a href="/files/2026-07.pdf">7月 pdf</a>
</body></html>
"""

XLS_HTML = """
<html><body>
<a href="/monthly-report/__icsFiles/afieldfile/2026/09/02/202608.pdf">PDF形式</a>
<a href="/monthly-report/__icsFiles/afieldfile/2026/09/02/history_1.xls">エクセル形式</a>
</body></html>
"""


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def test_parse_kura_relative() -> None:
    base = "https://www.kurasushi.co.jp/company/ir/library/monthly_reports.html"
    links = parse_file_links(KURA_HTML, base)
    urls = [ln.url for ln in links]
    expect = (
        "https://www.kurasushi.co.jp/company/ir/upload_file/m005-m005_05/0903month.pdf"
    )
    if expect not in urls:
        _fail(f"kura relative not joined: {urls}")
    if any("upload_file/m005-m005_05/0903month.pdf" in u and "/company/ir/" not in u for u in urls):
        _fail(f"joined to site root: {urls}")
    stems = {ln.stem for ln in links}
    if stems != {"0903month", "getuji", "2024getuji", "ir2019month"}:
        _fail(f"kura stems: {stems}")
    if any(ln.ext != "pdf" for ln in links):
        _fail(f"kura ext: {links}")


def test_parse_seven_skips_html_comment() -> None:
    base = "https://www.7andi.com/ir/financial/monthly_highlight.html"
    links = parse_file_links(SEVEN_HTML, base)
    urls = [ln.url for ln in links]
    if any("FY2024_0716" in u for u in urls):
        _fail(f"commented xlsx leaked: {urls}")
    if len([ln for ln in links if ln.ext == "xlsx"]) != 2:
        _fail(f"seven xlsx count: {links}")
    texts = [ln.text for ln in links if ln.ext == "xlsx"]
    if texts != ["2027年2月期[XLSX:42KB]", "2026年2月期[XLSX:44KB]"]:
        _fail(f"seven link text (nested span kept): {texts}")
    pdfs = [ln for ln in links if ln.ext == "pdf"]
    if len(pdfs) != 1:
        _fail(f"seven pdf: {pdfs}")


def test_pick_latest_year_sokuho() -> None:
    html = """
    <html><body>
    <a href="/ir/monthly/sokuho2023.xlsx">2024年3月期</a>
    <a href="/ir/monthly/sokuho2024.xlsx">2025年3月期</a>
    <a href="/ir/monthly/sokuho2025.xlsx">2026年3月期</a>
    <a href="/ir/monthly/sokuho2026-2.xlsx">2027年3月期</a>
    <a href="/ir/monthly/sokuho2027.xlsx">2027年3月期</a>
    </body></html>
    """
    base = "https://www.tabaya-holdings.co.jp/ir/monthly/"
    links = parse_file_links(html, base)
    all_xlsx = filter_links(links, r"sokuho.*\.xlsx(?:$|[?#])")
    if [ln.stem for ln in all_xlsx] != [
        "sokuho2023", "sokuho2024", "sokuho2025", "sokuho2026-2", "sokuho2027",
    ]:
        _fail(f"sokuho all: {[ln.stem for ln in all_xlsx]}")
    picked = filter_links(links, r"sokuho.*\.xlsx(?:$|[?#])", latest_year=True)
    if [ln.stem for ln in picked] != ["sokuho2027"]:
        _fail(f"latest year: {[ln.stem for ln in picked]}")
    if [ln.stem for ln in pick_latest_year(all_xlsx)] != ["sokuho2027"]:
        _fail("pick_latest_year")


def test_filter_xlsx_priority() -> None:
    base = "https://www.example.test/ir/"
    links = parse_file_links(BOTH_HTML, base)
    got = filter_links(links, r"\.(pdf|xlsx)(?:$|[?#])")
    if [ln.ext for ln in got] != ["xlsx"]:
        _fail(f"xlsx should drop pdf: {got}")
    pdf_only = filter_links(links, r"\.pdf(?:$|[?#])")
    if {ln.stem for ln in pdf_only} != {"2026-08", "2026-07"}:
        _fail(f"pdf filter: {pdf_only}")
    seven = parse_file_links(SEVEN_HTML, "https://www.7andi.com/ir/financial/monthly_highlight.html")
    picked = filter_links(seven, r"Monthly_Business_Performance.*\.xlsx(?:$|[?#])")
    if any(ln.ext != "xlsx" for ln in picked) or len(picked) != 2:
        _fail(f"seven filter: {picked}")
    if any("other.pdf" in ln.url for ln in picked):
        _fail("unrelated pdf survived")
    kentaku = parse_file_links(XLS_HTML, "https://www.kentaku.co.jp/ir/library/")
    xls_only = filter_links(kentaku, r"history_.*\.xls(?:$|[?#])")
    if [ln.ext for ln in xls_only] != ["xls"] or xls_only[0].stem != "history_1":
        _fail(f"kentaku xls filter: {xls_only}")
    sheet_over_pdf = filter_links(kentaku, r"\.(pdf|xlsx|xls)(?:$|[?#])")
    if [ln.ext for ln in sheet_over_pdf] != ["xls"]:
        _fail(f"xls should drop pdf: {sheet_over_pdf}")


def test_dest_filename_ext_and_unique_stem() -> None:
    pdf = dest_filename("2026-09-12", "2695", "くら寿司", "getuji", "pdf")
    if pdf != "20260912_2695_くら寿司_getuji.pdf":
        _fail(f"pdf dest: {pdf!r}")
    xlsx = dest_filename(
        "2026-09-12",
        "33820",
        "セブン＆アイ・ホールディングス",
        "Monthly_Business_Performance_FY2026_0910",
        "xlsx",
    )
    if not xlsx.startswith("20260912_3382_") or not xlsx.endswith(".xlsx"):
        _fail(f"xlsx dest: {xlsx!r}")
    if "33820" in xlsx:
        _fail(f"padded code in dest: {xlsx!r}")
    a = dest_filename("2026-09-12", "2695", "くら寿司", "getuji", "pdf")
    b = dest_filename("2026-09-12", "2695", "くら寿司", "0903month", "pdf")
    if a == b:
        _fail("same-day same title without stem collides")
    dest = Path("/home/stock/r2-share/monthly")
    p = dest_path(dest, "2026-09-12", "2695", "くら寿司", "getuji", "pdf")
    if p != dest / "2695" / "20260912_2695_くら寿司_getuji.pdf":
        _fail(f"dest_path: {p}")
    try:
        dest_filename("2026-09-12", "2695", "くら寿司", "x", "docx")
        _fail("docx should be rejected")
    except ValueError:
        pass


def test_dest_title_hash_stem() -> None:
    hashed = FileLink(
        url="https://example.test/ir/pdf-cms/2f90f64b7183cc1c5c878e15385bd37465fc2503.pdf",
        text="2026年7月度",
        ext="pdf",
        stem="2f90f64b7183cc1c5c878e15385bd37465fc2503",
    )
    counts = {"2026年7月度": 1, "月次報告書を更新しました": 2}
    if dest_title(hashed, "売上月次情報", counts) != "2026年7月度":
        _fail("hash stem should use unique link text")
    dup = FileLink(
        url="https://example.test/getuji.pdf",
        text="月次報告書を更新しました",
        ext="pdf",
        stem="getuji",
    )
    if dest_title(dup, "月次報告書", counts) != "getuji":
        _fail("human stem stays stem even if text exists")
    hashed_dup = FileLink(
        url="https://example.test/aaa.pdf",
        text="月次報告書を更新しました",
        ext="pdf",
        stem="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    if dest_title(hashed_dup, "月次", counts) != hashed_dup.stem:
        _fail("duplicate text must not replace hash stem")


def test_url_stem_and_magic() -> None:
    got = url_stem(
        "https://files.example.test/assets/monthly-sales-report_2608.xlsx?h=1"
    )
    if got != "monthly-sales-report_2608":
        _fail(f"url_stem: {got!r}")
    if not looks_like_file(b"%PDF-1.4 hello", "pdf"):
        _fail("pdf magic")
    if looks_like_file(b"<html>", "pdf"):
        _fail("html is not pdf")
    if not looks_like_file(b"PK\x03\x04rest", "xlsx"):
        _fail("xlsx magic")
    if looks_like_file(b"%PDF-1.4", "xlsx"):
        _fail("pdf is not xlsx")
    if not looks_like_file(b"\xd0\xcf\x11\xe0rest", "xls"):
        _fail("xls magic")
    if looks_like_file(b"PK\x03\x04rest", "xls"):
        _fail("xlsx is not xls")
    xls_name = dest_filename("2026-09-18", "1878", "大東建託", "history_1", "xls")
    if xls_name != "20260918_1878_大東建託_history_1.xls":
        _fail(f"xls dest: {xls_name!r}")
    html = dest_filename("2026-09-14", "2664", "カワチ薬品", "月次", "html")
    if html != "20260914_2664_カワチ薬品_月次.html":
        _fail(f"html dest: {html!r}")
    if not looks_like_file(b"  <html>", "html"):
        _fail("html magic")


def test_load_registry(tmp: Path) -> None:
    path = tmp / "ir_monthly.json"
    path.write_text(
        json.dumps(
            {
                "issuers": [
                    {
                        "code": "3382",
                        "name": "セブン",
                        "index_url": "https://www.7andi.com/ir/financial/monthly_highlight.html",
                        "link_re": r"\.xlsx(?:$|[?#])",
                        "title": "月次営業情報",
                    },
                    {
                        "code": "26950",
                        "name": "くら寿司",
                        "index_url": "https://www.kurasushi.co.jp/company/ir/library/monthly_reports.html",
                        "link_re": r"\.pdf(?:$|[?#])",
                        "title": "月次報告書",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = load_registry(path)
    if [r.code for r in rows] != ["3382", "2695"]:
        _fail(f"codes: {[r.code for r in rows]}")
    if display_code(rows[1].code) != "2695":
        _fail("26950 should normalize")
    repo_cfg = ROOT / "config" / "monthly" / "ir_monthly.json"
    live = load_registry(repo_cfg)
    codes = {i.code for i in live}
    if codes != {"1878", "2695", "3088", "3382", "3397", "7606"}:
        _fail(f"repo issuers: {codes}")
    kentaku = next(i for i in live if i.code == "1878")
    if "history_" not in kentaku.link_re or "xls" not in kentaku.link_re:
        _fail(f"1878 link_re: {kentaku.link_re}")
    ua = next(i for i in live if i.code == "7606")
    if "sokuho" not in ua.link_re or "xlsx" not in ua.link_re or not ua.latest_year:
        _fail(f"7606 issuer: {ua}")
    bad = tmp / "bad.json"
    bad.write_text(json.dumps({"issuers": [{"code": "12", "name": "x", "index_url": "http://x", "link_re": "."}]}), encoding="utf-8")
    try:
        load_registry(bad)
        _fail("short code should fail")
    except ValueError:
        pass


def test_hash_skip_and_xlsx_priority_fetch(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_r2 as mod

    dest = tmp / "monthly"
    pdf = b"%PDF-1.4 old"
    xlsx = b"PK\x03\x04sheet"
    index = (
        "<html><body>"
        '<a href="/a.xlsx">xlsx</a>'
        '<a href="/a.pdf">pdf</a>'
        "</body></html>"
    )
    bodies = {
        "https://example.test/ir/": index.encode("utf-8"),
        "https://example.test/a.xlsx": xlsx,
        "https://example.test/a.pdf": pdf,
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        if url not in bodies:
            raise RuntimeError(f"unexpected url {url}")
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = Issuer(
            code="3382",
            name="セブン",
            index_url="https://example.test/ir/",
            link_re=r"\.(pdf|xlsx)(?:$|[?#])",
            title="月次",
        )
        stock_map = {"33820": ("011", "セブン")}
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 12), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"first fetch: listed={n} saved={s} skipped={k} failed={f} calls={calls}")
        if "https://example.test/a.pdf" in calls:
            _fail(f"pdf downloaded despite xlsx: {calls}")
        files = list((dest / "3382").glob("*.xlsx"))
        if len(files) != 1:
            _fail(f"files: {files}")
        if files[0].name != "20260912_3382_セブン_a.xlsx":
            _fail(f"name: {files[0].name}")

        calls.clear()
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 13), dry_run=False)
        if (n, s, k, f) != (1, 0, 1, 0):
            _fail(f"hash skip: listed={n} saved={s} skipped={k} failed={f}")
        if len(list((dest / "3382").glob("*.xlsx"))) != 1:
            _fail("hash skip wrote a second file")

        bodies["https://example.test/a.xlsx"] = b"PK\x03\x04sheet-v2"
        calls.clear()
        n, s, k, f = fetch_issuer(iss, dest, stock_map, date(2026, 9, 13), dry_run=False)
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"changed content: listed={n} saved={s} skipped={k} failed={f}")
        names = sorted(p.name for p in (dest / "3382").glob("*.xlsx"))
        if names != ["20260912_3382_セブン_a.xlsx", "20260913_3382_セブン_a.xlsx"]:
            _fail(f"overwrite history: {names}")
        hashes = existing_hashes(dest / "3382")
        if len(hashes) != 2:
            _fail(f"hashes: {hashes}")
        if sha256_bytes(xlsx) not in hashes:
            _fail("old hash missing")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_xls_hash_skip_fetch(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_r2 as mod

    dest = tmp / "monthly"
    xls = b"\xd0\xcf\x11\xe0sheet"
    index = (
        "<html><body>"
        '<a href="/history_1.xls">excel</a>'
        '<a href="/202608.pdf">pdf</a>'
        "</body></html>"
    )
    bodies = {
        "https://example.test/kentaku/": index.encode("utf-8"),
        "https://example.test/history_1.xls": xls,
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        if url not in bodies:
            raise RuntimeError(f"unexpected url {url}")
        return bodies[url]

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = Issuer(
            code="1878",
            name="大東建託",
            index_url="https://example.test/kentaku/",
            link_re=r"history_.*\.xls(?:$|[?#])",
            title="月次",
        )
        n, s, k, f = fetch_issuer(
            iss, dest, {"18780": ("011", "大東建託")}, date(2026, 9, 18), dry_run=False
        )
        if (n, s, k, f) != (1, 1, 0, 0):
            _fail(f"xls fetch: listed={n} saved={s} skipped={k} failed={f} calls={calls}")
        if any(c.endswith(".pdf") for c in calls):
            _fail(f"pdf downloaded despite xls: {calls}")
        files = list((dest / "1878").glob("*.xls"))
        if len(files) != 1 or files[0].name != "20260918_1878_大東建託_history_1.xls":
            _fail(f"xls files: {files}")
        hashes = existing_hashes(dest / "1878")
        if sha256_bytes(xls) not in hashes:
            _fail(f"xls hash missing: {hashes}")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_http_get_falls_back_on_403() -> None:
    from urllib.error import HTTPError
    from unittest.mock import MagicMock, patch

    from jobs import _r2common
    import jobs.monthly.ir_monthly_r2 as mod

    uas: list[str] = []

    def fake_urlopen(req, timeout=30.0):  # noqa: ARG001
        ua = req.get_header("User-agent") or req.get_header("User-Agent")
        uas.append(ua)
        if ua == USER_AGENT:
            raise HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"ok-body"
        cm.__exit__.return_value = False
        return cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        body = http_get("https://www.kentaku.co.jp/ir/library/monthly-report/")
    if body != b"ok-body":
        _fail(f"fallback body: {body!r}")
    if uas != [USER_AGENT, BROWSER_UA]:
        _fail(f"uas: {uas}")
    if mod.http_get is not _r2common.http_get:
        _fail("http_get drifted from _r2common")


def test_http_get_result_returns_redirect_url() -> None:
    from unittest.mock import MagicMock, patch

    from jobs._r2common import http_get_result

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        cm = MagicMock()
        resp = cm.__enter__.return_value
        resp.read.return_value = b"year-page"
        resp.geturl.return_value = "https://example.test/order/orders_2026/"
        cm.__exit__.return_value = False
        return cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        body, final = http_get_result("https://example.test/order/")
    if body != b"year-page":
        _fail(f"redirect body: {body!r}")
    if final != "https://example.test/order/orders_2026/":
        _fail(f"redirect url: {final}")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        raw = http_get("https://example.test/order/")
    if raw != b"year-page":
        _fail(f"http_get still bytes: {raw!r}")


def _tlv(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n >= 128:
        raise AssertionError("short-form test helper")
    return bytes((tag, n)) + body


def test_aia_ca_issuer_uris() -> None:
    uri = b"http://example.test/ca.crt"
    ocsp = b"http://example.test/ocsp"
    ca_iss = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x02))
    ocsp_oid = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x01))
    aia_oid = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x01, 0x01))
    acc_ca = _tlv(0x30, _tlv(0x06, ca_iss) + _tlv(0x86, uri))
    acc_ocsp = _tlv(0x30, _tlv(0x06, ocsp_oid) + _tlv(0x86, ocsp))
    octet = _tlv(0x30, acc_ca + acc_ocsp)
    der = _tlv(0x06, aia_oid) + _tlv(0x04, octet)
    got = aia_ca_issuer_uris(der)
    if got != ["http://example.test/ca.crt"]:
        _fail(f"aia uris: {got}")
    if aia_ca_issuer_uris(b"\x30\x00") != []:
        _fail("empty der should be empty")
    if aia_ca_issuer_uris(_tlv(0x06, ocsp_oid) + _tlv(0x04, octet)) != []:
        _fail("non-AIA oid should be ignored")


def test_incomplete_chain_is_issuer_missing_only() -> None:
    import ssl as sslmod

    missing = sslmod.SSLCertVerificationError(
        "certificate verify failed: unable to get local issuer certificate"
    )
    expired = sslmod.SSLCertVerificationError(
        "certificate verify failed: certificate has expired"
    )
    if not _is_incomplete_chain(missing):
        _fail("missing issuer must be incomplete chain")
    if _is_incomplete_chain(expired):
        _fail("expired cert must not take the AIA path")


def _self_signed_pem() -> bytes:
    import os
    import subprocess
    import tempfile

    td = tempfile.mkdtemp(prefix="aia-self-")
    key = os.path.join(td, "k.pem")
    cert = os.path.join(td, "c.pem")
    proc = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key, "-out", cert, "-days", "1", "-nodes",
            "-subj", "/CN=aia-untrusted-test",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"openssl req: {proc.stderr!r}")
    return Path(cert).read_bytes()


def test_aia_rejects_untrusted_and_accepts_system_ca() -> None:
    import ssl as sslmod
    from unittest.mock import MagicMock, patch

    from jobs import _r2common

    self_pem = _self_signed_pem()
    if _aia_chains_to_default_trust(self_pem):
        _fail("self-signed AIA blob must not chain to the default store")
    cas = sslmod.create_default_context().get_ca_certs(binary_form=True)
    if not cas:
        _fail("default context has no CA certs to prove the allow path")
    if not _aia_chains_to_default_trust(cas[0]):
        _fail("system CA must chain to the default store")

    uri = b"http://example.test/ca.crt"
    ca_iss = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x02))
    aia_oid = bytes((0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x01, 0x01))
    acc_ca = _tlv(0x30, _tlv(0x06, ca_iss) + _tlv(0x86, uri))
    octet = _tlv(0x30, acc_ca)
    leaf = _tlv(0x06, aia_oid) + _tlv(0x04, octet)

    def fake_urlopen(req, timeout=30.0):  # noqa: ARG001
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = self_pem
        cm.__exit__.return_value = False
        return cm

    _r2common._AIA_CTX.clear()
    with (
        patch.object(_r2common, "_peer_leaf_der", return_value=leaf),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        ctx = ssl_context_with_aia("https://untrusted-aia.example.test/ir")
    if ctx is not None:
        _fail("untrusted AIA must not produce a retry context")


def test_http_get_aia_on_incomplete_chain() -> None:
    from urllib.error import URLError
    from unittest.mock import MagicMock, patch
    import ssl as sslmod

    from jobs import _r2common

    contexts: list[object] = []

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        contexts.append(context)
        if context is None:
            raise URLError(
                sslmod.SSLCertVerificationError(
                    "certificate verify failed: unable to get local issuer certificate"
                )
            )
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"<html>aia-ok</html>"
        cm.__exit__.return_value = False
        return cm

    dummy = sslmod.create_default_context()
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(_r2common, "ssl_context_with_aia", return_value=dummy) as aia,
    ):
        body = http_get("https://www.three-f.co.jp/ir/recentFact.html")
    if body != b"<html>aia-ok</html>":
        _fail(f"aia body: {body!r}")
    if aia.call_count != 1:
        _fail(f"aia calls: {aia.call_count}")
    if contexts != [None, dummy]:
        _fail(f"contexts: {contexts}")
    if len(contexts) != 2:
        _fail(f"should not fall back to second UA: {len(contexts)}")


def _http_err(url: str, code: int, msg: str, retry_after: str | None = None):
    from email.message import EmailMessage
    from urllib.error import HTTPError

    hdrs = None
    if retry_after is not None:
        hdrs = EmailMessage()
        hdrs["Retry-After"] = retry_after
    return HTTPError(url, code, msg, hdrs=hdrs, fp=None)


def test_retry_after_seconds_and_backoff() -> None:
    from urllib.error import HTTPError

    err = _http_err("https://example.test/", 429, "Too Many Requests", "7")
    if retry_after_seconds(err) != 7.0:
        _fail(f"retry-after seconds: {retry_after_seconds(err)}")
    http_date = _http_err(
        "https://example.test/",
        429,
        "Too Many Requests",
        "Wed, 21 Oct 2015 07:28:07 GMT",
    )
    got = retry_after_seconds(http_date, now=1445412480.0)
    if got != 7.0:
        _fail(f"retry-after http-date: {got}")
    bare = HTTPError("https://example.test/", 429, "Too Many Requests", hdrs=None, fp=None)
    if retry_after_seconds(bare) is not None:
        _fail("missing header must be None")
    rng = __import__("random").Random(0)
    if backoff_delay(0, rng=rng) <= 0:
        _fail("backoff must be positive")
    if backoff_delay(0, retry_after=7.0) != 7.0:
        _fail("retry-after wins")
    if backoff_delay(0, retry_after=120.0) != 60.0:
        _fail("retry-after cap")
    if backoff_delay(5, retry_after=None, rng=__import__("random").Random(1)) > 60.0:
        _fail("exp cap")


def test_http_get_retries_429_then_succeeds() -> None:
    from unittest.mock import MagicMock, patch

    from jobs import _r2common

    url = "https://srsholdings.com/pages/ir-library-monthly/"
    uas: list[str] = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        uas.append(req.get_header("User-agent") or req.get_header("User-Agent"))
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(req.full_url, 429, "Too Many Requests")
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"<html>ok</html>"
        cm.__exit__.return_value = False
        return cm

    slept: list[float] = []
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(_r2common.time, "sleep", slept.append),
        patch.object(_r2common.random, "random", return_value=0.5),
    ):
        body = http_get(url)
    if body != b"<html>ok</html>":
        _fail(f"429 then success body: {body!r}")
    if calls["n"] != 2:
        _fail(f"429 then success calls: {calls['n']}")
    if slept != [5.0]:
        _fail(f"429 then success wait: {slept}")
    if uas != [USER_AGENT, USER_AGENT]:
        _fail(f"429 must not switch UA: {uas}")


def test_http_get_honors_retry_after() -> None:
    from unittest.mock import MagicMock, patch

    from jobs import _r2common

    url = "https://srsholdings.com/pages/ir-library-monthly/"
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_err(req.full_url, 503, "Service Unavailable", "3")
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"ok"
        cm.__exit__.return_value = False
        return cm

    slept: list[float] = []
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(_r2common.time, "sleep", slept.append),
    ):
        body = http_get(url)
    if body != b"ok" or slept != [3.0]:
        _fail(f"retry-after: body={body!r} slept={slept}")


def test_http_get_429_exhausted_raises() -> None:
    from urllib.error import HTTPError
    from unittest.mock import patch

    from jobs import _r2common

    url = "https://srsholdings.com/pages/ir-library-monthly/"
    uas: list[str] = []

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        uas.append(req.get_header("User-agent") or req.get_header("User-Agent"))
        raise _http_err(req.full_url, 429, "Too Many Requests")

    slept: list[float] = []
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(_r2common.time, "sleep", slept.append),
        patch.object(_r2common.random, "random", return_value=0.5),
    ):
        try:
            http_get(url)
        except HTTPError as e:
            if e.code != 429:
                _fail(e)
        else:
            _fail("exhausted 429 must raise")
    if len(uas) != HTTP_RETRY_ATTEMPTS:
        _fail(f"exhausted calls: {len(uas)}")
    if any(ua != USER_AGENT for ua in uas):
        _fail(f"exhausted must stay on job UA: {uas}")
    if slept != [5.0, 10.0, 20.0, 40.0, 60.0]:
        _fail(f"exhausted backoff: {slept}")


def test_http_get_404_does_not_retry() -> None:
    from urllib.error import HTTPError
    from unittest.mock import patch

    from jobs import _r2common

    calls = {"n": 0}

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        calls["n"] += 1
        raise _http_err(req.full_url, 404, "Not Found")

    slept: list[float] = []
    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch.object(_r2common.time, "sleep", slept.append),
    ):
        try:
            http_get("https://example.test/missing")
        except HTTPError as e:
            if e.code != 404:
                _fail(e)
        else:
            _fail("404 must raise")
    if calls["n"] != 1 or slept:
        _fail(f"404 retried: calls={calls['n']} slept={slept}")


def test_unknown_stock_skip(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_r2 as mod

    def boom(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        raise RuntimeError(f"network {url}")

    orig = mod.http_get
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = boom  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        iss = Issuer(
            code="2695",
            name="くら寿司",
            index_url="https://example.test/ir/",
            link_re=r"\.pdf(?:$|[?#])",
            title="月次",
        )
        n, s, k, f = fetch_issuer(
            iss, tmp / "monthly", {"72030": ("011", "トヨタ")}, date(2026, 9, 12), dry_run=False
        )
        if (n, s, k, f) != (0, 0, 1, 0):
            _fail(f"unknown-stock: listed={n} saved={s} skipped={k} failed={f}")
    finally:
        mod.http_get = orig
        mod.SLEEP_SEC = orig_sleep


def test_run_only_and_dry_run(tmp: Path) -> None:
    import jobs.monthly.ir_monthly_r2 as mod

    cfg = tmp / "ir.json"
    cfg.write_text(
        json.dumps(
            {
                "issuers": [
                    {
                        "code": "3382",
                        "name": "セブン",
                        "index_url": "https://example.test/seven/",
                        "link_re": r"\.xlsx(?:$|[?#])",
                        "title": "月次",
                    },
                    {
                        "code": "2695",
                        "name": "くら寿司",
                        "index_url": "https://example.test/kura/",
                        "link_re": r"\.pdf(?:$|[?#])",
                        "title": "月次",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bodies = {
        "https://example.test/seven/": b'<a href="/m.xlsx">m</a>',
        "https://example.test/kura/": b'<a href="/m.pdf">m</a>',
    }
    calls: list[str] = []

    def fake_get(url: str, timeout: float = 30.0) -> bytes:  # noqa: ARG001
        calls.append(url)
        return bodies[url]

    orig = mod.http_get
    orig_map = mod.load_stock_product_map
    orig_sleep = mod.SLEEP_SEC
    mod.http_get = fake_get  # type: ignore[assignment]
    mod.load_stock_product_map = lambda **_k: {"33820": ("011", "セブン"), "26950": ("011", "くら寿司")}  # type: ignore[assignment]
    mod.SLEEP_SEC = 0
    try:
        rc = run(tmp / "out", cfg, only=["3382"], dry_run=True, today=date(2026, 9, 12))
        if rc != 0:
            _fail(f"dry-run rc {rc}")
        if calls != ["https://example.test/seven/"]:
            _fail(f"--only should skip kura: {calls}")
        if list((tmp / "out").rglob("*.xlsx")):
            _fail("dry-run wrote files")
    finally:
        mod.http_get = orig
        mod.load_stock_product_map = orig_map
        mod.SLEEP_SEC = orig_sleep


def main() -> None:
    from tempfile import TemporaryDirectory

    test_parse_kura_relative()
    test_parse_seven_skips_html_comment()
    test_pick_latest_year_sokuho()
    test_filter_xlsx_priority()
    test_dest_filename_ext_and_unique_stem()
    test_dest_title_hash_stem()
    test_url_stem_and_magic()
    with TemporaryDirectory() as d:
        p = Path(d)
        test_load_registry(p)
        test_hash_skip_and_xlsx_priority_fetch(p)
        test_xls_hash_skip_fetch(p)
        test_unknown_stock_skip(p)
        test_run_only_and_dry_run(p)
    test_http_get_falls_back_on_403()
    test_http_get_result_returns_redirect_url()
    test_aia_ca_issuer_uris()
    test_http_get_aia_on_incomplete_chain()
    test_retry_after_seconds_and_backoff()
    test_http_get_retries_429_then_succeeds()
    test_http_get_honors_retry_after()
    test_http_get_429_exhausted_raises()
    test_http_get_404_does_not_retry()
    print("ok: ir monthly r2")


if __name__ == "__main__":
    main()

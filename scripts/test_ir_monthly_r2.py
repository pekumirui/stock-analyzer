"""ir_monthly_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ir_monthly_r2 import (  # noqa: E402
    FileLink,
    Issuer,
    dest_filename,
    dest_path,
    dest_title,
    existing_hashes,
    fetch_issuer,
    filter_links,
    load_registry,
    looks_like_file,
    parse_file_links,
    run,
    sha256_bytes,
    url_stem,
)
from tdnet_monthly_r2 import display_code  # noqa: E402

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
    pdfs = [ln for ln in links if ln.ext == "pdf"]
    if len(pdfs) != 1:
        _fail(f"seven pdf: {pdfs}")


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
    repo_cfg = ROOT / "config" / "ir_monthly.json"
    live = load_registry(repo_cfg)
    codes = {i.code for i in live}
    if codes != {"2695", "3088", "3382", "3397"}:
        _fail(f"repo issuers: {codes}")
    bad = tmp / "bad.json"
    bad.write_text(json.dumps({"issuers": [{"code": "12", "name": "x", "index_url": "http://x", "link_re": "."}]}), encoding="utf-8")
    try:
        load_registry(bad)
        _fail("short code should fail")
    except ValueError:
        pass


def test_hash_skip_and_xlsx_priority_fetch(tmp: Path) -> None:
    import ir_monthly_r2 as mod

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


def test_unknown_stock_skip(tmp: Path) -> None:
    import ir_monthly_r2 as mod

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
    import ir_monthly_r2 as mod

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
    test_filter_xlsx_priority()
    test_dest_filename_ext_and_unique_stem()
    test_dest_title_hash_stem()
    test_url_stem_and_magic()
    with TemporaryDirectory() as d:
        p = Path(d)
        test_load_registry(p)
        test_hash_skip_and_xlsx_priority_fetch(p)
        test_unknown_stock_skip(p)
        test_run_only_and_dry_run(p)
    print("ok: ir monthly r2")


if __name__ == "__main__":
    main()

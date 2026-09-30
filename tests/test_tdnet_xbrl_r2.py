"""tdnet_xbrl_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))

from app.config import XBRL_DEST  # noqa: E402
import tdnet_xbrl_r2 as xbrl_mod  # noqa: E402
from tdnet_xbrl_r2 import (  # noqa: E402
    LIST_LIMIT,
    NOCODE_DIR,
    UNMATCHED_DIR,
    append_manifest,
    build_manifest_row,
    date_window,
    dest_filename,
    dest_path,
    dest_route,
    fetch_xbrl_records,
    list_url,
    load_seen_xbrl_ids,
    part_path,
    pubdate_iso_date,
    pubdate_time,
    save_zip_bytes,
    tdnet_xbrl_id,
    to_code4,
    unmatched_reason,
    validate_zip,
    xbrl_id_from_record,
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _tiny_zip(payload: bytes = b"hello xbrl") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", payload)
    return buf.getvalue()


def test_to_code4() -> None:
    cases = (
        ("92790", "9279"),
        ("72030", "7203"),
        ("269A0", "269A"),
        ("386A0", "386A"),
        ("7203", "7203"),
        ("262A", "262A"),
        ("", ""),
        ("   ", ""),
    )
    for raw, expect in cases:
        got = to_code4(raw)
        if got != expect:
            _fail(f"to_code4({raw!r}) → {got!r} != {expect!r}")


def test_unmatched_and_dest_paths() -> None:
    stock_map: dict[str, tuple[str | None, str | None]] = {
        "92820": ("013", "ジャパン・インフラファンド投資法人"),
        "89850": ("013", "ジャパン・ホテル・リート投資法人"),
        "72030": ("011", "トヨタ自動車"),
        "92790": ("010", "ギフト"),
    }
    dest = Path("/home/stock/r2-share/xbrl")
    pub = "2026-08-05 19:00:00"
    title = "2026年3月期 決算短信〔日本基準〕（連結）"
    xid = "081220260805509698"

    normal = dest_path(dest, pub, "92790", title, xid, "ギフトHD", stock_map)
    if normal.parent != dest / "9279":
        _fail(f"normal dir: {normal}")
    if UNMATCHED_DIR in normal.parts:
        _fail(f"matched must not be unmatched: {normal}")
    if "20260805" not in normal.name or "9279" not in normal.name:
        _fail(f"filename missing pubdate/code4: {normal.name}")
    if xid not in normal.name or not normal.name.endswith(".zip"):
        _fail(f"filename missing xbrl_id/.zip: {normal.name}")
    if unmatched_reason("92790", "ギフトHD", stock_map) is not None:
        _fail("matched 9279 must not be unmatched")

    unknown = dest_path(dest, pub, "3032", title, xid, "ゴルフ・ドゥ", stock_map)
    if unknown.parent != dest / UNMATCHED_DIR / "3032":
        _fail(f"unknown dest: {unknown}")
    if unmatched_reason("3032", "ゴルフ・ドゥ", stock_map) != "unknown-stock":
        _fail("unknown-stock reason")
    route = dest_route("3032", "ゴルフ・ドゥ", stock_map)
    if route.reason != "unknown-stock":
        _fail(f"unknown route skipped? {route}")

    infra = dest_path(
        dest, pub, "9282", title, xid, "ジャパン・インフラファンド投資法人", stock_map
    )
    if infra.parent != dest / UNMATCHED_DIR / "9282":
        _fail(f"infra dest: {infra}")
    if unmatched_reason("9282", "ジャパン・インフラファンド投資法人", stock_map) != "infra-fund":
        _fail("infra-fund reason")

    reit = dest_path(
        dest, pub, "8985", title, xid, "ジャパン・ホテル・リート投資法人", stock_map
    )
    if reit.parent != dest / "8985":
        _fail(f"REIT should be matched: {reit}")

    nocode = dest_path(dest, pub, "", title, xid, "名無し", stock_map)
    if nocode.parent != dest / UNMATCHED_DIR / NOCODE_DIR:
        _fail(f"nocode dest: {nocode}")
    if dest_route("", "名無し", stock_map).reason != "nocode":
        _fail("empty code must route nocode, not skip")
    if NOCODE_DIR not in nocode.name.split("_") and NOCODE_DIR not in nocode.parts:
        _fail(f"nocode filename: {nocode}")

    empty: dict[str, tuple[str | None, str | None]] = {}
    if unmatched_reason("3032", "ゴルフ・ドゥ", empty) is not None:
        _fail("empty map must not unmatched-unknown")
    empty_unknown = dest_path(dest, pub, "3032", title, xid, "ゴルフ・ドゥ", empty)
    if empty_unknown.parent != dest / "3032":
        _fail(f"empty map unknown stays matched: {empty_unknown}")
    if unmatched_reason("9282", "○○インフラ投資法人", empty) != "infra-fund":
        _fail("empty map still infra via company_name")
    empty_infra = dest_path(dest, pub, "9282", title, xid, "○○インフラ投資法人", empty)
    if empty_infra.parent != dest / UNMATCHED_DIR / "9282":
        _fail(f"empty map infra unmatched: {empty_infra}")

    alnum = dest_filename(pub, "269A", title, xid)
    if not alnum.startswith("20260805_269A_") or not alnum.endswith(f"_{xid}.zip"):
        _fail(f"alnum filename: {alnum}")


def test_tdnet_xbrl_id() -> None:
    zip_url = (
        "https://webapi.yanoshin.jp/rd.php?"
        "https://www.release.tdnet.info/inbs/081220260805509698.zip"
    )
    got = tdnet_xbrl_id(zip_url)
    if got != "081220260805509698":
        _fail(f"zip id from url: {got!r}")
    if got == "1271048":
        _fail("used yanoshin numeric id")
    direct = "https://www.release.tdnet.info/inbs/081220260805509698.zip"
    if tdnet_xbrl_id(direct) != "081220260805509698":
        _fail(f"direct inbs zip: {tdnet_xbrl_id(direct)!r}")
    pdf = "https://www.release.tdnet.info/inbs/140120260805509698.pdf"
    if tdnet_xbrl_id(pdf) is not None:
        _fail("pdf url must not yield zip id")

    rec = {
        "url_xbrl": zip_url,
        "document_url": pdf,
        "id": "1271048",
    }
    if xbrl_id_from_record(rec) != "081220260805509698":
        _fail("prefer url_xbrl")
    rec2 = {"document_url": direct}
    if xbrl_id_from_record(rec2) != "081220260805509698":
        _fail("document_url .zip")
    rec3 = {"document_url": pdf, "title": "決算短信"}
    if xbrl_id_from_record(rec3) is not None:
        _fail("pdf-only record has no zip id")


def test_list_url() -> None:
    url = list_url(date(2026, 8, 5), date(2026, 8, 5))
    if "hasXBRL=1" not in url:
        _fail(f"missing hasXBRL: {url}")
    if "keyword=" in url:
        _fail(f"must not filter keyword: {url}")
    if "20260805-20260805.json" not in url:
        _fail(f"span: {url}")
    if LIST_LIMIT < 1000:
        _fail(f"LIST_LIMIT too small for a full day: {LIST_LIMIT}")
    qs = parse_qs(urlparse(url).query)
    if qs.get("limit") != [str(LIST_LIMIT)]:
        _fail(f"limit in URL: {url}")
    paged = list_url(date(2026, 8, 1), date(2026, 8, 5), by_id=99)
    if "by_id=99" not in paged:
        _fail(f"by_id: {paged}")


def _no_sleep(*_a: object, **_k: object) -> None:
    return None


def _tdnet_item(day: date, n: int) -> dict:
    ymd = day.strftime("%Y%m%d")
    xid = f"{ymd}{n:06d}"
    return {
        "Tdnet": {
            "company_code": "72030",
            "title": f"item-{n}",
            "pubdate": f"{day.isoformat()} 10:00:00",
            "url_xbrl": f"https://www.release.tdnet.info/inbs/{xid}.zip",
        }
    }


def test_fetch_splits_per_day() -> None:
    """A 30-day window must hit one same-day list URL per day, not one span cap."""
    start, end = date_window(30, today=date(2026, 8, 5))
    if str(start) != "2026-07-07" or str(end) != "2026-08-05":
        _fail(f"fixture window: {start} {end}")
    n_days = (end - start).days + 1
    if n_days != 30:
        _fail(f"expected 30 days, got {n_days}")
    per_day = 20
    urls: list[str] = []

    def fake_get(url: str) -> bytes:
        urls.append(url)
        m = re.search(r"/(\d{8})-(\d{8})\.json", url)
        if not m:
            _fail(f"not a list URL: {url}")
        if m.group(1) != m.group(2):
            _fail(f"multi-day span list URL: {url}")
        if "by_id=" in url:
            _fail(f"by_id on main path: {url}")
        day = date.fromisoformat(f"{m.group(1)[0:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}")
        payload = {"items": [_tdnet_item(day, i) for i in range(per_day)]}
        return json.dumps(payload).encode("utf-8")

    orig_sleep = xbrl_mod.time.sleep
    xbrl_mod.time.sleep = _no_sleep
    try:
        rows = fetch_xbrl_records(start, end, get=fake_get)
    finally:
        xbrl_mod.time.sleep = orig_sleep

    if len(urls) != n_days:
        _fail(f"expected {n_days} daily list URLs, got {len(urls)}")
    span = f"{start:%Y%m%d}-{end:%Y%m%d}.json"
    if any(span in u for u in urls):
        _fail(f"used multi-day span {span}")
    cur = start
    for i, url in enumerate(urls):
        want = f"{cur:%Y%m%d}-{cur:%Y%m%d}.json"
        if want not in url:
            _fail(f"day {i} expected {want} in {url}")
        if f"limit={LIST_LIMIT}" not in url:
            _fail(f"day {i} missing raised limit: {url}")
        cur += timedelta(days=1)
    expect_n = n_days * per_day
    if len(rows) != expect_n:
        _fail(f"listed {len(rows)} != {expect_n} (must not stop at a 300 cap)")
    if expect_n <= 300:
        _fail("fixture too small to prove 300-cap is gone")


def test_fetch_warns_when_day_hits_list_limit() -> None:
    day = date(2026, 8, 5)

    def fake_get(url: str) -> bytes:
        if f"{day:%Y%m%d}-{day:%Y%m%d}.json" not in url:
            _fail(f"expected single-day URL: {url}")
        payload = {"items": [_tdnet_item(day, i) for i in range(LIST_LIMIT)]}
        return json.dumps(payload).encode("utf-8")

    buf = io.StringIO()
    orig_sleep = xbrl_mod.time.sleep
    xbrl_mod.time.sleep = _no_sleep
    try:
        with redirect_stdout(buf):
            rows = fetch_xbrl_records(day, day, get=fake_get)
    finally:
        xbrl_mod.time.sleep = orig_sleep

    text = buf.getvalue()
    want = (
        f"warn list truncated for {day.isoformat()}: "
        f"got {LIST_LIMIT} items (day may be incomplete)"
    )
    if want not in text:
        _fail(f"missing truncation warn: {text!r}")
    if len(rows) != LIST_LIMIT:
        _fail(f"rows {len(rows)} != LIST_LIMIT {LIST_LIMIT}")

    def fake_get_short(url: str) -> bytes:
        payload = {"items": [_tdnet_item(day, 0)]}
        return json.dumps(payload).encode("utf-8")

    buf2 = io.StringIO()
    orig_sleep = xbrl_mod.time.sleep
    xbrl_mod.time.sleep = _no_sleep
    try:
        with redirect_stdout(buf2):
            short_rows = fetch_xbrl_records(day, day, get=fake_get_short)
    finally:
        xbrl_mod.time.sleep = orig_sleep
    if "warn list truncated" in buf2.getvalue():
        _fail(f"short day should not warn: {buf2.getvalue()!r}")
    if len(short_rows) != 1:
        _fail(f"short day rows: {len(short_rows)}")


def test_corrupt_zip_not_promoted(tmp: Path) -> None:
    dest = tmp / "corrupt"
    final = dest / "9279" / "20260805_9279_t_xid.zip"
    try:
        save_zip_bytes(b"this is not a zip", final)
    except ValueError:
        pass
    else:
        _fail("corrupt zip must raise")
    if final.exists():
        _fail("corrupt must not promote")
    if part_path(final).exists():
        _fail("corrupt must delete .part")
    garbage = tmp / "garbage.zip"
    garbage.write_bytes(b"PK\x03\x04not-really")
    try:
        validate_zip(garbage)
    except ValueError:
        pass
    else:
        _fail("validate_zip must reject garbage")


def test_valid_zip_promoted(tmp: Path) -> None:
    dest = tmp / "valid"
    final = dest / "9279" / "20260805_9279_t_xid.zip"
    data = _tiny_zip()
    n = save_zip_bytes(data, final)
    if n != len(data):
        _fail(f"bytes {n} != {len(data)}")
    if not final.is_file():
        _fail("valid zip not promoted")
    if part_path(final).exists():
        _fail(".part left after valid promote")
    with zipfile.ZipFile(final) as zf:
        if zf.testzip() is not None:
            _fail("promoted zip failed testzip")
        if zf.read("a.txt") != b"hello xbrl":
            _fail("zip payload mismatch")


def test_resume_leftover_part(tmp: Path) -> None:
    dest = tmp / "resume"
    pub = "2026-08-05 19:00:00"
    xid = "081220260805509698"
    final = dest_path(dest, pub, "92790", "決算短信", xid, "ギフト", {"92790": ("010", "ギフト")})
    final.parent.mkdir(parents=True, exist_ok=True)
    leftover = part_path(final)
    leftover.write_bytes(b"partial-download")
    start, end = date(2026, 8, 5), date(2026, 8, 5)
    seen = load_seen_xbrl_ids(dest, start, end)
    if xid in seen:
        _fail("leftover .part must not enter skip set")
    if final.exists():
        _fail("final must be absent before retry")
    data = _tiny_zip(b"retry-ok")
    save_zip_bytes(data, final)
    if not final.is_file():
        _fail("retry did not promote")
    if leftover.exists():
        _fail(".part remains after retry")
    with zipfile.ZipFile(final) as zf:
        if zf.testzip() is not None:
            _fail("retry zip failed testzip")


def test_manifest_skip_by_xbrl_id(tmp: Path) -> None:
    dest = tmp / "manifest-dest"
    dest.mkdir()
    xid = "081220260805509698"
    pub = "2026-08-05 19:00:00"
    row = build_manifest_row(
        xbrl_id=xid,
        code="92790",
        code4="9279",
        pubdate=pub,
        title="決算短信",
        rel_path="9279/missing.zip",
        n_bytes=12,
        sha256="abc",
        fetched_at="2026-08-05T19:01:00+09:00",
    )
    if row["time"] != "19:00:00":
        _fail(f"time field: {row['time']!r}")
    if pubdate_iso_date(pub) != "2026-08-05":
        _fail("pubdate calendar date")
    if pubdate_time("2026-08-05") != "":
        _fail("date-only pubdate time must be empty")
    append_manifest(dest, row)
    man = dest / "manifest" / "2026-08-05.jsonl"
    if not man.is_file():
        _fail("manifest not written")
    seen = load_seen_xbrl_ids(dest, date(2026, 8, 5), date(2026, 8, 5))
    if xid not in seen:
        _fail("load_seen missed xbrl_id")
    zip_path = dest / row["path"]
    if zip_path.exists():
        _fail("skip must work even when zip path is missing")

    other = build_manifest_row(
        xbrl_id="9999outside",
        code="72030",
        code4="7203",
        pubdate="2026-08-01 10:00:00",
        title="外",
        rel_path="7203/out.zip",
        n_bytes=1,
        sha256="d",
        fetched_at="2026-08-01T10:00:00+09:00",
    )
    append_manifest(dest, other)
    window = load_seen_xbrl_ids(dest, date(2026, 8, 5), date(2026, 8, 5))
    if "9999outside" in window:
        _fail("manifest outside lookback must not skip")
    if xid not in window:
        _fail("in-window id dropped")

    line = json.loads(man.read_text(encoding="utf-8").splitlines()[0])
    if line["xbrl_id"] != xid or line["code"] != "92790":
        _fail(f"manifest fields: {line!r}")


def test_default_dest_shares_xbrl_dest() -> None:
    want = "/home/stock/r2-share/xbrl"
    if xbrl_mod.DEFAULT_DEST != want:
        _fail(f"DEFAULT_DEST {xbrl_mod.DEFAULT_DEST!r} != {want}")
    if XBRL_DEST != want:
        _fail(f"XBRL_DEST {XBRL_DEST!r} != {want}")
    if xbrl_mod.DEFAULT_DEST != XBRL_DEST:
        _fail("DEFAULT_DEST must alias XBRL_DEST")


def test_date_window() -> None:
    start, end = date_window(1, today=date(2026, 8, 5))
    if start != end or str(start) != "2026-08-05":
        _fail(f"N=1 window: {start} {end}")
    start, end = date_window(30, today=date(2026, 8, 5))
    if str(start) != "2026-07-07" or str(end) != "2026-08-05":
        _fail(f"N=30 window: {start} {end}")


def test_http_get_uses_job_ua_then_browser() -> None:
    from urllib.error import HTTPError
    from unittest.mock import MagicMock, patch

    from _r2common import BROWSER_UA

    uas: list[str] = []

    def fake_urlopen(req, timeout=30.0, context=None):  # noqa: ARG001
        ua = req.get_header("User-agent") or req.get_header("User-Agent")
        uas.append(ua)
        if ua == xbrl_mod.USER_AGENT:
            raise HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=None)
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b"ok"
        cm.__exit__.return_value = False
        return cm

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        body = xbrl_mod.http_get("https://www.release.tdnet.info/inbs/x.zip")
    if body != b"ok":
        _fail(f"body {body!r}")
    if uas != [xbrl_mod.USER_AGENT, BROWSER_UA]:
        _fail(f"uas {uas}")
    if xbrl_mod.USER_AGENT == BROWSER_UA:
        _fail("job UA must stay distinct from the browser fallback")


def test_non_sqlite_stock_map_warns_unmatched(tmp: Path) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = xbrl_mod.load_stock_product_map(environ={"DATABASE_URL": "postgresql://u@h/db"}, repo_root=tmp)
    if got != {}:
        _fail(f"non-sqlite map {got}")
    if "infra unmatched uses company_name only" not in buf.getvalue():
        _fail(f"xbrl warning text: {buf.getvalue()!r}")


def test_run_routes_manifest_and_seen(tmp: Path) -> None:
    from unittest.mock import patch

    def rec(xid: str, code: str, name: str) -> dict:
        return {"_xbrl_id": xid, "company_code": code, "company_name": name,
                "title": "2026年3月期 第1四半期決算短信", "pubdate": "2026-08-05 15:00:00"}

    toyota = rec("081220260805000001", "72030", "トヨタ")
    unknown = rec("081220260805000002", "99990", "未上場")
    broken = rec("081220260805000003", "26640", "カワチ薬品")
    seen = rec("081220260805000004", "72030", "トヨタ")
    dest = tmp / "run-xbrl"
    append_manifest(dest, build_manifest_row(
        xbrl_id=seen["_xbrl_id"], code="72030", code4="7203", pubdate=seen["pubdate"],
        title=seen["title"], rel_path="7203/old.zip", n_bytes=1, sha256="0" * 64,
        fetched_at="2026-08-05T15:10:00+09:00",
    ))
    gets: list[str] = []
    bad_ids = {broken["_xbrl_id"]}

    def fake_get(url: str, timeout: float = 30.0) -> bytes:
        xid = url.rsplit("/", 1)[1].removesuffix(".zip")
        gets.append(xid)
        return b"not a zip" if xid in bad_ids else _tiny_zip(xid.encode())

    stocks = {"72030": ("011", "トヨタ"), "26640": ("011", "カワチ薬品")}

    def run_once() -> tuple[int, str]:
        buf = io.StringIO()
        with patch.object(xbrl_mod, "fetch_xbrl_records", lambda s, e: [toyota, unknown, broken, seen]), \
                patch.object(xbrl_mod, "http_get", fake_get), \
                patch.object(xbrl_mod, "load_stock_product_map", lambda: stocks), \
                patch.object(xbrl_mod, "date_window", lambda days: (date(2026, 8, 5), date(2026, 8, 5))), \
                patch.object(xbrl_mod.time, "sleep", lambda s: None), \
                redirect_stdout(buf):
            rc = xbrl_mod.run(1, dest)
        return rc, buf.getvalue()

    rc, out = run_once()
    if rc != 1 or "saved=2 skipped=1 failed=1" not in out:
        _fail(f"first run rc={rc}\n{out}")
    if seen["_xbrl_id"] in gets:
        _fail("manifest xbrl_id must skip download")
    want = {
        toyota["_xbrl_id"]: Path("7203") / dest_filename(toyota["pubdate"], "7203", toyota["title"], toyota["_xbrl_id"]),
        unknown["_xbrl_id"]: Path(UNMATCHED_DIR) / "9999" / dest_filename(unknown["pubdate"], "9999", unknown["title"], unknown["_xbrl_id"]),
    }
    manifest = dest / "manifest" / "2026-08-05.jsonl"
    rows = {r["xbrl_id"]: r for r in map(json.loads, manifest.read_text(encoding="utf-8").splitlines())}
    for xid, rel in want.items():
        data = (dest / rel).read_bytes() if (dest / rel).is_file() else b""
        if data != _tiny_zip(xid.encode()):
            _fail(f"missing zip {rel}\n{out}")
        row = rows.get(xid) or {}
        if row.get("path") != rel.as_posix() or row.get("sha256") != hashlib.sha256(data).hexdigest():
            _fail(f"manifest row {row}")
    if broken["_xbrl_id"] in rows:
        _fail("failed zip must not enter the manifest")

    bad_ids.clear()
    gets.clear()
    rc, out = run_once()
    if rc != 0 or gets != [broken["_xbrl_id"]] or "saved=1 skipped=3 failed=0" not in out:
        _fail(f"second run must fetch only the failed id rc={rc} gets={gets}\n{out}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_default_dest_shares_xbrl_dest()
    test_to_code4()
    test_unmatched_and_dest_paths()
    test_tdnet_xbrl_id()
    test_list_url()
    test_fetch_splits_per_day()
    test_fetch_warns_when_day_hits_list_limit()
    test_date_window()
    test_http_get_uses_job_ua_then_browser()
    with TemporaryDirectory() as d:
        tmp = Path(d)
        test_corrupt_zip_not_promoted(tmp)
        test_valid_zip_promoted(tmp)
        test_resume_leftover_part(tmp)
        test_manifest_skip_by_xbrl_id(tmp)
        test_non_sqlite_stock_map_warns_unmatched(tmp)
        test_run_routes_manifest_and_seen(tmp)
    print("ok: tdnet xbrl r2")


if __name__ == "__main__":
    main()

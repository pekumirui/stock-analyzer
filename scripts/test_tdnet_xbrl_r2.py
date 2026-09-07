"""tdnet_xbrl_r2 の純関数。ネットワークには出ない。"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tdnet_xbrl_r2 import (  # noqa: E402
    NOCODE_DIR,
    UNMATCHED_DIR,
    append_manifest,
    build_manifest_row,
    date_window,
    dest_filename,
    dest_path,
    dest_route,
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
    paged = list_url(date(2026, 8, 1), date(2026, 8, 5), by_id=99)
    if "by_id=99" not in paged:
        _fail(f"by_id: {paged}")


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


def test_date_window() -> None:
    start, end = date_window(1, today=date(2026, 8, 5))
    if start != end or str(start) != "2026-08-05":
        _fail(f"N=1 window: {start} {end}")
    start, end = date_window(30, today=date(2026, 8, 5))
    if str(start) != "2026-07-07" or str(end) != "2026-08-05":
        _fail(f"N=30 window: {start} {end}")


def test_sha256_of_tiny_zip() -> None:
    data = _tiny_zip()
    digest = hashlib.sha256(data).hexdigest()
    if len(digest) != 64:
        _fail(f"sha256 len {len(digest)}")
    buf = io.BytesIO(data)
    with zipfile.ZipFile(buf) as zf:
        if zf.testzip() is not None:
            _fail("tiny zip testzip failed")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_to_code4()
    test_unmatched_and_dest_paths()
    test_tdnet_xbrl_id()
    test_list_url()
    test_date_window()
    test_sha256_of_tiny_zip()
    with TemporaryDirectory() as d:
        tmp = Path(d)
        test_corrupt_zip_not_promoted(tmp)
        test_valid_zip_promoted(tmp)
        test_resume_leftover_part(tmp)
        test_manifest_skip_by_xbrl_id(tmp)
    print("ok: tdnet xbrl r2")


if __name__ == "__main__":
    main()

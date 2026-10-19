"""taxonomy_bundle scan / sync / check. ネットワークなし。"""
from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "xbrl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-bundle-')}/stock.db")

import taxonomy_bundle as tb  # noqa: E402
from app.xbrlp.file_loader import FileLoader  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


XSD_URL = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2020-11-01/jppfs_cor.xsd"
LAB_URL = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2020-11-01/label/jppfs_cor_lab.xml"
GLA_URL = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2020-11-01/label/jppfs_cor_gla.xml"
DEF_URL = (
    "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/r/"
    "rol_std_DocumentAndEntityInformationDEI_def.xml"
)
ERRATA_URL = "http://www.xbrl.org/2003/2008-07-02/xbrl-instance-2003-12-31.xsd"
ORPHAN_URL = "http://www.xbrl.org/2003/orphan.xsd"
UNUSED = (GLA_URL, DEF_URL, ERRATA_URL, ORPHAN_URL)

COMPANY_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
  <xsd:import schemaLocation="{XSD_URL}"/>
  <link:linkbaseRef xlink:type="simple" xlink:href="{GLA_URL}"
                    xlink:role="http://www.xbrl.org/2009/role/genericLabelLinkbaseRef"/>
  <link:linkbaseRef xlink:type="simple" xlink:href="{DEF_URL}"
                    xlink:role="http://www.xbrl.org/2003/role/definitionLinkbaseRef"/>
  <!-- archived copy {ERRATA_URL} -->
</xsd:schema>
"""

REMOTE_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
  <!-- {ERRATA_URL} -->
  <link:linkbaseRef xlink:type="simple" xlink:href="{LAB_URL}"
                    xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>
</xsd:schema>
"""

REMOTE_LAB = """<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
               xmlns:xlink="http://www.w3.org/1999/xlink">
  <link:labelLink xlink:role="http://www.xbrl.org/2003/role/link">
    <link:loc xlink:type="locator" xlink:href="jppfs_cor_gla.xml#unused"
              xlink:role="http://www.xbrl.org/2003/role/generic" xlink:label="unused"/>
  </link:labelLink>
</link:linkbase>
"""

IXBRL = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink">
  <link:schemaRef xlink:type="simple" xlink:href="company.xsd"/>
</html>
"""


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRLData/Summary/summary.htm", IXBRL)
        zf.writestr("XBRLData/Summary/company.xsd", COMPANY_XSD)
    return buf.getvalue()


def _put(cache: Path, url: str, content: str) -> None:
    path = FileLoader(cache_dir=cache)._get_cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_filing(tmp: Path) -> Path:
    zips = tmp / "zips"
    zips.mkdir(parents=True)
    (zips / "7203").mkdir()
    (zips / "7203" / "filing.zip").write_bytes(_zip_bytes())
    return zips


def _complete_cache(cache: Path) -> None:
    _put(cache, XSD_URL, REMOTE_XSD)
    _put(cache, LAB_URL, REMOTE_LAB)
    _put(cache, GLA_URL, "<link:linkbase xmlns:link='http://www.xbrl.org/2003/linkbase'/>")
    _put(cache, DEF_URL, "<link:linkbase xmlns:link='http://www.xbrl.org/2003/linkbase'/>")
    _put(cache, ERRATA_URL, "<xsd:schema xmlns:xsd='http://www.w3.org/2001/XMLSchema'/>")
    _put(cache, ORPHAN_URL, "<xsd:schema xmlns:xsd='http://www.w3.org/2001/XMLSchema'/>")


def _run_check(zips: Path, bundle: Path) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = tb.cmd_check(argparse.Namespace(zips=str(zips), bundle=str(bundle)))
    return rc, buf.getvalue()


def test_scan_sync_check(tmp: Path) -> None:
    zips = _write_filing(tmp)
    src = tmp / "cache"
    _complete_cache(src)

    urls, missing = tb.collect_from_zips(zips, cache_dir=src)
    if missing:
        _fail(f"cache should have closure: {missing}")
    if XSD_URL not in urls or LAB_URL not in urls:
        _fail(f"closure {urls}")
    extra = {u for u in urls if u in UNUSED}
    if extra:
        _fail(f"parser-unused URLs must not be counted: {extra}")

    dest = tmp / "bundle"
    copied = 0
    for url in urls:
        if tb.copy_url(url, src, dest):
            copied += 1
    if copied != 2:
        _fail(f"copied {copied}")
    for url in UNUSED:
        if tb.cache_path_for(dest, url).is_file():
            _fail(f"unused {url} should not be copied")

    urls2, missing2 = tb.collect_from_zips(zips, cache_dir=dest)
    if missing2:
        _fail(f"bundle missing {missing2}")
    if urls2 != urls:
        _fail(f"bundle urls {urls2} != {urls}")


def test_check_reports_missing(tmp: Path) -> None:
    zips = tmp / "zips"
    zips.mkdir(parents=True)
    (zips / "a.zip").write_bytes(_zip_bytes())
    empty = tmp / "empty"
    empty.mkdir()
    urls, _ = tb.collect_from_zips(zips, cache_dir=empty)
    missing = [u for u in urls if not tb.cache_path_for(empty, u).is_file()]
    if XSD_URL not in missing:
        _fail(f"expected missing xsd, got {missing} from {urls}")
    extra = [u for u in missing if u in UNUSED]
    if extra:
        _fail(f"unused URLs must not be reported missing: {extra}")


def test_check_ok_without_unused_files(tmp: Path) -> None:
    zips = _write_filing(tmp)
    bundle = tmp / "bundle"
    _put(bundle, XSD_URL, REMOTE_XSD)
    _put(bundle, LAB_URL, REMOTE_LAB)
    rc, out = _run_check(zips, bundle)
    if rc != 0:
        _fail(f"complete bundle (minus unused) should exit 0: {rc} {out}")
    if "MISSING" in out:
        _fail(f"no unused file should be missing: {out}")
    if "missing=0" not in out:
        _fail(f"expected missing=0: {out}")


def test_check_fails_when_needed_xsd_missing(tmp: Path) -> None:
    zips = _write_filing(tmp)
    bundle = tmp / "bundle"
    _put(bundle, LAB_URL, REMOTE_LAB)
    rc, out = _run_check(zips, bundle)
    if rc == 0:
        _fail(f"missing xsd should fail: {out}")
    if XSD_URL not in out:
        _fail(f"missing xsd URL not reported: {out}")
    if GLA_URL in out or DEF_URL in out or ERRATA_URL in out:
        _fail(f"unused URLs leaked into missing: {out}")


def test_check_fails_when_needed_label_missing(tmp: Path) -> None:
    zips = _write_filing(tmp)
    bundle = tmp / "bundle"
    _put(bundle, XSD_URL, REMOTE_XSD)
    rc, out = _run_check(zips, bundle)
    if rc == 0:
        _fail(f"missing label should fail: {out}")
    if LAB_URL not in out:
        _fail(f"missing label URL not reported: {out}")


def test_sync_skips_unused_files(tmp: Path) -> None:
    zips = _write_filing(tmp)
    src = tmp / "cache"
    _complete_cache(src)
    dest = tmp / "bundle"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = tb.cmd_sync(
            argparse.Namespace(
                zips=str(zips),
                from_cache=str(src),
                to=str(dest),
                all_from_cache=False,
                prune=False,
            )
        )
    if rc != 0:
        _fail(f"sync should succeed: {rc} {buf.getvalue()}")
    if not tb.cache_path_for(dest, XSD_URL).is_file():
        _fail("needed xsd not synced")
    if not tb.cache_path_for(dest, LAB_URL).is_file():
        _fail("needed label not synced")
    for url in UNUSED:
        if tb.cache_path_for(dest, url).is_file():
            _fail(f"sync copied unused {url}")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        test_scan_sync_check(root / "scan")
        test_check_reports_missing(root / "missing")
        test_check_ok_without_unused_files(root / "ok")
        test_check_fails_when_needed_xsd_missing(root / "need-xsd")
        test_check_fails_when_needed_label_missing(root / "need-lab")
        test_sync_skips_unused_files(root / "sync")
    print("ok: taxonomy bundle")


if __name__ == "__main__":
    main()

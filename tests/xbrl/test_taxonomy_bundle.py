"""taxonomy_bundle scan / sync / check. ネットワークなし。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
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
ORPHAN_URL = "http://www.xbrl.org/2003/orphan.xsd"

COMPANY_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:import schemaLocation="{XSD_URL}"/>
</xsd:schema>
"""

REMOTE_XSD = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink">
  <link:linkbaseRef xlink:type="simple" xlink:href="{LAB_URL}"
                    xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>
</xsd:schema>
"""

REMOTE_LAB = """<?xml version="1.0" encoding="UTF-8"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>
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


def test_scan_sync_check(tmp: Path) -> None:
    zips = tmp / "zips"
    zips.mkdir()
    (zips / "7203").mkdir()
    (zips / "7203" / "filing.zip").write_bytes(_zip_bytes())
    src = tmp / "cache"
    _put(src, XSD_URL, REMOTE_XSD)
    _put(src, LAB_URL, REMOTE_LAB)
    _put(src, ORPHAN_URL, "<xsd:schema xmlns:xsd='http://www.w3.org/2001/XMLSchema'/>")

    urls, missing = tb.collect_from_zips(zips, cache_dir=src)
    if missing:
        _fail(f"cache should have closure: {missing}")
    if XSD_URL not in urls or LAB_URL not in urls:
        _fail(f"closure {urls}")
    if ORPHAN_URL in urls:
        _fail("orphan URL must not be referenced")

    dest = tmp / "bundle"
    copied = 0
    for url in urls:
        if tb.copy_url(url, src, dest):
            copied += 1
    if copied != 2:
        _fail(f"copied {copied}")
    if tb.cache_path_for(dest, ORPHAN_URL).is_file():
        _fail("orphan should not be copied")

    urls2, missing2 = tb.collect_from_zips(zips, cache_dir=dest)
    if missing2:
        _fail(f"bundle missing {missing2}")
    if urls2 != urls:
        _fail(f"bundle urls {urls2} != {urls}")


def test_check_reports_missing(tmp: Path) -> None:
    zips = tmp / "zips"
    zips.mkdir()
    (zips / "a.zip").write_bytes(_zip_bytes())
    empty = tmp / "empty"
    empty.mkdir()
    urls, _ = tb.collect_from_zips(zips, cache_dir=empty)
    missing = [u for u in urls if not tb.cache_path_for(empty, u).is_file()]
    if XSD_URL not in missing:
        _fail(f"expected missing xsd, got {missing} from {urls}")


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_scan_sync_check(tmp)
        test_check_reports_missing(tmp)
    print("ok: taxonomy bundle")


if __name__ == "__main__":
    main()

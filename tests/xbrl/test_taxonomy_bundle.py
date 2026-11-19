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

import taxonomy_bundle as tb
from app.xbrlp.file_loader import FileLoader


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


# KAB-167: S100SERZ (61910) referenced this edition; KAB-73's 10-year sample started at 2019-02-28.
JPIGP_2018_COR = (
    "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/jpigp_cor_2018-03-31.xsd"
)
JPIGP_2018_RT = (
    "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/jpigp_rt_2018-03-31.xsd"
)
JPIGP_2018_LAB = (
    "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/label/jpigp_2018-03-31_lab.xml"
)
JPIGP_2018_LAB_EN = (
    "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/label/jpigp_2018-03-31_lab-en.xml"
)
JPIGP_2018_NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/jpigp_cor"
JPIGP_2018_URLS = (JPIGP_2018_COR, JPIGP_2018_RT, JPIGP_2018_LAB, JPIGP_2018_LAB_EN)
BUNDLE = ROOT / "data" / "xbrl_taxonomy"


def _jpigp_2018_sample_zip() -> bytes:
    """S100SERZ-equivalent iXBRL: schemaRef → jpigp 2018-03-31 cor / rt / labels."""
    company_xsd = f"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:link="http://www.xbrl.org/2003/linkbase"
            xmlns:xlink="http://www.w3.org/1999/xlink"
            targetNamespace="http://example.jp/S100SERZ">
  <xsd:import namespace="{JPIGP_2018_NS}" schemaLocation="{JPIGP_2018_COR}"/>
  <xsd:import namespace="http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2018-03-31/jpigp_rt"
              schemaLocation="{JPIGP_2018_RT}"/>
  <link:linkbaseRef xlink:type="simple" xlink:href="{JPIGP_2018_LAB}"
                    xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>
  <link:linkbaseRef xlink:type="simple" xlink:href="{JPIGP_2018_LAB_EN}"
                    xlink:role="http://www.xbrl.org/2003/role/labelLinkbaseRef"/>
</xsd:schema>
"""
    ixbrl = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:jpigp_cor="{JPIGP_2018_NS}"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head>
  <ix:header>
    <link:schemaRef xlink:type="simple" xlink:href="company.xsd"/>
    <ix:hidden>
      <ix:nonFraction name="jpigp_cor:RevenueIFRS" contextRef="CurrentYearDuration"
                      unitRef="JPY" scale="6" decimals="-6">300</ix:nonFraction>
    </ix:hidden>
    <ix:resources>
      <xbrli:context id="CurrentYearDuration">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>2017-04-01</xbrli:startDate>
          <xbrli:endDate>2018-03-31</xbrli:endDate>
        </xbrli:period>
      </xbrli:context>
      <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
    </ix:resources>
  </ix:header>
</head>
<body></body>
</html>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/S100SERZ.htm", ixbrl)
        zf.writestr("XBRL/PublicDoc/company.xsd", company_xsd)
    return buf.getvalue()


def test_jpigp_2018_in_bundle_parses_offline(tmp: Path) -> None:
    """KAB-167: jpigp 2018-03-31 is bundled; S100SERZ-equivalent ZIP parses without network."""
    from unittest.mock import patch

    from app.xbrl.parser import extract_zip, parse_package, reset_taxonomy_caches

    for url in JPIGP_2018_URLS:
        if not tb.cache_path_for(BUNDLE, url).is_file():
            _fail(f"bundle missing {url}")

    zips = tmp / "zips"
    zips.mkdir(parents=True)
    (zips / "S100SERZ.zip").write_bytes(_jpigp_2018_sample_zip())
    rc, out = _run_check(zips, BUNDLE)
    if rc != 0 or "missing=0" not in out:
        _fail(f"jpigp 2018 check should be missing=0: {rc} {out}")

    reset_taxonomy_caches()
    try:
        unpacked = extract_zip(zips / "S100SERZ.zip", tmp / "unpacked")
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            pkg = parse_package(unpacked, cache_dir=BUNDLE, text_max=1000)
    finally:
        reset_taxonomy_caches()
    names = {(f.namespace_key, f.local_name) for f in pkg.facts}
    if ("jpigp_cor", "RevenueIFRS") not in names:
        _fail(f"expected RevenueIFRS fact, got {names}")
    lab = pkg.labels.get(("jpigp_cor", "RevenueIFRS")) or {}
    if lab.get("label_ja") != "売上収益":
        _fail(f"expected ja label 売上収益, got {lab}")


def _edinet_urls(family: str, date: str) -> tuple[str, str, str, str, str]:
    """cor xsd, rt xsd, ja label, en label, namespace. Parser closure for one edition."""
    base = f"http://disclosure.edinet-fsa.go.jp/taxonomy/{family}/{date}"
    return (
        f"{base}/{family}_cor_{date}.xsd",
        f"{base}/{family}_rt_{date}.xsd",
        f"{base}/label/{family}_{date}_lab.xml",
        f"{base}/label/{family}_{date}_lab-en.xml",
        f"{base}/{family}_cor",
    )


def _require_bundled(*urls: str) -> None:
    for url in urls:
        if not tb.cache_path_for(BUNDLE, url).is_file():
            _fail(f"bundle missing {url}")


def _company_xsd(
    imports: list[tuple[str, str]],
    labels: list[str],
    unused: list[str],
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"',
        '            xmlns:link="http://www.xbrl.org/2003/linkbase"',
        '            xmlns:xlink="http://www.w3.org/1999/xlink"',
        '            targetNamespace="http://example.jp/filing">',
    ]
    for ns, loc in imports:
        lines.append(f'  <xsd:import namespace="{ns}" schemaLocation="{loc}"/>')
    label_role = "http://www.xbrl.org/2003/role/labelLinkbaseRef"
    for href in labels:
        lines.append(
            f'  <link:linkbaseRef xlink:type="simple" xlink:href="{href}" xlink:role="{label_role}"/>'
        )
    for href in unused:
        role = (
            "http://www.xbrl.org/2003/role/definitionLinkbaseRef"
            if href.endswith("_def.xml")
            else "http://www.xbrl.org/2009/role/genericLabelLinkbaseRef"
        )
        lines.append(
            f'  <link:linkbaseRef xlink:type="simple" xlink:href="{href}" xlink:role="{role}"/>'
        )
    lines.append("</xsd:schema>")
    return "\n".join(lines) + "\n"


def _ixbrl_zip(
    *,
    prefix: str,
    ns: str,
    company_xsd: str,
    hidden: str,
    instant: bool,
) -> bytes:
    period = (
        "<xbrli:instant>2016-11-30</xbrli:instant>"
        if instant
        else "<xbrli:startDate>2016-03-01</xbrli:startDate><xbrli:endDate>2016-11-30</xbrli:endDate>"
    )
    ctx = "AsOf" if instant else "CurrentYearDuration"
    ixbrl = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:{prefix}="{ns}"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head>
  <ix:header>
    <link:schemaRef xlink:type="simple" xlink:href="company.xsd"/>
    <ix:hidden>
      {hidden}
    </ix:hidden>
    <ix:resources>
      <xbrli:context id="{ctx}">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E03044</xbrli:identifier></xbrli:entity>
        <xbrli:period>{period}</xbrli:period>
      </xbrli:context>
      <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
    </ix:resources>
  </ix:header>
</head>
<body></body>
</html>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/filing.htm", ixbrl)
        zf.writestr("XBRL/PublicDoc/company.xsd", company_xsd)
    return buf.getvalue()


def _parse_offline(zip_path: Path, unpacked: Path):
    from unittest.mock import patch

    from app.xbrl.parser import extract_zip, parse_package, reset_taxonomy_caches

    reset_taxonomy_caches()
    try:
        root = extract_zip(zip_path, unpacked)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            return parse_package(root, cache_dir=BUNDLE, text_max=1000)
    finally:
        reset_taxonomy_caches()


# KAB-324: S100M0FI (82540, 訂正四半期報告書 2016/09/01–2016/11/30) imports these.
# jpigp has no edition before 2018-03-31. Pre-2016 inline jpcrp/jppfs are only these two dates.
_PRE2016_GAAP = ("2013-08-31", "2015-03-31")
_PRE2016_JPSPS = ("2013-08-31", "2014-07-31", "2015-03-31")


def _gaap_filing(date: str) -> tuple[str, list[str], list[str]]:
    """Company schema shaped like S100M0FI: jpcrp + jppfs + jpdei, plus ignored gla/def."""
    imports: list[tuple[str, str]] = []
    labels: list[str] = []
    for family, edition in (("jpcrp", date), ("jppfs", date), ("jpdei", "2013-08-31")):
        cor, rt, lab, lab_en, ns = _edinet_urls(family, edition)
        imports.append((ns, cor))
        imports.append((ns.replace("_cor", "_rt"), rt))
        labels.extend((lab, lab_en))
    imports.append(("http://www.xbrl.org/2003/instance", "http://www.xbrl.org/2003/xbrl-instance-2003-12-31.xsd"))
    unused = [
        f"http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/{date}/label/jpcrp_{date}_gla.xml",
        "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/r/jpdei_000100-000_2013-08-31_def.xml",
    ]
    xsd = _company_xsd(imports, labels, unused)
    return xsd, labels, unused


def test_s100m0fi_2015_taxonomy_parses_offline(tmp: Path) -> None:
    """KAB-324: S100M0FI's jpcrp/jppfs 2015-03-31 closure is bundled and parses with no network."""
    date = "2015-03-31"
    needed: list[str] = []
    for family in ("jpcrp", "jppfs"):
        cor, rt, lab, lab_en, _ns = _edinet_urls(family, date)
        needed.extend((cor, rt, lab, lab_en))
    _require_bundled(*needed)
    # jppfs cor imports the part-element schema that sits beside it.
    _require_bundled(
        "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2015-03-31/jppfs_pe_2012.xsd"
    )

    xsd, _labels, unused = _gaap_filing(date)
    zips = tmp / "zips"
    zips.mkdir(parents=True)
    hidden = (
        '<ix:nonFraction name="jppfs_cor:NetSales" contextRef="CurrentYearDuration"'
        ' unitRef="JPY" scale="0" decimals="0">100</ix:nonFraction>'
    )
    (zips / "S100M0FI.zip").write_bytes(
        _ixbrl_zip(
            prefix="jppfs_cor",
            ns=_edinet_urls("jppfs", date)[4],
            company_xsd=xsd,
            hidden=hidden,
            instant=False,
        )
    )
    rc, out = _run_check(zips, BUNDLE)
    if rc != 0 or "missing=0" not in out:
        _fail(f"S100M0FI check should be missing=0: {rc} {out}")
    for href in unused:
        if href in out:
            _fail(f"unused linkbase counted as missing: {href}\n{out}")

    pkg = _parse_offline(zips / "S100M0FI.zip", tmp / "unpacked")
    names = {(f.namespace_key, f.local_name) for f in pkg.facts}
    if ("jppfs_cor", "NetSales") not in names:
        _fail(f"expected NetSales fact, got {names}")
    lab = pkg.labels.get(("jppfs_cor", "NetSales")) or {}
    if lab.get("label_ja") != "売上高":
        _fail(f"expected ja label 売上高, got {lab}")


def test_pre2016_gaap_and_jpsps_editions_parse_offline(tmp: Path) -> None:
    """Every inline jpcrp/jppfs/jpsps edition before 2016-02-29 is bundled."""
    # 2013-08-31 is the other GAAP edition (次世代). 2014 did not revise jpcrp/jppfs.
    xsd, _labels, unused = _gaap_filing("2013-08-31")
    zips = tmp / "gaap-2013"
    zips.mkdir(parents=True)
    hidden = (
        '<ix:nonFraction name="jppfs_cor:NetSales" contextRef="CurrentYearDuration"'
        ' unitRef="JPY" scale="0" decimals="0">100</ix:nonFraction>'
    )
    (zips / "nextgen.zip").write_bytes(
        _ixbrl_zip(
            prefix="jppfs_cor",
            ns=_edinet_urls("jppfs", "2013-08-31")[4],
            company_xsd=xsd,
            hidden=hidden,
            instant=False,
        )
    )
    rc, out = _run_check(zips, BUNDLE)
    if rc != 0 or "missing=0" not in out:
        _fail(f"2013-08-31 GAAP check should be missing=0: {rc} {out}")
    for href in unused:
        if href in out:
            _fail(f"unused linkbase counted as missing: {href}\n{out}")
    pkg = _parse_offline(zips / "nextgen.zip", tmp / "gaap-unpacked")
    lab = pkg.labels.get(("jppfs_cor", "NetSales")) or {}
    if lab.get("label_ja") != "売上高":
        _fail(f"2013-08-31 expected ja label 売上高, got {lab}")

    for date in _PRE2016_JPSPS:
        cor, rt, lab_url, lab_en, ns = _edinet_urls("jpsps", date)
        _require_bundled(cor, rt, lab_url, lab_en)
        jxsd = _company_xsd(
            [(ns, cor), (ns.replace("_cor", "_rt"), rt)],
            [lab_url, lab_en],
            [f"http://disclosure.edinet-fsa.go.jp/taxonomy/jpsps/{date}/label/jpsps_{date}_gla.xml"],
        )
        jz = tmp / f"jpsps-{date}"
        jz.mkdir(parents=True)
        jhidden = (
            '<ix:nonNumeric name="jpsps_cor:FundNameTextBlock" contextRef="AsOf">'
            "sample</ix:nonNumeric>"
        )
        (jz / "filing.zip").write_bytes(
            _ixbrl_zip(
                prefix="jpsps_cor",
                ns=ns,
                company_xsd=jxsd,
                hidden=jhidden,
                instant=True,
            )
        )
        rc, out = _run_check(jz, BUNDLE)
        if rc != 0 or "missing=0" not in out:
            _fail(f"jpsps {date} check should be missing=0: {rc} {out}")
        if "_gla.xml" in out:
            _fail(f"jpsps gla counted as missing: {out}")
        jpkg = _parse_offline(jz / "filing.zip", tmp / f"jpsps-unpacked-{date}")
        jlab = jpkg.labels.get(("jpsps_cor", "FundNameTextBlock")) or {}
        if jlab.get("label_ja") != "ファンドの名称":
            _fail(f"jpsps {date} expected ファンドの名称, got {jlab}")


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
        test_jpigp_2018_in_bundle_parses_offline(root / "jpigp-2018")
        test_s100m0fi_2015_taxonomy_parses_offline(root / "s100m0fi")
        test_pre2016_gaap_and_jpsps_editions_parse_offline(root / "pre2016")
    print("ok: taxonomy bundle")


if __name__ == "__main__":
    main()

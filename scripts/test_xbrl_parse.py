"""XBRL parse / normalize tests. Uses synthetic iXBRL fixtures (no network)."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-test-')}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest import upsert  # noqa: E402
from app.models import Stock, XbrlFact, XbrlFiling, XbrlFinancial, XbrlStdItem  # noqa: E402
from app.xbrl.context import classify_title, infer_consolidated  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402
from app.xbrl.parser import find_ixbrl_files, parse_package  # noqa: E402
from app.xbrl.store import parse_zip_into_filing  # noqa: E402
from tdnet_xbrl_r2 import append_manifest, build_manifest_row  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor"
IFRS_NS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2024-11-01/jpigp_cor"


def _ixbrl_html(
    facts: list[tuple[str, str, str, str, str]],
    *,
    ns_prefix: str = "jppfs_cor",
    ns_uri: str = NS,
    current_end: str = "2026-03-31",
    current_start: str = "2025-04-01",
    consol: str = "jppfs_cor:ConsolidatedMember",
    extra_context: str = "",
) -> str:
    fact_xml = []
    for name, ctx, unit, scale, value in facts:
        fact_xml.append(
            f'<ix:nonFraction name="{ns_prefix}:{name}" contextRef="{ctx}" '
            f'unitRef="{unit}" scale="{scale}" decimals="-6">{value}</ix:nonFraction>'
        )
    hidden = "\n      ".join(fact_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:{ns_prefix}="{ns_uri}"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head>
  <ix:header>
    <ix:hidden>
      {hidden}
    </ix:hidden>
    <ix:resources>
      <xbrli:context id="CurrentYearDuration">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>{current_start}</xbrli:startDate>
          <xbrli:endDate>{current_end}</xbrli:endDate>
        </xbrli:period>
        <xbrli:scenario>
          <xbrldi:explicitMember dimension="jppfs_cor:ConsolidatedOrNonConsolidatedAxis">{consol}</xbrldi:explicitMember>
        </xbrli:scenario>
      </xbrli:context>
      <xbrli:context id="CurrentYearInstant">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:instant>{current_end}</xbrli:instant>
        </xbrli:period>
        <xbrli:scenario>
          <xbrldi:explicitMember dimension="jppfs_cor:ConsolidatedOrNonConsolidatedAxis">{consol}</xbrldi:explicitMember>
        </xbrli:scenario>
      </xbrli:context>
      <xbrli:context id="PriorYearDuration">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>2024-04-01</xbrli:startDate>
          <xbrli:endDate>2025-03-31</xbrli:endDate>
        </xbrli:period>
      </xbrli:context>
      {extra_context}
      <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
    </ix:resources>
  </ix:header>
</head>
<body></body>
</html>
"""


def _manifest_xml() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><manifest><ixbrl>summary.htm</ixbrl></manifest>\n'


def build_zip(facts: list[tuple[str, str, str, str, str]], **kwargs) -> bytes:
    html = _ixbrl_html(facts, **kwargs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRLData/Summary/summary.htm", html)
        zf.writestr("XBRLData/Summary/manifest.xml", _manifest_xml())
    return buf.getvalue()


MANUFACTURING = [
    ("NetSales", "CurrentYearDuration", "JPY", "6", "200"),
    ("OperatingIncome", "CurrentYearDuration", "JPY", "6", "50"),
    ("OrdinaryIncome", "CurrentYearDuration", "JPY", "6", "48"),
    ("ProfitLossAttributableToOwnersOfParent", "CurrentYearDuration", "JPY", "6", "30"),
    ("Assets", "CurrentYearInstant", "JPY", "6", "1000"),
    ("Liabilities", "CurrentYearInstant", "JPY", "6", "600"),
    ("NetAssets", "CurrentYearInstant", "JPY", "6", "400"),
    ("NetCashProvidedByUsedInOperatingActivities", "CurrentYearDuration", "JPY", "6", "80"),
    ("NetSales", "PriorYearDuration", "JPY", "6", "180"),
]

BANK = [
    ("OperatingRevenue1", "CurrentYearDuration", "JPY", "6", "90"),
    ("OperatingIncome", "CurrentYearDuration", "JPY", "6", "20"),
    ("Assets", "CurrentYearInstant", "JPY", "6", "5000"),
    ("Liabilities", "CurrentYearInstant", "JPY", "6", "4500"),
    ("NetAssets", "CurrentYearInstant", "JPY", "6", "500"),
]

IFRS_FACTS = [
    ("RevenueIFRS", "CurrentYearDuration", "JPY", "6", "300"),
    ("OperatingProfitLossIFRS", "CurrentYearDuration", "JPY", "6", "40"),
    ("ProfitLossAttributableToOwnersOfParentIFRS", "CurrentYearDuration", "JPY", "6", "22"),
    ("AssetsIFRS", "CurrentYearInstant", "JPY", "6", "800"),
]

STANDALONE = [
    ("NetSales", "CurrentYearDuration", "JPY", "6", "10"),
    ("OperatingIncome", "CurrentYearDuration", "JPY", "6", "2"),
    ("Assets", "CurrentYearInstant", "JPY", "6", "40"),
    ("Liabilities", "CurrentYearInstant", "JPY", "6", "15"),
    ("NetAssets", "CurrentYearInstant", "JPY", "6", "25"),
]

CUSTOM = MANUFACTURING


def _custom_ixbrl() -> bytes:
    extra_fact = (
        '<ix:nonFraction name="acme:WeirdWidgetRevenue" contextRef="CurrentYearDuration" '
        'unitRef="JPY" scale="6" decimals="-6">1</ix:nonFraction>'
    )
    html = _ixbrl_html(MANUFACTURING).replace(
        "</ix:hidden>",
        extra_fact + "\n    </ix:hidden>",
    ).replace(
        'xmlns:iso4217="http://www.xbrl.org/2003/iso4217">',
        'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:acme="http://example.com/taxonomy/acme">',
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRLData/Summary/summary.htm", html)
        zf.writestr("XBRLData/Summary/manifest.xml", _manifest_xml())
    return buf.getvalue()


def write_zip(dest: Path, name: str, data: bytes) -> Path:
    path = dest / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def seed_stock(db, code: str, name: str) -> None:
    upsert(db, Stock, [{
        "code": code, "name": name, "sector17": "x", "sector33": "x",
        "market": "プライム", "scale_category": "", "margin_type": "",
        "product_category": "011",
    }])


def test_classify_title() -> None:
    a = classify_title("2026年3月期 決算短信〔日本基準〕（連結）")
    if a["doc_type"] != "annual" or a["period_type"] != "FY" or a["accounting_standard"] != "JPGAAP":
        _fail(f"annual title: {a}")
    b = classify_title("2027年3月期 第1四半期決算短信〔IFRS〕（連結）")
    if b["doc_type"] != "quarterly" or b["period_type"] != "Q1" or b["accounting_standard"] != "IFRS":
        _fail(f"q1 ifrs: {b}")
    c = classify_title("訂正四半期決算短信")
    if not c["is_amendment"] or c["doc_type"] != "quarterly":
        _fail(f"amendment: {c}")


def test_infer_consolidated() -> None:
    if infer_consolidated({"a": "jppfs_cor:NonConsolidatedMember"}) != 0:
        _fail("nonconsolidated")
    if infer_consolidated({}) != 1:
        _fail("empty dims should default consolidated")


def test_find_ixbrl_and_parse_package(tmp: Path) -> None:
    raw = build_zip(MANUFACTURING)
    zpath = write_zip(tmp, "mfg.zip", raw)
    from app.xbrl.parser import extract_zip
    unpacked = extract_zip(zpath, tmp / "unpacked")
    files = find_ixbrl_files(unpacked)
    if not files:
        _fail("no ixbrl files found")
    pkg = parse_package(unpacked, cache_dir=tmp / "cache", text_max=1000)
    if not pkg.facts:
        _fail("no facts")
    names = {f.local_name for f in pkg.facts}
    if "NetSales" not in names or "Assets" not in names:
        _fail(f"missing core facts: {names}")
    currents = [c for c in pkg.contexts if c.relative_period == "current"]
    if not currents:
        _fail(f"no current contexts: {[(c.context_ref, c.end_date, c.relative_period) for c in pkg.contexts]}")


def _std_value(db, filing_id: int, code: str) -> Decimal | None:
    item = db.query(XbrlStdItem).filter_by(code=code).one_or_none()
    if item is None:
        return None
    row = (
        db.query(XbrlFinancial)
        .filter_by(filing_id=filing_id, std_item_id=item.id)
        .one_or_none()
    )
    if row is None:
        return None
    return Decimal(str(row.value))


def test_parse_normalize_idempotent(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        apply_mappings(db)
        data = build_zip(MANUFACTURING)
        zpath = write_zip(tmp, "7203/mfg.zip", data)
        filing = parse_zip_into_filing(
            db,
            zip_path=zpath,
            code="72030",
            doc_id="mfg1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="7203/mfg.zip",
            raw_sha256="abc",
            submitted_at="2026-04-25 15:00:00",
            cache_dir=tmp / "cache",
        )
        if filing.parse_status != "parsed":
            _fail(f"parse status {filing.parse_status} {filing.parse_error}")
        n1 = db.query(XbrlFact).filter_by(filing_id=filing.id).count()
        if n1 == 0:
            _fail("no facts stored")
        parse_zip_into_filing(
            db,
            zip_path=zpath,
            code="72030",
            doc_id="mfg1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="7203/mfg.zip",
            raw_sha256="abc",
            submitted_at="2026-04-25 15:00:00",
            cache_dir=tmp / "cache",
        )
        n2 = db.query(XbrlFact).filter_by(filing_id=filing.id).count()
        if n1 != n2:
            _fail(f"idempotent facts {n1} != {n2}")
        run_normalize(db)
        sales = _std_value(db, filing.id, "net_sales")
        assets = _std_value(db, filing.id, "total_assets")
        liab = _std_value(db, filing.id, "total_liabilities")
        equity = _std_value(db, filing.id, "net_assets")
        if sales != Decimal("200000000"):
            _fail(f"net_sales {sales}")
        if assets is None or liab is None or equity is None:
            _fail(f"bs missing {assets} {liab} {equity}")
        if abs((assets - (liab + equity))) > Decimal("1"):
            _fail(f"bs identity {assets} != {liab}+{equity}")
        ocf = _std_value(db, filing.id, "operating_cf")
        if ocf != Decimal("80000000"):
            _fail(f"operating_cf {ocf}")
    finally:
        db.close()


def test_bank_ifrs_standalone_custom(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "83160", "三井住友FG")
        seed_stock(db, "99840", "ソフトバンクG")
        seed_stock(db, "22220", "単体工業")
        seed_stock(db, "99990", "カスタム工業")
        apply_mappings(db)

        bank_z = write_zip(tmp, "bank.zip", build_zip(BANK))
        bank = parse_zip_into_filing(
            db, zip_path=bank_z, code="83160", doc_id="bank1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="bank.zip", raw_sha256="b", submitted_at="2026-05-01 15:00:00",
            cache_dir=tmp / "cache",
        )
        ifrs_z = write_zip(
            tmp, "ifrs.zip",
            build_zip(IFRS_FACTS, ns_prefix="jpigp_cor", ns_uri=IFRS_NS),
        )
        ifrs = parse_zip_into_filing(
            db, zip_path=ifrs_z, code="99840", doc_id="ifrs1",
            title="2026年3月期 決算短信〔IFRS〕（連結）",
            raw_path="ifrs.zip", raw_sha256="i", submitted_at="2026-05-02 15:00:00",
            cache_dir=tmp / "cache",
        )
        solo_z = write_zip(
            tmp, "solo.zip",
            build_zip(STANDALONE, consol="jppfs_cor:NonConsolidatedMember"),
        )
        solo = parse_zip_into_filing(
            db, zip_path=solo_z, code="22220", doc_id="solo1",
            title="2026年3月期 決算短信〔日本基準〕（単体）",
            raw_path="solo.zip", raw_sha256="s", submitted_at="2026-05-03 15:00:00",
            cache_dir=tmp / "cache",
        )
        custom_z = write_zip(tmp, "custom.zip", _custom_ixbrl())
        custom = parse_zip_into_filing(
            db, zip_path=custom_z, code="99990", doc_id="custom1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="custom.zip", raw_sha256="c", submitted_at="2026-05-04 15:00:00",
            cache_dir=tmp / "cache",
        )
        run_normalize(db)
        if _std_value(db, bank.id, "net_sales") != Decimal("90000000"):
            _fail(f"bank sales {_std_value(db, bank.id, 'net_sales')}")
        if _std_value(db, ifrs.id, "net_sales") != Decimal("300000000"):
            _fail(f"ifrs sales {_std_value(db, ifrs.id, 'net_sales')}")
        if _std_value(db, ifrs.id, "ordinary_income") is not None:
            _fail("ifrs ordinary should stay unmapped")
        fin = db.query(XbrlFinancial).filter_by(filing_id=solo.id).first()
        if fin is None or fin.is_consolidated:
            _fail(f"standalone should be non-consolidated: {fin}")
        from app.models import XbrlElement
        weird = db.query(XbrlElement).filter_by(local_name="WeirdWidgetRevenue").one_or_none()
        if weird is None or not weird.is_custom:
            _fail(f"custom element missing: {weird}")
        if db.query(XbrlFact).filter_by(filing_id=custom.id).count() == 0:
            _fail("custom filing stored no facts")
    finally:
        db.close()


def test_no_ixbrl_skipped(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("XBRLData/instance.xbrl", "<xbrl></xbrl>")
        zpath = write_zip(tmp, "plain.zip", buf.getvalue())
        filing = parse_zip_into_filing(
            db, zip_path=zpath, code="72030", doc_id="plain1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="plain.zip", raw_sha256="p", submitted_at="2026-05-05 15:00:00",
            cache_dir=tmp / "cache",
        )
        if filing.parse_status != "skipped" or filing.parse_error != "no-ixbrl":
            _fail(f"expected skipped no-ixbrl, got {filing.parse_status} {filing.parse_error}")
    finally:
        db.close()


def test_manifest_job(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        apply_mappings(db)
        dest = tmp / "dest"
        data = build_zip(MANUFACTURING)
        rel = "7203/20260425_7203_短信_mfgjob.zip"
        write_zip(dest, rel, data)
        row = build_manifest_row(
            xbrl_id="mfgjob",
            code="72030",
            code4="7203",
            pubdate="2026-04-25 15:00:00",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            rel_path=rel,
            n_bytes=len(data),
            sha256="jobsha",
        )
        append_manifest(dest, row)
        unmatched = build_manifest_row(
            xbrl_id="unm1",
            code="00000",
            code4="0000",
            pubdate="2026-04-25 15:00:00",
            title="unknown",
            rel_path="_unmatched/0000/x.zip",
            n_bytes=1,
            sha256="u",
        )
        append_manifest(dest, unmatched)
        from app.xbrl.jobs import run_parse
        stats = run_parse(db, dest=dest, cache_dir=tmp / "cache")
        if stats["processed"] != 1:
            _fail(f"job stats {stats}")
        if stats["skipped"] < 1:
            _fail(f"unmatched should skip: {stats}")
        filing = db.query(XbrlFiling).filter_by(doc_id="mfgjob").one()
        if filing.parse_status != "parsed":
            _fail(filing.parse_error)
        stats2 = run_parse(db, dest=dest, cache_dir=tmp / "cache")
        if stats2["processed"] != 0:
            _fail(f"second run should not reparse: {stats2}")
        if stats2["skipped"] < 1:
            _fail(f"second run should skip: {stats2}")
    finally:
        db.close()


def main() -> None:
    test_classify_title()
    test_infer_consolidated()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_find_ixbrl_and_parse_package(tmp)
        test_parse_normalize_idempotent(tmp)
        test_bank_ifrs_standalone_custom(tmp)
        test_no_ixbrl_skipped(tmp)
        test_manifest_job(tmp)
    print("ok: xbrl parse")


if __name__ == "__main__":
    main()

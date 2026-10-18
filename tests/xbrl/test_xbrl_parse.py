"""XBRL parse / normalize tests. Uses synthetic iXBRL fixtures (no network)."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-test-')}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.db import upsert  # noqa: E402
from app.models import Stock, XbrlFact, XbrlFiling, XbrlFinancial, XbrlStdItem  # noqa: E402
from app.xbrl.context import (  # noqa: E402
    classify_title,
    infer_accounting_standard_from_namespaces,
    fiscal_year_end_from_title,
    infer_consolidated,
)
from datetime import date  # noqa: E402
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.mappings import apply_mappings  # noqa: E402
from app.xbrl.parser import (  # noqa: E402
    CachedTimeoutFileLoader,
    find_ixbrl_files,
    parse_package,
)
from app.xbrl.store import parse_zip_into_filing  # noqa: E402
from jobs.xbrl.tdnet_xbrl_r2 import append_manifest, build_manifest_row  # noqa: E402


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
    filing_instant: str | None = None,
    dei_period_end: str | None = None,
) -> str:
    fact_xml = []
    for name, ctx, unit, scale, value in facts:
        fact_xml.append(
            f'<ix:nonFraction name="{ns_prefix}:{name}" contextRef="{ctx}" '
            f'unitRef="{unit}" scale="{scale}" decimals="-6">{value}</ix:nonFraction>'
        )
    hidden = "\n      ".join(fact_xml)
    dei_bits = []
    filing_ctx = ""
    if filing_instant:
        filing_ctx = f"""
      <xbrli:context id="FilingDateInstant">
        <xbrli:entity><xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:instant>{filing_instant}</xbrli:instant>
        </xbrli:period>
      </xbrli:context>"""
        dei_bits.append(
            f'<ix:nonNumeric name="jpdei_cor:DummyFilingMarker" contextRef="FilingDateInstant">'
            f'{filing_instant}</ix:nonNumeric>'
        )
    if dei_period_end:
        # DEI period-end fact often sits on FilingDateInstant in real TDnet packages.
        dei_ctx = "FilingDateInstant" if filing_instant else "CurrentYearInstant"
        dei_bits.append(
            f'<ix:nonNumeric name="jpdei_cor:CurrentPeriodEndDateDEI" contextRef="{dei_ctx}">'
            f'{dei_period_end}</ix:nonNumeric>'
        )
    dei_hidden = "\n      ".join(dei_bits)
    extra_context = (extra_context or "") + filing_ctx
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:{ns_prefix}="{ns_uri}"
      xmlns:jpdei_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head>
  <ix:header>
    <ix:hidden>
      {hidden}
      {dei_hidden}
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
    # Fullwidth IFRS / JPGAAP (TDnet titles often use ＩＦＲＳ)
    d = classify_title("2026年3月期 決算短信〔ＩＦＲＳ〕（連結）")
    if d["accounting_standard"] != "IFRS" or d["doc_type"] != "annual":
        _fail(f"fullwidth IFRS: {d}")
    e = classify_title("2027年3月期 第１四半期決算短信〔ＩＦＲＳ〕（連結）")
    if e["accounting_standard"] != "IFRS" or e["period_type"] != "Q1":
        _fail(f"fullwidth Q1 IFRS: {e}")
    f = classify_title("2026年3月期 決算短信〔日本基準〕（連結）")
    if f["accounting_standard"] != "JPGAAP":
        _fail(f"JPGAAP still: {f}")
    g = classify_title("2026年3月期 決算短信（連結）")
    if g["accounting_standard"] is not None:
        _fail(f"ambiguous title should stay NULL: {g}")
    h = classify_title("2026年3月期通期業績予想の修正に関するお知らせ")
    if h["doc_type"] != "forecast_revision" or h["period_type"] != "FY":
        _fail(f"forecast revision title: {h}")


def test_infer_accounting_standard_from_namespaces() -> None:
    from types import SimpleNamespace

    ifrs_fact = SimpleNamespace(
        namespace_key="jpigp_cor",
        namespace_uri=IFRS_NS,
        local_name="RevenueIFRS",
        value_text=None,
    )
    got = infer_accounting_standard_from_namespaces([ifrs_fact], title_standard=None)
    if got != "IFRS":
        _fail(f"jpigp namespace → IFRS: {got}")
    got2 = infer_accounting_standard_from_namespaces([ifrs_fact], title_standard="JPGAAP")
    if got2 != "JPGAAP":
        _fail(f"title should win: {got2}")
    jppfs = SimpleNamespace(
        namespace_key="jppfs_cor",
        namespace_uri="http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor",
        local_name="NetSales",
        value_text=None,
    )
    got3 = infer_accounting_standard_from_namespaces([jppfs], title_standard=None)
    if got3 is not None:
        _fail(f"jppfs alone must stay NULL: {got3}")


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
    q = db.query(XbrlFinancial).filter_by(filing_id=filing_id, std_item_id=item.id)
    if hasattr(XbrlFinancial, "value_kind"):
        q = q.filter_by(value_kind="actual", range_side="point")
    rows = q.all()
    if not rows:
        return None
    if len(rows) > 1:
        consol = [r for r in rows if r.is_consolidated]
        rows = consol or rows
    return Decimal(str(rows[0].value))


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
        first = run_normalize(db)
        if first.get("processed", 0) != 1:
            _fail(f"first normalize {first}")
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
        second = run_normalize(db)
        if second.get("processed", 0) != 0:
            _fail(f"second normalize should skip: {second}")
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


def test_fullwidth_ifrs_and_namespace_fallback(tmp: Path) -> None:
    """Fullwidth title and ambiguous+jpigp both set accounting_standard=IFRS."""
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "21700", "全角ＩＦＲＳ銘柄")
        seed_stock(db, "38530", "名前空間フォールバック")
        apply_mappings(db)
        ifrs_zip = write_zip(
            tmp, "fw_ifrs.zip",
            build_zip(IFRS_FACTS, ns_prefix="jpigp_cor", ns_uri=IFRS_NS),
        )
        fw = parse_zip_into_filing(
            db, zip_path=ifrs_zip, code="21700", doc_id="fw-ifrs1",
            title="2026年3月期 決算短信〔ＩＦＲＳ〕（連結）",
            raw_path="fw_ifrs.zip", raw_sha256="fw", submitted_at="2026-05-02 15:00:00",
            cache_dir=tmp / "cache",
        )
        if fw.accounting_standard != "IFRS":
            _fail(f"fullwidth title filing standard: {fw.accounting_standard}")
        amb_zip = write_zip(
            tmp, "amb_ifrs.zip",
            build_zip(IFRS_FACTS, ns_prefix="jpigp_cor", ns_uri=IFRS_NS),
        )
        amb = parse_zip_into_filing(
            db, zip_path=amb_zip, code="38530", doc_id="amb-ifrs1",
            title="2026年3月期 決算短信（連結）",
            raw_path="amb_ifrs.zip", raw_sha256="amb", submitted_at="2026-05-02 15:00:00",
            cache_dir=tmp / "cache",
        )
        if amb.accounting_standard != "IFRS":
            _fail(f"namespace fallback standard: {amb.accounting_standard}")
        run_normalize(db)
        if _std_value(db, fw.id, "net_sales") != Decimal("300000000"):
            _fail(f"fullwidth ifrs normalize sales {_std_value(db, fw.id, 'net_sales')}")
        if _std_value(db, amb.id, "net_sales") != Decimal("300000000"):
            _fail(f"ambiguous ifrs normalize sales {_std_value(db, amb.id, 'net_sales')}")
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


def test_fiscal_year_end_not_disclosure_date(tmp: Path) -> None:
    """FilingDateInstant (pubdate) must not become fiscal_year_end or relative=current."""
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        apply_mappings(db)
        # Period end 2026-03-31; disclosure/pubdate 2026-05-15 later than period end
        # (same trap as real TDnet Attachment FilingDateInstant).
        data = build_zip(
            MANUFACTURING,
            current_end="2026-03-31",
            current_start="2025-04-01",
            filing_instant="2026-05-15",
            dei_period_end="2026-03-31",
        )
        zpath = write_zip(tmp, "7203/flash_fy.zip", data)
        filing = parse_zip_into_filing(
            db,
            zip_path=zpath,
            code="72030",
            doc_id="flashfy1",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            raw_path="7203/flash_fy.zip",
            raw_sha256="fy1",
            submitted_at="2026-05-15 15:00:00",
            cache_dir=tmp / "cache",
        )
        if filing.parse_status != "parsed":
            _fail(f"parse status {filing.parse_status} {filing.parse_error}")
        if filing.fiscal_year_end != date(2026, 3, 31):
            _fail(f"fiscal_year_end {filing.fiscal_year_end} want 2026-03-31 (not disclosure)")
        if filing.fiscal_year_end == date(2026, 5, 15):
            _fail("fiscal_year_end must never be submitted_at/pubdate")
        from app.models import XbrlContext
        currents = (
            db.query(XbrlContext)
            .filter_by(filing_id=filing.id, relative_period="current")
            .all()
        )
        if not currents:
            _fail("no current contexts after fy_end fix")
        if any(c.context_ref == "FilingDateInstant" and c.relative_period == "current" for c in currents):
            # FilingDateInstant may only be current if its instant equals period end (it does not here)
            bad = [c for c in currents if c.context_ref == "FilingDateInstant"]
            if bad:
                _fail(f"FilingDateInstant marked current: {bad[0].end_date}")
        pl_current = [c for c in currents if c.context_ref == "CurrentYearDuration"]
        if not pl_current:
            _fail(f"CurrentYearDuration not current: {[(c.context_ref, c.relative_period) for c in currents]}")

        run_normalize(db)
        n_fin = db.query(XbrlFinancial).filter_by(filing_id=filing.id).count()
        if n_fin <= 0:
            _fail("normalize wrote 0 xbrl_financials")
        sales = _std_value(db, filing.id, "net_sales")
        op = _std_value(db, filing.id, "operating_income")
        if sales != Decimal("200000000"):
            _fail(f"net_sales after normalize {sales}")
        if op != Decimal("50000000"):
            _fail(f"operating_income after normalize {op}")
    finally:
        db.close()


def test_fy_end_from_pl_when_dei_missing(tmp: Path) -> None:
    """Without DEI, duration ends used by NetSales beat FilingDateInstant max."""
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        apply_mappings(db)
        data = build_zip(
            MANUFACTURING,
            current_end="2026-06-30",
            current_start="2026-04-01",
            filing_instant="2026-08-10",
        )
        zpath = write_zip(tmp, "7203/flash_pl.zip", data)
        filing = parse_zip_into_filing(
            db,
            zip_path=zpath,
            code="72030",
            doc_id="flashpl1",
            title="2027年3月期 第1四半期決算短信〔日本基準〕（連結）",
            raw_path="7203/flash_pl.zip",
            raw_sha256="pl1",
            submitted_at="2026-08-10 15:00:00",
            cache_dir=tmp / "cache",
        )
        if filing.fiscal_year_end != date(2026, 6, 30):
            _fail(f"PL-inferred fy_end {filing.fiscal_year_end} want 2026-06-30")
        run_normalize(db)
        if db.query(XbrlFinancial).filter_by(filing_id=filing.id).count() <= 0:
            _fail("expected financials from PL-inferred fy_end")
        if _std_value(db, filing.id, "net_sales") is None:
            _fail("net_sales missing")
    finally:
        db.close()


def test_attachment_manifest_still_reads_summary(tmp: Path) -> None:
    """KAB-48: Attachment/manifest.xml があっても Summary の tse-ed-t を読む。"""
    from app.xbrl.parser import extract_zip

    att = _ixbrl_html([("NetSales", "CurrentYearDuration", "JPY", "6", "200")])
    summary = _ixbrl_html(
        [("NetIncomePerShare", "CurrentYearDuration", "JPY", "0", "12.5")],
        ns_prefix="tse-ed-t",
        ns_uri="http://www.xbrl.tdnet.info/jp/tse/ed/t/2014-01-12",
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRLData/Attachment/fs.htm", att)
        zf.writestr(
            "XBRLData/Attachment/manifest.xml",
            '<?xml version="1.0" encoding="UTF-8"?><manifest><ixbrl>fs.htm</ixbrl></manifest>\n',
        )
        zf.writestr("XBRLData/Summary/tse-qcedjpsm-13790-20270331-ixbrl.htm", summary)
    zpath = write_zip(tmp, "both.zip", buf.getvalue())
    unpacked = extract_zip(zpath, tmp / "unpacked")
    pkg = parse_package(unpacked, cache_dir=tmp / "cache", text_max=1000)
    names = {(f.namespace_key, f.local_name) for f in pkg.facts}
    if ("tse-ed-t", "NetIncomePerShare") not in names:
        _fail(f"summary tse-ed-t dropped: {names}")
    if ("jppfs_cor", "NetSales") not in names:
        _fail(f"attachment missing: {names}")


def test_xsd_fetch_uses_timeout(tmp: Path) -> None:
    from unittest.mock import patch

    def hang(url, timeout=None, *args, **kwargs):  # noqa: ANN001, ARG001
        if timeout is None:
            _fail("urlopen called without timeout")
        raise TimeoutError("timed out")

    loader = CachedTimeoutFileLoader(cache_dir=tmp / "xsd-cache", timeout=0.2, ignore_failure=True)
    url = "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12/tse-ed-t-2014-01-12.xsd"
    with patch("urllib.request.urlopen", side_effect=hang):
        got = loader.fetch(url)
    if got is not None:
        _fail(f"timeout should return None via ignore_failure, got {got}")


def test_reparse_reprocesses(tmp: Path) -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_stock(db, "72030", "トヨタ自動車")
        apply_mappings(db)
        dest = tmp / "dest-reparse"
        data = build_zip(MANUFACTURING)
        rel = "7203/20260425_7203_短信_reparse.zip"
        write_zip(dest, rel, data)
        row = build_manifest_row(
            xbrl_id="reparse1",
            code="72030",
            code4="7203",
            pubdate="2026-04-25 15:00:00",
            title="2026年3月期 決算短信〔日本基準〕（連結）",
            rel_path=rel,
            n_bytes=len(data),
            sha256="reparse-sha",
        )
        append_manifest(dest, row)
        from app.xbrl.jobs import run_parse
        first = run_parse(db, dest=dest, cache_dir=tmp / "cache")
        if first["processed"] != 1:
            _fail(f"first parse {first}")
        second = run_parse(db, dest=dest, cache_dir=tmp / "cache")
        if second["processed"] != 0:
            _fail(f"second parse should skip: {second}")
        third = run_parse(db, dest=dest, cache_dir=tmp / "cache", reparse=True)
        if third["processed"] != 1:
            _fail(f"reparse should process: {third}")
    finally:
        db.close()


def test_title_fy_end_annual_only() -> None:
    if fiscal_year_end_from_title("2026年3月期 決算短信〔日本基準〕（連結）") != date(2026, 3, 31):
        _fail("annual title fy_end")
    if fiscal_year_end_from_title("2027年3月期 第1四半期決算短信〔日本基準〕（連結）") is not None:
        _fail("quarterly title must not supply period end")
    if fiscal_year_end_from_title("2026年12月期 第2四半期(中間期)決算短信") is not None:
        _fail("semi-annual title must not supply period end")



def main() -> None:
    test_classify_title()
    test_infer_accounting_standard_from_namespaces()
    test_infer_consolidated()
    test_title_fy_end_annual_only()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_find_ixbrl_and_parse_package(tmp)
        test_parse_normalize_idempotent(tmp)
        test_bank_ifrs_standalone_custom(tmp)
        test_fullwidth_ifrs_and_namespace_fallback(tmp)
        test_no_ixbrl_skipped(tmp)
        test_manifest_job(tmp)
        test_fiscal_year_end_not_disclosure_date(tmp)
        test_fy_end_from_pl_when_dei_missing(tmp)
        test_attachment_manifest_still_reads_summary(tmp)
        test_xsd_fetch_uses_timeout(tmp)
        test_reparse_reprocesses(tmp)
    print("ok: xbrl parse")


if __name__ == "__main__":
    main()

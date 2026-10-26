"""短信 PDF の HTML → xbrl_financials（KAB-173）。空 DB。IR BANK にも本番 stock.db にも出ない。"""
from __future__ import annotations

import gzip
import hashlib
import html
import io
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import yaml

from app.db import SessionLocal
from app.models import (
    Statement,
    Stock,
    TdnetPdfDocument,
    XbrlContext,
    XbrlFiling,
    XbrlFinancial,
    XbrlStdItem,
)
from app.xbrl.mappings import apply_mappings
from app.xbrl.normalize import rebuild_all
from app.xbrl.pdf import (
    PdfDisclosure,
    SyncOutcome,
    label_key,
    read_tdnet_pdf,
    sync_pdf_filing,
    sync_pdf_filings,
)
from app.xbrl.store import parse_zip_into_filing

ROOT = Path(__file__).resolve().parents[2]
MILLION = Decimal(10) ** 6


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _dt(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


def _text(x: float, y: float, text: str, width: float) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float) -> str:
    return _text(x1 - width, y, text, width)


def _svg(parts: list[str], kind: str) -> str:
    rule = (
        '<line x1="0" y1="0" x2="100" y2="0" stroke="black"/>'
        if kind == "line"
        else '<path d="M0 0 H100" stroke="black" fill="none"/>'
    )
    inner = rule + "".join(parts)
    return f'<html><body><svg xmlns="http://www.w3.org/2000/svg">{inner}</svg></body></html>'


def _ifrs_1q(kind: str = "path") -> str:
    parts = [
        _text(40, 40, "（1）経営成績", 140),
        _text(40, 58, "（単位：百万円）", 120),
        _cell(280, 76, "売上収益", 70),
        _cell(420, 76, "営業利益", 70),
        _cell(640, 76, "親会社の所有者に帰属する当期利益", 200),
        _cell(860, 76, "経常利益", 70),
        _text(40, 94, "当第1四半期", 140),
        _cell(280, 94, "11,837,973", 80),
        _cell(420, 94, "1,000", 50),
        _cell(640, 94, "800", 50),
        _cell(860, 94, "50", 40),
        _text(40, 112, "前第1四半期", 140),
        _cell(280, 112, "10,000,000", 80),
        _cell(420, 112, "900", 50),
        _cell(640, 112, "700", 50),
        _cell(860, 112, "40", 40),
        _text(40, 140, "2. 業績予想", 100),
        _text(40, 158, "（単位：百万円）", 120),
        _cell(280, 176, "売上収益", 70),
        _text(40, 194, "通期", 40),
        _cell(280, 194, "40,000", 60),
        _text(40, 220, "（2）財政状態", 140),
        _text(40, 238, "（単位：百万円）", 120),
        _cell(280, 256, "資産合計", 70),
        _cell(420, 256, "自己資本比率", 90),
        _cell(680, 256, "1株当たり親会社所有者帰属持分", 200),
        _text(40, 274, "当第1四半期", 140),
        _cell(280, 274, "50,000", 60),
        _cell(420, 274, "39.1", 40),
        _cell(680, 274, "1,234.50", 70),
        _text(40, 310, "個別損益計算書", 140),
        _text(40, 328, "（単位：百万円）", 120),
        _cell(280, 346, "当第1四半期", 100),
        _text(40, 364, "売上高", 60),
        _cell(280, 364, "1", 20),
    ]
    return _svg(parts, kind)


def _jp_fy(kind: str = "line") -> str:
    parts = [
        _text(40, 40, "連結損益計算書", 160),
        _text(40, 58, "（単位：百万円）", 120),
        _cell(400, 76, "当連結会計年度", 120),
        _cell(580, 76, "前連結会計年度", 120),
        _text(40, 94, "親会社株主に帰属する当期純利益", 220),
        _cell(400, 94, "5,000", 50),
        _cell(580, 94, "4,000", 50),
        _text(40, 112, "当期純利益", 100),
        _cell(400, 112, "5,100", 50),
        _cell(580, 112, "4,100", 50),
        _text(40, 150, "連結キャッシュ・フロー計算書", 220),
        _text(40, 168, "（単位：百万円）", 120),
        _cell(400, 186, "当連結会計年度", 120),
        _cell(580, 186, "前連結会計年度", 120),
        _text(40, 204, "有形固定資産の取得による支出", 220),
        _cell(400, 204, "△1,234", 60),
        _cell(580, 204, "△900", 50),
        _text(40, 232, "2. 業績予想", 100),
        _text(40, 250, "（単位：百万円）", 120),
        _cell(400, 268, "売上高", 60),
        _text(40, 286, "通期", 40),
        _cell(400, 286, "9,000", 50),
        _text(40, 314, "連結貸借対照表", 140),
        _text(40, 332, "（単位：百万円）", 120),
        _cell(400, 350, "前期", 40),
        _cell(400, 368, "2025年3月31日", 100),
        _text(40, 386, "売上高", 60),
        _cell(400, 386, "10", 30),
    ]
    return _svg(parts, kind)


def _revision() -> str:
    parts = [
        _text(40, 40, "連結業績予想の修正", 180),
        _text(40, 58, "（単位：百万円）", 120),
        _cell(340, 76, "売上高", 60),
        _cell(520, 76, "売上高", 60),
        _cell(340, 94, "前回発表予想", 110),
        _cell(520, 94, "今回修正予想", 110),
        _text(40, 112, "通期", 40),
        _cell(340, 112, "1,000", 50),
        _cell(520, 112, "1,000～1,200", 100),
        _text(40, 130, "上期", 40),
        _cell(340, 130, "400", 40),
        _cell(520, 130, "450", 40),
    ]
    return _svg(parts, "path")


def _dividend() -> str:
    parts = [
        _text(40, 40, "1. 配当の状況", 120),
        _text(40, 58, "（単位：円）", 90),
        _text(160, 76, "年間配当金", 460),
        _cell(300, 94, "第2四半期末", 90),
        _cell(430, 94, "期末", 40),
        _cell(560, 94, "合計", 40),
        _text(40, 112, "2026年3月期（予想）", 150),
        _cell(300, 112, "20", 30),
        _cell(430, 112, "40", 30),
        _cell(560, 112, "60", 30),
        _text(40, 130, "2025年3月期", 110),
        _cell(300, 130, "―", 20),
        _cell(430, 130, "―", 20),
        _cell(560, 130, "―", 20),
    ]
    return _svg(parts, "line")


def _statement(**kwargs) -> SimpleNamespace:
    base = {
        "disclosure_no": "20240801512345",
        "code": "72030",
        "disclosed_date": date(2024, 8, 1),
        "disclosed_time": "15:00",
        "type_of_period": "1Q",
        "type_of_document": "1QFinancialStatements_Consolidated_IFRS",
        "period_start": date(2024, 4, 1),
        "period_end": date(2024, 6, 30),
        "fiscal_year_start": date(2024, 4, 1),
        "fiscal_year_end": date(2025, 3, 31),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _stock(db, code: str = "72030") -> None:
    if db.get(Stock, code) is None:
        db.add(Stock(
            code=code, name=code, sector17="x", sector33="x",
            market="プライム", scale_category="", margin_type="", product_category="011",
        ))


def _seed(db, no: str, html: str, statement: SimpleNamespace) -> None:
    _stock(db, statement.code)
    db.merge(Statement(
        disclosure_no=no,
        code=statement.code,
        disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time,
        type_of_period=statement.type_of_period,
        type_of_document=statement.type_of_document,
        period_start=statement.period_start,
        period_end=statement.period_end,
        fiscal_year_start=statement.fiscal_year_start,
        fiscal_year_end=statement.fiscal_year_end,
    ))
    raw = html.encode("utf-8")
    db.merge(TdnetPdfDocument(
        disclosure_no=no,
        code=statement.code,
        disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time,
        title=statement.type_of_document,
        status="ok",
        html_gz=gzip.compress(raw),
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        created_at=_dt(2024, 8, 1, 15, 0),
    ))
    db.commit()


def _rows(db, no: str) -> dict[tuple, tuple]:
    filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one_or_none()
    if filing is None:
        return {}
    found = {}
    query = (
        db.query(XbrlFinancial, XbrlStdItem)
        .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
        .filter(XbrlFinancial.filing_id == filing.id)
    )
    for row, item in query:
        found[(item.code, row.value_kind, row.range_side, row.target_period)] = (
            Decimal(str(row.value)), row.unit, row.source_layer,
        )
    return found


def _fact_set(pkg) -> set:
    return {
        (fact.namespace_key, fact.local_name, fact.context_ref, fact.unit, fact.value_num)
        for fact in pkg.facts
    }


def test_ifrs_1q_lands_like_a_zip() -> None:
    no = "20240801512345"
    db = SessionLocal()
    try:
        _seed(db, no, _ifrs_1q(), _statement(disclosure_no=no))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("first sync")
        rows = _rows(db, no)
        if rows[("net_sales", "actual", "point", "Q1")][0] != Decimal("11837973000000"):
            _fail(f"sales {rows.get(('net_sales', 'actual', 'point', 'Q1'))}")
        if ("ordinary_income", "actual", "point", "Q1") in rows:
            _fail("ifrs ordinary income")
        if rows[("net_sales", "forecast", "point", "FY")][2] != "summary":
            _fail("forecast layer")
        if rows[("equity_ratio", "actual", "point", "Q1")][:2] != (Decimal("0.391"), "ratio"):
            _fail(f"equity {rows.get(('equity_ratio', 'actual', 'point', 'Q1'))}")
        if ("operating_cf", "actual", "point", "Q1") in rows:
            _fail("1q cf should be absent")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        priors = [
            ctx.context_ref
            for ctx in db.query(XbrlContext).filter_by(filing_id=filing.id)
            if "Prior" in ctx.context_ref
        ]
        if priors:
            _fail(f"prior contexts stored {priors}")
        if sync_pdf_filing(db, no) is not SyncOutcome.UNCHANGED:
            _fail("second sync")
        rebuild_all(db)
        if _rows(db, no) != rows:
            _fail("rebuild changed rows")
        db.add(XbrlFiling(
            code="72030",
            source="tdnet",
            doc_id="0812" + no,
            doc_type="quarterly",
            period_type="Q1",
            submitted_at=_dt(2024, 8, 1, 15, 0),
            raw_path="x.zip",
            parse_status="parsed",
            is_amendment=False,
            created_at=_dt(2024, 8, 1, 15, 0),
        ))
        db.commit()
        if sync_pdf_filing(db, no) is not SyncOutcome.RETIRED:
            _fail("zip should retire pdf")
        if _rows(db, no) != {}:
            _fail("pdf financials remained")
        if db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).count() != 0:
            _fail("pdf filing remained")
    finally:
        db.close()


def test_svg1_and_svg2_read_the_same_numbers() -> None:
    disclosure = PdfDisclosure.parse(_statement(
        disclosure_no="20250515512346",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        disclosed_date=date(2025, 5, 15),
        period_start=date(2024, 4, 1),
        period_end=date(2025, 3, 31),
        fiscal_year_start=date(2024, 4, 1),
        fiscal_year_end=date(2025, 3, 31),
    ))
    if not isinstance(disclosure, PdfDisclosure):
        _fail(disclosure)
    left = read_tdnet_pdf(_jp_fy("line"), disclosure)
    right = read_tdnet_pdf(_jp_fy("path"), disclosure)
    if _fact_set(left) != _fact_set(right):
        _fail("svg1 and svg2 diverged")


def test_jp_fy_capex_positive_and_owners_profit() -> None:
    no = "20250515512346"
    db = SessionLocal()
    try:
        statement = _statement(
            disclosure_no=no,
            type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            disclosed_date=date(2025, 5, 15),
            period_start=date(2024, 4, 1),
            period_end=date(2025, 3, 31),
            fiscal_year_start=date(2024, 4, 1),
            fiscal_year_end=date(2025, 3, 31),
        )
        _seed(db, no, _jp_fy(), statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("fy sync")
        rows = _rows(db, no)
        if rows[("capex", "actual", "point", "FY")][0] != Decimal("1234000000"):
            _fail(f"capex {rows.get(('capex', 'actual', 'point', 'FY'))}")
        if rows[("net_income", "actual", "point", "FY")][0] != Decimal("5000000000"):
            _fail(f"net income {rows.get(('net_income', 'actual', 'point', 'FY'))}")
        if rows[("net_sales", "forecast", "point", "FY")][0] != Decimal("9000000000"):
            _fail(f"next forecast {rows.get(('net_sales', 'forecast', 'point', 'FY'))}")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        priors = [ctx.context_ref for ctx in db.query(XbrlContext).filter_by(filing_id=filing.id) if "Prior" in ctx.context_ref]
        if priors:
            _fail(f"prior stored {priors}")
        disclosure = PdfDisclosure.parse(statement)
        assert isinstance(disclosure, PdfDisclosure)
        pkg = read_tdnet_pdf(_jp_fy(), disclosure)
        dated = [fact for fact in pkg.facts if fact.local_name == "売上高" and fact.context_ref.startswith("Current")]
        if not dated:
            _fail("date header did not beat 前期")
    finally:
        db.close()


def test_forecast_revision_notice() -> None:
    no = "20250601512347"
    db = SessionLocal()
    try:
        _seed(db, no, _revision(), _statement(
            disclosure_no=no,
            type_of_period="FY",
            type_of_document="EarnForecastRevision",
            disclosed_date=date(2025, 6, 1),
            period_start=date(2024, 4, 1),
            period_end=date(2025, 3, 31),
            fiscal_year_start=date(2024, 4, 1),
            fiscal_year_end=date(2025, 3, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("revision sync")
        rows = _rows(db, no)
        expect = {
            ("net_sales", "forecast_previous", "point", "FY"): Decimal("1000000000"),
            ("net_sales", "forecast", "lower", "FY"): Decimal("1000000000"),
            ("net_sales", "forecast", "upper", "FY"): Decimal("1200000000"),
            ("net_sales", "forecast_previous", "point", "H1"): Decimal("400000000"),
            ("net_sales", "forecast", "point", "H1"): Decimal("450000000"),
        }
        for key, value in expect.items():
            if key not in rows or rows[key][0] != value:
                _fail(f"{key} got {rows.get(key)}")
    finally:
        db.close()


def test_dividend_table_slots() -> None:
    no = "20250701512348"
    db = SessionLocal()
    try:
        _seed(db, no, _dividend(), _statement(
            disclosure_no=no,
            type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            disclosed_date=date(2025, 5, 15),
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            fiscal_year_start=date(2025, 4, 1),
            fiscal_year_end=date(2026, 3, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("dividend sync")
        rows = _rows(db, no)
        for period, value in (("Q2", "20"), ("year_end", "40"), ("annual", "60")):
            key = ("dps", "forecast", "point", period)
            if key not in rows or rows[key][0] != Decimal(value):
                _fail(f"{key} got {rows.get(key)}")
        if any(key[0] == "dps" and key[1] == "actual" for key in rows):
            _fail("dashed dividend row became a fact")
    finally:
        db.close()


def test_reader_version_bump_reparses(monkeypatch) -> None:
    no = "20250801512349"
    db = SessionLocal()
    try:
        _seed(db, no, _dividend(), _statement(
            disclosure_no=no,
            type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            disclosed_date=date(2025, 5, 16),
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            fiscal_year_start=date(2025, 4, 1),
            fiscal_year_end=date(2026, 3, 31),
        ))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("version first")
        if sync_pdf_filing(db, no) is not SyncOutcome.UNCHANGED:
            _fail("version unchanged")
        monkeypatch.setattr("app.xbrl.pdf.READER_VERSION", 99)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("version bump")
    finally:
        db.close()


def test_pdf_label_names_are_label_keys() -> None:
    rows = yaml.safe_load((ROOT / "config/xbrl/mappings/tdnet_pdf.yaml").read_text(encoding="utf-8"))
    for spec in rows:
        for element in spec.get("elements") or []:
            if not str(element["ns"]).startswith("pdf-"):
                continue
            name = str(element["name"])
            if label_key(name) != name:
                _fail(f"{name!r} != label_key {label_key(name)!r}")


def test_pdf_internals_not_imported_outside_package() -> None:
    needle = re.compile(r"^\s*(?:from|import)\s+app\.xbrl\.pdf\._", re.M)
    for path in ROOT.rglob("*.py"):
        posix = path.as_posix()
        if "/app/xbrl/pdf/" in posix or "/.venv/" in posix or "/__pycache__/" in posix:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if needle.search(text):
            _fail(f"private import in {path.relative_to(ROOT)}")


def test_zip_namespace_covers_every_pdf_mapping() -> None:
    mapping_dir = ROOT / "config/xbrl/mappings"
    pdf_rows = yaml.safe_load((mapping_dir / "tdnet_pdf.yaml").read_text(encoding="utf-8"))
    others = []
    for path in sorted(mapping_dir.glob("*.yaml")):
        if path.name == "tdnet_pdf.yaml":
            continue
        others.extend(yaml.safe_load(path.read_text(encoding="utf-8")) or [])
    zip_ns = {"tse-ed-t", "jppfs_cor", "jpigp_cor"}

    def covered(std_item: str, pdf_standard) -> bool:
        for spec in others:
            if spec.get("std_item") != std_item:
                continue
            for element in spec.get("elements") or []:
                if element.get("ns") not in zip_ns:
                    continue
                zip_standard = element.get("standard")
                if pdf_standard is None and zip_standard in (None, "JPGAAP"):
                    return True
                if pdf_standard is not None and zip_standard in (None, pdf_standard):
                    return True
        return False

    for spec in pdf_rows:
        for element in spec.get("elements") or []:
            if not str(element["ns"]).startswith("pdf-"):
                continue
            standard = element.get("standard")
            if not covered(spec["std_item"], standard):
                _fail(f"no zip mapping for {spec['std_item']} standard={standard} name={element['name']}")


def test_limit_reports_remaining() -> None:
    db = SessionLocal()
    try:
        code = "99990"
        _stock(db, code)
        for index, day in enumerate((date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 1))):
            no = f"2024010{index}512350"
            db.merge(Statement(
                disclosure_no=no, code=code, disclosed_date=day, disclosed_time="15:00",
                type_of_period="FY", type_of_document="REITFinancialStatements_Consolidated_JP",
                period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
                fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
            ))
            raw = b"<html></html>"
            db.merge(TdnetPdfDocument(
                disclosure_no=no, code=code, disclosed_date=day, disclosed_time="15:00",
                title="reit", status="ok", html_gz=gzip.compress(raw), bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), created_at=_dt(2024, 1, 3),
            ))
        db.commit()
        counts = sync_pdf_filings(db, codes=[code], limit=1)
        if counts["remaining"] != 2:
            _fail(f"remaining {counts['remaining']}")
        if counts[SyncOutcome.ABSENT] != 1:
            _fail(f"absent {counts[SyncOutcome.ABSENT]}")
    finally:
        db.close()


def test_failed_html_stays_failed_until_reparse() -> None:
    no = "20250901512351"
    db = SessionLocal()
    try:
        html_text = (
            '<html><body><svg><text x="10" y="10" font-size="10" textLength="40">'
            "見出しだけ</text></svg></body></html>"
        )
        _seed(db, no, html_text, _statement(disclosure_no=no))
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.FAILED:
            _fail("expected failed")
        if sync_pdf_filing(db, no) is not SyncOutcome.UNCHANGED:
            _fail("failed fingerprint should stick")
    finally:
        db.close()


def _ixbrl_bps() -> str:
    ns = "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:tse-ed-t="{ns}"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
<head><ix:header><ix:hidden>
<ix:nonFraction name="tse-ed-t:NetAssetsPerShare" contextRef="Bps" unitRef="JPYPershare" scale="0" decimals="2">4512.33</ix:nonFraction>
</ix:hidden><ix:resources>
<xbrli:context id="Bps">
  <xbrli:entity><xbrli:identifier scheme="http://www.xbrl.tdnet.info">E00000</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period>
  <xbrli:scenario><xbrldi:explicitMember dimension="tse-ed-t:ResultForecastAxis">tse-ed-t:ResultMember</xbrldi:explicitMember></xbrli:scenario>
</xbrli:context>
<xbrli:unit id="JPYPershare"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></head><body></body></html>"""


def test_zip_net_assets_per_share_becomes_bps(tmp_path: Path) -> None:
    db = SessionLocal()
    try:
        _stock(db, "72030")
        db.commit()
        apply_mappings(db)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("XBRLData/Summary/summary.htm", _ixbrl_bps())
            zf.writestr("XBRLData/Summary/manifest.xml", "<manifest><ixbrl>summary.htm</ixbrl></manifest>")
        path = tmp_path / "bps.zip"
        path.write_bytes(buf.getvalue())
        filing = parse_zip_into_filing(
            db,
            zip_path=path,
            code="72030",
            doc_id="081220250801512399",
            title="2026年3月期 第1四半期決算短信〔日本基準〕（連結）",
            raw_path="bps.zip",
            raw_sha256="bps",
            submitted_at="2025-08-01 15:00:00",
            cache_dir=tmp_path / "cache",
        )
        if filing.parse_status != "parsed":
            _fail(f"zip parse {filing.parse_status} {filing.parse_error}")
        from app.xbrl.normalize import normalize_filing
        normalize_filing(db, filing)
        db.commit()
        item = db.query(XbrlStdItem).filter_by(code="bps").one()
        row = db.query(XbrlFinancial).filter_by(filing_id=filing.id, std_item_id=item.id).one_or_none()
        if row is None or Decimal(str(row.value)) != Decimal("4512.33"):
            _fail(f"bps row {row.value if row else None}")
    finally:
        db.close()


def test_pdf_retire_error_does_not_fail_the_zip(tmp_path: Path, monkeypatch) -> None:
    db = SessionLocal()
    try:
        _stock(db, "72030")
        db.commit()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("XBRLData/Summary/summary.htm", _ixbrl_bps())
            zf.writestr("XBRLData/Summary/manifest.xml", "<manifest><ixbrl>summary.htm</ixbrl></manifest>")
        path = tmp_path / "7203" / "bps.zip"
        path.parent.mkdir()
        path.write_bytes(buf.getvalue())

        def _boom(*_args, **_kwargs):
            raise RuntimeError("pdf down")

        monkeypatch.setattr("app.xbrl.pdf.sync_pdf_filing", _boom)
        from app.xbrl.jobs import parse_manifest_row
        status = parse_manifest_row(
            db,
            tmp_path,
            {
                "xbrl_id": "081220250801512398",
                "path": "7203/bps.zip",
                "code": "7203",
                "sha256": "abc",
                "pubdate": "2025-08-01 15:00:00",
                "title": "2026年3月期 第1四半期決算短信〔日本基準〕（連結）",
            },
            cache_dir=tmp_path / "cache",
        )
        if status != "parsed":
            _fail(status)
        filing = db.query(XbrlFiling).filter_by(source="tdnet", doc_id="081220250801512398").one()
        if filing.parse_status != "parsed":
            _fail(filing.parse_status)
    finally:
        db.close()

"""KAB-212〜220: 本番 5 銘柄で出た症状を <text> の座標で再現する。IR BANK も本番 DB も使わない。"""
from __future__ import annotations

import gzip
import hashlib
import html
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import (
    PdfDisclosure,
    SyncOutcome,
    format_garbled_pages,
    garbled_page_indexes,
    label_key,
    read_pages,
    read_tdnet_pdf,
    sync_pdf_filing,
)

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


def _cell(x1: float, y: float, text: str, width: float = 50) -> str:
    return _text(x1 - width, y, text, width)


def _pages(*pages: list[str]) -> str:
    svgs = "".join(
        f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>' for parts in pages
    )
    return f"<html><body>{svgs}</body></html>"


def _disclosure(**kwargs) -> PdfDisclosure:
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
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _values(pkg) -> set[Decimal]:
    return {fact.value_num for fact in pkg.facts}


def _stock(db, code: str) -> None:
    if db.get(Stock, code) is None:
        db.add(Stock(
            code=code, name=code, sector17="x", sector33="x",
            market="プライム", scale_category="", margin_type="", product_category="011",
        ))


def _seed(db, no: str, html_text: str, statement: SimpleNamespace, *, title: str | None = None) -> None:
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
    raw = html_text.encode("utf-8")
    db.merge(TdnetPdfDocument(
        disclosure_no=no,
        code=statement.code,
        disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time,
        title=title if title is not None else statement.type_of_document,
        status="ok",
        html_gz=gzip.compress(raw),
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        created_at=_dt(2024, 8, 1, 15, 0),
    ))
    db.commit()


def _rows(db, no: str) -> dict[tuple, Decimal]:
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
        found[(item.code, row.value_kind, row.range_side, row.target_period)] = Decimal(str(row.value))
    return found


def test_kab212_correction_keeps_after_table_not_before() -> None:
    """9983 20250522561281: 訂正前の予想 EPS 1,366.51 ではなく訂正後の 1,336.51。電話番号の 3 は入らない。"""
    parts = [
        _text(40, 40, "（訂正前）", 60),
        _text(40, 58, "3. 連結業績予想", 100),
        _text(40, 76, "（単位：円）", 80),
        _cell(400, 94, "基本的1株当たり当期利益", 160),
        _text(40, 112, "通期", 40),
        _cell(400, 112, "1,366.51", 50),
        _text(40, 140, "（訂正後）", 60),
        _text(40, 158, "3. 連結業績予想", 100),
        _text(40, 176, "（単位：円）", 80),
        _cell(400, 194, "基本的1株当たり当期利益", 160),
        _text(40, 212, "通期", 40),
        _cell(400, 212, "1,336.51", 50),
        _text(40, 240, "マスコミの方のお問合せ先：℡：03-6776-7449", 280),
    ]
    d = _disclosure(
        disclosure_no="20250522561281", code="99830",
        type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_IFRS",
        disclosed_date=date(2025, 5, 22),
        period_start=date(2024, 9, 1), period_end=date(2025, 2, 28),
        fiscal_year_start=date(2024, 9, 1), fiscal_year_end=date(2025, 8, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    eps = [
        value for (name, ref), value in facts.items()
        if "1株当たり" in name and "Forecast" in ref
    ]
    if Decimal("1336.51") not in eps:
        _fail(f"after eps missing {facts}")
    if Decimal("1366.51") in _values(pkg):
        _fail("before eps kept")
    if MILLION * 3 in _values(pkg) or Decimal(3) in _values(pkg):
        _fail("phone 3 leaked")


def test_kab212_cf_only_correction_and_is_amendment() -> None:
    """9983 20240703543410: 訂正後の CF。訂正前の値は入らない。is_amendment。"""
    parts = [
        _text(40, 40, "（訂正前）", 60),
        _text(40, 58, "連結キャッシュ・フロー計算書", 180),
        _text(40, 76, "（単位：百万円）", 100),
        _cell(400, 94, "当連結会計年度", 100),
        _text(40, 112, "定期預金の預入による支出", 180),
        _cell(400, 112, "△530,484", 60),
        _text(40, 140, "（訂正後）", 60),
        _text(40, 158, "連結キャッシュ・フロー計算書", 180),
        _text(40, 176, "（単位：百万円）", 100),
        _cell(400, 194, "当連結会計年度", 100),
        _text(40, 212, "定期預金の預入による支出", 180),
        _cell(400, 212, "△212,182", 60),
        _text(40, 230, "定期預金の払出による収入", 180),
        _cell(400, 230, "207,052", 50),
    ]
    no = "20240703543410"
    statement = SimpleNamespace(
        disclosure_no=no, code="99830", disclosed_date=date(2024, 7, 3),
        disclosed_time="15:00", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_IFRS",
        period_start=date(2023, 9, 1), period_end=date(2024, 8, 31),
        fiscal_year_start=date(2023, 9, 1), fiscal_year_end=date(2024, 8, 31),
    )
    db = SessionLocal()
    try:
        _seed(db, no, _pages(parts), statement, title="2024年8月期決算短信の一部訂正について")
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("amendment sync")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        if filing.is_amendment is not True:
            _fail("is_amendment")
        names = {
            (fact.local_name, fact.value_num)
            for fact in read_tdnet_pdf(_pages(parts), _disclosure(
                disclosure_no=no, code="99830", type_of_period="FY",
                type_of_document="FYFinancialStatements_Consolidated_IFRS",
                disclosed_date=date(2024, 7, 3),
                period_start=date(2023, 9, 1), period_end=date(2024, 8, 31),
                fiscal_year_start=date(2023, 9, 1), fiscal_year_end=date(2024, 8, 31),
            )).facts
        }
        if ("定期預金の預入による支出", MILLION * Decimal("-212182")) not in names:
            _fail(f"after cf {names}")
        if ("定期預金の払出による収入", MILLION * Decimal("207052")) not in names:
            _fail(f"after inflow {names}")
        if any(value == MILLION * Decimal("-530484") for _, value in names):
            _fail("before cf kept")
    finally:
        db.close()


def test_kab213_hanging_row_label_is_forecast_not_actual() -> None:
    """1377 20240214537240: 前回発表予想（Ａ）が数値より 5pt 上。actual にしない。"""
    parts = [
        _text(40, 40, "連結業績予想の修正", 140),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(280, 76, "連結売上高", 80),
        _cell(400, 76, "営業利益", 60),
        _cell(520, 76, "経常利益", 60),
        _cell(700, 76, "親会社株主に帰属する当期純利益", 180),
        _cell(860, 76, "1株当たり当期純利益", 140),
        _text(40, 94, "円銭", 30),
        _text(40, 476, "前回発表予想（Ａ）", 140),
        _cell(280, 481, "80,000", 50),
        _cell(400, 481, "8,000", 45),
        _cell(520, 481, "8,000", 45),
        _cell(700, 481, "8,000", 45),
        _cell(860, 481, "180.38", 45),
        _text(40, 500, "今回修正予想（Ｂ）", 140),
        _cell(280, 505, "84,000", 50),
        _cell(400, 505, "11,000", 50),
        _cell(520, 505, "11,000", 50),
        _cell(700, 505, "15,500", 50),
        _cell(860, 505, "349.49", 45),
    ]
    no = "20240214537240"
    statement = SimpleNamespace(
        disclosure_no=no, code="13770", disclosed_date=date(2024, 2, 14),
        disclosed_time="15:00", type_of_period="FY",
        type_of_document="EarnForecastRevision",
        period_start=date(2023, 6, 1), period_end=date(2024, 5, 31),
        fiscal_year_start=date(2023, 6, 1), fiscal_year_end=date(2024, 5, 31),
    )
    db = SessionLocal()
    try:
        db.merge(Statement(
            disclosure_no="20230713521566", code="13770", disclosed_date=date(2023, 7, 13),
            disclosed_time="15:00", type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_JP",
            period_start=date(2022, 6, 1), period_end=date(2023, 5, 31),
            fiscal_year_start=date(2022, 6, 1), fiscal_year_end=date(2023, 5, 31),
        ))
        _seed(db, no, _pages(parts), statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("rev sync")
        rows = _rows(db, no)
        expect = {
            ("net_sales", "forecast", "point", "FY"): MILLION * 84000,
            ("operating_income", "forecast", "point", "FY"): MILLION * 11000,
            ("ordinary_income", "forecast", "point", "FY"): MILLION * 11000,
            ("net_income", "forecast", "point", "FY"): MILLION * 15500,
            ("eps_basic", "forecast", "point", "FY"): Decimal("349.49"),
            ("net_sales", "forecast_previous", "point", "FY"): MILLION * 80000,
            ("net_income", "forecast_previous", "point", "FY"): MILLION * 8000,
            ("eps_basic", "forecast_previous", "point", "FY"): Decimal("180.38"),
        }
        for key, value in expect.items():
            if rows.get(key) != value:
                _fail(f"{key} got {rows.get(key)} want {value} all={rows}")
        if any(key[1] == "actual" for key in rows):
            _fail(f"actual leaked {rows}")
    finally:
        db.close()


def test_kab213_ifrs_revision_inherits_standard_from_statements() -> None:
    """2502 20260605564305: 修正開示は standard=None。近い *_IFRS から基準を取る。"""
    parts = [
        _text(40, 40, "業績予想の修正", 100),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(280, 76, "売上収益", 60),
        _cell(420, 76, "営業利益", 60),
        _cell(620, 76, "親会社の所有者に帰属する当期利益", 200),
        _cell(800, 76, "基本的1株当たり当期利益", 160),
        _text(40, 94, "円銭", 30),
        _text(40, 120, "前回発表予想（Ａ）", 140),
        _cell(280, 125, "2,950,000", 60),
        _cell(420, 125, "255,000", 50),
        _cell(620, 125, "167,500", 50),
        _cell(800, 125, "112.74", 40),
        _text(40, 150, "今回修正予想（Ｂ）", 140),
        _cell(280, 155, "2,890,000", 60),
        _cell(420, 155, "185,000", 50),
        _cell(620, 155, "120,000", 50),
        _cell(800, 155, "80.24", 40),
    ]
    no = "20260605564305"
    prior_no = "20260205500000"
    db = SessionLocal()
    try:
        db.merge(Statement(
            disclosure_no=prior_no, code="25020", disclosed_date=date(2026, 2, 5),
            disclosed_time="15:00", type_of_period="FY",
            type_of_document="FYFinancialStatements_Consolidated_IFRS",
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            fiscal_year_start=date(2025, 1, 1), fiscal_year_end=date(2025, 12, 31),
        ))
        statement = SimpleNamespace(
            disclosure_no=no, code="25020", disclosed_date=date(2026, 6, 5),
            disclosed_time="15:00", type_of_period="FY",
            type_of_document="EarnForecastRevision",
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
            fiscal_year_start=date(2025, 1, 1), fiscal_year_end=date(2025, 12, 31),
        )
        _seed(db, no, _pages(parts), statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("ifrs rev sync")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        if filing.accounting_standard != "IFRS":
            _fail(filing.accounting_standard)
        rows = _rows(db, no)
        if rows.get(("net_sales", "forecast", "point", "FY")) != MILLION * 2890000:
            _fail(f"sales {rows}")
        if rows.get(("net_income", "forecast", "point", "FY")) != MILLION * 120000:
            _fail(f"income {rows}")
        if rows.get(("eps_basic", "forecast", "point", "FY")) != Decimal("80.24"):
            _fail(f"eps {rows}")
        if rows.get(("net_sales", "forecast_previous", "point", "FY")) != MILLION * 2950000:
            _fail(f"prev sales {rows}")
    finally:
        db.close()


def test_kab213_flash_results_are_absent() -> None:
    """8511 の速報値のお知らせは absent（failed に数えない）。"""
    no = "20250509537810"
    statement = SimpleNamespace(
        disclosure_no=no, code="85110", disclosed_date=date(2025, 5, 9),
        disclosed_time="15:00", type_of_period="FY",
        type_of_document="EarnForecastRevision",
        period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
        fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
    )
    html_text = _pages([_text(40, 40, "連結決算速報値", 120), _cell(200, 80, "1,000", 40)])
    db = SessionLocal()
    try:
        _seed(db, no, html_text, statement, title="連結決算速報値に関するお知らせ")
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.ABSENT:
            _fail("flash should be absent")
        if db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).count() != 0:
            _fail("flash filing created")
    finally:
        db.close()


def test_kab213_dividend_yen_and_hanging_label() -> None:
    """8511 20260212556179: 「40 円 40 円 80 円」。行名が数値より上。"""
    parts = [
        _text(40, 40, "配当予想の修正", 100),
        _text(40, 58, "（単位：円）", 80),
        _cell(280, 76, "第2四半期末", 80),
        _cell(400, 76, "期末", 40),
        _cell(520, 76, "合計", 40),
        _text(40, 100, "前回予想", 80),
        _cell(280, 109, "40", 30), _text(285, 109, "円", 12),
        _cell(400, 109, "40", 30), _text(405, 109, "円", 12),
        _cell(520, 109, "80", 30), _text(525, 109, "円", 12),
        _text(40, 125, "今回予想", 80),
        _cell(280, 134, "46", 30), _text(285, 134, "円", 12),
        _cell(400, 134, "40", 30), _text(405, 134, "円", 12),
        _cell(520, 134, "86", 30), _text(525, 134, "円", 12),
    ]
    no = "20260212556179"
    statement = SimpleNamespace(
        disclosure_no=no, code="85110", disclosed_date=date(2026, 2, 12),
        disclosed_time="15:00", type_of_period="FY",
        type_of_document="DividendForecastRevision",
        period_start=date(2025, 4, 1), period_end=date(2026, 3, 31),
        fiscal_year_start=date(2025, 4, 1), fiscal_year_end=date(2026, 3, 31),
    )
    db = SessionLocal()
    try:
        _seed(db, no, _pages(parts), statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("div rev sync")
        rows = _rows(db, no)
        if rows.get(("dps", "forecast_previous", "point", "Q2")) != Decimal("40"):
            _fail(f"prev q2 {rows}")
        if rows.get(("dps", "forecast_previous", "point", "year_end")) != Decimal("40"):
            _fail(f"prev year_end {rows}")
        if rows.get(("dps", "forecast_previous", "point", "annual")) != Decimal("80"):
            _fail(f"prev annual {rows}")
        if rows.get(("dps", "forecast", "point", "Q2")) != Decimal("46"):
            _fail(f"curr q2 {rows}")
        if rows.get(("dps", "forecast", "point", "year_end")) != Decimal("40"):
            _fail(f"curr year_end {rows}")
        if rows.get(("dps", "forecast", "point", "annual")) != Decimal("86"):
            _fail(f"curr annual {rows}")
    finally:
        db.close()


def test_kab214_note_marks_are_not_amounts() -> None:
    """7532 FY 20260809515913: ※1 の番号が金額にならない。"""
    parts = [
        _text(40, 40, "連結損益計算書", 120),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 100),
        _cell(560, 76, "当連結会計年度", 100),
        _text(40, 112, "売上原価 ※1", 90),
        _cell(400, 112, "1,530,025", 55),
        _text(405, 112, "※1", 20),
        _cell(560, 112, "1,676,814", 55),
        _text(40, 140, "その他の包括利益合計 ※", 160),
        _cell(400, 140, "△1,023", 45),
        _text(405, 140, "※", 12),
        _cell(560, 140, "15,248", 45),
        _text(40, 180, "連結貸借対照表", 120),
        _text(40, 198, "（単位：百万円）", 100),
        _cell(400, 216, "前連結会計年度", 100),
        _cell(560, 216, "当連結会計年度", 100),
        _text(40, 234, "１年内返済予定の長期借入金 ※７", 200),
        _cell(400, 234, "56,375", 45),
        _cell(560, 234, "32,998", 45),
    ]
    d = _disclosure(
        disclosure_no="20260809515913", code="75320",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        disclosed_date=date(2026, 8, 9),
        period_start=date(2025, 7, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2025, 7, 1), fiscal_year_end=date(2026, 6, 30),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    if facts.get(("売上原価", "CurrentYearDuration")) != MILLION * 1676814:
        _fail(f"cogs {facts}")
    if facts.get(("売上原価", "Prior1YearDuration")) != MILLION * 1530025:
        _fail(f"cogs prior {facts}")
    if MILLION * 7 in _values(pkg) or MILLION * 2 in _values(pkg) or MILLION * 3 in _values(pkg):
        _fail("note number leaked")
    if facts.get(("その他の包括利益合計", "Prior1YearDuration")) != MILLION * Decimal("-1023"):
        _fail(f"oci prior {facts}")


def test_kab215_plain_q2_is_accumulated_not_year() -> None:
    """9983 20220413520835: 「2022年8月期第2四半期」の EPS 1,437.41 が Q2 実績。"""
    parts = [
        _text(40, 40, "（1）経営成績", 100),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "売上高", 50),
        _cell(560, 76, "1株当たり当期純利益", 140),
        _text(40, 94, "円銭", 30),
        _text(40, 112, "2022年8月期第2四半期", 160),
        _cell(400, 112, "1,000,000", 60),
        _cell(560, 112, "1,437.41", 50),
        _text(40, 130, "2021年8月期第2四半期", 160),
        _cell(400, 130, "900,000", 55),
        _cell(560, 130, "1,036.76", 50),
    ]
    d = _disclosure(
        disclosure_no="20220413520835", code="99830",
        type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_IFRS",
        disclosed_date=date(2022, 4, 13),
        period_start=date(2021, 9, 1), period_end=date(2022, 2, 28),
        fiscal_year_start=date(2021, 9, 1), fiscal_year_end=date(2022, 8, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    key = ("1株当たり当期純利益", "CurrentAccumulatedQ2Duration_ResultMember")
    if facts.get(key) != Decimal("1437.41"):
        _fail(f"{facts}")
    if any("CurrentYearDuration" in ref and "1株当たり" in name for name, ref in facts):
        _fail(f"year span {facts}")


def test_kab216_prose_notes_are_not_column_headers() -> None:
    """1377 Q1 20211012409694: 収益認識の注記が列見出しに入ると EPS が落ちる。"""
    note = "収益認識に関する会計基準を適用しており増減率は記載しておりませんのでご留意ください"
    note2 = "当第1四半期連結会計期間及び前第1四半期連結会計期間の数値は参考として掲げています"
    parts = [
        _text(40, 40, "（1）経営成績", 100),
        _text(40, 58, note, 400),
        _text(40, 76, note2, 400),
        _text(40, 94, "※ 増減率は記載しておりません", 180),
        _text(40, 112, "（単位：百万円）", 100),
        _cell(400, 130, "売上高", 50),
        _cell(560, 130, "1株当たり当期純利益", 140),
        _text(40, 148, "円銭", 30),
        _text(40, 166, "当第1四半期", 100),
        _cell(400, 166, "10,000", 50),
        _cell(560, 166, "49.54", 40),
    ]
    d = _disclosure(
        disclosure_no="20211012409694", code="13770",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_Consolidated_JP",
        disclosed_date=date(2021, 10, 12),
        period_start=date(2021, 6, 1), period_end=date(2021, 8, 31),
        fiscal_year_start=date(2021, 6, 1), fiscal_year_end=date(2022, 5, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    key = ("1株当たり当期純利益", "CurrentAccumulatedQ1Duration_ResultMember")
    if facts.get(key) != Decimal("49.54"):
        _fail(f"{facts}")


def test_kab216_midword_digit_is_not_amount() -> None:
    """7532 FY summary_fc: 注記末尾の「４」が列の左端になって予想 EPS が落ちる。"""
    parts = [
        _text(40, 40, "3. 連結業績予想", 100),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(280, 76, "売上高", 50),
        _cell(420, 76, "営業利益", 60),
        _cell(620, 76, "1株当たり当期純利益", 140),
        _text(40, 94, "円銭", 30),
        _text(40, 112, "第2四半期(累計)", 110),
        _cell(280, 112, "1,000", 40),
        _cell(420, 112, "100", 30),
        _cell(620, 112, "19.58", 40),
        _text(40, 130, "通期", 40),
        _cell(280, 130, "2,000", 40),
        _cell(420, 130, "200", 30),
        _cell(620, 130, "36.61", 40),
        _text(34, 160, "（注）当社はＡ社との株式交換契約を2026年４", 520),
    ]
    d = _disclosure(
        disclosure_no="20260809515913", code="75320",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        disclosed_date=date(2026, 8, 9),
        period_start=date(2025, 7, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2025, 7, 1), fiscal_year_end=date(2026, 6, 30),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    q2 = next(value for (name, ref), value in facts.items() if name == "1株当たり当期純利益" and "Q2" in ref)
    fy = next(value for (name, ref), value in facts.items() if name == "1株当たり当期純利益" and "Year" in ref)
    if q2 != Decimal("19.58") or fy != Decimal("36.61"):
        _fail(f"{facts}")
    if MILLION * 4 in _values(pkg):
        _fail("note 4 leaked")


def test_kab216_cf_class_head_does_not_glue() -> None:
    """2502 Q2 20260813519681: 投資活動によるキャッシュ・フローが capex の行名に付かない。"""
    parts = [
        _text(40, 40, "連結キャッシュ・フロー計算書", 180),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "当中間連結会計期間", 120),
        _text(40, 94, "投資活動によるキャッシュ・フロー", 200),
        _text(40, 112, "有形固定資産の取得による支出", 180),
        _cell(400, 112, "△66,649", 55),
    ]
    d = _disclosure(
        disclosure_no="20260813519681", code="25020",
        type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_IFRS",
        disclosed_date=date(2026, 8, 13),
        period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
        fiscal_year_start=date(2026, 1, 1), fiscal_year_end=date(2026, 12, 31),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    names = {fact.local_name for fact in pkg.facts}
    if "有形固定資産の取得による支出" not in names:
        _fail(names)
    if any("投資活動によるキャッシュ・フロー有形" in name for name in names):
        _fail(names)


def test_kab217_ifrs_depreciation_aliases() -> None:
    """9983 / 2502: 減価償却費及び(その他の)償却費 → depreciation。"""
    if label_key("減価償却費及びその他の償却費") != "減価償却費及びその他の償却費":
        _fail(label_key("減価償却費及びその他の償却費"))
    parts = [
        _text(40, 40, "連結キャッシュ・フロー計算書", 180),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "当連結会計年度", 100),
        _text(40, 94, "減価償却費及びその他の償却費", 180),
        _cell(400, 94, "237,251", 50),
    ]
    no = "20261008547647"
    statement = SimpleNamespace(
        disclosure_no=no, code="99830", disclosed_date=date(2026, 10, 8),
        disclosed_time="15:00", type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_IFRS",
        period_start=date(2025, 9, 1), period_end=date(2026, 8, 31),
        fiscal_year_start=date(2025, 9, 1), fiscal_year_end=date(2026, 8, 31),
    )
    db = SessionLocal()
    try:
        _seed(db, no, _pages(parts), statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("dep sync")
        rows = _rows(db, no)
        if rows.get(("depreciation", "actual", "point", "FY")) != MILLION * 237251:
            _fail(f"{rows}")
        other = [
            _text(40, 40, "連結キャッシュ・フロー計算書", 180),
            _text(40, 58, "（単位：百万円）", 100),
            _cell(400, 76, "当中間連結会計期間", 120),
            _text(40, 94, "減価償却費及び償却費", 140),
            _cell(400, 94, "86,649", 45),
        ]
        other_no = "20260813519681"
        _seed(db, other_no, _pages(other), SimpleNamespace(
            disclosure_no=other_no, code="25020", disclosed_date=date(2026, 8, 13),
            disclosed_time="15:00", type_of_period="2Q",
            type_of_document="2QFinancialStatements_Consolidated_IFRS",
            period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
            fiscal_year_start=date(2026, 1, 1), fiscal_year_end=date(2026, 12, 31),
        ))
        if sync_pdf_filing(db, other_no) is not SyncOutcome.PARSED:
            _fail("dep amort sync")
        other_rows = _rows(db, other_no)
        if other_rows.get(("depreciation", "actual", "point", "Q2")) != MILLION * 86649:
            _fail(f"{other_rows}")
    finally:
        db.close()


def test_kab218_garbled_statement_pages_stay_parsed() -> None:
    """7532: 本表ページが化けても parsed。parse_error に印。サマリーの売上は残る。"""
    garbled = "\u3e26\u3e27\u3e30" * 40
    page1 = [
        _text(40, 40, "（1）経営成績", 100),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "売上高", 50),
        _cell(560, 76, "1株当たり当期純利益", 140),
        _text(40, 94, "円銭", 30),
        _text(40, 112, "当第3四半期", 100),
        _cell(400, 112, "1,234,000", 60),
        _cell(560, 112, "49.40", 40),
    ]
    page2 = [_text(40, 40, "（2）財政状態の説明", 140)]
    page3 = [_text(40, 40, garbled, 400), _text(40, 80, garbled, 400)]
    page4 = [_text(40, 40, garbled, 400)]
    html_text = _pages(page1, page2, page3, page4)
    pages = read_pages(html_text)
    indexes = garbled_page_indexes(pages)
    if indexes != [2, 3]:
        _fail(f"indexes {indexes}")
    if format_garbled_pages(indexes) != "garbled pages=3-4":
        _fail(format_garbled_pages(indexes))
    no = "20220429532224"
    statement = SimpleNamespace(
        disclosure_no=no, code="75320", disclosed_date=date(2022, 4, 29),
        disclosed_time="15:00", type_of_period="3Q",
        type_of_document="3QFinancialStatements_Consolidated_JP",
        period_start=date(2021, 7, 1), period_end=date(2022, 3, 31),
        fiscal_year_start=date(2021, 7, 1), fiscal_year_end=date(2022, 6, 30),
    )
    db = SessionLocal()
    try:
        _seed(db, no, html_text, statement)
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("garbled should stay parsed")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        if filing.parse_error != "garbled pages=3-4":
            _fail(filing.parse_error)
        rows = _rows(db, no)
        if rows.get(("net_sales", "actual", "point", "Q3")) != MILLION * 1234000:
            _fail(f"sales {rows}")
    finally:
        db.close()


def test_kab220_dash_104_is_not_a_page_number() -> None:
    """7532 20221212577362: y=719 の「- 104」をページ番号として落とさない。cash_end と 104 が残る。"""
    parts = [
        _text(40, 40, "連結キャッシュ・フロー計算書", 180),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前中間連結会計期間", 120),
        _cell(560, 76, "当中間連結会計期間", 120),
        _text(40, 700, "非連結子会社との合併に伴う現金及び現金同等物の", 280),
        _text(370, 719, "-", 8),
        _cell(560, 719, "104", 30),
        _text(40, 735, "増加額", 40),
        _text(40, 760, "現金及び現金同等物の期末残高", 180),
        _cell(400, 760, "177,268", 50),
        _cell(560, 760, "223,770", 50),
        _text(280, 800, "- 6 -", 30),
    ]
    d = _disclosure(
        disclosure_no="20221212577362", code="75320",
        type_of_period="2Q",
        type_of_document="2QFinancialStatements_Consolidated_JP",
        disclosed_date=date(2022, 12, 12),
        period_start=date(2022, 7, 1), period_end=date(2022, 12, 31),
        fiscal_year_start=date(2022, 7, 1), fiscal_year_end=date(2023, 6, 30),
    )
    pkg = read_tdnet_pdf(_pages(parts), d)
    facts = _facts(pkg)
    if facts.get(("現金及び現金同等物の期末残高", "CurrentAccumulatedQ2Duration")) != MILLION * 223770:
        _fail(f"cash_end {facts}")
    merged = "非連結子会社との合併に伴う現金及び現金同等物の増加額"
    if facts.get((merged, "CurrentAccumulatedQ2Duration")) != MILLION * 104:
        _fail(f"increase {facts}")
    if any("期末残高" in name and "増加額" in name for name, _ref in facts):
        _fail(f"glued {facts}")

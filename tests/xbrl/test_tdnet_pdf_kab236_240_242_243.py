"""KAB-236 / 240 / 242 / 243: 短信 PDF の行名・単位・個別 JPGAAP。

IR BANK も本番 DB も使わない。PDF はリポジトリに入れない。対象開示は golden HTML。
"""
from __future__ import annotations

import gzip
import hashlib
import html
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Statement, Stock, TdnetPdfDocument, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.pdf import PdfDisclosure, label_key, read_tdnet_pdf, SyncOutcome, sync_pdf_filing

MILLION = Decimal(10) ** 6
GOLDEN_HTML = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "html"
EXPECTED = Path(__file__).resolve().parents[1] / "golden" / "tdnet_pdf" / "expected.json"

KAB236 = "20230120591941"
KAB240 = "20220428530699"
KAB242 = "20261008548015"
KAB243 = "20221115567382"


def _fail(msg: object) -> None:
    raise AssertionError(msg)


def _text(x: float, y: float, text: str, width: float) -> str:
    body = html.escape(text, quote=False)
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10.0" '
        f'textLength="{width:.1f}" lengthAdjust="spacingAndGlyphs">{body}</text>'
    )


def _cell(x1: float, y: float, text: str, width: float = 50) -> str:
    return _text(x1 - width, y, text, width)


def _pages(*pages: list[str]) -> str:
    svgs = "".join(f'<svg xmlns="http://www.w3.org/2000/svg">{"".join(parts)}</svg>' for parts in pages)
    return f"<html><body>{svgs}</body></html>"


def _disclosure(**kwargs) -> PdfDisclosure:
    base = {
        "disclosure_no": "20230120591941",
        "code": "95010",
        "disclosed_date": date(2023, 1, 23),
        "disclosed_time": "15:00",
        "type_of_period": "FY",
        "type_of_document": "EarnForecastRevision",
        "period_start": date(2022, 4, 1),
        "period_end": date(2023, 3, 31),
        "fiscal_year_start": date(2022, 4, 1),
        "fiscal_year_end": date(2023, 3, 31),
    }
    base.update(kwargs)
    parsed = PdfDisclosure.parse(SimpleNamespace(**base))
    assert isinstance(parsed, PdfDisclosure), parsed
    return parsed


def _facts(pkg) -> dict[tuple[str, str], Decimal]:
    return {(fact.local_name, fact.context_ref): fact.value_num for fact in pkg.facts}


def _golden(no: str) -> tuple[str, PdfDisclosure]:
    html_text = gzip.decompress((GOLDEN_HTML / f"{no}.html.gz").read_bytes()).decode("utf-8")
    doc = json.loads(EXPECTED.read_text(encoding="utf-8"))["disclosures"][no]
    st = doc["statement"]
    parsed = PdfDisclosure.parse(SimpleNamespace(
        disclosure_no=no,
        **{k: (date.fromisoformat(v) if k.endswith(("_date", "_start", "_end")) else v) for k, v in st.items()},
    ))
    assert isinstance(parsed, PdfDisclosure), parsed
    return html_text, parsed


def _dt(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc).replace(tzinfo=None)


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
        disclosure_no=no, code=statement.code, disclosed_date=statement.disclosed_date,
        disclosed_time=statement.disclosed_time, title=title or statement.type_of_document,
        status="ok", html_gz=gzip.compress(raw), bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(), created_at=_dt(2024, 8, 1, 15, 0),
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
        found[(item.code, row.value_kind, row.range_side, row.target_period, int(row.is_consolidated))] = (
            Decimal(str(row.value))
        )
    return found


def test_kab240_owners_profit_keeps_attribution_after_or_loss() -> None:
    """「又は損失(△)の帰属 親会社の所有者」を _ALTERNATIVE で落とさない。単純な又は損失は従来どおり。"""
    if label_key("四半期利益又は損失(△)の帰属親会社の所有者") != "親会社の所有者に帰属する当期利益":
        _fail(label_key("四半期利益又は損失(△)の帰属親会社の所有者"))
    if label_key("四半期利益又は損失(△)の帰属 親会社の所有者") != "親会社の所有者に帰属する当期利益":
        _fail(label_key("四半期利益又は損失(△)の帰属 親会社の所有者"))
    if label_key("当期純利益又は当期純損失(△)") != "当期純利益":
        _fail(label_key("当期純利益又は当期純損失(△)"))
    if label_key("営業利益又は営業損失（△）") != "営業利益":
        _fail(label_key("営業利益又は営業損失（△）"))
    if label_key("営業損益") != "営業利益":
        _fail(label_key("営業損益"))
    if label_key("経常損益") != "経常利益":
        _fail(label_key("経常損益"))
    if label_key("親会社株主に帰属する当期純損益") != "親会社株主に帰属する当期純利益":
        _fail(label_key("親会社株主に帰属する当期純損益"))


def test_kab240_nabtesco_pl_owners_profit_from_golden() -> None:
    """20220428530699: PL の親会社帰属利益 △4,388 百万円。"""
    html_text, d = _golden(KAB240)
    facts = _facts(read_tdnet_pdf(html_text, d))
    name = "親会社の所有者に帰属する当期利益"
    got = facts.get((name, "CurrentAccumulatedQ1Duration"))
    if got != MILLION * Decimal("-4388"):
        _fail(facts)


def test_kab242_paren_unit_row_not_joined_to_item() -> None:
    """列見出し ('売上収益','(百万円)') は売上収益のまま。"""
    parts = [
        _text(40, 40, "通期連結業績予想の修正", 180),
        _text(150, 58, "売上収益", 40),
        _text(280, 58, "営業利益", 40),
        _text(410, 58, "親会社の所有者に帰属する当期利益", 160),
        _text(600, 58, "基本的1株当たり当期利益", 140),
        _text(148, 76, "(百万円)", 40),
        _text(278, 76, "(百万円)", 40),
        _text(430, 76, "(百万円)", 40),
        _text(640, 76, "(円)", 24),
        _text(40, 100, "今回修正予想", 80),
        _cell(190, 100, "1,229,500", 50),
        _cell(320, 100, "160,000", 45),
        _cell(500, 100, "120,000", 45),
        _cell(680, 100, "81.35", 40),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no=KAB242, code="45430",
        disclosed_date=date(2026, 10, 8),
        period_start=date(2026, 4, 1), period_end=date(2027, 3, 31),
        fiscal_year_start=date(2026, 4, 1), fiscal_year_end=date(2027, 3, 31),
    )))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上収益", ctx)) != MILLION * 1229500:
        _fail(facts)
    if any("(百万円)" in name or name.endswith("(円)") for name, _ref in facts):
        _fail(facts)


def test_kab242_terumo_forecast_from_golden() -> None:
    """20261008548015: FY 予想 4 件。括弧付き単位が科目名に残らない。"""
    html_text, d = _golden(KAB242)
    facts = _facts(read_tdnet_pdf(html_text, d))
    ctx = "CurrentYearDuration_CurrentMember_ForecastMember"
    want = {
        ("売上収益", ctx): MILLION * 1229500,
        ("営業利益", ctx): MILLION * 160000,
        ("親会社の所有者に帰属する当期利益", ctx): MILLION * 120000,
        ("基本的1株当たり当期利益", ctx): Decimal("81.35"),
    }
    for key, value in want.items():
        if facts.get(key) != value:
            _fail((key, facts.get(key), facts))
    if any("(百万円)" in name or name.endswith("(円)") for name, _ref in facts):
        _fail(facts)


def test_kab236_dash_previous_row_is_kept_and_not_joined() -> None:
    """前回が「－」だけの行は点線として捨てず、今回行へ名前を連結しない。"""
    parts = [
        _text(40, 40, "通期連結業績予想の修正", 180),
        _text(40, 58, "（単位：百万円）", 80),
        _text(180, 76, "売上高", 40),
        _text(300, 76, "営業利益", 40),
        _text(420, 76, "経常利益", 40),
        _text(500, 76, "親会社株主に帰属する当期純利益", 100),
        _text(680, 76, "1株当たり当期純利益", 80),
        _text(40, 100, "前回発表予想(A)", 90),
        _cell(220, 100, "－", 8),
        _cell(340, 100, "－", 8),
        _cell(460, 100, "－", 8),
        _cell(600, 100, "－", 8),
        _cell(760, 100, "－", 8),
        _text(40, 124, "今回修正予想(B)", 90),
        _cell(220, 124, "7,931,000", 50),
        _cell(340, 124, "△488,000", 50),
        _cell(460, 124, "△502,000", 50),
        _cell(600, 124, "△317,000", 50),
        _cell(760, 124, "△197.86", 45),
    ]
    pkg = read_tdnet_pdf(_pages(parts), _disclosure())
    facts = _facts(pkg)
    current = "CurrentYearDuration_CurrentMember_ForecastMember"
    if any("PreviousMember" in ref and "CurrentMember" in ref for _name, ref in facts):
        _fail(facts)
    if facts.get(("売上高", current)) != MILLION * 7931000:
        _fail(facts)
    if facts.get(("営業利益", current)) != MILLION * Decimal("-488000"):
        _fail(facts)
    if facts.get(("経常利益", current)) != MILLION * Decimal("-502000"):
        _fail(facts)
    if facts.get(("親会社株主に帰属する当期純利益", current)) != MILLION * Decimal("-317000"):
        _fail(facts)
    if facts.get(("1株当たり当期純利益", current)) != Decimal("-197.86"):
        _fail(facts)
    if any("前回" in name and "今回" in name for name, _ref in facts):
        _fail(facts)


def test_kab236_tepco_forecast_from_golden() -> None:
    """20230120591941: 今回修正予想 5 項目。前回は未定なので Previous が付かない。"""
    html_text, d = _golden(KAB236)
    facts = _facts(read_tdnet_pdf(html_text, d))
    current = [
        ref for name, ref in facts
        if name == "売上高" and "ForecastMember" in ref
    ]
    if any("PreviousMember" in ref for ref in current):
        _fail(facts)
    want_current = "CurrentYearDuration_CurrentMember_ForecastMember"
    if facts.get(("売上高", want_current)) != MILLION * 7931000:
        _fail(facts)
    if facts.get(("営業利益", want_current)) != MILLION * Decimal("-488000"):
        _fail(facts)
    if facts.get(("経常利益", want_current)) != MILLION * Decimal("-502000"):
        _fail(facts)
    np = facts.get(("親会社株主に帰属する当期純利益", want_current))
    if np != MILLION * Decimal("-317000"):
        _fail(facts)
    if facts.get(("1株当たり当期純利益", want_current)) != Decimal("-197.86"):
        _fail(facts)


def test_kab243_revision_row_label_not_column_header() -> None:
    """「前回発表予想（Ａ）　円銭」の（Ａ）を売上高列に入れない。"""
    parts = [
        _text(40, 40, "2022年12月期通期個別業績予想値の修正について", 280),
        _text(464, 58, "（単位：百万円）", 80),
        _text(230, 76, "売上高", 30),
        _text(310, 76, "経常利益", 40),
        _text(390, 76, "当期純利益", 50),
        _text(456, 76, "１株当たり", 50),
        _text(452, 90, "当期純利益", 50),
        _text(95, 108, "前", 8),
        _text(107, 108, "回", 8),
        _text(119, 108, "発", 8),
        _text(131, 108, "表", 8),
        _text(143, 108, "予", 8),
        _text(155, 108, "想", 8),
        _text(181, 108, "（Ａ）", 16),
        _text(478, 108, "円", 8),
        _text(500, 108, "銭", 8),
        _cell(271, 120, "171,000", 28),
        _cell(345, 120, "19,600", 24),
        _cell(420, 120, "15,400", 24),
        _text(95, 140, "今", 8),
        _text(107, 140, "回", 8),
        _text(119, 140, "修", 8),
        _text(131, 140, "正", 8),
        _text(143, 140, "予", 8),
        _text(155, 140, "想", 8),
        _text(182, 140, "（Ｂ）", 16),
        _cell(271, 140, "171,000", 28),
        _cell(345, 140, "19,600", 24),
        _cell(420, 140, "47,100", 24),
        _cell(487, 140, "392", 13),
        _cell(509, 140, "52", 9),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no=KAB243, code="62680",
        disclosed_date=date(2022, 11, 15), disclosed_time="16:40",
        period_start=date(2022, 1, 1), period_end=date(2022, 12, 31),
        fiscal_year_start=date(2022, 1, 1), fiscal_year_end=date(2022, 12, 31),
    )))
    current = "CurrentYearDuration_CurrentMember_ForecastMember_NonConsolidated"
    if facts.get(("売上高", current)) != MILLION * 171000:
        _fail(facts)
    if facts.get(("経常利益", current)) != MILLION * 19600:
        _fail(facts)
    if facts.get(("当期純利益", current)) != MILLION * 47100:
        _fail(facts)
    glued = [ref for name, ref in facts if name == "売上高" and "CurrentMember" in ref and "PreviousMember" in ref]
    if glued:
        _fail(facts)


def test_kab243_nabtesco_solo_from_golden() -> None:
    """20221115567382: 今回の個別売上・経常利益が CurrentMember。"""
    html_text, d = _golden(KAB243)
    facts = _facts(read_tdnet_pdf(html_text, d))
    current = "CurrentYearDuration_CurrentMember_ForecastMember_NonConsolidated"
    if facts.get(("売上高", current)) != MILLION * 171000:
        _fail(facts)
    if facts.get(("経常利益", current)) != MILLION * 19600:
        _fail(facts)
    if facts.get(("当期純利益", current)) != MILLION * 47100:
        _fail(facts)
    if any(
        name == "売上高" and "CurrentMember" in ref and "PreviousMember" in ref
        for name, ref in facts
    ):
        _fail(facts)


def test_kab243_ifrs_filing_maps_nc_ordinary_income() -> None:
    """IFRS 提出会社の個別修正でも経常利益を採る。連結の経常利益は採らない。"""
    html_text, _d = _golden(KAB243)
    no = KAB243
    db = SessionLocal()
    try:
        db.merge(Statement(
            disclosure_no="20220428530699", code="62680", disclosed_date=date(2022, 4, 28),
            disclosed_time="16:00", type_of_period="1Q",
            type_of_document="1QFinancialStatements_Consolidated_IFRS",
            period_start=date(2022, 1, 1), period_end=date(2022, 3, 31),
            fiscal_year_start=date(2022, 1, 1), fiscal_year_end=date(2022, 12, 31),
        ))
        statement = SimpleNamespace(
            disclosure_no=no, code="62680", disclosed_date=date(2022, 11, 15),
            disclosed_time="16:40", type_of_period="FY",
            type_of_document="EarnForecastRevision",
            period_start=date(2022, 1, 1), period_end=date(2022, 12, 31),
            fiscal_year_start=date(2022, 1, 1), fiscal_year_end=date(2022, 12, 31),
        )
        _seed(db, no, html_text, statement, title="2022年12月期通期個別業績予想の修正に関するお知らせ")
        apply_mappings(db)
        if sync_pdf_filing(db, no) is not SyncOutcome.PARSED:
            _fail("sync")
        filing = db.query(XbrlFiling).filter_by(source="tdnet_pdf", doc_id=no).one()
        if filing.accounting_standard != "IFRS":
            _fail(filing.accounting_standard)
        rows = _rows(db, no)
        if rows.get(("net_sales", "forecast", "point", "FY", 0)) != MILLION * 171000:
            _fail(rows)
        if rows.get(("ordinary_income", "forecast", "point", "FY", 0)) != MILLION * 19600:
            _fail(rows)
        if rows.get(("net_income", "forecast", "point", "FY", 0)) != MILLION * 47100:
            _fail(rows)
        if any(key[0] == "ordinary_income" and key[4] == 1 for key in rows):
            _fail(rows)
    finally:
        db.close()


def _forecast_with_gap_percent(
    *,
    op_name: str,
    odp_name: str,
    np_name: str,
    h1: tuple[str, str, str, str],
    fy: tuple[str, str, str, str],
) -> list[str]:
    """％ が dash 列の右の隙間に落ちるサマリー予想（7513 / 9501 / 7865）。"""
    return [
        _text(40, 40, "3. 2022年8月期の業績予想（2021年9月1日～2022年8月31日）", 300),
        _text(40, 58, "（単位：百万円）", 80),
        _text(125, 76, "売上高", 30),
        _text(230, 76, op_name, 40),
        _text(340, 76, odp_name, 40),
        _text(445, 76, np_name, 70),
        _text(140, 92, "百万円", 28),
        _text(192, 92, "％", 8),
        _text(248, 92, "百万円", 28),
        _text(313, 92, "％", 8),
        _text(358, 92, "百万円", 28),
        _text(423, 92, "％", 8),
        _text(468, 92, "百万円", 28),
        _text(533, 92, "％", 8),
        _text(40, 110, "第２四半期(累計)", 80),
        _cell(170, 110, h1[0], 45),
        _cell(200, 110, "10.0", 18),
        _cell(280, 110, h1[1], 40),
        _cell(310, 110, "5.0", 16),
        _cell(390, 110, h1[2], 40),
        _cell(420, 110, "5.0", 16),
        _cell(500, 110, h1[3], 40),
        _cell(530, 110, "8.0", 16),
        _text(40, 128, "通期", 24),
        _cell(170, 128, fy[0], 45),
        _cell(200, 128, "8.0", 16),
        _cell(280, 128, fy[1], 40),
        _cell(310, 128, "12.0", 18),
        _cell(390, 128, fy[2], 40),
        _cell(420, 128, "12.0", 18),
        _cell(500, 128, fy[3], 40),
        _cell(530, 128, "15.0", 18),
    ]


def test_forecast_percent_unit_char_stays_on_percent_column() -> None:
    """列の隙間の ％ を右隣の金額列へ付けない。営業・経常・純利益の H1/FY 予想が残る。"""
    parts = _forecast_with_gap_percent(
        op_name="営業利益", odp_name="経常利益", np_name="当期純利益",
        h1=("131,400", "1,700", "1,700", "1,100"),
        fy=("270,000", "5,000", "5,000", "3,200"),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20211223559502", code="75130",
        type_of_period="1Q",
        type_of_document="1QFinancialStatements_NonConsolidated_JP",
        disclosed_date=date(2022, 1, 11),
        period_start=date(2021, 9, 1), period_end=date(2021, 11, 30),
        fiscal_year_start=date(2021, 9, 1), fiscal_year_end=date(2022, 8, 31),
    )))
    h1 = "CurrentAccumulatedQ2Duration_ForecastMember_NonConsolidated"
    fy_ctx = "CurrentYearDuration_ForecastMember_NonConsolidated"
    want = {
        ("営業利益", h1): MILLION * 1700,
        ("経常利益", h1): MILLION * 1700,
        ("当期純利益", h1): MILLION * 1100,
        ("営業利益", fy_ctx): MILLION * 5000,
        ("経常利益", fy_ctx): MILLION * 5000,
        ("当期純利益", fy_ctx): MILLION * 3200,
    }
    for key, value in want.items():
        if facts.get(key) != value:
            _fail((key, facts.get(key), facts))


def test_forecast_loss_headers_keep_negative_ordinary_and_net() -> None:
    """営業損益・経常損益の見出しと隙間の ％ でも、負の経常・純利益予想を落とさない。"""
    parts = _forecast_with_gap_percent(
        op_name="営業損益", odp_name="経常損益", np_name="親会社株主に帰属する当期純損益",
        h1=("7,000,000", "100,000", "△8,000", "△20,000"),
        fy=("7,931,000", "△488,000", "△16,000", "△41,000"),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20220124571329", code="95010",
        type_of_period="3Q",
        type_of_document="3QFinancialStatements_Consolidated_JP",
        disclosed_date=date(2022, 1, 24),
        period_start=date(2021, 4, 1), period_end=date(2021, 12, 31),
        fiscal_year_start=date(2021, 4, 1), fiscal_year_end=date(2022, 3, 31),
    )))
    fy_ctx = "CurrentYearDuration_ForecastMember"
    if facts.get(("営業利益", fy_ctx)) != MILLION * Decimal(-488000):
        _fail(facts)
    if facts.get(("経常利益", fy_ctx)) != MILLION * Decimal(-16000):
        _fail(facts)
    if facts.get(("親会社株主に帰属する当期純利益", fy_ctx)) != MILLION * Decimal(-41000):
        _fail(facts)


def test_cash_end_not_glued_to_scope_change_zougengaku() -> None:
    """連結範囲変更の折り返し「増減額（△は減少）」を期末残高に付けない。"""
    parts = [
        _text(40, 40, "連結キャッシュ・フロー計算書", 180),
        _text(40, 58, "（単位：百万円）", 100),
        _cell(400, 76, "前連結会計年度", 120),
        _cell(560, 76, "当連結会計年度", 120),
        _text(40, 112, "現金及び現金同等物の期首残高", 180),
        _cell(400, 112, "52,743", 50),
        _cell(560, 112, "37,438", 50),
        _text(40, 136, "連結の範囲の変更に伴う現金及び現金同等物の", 240),
        _cell(400, 144, "－", 8),
        _cell(560, 144, "△586", 40),
        _text(40, 152, "増減額（△は減少）", 120),
        _text(40, 176, "現金及び現金同等物の期末残高", 180),
        _cell(400, 176, "37,438", 50),
        _cell(560, 176, "59,945", 50),
    ]
    d = _disclosure(
        disclosure_no="20240209532259", code="25020",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_Consolidated_JP",
        disclosed_date=date(2024, 2, 9),
        period_start=date(2023, 1, 1), period_end=date(2023, 12, 31),
        fiscal_year_start=date(2023, 1, 1), fiscal_year_end=date(2023, 12, 31),
    )
    facts = _facts(read_tdnet_pdf(_pages(parts), d))
    if facts.get(("現金及び現金同等物の期末残高", "CurrentYearDuration")) != MILLION * 59945:
        _fail(facts)
    if any("期末残高" in name and "減少" in name for name, _ref in facts):
        _fail(f"glued {facts}")
    wrap = facts.get(("連結の範囲の変更に伴う現金及び現金同等物の増減額", "CurrentYearDuration"))
    if wrap != MILLION * Decimal(-586):
        _fail(facts)


def test_yen_sen_unit_char_does_not_steal_roe() -> None:
    """希薄化後 EPS 列の右の隙間の円銭を ROE 列へ付けない（ROE が 100 万倍にならない）。"""
    parts = [
        _text(40, 40, "（1）経営成績", 80),
        _text(40, 58, "（％表示は対前期増減率。）", 120),
        _text(130, 76, "1株当たり当期純利益", 90),
        _text(230, 76, "潜在株式調整後1株当たり当期純利益", 140),
        _text(400, 76, "自己資本当期純利益率", 90),
        _text(178, 92, "円", 8),
        _text(189, 92, "銭", 8),
        _text(258, 92, "円", 8),
        _text(270, 92, "銭", 8),
        _text(430, 92, "％", 8),
        _text(40, 110, "2024年1月期", 60),
        _cell(200, 110, "71.63", 30),
        _cell(250, 110, "－", 8),
        _cell(450, 110, "10.1", 20),
    ]
    facts = _facts(read_tdnet_pdf(_pages(parts), _disclosure(
        disclosure_no="20240229545849", code="78650",
        type_of_period="FY",
        type_of_document="FYFinancialStatements_NonConsolidated_JP",
        disclosed_date=date(2024, 3, 1),
        period_start=date(2023, 1, 21), period_end=date(2024, 1, 20),
        fiscal_year_start=date(2023, 1, 21), fiscal_year_end=date(2024, 1, 20),
    )))
    ctx = "CurrentYearDuration_ResultMember_NonConsolidated"
    if facts.get(("1株当たり当期純利益", ctx)) != Decimal("71.63"):
        _fail(facts)
    roe = facts.get(("自己資本当期純利益率", ctx))
    if roe != Decimal("0.101"):
        _fail(f"roe {roe} {facts}")

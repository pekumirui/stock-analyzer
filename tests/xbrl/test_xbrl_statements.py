"""KAB-383: 短信 XBRL から statements の行を作る。数値は XBRL、raw_json と空欄は J-Quants。"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

from app.db import SessionLocal, init_db
from app.market.ingest import upsert_statements
from app.models import Statement, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.statements import SOURCE_JQUANTS, SOURCE_XBRL, statement_row, sync_statements

FY_START = date(2026, 4, 1)
FY_END = date(2027, 3, 31)
H1_END = date(2026, 9, 30)


def _filing(doc_type: str, period_type: str, *, doc_id="081220261030561115", title="決算短信〔日本基準〕（連結）",
            standard="JPGAAP", submitted=datetime(2026, 11, 5, 13, 55)):  # noqa: DTZ001
    return SimpleNamespace(
        doc_id=doc_id, code="72030", doc_type=doc_type, period_type=period_type,
        fiscal_year_end=H1_END, submitted_at=submitted, title=title, accounting_standard=standard,
    )


def _r(code, kind, target, start, end, value, consolidated=1):
    return SimpleNamespace(code=code, value_kind=kind, target_period=target, period_start=start,
                           period_end=end, is_consolidated=consolidated, value=value)


def _q2_rows():
    return [
        _r("net_sales", "actual", "Q2", FY_START, H1_END, 26_962_462_000_000),
        _r("net_sales", "actual", "Q2", FY_START, H1_END, 9_000, consolidated=0),
        _r("operating_income", "actual", "Q2", FY_START, H1_END, 2_191_124_000_000),
        _r("net_income", "actual", "Q2", FY_START, H1_END, 2_236_446_000_000),
        _r("eps_basic", "actual", "Q2", FY_START, H1_END, 185.97),
        _r("equity_ratio", "actual", "Q2", None, H1_END, 0.367),
        _r("net_sales", "forecast", "FY", FY_START, FY_END, 53_500_000_000_000),
        _r("operating_income", "forecast", "FY", FY_START, FY_END, 3_900_000_000_000),
        _r("eps_basic", "forecast", "FY", FY_START, FY_END, 299.95),
        _r("dps", "actual", "Q2", FY_START, FY_END, 50, consolidated=0),
        _r("dps", "actual", "Q2", date(2025, 4, 1), date(2026, 3, 31), 45, consolidated=0),
        _r("dps", "forecast", "annual", FY_START, FY_END, 100, consolidated=0),
    ]


def test_quarter_row_matches_jquants_shape() -> None:
    row = statement_row(_filing("quarterly", "Q2", standard="IFRS"), _q2_rows())
    want = {
        "disclosure_no": "20261030561115",
        "disclosed_date": date(2026, 11, 5),
        "disclosed_time": "13:55:00",
        "type_of_period": "2Q",
        "type_of_document": "2QFinancialStatements_Consolidated_IFRS",
        "period_start": FY_START, "period_end": H1_END,
        "fiscal_year_start": FY_START, "fiscal_year_end": FY_END,
        "net_sales": 26_962_462_000_000, "operating_profit": 2_191_124_000_000,
        "ordinary_profit": None, "profit": 2_236_446_000_000, "eps": 185.97,
        "fy_net_sales": 53_500_000_000_000, "fy_operating_profit": 3_900_000_000_000, "fy_eps": 299.95,
        "f2_net_sales": None, "is_revision": False,
    }
    for key, value in want.items():
        assert row[key] == value, (key, row[key])
    raw = json.loads(row["raw_json"])
    assert raw["FSales"] == "53500000000000" and raw["FEPS"] == "299.95"
    assert raw["EqAR"] == "0.367" and raw["Div2Q"] == "50.0" and raw["FDivAnn"] == "100.0"
    assert raw["Source"] == SOURCE_XBRL and raw["CurFYEn"] == "2027-03-31"


def test_fy_row_puts_forecasts_into_next_year_keys() -> None:
    nx_end = date(2028, 3, 31)
    rows = [
        _r("net_sales", "actual", "FY", FY_START, FY_END, 50_000),
        _r("operating_income", "actual", "FY", FY_START, FY_END, 4_000),
        _r("net_income", "actual", "FY", FY_START, FY_END, 3_000),
        _r("net_sales", "forecast", "FY", date(2027, 4, 1), nx_end, 51_000),
        _r("net_income", "forecast", "FY", date(2027, 4, 1), nx_end, 3_100),
        _r("net_sales", "forecast", "H1", date(2027, 4, 1), date(2027, 9, 30), 25_000),
        _r("dps", "forecast", "annual", date(2027, 4, 1), nx_end, 110, consolidated=0),
    ]
    row = statement_row(_filing("annual", "FY"), rows)
    assert row["type_of_document"] == "FYFinancialStatements_Consolidated_JP"
    assert row["fiscal_year_end"] == FY_END and row["fy_net_sales"] is None
    raw = json.loads(row["raw_json"])
    assert raw["NxtFYEn"] == "2028-03-31" and raw["NxFSales"] == "51000" and raw["NxFNp"] == "3100"
    assert raw["NxFSales2Q"] == "25000" and raw["NxFDivAnn"] == "110.0" and not raw.get("FSales")


def test_fy_row_without_next_forecast_still_has_next_year_end() -> None:
    rows = [
        _r("net_sales", "actual", "FY", FY_START, FY_END, 50_000),
        _r("dps", "forecast", "annual", date(2027, 4, 1), date(2028, 3, 31), 5, consolidated=0),
    ]
    raw = json.loads(statement_row(_filing("annual", "FY"), rows)["raw_json"])
    assert raw["NxtFYEn"] == "2028-03-31" and raw["NxFDivAnn"] == "5.0" and raw["NxFSales"] == ""
    bare = json.loads(statement_row(_filing("annual", "FY"), rows[:1])["raw_json"])
    assert bare["NxtFYSt"] == "2027-04-01" and bare["NxtFYEn"] == "2028-03-31"


def test_revision_with_dividend_is_fy_and_reit_is_left_to_jquants() -> None:
    rows = [
        _r("net_sales", "forecast", "H1", FY_START, H1_END, 24_000),
        _r("net_sales", "forecast", "FY", FY_START, FY_END, 48_000),
        _r("dps", "forecast", "annual", FY_START, FY_END, 30, consolidated=0),
    ]
    row = statement_row(_filing("forecast_revision", "Q2"), rows)
    assert row["type_of_period"] == "FY" and row["period_end"] == FY_END and row["f2_net_sales"] == 24_000
    assert statement_row(_filing("quarterly", "Q2", title="2026年8月期 決算短信（REIT）"), _q2_rows()) is None


def test_revision_row_period_follows_half_year() -> None:
    rows = [
        _r("net_sales", "forecast", "H1", FY_START, H1_END, 24_000),
        _r("net_sales", "forecast", "FY", FY_START, FY_END, 48_000),
        _r("operating_income", "forecast", "FY", FY_START, FY_END, 3_000),
    ]
    row = statement_row(_filing("forecast_revision", "Q2"), rows)
    assert row["type_of_document"] == "EarnForecastRevision" and row["is_revision"] is True
    assert row["type_of_period"] == "2Q" and row["period_end"] == H1_END and row["fiscal_year_end"] == FY_END
    assert row["fy_net_sales"] == 48_000 and row["f2_net_sales"] == 24_000 and row["net_sales"] is None
    only_fy = statement_row(_filing("forecast_revision", "FY"), rows[1:])
    assert only_fy["type_of_period"] == "FY" and only_fy["period_end"] == FY_END


def test_no_actuals_no_row() -> None:
    assert statement_row(_filing("quarterly", "Q2"), [_r("bps", "actual", "Q2", None, H1_END, 1.0)]) is None
    assert statement_row(_filing("other", None), _q2_rows()) is None


def _seed_filing(db, doc_id: str, rows) -> XbrlFiling:
    std = {s.code: s.id for s in db.query(XbrlStdItem).all()}
    f = XbrlFiling(code="72030", source="tdnet", doc_id=doc_id, doc_type="quarterly", period_type="Q2",
                   fiscal_year_end=H1_END, submitted_at=datetime(2026, 11, 5, 13, 55),  # noqa: DTZ001
                   title="2027年3月期 第2四半期決算短信〔IFRS〕（連結）", accounting_standard="IFRS",
                   is_amendment=False, raw_path="x.zip", parse_status="parsed", created_at=datetime(2026, 11, 5))  # noqa: DTZ001
    db.add(f)
    db.flush()
    for r in rows:
        db.add(XbrlFinancial(code="72030", filing_id=f.id, std_item_id=std[r.code], is_consolidated=bool(r.is_consolidated),
                             period_type="Q2", period_start=r.period_start, period_end=r.period_end, value=r.value,
                             unit="JPY", value_kind=r.value_kind, range_side="point", target_period=r.target_period,
                             generated_at=datetime(2026, 11, 5)))  # noqa: DTZ001
    db.commit()
    return f


def _jq_row(no: str, **values) -> dict:
    cols = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps",
            "f2_net_sales", "f2_operating_profit", "f2_ordinary_profit", "f2_profit", "f2_eps",
            "fy_net_sales", "fy_operating_profit", "fy_ordinary_profit", "fy_profit", "fy_eps")
    row = {c: None for c in cols}
    row.update(values)
    row.update(disclosure_no=no, code="72030", disclosed_date=date(2026, 11, 5), disclosed_time="13:55:00",
               type_of_period="2Q", type_of_document="2QFinancialStatements_Consolidated_IFRS",
               period_start=FY_START, period_end=H1_END, fiscal_year_start=FY_START, fiscal_year_end=FY_END,
               is_revision=False, raw_json=json.dumps({"DiscNo": no, "NxFSales": "", "BPS": "777"}))
    return row


def test_xbrl_first_then_jquants_fills_raw_and_blanks() -> None:
    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        _seed_filing(db, "081220261030561115", _q2_rows())
        stats = sync_statements(db, date(2026, 11, 1))
        assert stats["inserted"] == 1, stats
        s = db.get(Statement, "20261030561115")
        assert s.source == SOURCE_XBRL and s.net_sales == 26_962_462_000_000 and s.ordinary_profit is None
        assert json.loads(s.raw_json)["Source"] == SOURCE_XBRL

        upsert_statements(db, [_jq_row("20261030561115", net_sales=1.0, ordinary_profit=5.0)])
        db.expire_all()
        s = db.get(Statement, "20261030561115")
        assert s.source == SOURCE_XBRL and s.net_sales == 26_962_462_000_000, s.net_sales
        assert s.ordinary_profit == 5.0
        assert json.loads(s.raw_json)["BPS"] == "777"

        again = sync_statements(db, date(2026, 11, 1))
        assert again["updated"] == 1, again
        db.expire_all()
        s = db.get(Statement, "20261030561115")
        assert json.loads(s.raw_json)["BPS"] == "777" and s.ordinary_profit == 5.0

        upsert_statements(db, [_jq_row("20261030569999", net_sales=3.0)])
        other = db.get(Statement, "20261030569999")
        assert other.source == SOURCE_JQUANTS and other.net_sales == 3.0
    finally:
        db.close()


def test_xbrl_overwrites_jquants_numbers_but_keeps_raw() -> None:
    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        upsert_statements(db, [_jq_row("20261030561116", net_sales=1.0, ordinary_profit=5.0)])
        _seed_filing(db, "081220261030561116", _q2_rows())
        sync_statements(db, date(2026, 11, 1))
        db.expire_all()
        s = db.get(Statement, "20261030561116")
        assert s.source == SOURCE_XBRL and s.net_sales == 26_962_462_000_000 and s.ordinary_profit == 5.0
        assert json.loads(s.raw_json)["BPS"] == "777"
    finally:
        db.close()


def test_check_tool_compares_with_jquants(capsys) -> None:
    from app.db import engine
    from tools.xbrl.xbrl_statements_check import main as check_main

    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        _seed_filing(db, "081220261030561117", _q2_rows())
        jq = _jq_row("20261030561117", net_sales=26_962_000_000_000, operating_profit=2_191_124_000_000,
                     profit=2_236_446_000_000, eps=185.97, fy_net_sales=53_500_000_000_000,
                     fy_operating_profit=3_900_000_000_000, fy_eps=299.95, ordinary_profit=7.0)
        upsert_statements(db, [jq])
    finally:
        db.close()
    check_main(["--db", engine.url.database, "--since", "2026-11-01"])
    out = capsys.readouterr().out
    assert "paired=1" in out, out
    assert "net_sales ok=1 diff=0" in out and "fy_eps ok=1 diff=0" in out, out
    assert "ordinary_profit ok=0 diff=1" in out, out

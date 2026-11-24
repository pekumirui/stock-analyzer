"""KAB-383: 短信 XBRL から statements の行を作る。数値は XBRL、raw_json と空欄は J-Quants。"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

from app.db import SessionLocal, init_db
from app.market.ingest import upsert_statements
from app.models import Statement, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from app.xbrl.statements import (SOURCE_JQUANTS, SOURCE_XBRL, statement_row, sync_statements,
                                 withdrawn_forecasts)

FY_START = date(2026, 4, 1)
FY_END = date(2027, 3, 31)
H1_END = date(2026, 9, 30)


def _filing(doc_type: str, period_type: str, *, doc_id="081220261030561115", title="決算短信〔日本基準〕（連結）",
            standard="JPGAAP", submitted=datetime(2026, 11, 5, 13, 55)):
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


def test_individual_revision_goes_to_fnc_unless_company_is_standalone() -> None:
    rows = [
        _r("net_sales", "forecast", "FY", FY_START, FY_END, 260_000, consolidated=0),
        _r("net_income", "forecast", "FY", FY_START, FY_END, 14_000, consolidated=0),
    ]
    consolidated_co = statement_row(_filing("forecast_revision", "FY"), rows)
    assert consolidated_co["fy_net_sales"] is None and consolidated_co["fy_profit"] is None
    raw = json.loads(consolidated_co["raw_json"])
    assert raw["FNCSales"] == "260000" and raw["FNCNP"] == "14000" and "FSales" not in raw
    single = statement_row(_filing("forecast_revision", "FY"), rows, standalone_company=True)
    assert single["fy_net_sales"] == 260_000 and json.loads(single["raw_json"])["FSales"] == "260000"


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


def _seed_filing(db, doc_id: str, rows, doc_type: str = "quarterly",
                 title: str = "2027年3月期 第2四半期決算短信〔IFRS〕（連結）") -> XbrlFiling:
    std = {s.code: s.id for s in db.query(XbrlStdItem).all()}
    f = XbrlFiling(code="72030", source="tdnet", doc_id=doc_id, doc_type=doc_type, period_type="Q2",
                   fiscal_year_end=H1_END, submitted_at=datetime(2026, 11, 5, 13, 55),
                   title=title, accounting_standard="IFRS",
                   is_amendment=False, raw_path="x.zip", parse_status="parsed", created_at=datetime(2026, 11, 5))
    db.add(f)
    db.flush()
    for r in rows:
        db.add(XbrlFinancial(code="72030", filing_id=f.id, std_item_id=std[r.code], is_consolidated=bool(r.is_consolidated),
                             period_type="Q2", period_start=r.period_start, period_end=r.period_end, value=r.value,
                             unit="JPY", value_kind=r.value_kind, range_side=getattr(r, "range_side", "point"),
                             target_period=r.target_period,
                             generated_at=datetime(2026, 11, 5)))
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


def test_standalone_company_from_latest_financial_statement() -> None:
    from app.xbrl.statements import is_standalone_company

    init_db()
    db = SessionLocal()
    try:
        row = _jq_row("20260801000001")
        row["type_of_document"] = "1QFinancialStatements_NonConsolidated_JP"
        row["disclosed_date"] = date(2026, 8, 1)
        upsert_statements(db, [row])
        assert is_standalone_company(db, "72030", date(2026, 8, 2))
        assert not is_standalone_company(db, "72030", date(2026, 7, 31))
        assert not is_standalone_company(db, "99990", date(2026, 8, 2))
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


PL_CODES = ("net_sales", "operating_income", "ordinary_income", "net_income", "eps_basic")
EVERY = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")


def _fc(code, kind, value=1.0, target="FY", consolidated=1, side="point"):
    r = _r(code, kind, target, FY_START, FY_END, value, consolidated)
    r.range_side = side
    return r


def test_withdrawal_notice_marks_items_with_only_previous_forecast() -> None:
    marks = [_fc(c, "forecast_previous") for c in PL_CODES]
    marks += [_fc("dps", "forecast_previous", 25, "annual", 0), _fc("dps", "forecast_previous", 25, "year_end", 0)]
    points = [_fc("dps", "forecast", 0, "Q2", 0)]
    assert withdrawn_forecasts(points, marks, standalone_company=False) == (*EVERY, "div_annual")


def test_dividend_only_revision_marks_nothing() -> None:
    marks = [_fc("dps", kind, 73, "annual", 0) for kind in ("forecast_previous", "forecast")]
    assert withdrawn_forecasts(marks, marks, standalone_company=False) == ()


def test_point_to_range_is_not_a_withdrawal() -> None:
    marks = [_fc(c, kind, side=side) for c in PL_CODES for kind in ("forecast_previous", "forecast")
             for side in ("lower", "upper")]
    assert withdrawn_forecasts([], marks, standalone_company=False) == ()
    half = [m for m in marks if not (m.value_kind == "forecast" and m.code == "net_income")]
    assert withdrawn_forecasts([], half, standalone_company=False) == ("profit",)


def test_move_to_consolidated_withdraws_the_standalone_forecast() -> None:
    marks = [_fc(c, "forecast_previous", consolidated=0) for c in PL_CODES if c != "operating_income"]
    assert withdrawn_forecasts([], marks, standalone_company=True) == (
        "net_sales", "ordinary_profit", "profit", "eps")


def test_consolidated_company_dropping_only_individual_forecast_is_not_marked() -> None:
    marks = [_fc(c, "forecast_previous", consolidated=0) for c in PL_CODES]
    assert withdrawn_forecasts([], marks, standalone_company=False) == ()
    both = marks + [_fc(c, kind) for c in PL_CODES for kind in ("forecast_previous", "forecast")]
    points = [_fc(c, "forecast") for c in PL_CODES]
    assert withdrawn_forecasts(points, both, standalone_company=False) == ()


def _jq_revision(no: str) -> dict:
    row = _jq_row(no)
    row.update(type_of_period="FY", type_of_document="EarnForecastRevision", is_revision=True)
    return row


def test_notice_marks_the_jquants_row_and_jquants_keeps_it() -> None:
    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        upsert_statements(db, [_jq_revision("20261105000001")])
        marks = [_fc(c, "forecast_previous") for c in PL_CODES]
        _seed_filing(db, "091220261105000001", marks, doc_type="other", title="連結業績予想の取り下げに関するお知らせ")
        _seed_filing(db, "091220261105000002", marks, doc_type="other", title="連結業績予想の取り下げに関するお知らせ")
        upsert_statements(db, [_jq_revision("20261105000005")])
        _seed_filing(db, "091220261105000005", marks, doc_type="forecast_revision",
                     title="通期連結業績予想の修正（未定）に関するお知らせ")
        stats = sync_statements(db, date(2026, 11, 1))
        assert stats["marked"] == 2 and stats["inserted"] == 0, stats
        db.expire_all()
        assert db.get(Statement, "20261105000001").withdrawn_forecasts == EVERY
        assert db.get(Statement, "20261105000005").withdrawn_forecasts == EVERY
        assert db.get(Statement, "20261105000002") is None
        upsert_statements(db, [_jq_revision("20261105000001")])
        db.expire_all()
        s = db.get(Statement, "20261105000001")
        assert s.withdrawn_forecasts == EVERY and s.source == SOURCE_JQUANTS
    finally:
        db.close()


def test_revision_row_carries_marks_whichever_arrives_first() -> None:
    init_db()
    db = SessionLocal()
    try:
        apply_mappings(db)
        rows = [_fc("net_sales", "forecast_previous", 100), _fc("net_sales", "forecast", 90),
                _fc("net_income", "forecast_previous", 10)]
        upsert_statements(db, [_jq_revision("20261105000003")])
        _seed_filing(db, "091220261105000003", rows, doc_type="forecast_revision", title="業績予想の修正に関するお知らせ")
        _seed_filing(db, "091220261105000004", rows, doc_type="forecast_revision", title="業績予想の修正に関するお知らせ")
        sync_statements(db, date(2026, 11, 1))
        db.expire_all()
        assert db.get(Statement, "20261105000004").source == SOURCE_XBRL
        upsert_statements(db, [_jq_revision("20261105000003"), _jq_revision("20261105000004")])
        db.expire_all()
        for no in ("20261105000003", "20261105000004"):
            s = db.get(Statement, no)
            assert s.withdrawn_forecasts == ("profit",) and s.fy_net_sales == 90, (no, s.withdrawn_forecasts)
        sync_statements(db, date(2026, 11, 1))
        db.expire_all()
        assert db.get(Statement, "20261105000004").withdrawn_forecasts == ("profit",)
    finally:
        db.close()


def test_unknown_item_is_rejected_on_write() -> None:
    import pytest
    from sqlalchemy.exc import StatementError

    init_db()
    db = SessionLocal()
    try:
        row = _jq_revision("20261105000009")
        upsert_statements(db, [row])
        db.get(Statement, "20261105000009").withdrawn_forecasts = ("eps", "dividend")
        with pytest.raises(StatementError, match="dividend"):
            db.commit()
        db.rollback()
    finally:
        db.close()

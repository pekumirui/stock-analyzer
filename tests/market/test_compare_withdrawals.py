from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base, DailyPrice, Statement, XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.mappings import apply_mappings
from tools.market.compare_withdrawals import compare
from tools.market.compare_quarter_flash import readonly_session

FY26, FY27 = date(2026, 3, 31), date(2027, 3, 31)
PL_CODES = ("net_sales", "operating_income", "ordinary_income", "net_income", "eps_basic")


def _statement(no: str, code: str, day: date, doc: str, period: str, fy_end: date, **raw) -> Statement:
    return Statement(disclosure_no=no, code=code, disclosed_date=day, disclosed_time="15:30:00", type_of_period=period,
                     type_of_document=doc, fiscal_year_end=fy_end, is_revision="Revision" in doc,
                     raw_json=json.dumps({k: str(v) for k, v in raw.items()}))


def _notice(db: Session, code: str, no: str, title: str) -> None:
    std = {s.code: s.id for s in db.query(XbrlStdItem).all()}
    f = XbrlFiling(code=code, source="tdnet", doc_id="0912" + no, doc_type="other", period_type=None,
                   fiscal_year_end=FY27, submitted_at=datetime(2026, 8, 14, 15, 30), title=title,
                   accounting_standard="JPGAAP", is_amendment=False, raw_path="x.zip", parse_status="parsed",
                   created_at=datetime(2026, 8, 14))
    db.add(f)
    db.flush()
    for c, target in (*((c, "FY") for c in PL_CODES), ("dps", "annual"), ("dps", "year_end")):
        db.add(XbrlFinancial(code=code, filing_id=f.id, std_item_id=std[c], is_consolidated=c != "dps",
                             period_type="FY", period_start=date(2026, 4, 1), period_end=FY27, value=1, unit="JPY",
                             value_kind="forecast_previous", range_side="point", target_period=target,
                             generated_at=datetime(2026, 8, 14)))


def _seed(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        apply_mappings(db)
        for code in ("70610", "99990"):
            db.add(DailyPrice(code=code, date=date(2026, 8, 17), close=1000.0, adj_factor=1.0))
            db.add(_statement(f"{code}0512", code, date(2026, 5, 12), "FYFinancialStatements_Consolidated_JP", "FY",
                              FY26, NxFEPS=71.2, NxFDivAnn=25))
        db.add(_statement("20260806513051", "70610", date(2026, 8, 14), "EarnForecastRevision", "FY", FY27))
        xbrl_row = _statement("20261013550006", "99990", date(2026, 10, 16), "EarnForecastRevision", "FY", FY27)
        xbrl_row.fy_eps, xbrl_row.source = 41.43, "xbrl"
        db.add(xbrl_row)
        _notice(db, "70610", "20260806513051", "連結業績予想の取り下げに関するお知らせ")
        db.commit()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE statements DROP COLUMN withdrawn_forecasts"))
    engine.dispose()


def test_compare_marks_from_xbrl_when_the_column_is_missing(tmp_path: Path, capsys) -> None:
    path = tmp_path / "prod.db"
    _seed(path)
    db = readonly_session(path)
    try:
        assert compare(db, show=True) == 0
    finally:
        db.close()
    out = capsys.readouterr().out
    assert "marks_source=xbrl_financials column=no marked_disclosures=1" in out, out
    assert "changed code=70610 eps_steps=2 dividend=25.0->None marked=1" in out, out
    assert ("marked code=70610 disclosure_no=20260806513051 disclosed=2026-08-14 "
            "items=net_sales,operating_profit,ordinary_profit,profit,eps,div_annual") in out, out
    assert "codes=2 changed_codes=1 eps_steps=2 dividend_codes=1" in out, out
    assert "changed_codes_within_marked_codes=True changed=1 marked=1 outside=0" in out, out
    assert "columns_without_raw code=99990 disclosure_no=20261013550006" in out, out
    assert "changed_marked=1 changed_columns_without_raw=0 columns_without_raw_codes=1 changed_other=0" in out, out

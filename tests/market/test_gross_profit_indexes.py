"""決算一覧の売上総利益 SQL が xbrl_financials の covering index だけで済むこと（KAB-295）。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import inspect, text

from app.db import SessionLocal, engine, ensure_indexes
from app.models import XbrlFiling, XbrlFinancial, XbrlStdItem
from app.xbrl.gross_profit import gross_profit_query

COVER = "idx_xbrl_financials_gp_cover"
FILINGS_COVER = "idx_xbrl_filings_id_cover"
OLD_LOOKUP = "idx_xbrl_financials_lookup"
COVER_COLS = [
    "code", "std_item_id", "value_kind", "is_consolidated",
    "period_start", "period_end", "value", "filing_id",
]
FILINGS_COVER_COLS = ["id", "parse_status", "source", "doc_id", "submitted_at"]


def _codes() -> list[str]:
    return [f"{i:04d}0" for i in range(40)]


def _seed() -> None:
    db = SessionLocal()
    try:
        if db.query(XbrlStdItem).filter_by(code="gross_profit").count():
            return
        gp = XbrlStdItem(code="gross_profit", statement="PL", label_ja="売上総利益")
        sales = XbrlStdItem(code="net_sales", statement="PL", label_ja="売上高")
        db.add_all([gp, sales])
        db.flush()
        ts0 = datetime.combine(date(2024, 1, 1), time(15))
        for i, code in enumerate(_codes()):
            for j in range(8):
                at = ts0 + timedelta(days=i * 8 + j)
                f = XbrlFiling(
                    code=code, source="tdnet", doc_id=f"{code}-{j:04d}",
                    doc_type="quarterly", period_type="Q2", submitted_at=at,
                    raw_path=f"{code}-{j}", parse_status="parsed", created_at=at,
                )
                db.add(f)
                db.flush()
                start = date(2024, 4, 1)
                end = date(2024, 6, 30) + timedelta(days=j)
                for kind in ("actual", "forecast"):
                    db.add(XbrlFinancial(
                        code=code, filing_id=f.id, std_item_id=gp.id,
                        is_consolidated=True, period_type="Q2",
                        period_start=start, period_end=end, value=1000 + i + j,
                        value_kind=kind, generated_at=at,
                    ))
                db.add(XbrlFinancial(
                    code=code, filing_id=f.id, std_item_id=sales.id,
                    is_consolidated=True, period_type="Q2",
                    period_start=start, period_end=end, value=9000 + i,
                    value_kind="actual", generated_at=at,
                ))
        db.execute(text("ANALYZE"))
        db.commit()
    finally:
        db.close()


def _explain(db, stmt) -> str:
    compiled = stmt.compile(dialect=engine.dialect, compile_kwargs={"render_postcompile": True})
    sql = "EXPLAIN QUERY PLAN " + str(compiled)
    pos = compiled.positiontup or tuple(compiled.params)
    args = tuple(compiled.params[k] for k in pos)
    rows = db.connection().exec_driver_sql(sql, args).fetchall()
    return "\n".join(r[-1] for r in rows)


def test_cover_index_columns_match_query() -> None:
    fin = next(i for i in XbrlFinancial.__table__.indexes if i.name == COVER)
    assert [c.name for c in fin.columns] == COVER_COLS
    filings = next(i for i in XbrlFiling.__table__.indexes if i.name == FILINGS_COVER)
    assert [c.name for c in filings.columns] == FILINGS_COVER_COLS
    names = {ix["name"] for ix in inspect(engine).get_indexes("xbrl_financials")}
    assert COVER in names
    assert OLD_LOOKUP not in names
    filing_names = {ix["name"] for ix in inspect(engine).get_indexes("xbrl_filings")}
    assert FILINGS_COVER in filing_names


def test_ensure_indexes_replaces_lookup() -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {COVER}"))
        conn.execute(text(f"DROP INDEX IF EXISTS {FILINGS_COVER}"))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {OLD_LOOKUP} ON xbrl_financials "
            "(code, std_item_id, is_consolidated, period_end, value_kind)"
        ))
    ensure_indexes()
    ensure_indexes()
    names = {ix["name"] for ix in inspect(engine).get_indexes("xbrl_financials")}
    assert COVER in names
    assert OLD_LOOKUP not in names
    filing_names = {ix["name"] for ix in inspect(engine).get_indexes("xbrl_filings")}
    assert FILINGS_COVER in filing_names


def test_explain_query_plan_uses_covering_index() -> None:
    _seed()
    db = SessionLocal()
    try:
        plan = _explain(db, gross_profit_query(db, _codes()[:10]).statement)
    finally:
        db.close()

    fin_lines = [ln for ln in plan.splitlines() if "xbrl_financials" in ln]
    assert fin_lines, plan
    assert all("COVERING INDEX" in ln and COVER in ln for ln in fin_lines), plan
    assert "USING INDEX idx_xbrl_financials_lookup" not in plan, plan
    assert "SCAN xbrl_financials" not in plan, plan
    filing_lines = [ln for ln in plan.splitlines() if "xbrl_filings" in ln]
    assert filing_lines, plan
    assert all("COVERING INDEX" in ln and FILINGS_COVER in ln for ln in filing_lines), plan


def test_ensure_indexes_analyzes_so_covering_is_chosen() -> None:
    """統計が無いと parse_status 経由になり covering を使わない。"""
    _seed()
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {COVER}"))
        conn.execute(text(f"DROP INDEX IF EXISTS {FILINGS_COVER}"))
        conn.execute(text("DROP TABLE IF EXISTS sqlite_stat1"))
        conn.execute(text("DROP TABLE IF EXISTS sqlite_stat4"))
        conn.execute(text(
            f"CREATE INDEX IF NOT EXISTS {OLD_LOOKUP} ON xbrl_financials "
            "(code, std_item_id, is_consolidated, period_end, value_kind)"
        ))
    ensure_indexes()
    db = SessionLocal()
    try:
        plan = _explain(db, gross_profit_query(db, _codes()[:10]).statement)
        stat = db.execute(
            text("SELECT idx FROM sqlite_stat1 WHERE tbl='xbrl_financials' AND idx=:i"),
            {"i": COVER},
        ).scalar()
    finally:
        db.close()
    assert stat == COVER, plan
    assert COVER in plan and "COVERING INDEX" in plan, plan
    assert FILINGS_COVER in plan, plan

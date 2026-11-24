"""決算カレンダー・一覧の SQL が索引だけで済むこと（KAB-292）。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import aliased

from app.api.earnings import (
    _ACTUAL_DOC,
    _LIST_HISTORY_COLUMNS,
    _not_duplicate,
    _not_excluded_product,
)
from app.config import EARNINGS_PARAMS as EP
from app.db import SessionLocal, engine, ensure_indexes, upsert
from app.models import EarningsDatePub, Statement, Stock

DAY = date(2026, 10, 30)
MONTH_FIRST = date(2026, 10, 1)
MONTH_AFTER = date(2026, 11, 1)
COVER = "ix_statements_earnings_cover"
PUBS = "ix_earnings_date_pubs_sch_date"


def _stmt_dates() -> list[date]:
    return [
        date(2024, 2, 14), date(2024, 5, 15), date(2024, 8, 14), date(2024, 11, 14),
        date(2025, 2, 14), date(2025, 5, 15), date(2025, 8, 14), date(2025, 11, 14),
        date(2026, 2, 13), date(2026, 5, 15), date(2026, 8, 14), date(2026, 10, 15),
    ]


def _seed() -> None:
    n_codes = 200
    stocks, pubs, stmts = [], [], []
    dates = _stmt_dates()
    for i in range(n_codes):
        code = f"{i:04d}0"
        stocks.append({"code": code, "name": f"N{i}", "product_category": "011"})
        for q, fq in enumerate(("1Q", "2Q", "3Q", "FY")):
            pubs.append({
                "code": code, "fq_name": fq,
                "pub_date": date(2026, 9, 1) + timedelta(days=q),
                "sch_date": date(2026, 10, 1) + timedelta(days=(i + q) % 28),
                "fye": "0331", "co_name": f"N{i}",
            })
        for j, d in enumerate(dates):
            stmts.append({
                "disclosure_no": f"{code}-{j}", "code": code, "disclosed_date": d,
                "disclosed_time": "15:00:00",
                "type_of_period": ("1Q", "2Q", "3Q", "FY")[j % 4],
                "type_of_document": "FYFinancialStatements_Consolidated_JP",
                "period_start": d - timedelta(days=90), "period_end": d,
                "fiscal_year_start": date(d.year - 1, 4, 1),
                "fiscal_year_end": date(d.year, 3, 31),
                "net_sales": 1000 + j, "operating_profit": 100 + j,
                "ordinary_profit": 90 + j, "profit": 80 + j, "eps": 10 + j,
                "is_revision": False, "raw_json": "{" + ("x" * 1800) + "}",
            })
        if i % 2 == 0:
            stmts.append({
                "disclosure_no": f"{code}-today", "code": code, "disclosed_date": DAY,
                "disclosed_time": "15:30:00", "type_of_period": "2Q",
                "type_of_document": "2QFinancialStatements_Consolidated_JP",
                "period_start": date(2026, 4, 1), "period_end": date(2026, 9, 30),
                "fiscal_year_start": date(2026, 4, 1), "fiscal_year_end": date(2027, 3, 31),
                "net_sales": 2000, "operating_profit": 200, "ordinary_profit": 180,
                "profit": 150, "eps": 20, "is_revision": False,
                "raw_json": "{" + ("y" * 1800) + "}",
            })
    db = SessionLocal()
    try:
        upsert(db, Stock, stocks)
        upsert(db, EarningsDatePub, pubs)
        upsert(db, Statement, stmts)
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


def _month_stmt(db):
    """app.api.earnings.actual_month_facts の件数クエリと同じ。"""
    a = aliased(Statement)
    return (db.query(a.disclosed_date, func.count(func.distinct(a.code)))
            .filter(a.disclosed_date >= MONTH_FIRST, a.disclosed_date < MONTH_AFTER,
                    a.type_of_document.contains("FinancialStatements"),
                    _not_duplicate(a),
                    _not_excluded_product(a.code))
            .group_by(a.disclosed_date))


def _codes_stmt():
    """earnings_rows が履歴を取る銘柄のサブクエリ。"""
    return select(Statement.code).where(
        Statement.disclosed_date == DAY, _ACTUAL_DOC, _not_excluded_product(Statement.code))


def _prefetch_stmt():
    """app.api.earnings.prefetch_list_history と同じ SELECT（IN は当日銘柄のサブクエリ）。"""
    since = DAY - timedelta(days=EP["list_history_days"])
    return (select(*_LIST_HISTORY_COLUMNS)
            .where(Statement.code.in_(_codes_stmt()),
                   Statement.disclosed_date <= DAY,
                   Statement.disclosed_date >= since)
            .order_by(Statement.code, Statement.disclosed_date, Statement.disclosure_no))


def _pubs_stmt(db):
    """app.market.earnings_schedule.load_current の pubs 取得と同じ。"""
    newer = aliased(EarningsDatePub)
    latest = ~(select(1).where(
        newer.code == EarningsDatePub.code,
        newer.fq_name == EarningsDatePub.fq_name,
        newer.pub_date > EarningsDatePub.pub_date,
    ).exists())
    return db.query(EarningsDatePub).filter(
        EarningsDatePub.sch_date >= MONTH_FIRST,
        EarningsDatePub.sch_date <= date(2026, 10, 31),
        latest,
    )


def test_cover_index_columns_match_queries() -> None:
    idx = next(i for i in Statement.__table__.indexes if i.name == COVER)
    got = [c.name for c in idx.columns]
    want = {c.name for c in Statement.__table__.columns if c.name not in ("raw_json", "source", "withdrawn_forecasts")}
    assert set(got) == want
    assert got[:3] == ["code", "disclosed_date", "disclosure_no"]
    assert "raw_json" not in got
    names = {ix["name"] for ix in inspect(engine).get_indexes("statements")}
    assert COVER in names
    pub_names = {ix["name"] for ix in inspect(engine).get_indexes("earnings_date_pubs")}
    assert PUBS in pub_names


def test_ensure_indexes_recreates_earnings_indexes() -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DROP INDEX IF EXISTS {COVER}"))
        conn.execute(text(f"DROP INDEX IF EXISTS {PUBS}"))
    ensure_indexes()
    ensure_indexes()
    names = {ix["name"] for ix in inspect(engine).get_indexes("statements")}
    assert COVER in names
    pub_names = {ix["name"] for ix in inspect(engine).get_indexes("earnings_date_pubs")}
    assert PUBS in pub_names


def test_explain_query_plan_avoids_table_body() -> None:
    _seed()
    db = SessionLocal()
    try:
        month = _explain(db, _month_stmt(db).statement)
        codes = _explain(db, _codes_stmt())
        prefetch = _explain(db, _prefetch_stmt())
        pubs = _explain(db, _pubs_stmt(db).statement)
    finally:
        db.close()

    assert "SCAN earnings_date_pubs" not in pubs, pubs
    assert PUBS in pubs, pubs

    assert COVER in month, month
    assert "USING INDEX ix_statements_code" not in month, month
    exists_lines = [ln for ln in month.splitlines() if "statements_2" in ln]
    assert exists_lines, month
    assert all("COVERING INDEX" in ln and COVER in ln for ln in exists_lines), month

    # 履歴本体は covering。IN サブクエリ（その日の銘柄）だけ disclosed_date 索引
    assert prefetch.splitlines()[0].startswith(
        f"SEARCH statements USING COVERING INDEX {COVER}"), prefetch
    assert "USING INDEX ix_statements_code" not in prefetch, prefetch

    # 当日銘柄の抽出は disclosed_date。本体を読むのはこの 1 日分だけ
    assert "ix_statements_disclosed_date" in codes or COVER in codes, codes

"""KAB-144: 正規化は科目・要素カタログをジョブで 1 回だけ読む。結果は変えない。"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-catalog-')}/stock.db",
)

from sqlalchemy import event  # noqa: E402

from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.models import (  # noqa: E402
    XbrlContext,
    XbrlElement,
    XbrlElementMapping,
    XbrlFact,
    XbrlFiling,
    XbrlFinancial,
    XbrlJobRun,
    XbrlSegment,
    XbrlStdItem,
)
from app.xbrl.jobs import run_normalize  # noqa: E402
from app.xbrl.normalize import load_normalize_catalog, normalize_filing, rebuild_all  # noqa: E402

N_FILINGS = 8
N_DUMMY_ELEMENTS = 120
TS = datetime(2026, 10, 23, 12, 0, 0)  # noqa: DTZ001
SALES = Decimal("100000000")


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _norm_sql(statement: str) -> str:
    return " ".join(statement.lower().split())


def _is_full_table_select(statement: str, table: str) -> bool:
    s = _norm_sql(statement)
    if not s.startswith("select "):
        return False
    if f"from {table}" not in s:
        return False
    return " where " not in s


@contextmanager
def _capture_sql():
    statements: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


def _wipe(db) -> None:
    for model in (
        XbrlFinancial, XbrlSegment, XbrlFact, XbrlContext, XbrlFiling,
        XbrlElementMapping, XbrlElement, XbrlStdItem, XbrlJobRun,
    ):
        db.query(model).delete()
    db.commit()


def _seed(db, n_filings: int = N_FILINGS) -> list[XbrlFiling]:
    _wipe(db)
    std = XbrlStdItem(
        code="net_sales", statement="PL", label_ja="売上高",
        display_order=10, sign="natural", is_required=False,
    )
    db.add(std)
    db.flush()
    db.add(XbrlElementMapping(
        std_item_id=std.id, namespace_key="jppfs_cor", local_name="NetSales",
        priority=10, accounting_standard=None,
    ))
    el = XbrlElement(
        namespace_uri="http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2024-11-01/jppfs_cor",
        namespace_key="jppfs_cor", local_name="NetSales",
        item_type="monetary", is_custom=False, first_seen_at=TS,
    )
    db.add(el)
    for i in range(N_DUMMY_ELEMENTS):
        db.add(XbrlElement(
            namespace_uri=f"http://example.invalid/dummy/{i}",
            namespace_key="dummy", local_name=f"Dummy{i}",
            item_type="monetary", is_custom=True, first_seen_at=TS,
        ))
    db.flush()
    filings: list[XbrlFiling] = []
    for i in range(n_filings):
        filing = XbrlFiling(
            code="72030", source="tdnet", doc_id=f"doc{i}",
            doc_type="earnings", period_type="FY",
            submitted_at=TS, title="決算短信", accounting_standard="JPGAAP",
            raw_path=f"7203/doc{i}.zip", parse_status="parsed", parsed_at=TS,
            created_at=TS,
        )
        db.add(filing)
        db.flush()
        ctx = XbrlContext(
            filing_id=filing.id, context_ref="CurrentYearDuration",
            period_type="duration", start_date=date(2025, 4, 1),
            end_date=date(2026, 3, 31), is_consolidated=1,
            relative_period="current", dimensions_json="{}", has_dimensions=False,
        )
        db.add(ctx)
        db.flush()
        db.add(XbrlFact(
            filing_id=filing.id, element_id=el.id, context_id=ctx.id,
            unit="JPY", value_num=SALES, is_nil=False,
        ))
        filings.append(filing)
    db.commit()
    return filings


def _sales_by_filing(db) -> dict[int, Decimal]:
    rows = (
        db.query(XbrlFinancial.filing_id, XbrlFinancial.value)
        .join(XbrlStdItem, XbrlStdItem.id == XbrlFinancial.std_item_id)
        .filter(XbrlStdItem.code == "net_sales")
        .all()
    )
    return {fid: val for fid, val in rows}


def test_catalog_same_result_as_per_filing_load() -> None:
    init_db()
    db = SessionLocal()
    try:
        f1, f2 = _seed(db, n_filings=2)
        catalog = load_normalize_catalog(db)
        s1 = normalize_filing(db, f1, catalog=catalog)
        s2 = normalize_filing(db, f2)
        db.commit()
        if s1["upserted"] != 1 or s2["upserted"] != 1:
            _fail(f"upserted {s1} {s2}")
        if s1["missing_required"] != s2["missing_required"]:
            _fail(f"missing {s1} {s2}")
        got = _sales_by_filing(db)
        if got.get(f1.id) != SALES or got.get(f2.id) != SALES:
            _fail(got)
    finally:
        db.close()


def test_rebuild_full_table_element_selects_once() -> None:
    init_db()
    db = SessionLocal()
    try:
        filings = _seed(db)
        with _capture_sql() as statements:
            stats = rebuild_all(db)
        if stats["processed"] != N_FILINGS or stats["errors"] != 0:
            _fail(stats)
        if stats["financials"] != N_FILINGS:
            _fail(stats)
        scans = [s for s in statements if _is_full_table_select(s, "xbrl_elements")]
        if len(scans) != 1:
            _fail(f"xbrl_elements full scans {len(scans)} expected 1: {scans}")
        std_scans = [s for s in statements if _is_full_table_select(s, "xbrl_std_items")]
        map_scans = [s for s in statements if _is_full_table_select(s, "xbrl_element_mappings")]
        if len(std_scans) != 1:
            _fail(f"std_items full scans {len(std_scans)}: {std_scans}")
        if len(map_scans) != 1:
            _fail(f"mappings full scans {len(map_scans)}: {map_scans}")
        got = _sales_by_filing(db)
        if len(got) != N_FILINGS or set(got.values()) != {SALES}:
            _fail(got)
        if len(got) != len(filings):
            _fail("filing count")
    finally:
        db.close()


def test_run_normalize_full_table_element_selects_once() -> None:
    init_db()
    db = SessionLocal()
    try:
        _seed(db)
        with _capture_sql() as statements:
            stats = run_normalize(db)
        if stats.get("processed") != N_FILINGS or stats.get("errors") != 0:
            _fail(stats)
        scans = [s for s in statements if _is_full_table_select(s, "xbrl_elements")]
        if len(scans) != 1:
            _fail(f"xbrl_elements full scans {len(scans)} expected 1: {scans}")
        got = _sales_by_filing(db)
        if len(got) != N_FILINGS or set(got.values()) != {SALES}:
            _fail(got)
        second = run_normalize(db)
        if second.get("processed") != 0:
            _fail(f"second should skip: {second}")
    finally:
        db.close()


def test_normalize_filing_without_catalog_does_not_scan_all_elements() -> None:
    init_db()
    db = SessionLocal()
    try:
        filings = _seed(db)
        with _capture_sql() as statements:
            for filing in filings:
                normalize_filing(db, filing)
                db.commit()
        scans = [s for s in statements if _is_full_table_select(s, "xbrl_elements")]
        if scans:
            _fail(f"unexpected full xbrl_elements scans: {scans}")
        in_queries = [
            s for s in statements
            if "from xbrl_elements" in _norm_sql(s) and " where " in _norm_sql(s)
        ]
        if len(in_queries) != N_FILINGS:
            _fail(f"per-filing element lookups {len(in_queries)} expected {N_FILINGS}")
        got = _sales_by_filing(db)
        if len(got) != N_FILINGS or set(got.values()) != {SALES}:
            _fail(got)
    finally:
        db.close()


def main() -> None:
    test_catalog_same_result_as_per_filing_load()
    test_rebuild_full_table_element_selects_once()
    test_run_normalize_full_table_element_selects_once()
    test_normalize_filing_without_catalog_does_not_scan_all_elements()
    print("ok: xbrl normalize catalog")


if __name__ == "__main__":
    main()

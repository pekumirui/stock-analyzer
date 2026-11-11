"""短信 PDF の行と XBRL の要素の対応（xbrl_pdf_matches）と、出どころをまたぐビュー（docs/DESIGN_xbrl.md §18）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import SessionLocal, engine, ensure_views, init_db
from app.xbrl import three_statements as fst
from app.xbrl.pdf_matches import rebuild_matches, summary, target_codes, unmatched_names
from app.xbrl.statement_values import VIEW_NAME
from app.xbrl.store import delete_filing_payload

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.xbrl.test_three_statements import CODE, LOOSE, NAMED, SINGLE, M, seed  # noqa: E402

EXTRA = "11110"


def _run(sql: str, params: dict | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})


def _rows(sql: str, params: dict | None = None) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params or {})]


def _filing(fid: int, source: str, period_type: str, end: str, submitted: str, *, amendment: int = 0) -> None:
    kind = "annual" if period_type == "FY" else "quarterly"
    _run(
        "INSERT INTO xbrl_filings (id, code, source, doc_id, doc_type, period_type, fiscal_year_end, submitted_at, "
        "accounting_standard, is_amendment, raw_path, parse_status, created_at) VALUES (:id, :code, :src, :doc, "
        ":kind, :pt, :end, :sub, 'JPGAAP', :am, 'x', 'parsed', '2026-01-01')",
        {"id": fid, "code": EXTRA, "src": source, "doc": f"E{fid}", "kind": kind, "pt": period_type, "end": end,
         "sub": submitted, "am": amendment},
    )


def _element(eid: int, ns: str, local: str, *, custom: int, uri: str | None = None) -> None:
    _run(
        "INSERT INTO xbrl_elements (id, namespace_uri, namespace_key, local_name, label_ja, is_custom, first_seen_at) "
        "VALUES (:id, :uri, :ns, :local, :local, :custom, '2026-01-01')",
        {"id": eid, "uri": uri or f"uri:{ns}:{eid}", "ns": ns, "local": local, "custom": custom},
    )


def _fact(fact_id: int, fid: int, eid: int, start: str, end: str, value: float) -> None:
    ctx = fact_id
    _run(
        "INSERT INTO xbrl_contexts (id, filing_id, context_ref, period_type, start_date, end_date, is_consolidated, "
        "relative_period, dimensions_json, has_dimensions) VALUES (:id, :fid, :ref, 'duration', :start, :end, 1, "
        "'current', '{}', 0)",
        {"id": ctx, "fid": fid, "ref": f"c{ctx}", "start": start, "end": end},
    )
    _run(
        "INSERT INTO xbrl_facts (id, filing_id, element_id, context_id, unit, value_num, is_nil) "
        "VALUES (:id, :fid, :el, :ctx, 'JPY', :v, 0)",
        {"id": fact_id, "fid": fid, "el": eid, "ctx": ctx, "v": value * M},
    )


def seed_extra() -> None:
    _run("INSERT INTO stocks (code, name) VALUES (:c, 'ビュー')", {"c": EXTRA})
    _element(9001, "jppfs_cor", "NetSales", custom=0, uri="uri:jppfs_cor:2024")
    _element(9002, "jpcrp030000-asr_E99999-000", "SpecialItem", custom=1)
    _element(9003, f"tse-acedjpfr-{EXTRA}", "SpecialItem", custom=1)
    _element(9004, f"tse-acedjpfr-{EXTRA}", "OnlyTdnet", custom=1)
    _filing(901, "edinet", "FY", "2025-03-31", "2025-06-20 15:00:00")
    _fact(90101, 901, 9001, "2024-04-01", "2025-03-31", 500)
    _fact(90102, 901, 9002, "2024-04-01", "2025-03-31", 7)
    _filing(902, "edinet", "FY", "2025-03-31", "2025-07-07 15:00:00", amendment=1)
    _fact(90201, 902, 9001, "2024-04-01", "2025-03-31", 510)
    _filing(903, "tdnet", "FY", "2025-03-31", "2025-05-10 15:00:00")
    _fact(90301, 903, 9001, "2024-04-01", "2025-03-31", 499)
    _fact(90302, 903, 9003, "2024-04-01", "2025-03-31", 8)
    _fact(90303, 903, 9004, "2024-04-01", "2025-03-31", 3)
    _filing(904, "tdnet", "Q2", "2025-09-30", "2025-11-10 15:00:00")
    _fact(90401, 904, 9001, "2025-07-01", "2025-09-30", 130)
    _fact(90402, 904, 9001, "2025-04-01", "2025-09-30", 250)


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed()
    seed_extra()
    db = SessionLocal()
    try:
        rebuild_matches(db)
    finally:
        db.close()


def matches(code: str = CODE) -> list[tuple]:
    return _rows(
        "SELECT m.filing_id, m.statement, e.local_name, m.element_key, m.sign, m.matched_by, m.is_consolidated "
        "FROM xbrl_pdf_matches m JOIN xbrl_facts f ON f.id = m.fact_id JOIN xbrl_elements e ON e.id = f.element_id "
        "WHERE m.code = :c ORDER BY m.fact_id",
        {"c": code},
    )


def view(code: str, key: str, consolidated: int = 1) -> dict[str, tuple]:
    rows = _rows(
        f"SELECT period_end, value, source, filing_id, matched_by FROM {VIEW_NAME} "
        "WHERE code = :c AND element_key = :k AND is_consolidated = :b ORDER BY period_end",
        {"c": code, "k": key, "b": consolidated},
    )
    return {str(r[0])[:10]: (float(r[1]), r[2], r[3], r[4]) for r in rows}


def by_name(code: str = CODE) -> dict[tuple[str, str], tuple]:
    return {(r[1], r[2]): (r[3], r[4], r[5]) for r in matches(code)}


def test_only_pdf_only_periods_are_matched() -> None:
    assert {r[0] for r in matches(CODE)} == {5}
    assert {r[0] for r in matches(LOOSE)} == {105}
    assert {r[0] for r in matches(NAMED)} == {301}
    assert matches(SINGLE) == [] and matches(EXTRA) == []


def test_value_name_unmatched_and_sign() -> None:
    rows = by_name()
    assert rows[("BS", "現金及び預金")] == ("CashAndDeposits", 1, "value")
    assert rows[("BS", "前受金")] == (None, 1, None)
    assert rows[("PL", "支払利息")] == ("InterestExpensesNOE", -1, "value")
    named = by_name(NAMED)
    assert named[("BS", "社債")] == ("BondsPayable", 1, "name")
    assert named[("BS", "借入金")] == (None, 1, None)
    assert {r[6] for r in matches(CODE)} == {1}


def test_rerun_gives_the_same_rows() -> None:
    before = {code: matches(code) for code in (CODE, LOOSE, NAMED)}
    db = SessionLocal()
    try:
        counts = rebuild_matches(db)
        again = rebuild_matches(db, codes=[CODE])
    finally:
        db.close()
    assert {code: matches(code) for code in (CODE, LOOSE, NAMED)} == before
    assert counts["failed"] == 0 and counts["filings"] == 3
    assert counts["matched"] + counts["unmatched"] == sum(len(v) for v in before.values())
    assert again["codes"] == 1 and again["filings"] == 1


def test_unmatched_names_counts_rows() -> None:
    db = SessionLocal()
    try:
        names = unmatched_names(db)
    finally:
        db.close()
    assert ("BS", "前受金", 2) in names
    assert ("BS", "借入金", 1) in names


def test_reread_with_new_fact_ids_drops_old_matches() -> None:
    old = _rows("SELECT fact_id FROM xbrl_pdf_matches WHERE filing_id = 301 ORDER BY fact_id")
    saved = _rows(
        "SELECT f.element_id, f.context_id, f.unit, f.value_num FROM xbrl_facts f WHERE f.filing_id = 301 ORDER BY f.id"
    )
    db = SessionLocal()
    try:
        delete_filing_payload(db, 301)
        db.commit()
        assert _rows("SELECT count(*) FROM xbrl_pdf_matches WHERE filing_id = 301") == [(0,)]
        _run("INSERT INTO xbrl_contexts (id, filing_id, context_ref, period_type, end_date, is_consolidated, "
             "relative_period, dimensions_json, has_dimensions) VALUES (8000, 301, 'i', 'instant', '2026-06-30', 1, "
             "'current', '{}', 0)")
        for n, (element_id, _ctx, unit, value) in enumerate(saved):
            _run("INSERT INTO xbrl_facts (id, filing_id, element_id, context_id, unit, value_num, is_nil) "
                 "VALUES (:id, 301, :el, 8000, :u, :v, 0)", {"id": 50000 + n, "el": element_id, "u": unit, "v": value})
        rebuild_matches(db, codes=[NAMED])
    finally:
        db.close()
    new = _rows("SELECT fact_id FROM xbrl_pdf_matches WHERE filing_id = 301 ORDER BY fact_id")
    assert len(new) == len(old) and not set(new) & set(old)
    assert _rows("SELECT count(*) FROM xbrl_pdf_matches m LEFT JOIN xbrl_facts f ON f.id = m.fact_id "
                 "WHERE f.id IS NULL") == [(0,)]
    assert by_name(NAMED)[("BS", "社債")] == ("BondsPayable", 1, "name")


def test_view_priority_supplement_and_pdf_rows() -> None:
    cash = view(CODE, "CashAndDeposits")
    assert cash["2024-09-30"][:2] == (80 * M, "edinet")
    assert cash["2025-03-31"][:2] == (100 * M, "edinet")
    assert cash["2025-06-30"] == (90 * M, "tdnet_pdf", 5, "value")
    assert cash["2025-09-30"][:3] == (70 * M, "tdnet", 6)
    assert cash["2025-12-31"][:3] == (60 * M, "edinet", 8)
    assert view(CODE, "Goodwill")["2025-12-31"][:3] == (6 * M, "tdnet", 9)
    assert view(CODE, "InterestExpensesNOE")["2025-06-30"][:2] == (4 * M, "tdnet_pdf")
    assert "2026-03-31" not in cash
    assert _rows(f"SELECT count(*) FROM {VIEW_NAME} WHERE element_key IS NULL OR element_key LIKE 'pdf:%'") == [(0,)]
    assert view(NAMED, "BondsPayable") == {
        "2026-06-30": (4 * M, "tdnet_pdf", 301, "name"), "2026-09-30": (5 * M, "tdnet", 302, None),
    }


def test_view_basis_dimensions_and_namespaces() -> None:
    assert view(CODE, "CashAndDeposits", 0) == {"2025-03-31": (50 * M, "edinet", 1, None)}
    assert view(CODE, "NetSales")["2025-03-31"][:2] == (500 * M, "edinet")
    assert view(SINGLE, "CashAndDeposits", 1) == {}
    assert view(SINGLE, "CashAndDeposits", 0)["2025-03-31"][:2] == (100 * M, "edinet")
    assert _rows(f"SELECT count(*) FROM {VIEW_NAME} WHERE element_key IN ('NumberOfEmployees', "
                 f"'WhetherConsolidatedFinancialStatementsArePreparedDEI')") == [(0,)]


def test_view_amendment_ext_keys_and_cumulative() -> None:
    sales = view(EXTRA, "NetSales")
    assert sales["2025-03-31"] == (510 * M, "edinet", 902, None)
    assert sales["2025-09-30"] == (250 * M, "tdnet", 904, None)
    assert view(EXTRA, "ext:SpecialItem")["2025-03-31"][:3] == (7 * M, "edinet", 901)
    assert view(EXTRA, "ext:OnlyTdnet")["2025-03-31"][:3] == (3 * M, "tdnet", 903)
    assert view(EXTRA, "SpecialItem") == {}


def _api_cells(code: str) -> dict[tuple[str, str], tuple[float, str, str]]:
    db = SessionLocal()
    try:
        out = {}
        for statement in fst.STATEMENTS:
            table = fst.financial_table(db, code, statement=statement, periods=fst.MAX_PERIODS, consolidated=True)
            for row in table["rows"]:
                for period, cell in zip(table["periods"], row["cells"]):
                    if cell is not None:
                        out[(row["key"], period["period_end"])] = (cell["value"], cell["source"], period["source"])
        return out
    finally:
        db.close()


@pytest.mark.parametrize("code", [CODE, LOOSE, NAMED])
def test_view_agrees_with_api(code: str) -> None:
    rows = _rows(f"SELECT element_key, period_end, value, source FROM {VIEW_NAME} WHERE code = :c "
                 "AND is_consolidated = 1", {"c": code})
    got = {(r[0], str(r[1])[:10]): (float(r[2]), r[3]) for r in rows}
    for (key, end), (value, source, primary) in _api_cells(code).items():
        if key.startswith("pdf:"):
            assert (key, end) not in got
            continue
        if source == "tdnet_pdf" and primary != "tdnet_pdf":
            assert (key, end) not in got
            continue
        assert got[(key, end)] == (value, source), (key, end)


def test_summary_line() -> None:
    db = SessionLocal()
    try:
        counts = rebuild_matches(db, codes=[NAMED])
    finally:
        db.close()
    assert summary(counts).startswith("pdf_matches codes=1 filings=1 matched=")
    assert summary(counts).endswith("failed=0 skipped=0 deleted=0")


def test_batch_lines_and_locked_retry(capsys, monkeypatch) -> None:
    from sqlalchemy.exc import OperationalError

    from app import xbrl
    real = xbrl.pdf_matches.rebuild_code
    left = {NAMED: 1}

    def fake(db, code):
        if left.get(code):
            left[code] -= 1
            raise OperationalError("DELETE", {}, Exception("database is locked"))
        return real(db, code)

    monkeypatch.setattr(xbrl.pdf_matches, "rebuild_code", fake)
    monkeypatch.setattr(xbrl.pdf_matches.time, "sleep", lambda s: None)
    db = SessionLocal()
    try:
        counts = rebuild_matches(db, codes=[CODE, LOOSE, NAMED], batch=2)
    finally:
        db.close()
    out = capsys.readouterr().out.splitlines()
    assert [line.split()[2] for line in out if line.startswith("pdf_matches batch")] == ["done=2/3", "done=3/3"]
    assert any(line.startswith("pdf_matches locked retry=1") for line in out)
    assert counts["failed"] == 0 and counts["codes"] == 3


def _insert_unlisted_matches(code: str, n: int) -> None:
    for i in range(n):
        _run(
            "INSERT INTO xbrl_pdf_matches (fact_id, filing_id, code, is_consolidated, statement, element_key, sign, "
            "generated_at) VALUES (:f, 301, :c, 1, 'BS', NULL, 1, '2026-01-01')",
            {"f": 700000 + i, "c": code},
        )


def test_unlisted_codes_are_skipped_and_their_rows_deleted(capsys) -> None:
    unlisted = "99990"
    _insert_unlisted_matches(unlisted, 3)
    _filing(950, "tdnet_pdf", "FY", "2025-03-31", "2025-05-10 15:00:00")
    _run("UPDATE xbrl_filings SET code = :c WHERE id = 950", {"c": unlisted})
    db = SessionLocal()
    try:
        counts = rebuild_matches(db, batch=2)
        out = capsys.readouterr().out
        assert counts["deleted"] == 3 and counts["skipped"] == 1 and counts["failed"] == 0
        assert counts["codes"] == len(target_codes(db)) and unlisted not in target_codes(db)
        assert _rows("SELECT count(*) FROM xbrl_pdf_matches WHERE code = :c", {"c": unlisted}) == [(0,)]
        assert _rows("SELECT count(*) FROM xbrl_facts") != [(0,)]
        assert summary(counts).endswith("failed=0 skipped=1 deleted=3")
        assert "skipped=1 deleted=3 left=" in out
        again = rebuild_matches(db)
        assert again["deleted"] == 0 and again["skipped"] == 1
    finally:
        db.close()
        _run("DELETE FROM xbrl_filings WHERE id = 950")


def test_code_option_skips_unlisted_and_keeps_its_rows(capsys) -> None:
    unlisted = "99991"
    _insert_unlisted_matches(unlisted, 2)
    db = SessionLocal()
    try:
        counts = rebuild_matches(db, codes=[unlisted, NAMED])
    finally:
        db.close()
    out = capsys.readouterr().out
    assert f"pdf_matches skipped code={unlisted} reason=not in stocks" in out
    assert counts["skipped"] == 1 and counts["codes"] == 1 and counts["deleted"] == 0
    assert _rows("SELECT count(*) FROM xbrl_pdf_matches WHERE code = :c", {"c": unlisted}) == [(2,)]
    _run("DELETE FROM xbrl_pdf_matches WHERE code = :c", {"c": unlisted})


def test_view_is_recreated_when_definition_changes() -> None:
    _run(f"DROP VIEW {VIEW_NAME}")
    _run(f"CREATE VIEW {VIEW_NAME} AS SELECT 1 AS code")
    ensure_views()
    assert _rows(f"SELECT count(*) FROM {VIEW_NAME} WHERE code = :c", {"c": EXTRA})[0][0] > 0


def test_migration_adds_table_indexes_and_view_to_existing_db() -> None:
    before = _rows("SELECT count(*) FROM xbrl_facts")
    _run(f"DROP VIEW {VIEW_NAME}")
    _run("DROP TABLE xbrl_pdf_matches")
    _run("DROP INDEX idx_xbrl_elements_local")
    _run("DROP INDEX idx_xbrl_elements_ext_key")
    init_db()
    names = {r[0] for r in _rows("SELECT name FROM sqlite_master")}
    assert {"xbrl_pdf_matches", VIEW_NAME, "idx_xbrl_elements_local", "idx_xbrl_elements_ext_key",
            "idx_xbrl_pdf_matches_key", "idx_xbrl_pdf_matches_code", "idx_xbrl_pdf_matches_filing"} <= names
    assert _rows("SELECT count(*) FROM xbrl_facts") == before
    db = SessionLocal()
    try:
        rebuild_matches(db)
    finally:
        db.close()
    assert view(CODE, "CashAndDeposits")["2025-06-30"][1] == "tdnet_pdf"


def test_view_uses_indexes() -> None:
    plans = {}
    for where in ("element_key = 'CashAndDeposits'", f"code = '{CODE}'", "element_key = 'ext:SpecialItem'"):
        plans[where] = " | ".join(
            r[3] for r in _rows(f"EXPLAIN QUERY PLAN SELECT * FROM {VIEW_NAME} WHERE {where}")
        )
    assert "idx_xbrl_elements_local" in plans["element_key = 'CashAndDeposits'"]
    assert "idx_xbrl_pdf_matches_key" in plans["element_key = 'CashAndDeposits'"]
    assert "idx_xbrl_elements_ext_key" in plans["element_key = 'ext:SpecialItem'"]
    assert "SCAN fl" not in plans[f"code = '{CODE}'"] and "SCAN f " not in plans[f"code = '{CODE}'"]


def _cli():
    spec = importlib.util.spec_from_file_location(
        "tdnet_pdf_financials_cli_matches", ROOT / "tools" / "tdnet_pdf" / "financials.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_financials_rebuilds_matches_for_reread_codes(capsys, monkeypatch) -> None:
    from collections import Counter

    cli = _cli()
    seen = []

    def fake_sync(db, *, codes, reparse, limit, batch, changed):
        changed.update({NAMED, CODE} if codes is None else set(codes))
        return Counter()

    def fake_rebuild(db, *, codes, batch):
        seen.append((codes, batch))
        return Counter({"codes": len(codes)})

    monkeypatch.setattr(cli, "sync_pdf_filings", fake_sync)
    monkeypatch.setattr(cli, "rebuild_matches", fake_rebuild)
    assert cli.main(["--batch", "5"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("tdnet_pdf_financials ") and out[1].startswith("pdf_matches codes=2 ")
    assert seen == [(sorted([NAMED, CODE]), 5)]
    assert cli.main(["--no-matches"]) == 0
    assert len(seen) == 1

    def broken(db, *, codes, batch):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "rebuild_matches", broken)
    assert cli.main([]) == 1
    out = capsys.readouterr().out
    assert "tdnet_pdf_financials " in out and "pdf_matches failed err=boom" in out


def test_financials_skips_reread_codes_not_in_stocks(capsys, monkeypatch) -> None:
    from collections import Counter

    cli = _cli()
    unlisted = "99992"

    def fake_sync(db, *, codes, reparse, limit, batch, changed):
        changed.update({unlisted, NAMED})
        return Counter()

    monkeypatch.setattr(cli, "sync_pdf_filings", fake_sync)
    assert cli.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"pdf_matches skipped code={unlisted} reason=not in stocks" in out
    assert out[-1].startswith("pdf_matches codes=1 ") and out[-1].endswith("skipped=1 deleted=0")
    assert matches(unlisted) == []


def test_sync_reports_changed_codes() -> None:
    from datetime import date

    from tests.xbrl.test_tdnet_pdf_financials import _jp_fy, _seed, _statement

    from app.xbrl.mappings import apply_mappings
    from app.xbrl.pdf import sync_pdf_filings

    no = "20250515512346"
    statement = _statement(
        disclosure_no=no, type_of_period="FY", type_of_document="FYFinancialStatements_Consolidated_JP",
        disclosed_date=date(2025, 5, 15), period_start=date(2024, 4, 1), period_end=date(2025, 3, 31),
        fiscal_year_start=date(2024, 4, 1), fiscal_year_end=date(2025, 3, 31),
    )
    db = SessionLocal()
    try:
        _seed(db, no, _jp_fy(), statement)
        apply_mappings(db)
        changed: set[str] = set()
        sync_pdf_filings(db, codes=["72030"], limit=10, changed=changed)
        assert changed == {"72030"}
        again: set[str] = set()
        sync_pdf_filings(db, codes=["72030"], limit=10, changed=again)
        assert again == set()
        counts = rebuild_matches(db, codes=sorted(changed))
        assert counts["filings"] == 1 and counts["unmatched"] > 0 and counts["matched"] == 0
    finally:
        db.close()

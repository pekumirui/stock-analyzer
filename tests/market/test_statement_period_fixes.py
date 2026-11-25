"""J-Quants の期の日付が 1 年前の開示の補正（一覧にある開示だけ）と、新しい候補の WARNING。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-period-fixes-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db
from app.market.ingest import (
    _statement_row,
    ingest_statements_for_code,
    ingest_statements_for_date,
    upsert_statements,
)
from app.market.period_fixes import (
    apply_period_fix,
    default_dropped_disclosures,
    load_dropped_disclosures,
    load_period_fixes,
    next_year,
    period_candidates,
)
from app.models import EarningsEvent, Statement, TdnetPdfDocument
from app.xbrl.statements import RAW_MARKER, SOURCE_XBRL, merge_into
from deploy.alert_patterns import ALERT_PATTERNS
from tools.market.fix_statement_periods import main, plan, scan_candidates, write

FY_2121 = {
    "DiscDate": "2024-05-10", "DiscTime": "15:00", "Code": "21210", "DiscNo": "20240508585107",
    "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY",
    "CurPerSt": "2022-04-01", "CurPerEn": "2023-03-31", "CurFYSt": "2022-04-01", "CurFYEn": "2023-03-31",
    "NxtFYSt": "2024-04-01", "NxtFYEn": "2025-03-31",
    "Sales": "146868000000", "OP": "19177000000", "OdP": "", "NP": "", "EPS": "",
}
DROPPED = "20250521559959"
DROPPED_RAW = {
    "DiscDate": "2025-05-21", "DiscTime": "15:00", "Code": "13320", "DiscNo": DROPPED,
    "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY",
    "CurPerSt": "2024-04-01", "CurPerEn": "2025-03-31", "CurFYSt": "2024-04-01", "CurFYEn": "2025-03-31",
    "NxtFYSt": "2025-04-01", "NxtFYEn": "2026-03-31", "Sales": "1000", "NP": "27213000000", "EPS": "",
}
NEXT_ONLY = {
    "DiscDate": "2023-12-13", "Code": "92540", "DiscNo": "20231213502556",
    "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY",
    "CurPerSt": "2022-11-01", "CurPerEn": "2023-10-31", "CurFYSt": "2022-11-01", "CurFYEn": "2023-10-31",
    "NxtFYSt": "2024-11-01", "NxtFYEn": "2025-10-31", "Sales": "1000",
}


def _q(no: str, disc: str, sales: str, *, code: str = "17570", end: str = "2024-06-30") -> dict:
    return {
        "DiscDate": disc, "Code": code, "DiscNo": no, "DocType": "1QFinancialStatements_Consolidated_JP",
        "CurPerType": "1Q", "CurPerSt": "2024-04-01", "CurPerEn": end, "CurFYSt": "2024-04-01",
        "CurFYEn": "2025-03-31", "NxtFYSt": "", "Sales": sales,
    }


@pytest.fixture
def db():
    init_db()
    s = SessionLocal()
    for model in (Statement, EarningsEvent, TdnetPdfDocument):
        s.query(model).delete()
    s.commit()
    try:
        yield s
    finally:
        s.close()


def test_real_config() -> None:
    fixes = load_period_fixes()
    assert len(fixes) == 24
    assert fixes["20240508585107"] == date(2023, 3, 31)
    assert fixes["20260513530259"] == date(2025, 3, 31)
    for no in ("20260731505749", "20231213502556", "20240215537950", "20250720517365",
               "20260819522868", "20260911535254", "20260930543013", "20260930543580", "20261028557434"):
        assert no not in fixes


def test_duplicate_config(tmp_path: Path) -> None:
    path = tmp_path / "fixes.json"
    item = {"disclosure_no": "1", "period_end": "2023-03-31"}
    path.write_text(json.dumps({"fixes": [item, item]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_period_fixes(path)


def test_drop_config(tmp_path: Path) -> None:
    path = tmp_path / "fixes.json"
    fix = {"disclosure_no": "1", "period_end": "2023-03-31"}
    drop = {"disclosure_no": "2", "code": "13320", "drop": True, "evidence": "x"}
    path.write_text(json.dumps({"fixes": [fix, drop]}), encoding="utf-8")
    assert load_period_fixes(path) == {"1": date(2023, 3, 31)}
    assert load_dropped_disclosures(path) == frozenset({"2"})
    for items in ([drop, drop], [fix, {**drop, "disclosure_no": "1"}], [drop, {**fix, "disclosure_no": "2"}]):
        path.write_text(json.dumps({"fixes": items}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_period_fixes(path)
        with pytest.raises(ValueError):
            load_dropped_disclosures(path)
    assert DROPPED in default_dropped_disclosures()
    assert len(default_dropped_disclosures()) == 14
    assert DROPPED not in load_period_fixes()


def test_next_year() -> None:
    assert next_year(date(2023, 3, 31)) == date(2024, 3, 31)
    assert next_year(date(2022, 4, 1)) == date(2023, 4, 1)
    assert next_year(date(2023, 2, 28)) == date(2024, 2, 29)
    assert next_year(date(2024, 2, 29)) == date(2025, 2, 28)
    assert next_year(date(2023, 6, 30)) == date(2024, 6, 30)
    assert next_year(None) is None


def test_listed_row_moves_one_year_and_keeps_raw() -> None:
    row = _statement_row(dict(FY_2121))
    assert (row["period_start"], row["period_end"]) == (date(2023, 4, 1), date(2024, 3, 31))
    assert (row["fiscal_year_start"], row["fiscal_year_end"]) == (date(2023, 4, 1), date(2024, 3, 31))
    assert row["disclosed_date"] == date(2024, 5, 10)
    assert row["net_sales"] == 146868000000.0
    assert json.loads(row["raw_json"]) == FY_2121
    assert row == {**_statement_row(dict(FY_2121), {}), **{k: row[k] for k in (
        "period_start", "period_end", "fiscal_year_start", "fiscal_year_end")}}


def test_listed_but_period_end_differs(capsys) -> None:
    fixed_by_jquants = {**FY_2121, "CurPerSt": "2023-04-01", "CurPerEn": "2024-03-31",
                        "CurFYSt": "2023-04-01", "CurFYEn": "2024-03-31"}
    row = _statement_row(fixed_by_jquants)
    assert row == _statement_row(fixed_by_jquants, {})
    assert row["period_end"] == date(2024, 3, 31)
    out = capsys.readouterr().out
    assert "WARNING jquants statements period-fix-mismatch disclosure_no=20240508585107" in out
    assert any(re.search(p.regex, out) for p in ALERT_PATTERNS if p.source == "batch")


def test_unlisted_rows_unchanged() -> None:
    for raw in (NEXT_ONLY, {**FY_2121, "DiscNo": "99999999999999"},
                _q("20250720517365", "2025-08-07", "500")):
        assert _statement_row(dict(raw)) == _statement_row(dict(raw), {})


def test_apply_period_fix_only_listed() -> None:
    row = _statement_row(dict(FY_2121), {})
    assert apply_period_fix(row, {}) is row
    assert apply_period_fix(row, {"other": date(2023, 3, 31)}) is row


def test_upsert_stores_shifted_dates(db) -> None:
    upsert_statements(db, [_statement_row(dict(FY_2121))])
    st = db.get(Statement, "20240508585107")
    assert st.period_end == date(2024, 3, 31) and st.fiscal_year_end == date(2024, 3, 31)
    assert json.loads(st.raw_json)["CurPerEn"] == "2023-03-31"


def test_candidates(db) -> None:
    upsert_statements(db, [
        _statement_row(_q("A1", "2024-08-09", "1000")),
        _statement_row(_q("B1", "2024-08-09", "2000", code="38560")),
    ])
    reissue_same = _statement_row(_q("A2", "2025-08-07", "1000"))
    reissue_diff = _statement_row(_q("B2", "2025-08-07", "2100", code="38560"))
    fy_unlisted = _statement_row({**FY_2121, "DiscNo": "F1", "Code": "11110"})
    fy_listed = _statement_row(dict(FY_2121))
    next_only = _statement_row(dict(NEXT_ONLY))
    not_late = _statement_row(_q("C1", "2024-08-09", "1", code="99990"))
    items = [(reissue_same, None), (reissue_diff, None), (fy_unlisted, date(2024, 4, 1)),
             (fy_listed, date(2024, 4, 1)), (next_only, date(2024, 11, 1)), (not_late, None)]
    found = period_candidates(db, items)
    assert [(c.rule, c.disclosure_no) for c in found] == [("quarter", "B2"), ("fy", "F1")]
    assert "prev=B1" in found[0].line()
    assert "next_fy_start=2024-04-01" in found[1].line()
    fy_ok = _statement_row({**FY_2121, "DiscNo": "F2", "DiscDate": "2024-05-10", "CurPerSt": "2023-04-01",
                            "CurPerEn": "2024-03-31", "CurFYSt": "2023-04-01", "CurFYEn": "2024-03-31"})
    assert period_candidates(db, [(fy_ok, date(2024, 4, 1))]) == []
    for c in found:
        assert any(re.search(p.regex, c.line()) for p in ALERT_PATTERNS if p.source == "batch")


class _JQ:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def fins_summary(self, date: str | None = None, **kwargs) -> list[dict]:
        return [dict(r) for r in self.rows]


def test_ingest_warns_once_for_new_disclosures(db, capsys) -> None:
    raws = [{**FY_2121, "DiscNo": "F1", "Code": "11110"}, dict(FY_2121)]
    xbrl_raw = {RAW_MARKER: SOURCE_XBRL, "DiscNo": "F1"}
    db.add(Statement(disclosure_no="F1", code="11110", disclosed_date=date(2024, 5, 10),
                     raw_json=json.dumps(xbrl_raw), source=SOURCE_XBRL))
    db.commit()
    ingest_statements_for_date(db, _JQ(raws), "2024-05-10", warn=True)
    out = capsys.readouterr().out
    assert out.count("period-candidate") == 1 and "disclosure_no=F1" in out
    ingest_statements_for_date(db, _JQ(raws), "2024-05-10", warn=True)
    assert "period-candidate" not in capsys.readouterr().out
    db.query(Statement).filter(Statement.disclosure_no == "F1").delete()
    db.commit()
    ingest_statements_for_date(db, _JQ(raws), "2024-05-10")
    assert "period-candidate" not in capsys.readouterr().out
    st = db.get(Statement, "20240508585107")
    assert st.period_end == date(2024, 3, 31)
    assert json.loads(st.raw_json) == FY_2121


def test_fix_tool(db) -> None:
    old = _statement_row(dict(FY_2121), {})
    other = _statement_row({**FY_2121, "DiscNo": "F1", "Code": "11110"}, {})
    upsert_statements(db, [old, other])
    db.add(Statement(disclosure_no="20260513530259", code="92490", raw_json=json.dumps(
        {"DiscNo": "20260513530259", "Code": "92490", "CurPerEn": "2025-09-30"})))
    db.commit()
    before_other = {c.key: getattr(db.get(Statement, "F1"), c.key) for c in Statement.__table__.columns}
    planned = {p.disclosure_no: p for p in plan(db)}
    assert len(planned) == 38
    assert planned["20240508585107"].status == "changed"
    assert "period_end=2023-03-31->2024-03-31" in planned["20240508585107"].line()
    assert planned["20260513530259"].status == "mismatch"
    assert planned["20250514551471"].status == "missing"
    assert db.get(Statement, "20240508585107").period_end == date(2023, 3, 31)
    assert write(db, list(planned.values())) == 1
    db.expire_all()
    st = db.get(Statement, "20240508585107")
    assert (st.period_start, st.period_end, st.fiscal_year_start, st.fiscal_year_end) == (
        date(2023, 4, 1), date(2024, 3, 31), date(2023, 4, 1), date(2024, 3, 31))
    assert json.loads(st.raw_json) == FY_2121
    assert st.net_sales == old["net_sales"]
    assert {c.key: getattr(db.get(Statement, "F1"), c.key) for c in Statement.__table__.columns} == before_other
    assert {p.disclosure_no: p.status for p in plan(db)}["20240508585107"] == "unchanged"
    found = scan_candidates(db)
    assert [c.disclosure_no for c in found] == ["F1"]


def test_ingest_skips_dropped(db) -> None:
    kept = {**DROPPED_RAW, "DiscNo": "20250513546856", "DiscDate": "2025-05-13", "NP": "25381000000", "EPS": "81.66"}
    assert ingest_statements_for_date(db, _JQ([DROPPED_RAW, kept]), "2025-05-21") == 1
    assert db.get(Statement, DROPPED) is None
    assert db.get(Statement, "20250513546856").eps == 81.66
    assert ingest_statements_for_code(db, _JQ([DROPPED_RAW]), "13320") == 0
    upsert_statements(db, [_statement_row(dict(DROPPED_RAW))])
    assert db.get(Statement, DROPPED) is None


def test_xbrl_merge_does_not_create_dropped(db) -> None:
    assert merge_into(db, {"disclosure_no": DROPPED}) == "skipped"
    db.commit()
    assert db.get(Statement, DROPPED) is None


def test_fix_tool_drop(db, capsys) -> None:
    other = {**DROPPED_RAW, "DiscNo": "20250513546856", "DiscDate": "2025-05-13"}
    db.add_all([
        Statement(disclosure_no=DROPPED, code="13320", disclosed_date=date(2025, 5, 21), raw_json=json.dumps(DROPPED_RAW)),
        Statement(disclosure_no="20250513546856", code="13320", disclosed_date=date(2025, 5, 13), raw_json=json.dumps(other)),
        TdnetPdfDocument(disclosure_no=DROPPED, code="13320", status="missing", created_at=datetime(2025, 5, 22)),
        EarningsEvent(code="13320", disclosed_date=date(2025, 5, 21)),
        EarningsEvent(code="13320", disclosed_date=date(2025, 5, 13)),
    ])
    db.commit()
    planned = plan(db)
    by_no = {p.disclosure_no: p for p in planned}
    assert by_no[DROPPED].status == "drop"
    assert by_no[DROPPED].line() == f"drop disclosure_no={DROPPED} code=13320"
    assert db.get(Statement, DROPPED) is not None
    assert plan(db, {}, frozenset({"nope"}))[0].status == "missing"
    assert write(db, planned) == 1
    db.expire_all()
    assert db.get(Statement, DROPPED) is None
    assert db.get(Statement, "20250513546856") is not None
    assert db.get(TdnetPdfDocument, DROPPED) is not None
    assert [e.disclosed_date for e in db.query(EarningsEvent)] == [date(2025, 5, 13)]
    assert {p.disclosure_no: p.status for p in plan(db)}[DROPPED] == "missing"
    main(["--write"])
    assert "drop=0" in capsys.readouterr().out


def test_fix_tool_drop_keeps_event_with_other_disclosure(db) -> None:
    db.add_all([
        Statement(disclosure_no=DROPPED, code="13320", disclosed_date=date(2025, 5, 21), raw_json="{}"),
        Statement(disclosure_no="20250521000001", code="13320", disclosed_date=date(2025, 5, 21), raw_json="{}"),
        EarningsEvent(code="13320", disclosed_date=date(2025, 5, 21)),
    ])
    db.commit()
    assert write(db, plan(db)) == 1
    assert db.query(EarningsEvent).count() == 1

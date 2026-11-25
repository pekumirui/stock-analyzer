from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tests.market.jquants_columns import fy_columns
from tests.market.test_compare_withdrawals import _seed
from tools.market.compare_quarter_flash import readonly_session
from tools.market.export_revisions import (after_undecided_report, compare, export, fiscal_year_changes,
                                           old_period_reports, partial_revisions, same_day_out_of_order,
                                           same_period_reports)


def _export(tmp_path: Path) -> dict[str, dict]:
    path = tmp_path / "prod.db"
    _seed(path)
    out = tmp_path / "new.jsonl"
    db = readonly_session(path)
    try:
        assert export(db, out) == 0
    finally:
        db.close()
    return {r["code"]: r for r in map(json.loads, out.read_text(encoding="utf-8").splitlines())}


def _write(path: Path, rows: dict[str, dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()), encoding="utf-8")
    return path


def test_export_marks_from_xbrl_when_the_column_is_missing(tmp_path: Path, capsys) -> None:
    rows = _export(tmp_path)
    assert sorted(rows) == ["70610", "99990"]
    assert rows["70610"]["marked"] == ["20260806513051"]
    assert [(r["kind"], r["disclosed_date"], r["values"]["eps"]) for r in rows["70610"]["history"]] == [
        ("forecast_withdrawn", "2026-08-14", None)]
    assert rows["99990"]["marked"] == [] and rows["99990"]["history"] == []
    assert "codes=2 marks_source=xbrl_financials marked_disclosures=1" in capsys.readouterr().out


def test_compare_sorts_changed_codes_into_categories(tmp_path: Path, capsys) -> None:
    new = _export(tmp_path)
    old = json.loads(json.dumps(new))
    old["70610"]["history"] = []
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 0
    out = capsys.readouterr().out
    assert "codes=2 changed=1" in out and "marked=1 codes=70610" in out and "other=0 codes=" in out, out

    old["99990"]["history"] = new["70610"]["history"]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=True) == 1
    out = capsys.readouterr().out
    assert "changed code=99990 category=other" in out and "other=1 codes=99990" in out, out


def test_compare_explains_badges_after_an_undecided_report(tmp_path: Path, capsys) -> None:
    new = _export(tmp_path)
    old = json.loads(json.dumps(new))
    new["99990"]["badges"] = [["99990x", "2026-08-01", None]]
    old["99990"]["badges"] = [["99990x", "2026-08-01", {"direction": "up", "pct": 5.0}]]
    new["99990"]["after_undecided_report"] = ["99990x"]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 0
    assert "after_undecided_report=1 codes=99990" in capsys.readouterr().out
    new["99990"]["after_undecided_report"] = []
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 1


def _s(no, day, period, doc, fy_op=None, fy_end=date(2026, 3, 31), **raw):
    return SimpleNamespace(disclosure_no=no, disclosed_date=day, disclosed_time="15:30:00", type_of_period=period,
                           type_of_document=doc, is_revision="Revision" in doc, raw_json=json.dumps(raw),
                           **{**fy_columns(raw), "fy_operating_profit": fy_op, "fiscal_year_end": fy_end})


def test_forms_that_may_change() -> None:
    q1 = _s("q1", date(2025, 8, 1), "1Q", "1QFinancialStatements_Consolidated_IFRS", 40, FSales=1, FOP=40, FNP=2,
            FEPS=3)
    op_only = _s("r1", date(2025, 9, 1), "FY", "EarnForecastRevision", 45, FOP=45)
    no_odp = _s("r2", date(2025, 9, 2), "FY", "EarnForecastRevision", 45, FSales=1, FOP=45, FNP=2, FEPS=3)
    flash = _s("r3", date(2025, 9, 3), "1Q", "EarnForecastRevision", 9, FOP=9)
    undecided = _s("q2", date(2025, 11, 1), "2Q", "2QFinancialStatements_Consolidated_IFRS")
    q3 = _s("q3", date(2026, 2, 1), "3Q", "3QFinancialStatements_Consolidated_IFRS", 50, FOP=50)
    again = _s("q1b", date(2026, 2, 5), "1Q", "1QFinancialStatements_Consolidated_IFRS", 40, FOP=40)
    stmts = [q1, op_only, no_odp, flash, undecided, q3, again]
    assert partial_revisions(stmts) == ["r1"]
    assert after_undecided_report(stmts) == ["q3"]
    assert old_period_reports(stmts) == ["q1b"]


def test_same_day_disclosures_whose_time_and_number_disagree() -> None:
    day = date(2021, 11, 11)
    q2 = _s("20211111431991", day, "2Q", "2QFinancialStatements_Consolidated_JP")
    q2.disclosed_time = "12:30:00"
    q3 = _s("20211110431031", day, "3Q", "3QFinancialStatements_Consolidated_JP")
    rev = _s("20211110431055", day, "FY", "EarnForecastRevision")
    other = _s("20211112000001", date(2021, 11, 12), "1Q", "1QFinancialStatements_Consolidated_JP")
    assert same_day_out_of_order([q2, q3, rev, other]) == ["20211110431031", "20211110431055", "20211111431991"]
    assert same_day_out_of_order([q3, rev, other]) == []


def test_export_writes_the_chart_steps_of_the_forecast_eps(tmp_path: Path) -> None:
    rows = _export(tmp_path)
    assert rows["70610"]["chart"] == [["2026-08-17", None]]
    assert rows["99990"]["chart"] == [["2026-08-17", 71.2], ["2026-10-17", 41.43]]
    assert rows["99990"]["columns_without_raw"] == ["20261013550006"]


def test_compare_allows_chart_changes_only_for_marks_and_columns_without_raw(tmp_path: Path, capsys) -> None:
    new = _export(tmp_path)
    old = json.loads(json.dumps(new))
    old["99990"]["chart"] = old["99990"]["chart"][:1]
    old["70610"]["chart"] = [["2026-08-17", 71.2]]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 0
    out = capsys.readouterr().out
    assert "changed code=70610 category=marked parts=chart" in out, out
    assert "changed code=99990 category=columns_without_raw parts=chart" in out, out
    assert "chart_codes=2" in out, out
    new["99990"]["columns_without_raw"] = []
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 1
    assert "changed code=99990 category=other parts=chart" in capsys.readouterr().out


def test_reports_of_one_year_start_with_two_year_ends() -> None:
    q3 = _s("q3", date(2026, 2, 16), "3Q", "3QFinancialStatements_Consolidated_JP")
    fy = _s("fy", date(2026, 4, 17), "FY", "FYFinancialStatements_Consolidated_JP", fy_end=date(2026, 2, 28))
    rev = _s("rev", date(2026, 4, 17), "FY", "EarnForecastRevision", fy_end=date(2026, 2, 28))
    q1 = _s("q1", date(2026, 7, 15), "1Q", "1QFinancialStatements_Consolidated_JP", fy_end=date(2027, 2, 28))
    for s, start in ((q3, date(2025, 4, 1)), (fy, date(2025, 4, 1)), (rev, date(2025, 4, 1)), (q1, date(2026, 3, 1))):
        s.fiscal_year_start = start
    assert fiscal_year_changes([q1, rev, fy, q3]) == ["q3", "fy"]
    assert fiscal_year_changes([q3, q1]) == []
    del q3.fiscal_year_start
    assert fiscal_year_changes([q3, fy, q1]) == ["fy"]


def test_compare_explains_history_and_chart_by_a_fiscal_year_change(tmp_path: Path, capsys) -> None:
    new = _export(tmp_path)
    old = json.loads(json.dumps(new))
    new["99990"]["columns_without_raw"] = []
    new["99990"]["fiscal_year_change"] = ["99990q3", "99990fy"]
    old["99990"]["chart"] = old["99990"]["chart"][:1]
    old["99990"]["history"] = new["70610"]["history"]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 0
    out = capsys.readouterr().out
    assert "changed code=99990 category=fiscal_year_change parts=history,chart" in out, out
    assert "fiscal_year_change=1 codes=99990" in out, out
    new["99990"]["fiscal_year_change"] = []
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 1


def test_reports_whose_year_start_and_year_end_disagree_on_which_is_newer() -> None:
    mar24 = date(2024, 3, 31)
    q3 = _s("q3", date(2024, 2, 14), "3Q", "3QFinancialStatements_Consolidated_JP", fy_end=mar24)
    fix = _s("fix", date(2024, 2, 22), "3Q", "3QFinancialStatements_Consolidated_JP", fy_end=mar24)
    fy = _s("fy", date(2024, 5, 15), "FY", "FYFinancialStatements_Consolidated_JP", fy_end=mar24)
    for s, start in ((q3, date(2023, 4, 1)), (fix, date(2023, 10, 2)), (fy, date(2023, 4, 1))):
        s.fiscal_year_start = start
    assert fiscal_year_changes([q3, fix, fy]) == ["fy"]

    aug25 = date(2025, 8, 31)
    q1 = _s("q1", date(2025, 8, 8), "1Q", "1QFinancialStatements_Consolidated_JP", fy_end=aug25)
    wrong = _s("wrong", date(2025, 10, 14), "FY", "FYFinancialStatements_Consolidated_JP", fy_end=aug25)
    again = _s("again", date(2025, 12, 1), "FY", "FYFinancialStatements_Consolidated_JP", fy_end=aug25)
    for s, start in ((q1, date(2025, 4, 1)), (wrong, date(2024, 9, 1)), (again, date(2025, 4, 1))):
        s.fiscal_year_start = start
    assert fiscal_year_changes([q1, wrong, again]) == ["wrong"]

    q2 = _s("q2", date(2026, 11, 11), "2Q", "2QFinancialStatements_Consolidated_JP", fy_end=date(2027, 3, 31))
    q1b = _s("q1b", date(2026, 11, 12), "1Q", "1QFinancialStatements_Consolidated_JP", fy_end=date(2027, 3, 31))
    for s in (q2, q1b):
        s.fiscal_year_start = date(2026, 4, 1)
    assert fiscal_year_changes([q2, q1b]) == []


def test_compare_explains_changes_from_the_second_report_of_the_same_period(tmp_path: Path, capsys) -> None:
    doc = "3QFinancialStatements_Consolidated_JP"
    q3 = _s("q3", date(2025, 11, 12), "3Q", doc, 47)
    rev = _s("rev", date(2025, 11, 27), "FY", "EarnForecastRevision", 56)
    fix = _s("fix", date(2025, 11, 27), "3Q", doc, 47)
    ifrs = _s("ifrs", date(2025, 12, 1), "3Q", "3QFinancialStatements_Consolidated_IFRS", 47)
    assert same_period_reports([q3, rev, fix, ifrs]) == ["2025-11-27"]
    new = _export(tmp_path)
    old = json.loads(json.dumps(new))
    new["99990"]["same_period_report"] = ["2025-11-27"]
    old["99990"]["chart"] = [["2025-11-28", 396.09]]
    new["99990"]["chart"] = [["2025-11-28", 443.49]]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 0
    assert "same_period_report=1 codes=99990" in capsys.readouterr().out
    new["99990"]["badges"] = [["early", "2025-11-12", None]]
    assert compare(_write(tmp_path / "old.jsonl", old), _write(tmp_path / "new.jsonl", new), show=False) == 1

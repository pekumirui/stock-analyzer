from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from tests.market.jquants_columns import fy_columns
from tests.market.test_compare_withdrawals import _seed
from tools.market.compare_quarter_flash import readonly_session
from tools.market.export_revisions import (after_undecided_report, compare, export, old_period_reports,
                                           partial_revisions, same_day_out_of_order)


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
    new["99990"]["badges"] = [["99990x", None]]
    old["99990"]["badges"] = [["99990x", {"direction": "up", "pct": 5.0}]]
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

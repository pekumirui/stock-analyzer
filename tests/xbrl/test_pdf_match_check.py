from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import engine
from app.xbrl import three_statements as fst
from tests.xbrl.test_three_statements import CODE, seed
from tools.xbrl import pdf_match_check as check


@pytest.fixture(scope="module", autouse=True)
def _seeded(_fresh_db_per_file):
    seed()


@pytest.fixture
def no_labels(tmp_path) -> str:
    path = tmp_path / "labels.json"
    path.write_text('{"labels": {}, "per_share": []}', encoding="utf-8")
    return str(path)


def stats(text: str) -> dict[str, int]:
    return {
        line.split("\t")[1]: int(line.split("\t")[2]) for line in text.splitlines() if line.startswith("stat\t")
    }


def table(path: Path) -> list[dict[str, str]]:
    head, *rows = path.read_text(encoding="utf-8").splitlines()
    names = head.split("\t")
    return [dict(zip(names, row.split("\t"))) for row in rows]


def test_hidden_period_is_scored_against_its_xbrl(tmp_path, capsys) -> None:
    remain = tmp_path / "remain.tsv"
    assert check.main(["check", "--db", engine.url.database, "--periods", "0", "--remain", str(remain)]) == 0
    out = capsys.readouterr().out
    got = stats(out)
    assert "pdf_match_check done codes=" in out and " failed=0 " in out
    assert got["new_matched_value_ok"] > 0
    assert not any(key.endswith("_wrong") for key in got)
    rows = table(remain)
    assert len(rows) == sum(n for key, n in got.items() if key.startswith("new_unmatched_"))
    assert {row["kind"] for row in rows} <= {"std_new", "std_known", "ext_new", "ext_known", "not_in_xbrl"}
    assert all(row["truth_key"] or row["kind"] == "not_in_xbrl" for row in rows)


def test_same_version_as_old_has_no_changes(tmp_path, capsys, no_labels) -> None:
    changes = tmp_path / "changes.tsv"
    args = ["check", "--db", engine.url.database, "--periods", "0", "--old", fst.__file__, "--changes", str(changes),
            "--labels", no_labels]
    assert check.main(args) == 0
    got = stats(capsys.readouterr().out)
    assert not any(key.startswith("change_") for key in got)
    assert "row_sets_differ" not in got
    assert table(changes) == []
    assert {k[4:]: n for k, n in got.items() if k.startswith("old_")} == {
        k[4:]: n for k, n in got.items() if k.startswith("new_")
    }


def test_older_rules_show_as_changes(tmp_path, capsys, no_labels) -> None:
    old = tmp_path / "old_rules.py"
    source = Path(fst.__file__).read_text(encoding="utf-8")
    old.write_text(source + "\n\ndef name_index(filings, statement, excluded=()):\n    return Names()\n", encoding="utf-8")
    changes = tmp_path / "changes.tsv"
    args = ["check", "--db", engine.url.database, "--periods", "0", "--old", str(old), "--changes", str(changes),
            "--labels", no_labels]
    assert check.main(args) == 0
    got = stats(capsys.readouterr().out)
    rows = table(changes)
    assert rows and {row["change"] for row in rows} <= {"gained_ok", "gained_no_truth"}
    assert got["change_gained_ok"] == sum(1 for row in rows if row["change"] == "gained_ok")
    assert all(row["old_key"] == "" and row["new_by"] == "name" for row in rows)


def test_rows_on_another_element_are_listed(tmp_path, capsys, no_labels) -> None:
    forced = tmp_path / "forced_rules.py"
    source = Path(fst.__file__).read_text(encoding="utf-8")
    forced.write_text(source + """

_real_matches = pdf_only_matches


def pdf_only_matches(db, code):
    return [
        m if m.mapped is None else replace(m, mapped=Mapped("Assets", 1, "name"))
        for m in _real_matches(db, code)
    ]
""", encoding="utf-8")
    wrong = tmp_path / "wrong.tsv"
    args = ["check", "--db", engine.url.database, "--periods", "0", "--new", str(forced), "--wrong", str(wrong),
            "--labels", no_labels]
    assert check.main(args) == 0
    got = stats(capsys.readouterr().out)
    rows = table(wrong)
    assert rows and len(rows) == got["new_matched_name_wrong"]
    assert all(row["key"] == "Assets" and row["truth_key"] not in ("", "Assets") for row in rows)
    assert all(row["matched_by"] == "name" and row["truth_label"] for row in rows)
    on_bs = [row for row in rows if row["statement"] == "BS"]
    assert on_bs and all(row["key_in_hidden"] == "1" and row["key_hidden_value"] for row in on_bs)


def test_dictionary_without_the_company_itself(capsys) -> None:
    args = ["check", "--db", engine.url.database, "--periods", "0", "--exclude-own", "--min-codes", "1", "--code", CODE]
    assert check.main(args) == 0
    out = capsys.readouterr().out
    assert "label_dictionary batch done=" in out
    assert fst._standard_book is fst.StandardBook


def test_change_kinds() -> None:
    assert check.change_kind(None, "A", "A") == "gained_ok"
    assert check.change_kind(None, "A", "B") == "gained_wrong"
    assert check.change_kind(None, "A", None) == "gained_no_truth"
    assert check.change_kind("A", None, "A") == "lost_ok"
    assert check.change_kind("A", None, "B") == "lost_wrong"
    assert check.change_kind("A", "B", "B") == "fixed"
    assert check.change_kind("A", "B", "A") == "broke"
    assert check.change_kind("A", "B", "C") == "changed_other"
    assert check.unmatched_kind(None, set()) == "not_in_xbrl"
    assert check.unmatched_kind("ext:A", {"ext:A"}) == "ext_known"
    assert check.unmatched_kind("A", set()) == "std_new"


def test_subset_keeps_only_the_chosen_codes(tmp_path, capsys) -> None:
    out = tmp_path / "subset.db"
    assert check.main(["subset", "--db", engine.url.database, "--out", str(out), "--code", CODE]) == 0
    con = sqlite3.connect(out)
    try:
        assert {r[0] for r in con.execute("SELECT DISTINCT code FROM xbrl_filings")} == {CODE}
        assert con.execute("SELECT count(*) FROM xbrl_facts").fetchone()[0] > 0
        orphan = "SELECT count(*) FROM xbrl_facts WHERE filing_id NOT IN (SELECT id FROM xbrl_filings)"
        assert con.execute(orphan).fetchone()[0] == 0
    finally:
        con.close()
    assert check.main(["check", "--db", str(out), "--periods", "0"]) == 0
    assert "failed=0" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        check.main(["subset", "--db", engine.url.database, "--out", str(out), "--code", CODE])

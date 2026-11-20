from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.xbrl import pdf
from app.xbrl.pdf import fingerprint, inherit_accounting_standard_from_statements, load_standard_fixes
from tests.xbrl.test_tdnet_pdf_kab212_220 import _cell, _pages, _seed, _text

LISTED = "20240506582139"
HTML = _pages([_text(40, 40, "連結貸借対照表", 120), _cell(200, 80, "1,000", 40)])


def _statement(no: str, doc: str, *, code: str = "96130", disclosed: date = date(2024, 5, 9)) -> SimpleNamespace:
    return SimpleNamespace(
        disclosure_no=no, code=code, disclosed_date=disclosed, disclosed_time="15:00",
        type_of_period="FY", type_of_document=doc,
        period_start=date(2023, 4, 1), period_end=date(2024, 3, 31),
        fiscal_year_start=date(2023, 4, 1), fiscal_year_end=date(2024, 3, 31),
    )


def _plan(no: str, doc: str, title: str, **kwargs):
    db = SessionLocal()
    try:
        _seed(db, no, HTML, _statement(no, doc, **kwargs), title=title)
        db.commit()
        return pdf._plan(db, no, False)
    finally:
        db.close()


def test_list_is_well_formed(tmp_path) -> None:
    fixes = load_standard_fixes()
    assert len(fixes) == 16 and fixes[LISTED] == ("JPGAAP", "IFRS")
    assert all({wrong, right} == {"JPGAAP", "IFRS"} for wrong, right in fixes.values())
    twice = tmp_path / "fixes.json"
    item = {"disclosure_no": "1", "jquants": "IFRS", "standard": "JPGAAP"}
    twice.write_text(json.dumps({"fixes": [item, item]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_standard_fixes(twice)


def test_listed_disclosure_takes_the_listed_standard(capsys) -> None:
    built = _plan(LISTED, "FYFinancialStatements_Consolidated_JP", "2024年3月期決算短信〔ＩＦＲＳ〕(連結)")
    assert built.disclosure.standard == "IFRS"
    assert built.fingerprint == fingerprint(built.doc.sha256, "IFRS")
    assert "WARNING" not in capsys.readouterr().out


def test_listed_disclosure_is_left_alone_once_jquants_is_right(capsys) -> None:
    built = _plan(LISTED, "FYFinancialStatements_Consolidated_IFRS", "2024年3月期決算短信〔ＩＦＲＳ〕(連結)")
    assert built.disclosure.standard == "IFRS"
    assert "WARNING" not in capsys.readouterr().out


def test_unlisted_mismatch_only_warns(capsys) -> None:
    built = _plan("20990101000001", "FYFinancialStatements_Consolidated_JP", "2024年3月期決算短信〔ＩＦＲＳ〕(連結)",
                  code="96140")
    assert built.disclosure.standard == "JPGAAP"
    out = capsys.readouterr().out
    assert "WARNING tdnet_pdf standard-candidate disclosure_no=20990101000001 code=96140 jquants=JPGAAP title=IFRS" in out
    same = _plan("20990101000002", "FYFinancialStatements_Consolidated_JP", "2024年3月期決算短信〔日本基準〕(連結)",
                 code="96140")
    assert same.disclosure.standard == "JPGAAP" and "WARNING" not in capsys.readouterr().out
    plain = _plan("20990101000003", "FYFinancialStatements_Consolidated_JP", "FYFinancialStatements_Consolidated_JP",
                  code="96140")
    assert plain.disclosure.standard == "JPGAAP" and "WARNING" not in capsys.readouterr().out


def test_fix_mismatch_warns_and_keeps_the_value(capsys) -> None:
    assert pdf.fixed_standard(LISTED, "96130", "USGAAP") == "USGAAP"
    assert "WARNING tdnet_pdf standard-fix-mismatch disclosure_no=20240506582139 code=96130 recorded=JPGAAP jquants=USGAAP" in capsys.readouterr().out
    assert pdf.fixed_standard(None, "96130", "JPGAAP") == "JPGAAP"


def test_revisions_inherit_the_fixed_standard() -> None:
    db = SessionLocal()
    try:
        _seed(db, "20220202578371", HTML, _statement("20220202578371", "3QFinancialStatements_Consolidated_IFRS",
                                                     code="69300", disclosed=date(2022, 2, 3)))
        db.commit()
        got = inherit_accounting_standard_from_statements(db, code="69300", disclosed_date=date(2022, 3, 28))
    finally:
        db.close()
    assert got == "JPGAAP"

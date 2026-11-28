from __future__ import annotations

import json
from datetime import date

from app.config import XBRL_PDF_ROW_FIXES_PATH
from app.xbrl import three_statements as fst
from tests.xbrl.test_standard_labels import history, pdf_row


def pdf_filing(rows: list[fst.Fact], fixes: dict[tuple[str, str], fst.Mapped]) -> fst.Filing:
    f = fst.Filing(id=9, doc_id="P9", source="tdnet_pdf", period_type="Q1", period_end=date(2026, 3, 31),
                   submitted="2026-05-15", standard="JPGAAP", row_fixes=fixes)
    f.by_statement = {"BS": rows}
    return f


def test_listed_rows_take_the_listed_element_and_free_it_from_other_rows() -> None:
    rows = [pdf_row(1, "土地", 50), pdf_row(2, "使用権資産", 55), pdf_row(3, "減価償却累計額#5", -17),
            pdf_row(4, "使用権資産(純額)", 38)]
    learned = history([("土地", "Land", 50), ("使用権資産", "RightOfUseAssetsNet", 38)])
    fixes = {("BS", "使用権資産"): fst.Mapped("RightOfUseAssets", 1, "fix"),
             ("BS", "減価償却累計額#5"): fst.Mapped("AccumulatedDepreciationRightOfUseAssets", 1, "fix")}
    filing = pdf_filing(rows, fixes)
    applied = fst.apply_learned(rows, learned, "BS", filing.period_end, "Q", fst.Names(), "JPGAAP")
    assert [None if m is None else m.key for _, m in applied] == ["Land", "RightOfUseAssetsNet", None, None]
    got = fst._with_row_fixes(filing, "BS", applied)
    assert [None if m is None else (m.key, m.by) for _, m in got] == [
        ("Land", "value"), ("RightOfUseAssets", "fix"), ("AccumulatedDepreciationRightOfUseAssets", "fix"), None]
    assert fst._with_row_fixes(filing, "PL", applied) == applied


def test_pdf_row_fixes_are_well_formed() -> None:
    raw = json.loads(XBRL_PDF_ROW_FIXES_PATH.read_text(encoding="utf-8"))
    seen = set()
    for item in raw["rows"]:
        assert len(item["code"]) == 5 and isinstance(item["consolidated"], bool)
        assert item["statement"] in fst.STATEMENTS and item["doc_id"] and item["row"] and item["key"]
        assert item["evidence"] and item.get("sign", 1) in (1, -1)
        key = (item["code"], item["consolidated"], item["doc_id"], item["statement"], item["row"])
        assert key not in seen
        seen.add(key)
    by_doc = [m for docs in fst._pdf_row_fixes().values() for rows in docs.values() for m in rows.values()]
    assert len(by_doc) == len(raw["rows"])
    for docs in fst._pdf_row_fixes().values():
        for rows in docs.values():
            assert len({m.key for m in rows.values()}) == len(rows)
    labels = {(item["code"], item["key"]) for item in raw["labels"]}
    assert len(labels) == len(raw["labels"]) and all(item["label"] and item["evidence"] for item in raw["labels"])

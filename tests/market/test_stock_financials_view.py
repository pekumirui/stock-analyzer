"""銘柄の業績詳細（docs/DESIGN_stock.md「業績詳細」）。node で static/js/stock-financials.js の表の組み立てを呼ぶ。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_NODE = r"""
global.$ = () => ({innerHTML: "", textContent: "", hidden: false});
global.document = {querySelectorAll(){ return []; }};
global.fmt = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("ja-JP", {maximumFractionDigits: d, minimumFractionDigits: d});
const fs = require(process.argv[1]);
const d = JSON.parse(process.argv[2]);
console.log(JSON.stringify({
  html: fs.fsTableHtml(d),
  empty: fs.fsTableHtml({periods: [], rows: [], empty: {reason: "no_material", message: "本表が無い"}}),
  meta: fs.fsMeta(d),
  amounts: [fs.fsAmount(1234567890, "money"), fs.fsAmount(-2500000, "money"), fs.fsAmount(-400000, "money"),
            fs.fsAmount(41.666, "per_share"), fs.fsAmount(null, "money")],
}));
"""

PAYLOAD = {
    "statement": "BS", "consolidated": True, "basis": "連結", "accounting_standards": ["JPGAAP"],
    "cumulative": False, "unit": "JPY",
    "periods": [
        {"period_end": "2025-03-31", "label": "25.03", "period_type": "FY", "source": "edinet", "source_label": "有報",
         "doc_id": "S100A", "submitted_at": "2025-06-20 15:00", "accounting_standard": "JPGAAP"},
        {"period_end": "2025-06-30", "label": "25.06", "period_type": "Q1", "source": "tdnet_pdf", "source_label": "PDF",
         "doc_id": "D1", "submitted_at": "2025-08-08 15:00", "accounting_standard": "JPGAAP"},
        {"period_end": "2025-12-31", "label": "25.12", "period_type": "Q3", "source": "edinet", "source_label": "有報",
         "doc_id": "S100B", "submitted_at": "2026-02-12 15:00", "accounting_standard": "JPGAAP"},
    ],
    "rows": [
        {"key": "Goodwill", "label": "のれん", "unit": "money", "unmatched_pdf": False,
         "cells": [{"value": 5_000_000, "source": "edinet"}, None, {"value": 6_400_000, "source": "tdnet"}]},
        {"key": "pdf:前受金", "label": "前受金", "unit": "money", "unmatched_pdf": True,
         "cells": [None, {"value": 4_000_000, "source": "tdnet_pdf"}, None]},
        {"key": "EPS", "label": "1株当たり当期純利益", "unit": "per_share", "unmatched_pdf": False,
         "cells": [{"value": -12.5, "source": "edinet"}, None, None]},
    ],
    "empty": None,
}


def _run() -> dict:
    out = subprocess.run(["node", "-e", _NODE, str(ROOT / "static/js/stock-financials.js"), json.dumps(PAYLOAD)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _rows(html: str) -> list[str]:
    body = html.split("<tbody>", 1)[1]
    return body.split("<tr")[1:]


def _cells(row: str) -> list[str]:
    return re.findall(r"<td[^>]*>.*?</td>", row)


def test_amounts_are_million_yen_and_per_share_yen() -> None:
    a = _run()["amounts"]
    assert a[0] == "1,235"
    assert a[1] == '<span class="loss">-3</span>'
    assert a[2] == "0"
    assert a[3] == "41.67"
    assert a[4] == ""


def test_header_shows_label_period_type_and_source() -> None:
    html = _run()["html"]
    head = html.split("<tbody>", 1)[0]
    ths = re.findall(r"<th[^>]*>.*?</th>", head)
    assert len(ths) == 4
    assert "科目" in ths[0]
    assert "25.03" in ths[1] and "通期" in ths[1] and "有報" in ths[1]
    assert "25.06" in ths[2] and "1Q" in ths[2] and "PDF" in ths[2]
    assert "25.12" in ths[3] and "3Q" in ths[3]


def test_filled_cell_is_marked_with_its_source() -> None:
    goodwill = _cells(_rows(_run()["html"])[0])
    assert len(goodwill) == 4
    assert "fs-fill" not in goodwill[1]
    assert goodwill[2] == '<td class="num"></td>'
    assert 'class="num fill"' in goodwill[3]
    assert '<span class="qb fs-fill">XBRL</span>' in goodwill[3]
    assert "有報 に無く、XBRL から補った値" in goodwill[3]
    assert goodwill[3].startswith('<td class="num fill"') and ">6<" in goodwill[3]


def test_unmatched_pdf_row_is_marked() -> None:
    rows = _rows(_run()["html"])
    pdf = _cells(rows[1])
    assert rows[1].startswith(' class="pdf"')
    assert "前受金" in pdf[0] and "fs-pdf" in pdf[0] and "PDFのみ" in pdf[0]
    assert "fs-fill" not in rows[1]
    assert "fs-pdf" not in rows[0] and "fs-pdf" not in rows[2]


def test_per_share_row_and_null_cells() -> None:
    eps = _cells(_rows(_run()["html"])[2])
    assert "（円）" in eps[0]
    assert eps[1] == '<td class="num"><span class="loss">-12.50</span></td>'
    assert eps[2] == eps[3] == '<td class="num"></td>'


def test_meta_and_empty() -> None:
    r = _run()
    assert r["meta"] == "連結 / 日本基準 / 単位: 百万円（1株当たりは円）"
    assert r["empty"] == '<tr><td class="l sub" title="本表が無い">データなし</td></tr>'

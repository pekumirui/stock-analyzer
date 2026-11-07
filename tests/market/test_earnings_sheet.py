"""決算速報の決算表はボトムシート、一覧の下は選んだ銘柄のニュース（KAB-386）。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/earnings.js").read_text(encoding="utf-8")


def _block(start: str, end: str) -> str:
    i = HTML.index(start)
    return HTML[i:HTML.index(end, i)]


def test_fin_tables_live_in_sheet_inside_earnings_tab() -> None:
    tab = _block('<section id="tab-earnings">', "</section>")
    sheet = tab[tab.index('id="earn-sheet"'):]
    for tid in ("cum-tbl", "q-tbl", "prog-tbl", "rev-tbl"):
        assert f'id="{tid}"' in sheet
    assert 'id="earn-sheet-x"' in sheet
    assert "panel a-fin" not in HTML
    assert 'id="earn-news"' in tab
    assert 'id="en-news" class="sheet"' in tab


def test_sheet_opens_only_from_row_press_and_search() -> None:
    assert "selectRow('${r.disclosure_no}', true)" in JS
    assert "selectSched('${r.code}', true)" in JS
    flat = re.search(r"function selectFlat\(entry\)\{(.*?)\n\}", JS, re.S).group(1)
    assert "true" not in flat and "openSheet" not in flat
    pick = re.search(r"async function pickEarnStock\(.*?\n\}", JS, re.S).group(0)
    assert pick.count("openSheet()") == 2


def test_news_reuses_stock_tab_rows_frames_and_deck() -> None:
    for name in ("stockNewsRows(", "mountFilingFrames(", "deckDocHtml(", "fitStockFrame"):
        assert name in JS
    assert "function mountFilingFrames(host, src, f)" in (ROOT / "static/js/stock.js").read_text(encoding="utf-8")


def test_deck_doc_html_before_extract_shows_status() -> None:
    script = (
        "const m = require(process.argv[1]);"
        "process.stdout.write(m.deckDocHtml({document:{title:'説明<資料>', pubdate:'2026-10-14 15:30:00',"
        " url:'', status:'submitted'}, extract:null, pages:[]}, 400));"
    )
    out = subprocess.run(["node", "-e", script, str(ROOT / "static/js/stock-deck.js")],
                         capture_output=True, text=True, check=True).stdout
    assert "説明&lt;資料&gt;" in out
    assert "2026-10-14" in out
    assert "読み取り中。" in out
    assert json.dumps("原本の PDF", ensure_ascii=False).strip('"') not in out
